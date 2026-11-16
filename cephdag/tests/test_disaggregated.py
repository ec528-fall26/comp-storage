"""Disaggregated clusters: storage-only and compute-only nodes on one network."""

import dataclasses
import os
from pathlib import Path
import shlex
import sys
import unittest
from unittest import mock

from cephdag import Client, Graph
from cephdag import worker as worker_module
from cephdag.cluster import Cluster, Node
from cephdag.functions import synthetic
from cephdag.graph import ObjectRef
from cephdag.model import Dispatch, Models, Simulation, StorageModel, hashed_targets
from cephdag.policies import make_policy
from cephdag.storage import MemoryStore
from cephdag.worker import DurableWorker
from infra import lab as lifecycle
from tests.helpers import LocalTransport, cs_nodes, disagg_nodes, terminal_job, zero_counters
from tests.test_policies import chain_and_join

ROOT = Path(__file__).resolve().parents[1]
EIGHT = lifecycle.Lab.load(ROOT / "infra" / "labs" / "cephdag8.json")

STORAGE = (0, 1)
COMPUTE = (2, 3)
SPLIT = {
    "nodes": [
        {"pool": "node0", "worker": None},
        {"pool": "node1", "worker": None},
        {"pool": None, "worker": "http://127.0.0.1:8082"},
        {"pool": None, "worker": "http://127.0.0.1:8083"},
    ]
}
PYTHON_POLICIES = (
    "fifo-hash",
    "greedy",
    "heft",
    "greedy-wait",
    "stealing",
    "data-local",
    "random",
)


def split_client():
    cluster = Cluster.from_dict(SPLIT)
    store = MemoryStore(cluster.pools)
    workers = {
        node: DurableWorker(
            node,
            store,
            cluster.pools,
            counters=zero_counters,
            journal_pool=cluster.metadata_pool,
        )
        for node in cluster.worker_nodes
    }
    transport = LocalTransport(workers)
    return Client(cluster, Models.uncalibrated(4), store=store, transport=transport), transport


def simulate(policy_name, models, graph):
    """Run a policy to completion on the split cluster's forecast; return (state, actions)."""
    cluster = Cluster.from_dict(SPLIT)
    state = Simulation(graph, models, cluster.pools, workers=cluster.worker_nodes)
    policy = make_policy(policy_name)
    actions = []
    while not state.done:
        action = policy.choose(state)
        if action is None:
            state.advance()
        else:
            actions.append((action, state.stages_for(action), state.transfer_bytes(action)))
            state.dispatch(action)
    return state, actions


class ClusterRoleTests(unittest.TestCase):
    def test_a_node_may_have_only_a_pool_or_only_a_worker(self):
        cluster = Cluster.from_dict(SPLIT)
        self.assertEqual(cluster.pools, ("node0", "node1", None, None))
        self.assertEqual(cluster.storage_nodes, STORAGE)
        self.assertEqual(cluster.worker_nodes, COMPUTE)
        self.assertEqual(cluster.metadata_pool, "node0")
        compute_first = Cluster.from_dict({"nodes": SPLIT["nodes"][::-1]})
        self.assertEqual(compute_first.metadata_pool, "node1")

    def test_clusters_without_a_worker_or_a_pool_are_rejected(self):
        storage, _, compute, _ = (Node(**node) for node in SPLIT["nodes"])
        cases = {
            "a pool, a worker or both": ((storage, compute, Node(None, None)), "node0"),
            "at least one worker": ((storage,), "node0"),
            "metadata pool": ((compute,), None),
        }
        for message, (nodes, metadata_pool) in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    Cluster(nodes, metadata_pool)
        with self.assertRaisesRegex(ValueError, "metadata pool"):
            Cluster((storage, compute), "node2")

    def test_datasets_go_round_robin_over_storage_nodes_only(self):
        client, _ = split_client()
        data = client.put_dataset("numbers", [synthetic(400, seed) for seed in range(3)])
        self.assertEqual([shard.pool for shard in data.shards], ["node0", "node1", "node0"])


