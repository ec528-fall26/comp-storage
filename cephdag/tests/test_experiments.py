import contextlib
import dataclasses
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

from cephdag import coordinator, droid
from cephdag.client import Client
from cephdag.cluster import Cluster
from cephdag.functions import function, numeric
from cephdag.graph import Graph
from cephdag.model import ComputeModel, FunctionCost, Models, NetworkModel, StorageModel
from experiments import datasets, derive_models, run
from experiments.calibrate import fit_nonnegative
from experiments.oracle import verify_oracle
from experiments.summarize import summarize
from tests.helpers import cs_nodes, disagg_nodes, local_cluster, role_lab

FOUR_AT_ONCE = threading.Barrier(4, timeout=20)


@function("all-four", "1", inputs=["float32"], outputs=["float32"])
def all_four(inputs, params, output_sizes):
    """`numeric`, once four tasks of this function run at the same time."""
    FOUR_AT_ONCE.wait()
    return numeric(inputs, params, output_sizes)


def small_lab():
    cluster, store, workers, transport = local_cluster()
    client = Client(cluster, Models(), store=store, transport=transport)
    return client, workers


def synthetic_dataset(client, shards):
    # The Docker free-space guard has its own test; this machine's free space is irrelevant.
    with mock.patch.object(run, "require_capacity"):
        return run.synthetic_dataset(client, shards * 4096, 4096, "/")


def run_quietly(client, dataset, case, policies, directory, files=None):
    """run_matrix with one repeat of each policy; returns the rows it appended."""
    rows = run.make_rows(policies, 1, 9103)
    settings = {"seed": 9103, "budget_ms": None, "ceph_data": "/", "warm": False}
    settings["files"] = files or {}
    with mock.patch.object(run, "require_capacity"), contextlib.redirect_stdout(io.StringIO()):
        run.run_matrix(client, dataset, case, rows, settings, directory)
    return [json.loads(line) for line in (directory / "rows.jsonl").read_text().splitlines()]


