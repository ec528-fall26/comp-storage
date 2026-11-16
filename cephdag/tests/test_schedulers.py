"""heft-cc, backlog, lp-plan and lp-plan2: their decision rules, and complete verified runs."""

import dataclasses
import itertools
import random
import unittest

import numpy as np

from cephdag import Client, Graph
from cephdag.cluster import Cluster
from cephdag.functions import synthetic
from cephdag.graph import Dataset, ObjectRef
from cephdag.model import (
    ComputeModel,
    Models,
    NetworkModel,
    Objective,
    Simulation,
    StorageModel,
    hashed_targets,
)
from cephdag.policies import make_policy
from cephdag.policies.backlog import Backlog
from cephdag.policies.list import ContentionHeftPlan, HeftPlan
from cephdag.policies.lp import PlacementLp
from cephdag.policies.simplex import minimize
from experiments import run
from experiments.run import build_workload
from tests.helpers import cs_nodes, disagg_nodes, role_lab
from tests.test_droid import MEASURED, MINI, ingest, run_waves
from tests.test_model import random_graph
from tests.test_policies import chain_and_join, run_to_completion

NEW = ("heft-cc", "backlog", "lp-plan", "lp-plan2")
MIB = 2**20
# The cephdag8 calibration, rounded: 64 MiB local tasks take about 0.3 s, a shard needs
# about 1.4 s to cross a link.
LAB_LIKE = Models(
    ComputeModel(0.0, 4.95e-10, 6.0e-11, 4.5e-10),
    StorageModel((300e6,) * 8, (450e6,) * 8),
    NetworkModel((47e6,) * 8, (47e6,) * 8),
)
POOLS = tuple(f"node{node}" for node in range(8))
LAB_LIKE_3 = Models(
    LAB_LIKE.compute,
    StorageModel((300e6,) * 3, (450e6,) * 3),
    NetworkModel((47e6,) * 3, (47e6,) * 3),
)


def skew_graph(iterations, hot=65, cold=9):
    """64 MiB shards, `hot` of them on node 0 and `cold` on each of nodes 1-7, as demo3-skew."""
    nodes = [0] * hot + [node for node in range(1, 8) for _ in range(cold)]
    shards = tuple(
        ObjectRef(POOLS[node], f"d/{index}", 64 * MIB, partition=index)
        for index, node in enumerate(nodes)
    )
    return build_workload(Dataset("skew", shards), "independent", iterations, 0.25)


def run_policy(policy, graph, models, pools, workers=None):
    """(final state, actions) of `policy` run to completion in the forecast."""
    state = Simulation(graph, models, pools, workers=workers)
    actions = []
    while not state.done:
        action = policy.choose(state)
        if action is None:
            state.advance()
        else:
            actions.append((action, state.transfer_bytes(action)))
            state.dispatch(action)
    return state, actions


def bytes_off(graph, actions, node):
    return sum(
        graph.size(ref)
        for action, _ in actions
        for ref in graph.tasks[action.task].inputs
        if isinstance(ref, ObjectRef) and ref.pool == f"node{node}" and action.worker != node
    )


