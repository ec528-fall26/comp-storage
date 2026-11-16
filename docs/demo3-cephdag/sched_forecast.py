"""Forecast scheduling policies in-process on the skew and uniform graphs with the lab's
calibrated models; no lab is touched.

    cd cephdag && .venv/bin/python -I ../demo3/sched_forecast.py MODELS_JSON OUTPUT_JSON [CASE ...]

A case is skew-i<N> (8 GiB, 65 of 128 shards on node 0) or uniform-i<N> (16 GiB round-robin).
Each policy runs to completion twice: in model.Simulation, the forecast the policies use,
which gives each concurrent stage an equal share of every disk and link it uses ("equal"),
and in FairSimulation below, which shares them max-min fairly ("fair"). Records wall
seconds, planning seconds (time spent in choose), bytes moved and tasks moved off their
data's node. Beam runs with its default 40 ms budget, as in the lab.
"""

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path.cwd()))

from cephdag.graph import Dataset, ObjectRef  # noqa: E402
from cephdag.model import Models, Simulation  # noqa: E402
from cephdag.policies import make_policy  # noqa: E402
from experiments.run import build_workload  # noqa: E402

SHARD = 64 * 2**20
POOLS = tuple(f"node{node}" for node in range(8))
SKEW = (0,) * 7 + tuple(range(1, 8))
POLICIES = (
    "heft-cc",
    "backlog",
    "lp-plan",
    "data-local",
    "greedy-wait",
    "stealing",
    "heft",
    "beam-4",
)


def dataset(case):
    shape, _ = case.split("-i")
    placement, total = (SKEW, 8 * 2**30) if shape == "skew" else (tuple(range(8)), 16 * 2**30)
    shards = tuple(
        ObjectRef(POOLS[placement[index % len(placement)]], f"d/{index}", SHARD, partition=index)
        for index in range(total // SHARD)
    )
    return Dataset(case, shards)


def graph_for(case):
    iterations = int(case.split("-i")[1])
    return build_workload(dataset(case), "independent", iterations, 0.25)


class FairSimulation(Simulation):
    """The same forecast, but concurrent stages share each disk and link max-min fairly: a
    stage held back by one resource leaves the rest of its other resources to the others.
    model.Simulation instead gives every stage an equal share of each resource it uses, so a
    47 MB/s transfer out of node 0 halves node 0's own 264 MB/s local read. A policy that
    forecasts on a clone of the state (greedy, greedy-wait, stealing) gets a model.Simulation,
    as in the lab."""

    def clone(self):
        other = super().clone()
        other.__class__ = Simulation
        return other

    def advance(self):
        self._complete_finished()
        if not self.running:
            if self.done:
                return 0.0
            raise RuntimeError("cannot wait without a running task")
        rates = fair_rates(
            {task_id: flight.stages[0].resources for task_id, flight in self.running.items()},
            self.capacity,
        )
        step = min(
            flight.stages[0].remaining / rates[task_id] for task_id, flight in self.running.items()
        )
        for task_id, flight in self.running.items():
            flight.stages[0].remaining = max(0, flight.stages[0].remaining - step * rates[task_id])
        self.time += step
        self._complete_finished()
        return step


def fair_rates(uses, capacity):
    """Max-min fair rate of each flow, `uses` mapping a flow to the resources it needs."""
    left = dict(capacity)
    rates = {}
    while len(rates) < len(uses):
        active = {flow: names for flow, names in uses.items() if flow not in rates}
        users = {}
        for flow, names in active.items():
            for name in names:
                users.setdefault(name, []).append(flow)
        bottleneck = min(users, key=lambda name: left[name] / len(users[name]))
        share = left[bottleneck] / len(users[bottleneck])
        for flow in users[bottleneck]:
            rates[flow] = share
            for name in active[flow]:
                left[name] -= share
    return rates


def forecast(name, graph, models, simulation=Simulation):
    policy = make_policy(name)
    state = simulation(graph, models, POOLS)
    planning = 0.0
    moved_tasks = 0
    moved_off_node0 = 0
    infos = []
    while not state.done:
        started = time.perf_counter()
        action = policy.choose(state)
        planning += time.perf_counter() - started
        info = getattr(policy, "last_info", {})
        if info and any(key.startswith("lp_") for key in info):
            infos.append(dict(info))
        if action is None:
            state.advance()
            continue
        inputs = graph.tasks[action.task].inputs
        if any(state.location(ref) != action.worker for ref in inputs):
            moved_tasks += 1
            moved_off_node0 += any(state.location(ref) == 0 for ref in inputs)
        state.dispatch(action)
    return {
        "wall_seconds": state.time,
        "planning_seconds": planning,
        "moved_bytes": state.remote_bytes,
        "moved_tasks": moved_tasks,
        "moved_off_node0": moved_off_node0,
        "lp": infos[0] if infos else None,
    }


SIMULATIONS = {"equal": Simulation, "fair": FairSimulation}
CASES = ("skew-i1", "skew-i16", "skew-i128", "skew-i1024", "uniform-i256", "uniform-i1024")


def main():
    models = Models.from_dict(json.loads(Path(sys.argv[1]).read_text()))
    output = Path(sys.argv[2])
    cases = sys.argv[3:] or CASES
    results = json.loads(output.read_text()) if output.exists() else {}
    for case in cases:
        graph = graph_for(case)
        for name in POLICIES:
            for sharing, simulation in SIMULATIONS.items():
                key = f"{sharing}/{case}/{name}"
                if key in results:
                    continue
                results[key] = forecast(name, graph, models, simulation)
                print(key, json.dumps(results[key]), flush=True)
                output.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
