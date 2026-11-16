"""The Rust crate must simulate exactly what model.Simulation simulates."""

import dataclasses
import math
import random
import unittest

import cephdag.droid  # noqa: F401  registers the DROID functions
from cephdag.graph import Graph, ObjectRef
from cephdag.model import (
    ComputeModel,
    Dispatch,
    FunctionCost,
    Models,
    NetworkModel,
    Simulation,
    StorageModel,
)
from cephdag.policies import make_policy
from cephdag.policies.native import Beam, NativeGraph, _buffer

POOLS = ("node0", "node1", "node2")
SIX_POOLS = tuple(f"node{node}" for node in range(6))
EIGHT_POOLS = tuple(f"node{node}" for node in range(8))
# Unlike the global coefficients, so a function forecast with the wrong ones is far off.
DROID_COSTS = (
    ("decode_resize@1", FunctionCost(0.004, 3.1e-8, 2e-10)),
    ("encode_video@1", FunctionCost(0.0, 1.6e-8, 5e-9)),
    ("frame_stats@1", FunctionCost(0.001, 8.7e-9, 0.0)),
    ("state_parquet@1", FunctionCost(0.002, 0.0, 3e-9)),
)


def random_graph(generator, pools=POOLS, tasks=10):
    graph = Graph()
    available = [
        ObjectRef(
            pools[index % len(pools)],
            f"d/{index}",
            4 * generator.randint(1, 10**5),
            partition=index,
        )
        for index in range(len(pools))
    ]
    for _ in range(tasks):
        function = generator.choice(("map@1", "join@1", "split@1"))
        inputs = [generator.choice(available) for _ in range(2 if function == "join@1" else 1)]
        ratios = [
            generator.choice((0, 0.5, 1, 2)) for _ in range(2 if function == "split@1" else 1)
        ]
        available.extend(
            graph.add(function, inputs, ratios=ratios, iterations=generator.choice((0, 4)))
        )
    return graph


def droid_shaped_graph(generator, pools, partitions):
    """The DROID pipeline per partition, plus numeric map chains that use the global model."""
    graph = Graph()
    for index in range(partitions):
        pool = pools[index % len(pools)]
        source = ObjectRef(
            pool, f"e/{index}", generator.randint(10**6, 4 * 10**6), "droid-episodes", index
        )
        frames, state = graph.add(
            "decode_resize@1",
            [source],
            ratios=[2.16, 0.01],
            iterations=0,
            params={"resolution": 128},
        )
        graph.add("encode_video@1", [frames], ratios=[0.33], iterations=0)
        graph.add("frame_stats@1", [frames], ratios=[0.001], iterations=0)
        graph.add("state_parquet@1", [state], ratios=[0.69], iterations=0)
        numbers = ObjectRef(pool, f"d/{index}", 4 * generator.randint(10**5, 10**6), "float32")
        (mapped,) = graph.add("map@1", [numbers], ratios=[0.5], iterations=4)
        graph.add("map@1", [mapped], ratios=[1], iterations=8)
    return graph


def resize_completed(state, generator):
    """Give every completed output a stored size other than its estimate, as real outputs have."""
    for task in list(state.completed):
        state.completed[task] = tuple(
            dataclasses.replace(ref, size=max(0, ref.size + 4 * generator.randrange(-9000, 9000)))
            for ref in state.completed[task]
        )
    state.refresh()


def six_node_models(generator):
    return node_models(generator, 6)


def node_models(generator, node_count):
    """Nodes whose disks, links and CPUs all differ, so a node mix-up changes the result."""

    def rates(low, high):
        return tuple(generator.uniform(low, high) for _ in range(node_count))

    return Models(
        ComputeModel(0.002, 1e-9, 4e-10, 1e-9),
        StorageModel(rates(100e6, 400e6), rates(50e6, 200e6)),
        NetworkModel(rates(5e6, 50e6), rates(5e6, 50e6)),
    )