class ContentionHeftTests(unittest.TestCase):
    def test_no_two_transfers_share_a_link_at_once_and_every_transfer_is_booked(self):
        state = Simulation(skew_graph(1), LAB_LIKE, POOLS)
        plan = ContentionHeftPlan(state, Objective())
        for calendars in (plan.sending, plan.receiving):
            for calendar in calendars:
                ordered = sorted(calendar.intervals)
                for (_, end, _), (start, _, _) in zip(ordered, ordered[1:]):
                    self.assertLessEqual(end, start + 1e-9)
        remote_reads = [
            task
            for task, action in plan.actions.items()
            if state.location(state.graph.tasks[task].inputs[0]) != action.worker
        ]
        booked = [task for calendar in plan.sending for _, _, task in calendar.intervals]
        self.assertEqual(sorted(booked), sorted(remote_reads))
        self.assertTrue(remote_reads)

    def test_a_task_starts_computing_only_after_its_booked_transfers(self):
        state = Simulation(skew_graph(1), LAB_LIKE, POOLS)
        plan = ContentionHeftPlan(state, Objective())
        for calendar in plan.sending:
            for start, end, task in calendar.intervals:
                planned_start, planned_end = plan.intervals[task]
                self.assertGreaterEqual(start, planned_start - 1e-9)
                compute = plan.compute_seconds[task]
                self.assertLessEqual(end + compute, planned_end + 1e-9)

    def test_it_moves_far_fewer_bytes_off_the_hot_node_than_heft(self):
        graph = skew_graph(1)
        state = Simulation(graph, LAB_LIKE, POOLS)
        moved = {}
        for plan_type in (HeftPlan, ContentionHeftPlan):
            plan = plan_type(state, Objective())
            actions = [(action, 0) for action in plan.actions.values()]
            moved[plan_type] = bytes_off(graph, actions, 0)
        self.assertLess(moved[ContentionHeftPlan], moved[HeftPlan] / 2)
        # Node 0's send link carries one 64 MiB shard per 1.43 s: within the plan's makespan.
        plan = ContentionHeftPlan(state, Objective())
        makespan = max(end for _, end in plan.intervals.values())
        self.assertLessEqual(moved[ContentionHeftPlan] / 47e6, makespan + 1e-6)
        _, heft = run_policy(make_policy("heft"), graph, LAB_LIKE, POOLS)
        _, contention = run_policy(make_policy("heft-cc"), graph, LAB_LIKE, POOLS)
        self.assertLess(bytes_off(graph, contention, 0), bytes_off(graph, heft, 0) / 2)

    def test_it_does_not_chase_calibrated_rate_differences_on_uniform_data(self):
        read_rates = (264e6, 306e6, 323e6, 392e6, 375e6, 310e6, 332e6, 282e6)
        models = dataclasses.replace(
            LAB_LIKE, storage=StorageModel(read_rates, LAB_LIKE.storage.write_bps)
        )
        shards = tuple(
            ObjectRef(POOLS[index % 8], f"d/{index}", 64 * MIB, partition=index)
            for index in range(256)
        )
        graph = build_workload(Dataset("uniform", shards), "independent", 256, 0.25)
        state = Simulation(graph, models, POOLS)
        moves = {}
        for plan_type in (HeftPlan, ContentionHeftPlan):
            plan = plan_type(state, Objective())
            moves[plan_type] = sum(
                action.worker != state.location(graph.tasks[task].inputs[0])
                for task, action in plan.actions.items()
            )
        self.assertGreater(moves[HeftPlan], 50)
        self.assertEqual(moves[ContentionHeftPlan], 0)

    def test_with_links_never_shared_it_plans_exactly_as_heft(self):
        graph = Graph()
        for index in range(12):
            graph.add("map@1", [ObjectRef(f"node{index % 3}", f"d/{index}", 4 * MIB)], ratios=[1])
        state = Simulation(graph, Models(), ("node0", "node1", "node2"))
        heft = HeftPlan(state, Objective())
        contention = ContentionHeftPlan(state, Objective())
        self.assertEqual(contention.actions, heft.actions)
        for task, (start, end) in heft.intervals.items():
            self.assertAlmostEqual(contention.intervals[task][0], start, places=9)
            self.assertAlmostEqual(contention.intervals[task][1], end, places=9)


