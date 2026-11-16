"""Calibration on a computational-storage cluster and on a role-split one, with known timings.

The workers run in-process; a transport in front of them replaces every stage time a
worker reports with the time a known model gives, so the fit must recover that model.
"""

from collections import Counter
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from cephdag.client import Client
from cephdag.graph import Graph, ObjectRef
from cephdag.model import Models
from experiments import calibrate
from tests.helpers import cs_nodes, disagg_nodes, role_lab
from tests.test_droid import MINI, ingest

MIB = 2**20
NETWORK_BPS = 50e6
# Per node: local read and write bytes per second, all different.
READ_BPS = (100e6, 200e6, 300e6, 400e6)
WRITE_BPS = (60e6, 70e6, 80e6, 90e6)
# map@1 compute: fixed, per input MiB, per input MiB and iteration, per output MiB.
COMPUTE = (0.01, 0.002, 0.0005, 0.003)
DROID_FIXED = {
    "decode_resize@1": 0.4,
    "encode_video@1": 0.2,
    "frame_stats@1": 0.1,
    "state_parquet@1": 0.05,
}


class KnownTimings:
    """A transport that reports the stage times of a known model instead of measured ones."""

    def __init__(self, transport, pools):
        self.transport = transport
        self.pools = pools
        self.submitted = {}

    def post(self, node, path, payload):
        if path == "submit":
            self.submitted[f"{payload['run_id']}/{payload['task']['id']}"] = payload
        response = self.transport.post(node, path, payload)
        if path == "state":
            for key, job in response["jobs"].items():
                if job.get("status") == "completed" and key in self.submitted:
                    self.retime(self.submitted[key], job["result"])
        return response

    def seconds(self, bytes_, source, destination, rates):
        local = rates[source] if source == destination else min(rates[source], NETWORK_BPS)
        return bytes_ / local

    def retime(self, payload, result):
        worker = payload["worker"]
        task = payload["task"]
        inputs = [self.pools.index(ref["pool"]) for ref in payload["inputs"]]
        for stage in result["stages"]:
            if stage["kind"] == "read":
                node = inputs[stage["index"]]
                stage["seconds"] = self.seconds(stage["bytes"], node, worker, READ_BPS)
            elif stage["kind"] == "write":
                node = payload["targets"][stage["index"] - len(inputs) - 1]
                stage["seconds"] = self.seconds(stage["bytes"], node, worker, WRITE_BPS)
            elif task["function"] == "map@1":
                fixed, per_input, per_iteration, per_output = COMPUTE
                in_mib = stage["bytes"] / MIB
                out_mib = sum(task["output_sizes"]) / MIB
                stage["seconds"] = (
                    fixed
                    + per_input * in_mib
                    + per_iteration * in_mib * task["params"]["iterations"]
                    + per_output * out_mib
                )
            else:
                stage["seconds"] = DROID_FIXED[task["function"]] + 0.01 * stage["bytes"] / MIB


class Rounds:
    """A transport that slows worker `slow` down and records, at every submit, how many
    samples that worker had started minus how many the worker furthest behind had finished.
    Its first submit to each worker waits until every worker has had one, so a calibration
    completes only if the workers' samples run at the same time."""

    def __init__(self, transport, workers, slow):
        self.transport = transport
        self.workers = workers
        self.slow = slow
        self.barrier = threading.Barrier(len(workers), timeout=10)
        self.lock = threading.Lock()
        self.started = Counter()
        self.finished = Counter()
        self.ahead = []

    def post(self, node, path, payload):
        if path == "submit":
            with self.lock:
                first = not self.started[node]
                behind = min(self.finished[worker] for worker in self.workers)
                self.ahead.append(self.started[node] - behind)
                self.started[node] += 1
            if first:
                self.barrier.wait()
        if path == "state" and node == self.slow:
            time.sleep(0.01)
        response = self.transport.post(node, path, payload)
        if path == "ack":
            with self.lock:
                self.finished[node] += 1
        return response


def lab(nodes):
    """(cluster, store, transport) running in this process, with known timings."""
    cluster, store, transport = role_lab(nodes)
    return cluster, store, KnownTimings(transport, cluster.pools)


def run_calibration(cluster, store, transport, **options):
    # Small samples keep the test fast; the fit only needs two input sizes.
    with (
        mock.patch.object(calibrate, "SAMPLE_MIB", (1, 2)),
        tempfile.TemporaryDirectory() as directory,
        contextlib.redirect_stdout(io.StringIO()),
    ):
        calibrate.calibrate(cluster, store, transport, 400, Path(directory), **options)
        artifact = json.loads((Path(directory) / "calibration.json").read_text())
        models = json.loads((Path(directory) / "models.json").read_text())
    return artifact, Models.from_dict(models)