class SplitClusterSimulationTests(unittest.TestCase):
    def test_tasks_run_on_workers_and_outputs_go_to_pools_with_every_byte_on_the_network(self):
        graph = chain_and_join(("node0", "node1"))
        every_byte = sum(graph.size(ref) for task in graph.tasks.values() for ref in task.inputs)
        every_byte += sum(sum(task.output_sizes) for task in graph.tasks.values())
        for name in PYTHON_POLICIES:
            with self.subTest(policy=name):
                state, actions = simulate(name, Models.uncalibrated(4), graph)
                self.assertEqual(len(actions), len(graph.tasks))
                for action, stages, moved in actions:
                    self.assertIn(action.worker, COMPUTE)
                    self.assertTrue(set(action.targets) <= set(STORAGE))
                    task = graph.tasks[action.task]
                    reads = [stage for stage in stages if stage.kind == "read"]
                    writes = [stage for stage in stages if stage.kind == "write"]
                    self.assertEqual(len(reads), len(task.inputs))
                    for stage in reads:
                        source = int(stage.resources[0].split(":")[1])
                        self.assertEqual(
                            stage.resources,
                            (f"read:{source}", f"tx:{source}", f"rx:{action.worker}"),
                        )
                    for stage, target in zip(writes, action.targets):
                        self.assertEqual(
                            stage.resources,
                            (f"write:{target}", f"tx:{action.worker}", f"rx:{target}"),
                        )
                    read_bytes = sum(stage.remaining for stage in reads)
                    self.assertEqual(moved, read_bytes + sum(task.output_sizes))
                self.assertEqual(state.remote_bytes, every_byte)

    def test_compute_only_disks_change_no_decision(self):
        graph = chain_and_join(("node0", "node1"))
        normal = Models.uncalibrated(4)
        absurd_disks = dataclasses.replace(
            normal,
            storage=StorageModel(
                normal.storage.read_bps[:2] + (1e15, 1.0),
                normal.storage.write_bps[:2] + (1.0, 1e15),
            ),
        )
        for name in ("fifo-hash", "greedy", "heft", "greedy-wait", "stealing"):
            with self.subTest(policy=name):
                expected, expected_actions = simulate(name, normal, graph)
                actual, actual_actions = simulate(name, absurd_disks, graph)
                self.assertEqual(actual_actions, expected_actions)
                self.assertEqual(actual.time, expected.time)
        cluster = Cluster.from_dict(SPLIT)
        fresh = [
            Simulation(graph, models, cluster.pools, workers=cluster.worker_nodes)
            for models in (normal, absurd_disks)
        ]
        self.assertEqual(fresh[0].tail_estimate(), fresh[1].tail_estimate())

    def test_storage_only_workers_and_compute_only_targets_are_infeasible(self):
        cluster = Cluster.from_dict(SPLIT)
        graph = chain_and_join(("node0", "node1"))
        state = Simulation(
            graph, Models.uncalibrated(4), cluster.pools, workers=cluster.worker_nodes
        )
        self.assertEqual(state.free_workers(), [2, 3])
        self.assertEqual(state.tail_estimate(), state.unassigned_work / 2)
        task = state.ready(1)[0]
        for action in (Dispatch(task, 0, (0,)), Dispatch(task, 2, (3,))):
            with self.subTest(action=action):
                with self.assertRaisesRegex(ValueError, "infeasible"):
                    state.clone().dispatch(action)

    def test_policies_that_need_both_roles_on_a_node_say_so(self):
        cluster = Cluster.from_dict(SPLIT)
        state = Simulation(
            chain_and_join(("node0", "node1")),
            Models.uncalibrated(4),
            cluster.pools,
            workers=cluster.worker_nodes,
        )
        for name in ("beam-4", "native-mcts-heuristic-8", "single-node"):
            with self.subTest(policy=name):
                with self.assertRaisesRegex(ValueError, "both a worker and a pool"):
                    make_policy(name).choose(state)