class BacklogTests(unittest.TestCase):
    def test_moves_nothing_while_the_hot_nodes_backlog_is_shorter_than_a_move(self):
        graph = skew_graph(1, hot=4, cold=2)
        state, actions = run_policy(Backlog(), graph, LAB_LIKE, POOLS)
        self.assertTrue(state.done)
        self.assertEqual(state.remote_bytes, 0)

    def test_moves_when_the_backlog_is_long_and_fewer_than_data_local(self):
        graph = skew_graph(1)
        state, actions = run_policy(Backlog(), graph, LAB_LIKE, POOLS)
        _, data_local = run_policy(make_policy("data-local"), graph, LAB_LIKE, POOLS)
        moved = bytes_off(graph, actions, 0)
        self.assertGreater(moved, 0)
        self.assertLess(moved, bytes_off(graph, data_local, 0))
        self.assertEqual(state.remote_bytes, moved)

    def test_a_move_needs_the_backlog_to_outlast_the_queued_link_and_the_task(self):
        graph = skew_graph(1, hot=20, cold=0)
        state = Simulation(graph, LAB_LIKE, POOLS)
        policy = Backlog()
        local = policy.choose(state)
        self.assertEqual(local.worker, 0)
        state.dispatch(local)
        task = state.graph.tasks[local.task]
        one_local = state.isolated_seconds(local)
        one_remote = (
            64 * MIB / 47e6
            + task.output_sizes[0] / 450e6
            + (LAB_LIKE.compute.seconds(task, [64 * MIB]))
        )
        moves = []
        while True:
            action = policy.choose(state)
            if action is None:
                break
            moves.append(action)
            state.dispatch(action)
        # The k-th move waits for k - 1 shards on node 0's link; it pays while the 19 - k
        # tasks still queued on node 0 take longer.
        expected = max(
            k
            for k in range(1, 8)
            if (19 - k + 1) * one_local > (k - 1) * 64 * MIB / 47e6 + one_remote
        )
        self.assertEqual(len(moves), expected)
        self.assertTrue(all(action.worker != 0 for action in moves))

    def test_never_leaves_a_worker_idle_that_has_local_work(self):
        graph = skew_graph(16)
        state = Simulation(graph, LAB_LIKE, POOLS)
        policy = Backlog()
        while not state.done:
            free = state.free_workers()
            local = {
                worker
                for worker in free
                for task in state.ready()
                if state.location(graph.tasks[task].inputs[0]) == worker
            }
            action = policy.choose(state)
            if local:
                self.assertIsNotNone(action)
                self.assertIn(action.worker, local)
                self.assertEqual(state.location(graph.tasks[action.task].inputs[0]), action.worker)
            if action is None:
                state.advance()
            else:
                state.dispatch(action)


class SimplexTests(unittest.TestCase):
    def test_matches_the_best_vertex_of_small_random_programs(self):
        generator = np.random.default_rng(3)
        solved = 0
        for _ in range(300):
            variables = int(generator.integers(2, 5))
            upper = generator.integers(-3, 4, size=(int(generator.integers(1, 4)), variables))
            equal = generator.integers(-3, 4, size=(int(generator.integers(0, 2)), variables))
            upper_bounds = generator.integers(-2, 6, size=len(upper)).astype(float)
            equal_bounds = generator.integers(-2, 6, size=len(equal)).astype(float)
            cost = generator.integers(-3, 4, size=variables).astype(float)
            # Keep it bounded: every variable at most 10.
            upper = np.vstack([upper, np.eye(variables)])
            upper_bounds = np.concatenate([upper_bounds, np.full(variables, 10.0)])
            best = best_vertex(cost, upper, upper_bounds, equal, equal_bounds)
            if best is None:
                with self.assertRaisesRegex(ValueError, "no feasible point"):
                    minimize(cost, upper, upper_bounds, equal, equal_bounds)
                continue
            solution = minimize(cost, upper, upper_bounds, equal, equal_bounds)
            self.assertTrue(np.all(solution >= -1e-9))
            self.assertTrue(np.all(upper @ solution <= upper_bounds + 1e-7))
            self.assertTrue(np.allclose(equal @ solution, equal_bounds, atol=1e-7))
            self.assertAlmostEqual(cost @ solution, best, places=6)
            solved += 1
        self.assertGreater(solved, 100)

    def test_reports_an_unbounded_program(self):
        with self.assertRaisesRegex(ValueError, "unbounded"):
            minimize([-1.0, 0.0], [[1.0, -1.0]], [1.0], np.zeros((0, 2)), [])


def best_vertex(cost, upper, upper_bounds, equal, equal_bounds):
    """The least cost over every basic feasible point, or None if there is none."""
    variables = len(cost)
    rows = np.vstack([upper, equal, -np.eye(variables)])
    bounds = np.concatenate([upper_bounds, equal_bounds, np.zeros(variables)])
    best = None
    for chosen in itertools.combinations(range(len(rows)), variables):
        tight = rows[list(chosen)]
        if abs(np.linalg.det(tight)) < 1e-9:
            continue
        point = np.linalg.solve(tight, bounds[list(chosen)])
        feasible = (
            np.all(point >= -1e-9)
            and np.all(upper @ point <= upper_bounds + 1e-9)
            and np.allclose(equal @ point, equal_bounds, atol=1e-9)
        )
        if feasible and (best is None or cost @ point < best):
            best = cost @ point
    return best


def two_node_models():
    """One second of compute per task; a shard takes one more second to cross the link and
    disks are effectively free."""
    return Models(
        ComputeModel(1.0, 0.0, 0.0, 0.0),
        StorageModel((1e15,) * 2, (1e15,) * 2),
        NetworkModel((4e6,) * 2, (4e6,) * 2),
    )


