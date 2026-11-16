import copy
from http.client import IncompleteRead
import json
import random
import time
import unittest
from urllib.error import HTTPError

from cephdag import coordinator
from cephdag.functions import synthetic
from cephdag.graph import Graph, ObjectRef
from cephdag.model import Dispatch, Models, Simulation
from tests.helpers import LocalTransport, local_cluster
from tests.test_equivalence import dump_state

RUN_ID = "c" * 32


def two_step_graph(store):
    source = store.write("node0", "datasets/test/part-000000", synthetic(4096, 1))
    graph = Graph()
    (first,) = graph.add("map@1", [source], ratios=[1], iterations=0)
    graph.add("map@1", [first], ratios=[1], iterations=0)
    return graph


class LossyTransport(LocalTransport):
    """Delivers every request, but loses the first reply to each submit and each ack."""

    def __init__(self, workers):
        super().__init__(workers)
        self.lost = set()

    def post(self, node, path, payload):
        answer = super().post(node, path, payload)
        key = (path, payload.get("key") or payload.get("task", {}).get("id"))
        if path == "submit" and key not in self.lost:
            self.lost.add(key)
            raise TimeoutError("accepted; reply lost")
        if path == "ack" and key not in self.lost:
            self.lost.add(key)
            raise IncompleteRead(b"{", 10)
        return answer


class ScriptedWorkers:
    """Workers on a fake clock: each task moves one stage further when the test says so."""

    def __init__(self, graph):
        self.graph = graph
        self.clock = 0.0
        self.records = {}

    def post(self, node, path, payload):
        if path == "ack":
            return {"key": payload["key"], "acknowledged": True}
        task = payload["task"]["id"]
        self.records[task] = {
            "key": f"{payload['run_id']}/{task}",
            "status": "running",
            "stage_index": 0,
            "stage_started": self.clock,
            "stage_events": [{"index": 0, "start": self.clock, "end": None}],
            "targets": payload["targets"],
        }
        return copy.deepcopy(self.records[task])

    def advance(self, task, state):
        """End the current stage of `task`; return the /state snapshot of its worker."""
        job = self.records[task]
        job["stage_events"][-1]["end"] = self.clock
        index = job["stage_index"] + 1
        if index < len(state.stages_for(state.running[task].action)):
            job["stage_events"].append({"index": index, "start": self.clock, "end": None})
            job.update(stage_index=index, stage_started=self.clock)
        else:
            sizes = self.graph.tasks[task].output_sizes
            outputs = [
                ObjectRef(f"node{target}", f"out/{task}/{index}", size).to_dict()
                for index, (target, size) in enumerate(zip(job["targets"], sizes))
            ]
            job.update(status="completed", finished=self.clock, ack_token="t")
            job["result"] = {"outputs": outputs}
        return [{"node": 0, "jobs": {job["key"]: copy.deepcopy(job)}}]