class BothModesTests(unittest.TestCase):
    """One store, seen through a computational-storage and a disaggregated cluster file."""

    def setUp(self):
        cs, self.store, cs_transport = role_lab(cs_nodes(2))
        disagg, _, disagg_transport = role_lab(disagg_nodes(2, 2), self.store)
        self.cs = Client(cs, Models.uncalibrated(2), store=self.store, transport=cs_transport)
        self.disagg = Client(
            disagg, Models.uncalibrated(4), store=self.store, transport=disagg_transport
        )
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)

    def dataset(self, client, placement=(0, 1)):
        with mock.patch.object(run, "require_capacity"):
            return run.synthetic_dataset(client, 6 * 4096, 4096, "/", "shared", placement)

    def test_both_modes_reuse_one_dataset_and_agree_on_every_output(self):
        created = self.dataset(self.cs)
        self.assertEqual(self.dataset(self.disagg), created)
        with self.assertRaisesRegex(RuntimeError, "dataset shared exists with other"):
            self.dataset(self.disagg, placement=(1, 0))
        case = {"shape": "chain", "iterations": 2, "ratio": 0.5}
        files = {"cluster_sha256": "c" * 64, "models_sha256": "m" * 64}
        (self.directory / "cs").mkdir()
        (self.directory / "disagg").mkdir()
        cs_rows = run_quietly(
            self.cs, created, case, ["greedy", "data-local"], self.directory / "cs", files
        )
        disagg_rows = run_quietly(
            self.disagg, created, case, ["fifo-hash", "heft"], self.directory / "disagg"
        )
        self.assertEqual(
            [row["mode"] for row in cs_rows + disagg_rows], ["cs"] * 2 + ["disagg"] * 2
        )
        self.assertEqual(sorted(row["policy"] for row in cs_rows), ["data-local", "greedy"])
        self.assertEqual(sorted(row["policy"] for row in disagg_rows), ["fifo-hash", "heft"])
        for row in cs_rows + disagg_rows:
            self.assertTrue(row["verification"]["passed"])
            self.assertEqual(row["dataset"], "shared")
            self.assertEqual(row["dataset_bytes"], 6 * 4096)
            self.assertEqual((row["workload"], row["iterations"]), ("chain", 2))
            self.assertEqual(row["tasks"], 18)
        self.assertEqual(cs_rows[0]["cluster_sha256"], "c" * 64)
        digests = {row["verification"]["outputs_sha256"] for row in cs_rows + disagg_rows}
        self.assertEqual(len(digests), 1)
        # Every task on a compute-only node reads its inputs from, and writes its outputs
        # to, a storage-only node: all of a chain's 6 * 4096 * (1 + 2 * 0.5 + 2 * 0.25 +
        # 0.125) bytes cross the network, and only storage nodes send to compute nodes.
        for row in disagg_rows:
            traffic = row["network_payload_bytes"]
            self.assertEqual(traffic["bytes"], 6 * 4096 * 2.625)
            sent = sum(node["tx"] for node in traffic["per_node"].values())
            received = sum(node["rx"] for node in traffic["per_node"].values())
            self.assertEqual(sent, traffic["bytes"])
            self.assertEqual(received, traffic["bytes"])
            reads = 6 * 4096 * (1 + 0.5 + 0.25)
            for node in ("0", "1"):
                self.assertLessEqual(traffic["per_node"][node]["rx"], traffic["bytes"] - reads)
            self.assertEqual(sorted(row["network_interface_bytes"]), ["2", "3"])
        (data_local,) = [row for row in cs_rows if row["policy"] == "data-local"]
        self.assertEqual(data_local["network_payload_bytes"]["bytes"], 0)
        generated = [key for _, key in self.store.objects if key.startswith("runs/")]
        self.assertEqual(generated, [])
        self.assertEqual(self.disagg.dataset("shared"), created)

    def test_a_failed_verification_is_recorded_and_stops_the_matrix(self):
        created = self.dataset(self.cs)
        case = {"shape": "independent", "iterations": 1, "ratio": 1}
        failure = AssertionError("oracle mismatch")
        with (
            mock.patch.object(run, "verify_outputs", side_effect=failure),
            self.assertRaisesRegex(AssertionError, "oracle mismatch"),
        ):
            run_quietly(self.disagg, created, case, ["greedy", "heft"], self.directory)
        lines = (self.directory / "rows.jsonl").read_text().splitlines()
        (row,) = [json.loads(line) for line in lines]
        self.assertFalse(row["verification"]["passed"])
        self.assertEqual(row["verification"]["error"], "AssertionError: oracle mismatch")
        self.assertEqual(len(list(self.directory.glob("run-*.json"))), 1)
        kept = [key for _, key in self.store.objects if key.startswith("runs/")]
        self.assertEqual(len(kept), len(created.shards))

    def test_a_dataset_is_deleted_only_by_name_and_a_repeated_delete_is_harmless(self):
        created = self.dataset(self.cs)
        other = run.synthetic_dataset(self.cs, 4096, 4096, "/", "other", (1,))
        self.store.put_manifest("node0", "datasets/shared/droid.json", {"partitions": []})
        self.assertEqual(datasets.delete(self.disagg, ["shared"]), {"shared": len(created.shards)})
        remaining = sorted(key for _, key in self.store.objects) + sorted(
            key for _, key in self.store.manifests
        )
        self.assertEqual(remaining, ["datasets/other/part-000000", "datasets/other/manifest.json"])
        self.assertEqual(
            datasets.delete(self.cs, ["shared", "other"]), {"shared": None, "other": 1}
        )
        self.assertEqual(other.shards[0].pool, "node1")
        with self.assertRaisesRegex(ValueError, "exact dataset namespace"):
            self.store.remove_dataset_object("node0", "datasets/other2/x", "other")

    def test_the_command_line_records_the_files_it_ran_with(self):
        cluster_file = self.directory / "disagg.json"
        cluster_file.write_text(json.dumps({"nodes": disagg_nodes(2, 2)}))
        models = dataclasses.replace(Models.uncalibrated(4), calibrated=True, calibration_id="t")
        models_file = self.directory / "models.json"
        models_file.write_text(json.dumps(models.to_dict()))
        argv = [
            "run",
            *("--workload", "independent", "--iterations", "1", "--ratio", "0.5"),
            *("--policies", "fifo-hash,greedy", "--repeats", "1"),
            *("--dataset-gib", str(4 / 1024), "--shard-mib", "1"),
            *("--dataset", "shared", "--placement", "1,0", "--no-warm"),
            *("--models", str(models_file), "--cluster", str(cluster_file)),
            *("--output", str(self.directory / "out"), "--ceph-data", "/"),
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(run, "Client", lambda cluster, models: self.disagg),
            mock.patch.object(run, "warm", side_effect=AssertionError("warmed")),
            mock.patch.object(run, "require_capacity"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            run.main()
        lines = (self.directory / "out" / "rows.jsonl").read_text().splitlines()
        rows = [json.loads(line) for line in lines]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["cluster_file"], str(cluster_file))
            self.assertEqual(row["cluster_sha256"], run.file_sha256(cluster_file))
            self.assertEqual(row["models_sha256"], run.file_sha256(models_file))
            self.assertEqual(
                (row["mode"], row["dataset"], row["warmed"]), ("disagg", "shared", False)
            )
        shards = self.disagg.dataset("shared").shards
        self.assertEqual([shard.pool for shard in shards], ["node1", "node0"] * 2)

    def test_two_workers_per_compute_host_run_fifo_hash_on_models_derived_without_calibrating(
        self,
    ):
        disagg2, _, transport = role_lab(disagg_nodes(2, 2, slots=2), self.store)
        calibrated = dataclasses.replace(Models.uncalibrated(4), calibrated=True)
        models = derive_models.with_hosts(calibrated, disagg2.hosts)
        self.assertEqual(
            models.network.tx_bps, calibrated.network.tx_bps + calibrated.network.tx_bps[2:]
        )
        client = Client(disagg2, models, store=self.store, transport=transport)
        created = self.dataset(self.cs)
        case = {"shape": "independent", "iterations": 2, "ratio": 0.5}
        for mode in ("cs", "disagg2"):
            (self.directory / mode).mkdir()
        (cs_row,) = run_quietly(self.cs, created, case, ["data-local"], self.directory / "cs")
        (row,) = run_quietly(client, created, case, ["fifo-hash"], self.directory / "disagg2")
        self.assertEqual(row["mode"], "disagg2")
        self.assertTrue(row["verification"]["passed"])
        self.assertEqual(
            row["verification"]["outputs_sha256"], cs_row["verification"]["outputs_sha256"]
        )
        self.assertEqual(set(row["network_interface_bytes"]), {"2", "3"})
        (raw,) = (self.directory / "disagg2").glob("run-*.json")
        records = json.loads(raw.read_text())["tasks"].values()
        self.assertTrue(all(record["worker"] in (2, 3, 4, 5) for record in records))
        self.assertTrue(all(set(record["targets"]) <= {0, 1} for record in records))

    def test_both_workers_of_a_compute_host_run_tasks_at_the_same_time(self):
        disagg2, _, transport = role_lab(disagg_nodes(2, 2, slots=2), self.store)
        models = derive_models.with_hosts(Models.uncalibrated(4), disagg2.hosts)
        client = Client(disagg2, models, store=self.store, transport=transport)
        with mock.patch.object(run, "require_capacity"):
            four = run.synthetic_dataset(client, 4 * 4096, 4096, "/", "four", (0, 1))
        graph = Graph()
        graph.map("all-four@1", four, iterations=1, ratios=[0.5])
        result = client.submit(graph, "fifo-hash").wait(timeout=60).metrics()
        self.assertEqual(
            sorted(record["worker"] for record in result["tasks"].values()), [2, 3, 4, 5]
        )

    def test_derived_models_give_each_node_its_hosts_rates_and_are_never_overwritten(self):
        functions = tuple((name, FunctionCost(0.0, 5e-9, 1e-8)) for name in droid.FUNCTIONS)
        clusters = Path(run.__file__).parents[1] / "cephdag/clusters"
        for base, slots in (("disagg", "disagg2"), ("cs", "cs2")):
            with self.subTest(mode=slots):
                hosts = len(Cluster.load(clusters / f"cephdag8-{base}.json").nodes)
                rates = tuple(1e8 + node for node in range(hosts))
                source = Models(
                    ComputeModel(functions=functions),
                    StorageModel(rates, rates),
                    NetworkModel(rates, rates),
                    calibrated=True,
                    calibration_id="c",
                )
                (self.directory / f"{base}.json").write_text(json.dumps(source.to_dict()))
                cluster_file = clusters / f"cephdag8-{slots}.json"
                argv = ["derive_models", str(self.directory / f"{base}.json")]
                argv += [str(self.directory / f"{slots}.json"), "--cluster", str(cluster_file)]
                argv.append("--fused")
                with (
                    mock.patch.object(sys, "argv", argv),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    derive_models.main()
                    with self.assertRaises(FileExistsError):
                        derive_models.main()
                derived = Models.from_dict(
                    json.loads((self.directory / f"{slots}.json").read_text())
                )
                cluster = Cluster.load(cluster_file)
                self.assertEqual(derived.node_count, len(cluster.nodes))
                self.assertEqual(derived.network.tx_bps, tuple(rates[h] for h in cluster.hosts))
                self.assertEqual(derived.storage.read_bps, tuple(rates[h] for h in cluster.hosts))
                self.assertEqual(derived.calibration_id, "c+slots2+fused")
                self.assertTrue(derived.calibrated)
                self.assertEqual(
                    [name for name, _ in derived.compute.functions],
                    sorted([*droid.FUNCTIONS, droid.FUSED]),
                )
        with self.assertRaisesRegex(ValueError, "the models describe 16 hosts"):
            derive_models.with_hosts(Models.uncalibrated(16), Cluster.load(cluster_file).hosts)

    def test_cs2_runs_both_workers_of_every_storage_host_on_local_data(self):
        cs2, _, transport = role_lab(cs_nodes(2, slots=2), self.store)
        self.assertEqual(cs2.hosts, (0, 1, 0, 1))
        models = derive_models.with_hosts(Models.uncalibrated(2), cs2.hosts)
        client = Client(cs2, models, store=self.store, transport=transport)
        created = self.dataset(self.cs)
        case = {"shape": "independent", "iterations": 2, "ratio": 0.5}
        for mode in ("cs", "cs2"):
            (self.directory / mode).mkdir()
        (cs_row,) = run_quietly(self.cs, created, case, ["data-local"], self.directory / "cs")
        rows = run_quietly(client, created, case, ["data-local", "heft"], self.directory / "cs2")
        workers = {}
        for raw in (self.directory / "cs2").glob("run-*.json"):
            for record in json.loads(raw.read_text())["tasks"].values():
                workers.setdefault(record["worker"], []).append(record["targets"])
        for row in rows:
            with self.subTest(policy=row["policy"]):
                self.assertEqual(row["mode"], "cs2")
                self.assertTrue(row["verification"]["passed"])
                self.assertEqual(
                    row["verification"]["outputs_sha256"], cs_row["verification"]["outputs_sha256"]
                )
                self.assertEqual(row["network_payload_bytes"]["bytes"], 0)
                self.assertEqual(set(row["network_interface_bytes"]), {"0", "1"})
        self.assertEqual(set(workers), {0, 1, 2, 3})
        for worker, targets in workers.items():
            self.assertTrue(all(target == [cs2.hosts[worker]] for target in targets), worker)

    def test_two_workers_on_one_host_count_its_interface_once(self):
        cluster = Cluster.from_dict({"nodes": disagg_nodes(2, 2, slots=2)})
        self.assertEqual(cluster.interface_nodes, (2, 3))
        host_of = {2: 2, 3: 3, 4: 2, 5: 3}
        counters = {2: {"tx": 20, "rx": 2}, 3: {"tx": 30, "rx": 3}}
        before = [{"node": node, "counters": {"tx": 0, "rx": 0}} for node in host_of]
        after = [{"node": node, "counters": counters[host]} for node, host in host_of.items()]
        sent = coordinator.total_transmit_bytes(
            [snapshot["counters"] for snapshot in coordinator.host_snapshots(cluster, before)],
            [snapshot["counters"] for snapshot in coordinator.host_snapshots(cluster, after)],
        )
        self.assertEqual(sent, 50)
        traffic = run.interface_traffic({"counter_before": before, "counter_after": after}, cluster)
        self.assertEqual(traffic, {"2": {"tx": 20, "rx": 2}, "3": {"tx": 30, "rx": 3}})

    def test_mode_of_a_cluster(self):
        self.assertEqual(run.cluster_mode(self.cs.cluster), "cs")
        self.assertEqual(run.cluster_mode(self.disagg.cluster), "disagg")
        mixed = Cluster.from_dict({"nodes": cs_nodes(1) + disagg_nodes(1, 1)[1:]})
        self.assertEqual(run.cluster_mode(mixed), "mixed")
        shared = Cluster.from_dict({"nodes": disagg_nodes(2, 2, slots=2)})
        self.assertEqual(run.cluster_mode(shared), "disagg2")


class ExperimentTests(unittest.TestCase):
    def test_full_matrix_on_an_in_process_cluster_with_all_checks(self):
        client, workers = small_lab()
        dataset = synthetic_dataset(client, 5)
        self.assertEqual(
            [shard.pool for shard in dataset.shards], ["node0", "node0", "node1", "node2", "node0"]
        )
        self.assertEqual(synthetic_dataset(client, 5), dataset)
        rows = run.make_rows(["greedy", "heft", "random"], 2, 9103)
        case = {"shape": "divergent", "iterations": 2, "ratio": 0.5}
        settings = {"seed": 9103, "budget_ms": None, "ceph_data": "/"}
        quiet = contextlib.redirect_stdout(io.StringIO())
        with (
            mock.patch.object(run, "require_capacity"),
            tempfile.TemporaryDirectory() as directory,
            quiet,
        ):
            results = run.run_matrix(client, dataset, case, rows, settings, Path(directory))
            self.assertEqual(len(list(Path(directory).glob("run-*.json"))), 6)
        self.assertTrue(
            all(result["independent_oracle"]["scalar_values_checked"] > 0 for result in results)
        )
        generated = [key for pool, key in client.store.objects if key.startswith("runs/")]
        self.assertEqual(generated, [])
        self.assertEqual(
            len([key for pool, key in client.store.objects if key.startswith("datasets/")]), 5
        )
        self.assertTrue(all(not worker.jobs for worker in workers))

    def test_oracle_rejects_a_corrupted_output(self):
        client, _ = small_lab()
        dataset = synthetic_dataset(client, 2)
        graph = run.build_workload(dataset, "chain", 3, 1)
        result = client.submit(graph, "greedy").wait(timeout=30).metrics()
        self.assertEqual(
            verify_oracle(client.store, result, graph, run.SOURCE_SEED)["sampled_outputs"], 2
        )
        output = result["final_outputs"][0]
        data = bytearray(client.store.objects[output["pool"], output["key"]])
        # The top exponent bit of the first float32: a low mantissa byte can move a value by
        # less than the oracle's tolerance.
        data[3] ^= 0x40
        client.store.objects[output["pool"], output["key"]] = bytes(data)
        output["sha256"] = ""
        with self.assertRaisesRegex(AssertionError, "oracle mismatch"):
            verify_oracle(client.store, result, graph, run.SOURCE_SEED)

    def test_cleanup_touches_only_the_runs_own_namespace(self):
        client, _ = small_lab()
        source = client.store.write("node0", "datasets/x", b"\0" * 4)
        fake = {
            "run_id": "e" * 32,
            "tasks": {"t000000": {"result": {"outputs": [source.to_dict()]}}},
        }
        with self.assertRaisesRegex(ValueError, "generated run namespace"):
            run.cleanup_outputs(client.store, "node0", fake)
        self.assertIn(("node0", "datasets/x"), client.store.objects)

    def test_capacity_and_topology_gates(self):
        with self.assertRaisesRegex(RuntimeError, "headroom"):
            run.require_capacity(8.5 * 2**30, 2**30, 0)
        run.require_capacity(10 * 2**30, 2**30, 0)
        evidence = {"fsid": "f", "aggregate_mbit": {"shared_tx": 395, "shared_rx": 401}}
        run.require_validated(evidence, "f", 400)
        for fsid, bandwidth in (("other", 400), ("f", 1000)):
            with self.assertRaisesRegex(RuntimeError, "validation"):
                run.require_validated(evidence, fsid, bandwidth)

    def test_rows_are_a_reproducible_shuffle_of_every_policy_per_repeat(self):
        rows = run.make_rows(["a", "b", "c"], 3, 1)
        self.assertEqual(rows, run.make_rows(["a", "b", "c"], 3, 1))
        for repeat in range(3):
            self.assertEqual(
                sorted(row["scheduler"] for row in rows if row["repeat"] == repeat), ["a", "b", "c"]
            )
        self.assertEqual(
            run.policy_options("native-mcts-heuristic-8", 5, 40), {"seed": 5, "budget_ms": 40}
        )
        self.assertEqual(run.policy_options("heft", 5, 40), {})

    def test_nonnegative_fit_recovers_coefficients(self):
        generator = np.random.default_rng(0)
        features = np.column_stack([np.ones(20), generator.random(20), generator.random(20)])
        targets = features @ np.array([0.5, 0.0, 2.0])
        np.testing.assert_allclose(fit_nonnegative(features, targets), [0.5, 0.0, 2.0], atol=1e-9)

    def test_summary_reports_median_and_range_per_case_and_policy(self):
        def result(policy, wall):
            case = {"shape": "chain", "iterations": 1, "ratio": 0.25}
            return {
                "case": case,
                "scheduler": policy,
                "wall_seconds": wall,
                "scheduler_seconds": 1,
                "network_tx_bytes": 0,
            }

        summary = summarize(
            [result("heft", 3), result("heft", 1), result("heft", 10), result("greedy", 4)]
        )
        self.assertEqual(
            summary["chain-1-0.25", "heft"]["wall_seconds"], {"median": 3, "min": 1, "max": 10}
        )
        self.assertEqual(summary["chain-1-0.25", "greedy"]["runs"], 1)


if __name__ == "__main__":
    unittest.main()