def ten_on_node_zero():
    graph = Graph()
    for index in range(10):
        graph.add("map@1", [ObjectRef("pool0", f"d/{index}", 4_000_000)], ratios=[0])
    return graph


class LpPlanTests(unittest.TestCase):
    def test_shares_match_the_hand_solved_two_node_plan(self):
        # x0 + x1 = 10 tasks; node 0 takes 1 s each, node 1 takes 2 s (1 s on the link):
        # x0 <= T, 2 x1 <= T, x1 <= T, so T = x0 = 2 x1 = 20/3.
        state = Simulation(ten_on_node_zero(), two_node_models(), ("pool0", "pool1"))
        plan = PlacementLp(state)
        self.assertAlmostEqual(plan.makespan, 20 / 3, places=6)
        np.testing.assert_allclose(plan.shares, [[20 / 3, 10 / 3]], atol=1e-4)
        self.assertEqual(plan.counts.tolist(), [[7, 3]])
        self.assertAlmostEqual(plan.moved_bytes / 4_000_000, 10 / 3, places=4)

    def test_the_link_bound_limits_the_moves(self):
        # Link 0.5 s per shard less: now the link, not node 1, binds when node 1 also has
        # local work: x1_remote * 1 <= T.
        graph = ten_on_node_zero()
        for index in range(6):
            graph.add("map@1", [ObjectRef("pool1", f"e/{index}", 4_000_000)], ratios=[0])
        state = Simulation(graph, two_node_models(), ("pool0", "pool1"))
        plan = PlacementLp(state)
        # Node 0: 10 - m tasks; node 1: 6 local + 2 m; T = 10 - m = 6 + 2 m, m = 4/3.
        self.assertAlmostEqual(plan.makespan, 10 - 4 / 3, places=6)
        self.assertEqual(plan.counts.sum(), 16)

    def test_dispatcher_runs_exactly_the_planned_counts(self):
        for graph, models, pools in (
            (ten_on_node_zero(), two_node_models(), ("pool0", "pool1")),
            (skew_graph(128), LAB_LIKE, POOLS),
        ):
            policy = make_policy("lp-plan")
            state, actions = run_policy(policy, graph, models, pools)
            self.assertTrue(state.done)
            executed = np.zeros_like(policy.plan.counts)
            for action, _ in actions:
                executed[policy.plan.class_of[action.task], action.worker] += 1
            self.assertEqual(executed.tolist(), policy.plan.counts.tolist())

    def test_on_the_skew_graph_it_moves_what_the_plan_says_and_no_more(self):
        policy = make_policy("lp-plan")
        graph = skew_graph(1)
        state, actions = run_policy(policy, graph, LAB_LIKE, POOLS)
        planned_moves = sum(
            count
            for key, counts in zip(policy.plan.classes, policy.plan.counts)
            for worker, count in enumerate(counts)
            if POOLS[worker] not in key[2]
        )
        self.assertEqual(state.remote_bytes, planned_moves * 64 * MIB)
        self.assertAlmostEqual(planned_moves * 64 * MIB, policy.plan.moved_bytes, delta=64 * MIB)
        self.assertLess(state.remote_bytes, 16 * 64 * MIB)
        self.assertEqual(policy.last_info, {})

    def test_records_its_solve_time_at_the_first_decision(self):
        policy = make_policy("lp-plan")
        state = Simulation(ten_on_node_zero(), two_node_models(), ("pool0", "pool1"))
        policy.choose(state)
        self.assertEqual(
            set(policy.last_info),
            {"lp_build_seconds", "lp_solve_seconds", "lp_makespan_seconds", "lp_classes"},
        )
        self.assertGreater(policy.last_info["lp_solve_seconds"], 0)

    def test_later_stages_run_on_competing_and_random_graphs(self):
        pools = ("node0", "node1", "node2")
        shards = tuple(
            ObjectRef(pools[index % 3], f"d/{index}", 4 * MIB, partition=index)
            for index in range(6)
        )
        competing = build_workload(Dataset("c", shards), "competing", 2, 0.25)
        policy = make_policy("lp-plan")
        state = run_to_completion(policy, competing, Models(), pools)
        self.assertTrue(state.done)
        # Each node's shards feed two maps with different iterations: two classes per node.
        self.assertEqual(len(policy.plan.classes), 6)