def independent_maps(storage, per_node):
    """`per_node` 4 MiB map tasks on the data of each of `storage` pools."""
    graph = Graph()
    for index in range(storage * per_node):
        source = ObjectRef(f"node{index % storage}", f"d/{index}", 4 * 2**20, partition=index)
        graph.add("map@1", [source], ratios=[0.25], iterations=2)
    return graph


def run_policy(name, nodes, graph):
    """Every Dispatch `name` makes running `graph` to completion on cluster-file `nodes`."""
    cluster = Cluster.from_dict({"nodes": nodes})
    models = Models.uncalibrated(len(nodes))
    state = Simulation(graph, models, cluster.pools, workers=cluster.worker_nodes)
    policy = make_policy(name)
    actions = []
    while not state.done:
        action = policy.choose(state)
        if action is None:
            state.advance()
        else:
            actions.append(action)
            state.dispatch(action)
    return actions


class RoleSplitPlacementTests(unittest.TestCase):
    """On a role-split cluster the storage system places every output, as CRUSH would."""

    def test_heft_and_greedy_spread_outputs_over_every_pool_by_the_fifo_hash(self):
        for storage, compute in ((2, 2), (8, 8)):
            graph = independent_maps(storage, 8)
            pools = tuple(range(storage))
            for name in ("heft", "greedy"):
                with self.subTest(cluster=f"{storage}+{compute}", policy=name):
                    actions = run_policy(name, disagg_nodes(storage, compute), graph)
                    self.assertEqual(len(actions), len(graph.tasks))
                    for action in actions:
                        task = graph.tasks[action.task]
                        self.assertEqual(action.targets, hashed_targets(task, pools))
                    targets = [node for action in actions for node in action.targets]
                    self.assertEqual(set(targets), set(pools))

    def test_every_policy_places_like_fifo_hash_on_a_role_split_cluster(self):
        graph = chain_and_join(("node0", "node1"))
        expected = {
            action.task: action.targets
            for action in run_policy("fifo-hash", disagg_nodes(2, 2), graph)
        }
        for name in PYTHON_POLICIES:
            with self.subTest(policy=name):
                actions = run_policy(name, disagg_nodes(2, 2), graph)
                self.assertEqual({action.task: action.targets for action in actions}, expected)

    def test_a_computational_storage_cluster_still_offers_every_placement(self):
        graph = independent_maps(3, 2)
        cluster = Cluster.from_dict({"nodes": cs_nodes(3)})
        state = Simulation(
            graph, Models.uncalibrated(3), cluster.pools, workers=cluster.worker_nodes
        )
        self.assertFalse(state.role_split)
        task = state.ready(1)[0]
        self.assertEqual(list(state.target_options(task)), [(0,), (1,), (2,)])
        self.assertEqual(len(list(state.candidates())), len(graph.tasks) * 3 * 3)
        local = {
            action.targets for action in state.candidates(all_targets=False) if action.worker == 1
        }
        self.assertEqual(local, {(1,)})
        heft = run_policy("heft", cs_nodes(3), graph)
        self.assertTrue(all(action.targets == (action.worker,) for action in heft))


class FifoHashTests(unittest.TestCase):
    def test_oldest_ready_task_goes_to_the_lowest_free_worker_and_outputs_to_hashed_pools(self):
        graph = Graph()
        for index in range(8):
            graph.add("map@1", [ObjectRef(f"p{index % 4}", f"d/{index}", 4096)], ratios=[1])
        state = Simulation(graph, Models.uncalibrated(4), ("p0", "p1", "p2", "p3"))
        policy = make_policy("fifo-hash")
        placed = []
        while not state.done:
            action = policy.choose(state)
            if action is None:
                state.advance()
                continue
            self.assertEqual(action.task, state.ready(1)[0])
            self.assertEqual(action.worker, state.free_workers()[0])
            placed.append(action.targets[0])
            state.dispatch(action)
        self.assertEqual(len(placed), len(graph.tasks))
        # A stable hash: the same placement in every process and run, unlike hash().
        self.assertEqual(placed, [3, 3, 1, 1, 2, 3, 1, 3])


