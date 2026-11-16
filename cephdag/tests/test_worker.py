from concurrent.futures import ThreadPoolExecutor
import copy
import threading
import unittest

import numpy as np

from cephdag.functions import CHUNK_VALUES, function, numeric, synthetic
from cephdag.graph import Graph
from cephdag.worker import DurableWorker, execute_payload
from tests.helpers import LAB_POOLS, FaultStore, terminal_job, zero_counters

RUN_ID = "a" * 32


def numeric_payload(store, iterations=2, ratios=(0.5,), function_key="map@1", targets=None):
    source = store.write("node0", "datasets/test/part-000000", synthetic(4096, 1))
    graph = Graph()
    inputs = [source] if function_key != "join@1" else [source, source]
    graph.add(function_key, inputs, ratios=list(ratios), iterations=iterations)
    task = graph.tasks["t000000"]
    return {
        "run_id": RUN_ID,
        "worker": 0,
        "targets": list(targets or [1] * len(task.output_sizes)),
        "inputs": [ref.to_dict() for ref in inputs],
        "task": task.to_dict(),
    }


def make_worker(store, node=0):
    return DurableWorker(node, store, LAB_POOLS, counters=zero_counters)


def lose_terminal_records(store, commit_then_lose):
    """Fail terminal job-record writes (optionally after committing) while failing[0] is set."""
    put = store.put_manifest
    failing = [True]

    def journal(pool, key, value):
        terminal = key.endswith("worker-job.json") and value["status"] in ("completed", "failed")
        if terminal and failing[0]:
            if commit_then_lose:
                put(pool, key, value)
            raise OSError("journal response lost")
        put(pool, key, value)

    store.put_manifest = journal
    return failing


