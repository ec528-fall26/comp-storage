import random
import unittest

from cephdag.graph import Graph, ObjectRef
from cephdag.model import ComputeModel, Dispatch, Models, NetworkModel, Simulation, StorageModel
from cephdag.policies import NAMES, make_policy
from cephdag.policies.list import Calendar, Heft, WorkStealing, insertion_start
from tests.test_model import random_graph

MB = 10**6
NON_NATIVE = ("random", "single-node", "data-local", "greedy", "greedy-wait", "heft", "stealing")
ALL = NON_NATIVE + ("beam-4", "native-mcts-heuristic-16", "native-mcts-uniform-16")


def chain_and_join(pools):
    graph = Graph()
    sources = [
        ObjectRef(pools[index % len(pools)], f"d/{index}", 4 * MB, partition=index)
        for index in range(4)
    ]
    mapped = [graph.add("map@1", [source], ratios=[0.5], iterations=4)[0] for source in sources]
    left, right = graph.add("split@1", [mapped[0]], ratios=[0.5, 0.25])
    graph.add("join@1", [left, mapped[1]], ratios=[0.5])
    graph.add("join@1", [right, mapped[2]], ratios=[0.5])
    graph.add("map@1", [mapped[3]], ratios=[1], iterations=16)
    return graph


def run_to_completion(policy, graph, models, pools):
    state = Simulation(graph, models, pools)
    for _ in range(10 * len(graph.tasks) + 10):
        if state.done:
            return state
        action = policy.choose(state)
        if action is None:
            state.advance()
        else:
            state.dispatch(action)
    raise AssertionError("policy did not finish the graph")


def blocks(intervals):
    """Starts and latest ends of the runs of sorted intervals that leave no gap."""
    starts = []
    ends = []
    for start, end, _ in intervals:
        if ends and start <= ends[-1]:
            ends[-1] = max(ends[-1], end)
        else:
            starts.append(start)
            ends.append(end)
    return starts, ends