class SplitClusterRunTests(unittest.TestCase):
    def test_runs_compute_only_on_workers_and_store_only_in_pools(self):
        for name in ("fifo-hash", "greedy", "heft"):
            with self.subTest(policy=name):
                client, transport = split_client()
                data = client.put_dataset("numbers", [synthetic(4096, seed) for seed in range(6)])
                graph = Graph()
                mapped = graph.map("map@1", data, iterations=2, ratios=[0.5])
                graph.reduce("join@1", mapped, iterations=1, ratios=[0.5])
                result = client.submit(graph, name).wait(timeout=60).metrics()
                self.assertEqual(len(result["tasks"]), len(graph.tasks))
                for record in result["tasks"].values():
                    self.assertIn(record["worker"], COMPUTE)
                    self.assertTrue(set(record["targets"]) <= set(STORAGE))
                    for output in record["result"]["outputs"]:
                        self.assertIn(output["pool"], ("node0", "node1"))
                self.assertEqual({call["node"] for call in transport.calls}, set(COMPUTE))
                (final,) = result["final_outputs"]
                self.assertEqual(result["verified_final_bytes"], final["size"])


def lab_worker_commands(lab, node, mode):
    """The command lines infra/lab.py runs in /app to start the workers of `node`'s container
    in `mode`."""
    lines = []
    with (
        mock.patch.object(lifecycle, "running_workers", return_value=[]),
        mock.patch.object(lifecycle, "daemon", lambda lab, node, line: lines.append(line)),
    ):
        lifecycle.start_workers(lab, node, mode, ["cephdag.droid"])
    commands = []
    for line in lines:
        words = shlex.split(line)
        if words[:4] != ["cd", "/app", "&&", "exec"]:
            raise AssertionError(f"unexpected worker command: {line}")
        commands.append(words[4 : words.index(">>")])
    return commands


def start_worker(command):
    """Run cephdag.worker's main with `command`'s arguments from the repository root, as
    from /app; return the worker it would serve, its address and its store."""
    if command[:3] != ["python3", "-m", "cephdag.worker"]:
        raise AssertionError(f"not a worker command: {command}")
    served = {}

    def serve(worker, host, port):
        served.update(worker=worker, address=(host, port))

    directory = os.getcwd()
    os.chdir(ROOT)
    try:
        with (
            mock.patch.object(sys, "argv", ["cephdag.worker", *command[3:]]),
            mock.patch.object(
                worker_module, "RadosStore", lambda cluster: MemoryStore(cluster.pools)
            ),
            mock.patch.object(worker_module, "serve", serve),
        ):
            worker_module.main()
    finally:
        os.chdir(directory)
    # The container's eth0 counters; this machine may have no eth0.
    served["worker"].counters = zero_counters
    return served["worker"], served["address"]