class NumericTests(unittest.TestCase):
    def test_chunked_map_gives_the_bytes_of_the_whole_array_map(self):
        size = (2 * CHUNK_VALUES + CHUNK_VALUES // 2) * 4
        data = synthetic(size, 3)
        expected = np.frombuffer(data, dtype="<f4").copy()
        for _ in range(5):
            np.multiply(expected, np.float32(1.0001), out=expected)
            np.add(expected, np.float32(0.0001), out=expected)
            np.tanh(expected, out=expected)
        (output,) = numeric([data], {"iterations": 5}, [size])
        self.assertEqual(output, expected.tobytes())


class ExecuteTests(unittest.TestCase):
    def test_numeric_result_is_written_to_the_target_pool_and_published_on_the_worker(self):
        store = FaultStore()
        payload = numeric_payload(store, iterations=3, ratios=(0.5,))
        result = execute_payload(payload, store, LAB_POOLS)
        (output,) = result["outputs"]
        self.assertEqual((output["pool"], output["size"]), ("node1", 2048))
        values = np.frombuffer(synthetic(4096, 1), dtype="<f4")
        for _ in range(3):
            values = np.tanh(values * np.float32(1.0001) + np.float32(0.0001))
        stored = np.frombuffer(store.objects["node1", output["key"]], dtype="<f4")
        np.testing.assert_allclose(stored, values[:512], rtol=1e-6)
        self.assertIn(("node0", f"runs/{RUN_ID}/t000000/complete.json"), store.manifests)
        self.assertEqual(
            [stage["kind"] for stage in result["stages"]], ["read", "compute", "write"]
        )

    def test_join_split_and_zero_size_outputs(self):
        store = FaultStore()
        join = execute_payload(
            numeric_payload(store, function_key="join@1", ratios=(0.25,)), store, LAB_POOLS
        )
        self.assertEqual(join["outputs"][0]["size"], 2048)
        split = numeric_payload(store, function_key="split@1", ratios=(0, 1), targets=[2, 0])
        outputs = execute_payload(split, store, LAB_POOLS)["outputs"]
        self.assertEqual(
            [(ref["pool"], ref["size"]) for ref in outputs], [("node2", 0), ("node0", 4096)]
        )

    def test_nothing_is_published_when_an_output_write_fails(self):
        store = FaultStore(fail_write=3)
        payload = numeric_payload(store, function_key="split@1", ratios=(1, 1))
        with self.assertRaisesRegex(OSError, "injected"):
            execute_payload(payload, store, LAB_POOLS)
        self.assertNotIn(("node0", f"runs/{RUN_ID}/t000000/complete.json"), store.manifests)

    def test_rejects_bad_namespace_targets_sizes_and_corrupt_inputs(self):
        store = FaultStore()
        payload = numeric_payload(store)
        cases = {
            "invalid run namespace": dict(payload, run_id="../x"),
            "invalid output targets": dict(payload, targets=[3]),
            "output size contract": dict(payload, task=dict(payload["task"], output_sizes=[4])),
        }
        for message, bad in cases.items():
            with self.assertRaisesRegex(ValueError, message):
                execute_payload(bad, store, LAB_POOLS)
        store.objects["node0", "datasets/test/part-000000"] = b"\0" * 4096
        with self.assertRaisesRegex(ValueError, "checksum"):
            execute_payload(payload, store, LAB_POOLS)

    def test_actual_output_must_fit_the_memory_bound(self):
        @function("test-inflate", "1", inputs=["float32"], outputs=["bytes"])
        def inflate(inputs, params, output_sizes):
            return (b"\0" * params["size"],)

        store = FaultStore()
        source = store.write("node0", "datasets/test/part-000000", synthetic(4096, 1))
        graph = Graph()
        graph.add("test-inflate@1", [source], ratios=[1], params={"size": 2**20})
        payload = {
            "run_id": RUN_ID,
            "worker": 0,
            "targets": [0],
            "inputs": [source.to_dict()],
            "task": graph.tasks["t000000"].to_dict(),
        }
        self.assertEqual(execute_payload(payload, store, LAB_POOLS)["outputs"][0]["size"], 2**20)
        with self.assertRaisesRegex(ValueError, "exceeds worker memory"):
            execute_payload(payload, store, LAB_POOLS, memory_limit=10 * 2**20)


class RetentionTests(unittest.TestCase):
    """The ACK protocol that bounded worker memory: forget a job only after a verified ACK."""

    def test_ack_releases_memory_but_a_retry_returns_the_exact_durable_result(self):
        store = FaultStore()
        payload = numeric_payload(store)
        worker = make_worker(store)
        key = f"{RUN_ID}/t000000"
        worker.submit(payload)
        job = terminal_job(worker, key)
        self.assertEqual(job["status"], "completed")
        worker.acknowledge({"key": key, "token": job["ack_token"]})
        self.assertEqual(worker.snapshot([])["retained_jobs"], 0)
        self.assertEqual(worker.submit(payload), job)
        self.assertEqual(worker.snapshot([key])["jobs"][key], job)
        worker.acknowledge({"key": key, "token": job["ack_token"]})
        self.assertEqual(make_worker(store).submit(payload), job)
        self.assertEqual(store.writes, 2)

    def test_busy_slot_bad_tokens_and_concurrent_acks(self):
        store = FaultStore()
        payload = numeric_payload(store)
        entered = threading.Event()
        release = threading.Event()
        read = store.read

        def blocked_read(ref):
            entered.set()
            release.wait(2)
            return read(ref)

        store.read = blocked_read
        worker = make_worker(store)
        warm = {"run_id": "b" * 32, "refs": payload["inputs"]}
        key = "b" * 32 + "/warm"
        worker.warm(warm)
        self.assertTrue(entered.wait(1))
        self.assertEqual(worker.warm(warm)["status"], "running")
        with self.assertRaises(ValueError):
            worker.acknowledge({"key": key, "token": "bad"})
        with self.assertRaisesRegex(ValueError, "different payload"):
            worker.warm(dict(warm, refs=[]))
        with self.assertRaisesRegex(RuntimeError, "slot already reserved"):
            worker.submit(payload)
        release.set()
        job = terminal_job(worker, key)
        with ThreadPoolExecutor(8) as pool:
            futures = [
                pool.submit(worker.acknowledge, {"key": key, "token": job["ack_token"]})
                for _ in range(20)
            ]
            futures += [pool.submit(worker.warm, warm) for _ in range(20)]
            for future in futures:
                future.result()
        self.assertEqual(worker.snapshot([])["retained_jobs"], 0)
        self.assertEqual(worker.warm(warm), job)

    def test_retention_bound_backpressures_without_losing_failures(self):
        store = FaultStore()
        payload = numeric_payload(store)
        store.writes = 0
        store.fail_write = 1
        worker = make_worker(store)
        worker.max_retained = 1
        key = f"{RUN_ID}/t000000"
        worker.submit(payload)
        job = terminal_job(worker, key)
        self.assertEqual(job["status"], "failed")
        with self.assertRaisesRegex(RuntimeError, "acknowledgment required"):
            worker.submit(dict(payload, run_id="d" * 32))
        worker.acknowledge({"key": key, "token": job["ack_token"]})
        self.assertEqual(worker.submit(payload), job)
        self.assertEqual(store.writes, 1)

    def test_failed_publication_keeps_the_slot_fenced_and_recovers_without_rerunning(self):
        for task_fails, commit_then_lose in ((False, False), (True, False), (False, True)):
            with self.subTest(task_fails=task_fails, commit_then_lose=commit_then_lose):
                store = FaultStore()
                payload = numeric_payload(store)
                store.writes = 0
                store.fail_write = 1 if task_fails else None
                failing = lose_terminal_records(store, commit_then_lose)
                worker = make_worker(store)
                key = f"{RUN_ID}/t000000"
                worker.submit(payload)
                job = terminal_job(worker, key)
                self.assertEqual(job["status"], "publication_failed")
                self.assertEqual(worker.snapshot([])["active"], key)
                terminal = job["terminal"]
                self.assertIsNotNone(terminal["stage_events"][-1]["end"])
                with self.assertRaises(ValueError):
                    worker.acknowledge({"key": key, "token": terminal["ack_token"]})
                if commit_then_lose:
                    self.assertEqual(make_worker(store).submit(payload), terminal)
                else:
                    with self.assertRaisesRegex(RuntimeError, "indeterminate"):
                        make_worker(store).submit(payload)
                failing[0] = False
                writes = store.writes
                self.assertEqual(
                    worker.recover_publication({"key": key, "token": terminal["ack_token"]}),
                    terminal,
                )
                self.assertIsNone(worker.active)
                self.assertEqual(
                    worker.recover_publication({"key": key, "token": terminal["ack_token"]}),
                    terminal,
                )
                worker.acknowledge({"key": key, "token": terminal["ack_token"]})
                self.assertFalse(worker.jobs)
                self.assertEqual(worker.submit(payload), terminal)
                self.assertEqual(store.writes, writes)

    def test_snapshot_rejects_foreign_keys_and_unknown_endpoints(self):
        worker = make_worker(FaultStore())
        with self.assertRaisesRegex(ValueError, "invalid key"):
            worker.snapshot(["../etc/passwd"])
        with self.assertRaisesRegex(ValueError, "unknown endpoint"):
            worker.handle("/delete", {})
        with self.assertRaisesRegex(ValueError, "wrong worker"):
            make_worker(FaultStore(), node=2).submit(numeric_payload(FaultStore()))

    def test_job_records_are_json_round_trippable(self):
        store = FaultStore()
        worker = make_worker(store)
        worker.submit(numeric_payload(store))
        job = terminal_job(worker, f"{RUN_ID}/t000000")
        self.assertEqual(
            copy.deepcopy(job),
            store.get_manifest("node0", f"runs/{RUN_ID}/t000000/worker-job.json"),
        )


if __name__ == "__main__":
    unittest.main()