class PolicyTests(unittest.TestCase):
    def test_every_policy_finishes_a_graph_with_joins_and_multiple_outputs(self):
        pools = ("node0", "node1", "node2")
        for name in ALL:
            with self.subTest(policy=name):
                policy = make_policy(
                    name, **({"budget_ms": 1e9} if name.startswith(("beam", "native")) else {})
                )
                state = run_to_completion(policy, chain_and_join(pools), Models(), pools)
                self.assertEqual(len(state.completed), 8)

    def test_python_policies_work_on_other_node_counts(self):
        for node_count in (1, 2, 5):
            pools = tuple(f"pool{node}" for node in range(node_count))
            for name in NON_NATIVE:
                with self.subTest(nodes=node_count, policy=name):
                    state = run_to_completion(
                        make_policy(name),
                        chain_and_join(pools),
                        Models.uncalibrated(node_count),
                        pools,
                    )
                    self.assertTrue(state.done)

    def test_native_policies_work_on_other_node_counts_up_to_twelve(self):
        for node_count in (1, 2, 6, 12):
            pools = tuple(f"pool{node}" for node in range(node_count))
            for name in ("beam-4", "native-mcts-heuristic-8"):
                with self.subTest(nodes=node_count, policy=name):
                    state = run_to_completion(
                        make_policy(name, budget_ms=1e9),
                        chain_and_join(pools),
                        Models.uncalibrated(node_count),
                        pools,
                    )
                    self.assertTrue(state.done)
        pools = tuple(f"pool{node}" for node in range(13))
        state = Simulation(chain_and_join(pools), Models.uncalibrated(13), pools)
        for name in ("beam-4", "native-mcts-heuristic-8"):
            with self.assertRaisesRegex(ValueError, "1 to 12 nodes, not 13"):
                make_policy(name).choose(state)

    def test_unknown_names_list_the_choices(self):
        with self.assertRaisesRegex(ValueError, "beam-<width>"):
            make_policy("mcts-heuristic-8")
        self.assertIn("heft", NAMES)

    def test_single_node_runs_everything_on_node_zero(self):
        pools = ("node0", "node1", "node2")
        policy = make_policy("single-node")
        state = Simulation(chain_and_join(pools), Models(), pools)
        action = policy.choose(state)
        self.assertEqual((action.worker, set(action.targets)), (0, {0}))
        state.dispatch(action)
        self.assertIsNone(policy.choose(state))

    def test_greedy_wait_waits_for_a_busy_local_worker_when_that_is_faster(self):
        models = Models(
            ComputeModel(0, 0, 0, 0),
            StorageModel((1e9,) * 3, (1e9,) * 3),
            NetworkModel((1e6,) * 3, (1e6,) * 3),
        )
        pools = ("node0", "node1", "node2")
        graph = Graph()
        graph.add("map@1", [ObjectRef("node0", "d/0", 4)], ratios=[0], iterations=0)
        graph.add("map@1", [ObjectRef("node0", "d/1", 40 * MB)], ratios=[0], iterations=0)
        state = Simulation(graph, models, pools)
        state.dispatch(Dispatch("t000000", 0, (0,)))
        self.assertEqual(make_policy("greedy").choose(state).worker, 1)
        self.assertIsNone(make_policy("greedy-wait").choose(state))

    def test_stealing_moves_a_queued_task_only_when_it_finishes_sooner(self):
        models = Models(ComputeModel(1.0, 0, 0, 0), StorageModel(), NetworkModel())
        pools = ("node0", "node1", "node2")
        graph = Graph()
        for index in range(3):
            graph.add("map@1", [ObjectRef("node0", f"d/{index}", 4)], ratios=[1], iterations=0)
        state = Simulation(graph, models, pools)
        policy = WorkStealing()
        first = policy.choose(state)
        self.assertEqual(first.worker, 0)
        state.dispatch(first)
        second = policy.choose(state)
        self.assertNotEqual(second.worker, 0)
        self.assertEqual(policy.last_info["steals"], 1)

    def test_insertion_calendar_fills_gaps_without_overlap(self):
        calendar = [(0, 1, "a"), (3, 4, "b")]
        self.assertEqual(insertion_start(calendar, 0, 2), 1)
        self.assertEqual(insertion_start(calendar, 0, 2.5), 4)
        self.assertEqual(insertion_start(calendar, 3.5, 0.1), 4)

    def test_calendar_starts_tasks_exactly_where_a_full_insertion_scan_would(self):
        generator = random.Random(4)
        for _ in range(150):
            calendar = Calendar()
            for _ in range(30):
                ends = [end for _, end, _ in calendar.intervals]
                lefts = [start for start, _, _ in calendar.intervals]
                for _ in range(4):
                    release = generator.choice([0, generator.uniform(0, 40), *ends, *lefts])
                    duration = generator.choice(
                        [0.0, 1e-13, 3e-12, generator.uniform(0, 4), generator.uniform(0, 0.01)]
                    )
                    start = calendar.earliest_start(release, duration)
                    self.assertEqual(start, insertion_start(calendar.intervals, release, duration))
                calendar.add(start, start + duration, f"t{len(calendar.intervals):03d}")
                self.assertEqual(
                    (calendar.block_starts, calendar.block_ends), blocks(calendar.intervals)
                )

    def test_heft_starts_each_queue_head_as_a_full_scan_would_across_reused_runs(self):
        pools = ("node0", "node1", "node2")
        graph = random_graph(random.Random(8), 120)
        heft = Heft()
        for run in range(2):
            state = Simulation(graph, Models(), pools)
            while not state.done:
                action = heft.choose(state)
                heads = [
                    next(
                        (
                            task
                            for task in queue
                            if task not in state.completed and task not in state.running
                        ),
                        None,
                    )
                    for queue in heft.plan.queues
                ]
                expected = min(
                    (
                        heft.plan.actions[heads[worker]]
                        for worker in state.free_workers()
                        if heads[worker] is not None and state.is_ready(heads[worker])
                    ),
                    key=lambda action: (heft.plan.intervals[action.task][0], action.task),
                    default=None,
                )
                self.assertEqual(action, expected, f"run {run}")
                if action is None:
                    state.advance()
                else:
                    state.dispatch(action)

    def test_random_policy_is_reproducible_from_its_seed(self):
        pools = ("node0", "node1", "node2")
        first = run_to_completion(
            make_policy("random", seed=7), chain_and_join(pools), Models(), pools
        )
        second = run_to_completion(
            make_policy("random", seed=7), chain_and_join(pools), Models(), pools
        )
        self.assertEqual(first.time, second.time)


