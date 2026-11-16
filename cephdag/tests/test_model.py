import json
import random
import unittest

from cephdag.graph import Graph, ObjectRef
from cephdag.model import (
    ComputeModel,
    Dispatch,
    FunctionCost,
    Models,
    NetworkModel,
    Outputs,
    Simulation,
    StorageModel,
)

MB = 10**6
POOLS = ("node0", "node1", "node2")


def io_models():
    """Compute is free; reads 100 MB/s, writes 50 MB/s, links 10 MB/s each way."""
    return Models(
        ComputeModel(0, 0, 0, 0),
        StorageModel((100 * MB,) * 3, (50 * MB,) * 3),
        NetworkModel((10 * MB,) * 3, (10 * MB,) * 3),
    )


def random_graph(generator, tasks):
    graph = Graph()
    available = [
        ObjectRef(POOLS[index % 3], f"d/{index}", 4 * generator.randint(1, 10**5), partition=index)
        for index in range(6)
    ]
    while len(graph.tasks) < tasks:
        function = generator.choice(("map@1", "join@1", "split@1"))
        inputs = [
            generator.choice(available[-40:]) for _ in range(2 if function == "join@1" else 1)
        ]
        ratios = [
            generator.choice((0.25, 0.5, 1)) for _ in range(2 if function == "split@1" else 1)
        ]
        available.extend(graph.add(function, inputs, ratios=ratios, iterations=1))
    return graph


def random_step(state, generator):
    """Dispatch a random ready task to a random free worker, or advance time."""
    ready = state.ready()
    if ready and state.free_workers() and (not state.running or generator.random() < 0.6):
        task = state.graph.tasks[generator.choice(ready)]
        targets = tuple(generator.randrange(3) for _ in task.output_sizes)
        state.dispatch(Dispatch(task.id, generator.choice(state.free_workers()), targets))
    else:
        state.advance()


def snapshot(state):
    running = {
        task: (flight.action, flight.stage_index, [vars(stage) for stage in flight.stages])
        for task, flight in state.running.items()
    }
    return (
        state.time,
        state.remote_bytes,
        state.unassigned_work,
        list(state.assigned_work),
        dict(state.completed),
        running,
        state.ready(),
    )


def finish(state, action):
    state.dispatch(action)
    while action.task not in state.completed:
        state.advance()
    return state.time


