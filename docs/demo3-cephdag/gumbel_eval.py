"""In-process evaluation of the Gumbel search arc on the lab's calibrated models; no lab is touched.

    cd <cephdag checkout> && .venv/bin/python -I ../demo3/gumbel_eval.py MODELS_JSON OUT_JSONL \\
        [--cases skew-i1,...] [--policies gumbel-32,...] [--charged] [--options JSON]

The world is demo3/sched_forecast.py's FairSimulation: concurrent stages share disks and links
max-min fairly, and a Python policy that forecasts on a clone of the state gets the equal-share
model.Simulation, as in the lab. Native policies plan with the forecast their name selects
(equal for beam-N and native-mcts-*, fair for the -fair names, gumbel and restarts). With
--charged, every decision's measured planning time passes in the world before its dispatch,
as the lab's serial coordinator spends it; otherwise planning is free and the wall is the plan's
alone. One JSON line per (case, policy, options, charged) is appended to OUT_JSONL; existing keys
are skipped, so a rerun resumes.

Cases: skew-i<N> (8 GiB, 65 of 128 64 MiB shards on node 0), uniform-i<N> (16 GiB round-robin),
and the held-out tuning cases hot5-i<N> (4 GiB, 5 of every 11 shards on node 0) and
uniform8-i<N> (8 GiB round-robin), which the evaluation does not use.
"""

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cephdag.graph import Dataset, ObjectRef  # noqa: E402
from cephdag.model import Models  # noqa: E402
from cephdag.policies import make_policy  # noqa: E402
from experiments.run import build_workload  # noqa: E402

from sched_forecast import FairSimulation, fair_rates  # noqa: E402

SHARD = 64 * 2**20
POOLS = tuple(f"node{node}" for node in range(8))
SHAPES = {
    "skew": ((0,) * 7 + tuple(range(1, 8)), 8),
    "uniform": (tuple(range(8)), 16),
    "hot5": ((0,) * 5 + tuple(range(1, 7)), 4),
    "uniform8": (tuple(range(8)), 8),
}


def graph_for(case):
    shape, iterations = case.split("-i")
    placement, gib = SHAPES[shape]
    shards = tuple(
        ObjectRef(POOLS[placement[index % len(placement)]], f"d/{index}", SHARD, partition=index)
        for index in range(gib * 2**30 // SHARD)
    )
    return build_workload(Dataset(case, shards), "independent", int(iterations), 0.25)


class World(FairSimulation):
    """FairSimulation that can also let a fixed time pass, for planning time."""

    def advance_for(self, seconds):
        """Let `seconds` pass; return whether a stage ended meanwhile."""
        ended = False
        while seconds > 0 and self.running:
            rates = fair_rates(
                {task_id: flight.stages[0].resources for task_id, flight in self.running.items()},
                self.capacity,
            )
            boundary = min(
                flight.stages[0].remaining / rates[task_id]
                for task_id, flight in self.running.items()
            )
            step = min(boundary, seconds)
            for task_id, flight in self.running.items():
                flight.stages[0].remaining = max(
                    0, flight.stages[0].remaining - step * rates[task_id]
                )
            self.time += step
            seconds -= step
            ended = ended or step >= boundary
            self._complete_finished()
        if seconds > 0:
            self.time += seconds
        return ended


def run(name, graph, models, charged, options):
    policy = make_policy(name, **options)
    state = World(graph, models, POOLS)
    hot = 0
    decisions = []
    moved_tasks = 0
    moved_off_hot = 0
    searched = 0
    transitions = 0
    truncated = 0
    while not state.done:
        started = time.perf_counter()
        action = policy.choose(state)
        seconds = time.perf_counter() - started
        info = getattr(policy, "last_info", {})
        decisions.append(seconds)
        searched += bool(info.get("searched"))
        transitions += info.get("simulated_transitions", 0)
        requested = info.get("iterations_requested")
        truncated += bool(
            requested and info.get("searched", True) and info["iterations"] < requested
        )
        ended = state.advance_for(seconds) if charged else False
        if action is None:
            if not ended:
                state.advance()
            continue
        inputs = graph.tasks[action.task].inputs
        if any(state.location(ref) != action.worker for ref in inputs):
            moved_tasks += 1
            moved_off_hot += any(state.location(ref) == hot for ref in inputs)
        state.dispatch(action)
    if hasattr(policy, "close"):
        policy.close()
    return {
        "wall_seconds": state.time,
        "planning_seconds": sum(decisions),
        "decisions": len(decisions),
        "decision_ms_median": 1000 * statistics.median(decisions) if decisions else 0.0,
        "decision_ms_max": 1000 * max(decisions) if decisions else 0.0,
        "moved_bytes": state.remote_bytes,
        "moved_tasks": moved_tasks,
        "moved_off_node0": moved_off_hot,
        "searches": searched,
        "simulated_transitions": transitions,
        "truncated_searches": truncated,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("models")
    parser.add_argument("output")
    parser.add_argument("--cases", default="skew-i1,skew-i16,skew-i128,skew-i1024")
    parser.add_argument("--policies", required=True)
    parser.add_argument("--charged", action="store_true")
    parser.add_argument("--options", default="{}")
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    models = Models.from_dict(json.loads(Path(args.models).read_text()))
    options = json.loads(args.options)
    output = Path(args.output)
    done = set()
    if output.exists():
        done = {json.loads(line)["key"] for line in output.read_text().splitlines() if line}
    for case in args.cases.split(","):
        graph = graph_for(case)
        for name in args.policies.split(","):
            key = f"{case}/{name}/{args.label}/{'charged' if args.charged else 'free'}"
            if key in done:
                continue
            row = {"key": key, "case": case, "policy": name, "label": args.label}
            row["charged"] = args.charged
            row["options"] = options
            row.update(run(name, graph, models, args.charged, options))
            with output.open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