TWO_HOSTS_FOUR_WORKERS = ("node0", "node1", None, None)
SECOND_WORKERS_ON_HOSTS = (0, 1, 0, 1)


def one_map_per_shard(shards):
    """Independent maps of 64 MB shards, alternately on node0 and node1."""
    graph = Graph()
    for index in range(shards):
        source = ObjectRef(f"node{index % 2}", f"d/{index}", 64 * MB, partition=index)
        graph.add("map@1", [source], ratios=[0.25], iterations=1)
    return graph


def lab_rates(nodes):
    return Models(
        ComputeModel(0.0, 4.95e-10, 6.0e-11, 4.5e-10),
        StorageModel((300e6,) * nodes, (450e6,) * nodes),
        NetworkModel((47e6,) * nodes, (47e6,) * nodes),
    )


class SharedHostTests(unittest.TestCase):
    """Lab mode cs2: nodes 2 and 3 are second workers on the hosts of storage nodes 0 and 1."""

    def state(self, graph):
        return Simulation(
            graph, lab_rates(4), TWO_HOSTS_FOUR_WORKERS, hosts=SECOND_WORKERS_ON_HOSTS
        )

    def test_a_second_worker_reads_and_writes_its_hosts_pool_locally(self):
        state = self.state(one_map_per_shard(2))
        self.assertFalse(state.role_split)
        on_node0 = state.ready()[0]
        local = Dispatch(on_node0, 2, (0,))
        self.assertEqual(state.transfer_bytes(local), 0)
        self.assertEqual(
            [stage.resources for stage in state.stages_for(local)],
            [("read:0",), ("cpu:2",), ("write:0",)],
        )
        remote = Dispatch(on_node0, 3, (1,))
        self.assertEqual(state.stages_for(remote)[0].resources, ("read:0", "tx:0", "rx:1"))
        kept = {
            action.worker: action.targets
            for action in state.candidates(all_targets=False)
            if action.task == on_node0
        }
        self.assertEqual(kept, {0: (0,), 1: (1,), 2: (0,), 3: (1,)})

    def test_data_local_and_heft_keep_every_task_on_its_host_and_use_every_worker(self):
        for name in ("data-local", "heft", "heft-cc"):
            with self.subTest(policy=name):
                policy = make_policy(name)
                state = self.state(one_map_per_shard(4))
                workers = set()
                while not state.done:
                    action = policy.choose(state)
                    if action is None:
                        state.advance()
                        continue
                    self.assertEqual(state.transfer_bytes(action), 0)
                    workers.add(action.worker)
                    state.dispatch(action)
                self.assertEqual(workers, {0, 1, 2, 3})

    def test_workers_of_one_compute_host_share_its_links(self):
        state = Simulation(
            one_map_per_shard(2), lab_rates(4), ("node0", "node1", None, None),
            workers=(2, 3), hosts=(0, 1, 2, 2),
        )  # fmt: skip
        self.assertTrue(state.role_split)
        on_node0 = state.ready()[0]
        self.assertEqual(
            state.stages_for(Dispatch(on_node0, 3, (0,)))[0].resources,
            ("read:0", "tx:0", "rx:2"),
        )

    def test_policies_with_a_queue_per_node_refuse_workers_that_share_a_host(self):
        for name in ("backlog", "lp-plan"):
            with self.subTest(policy=name):
                with self.assertRaisesRegex(ValueError, "every worker on a host of its own"):
                    make_policy(name).choose(self.state(one_map_per_shard(4)))


if __name__ == "__main__":
    unittest.main()