class SimulationTests(unittest.TestCase):
    def test_local_task_reads_then_writes_without_network(self):
        graph = Graph()
        graph.add("map@1", [ObjectRef("node0", "d/0", 100 * MB)], ratios=[0.5], iterations=0)
        state = Simulation(graph, io_models(), POOLS)
        self.assertAlmostEqual(finish(state, Dispatch("t000000", 0, (0,))), 1.0 + 1.0)
        self.assertEqual(state.remote_bytes, 0)
        self.assertEqual(state.completed["t000000"][0].pool, "node0")

    def test_compute_and_output_placement_are_independent_decisions(self):
        graph = Graph()
        graph.add("map@1", [ObjectRef("node0", "d/0", 10 * MB)], ratios=[1], iterations=0)
        state = Simulation(graph, io_models(), POOLS)
        action = Dispatch("t000000", 0, (2,))
        self.assertEqual(state.transfer_bytes(action), 10 * MB)
        self.assertAlmostEqual(finish(state, action), 0.1 + 1.0)
        self.assertEqual(state.completed["t000000"][0].pool, "node2")

    def test_two_senders_share_one_receiver(self):
        graph = Graph()
        graph.add("map@1", [ObjectRef("node0", "d/0", 10 * MB)], ratios=[1], iterations=0)
        graph.add("map@1", [ObjectRef("node1", "d/1", 10 * MB)], ratios=[1], iterations=0)
        alone = Simulation(graph, io_models(), POOLS)
        self.assertAlmostEqual(finish(alone, Dispatch("t000000", 0, (2,))), 0.1 + 1.0)
        shared = Simulation(graph, io_models(), POOLS)
        shared.dispatch(Dispatch("t000000", 0, (2,)))
        shared.dispatch(Dispatch("t000001", 1, (2,)))
        shared.finish_running()
        self.assertAlmostEqual(shared.time, 0.1 + 2.0)

    def test_ready_slots_and_memory_feasibility(self):
        graph = Graph()
        (first,) = graph.add("map@1", [ObjectRef("node0", "d/0", 4 * MB)])
        graph.add("map@1", [first])
        state = Simulation(graph, io_models(), POOLS, memory_limit=1)
        self.assertEqual(state.ready(), ["t000000"])
        self.assertEqual(list(state.candidates()), [])
        with self.assertRaisesRegex(ValueError, "memory"):
            state.dispatch(Dispatch("t000000", 0, (0,)))
        with self.assertRaisesRegex(ValueError, "infeasible"):
            state.dispatch(Dispatch("t000001", 0, (0,)))

    def test_clone_leaves_the_original_untouched(self):
        graph = Graph()
        graph.add("map@1", [ObjectRef("node0", "d/0", 4 * MB)])
        state = Simulation(graph, io_models(), POOLS)
        future = state.clone()
        future.dispatch(Dispatch("t000000", 1, (2,)))
        future.finish_running()
        self.assertEqual(
            (state.time, state.completed, state.running, state.ready()), (0.0, {}, {}, ["t000000"])
        )

    def test_completed_outputs_are_planned_with_their_actual_size(self):
        graph = Graph()
        (first,) = graph.add(
            "map@1", [ObjectRef("node0", "d/0", 10 * MB)], ratios=[1], iterations=0
        )
        graph.add("map@1", [first], ratios=[0], iterations=0)
        state = Simulation(graph, io_models(), POOLS)
        state.completed["t000000"] = (ObjectRef("node0", "actual", 1 * MB),)
        state.refresh()
        self.assertAlmostEqual(state.isolated_seconds(Dispatch("t000001", 1, (1,))), 0.1)

    def test_any_node_count_and_mismatch_is_rejected(self):
        graph = Graph()
        graph.add("map@1", [ObjectRef("a", "d/0", 4 * MB)])
        state = Simulation(graph, Models.uncalibrated(5), ("a", "b", "c", "d", "e"))
        self.assertEqual(state.free_workers(), [0, 1, 2, 3, 4])
        self.assertEqual(len(list(state.candidates())), 5 * 5)
        with self.assertRaisesRegex(ValueError, "pools"):
            Simulation(graph, Models.uncalibrated(2), POOLS)

    def test_ready_tasks_and_clones_stay_consistent_through_a_long_random_episode(self):
        generator = random.Random(11)
        graph = random_graph(generator, 300)
        state = Simulation(graph, io_models(), POOLS)
        clones = 0
        while not state.done:
            ready = state.ready()
            self.assertEqual(ready, sorted(graph.ready(state.completed, state.running)))
            self.assertEqual(
                sorted(state.still_read()),
                sorted(
                    task
                    for task in state.completed
                    if any(child not in state.completed for child in state.successors[task])
                ),
            )
            self.assertFalse(state.resized)
            self.assertEqual(state.ready(5), ready[:5])
            self.assertTrue(all(state.is_ready(task) for task in ready))
            if ready and generator.random() < 0.3:
                submitted = generator.choice(ready)
                live = state.clone()
                live.observe_dispatch(submitted)
                unstarted = sum(
                    work
                    for task, work in state.work.items()
                    if task not in state.completed and task not in state.running
                    if task != submitted
                )
                self.assertEqual(live.unassigned_work, unstarted)
            if generator.random() < 0.3:
                before = snapshot(state)
                future = state.clone()
                for _ in range(generator.randrange(1, 8)):
                    if not future.done:
                        random_step(future, generator)
                self.assertEqual(snapshot(state), before)
                clones += 1
            random_step(state, generator)
        self.assertEqual(len(state.completed), 300)
        self.assertGreater(clones, 100)

    def test_unassigned_work_is_summed_at_refresh_less_each_later_dispatch(self):
        graph = Graph()
        for index in range(20):
            graph.add("map@1", [ObjectRef(POOLS[index % 3], f"d/{index}", (index + 1) * MB)])
        state = Simulation(graph, Models(), POOLS)
        for task_id in ("t000000", "t000003"):
            state.completed[task_id] = (ObjectRef("node0", f"out/{task_id}", 4),)
        state.refresh()
        first, second = state.ready(2)
        state.dispatch(Dispatch(first, 0, (0,)))
        future = state.clone()
        future.dispatch(Dispatch(second, 1, (1,)))
        at_refresh = sum(
            work for task, work in state.work.items() if task not in ("t000000", "t000003")
        )
        self.assertEqual(state.unassigned_work, at_refresh - state.work[first])
        self.assertEqual(
            future.unassigned_work, at_refresh - state.work[first] - state.work[second]
        )

    def test_an_observed_output_size_that_differs_from_the_estimate_is_remembered(self):
        graph = Graph()
        (first,) = graph.add("map@1", [ObjectRef("node0", "d/0", 4 * MB)], ratios=[1])
        graph.add("map@1", [first], ratios=[1])
        state = Simulation(graph, Models(), POOLS)
        state.observe_dispatch("t000000")
        state.observe_completion("t000000", (ObjectRef("node1", "out/0", 4 * MB),))
        self.assertFalse(state.resized)
        self.assertEqual(state.still_read(), ["t000000"])
        state.observe_dispatch("t000001")
        state.observe_completion("t000001", (ObjectRef("node1", "out/1", 4 * MB + 4),))
        self.assertTrue(state.resized)
        self.assertTrue(state.clone().resized)
        self.assertEqual(state.still_read(), [])
        restored = Simulation(graph, Models(), POOLS)
        restored.completed = dict(state.completed)
        self.assertTrue(restored.resized)

    def test_copies_of_completed_outputs_never_see_each_others_changes(self):
        outputs = Outputs({"a": 1})
        for index in range(Outputs.FOLD_AT + 5):
            outputs[f"x{index}"] = index
        copy = outputs.copy()
        copy["a"] = 2
        copy["new"] = 3
        outputs["other"] = 4
        self.assertEqual((outputs["a"], copy["a"]), (1, 2))
        self.assertNotIn("new", outputs)
        self.assertNotIn("other", copy)
        self.assertEqual(list(copy)[:2], ["a", "x0"])
        self.assertEqual(len(copy), Outputs.FOLD_AT + 7)
        self.assertEqual(dict(outputs), {"a": 1, **{f"x{i}": i for i in range(133)}, "other": 4})