class CoordinatorTests(unittest.TestCase):
    def test_run_completes_reads_back_outputs_and_releases_every_worker(self):
        cluster, store, workers, transport = local_cluster()
        graph = two_step_graph(store)
        for policy in ("greedy", "heft", "beam-2", "stealing"):
            with self.subTest(policy=policy):
                result = coordinator.run(
                    graph, Models(), policy, cluster=cluster, store=store, transport=transport
                )
                self.assertEqual(len(result["tasks"]), 2)
                self.assertEqual(result["verified_final_bytes"], 4096)
                self.assertTrue(
                    all(not worker.jobs and worker.active is None for worker in workers)
                )
                self.assertIn(("node0", f"runs/{result['run_id']}/result.json"), store.manifests)

    def test_lost_submit_and_ack_replies_are_reconciled_by_identical_retries(self):
        cluster, store, workers, _ = local_cluster()
        transport = LossyTransport(workers)
        result = coordinator.run(
            two_step_graph(store),
            Models(),
            "greedy",
            cluster=cluster,
            store=store,
            transport=transport,
            run_id=RUN_ID,
        )
        self.assertEqual(len(result["tasks"]), 2)
        submits = [call["body"] for call in transport.calls if call["path"] == "submit"]
        self.assertEqual(len(submits), 4)
        self.assertEqual(submits[0], submits[1])
        self.assertTrue(all(not worker.jobs for worker in workers))

    def test_exhausted_ambiguous_submit_is_recorded_as_pending(self):
        cluster, store, _, _ = local_cluster()
        attempts = []

        class Unreachable:
            def post(self, node, path, payload):
                if path == "state":
                    return {
                        "node": node,
                        "active": None,
                        "jobs": {},
                        "counters": {"tx": 0, "rx": 0},
                    }
                attempts.append(copy.deepcopy(payload))
                raise TimeoutError("ambiguous transport")

        with self.assertRaises(TimeoutError):
            coordinator.run(
                two_step_graph(store),
                Models(),
                "greedy",
                cluster=cluster,
                store=store,
                transport=Unreachable(),
                run_id=RUN_ID,
            )
        self.assertEqual(len(attempts), 3)
        self.assertTrue(all(attempt == attempts[0] for attempt in attempts))
        failed = store.get_manifest("node0", f"runs/{RUN_ID}/failed.json")
        self.assertEqual(failed["pending"], ["t000000"])

    def test_a_failed_task_stops_the_run_with_its_error(self):
        cluster, store, workers, transport = local_cluster()
        graph = two_step_graph(store)
        store.objects["node0", "datasets/test/part-000000"] = b"\0" * 4096
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            coordinator.run(
                graph, Models(), "greedy", cluster=cluster, store=store, transport=transport
            )
        self.assertTrue(all(not worker.jobs for worker in workers))

    def test_ack_retries_ambiguous_failures_but_not_rejections(self):
        job = {"key": "a" * 32 + "/warm", "ack_token": "token"}
        answer = {"key": job["key"], "acknowledged": True}

        class Scripted:
            def __init__(self, replies):
                self.replies = list(replies)
                self.calls = 0

            def post(self, node, path, payload):
                self.calls += 1
                reply = self.replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply

        for error in (
            IncompleteRead(b"{", 10),
            json.JSONDecodeError("partial", "{", 1),
            TimeoutError("lost"),
        ):
            transport = Scripted([error, answer])
            coordinator.acknowledge(transport, 0, job)
            self.assertEqual(transport.calls, 2)
        rejecting = Scripted([HTTPError("url", 400, "bad token", None, None)])
        with self.assertRaises(HTTPError):
            coordinator.acknowledge(rejecting, 0, job)
        self.assertEqual(rejecting.calls, 1)

    def test_inputs_outside_the_cluster_and_mismatched_models_are_rejected(self):
        cluster, store, _, transport = local_cluster()
        graph = two_step_graph(store)
        with self.assertRaisesRegex(ValueError, "models describe 2 nodes"):
            coordinator.run(
                graph,
                Models.uncalibrated(2),
                "greedy",
                cluster=cluster,
                store=store,
                transport=transport,
            )
        two_cluster, _, _, two_transport = local_cluster(2)
        outside = Graph()
        outside.add("map@1", [store.write("node2", "d", b"\0" * 4)])
        with self.assertRaisesRegex(ValueError, "no cluster node owns"):
            coordinator.run(
                outside,
                Models.uncalibrated(2),
                "greedy",
                cluster=two_cluster,
                store=store,
                transport=two_transport,
            )

    def test_live_state_counts_progress_of_a_running_stage(self):
        cluster, _, _, _ = local_cluster()
        graph = Graph()
        graph.add("map@1", [ObjectRef("node0", "d/0", 4 * 10**6)])
        action = Dispatch("t000000", 1, (1,))
        # A remote read on the default model moves 12.5 MB/s, so 100 us of it moved 1250 bytes.
        jobs = {"t000000": {"stage_index": 0, "stage_started": 10.0 - 1e-4}}
        pending = {"t000000": action}
        state = coordinator.observed_state(
            graph, Models(), cluster.pools, {}, pending, jobs, [0.0] * 3, now=10.0, elapsed=5.0
        )
        stage = state.running["t000000"].stages[0]
        self.assertEqual(stage.kind, "read")
        self.assertAlmostEqual(stage.remaining, 4 * 10**6 - 1250, delta=1e-3)
        # A stage the worker still reports as running keeps at least a millisecond of work.
        jobs["t000000"]["stage_started"] = 0.0
        state = coordinator.observed_state(
            graph, Models(), cluster.pools, {}, pending, jobs, [0.0] * 3, now=10.0, elapsed=5.0
        )
        self.assertAlmostEqual(state.running["t000000"].stages[0].remaining, 12.5e6 * 0.001)
        self.assertEqual(state.free_workers(), [0, 2])
        self.assertEqual(state.time, 5.0)

    def test_incremental_live_state_equals_a_full_rebuild_at_every_event(self):
        generator = random.Random(3)
        graph = Graph()
        # All sources on node0, so reads from different workers share its disk and NIC.
        sources = [ObjectRef("node0", f"d/{index}", 2**20, partition=index) for index in range(9)]
        mapped = [graph.add("map@1", [source])[0] for source in sources]
        for left, right in zip(mapped, mapped[1:]):
            graph.add("join@1", [left, right], ratios=[0.5])
        cluster, store, _, _ = local_cluster()
        transport = ScriptedWorkers(graph)
        run = coordinator.Run(
            graph, Models(), "greedy", cluster, store, transport, RUN_ID, 60, None
        )
        run.start = time.perf_counter()
        run.graph.freeze()
        run.live = Simulation(graph, Models(), cluster.pools)
        compared = 0
        while len(run.completed) < len(graph.tasks):
            transport.clock += generator.uniform(0.001, 0.05)
            state = run.observe(now=transport.clock, elapsed=transport.clock)
            rebuilt = coordinator.observed_state(
                graph,
                Models(),
                cluster.pools,
                run.completed,
                run.pending,
                transport.records,
                run.assigned,
                now=transport.clock,
                elapsed=transport.clock,
                history=run.history,
            )
            self.assertEqual(dump_state(state), dump_state(rebuilt))
            compared += 1
            idle = state.free_workers()
            if state.ready(1) and idle and (not run.pending or generator.random() < 0.5):
                task = graph.tasks[generator.choice(state.ready())]
                targets = tuple(generator.randrange(3) for _ in task.output_sizes)
                run._dispatch(state, Dispatch(task.id, generator.choice(idle), targets), 0.0)
            else:
                run._absorb(transport.advance(generator.choice(list(run.pending)), state))
        self.assertGreater(compared, 3 * len(graph.tasks))

    def test_transmit_bytes_count_senders_only(self):
        before = [{"tx": 10, "rx": 0}, {"tx": 0, "rx": 5}]
        after = [{"tx": 30, "rx": 0}, {"tx": 0, "rx": 25}]
        self.assertEqual(coordinator.total_transmit_bytes(before, after), 20)
        with self.assertRaisesRegex(ValueError, "reset"):
            coordinator.total_transmit_bytes(after, before)


if __name__ == "__main__":
    unittest.main()