class LabWorkerCommandTests(unittest.TestCase):
    def test_compute_only_workers_start_from_the_lab_command_line_and_journal_in_node0(self):
        for j in range(EIGHT.compute_nodes):
            node = EIGHT.storage_nodes + j
            with self.subTest(container=EIGHT.container(node)):
                self.assertEqual(EIGHT.container(node), f"cephdag8-c{j}")
                (command,) = lab_worker_commands(EIGHT, node, "disagg")
                self.assertIn("cephdag/clusters/cephdag8-disagg.json", command)
                served, address = start_worker(command)
                self.assertEqual(served.node, node)
                self.assertEqual(served.journal_pool, "node0")
                self.assertEqual(address, (f"172.30.{152 + j}.10", 8080))
                self.run_one_task(served, node, source_node=j % EIGHT.storage_nodes)

    def test_disagg2_runs_a_second_worker_per_compute_container_on_the_next_port(self):
        for j in range(EIGHT.compute_nodes):
            node = EIGHT.storage_nodes + j
            with self.subTest(container=EIGHT.container(node)):
                commands = lab_worker_commands(EIGHT, node, "disagg2")
                served = [start_worker(command) for command in commands]
                self.assertEqual(
                    [(worker.node, address) for worker, address in served],
                    [
                        (node, (f"172.30.{152 + j}.10", 8080)),
                        (node + EIGHT.compute_nodes, (f"172.30.{152 + j}.10", 8081)),
                    ],
                )
                for worker, _ in served:
                    self.assertEqual(len(worker.pools), 24)
                    self.assertEqual(worker.journal_pool, "node0")
                    self.run_one_task(worker, worker.node, source_node=j % EIGHT.storage_nodes)

    def test_cs2_runs_a_second_worker_per_storage_container_that_journals_in_its_pool(self):
        for node in EIGHT.storage:
            with self.subTest(container=EIGHT.container(node)):
                commands = lab_worker_commands(EIGHT, node, "cs2")
                served = [start_worker(command) for command in commands]
                self.assertEqual(
                    [(worker.node, address) for worker, address in served],
                    [
                        (node, (f"172.30.{144 + node}.10", 8080)),
                        (node + EIGHT.storage_nodes, (f"172.30.{144 + node}.10", 8081)),
                    ],
                )
                for worker, _ in served:
                    self.assertEqual(len(worker.pools), 16)
                    self.assertEqual(worker.journal_pool, f"node{node}")
                    self.run_one_task(worker, worker.node, source_node=node)

    def test_storage_workers_journal_in_their_own_pool(self):
        (command,) = lab_worker_commands(EIGHT, 7, "cs")
        served, address = start_worker(command)
        self.assertEqual((served.node, served.journal_pool), (7, "node7"))
        self.assertEqual(address, ("172.30.151.10", 8080))
        self.run_one_task(served, 7, source_node=7)

    def run_one_task(self, served, node, source_node):
        """A map task read from and written to `source_node`'s pool, through the worker API."""
        store = served.store
        source = store.write(f"node{source_node}", "datasets/x/part-000000", synthetic(4096, 1))
        graph = Graph()
        (output,) = graph.add("map@1", [source], iterations=1, ratios=[0.5])
        payload = {
            "run_id": "a" * 32,
            "worker": node,
            "targets": [source_node],
            "inputs": [source.to_dict()],
            "task": graph.tasks[output.task].to_dict(),
        }
        served.handle("/submit", payload)
        job = terminal_job(served, f"{'a' * 32}/{output.task}")
        self.assertEqual(job["status"], "completed", job.get("error"))
        (stored,) = job["result"]["outputs"]
        self.assertEqual(stored["pool"], f"node{source_node}")
        journal = served.journal_pool
        self.assertIn((journal, f"runs/{'a' * 32}/{output.task}/complete.json"), store.manifests)
        served.handle("/ack", {"key": job["key"], "token": job["ack_token"]})
        self.assertFalse(served.jobs)


class NativeRefusalTests(unittest.TestCase):
    def test_native_policies_refuse_the_disaggregated_lab_and_more_than_twelve_nodes(self):
        disagg = Cluster.load(ROOT / "cephdag" / "clusters" / "cephdag8-disagg.json")
        self.assertEqual(len(disagg.nodes), 16)
        thirteen = Cluster(tuple(Node(f"node{n}", f"http://h{n}:8080") for n in range(13)), "node0")
        cases = (
            (disagg, "every node to have both a worker and a pool"),
            (thirteen, "1 to 12 nodes, not 13"),
        )
        for cluster, message in cases:
            graph = Graph()
            for index in range(4):
                graph.add("map@1", [ObjectRef("node1", f"d/{index}", 4096)], ratios=[1])
            models = Models.uncalibrated(len(cluster.nodes))
            state = Simulation(graph, models, cluster.pools, workers=cluster.worker_nodes)
            for name in ("beam-4", "native-mcts-heuristic-8"):
                with self.subTest(nodes=len(cluster.nodes), policy=name):
                    with self.assertRaisesRegex(ValueError, message):
                        make_policy(name).choose(state)


if __name__ == "__main__":
    unittest.main()
