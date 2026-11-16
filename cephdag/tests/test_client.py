import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from cephdag import Client, Cluster, Graph, Models
from cephdag.cluster import LAB_CLUSTER
from cephdag.functions import synthetic
from cephdag.storage import MemoryStore
from examples import custom_function, quickstart
from examples.custom_function import keyword_count_graph
from tests.helpers import local_cluster

LOGS = [b"ok\nerror: disk\nok\n", b"error: net\nerror: cpu\n", b"ok\n", b"error: fan\n"]


def local_client(node_count=3, models=None):
    cluster, store, workers, transport = local_cluster(node_count)
    return Client(cluster, models, store=store, transport=transport), workers


class ClientTests(unittest.TestCase):
    def test_datasets_are_placed_round_robin_and_can_be_reopened(self):
        client, _ = local_client()
        data = client.put_dataset("numbers", [synthetic(400, seed) for seed in range(4)])
        self.assertEqual(
            [shard.pool for shard in data.shards], ["node0", "node1", "node2", "node0"]
        )
        self.assertEqual(client.dataset("numbers"), data)
        placed = client.put_dataset("placed", [b"a", b"b"], placement=[2, 2], dtype="lines")
        self.assertEqual([shard.pool for shard in placed.shards], ["node2", "node2"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            client.put_dataset("numbers", [b""])
        with self.assertRaises(KeyError):
            client.dataset("missing")

    def test_map_reduce_job_produces_readable_outputs_and_metrics(self):
        client, _ = local_client()
        data = client.put_dataset("numbers", [synthetic(4096, seed) for seed in range(5)])
        graph = Graph()
        mapped = graph.map("map@1", data, iterations=4, ratios=[0.5])
        graph.reduce("join@1", mapped, iterations=1, ratios=[0.5])
        job = client.submit(graph, "heft")
        self.assertEqual(job.wait(timeout=30).status(), "completed")
        (result,) = job.outputs()
        self.assertEqual(len(client.read(result)), result.size)
        self.assertEqual(job.metrics()["scheduler"], "heft")
        self.assertEqual(job.metrics()["dispatches"], 9)

    def test_a_policy_object_and_scheduler_options_are_accepted(self):
        client, _ = local_client()
        data = client.put_dataset("numbers", [synthetic(400, seed) for seed in range(3)])
        graph = Graph()
        graph.map("map@1", data)
        seeded = client.submit(graph, "random", seed=3).wait(timeout=30)
        self.assertEqual(seeded.metrics()["scheduler_options"], {"seed": 3})

        class EverythingOnNodeZero:
            def choose(self, state):
                local = (
                    action for action in state.candidates(all_targets=False) if action.worker == 0
                )
                return next(local, None)

        custom = client.submit(graph, EverythingOnNodeZero()).wait(timeout=30)
        self.assertEqual({record["worker"] for record in custom.metrics()["tasks"].values()}, {0})

    def test_submitted_graph_cannot_be_changed_and_failures_are_reported(self):
        client, _ = local_client()
        data = client.put_dataset("numbers", [synthetic(400, 1)])
        graph = Graph()
        graph.map("map@1", data)
        job = client.submit(graph, "no-such-policy")
        with self.assertRaises(RuntimeError):
            graph.map("map@1", data)
        with self.assertRaisesRegex(RuntimeError, "unknown scheduler"):
            job.wait(timeout=30)
        self.assertEqual(job.status(), "failed")
        with self.assertRaisesRegex(RuntimeError, "job is failed"):
            job.outputs()

    def test_models_from_a_file_and_node_count_checks(self):
        models = Models.uncalibrated(3)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "models.json")
            with open(path, "w") as file:
                json.dump(models.to_dict(), file)
            client, _ = local_client(models=path)
        self.assertEqual(client.models, models)
        with self.assertRaisesRegex(ValueError, "disagree"):
            local_client(node_count=2, models=Models())

    def test_lab_cluster_file_describes_the_three_node_lab(self):
        cluster = Cluster.load(LAB_CLUSTER)
        self.assertEqual(cluster.pools, ("node0", "node1", "node2"))
        self.assertEqual(cluster.nodes[2].address, ("172.30.82.10", 8080))
        self.assertEqual(cluster.metadata_pool, "node0")


class CustomFunctionTests(unittest.TestCase):
    """examples/custom_function.py: an output whose size depends on the data."""

    def run_keyword_count(self, node_count, policy):
        client, _ = local_client(node_count)
        dataset = client.put_dataset("logs", LOGS, dtype="lines")
        graph, _ = keyword_count_graph(dataset, "error")
        job = client.submit(graph, policy).wait(timeout=30)
        (total,) = job.outputs()
        return int(client.read(total)), job.metrics(), graph

    def test_counts_matching_lines_on_any_cluster_size(self):
        for node_count, policy in (
            (3, "greedy"),
            (3, "beam-4"),
            (2, "heft"),
            (4, "stealing"),
            (1, "data-local"),
        ):
            with self.subTest(nodes=node_count, policy=policy):
                count, _, _ = self.run_keyword_count(node_count, policy)
                self.assertEqual(count, 4)

    def test_actual_sizes_replace_estimates_after_a_task_finishes(self):
        _, metrics, graph = self.run_keyword_count(3, "greedy")
        records = metrics["tasks"]
        grep_outputs = {
            task_id: records[task_id]["result"]["outputs"][0]["size"]
            for task_id in ("t000000", "t000001")
        }
        self.assertEqual(grep_outputs, {"t000000": len(b"error: disk\n"), "t000001": len(LOGS[1])})
        self.assertEqual(graph.tasks["t000000"].output_sizes, (int(len(LOGS[0]) * 0.1),))

    def test_both_examples_run_end_to_end_in_process(self):
        expected = {custom_function: "lines with 'error': 3", quickstart: "2097152 bytes"}
        for example, line in expected.items():
            with self.subTest(example=example.__name__):
                client, workers = local_client(models=Models())
                printed = io.StringIO()
                with mock.patch.object(example, "Client", return_value=client):
                    with contextlib.redirect_stdout(printed):
                        example.main()
                self.assertIn(line, printed.getvalue())
                self.assertTrue(all(not worker.jobs for worker in workers))

    def test_store_rejects_reading_a_tampered_result(self):
        store = MemoryStore()
        ref = store.write("node0", "runs/x", b"abc", dtype="lines")
        store.objects["node0", "runs/x"] = b"abd"
        with self.assertRaisesRegex(ValueError, "checksum"):
            store.read(ref)


if __name__ == "__main__":
    unittest.main()
