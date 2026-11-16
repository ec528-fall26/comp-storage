"""The new coordinator must speak to the deployed v2 workers byte for byte.

tests/fixtures/wire.json holds every request the ORIGINAL coordinator sent to the
ORIGINAL DurableWorkers for a three-task greedy run. Polling counts and ack tokens
depend on timing; everything else must match exactly.
"""

import json
from pathlib import Path
import unittest

from cephdag import coordinator
from cephdag.functions import synthetic
from cephdag.graph import Graph
from cephdag.model import Models
from tests.helpers import local_cluster

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "wire.json").read_text())


def run_wire_scenario():
    cluster, store, workers, transport = local_cluster()
    source = store.write("node1", "datasets/wire/part-000000", synthetic(4096, 5), partition=0)
    graph = Graph(graph_id="f" * 32)
    left, right = graph.add("split@1", [source], ratios=[0.5, 1], iterations=2)
    (middle,) = graph.add("map@1", [left], ratios=[0.5], iterations=3)
    graph.add("join@1", [middle, right], ratios=[0.25], iterations=1)
    result = coordinator.run(
        graph,
        Models(),
        "greedy",
        cluster=cluster,
        store=store,
        transport=transport,
        run_id=FIXTURE["run_id"],
    )
    return source, graph, result, transport.calls, store, workers


class WireCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source, cls.graph, cls.result, cls.calls, cls.store, cls.workers = run_wire_scenario()

    def calls_on(self, calls, path):
        return [(call["node"], call["body"]) for call in calls if call["path"] == path]

    def test_same_source_and_graph(self):
        self.assertEqual(self.source.to_dict(), FIXTURE["source"]["ref"])
        tasks = json.loads(json.dumps([task.to_dict() for task in self.graph.tasks.values()]))
        self.assertEqual(tasks, FIXTURE["graph"]["tasks"])

    def test_submit_bodies_are_byte_identical(self):
        self.assertEqual(
            self.calls_on(self.calls, "submit"), self.calls_on(FIXTURE["calls"], "submit")
        )

    def test_ack_bodies_match_apart_from_timing_dependent_tokens(self):
        def without_token(calls):
            return [
                (node, json.loads(body)["key"], sorted(json.loads(body))) for node, body in calls
            ]

        self.assertEqual(
            without_token(self.calls_on(self.calls, "ack")),
            without_token(self.calls_on(FIXTURE["calls"], "ack")),
        )

    def test_state_polls_ask_for_the_same_keys(self):
        recorded = {(node, body) for node, body in self.calls_on(FIXTURE["calls"], "state")}
        sent = {(node, body) for node, body in self.calls_on(self.calls, "state")}
        self.assertEqual(sent, recorded)

    def test_request_paths_are_the_v2_endpoints(self):
        self.assertLessEqual(
            {call["path"] for call in self.calls}, {"state", "submit", "ack", "warm"}
        )

    def test_outputs_and_record_shapes_match(self):
        self.assertEqual(self.result["final_outputs"], FIXTURE["final_outputs"])
        self.assertEqual(sorted(self.result), FIXTURE["result_keys"])
        key = json.loads(self.calls_on(self.calls, "ack")[0][1])["key"]
        node = self.calls_on(self.calls, "ack")[0][0]
        job = self.store.get_manifest(f"node{node}", f"runs/{key}/worker-job.json")
        self.assertEqual(sorted(job), FIXTURE["completed_job_keys"])
        self.assertEqual(sorted(job["result"]), FIXTURE["worker_result_keys"])
        self.assertEqual(sorted(self.workers[0].snapshot([])), FIXTURE["snapshot_keys"])


if __name__ == "__main__":
    unittest.main()