class CalibrationTests(unittest.TestCase):
    def assert_compute_recovered(self, models):
        fixed, per_input, per_iteration, per_output = COMPUTE
        compute = models.compute
        self.assertAlmostEqual(compute.fixed_seconds, fixed, places=9)
        self.assertAlmostEqual(compute.input_seconds_per_byte * MIB, per_input, places=9)
        self.assertAlmostEqual(compute.iteration_seconds_per_byte * MIB, per_iteration, places=9)
        self.assertAlmostEqual(compute.output_seconds_per_byte * MIB, per_output, places=9)

    def test_every_node_with_both_parts_measures_its_own_disk(self):
        artifact, models = run_calibration(*lab(cs_nodes(3)))
        self.assertEqual(models.storage.read_bps, READ_BPS[:3])
        self.assertEqual(models.storage.write_bps, WRITE_BPS[:3])
        self.assertEqual(models.network.tx_bps, (50e6,) * 3)
        self.assert_compute_recovered(models)
        self.assertEqual(artifact["storage_sources"], ["measured"] * 3)
        self.assertEqual({row["node"] for row in artifact["samples"]}, {0, 1, 2})
        self.assertTrue(all(row["pool_node"] == row["node"] for row in artifact["samples"]))

    def test_samples_run_in_rounds_of_one_on_every_worker_at_once(self):
        for nodes in (cs_nodes(3), disagg_nodes(2, 3)):
            with self.subTest(nodes=len(nodes)):
                cluster, store, transport = lab(nodes)
                rounds = Rounds(transport, cluster.worker_nodes, slow=cluster.worker_nodes[0])
                reference, _ = run_calibration(*lab(cs_nodes(2)))
                options = {"reference": reference} if len(nodes) == 5 else {}
                artifact, models = run_calibration(cluster, store, rounds, **options)
                self.assert_compute_recovered(models)
                self.assertFalse(rounds.barrier.broken)
                # No worker starts its next sample before the slow one has finished this one.
                self.assertEqual(max(rounds.ahead), 0)
                per_worker = Counter(row["node"] for row in artifact["samples"])
                self.assertEqual(per_worker, {node: 9 for node in cluster.worker_nodes})

    def test_every_sample_at_the_real_sizes_fits_in_a_worker(self):
        graph = Graph()
        for mib in calibrate.SAMPLE_MIB:
            source = ObjectRef("node0", f"input-{mib}", mib * MIB)
            for iterations, ratio in calibrate.sample_settings(mib):
                graph.add("map@1", [source], iterations=iterations, ratios=[ratio])
        self.assertEqual(len(graph.tasks), 4 + 5)

    def test_a_role_split_cluster_takes_storage_only_disks_from_the_reference(self):
        reference, _ = run_calibration(*lab(cs_nodes(3)))
        cluster, store, transport = lab(disagg_nodes(2, 2))
        artifact, models = run_calibration(cluster, store, transport, reference=reference)
        placeholder_read = (READ_BPS[0] + READ_BPS[1]) / 2
        placeholder_write = (WRITE_BPS[0] + WRITE_BPS[1]) / 2
        self.assertEqual(
            models.storage.read_bps, READ_BPS[:2] + (placeholder_read, placeholder_read)
        )
        self.assertEqual(
            models.storage.write_bps, WRITE_BPS[:2] + (placeholder_write, placeholder_write)
        )
        self.assertEqual(models.network.rx_bps, (50e6,) * 4)
        self.assert_compute_recovered(models)
        source = f"reference {reference['calibration_id']}"
        self.assertEqual(
            artifact["storage_sources"],
            [source, source, "unused: compute-only", "unused: compute-only"],
        )
        self.assertEqual({row["node"] for row in artifact["samples"]}, {2, 3})
        self.assertEqual({row["pool_node"] for row in artifact["samples"]}, {0, 1})
        for payload in transport.submitted.values():
            self.assertIn(payload["worker"], (2, 3))
            self.assertTrue(set(payload["targets"]) <= {0, 1})

    def test_a_storage_only_pool_without_a_reference_is_refused(self):
        with self.assertRaisesRegex(ValueError, "pool node0 .*--storage-from"):
            run_calibration(*lab(disagg_nodes(2, 1)))
        other = cs_nodes(3)[1:]
        reference, _ = run_calibration(*lab(other))
        with self.assertRaisesRegex(ValueError, "pool node0"):
            run_calibration(*lab(disagg_nodes(2, 1)), reference=reference)

    def test_droid_functions_are_fitted_on_compute_only_workers_of_a_split_cluster(self):
        reference, _ = run_calibration(*lab(cs_nodes(2)))
        cluster, store, transport = lab(disagg_nodes(2, 2))
        client = Client(cluster, Models.uncalibrated(4), store=store, transport=transport)
        with MINI.open("rb") as stream:
            ingest(client, stream, "droid-128-02047", placement=(1,))
        artifact, models = run_calibration(
            cluster, store, transport, droid="droid-128-02047", reference=reference
        )
        # One partition gives one sample per function: the fit reproduces it exactly
        # (fit_function_costs has its own test of recovering coefficients).
        self.assertEqual(
            [function for function, _ in models.compute.functions], sorted(DROID_FIXED)
        )
        self.assertLess(max(artifact["droid_fit_rmse_seconds"].values()), 1e-9)
        self.assertEqual({row["node"] for row in artifact["droid_samples"]}, {2})
        self.assertEqual({row["pool_node"] for row in artifact["droid_samples"]}, {1})
        droid = [p for p in transport.submitted.values() if p["task"]["function"] in DROID_FIXED]
        self.assertEqual(len(droid), 4)
        for payload in droid:
            self.assertEqual(payload["worker"], 2)
            self.assertEqual(set(payload["targets"]), {1})


if __name__ == "__main__":
    unittest.main()
