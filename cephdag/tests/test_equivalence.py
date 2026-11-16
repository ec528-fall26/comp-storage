"""The rewrite must decide exactly as the original refinement-v2 code did.

tests/fixtures/equivalence.json was recorded from the frozen original by
tests/fixtures/make_equivalence_fixture.py under Python 3.9.
"""

import json
from pathlib import Path
import unittest

from cephdag.coordinator import observed_state
from cephdag.graph import Dataset, Graph, ref_from_dict
from cephdag.model import Dispatch, InFlight, Models, Objective, Simulation, Stage
from cephdag.policies.baselines import DataLocal, RandomPolicy, SingleNode
from cephdag.policies.greedy import Greedy, GreedyWait
from cephdag.policies.list import Heft, WorkStealing
from cephdag.policies.native import Beam, NativeMcts
from experiments.run import build_workload
from tests.helpers import LAB_POOLS, rng_digest

FIXTURE = Path(__file__).parent / "fixtures" / "equivalence.json"


def load_fixture():
    return json.loads(FIXTURE.read_text())


def make_policies(seed, objective):
    return {
        "random": RandomPolicy(seed),
        "single-node": SingleNode(),
        "data-local": DataLocal(),
        "greedy": Greedy(objective),
        "greedy-wait": GreedyWait(objective),
        "heft": Heft(objective),
        "stealing": WorkStealing(objective),
        "beam-4": Beam(width=4, budget_ms=1e9, objective=objective),
        "native-mcts-heuristic-32": NativeMcts(
            iterations=32, budget_ms=1e9, seed=seed, rollout_policy="heuristic", objective=objective
        ),
        "native-mcts-uniform-32": NativeMcts(
            iterations=32, budget_ms=1e9, seed=seed, rollout_policy="uniform", objective=objective
        ),
    }


def action_from_dict(value):
    if value is None:
        return None
    return Dispatch(value["task"], value["worker"], tuple(value["targets"]))


def action_to_dict(action):
    if action is None:
        return None
    return {"task": action.task, "worker": action.worker, "targets": list(action.targets)}


def restore_state(graph, models, saved):
    state = Simulation(graph, models, LAB_POOLS, memory_limit=saved["memory_limit"])
    state.time = saved["time"]
    state.remote_bytes = saved["remote_bytes"]
    state.completed = {
        task: tuple(ref_from_dict(ref) for ref in refs) for task, refs in saved["completed"].items()
    }
    state.running = {
        task: InFlight(
            action_from_dict(flight["action"]),
            [
                Stage(stage["kind"], stage["remaining"], tuple(stage["resources"]))
                for stage in flight["stages"]
            ],
            flight["stage_index"],
        )
        for task, flight in saved["running"].items()
    }
    state.assigned_work = list(saved["assigned_work"])
    state.ready_order = list(saved["ready_order"])
    state.ready_set = set(saved["ready_order"])
    state.unassigned_work = saved["unassigned_work"]
    return state


def dump_state(state):
    return {
        "time": state.time,
        "remote_bytes": state.remote_bytes,
        "unassigned_work": state.unassigned_work,
        "assigned_work": list(state.assigned_work),
        "memory_limit": state.memory_limit,
        "completed": {
            task: [ref.to_dict() for ref in refs] for task, refs in state.completed.items()
        },
        "running": {
            task: {
                "action": action_to_dict(flight.action),
                "stage_index": flight.stage_index,
                "stages": [
                    {
                        "kind": stage.kind,
                        "remaining": stage.remaining,
                        "resources": list(stage.resources),
                    }
                    for stage in flight.stages
                ],
            }
            for task, flight in state.running.items()
        },
        "ready_order": list(state.ready_order),
    }


def decide(policy, state):
    try:
        action = policy.choose(state)
    except Exception as error:
        return {"error": type(error).__name__}
    record = {"action": action_to_dict(action)}
    if hasattr(policy, "random"):
        record["rng"] = rng_digest(policy.random)
    return record


def close_all(policies):
    for policy in policies.values():
        if hasattr(policy, "close"):
            policy.close()


def relative_difference(expected, actual):
    if expected == actual:
        return 0.0
    return abs(expected - actual) / max(abs(expected), abs(actual), 1e-300)


class EquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_fixture()

    def test_fixture_is_large_enough(self):
        self.assertGreaterEqual(len(self.fixture["states"]), 300)
        self.assertGreaterEqual(len(self.fixture["episodes"]), 40)

    def test_every_policy_chooses_the_recorded_action(self):
        mismatches = []
        compared = 0
        for row in self.fixture["states"]:
            graph = Graph.from_dict(row["graph"])
            models = Models.from_dict(row["models"])
            policies = make_policies(row["seed"], Objective(*row["objective"]))
            for name, policy in policies.items():
                state = restore_state(graph, models, row["state"])
                actual = decide(policy, state)
                compared += 1
                if actual != row["decisions"][name]:
                    mismatches.append((row["seed"], name, row["decisions"][name], actual))
            close_all(policies)
        self.assertEqual(compared, 10 * len(self.fixture["states"]))
        self.assertEqual(mismatches, [], f"{len(mismatches)} of {compared} decisions differ")

    def test_policy_choice_does_not_mutate_state(self):
        row = self.fixture["states"][1]
        graph = Graph.from_dict(row["graph"])
        models = Models.from_dict(row["models"])
        for name, policy in make_policies(1, Objective()).items():
            state = restore_state(graph, models, row["state"])
            decide(policy, state)
            self.assertEqual(dump_state(state), row["state"], name)
            if hasattr(policy, "close"):
                policy.close()

    def test_forecasts_match(self):
        worst = 0.0
        for row in self.fixture["states"]:
            graph = Graph.from_dict(row["graph"])
            models = Models.from_dict(row["models"])
            state = restore_state(graph, models, row["state"])
            recorded = row["forecasts"]
            worst = max(
                worst, relative_difference(recorded["tail_estimate"], state.tail_estimate())
            )
            for expected in recorded["dispatches"]:
                action = action_from_dict(expected["action"])
                future = state.clone()
                future.dispatch(action)
                while action.task not in future.completed:
                    future.advance()
                everything = future.clone()
                everything.finish_running()
                self.assertEqual(future.remote_bytes, expected["remote_bytes"])
                self.assertEqual(state.transfer_bytes(action), expected["transfer_bytes"])
                worst = max(
                    worst,
                    relative_difference(expected["finish_time"], future.time),
                    relative_difference(
                        expected["isolated_seconds"], state.isolated_seconds(action)
                    ),
                    relative_difference(expected["all_running_finish_time"], everything.time),
                )
        self.assertLessEqual(worst, 1e-9)

    def test_whole_episodes_match(self):
        mismatches = []
        for row in self.fixture["episodes"]:
            graph = Graph.from_dict(row["graph"])
            models = Models.from_dict(row["models"])
            policies = make_policies(row["seed"], Objective(*row["objective"]))
            for name, policy in policies.items():
                state = Simulation(graph, models, LAB_POOLS)
                actions = []
                outcome = "done"
                try:
                    for _ in range(500):
                        if state.done:
                            break
                        action = policy.choose(state)
                        actions.append(action_to_dict(action))
                        if action is None:
                            state.advance()
                        else:
                            state.dispatch(action)
                except Exception as error:
                    outcome = type(error).__name__
                expected = row["episodes"][name]
                actual = {"actions": actions, "time": state.time, "outcome": outcome}
                if (
                    actual["actions"] != expected["actions"]
                    or actual["outcome"] != expected["outcome"]
                ):
                    mismatches.append((row["seed"], name))
                elif relative_difference(expected["time"], actual["time"]) > 1e-9:
                    mismatches.append((row["seed"], name, expected["time"], actual["time"]))
            close_all(policies)
        self.assertEqual(mismatches, [])

    def test_live_state_reconstruction_matches(self):
        for row in self.fixture["live_states"]:
            graph = Graph.from_dict(row["graph"])
            models = Models.from_dict(row["models"])
            completed = {
                task: tuple(ref_from_dict(ref) for ref in refs)
                for task, refs in row["completed"].items()
            }
            pending = {task: action_from_dict(action) for task, action in row["pending"].items()}
            history = {task: action_from_dict(action) for task, action in row["history"].items()}
            state = observed_state(
                graph,
                models,
                LAB_POOLS,
                completed,
                pending,
                row["jobs"],
                row["assigned"],
                now=row["now"],
                elapsed=row["elapsed"],
                history=history if row["used_history"] else None,
            )
            actual = dump_state(state)
            expected = row["result"]
            for task, flight in expected["running"].items():
                for want, got in zip(flight["stages"], actual["running"][task]["stages"]):
                    self.assertLessEqual(
                        relative_difference(want["remaining"], got["remaining"]), 1e-9
                    )
                    got["remaining"] = want["remaining"]
            self.assertEqual(actual, expected)

    def test_experiment_workloads_build_the_original_graphs(self):
        recorded = self.fixture["workloads"]
        dataset = Dataset.from_dict(recorded["dataset"])
        for case, tasks in recorded["graphs"].items():
            shape, iterations, ratio = case.rsplit("-", 2)
            graph = build_workload(dataset, shape, int(iterations), float(ratio))
            self.assertEqual(
                normalize([task.to_dict() for task in graph.tasks.values()]), normalize(tasks), case
            )


def normalize(tasks):
    text = json.dumps(tasks)
    for task in tasks:
        for ref in task["inputs"]:
            if ref["kind"] == "output":
                text = text.replace(ref["graph"], "GRAPH")
    return json.loads(text)


if __name__ == "__main__":
    unittest.main()