THREE = ("pool0", "pool1", "pool2")


def hot_node_zero():
    """Twelve tasks on node 0 and one on each of nodes 1 and 2, with two_node_models' rates:
    one second of compute per task, one more to cross a link."""
    graph = Graph()
    for index, node in enumerate([0] * 12 + [1, 2]):
        graph.add("map@1", [ObjectRef(f"pool{node}", f"d/{index}", 4_000_000)], ratios=[0])
    models = Models(
        ComputeModel(1.0, 0.0, 0.0, 0.0),
        StorageModel((1e15,) * 3, (1e15,) * 3),
        NetworkModel((4e6,) * 3, (4e6,) * 3),
    )
    return graph, models


class SlowLink(Simulation):
    """Node 0's send link runs at a quarter of its modelled rate from the first advance on,
    after the plan was made at the first decision."""

    def advance(self):
        self.capacity["tx:0"] = self.models.network.tx_bps[0] / 4
        return super().advance()


def first_start(policy, state, worker):
    """(time, node its input is on) of the first task `policy` starts on `worker`."""
    while True:
        action = policy.choose(state)
        if action is None:
            state.advance()
        elif action.worker == worker:
            return state.time, state.location(state.graph.tasks[action.task].inputs[0])
        else:
            state.dispatch(action)


def planned_and_executed(policy, state):
    """Run `policy` to completion: the task counts per class and worker of its plan as made
    at the first decision, and of what it ran."""
    planned = executed = None
    while not state.done:
        action = policy.choose(state)
        if planned is None:
            planned = policy.plan.counts.copy()
            executed = np.zeros_like(planned)
        if action is None:
            state.advance()
        else:
            executed[policy.plan.class_of[action.task], action.worker] += 1
            state.dispatch(action)
    return planned, executed


class LpPlan2Tests(unittest.TestCase):
    def test_a_worker_due_a_transfer_over_a_busy_link_waits_for_the_link(self):
        # T = 6.5 s: node 0 runs 6.5 of its tasks, nodes 1 and 2 their own and 2.75 moves each.
        # Worker 1 takes node 0's link at 0 s; worker 2 is due a move most (3 of its 4 tasks).
        starts = {}
        for name in ("lp-plan", "lp-plan2"):
            policy = make_policy(name)
            starts[name] = first_start(policy, Simulation(*hot_node_zero(), THREE), 2)
            self.assertEqual(policy.plan.counts.tolist(), [[6, 3, 3], [0, 1, 0], [0, 0, 1]])
        self.assertEqual(starts["lp-plan"], (0.0, 2))
        self.assertAlmostEqual(starts["lp-plan2"][0], 1.0, places=6)
        self.assertEqual(starts["lp-plan2"][1], 0)

    def test_once_its_own_plan_is_done_the_hot_node_takes_back_unstarted_moves(self):
        # Moves take 4 s each: lp-plan's node 0 is done with its 6 tasks at 6 s and leaves the
        # other 6 to its link.
        for name, on_node_zero, wall in (("lp-plan", 6, 25.0), ("lp-plan2", 9, 13.0)):
            with self.subTest(policy=name):
                policy = make_policy(name)
                state = SlowLink(*hot_node_zero(), THREE)
                planned, executed = planned_and_executed(policy, state)
                self.assertEqual(planned[0].tolist(), [6, 3, 3])
                self.assertEqual(executed[0, 0], on_node_zero)
                self.assertEqual(executed.tolist(), policy.plan.counts.tolist())
                self.assertAlmostEqual(state.time, wall, places=6)

    def test_on_the_skew_graph_a_worker_takes_back_only_tasks_stored_on_its_node(self):
        for iterations, simulation in ((1, SlowLink), (128, SlowLink), (1024, Simulation)):
            with self.subTest(iterations=iterations, simulation=simulation.__name__):
                policy = make_policy("lp-plan2")
                state = simulation(skew_graph(iterations), LAB_LIKE, POOLS)
                planned, executed = planned_and_executed(policy, state)
                self.assertEqual(executed.tolist(), policy.plan.counts.tolist())
                taken = list(zip(*np.nonzero(executed > planned)))
                self.assertTrue(taken)
                for index, worker in taken:
                    self.assertEqual(policy.plan.classes[index][2], (POOLS[worker],))

    def test_on_the_skew_graph_at_1024_iterations_it_beats_lp_plan(self):
        walls = {}
        for name in ("lp-plan", "lp-plan2"):
            state, _ = run_policy(make_policy(name), skew_graph(1024), LAB_LIKE, POOLS)
            walls[name] = state.time
        self.assertLess(walls["lp-plan2"], 0.8 * walls["lp-plan"])