class PerFunctionCostTests(unittest.TestCase):
    def test_functions_with_their_own_cost_use_it_and_others_keep_the_default(self):
        default = ComputeModel(1.0, 1e-9, 4e-10, 1e-9)
        cost = FunctionCost(0.5, 2e-9, 3e-9)
        model = ComputeModel(1.0, 1e-9, 4e-10, 1e-9, functions=(("split@1", cost),))
        graph = Graph()
        (left, _) = graph.add("split@1", [ObjectRef("node0", "a", 1000)], ratios=[2, 0.5])
        (mapped,) = graph.add("map@1", [ObjectRef("node0", "b", 1000)], iterations=3)
        split = graph.tasks[left.task]
        self.assertAlmostEqual(model.seconds(split, [1000]), 0.5 + 1000 * 2e-9 + 2500 * 3e-9)
        numeric = graph.tasks[mapped.task]
        self.assertEqual(model.seconds(numeric, [1000]), default.seconds(numeric, [1000]))

    def test_the_forecast_uses_the_per_function_cost(self):
        graph = Graph()
        graph.add("map@1", [ObjectRef("node0", "d/0", 4 * MB)], iterations=50)
        models = Models(ComputeModel(functions=(("map@1", FunctionCost(7.0, 0.0, 0.0)),)))
        state = Simulation(graph, models, POOLS)
        stages = state.stages_for(Dispatch("t000000", 0, (0,)))
        self.assertEqual([stage.remaining for stage in stages if stage.kind == "compute"], [7.0])

    def test_models_json_keeps_the_original_form_and_reads_both(self):
        plain = Models()
        self.assertNotIn("functions", plain.to_dict()["compute"])
        self.assertEqual(Models.from_dict(plain.to_dict()), plain)
        costs = (("split@1", FunctionCost(1, 2, 3)), ("join@1", FunctionCost(4, 5, 6)))
        extended = Models(ComputeModel(functions=tuple(sorted(costs))))
        serialized = json.loads(json.dumps(extended.to_dict()))
        self.assertEqual(
            serialized["compute"]["functions"]["split@1"],
            {"fixed_seconds": 1, "input_seconds_per_byte": 2, "output_seconds_per_byte": 3},
        )
        restored = Models.from_dict(serialized)
        self.assertEqual(restored, extended)
        self.assertEqual(hash(restored.compute), hash(extended.compute))


if __name__ == "__main__":
    unittest.main()
