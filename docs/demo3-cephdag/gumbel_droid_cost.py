"""Planning cost per decision on a DROID batch-sized graph, built as experiments/run.py builds DROID
graphs (experiments.droid_workload.build_graph), from synthetic partitions.

    cd <cephdag gumbel checkout> && .venv/bin/python -I ../../demo3/gumbel_droid_cost.py \\
        MODELS OUT_JSON POLICY [POLICY ...]

The batch: 2,255 partitions (4 tasks each, 9,020 tasks, as demo3-droid's batch 2), sizes drawn
log-uniformly within a factor e^0.5 of batch 2's mean of 31 MB with seed 2 (wider spreads exceed
a worker's memory reservation), placed on the 8 nodes at random as CRUSH would, steps set so the
frames are 2.16x the source. Each policy runs the whole graph in
demo3/sched_forecast.py's FairSimulation with planning free; the time of every choose() is kept.
"""

import json
import math
from pathlib import Path
import random
import statistics
import sys
import time

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cephdag import droid  # noqa: E402
from cephdag.graph import Dataset, ObjectRef  # noqa: E402
from cephdag.model import Models  # noqa: E402
from cephdag.policies import make_policy  # noqa: E402
from experiments import droid_workload  # noqa: E402

from sched_forecast import FairSimulation  # noqa: E402

POOLS = tuple(f"node{node}" for node in range(8))
PARTITIONS = 2255
MEAN_BYTES = 69.8e9 / PARTITIONS


def batch():
    generator = random.Random(2)
    shards = []
    steps = []
    for index in range(PARTITIONS):
        size = int(MEAN_BYTES * math.exp(generator.uniform(-0.5, 0.5)) / (2 * math.sinh(0.5)))
        pool = POOLS[generator.randrange(8)]
        shards.append(ObjectRef(pool, f"droid/{index}", size, "droid-episodes", index))
        steps.append(max(1, round(2.16 * size / droid.frame_bytes(1, 128))))
    return droid_workload.build_graph(Dataset("droid-batch", tuple(shards)), 128, steps)


def run(name, graph, models):
    policy = make_policy(name)
    state = FairSimulation(graph, models, POOLS)
    seconds = []
    searches = 0
    started = time.perf_counter()
    while not state.done:
        before = time.perf_counter()
        action = policy.choose(state)
        seconds.append(time.perf_counter() - before)
        searches += bool(getattr(policy, "last_info", {}).get("searched"))
        if action is None:
            state.advance()
        else:
            state.dispatch(action)
    if hasattr(policy, "close"):
        policy.close()
    ordered = sorted(seconds)
    return {
        "policy": name,
        "tasks": len(graph.tasks),
        "decisions": len(seconds),
        "searches": searches,
        "planning_seconds": sum(seconds),
        "decision_ms_median": 1000 * statistics.median(seconds),
        "decision_ms_p90": 1000 * ordered[int(0.9 * len(ordered))],
        "decision_ms_max": 1000 * ordered[-1],
        "forecast_wall_seconds": state.time,
        "moved_bytes": state.remote_bytes,
        "elapsed_seconds": time.perf_counter() - started,
    }


def main():
    models = Models.from_dict(json.loads(Path(sys.argv[1]).read_text()))
    output = Path(sys.argv[2])
    graph = batch()
    rows = json.loads(output.read_text()) if output.exists() else []
    for name in sys.argv[3:]:
        row = run(name, graph, models)
        print(json.dumps(row), flush=True)
        rows.append(row)
        output.write_text(json.dumps(rows, indent=1) + "\n")


if __name__ == "__main__":
    main()