class AllNewTests(unittest.TestCase):
    def test_complete_random_graphs_and_other_node_counts(self):
        pools = ("node0", "node1", "node2")
        for seed, name in itertools.product(range(4), NEW):
            with self.subTest(seed=seed, policy=name):
                graph = random_graph(random.Random(seed), 80)
                for models in (Models(), LAB_LIKE_3):
                    state, actions = run_policy(make_policy(name), graph, models, pools)
                    self.assertTrue(state.done)
                    self.assertEqual(len(actions), len(graph.tasks))
        for node_count, name in itertools.product((1, 2, 5), NEW):
            with self.subTest(nodes=node_count, policy=name):
                node_pools = tuple(f"pool{node}" for node in range(node_count))
                models = Models.uncalibrated(node_count)
                state = run_to_completion(
                    make_policy(name), chain_and_join(node_pools), models, node_pools
                )
                self.assertTrue(state.done)

    def test_role_split_clusters_place_outputs_by_the_storage_hash(self):
        graph = random_graph(random.Random(5), 40)
        cluster = Cluster.from_dict({"nodes": disagg_nodes(3, 2)})
        models = Models.uncalibrated(5)
        for name in NEW:
            with self.subTest(policy=name):
                state, actions = run_policy(
                    make_policy(name), graph, models, cluster.pools, cluster.worker_nodes
                )
                self.assertTrue(state.done)
                for action, _ in actions:
                    self.assertIn(action.worker, (3, 4))
                    task = state.graph.tasks[action.task]
                    self.assertEqual(action.targets, hashed_targets(task, (0, 1, 2)))

    def test_real_runs_verify_and_match_fifo_hash_on_cs_and_role_split_clusters(self):
        for nodes in (cs_nodes(3), disagg_nodes(3, 2)):
            cluster, store, transport = role_lab(nodes)
            client = Client(
                cluster, Models.uncalibrated(len(nodes)), store=store, transport=transport
            )
            data = client.put_dataset(
                "numbers",
                [synthetic(4096, seed) for seed in range(9)],
                placement=[0] * 6 + [1, 2, 2],
            )
            graph = build_workload(data, "branch-join", 2, 0.5)
            digests = set()
            for name in ("fifo-hash", *NEW):
                with self.subTest(nodes=len(nodes), policy=name):
                    result = client.submit(graph, name).wait(timeout=60).metrics()
                    self.assertEqual(len(result["tasks"]), len(graph.tasks))
                    final = sum(output["size"] for output in result["final_outputs"])
                    self.assertEqual(result["verified_final_bytes"], final)
                    digests.add(run.outputs_digest(result))
            self.assertEqual(len(digests), 1)

    def test_droid_mini_graphs_verify_on_cs_and_role_split_clusters(self):
        cs, store, cs_transport = role_lab(cs_nodes(2))
        disagg, _, disagg_transport = role_lab(disagg_nodes(2, 2), store)
        compute = ComputeModel(functions=MEASURED)
        clients = []
        for cluster, transport in ((cs, cs_transport), (disagg, disagg_transport)):
            models = dataclasses.replace(Models.uncalibrated(len(cluster.nodes)), compute=compute)
            clients.append(Client(cluster, models, store=store, transport=transport))
        names = ["droid-128-00000", "droid-128-00001"]
        for shard, name in enumerate(names):
            with MINI.open("rb") as stream:
                ingest(clients[0], stream, name, placement=(shard,))
        results = []
        for client in clients:
            _, waves = run_waves(client, names, ["fifo-hash", *NEW], 2**40)
            results += waves
        self.assertEqual(len(results), 2 * (1 + len(NEW)))
        self.assertEqual(len({run.outputs_digest(result) for result in results}), 1)
        for result in results:
            self.assertEqual(result["droid_verification"]["decoded"], 2 * 57)


if __name__ == "__main__":
    unittest.main()