def random_step(state, generator):
    """Dispatch a random ready task to a random free worker, or advance; return the action."""
    if state.ready() and state.free_workers() and (not state.running or generator.random() < 0.7):
        task = state.graph.tasks[generator.choice(state.ready())]
        targets = tuple(generator.randrange(state.node_count) for _ in task.output_sizes)
        action = Dispatch(task.id, generator.choice(state.free_workers()), targets)
        state.dispatch(action)
        return action
    state.advance()
    return None


def sending_every_completed_task(state):
    """A copy of `state` that sends the crate every completed task, not only those still read."""
    copy = state.clone()
    copy.still_read = lambda: list(copy.completed)
    return copy


class NativeSimulationTests(unittest.TestCase):
    def test_random_dispatch_and_wait_sequences_match_python(self):
        generator = random.Random(5)
        for _ in range(20):
            graph = random_graph(generator)
            python = Simulation(graph, Models(), POOLS)
            start = python.clone()
            actions = []
            while not python.done:
                can_dispatch = python.ready() and python.free_workers()
                if can_dispatch and (not python.running or generator.random() < 0.7):
                    task = python.graph.tasks[generator.choice(python.ready())]
                    targets = tuple(generator.randrange(3) for _ in task.output_sizes)
                    action = Dispatch(task.id, generator.choice(python.free_workers()), targets)
                    python.dispatch(action)
                else:
                    action = None
                    python.advance()
                actions.append(action)
            with NativeGraph(start) as native:
                result = native.simulate(start, actions)
            self.assertAlmostEqual(result["time"], python.time, delta=1e-9 * max(1.0, python.time))
            self.assertEqual(result["remote_bytes"], python.remote_bytes)
            self.assertEqual(sorted(result["completed"]), sorted(python.completed))

    def test_six_node_forecasts_from_random_mid_run_states_match_python(self):
        generator = random.Random(6)
        models = six_node_models(generator)
        compared = 0
        for _ in range(30):
            graph = random_graph(generator, SIX_POOLS, tasks=25)
            python = Simulation(graph, models, SIX_POOLS)
            for _ in range(generator.randrange(30)):
                if not python.done:
                    random_step(python, generator)
            start = python.clone()
            actions = []
            for _ in range(generator.randrange(1, 40)):
                if python.done:
                    break
                actions.append(random_step(python, generator))
            with NativeGraph(start) as native:
                result = native.simulate(start, actions)
            close = {"rel_tol": 1e-9, "abs_tol": 1e-12}
            self.assertTrue(math.isclose(result["time"], python.time, **close))
            self.assertTrue(math.isclose(result["tail"], python.tail_estimate(), **close))
            for native_work, python_work in zip(result["assigned_work"], python.assigned_work):
                self.assertTrue(math.isclose(native_work, python_work, **close))
            self.assertEqual(result["remote_bytes"], python.remote_bytes)
            self.assertEqual(
                result["completed"],
                {
                    task: [SIX_POOLS.index(ref.pool) for ref in refs]
                    for task, refs in python.completed.items()
                },
            )
            self.assertEqual(sorted(result["running"]), sorted(python.running))
            self.assertEqual(result["ready"], python.ready())
            compared += 1
        self.assertEqual(compared, 30)

    def test_per_function_forecasts_match_python_after_outputs_differ_from_their_estimates(self):
        generator = random.Random(8)
        models = node_models(generator, 8)
        models = dataclasses.replace(
            models, compute=dataclasses.replace(models.compute, functions=DROID_COSTS)
        )
        read_resized = 0
        for _ in range(20):
            graph = droid_shaped_graph(generator, EIGHT_POOLS, partitions=6)
            python = Simulation(graph, models, EIGHT_POOLS)
            for _ in range(generator.randrange(8, 30)):
                if not python.done:
                    random_step(python, generator)
            resize_completed(python, generator)
            if python.done or not python.resized:
                continue
            start = python.clone()
            resized = set(start.completed)
            actions = []
            while not python.done and len(actions) < 60:
                action = random_step(python, generator)
                actions.append(action)
                if action is not None:
                    inputs = graph.tasks[action.task].inputs
                    read_resized += any(getattr(ref, "task", None) in resized for ref in inputs)
            with NativeGraph(start) as native:
                result = native.simulate(start, actions)
            close = {"rel_tol": 1e-9, "abs_tol": 1e-12}
            self.assertTrue(math.isclose(result["time"], python.time, **close))
            self.assertTrue(math.isclose(result["tail"], python.tail_estimate(), **close))
            for native_work, python_work in zip(result["assigned_work"], python.assigned_work):
                self.assertTrue(math.isclose(native_work, python_work, **close))
            self.assertEqual(result["remote_bytes"], python.remote_bytes)
        self.assertGreater(read_resized, 20)

    def test_six_node_searches_return_feasible_actions_that_use_every_worker(self):
        generator = random.Random(7)
        graph = random_graph(generator, SIX_POOLS, tasks=40)
        models = six_node_models(generator)
        for name in ("beam-4", "native-mcts-heuristic-16", "native-mcts-uniform-16"):
            with self.subTest(policy=name):
                policy = make_policy(name, budget_ms=1e9)
                state = Simulation(graph, models, SIX_POOLS)
                workers = set()
                while not state.done:
                    action = policy.choose(state)
                    if action is None:
                        state.advance()
                    else:
                        state.dispatch(action)
                        workers.add(action.worker)
                policy.close()
                self.assertEqual(workers, set(range(6)))

    def test_six_node_searches_store_outputs_on_any_node(self):
        graph = Graph()
        graph.add("split@1", [ObjectRef("node4", "d/0", 4 * 10**6)], ratios=[1, 1])
        fast_disk_on_node_five = Models(
            ComputeModel(0.001, 0, 0, 0),
            StorageModel((300e6,) * 6, (1e6,) * 5 + (1e9,)),
            NetworkModel((1e9,) * 6, (1e9,) * 6),
        )
        state = Simulation(graph, fast_disk_on_node_five, SIX_POOLS)
        for name in ("beam-4", "native-mcts-heuristic-16"):
            with self.subTest(policy=name):
                policy = make_policy(name, budget_ms=1e9)
                self.assertEqual(policy.choose(state).targets, (5, 5))
                policy.close()

    def test_searches_decide_the_same_from_only_the_outputs_still_read(self):
        generator = random.Random(9)
        for pools in (POOLS, SIX_POOLS):
            models = Models.uncalibrated(len(pools))
            for _ in range(12):
                graph = random_graph(generator, pools, tasks=30)
                state = Simulation(graph, models, pools)
                for _ in range(generator.randrange(10, 40)):
                    if not state.done:
                        random_step(state, generator)
                if state.done:
                    continue
                for task in generator.sample(list(state.completed), len(state.completed) // 2):
                    state.completed[task] = tuple(
                        dataclasses.replace(ref, size=ref.size + 4 * generator.randrange(1, 9))
                        for ref in state.completed[task]
                    )
                state.refresh()
                everything = sending_every_completed_task(state)
                for name in ("beam-4", "native-mcts-heuristic-16"):
                    with self.subTest(nodes=len(pools), policy=name):
                        decisions = []
                        for view in (state, everything):
                            policy = make_policy(name, budget_ms=1e9)
                            action = policy.choose(view)
                            searched = policy.last_info.get("simulated_transitions")
                            decisions.append((action, searched))
                            policy.close()
                        self.assertEqual(decisions[0], decisions[1])

    def test_beam_does_not_search_when_no_worker_is_free(self):
        graph = Graph()
        for index in range(4):
            graph.add("map@1", [ObjectRef("node0", f"d/{index}", 4096)])
        state = Simulation(graph, Models(), POOLS)
        for worker in range(3):
            state.dispatch(Dispatch(f"t00000{worker}", worker, (worker,)))
        beam = Beam(budget_ms=1e9)
        self.assertIsNone(beam.choose(state))
        self.assertEqual(beam.last_info["iterations"], 0)

    def test_integers_beyond_exact_float_range_are_refused(self):
        with self.assertRaisesRegex(ValueError, "exact numeric"):
            _buffer([2**53 + 1])


if __name__ == "__main__":
    unittest.main()
