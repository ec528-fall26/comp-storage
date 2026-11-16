"""What HEFT and link-aware HEFT plan on the skewed dataset at 1 iteration, against what was measured.

Builds the sched-skew graph and each policy's static plan with the lab's calibrated models (no
lab is touched) and writes ../demo-data/t11_heft_plan.csv: the finish time the plan expects, the
tasks it moves off node 0, the seconds node 0's one link needs to carry them, and the measured
median wall time from t4_sched_skew_cells.csv.

    cd cephdag && .venv/bin/python -I ../demo3/slides/heft_plan.py [MODELS_JSON]
"""

import csv
import json
import sys
from pathlib import Path

DEMO3 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(DEMO3))

import sched_forecast  # noqa: E402
from cephdag.model import Models, Simulation  # noqa: E402
from cephdag.policies import make_policy  # noqa: E402

MODELS = Path.home() / "datasets/cephdag-demo3/calibration/cephdag8-cs/models.json"
MEASURED = DEMO3 / "demo-data" / "t4_sched_skew_cells.csv"
OUTPUT = DEMO3 / "demo-data" / "t11_heft_plan.csv"
CASE = "skew-i1"
POLICIES = ("heft", "heft-cc")
HOT_POOL, HOT_NODE = "node0", 0


def planned(policy_name, graph, models):
    """(seconds the static plan expects to take, tasks it moves off the hot node)."""
    policy = make_policy(policy_name)
    policy.choose(Simulation(graph, models, sched_forecast.POOLS))
    plan = policy.plan
    finish = max(interval[1] for interval in plan.intervals.values())
    moved = sum(
        graph.tasks[task].inputs[0].pool == HOT_POOL and action.worker != HOT_NODE
        for task, action in plan.actions.items()
    )
    return finish, moved


def measured_seconds():
    with open(MEASURED, newline="", encoding="utf-8") as handle:
        return {row["policy"]: float(row["median s"]) for row in csv.DictReader(handle) if row["iterations"] == "1"}


def main():
    models_path = Path(sys.argv[1]) if len(sys.argv) > 1 else MODELS
    models = Models.from_dict(json.loads(models_path.read_text()))
    graph = sched_forecast.graph_for(CASE)
    shard_seconds = sched_forecast.SHARD / models.network.tx_bps[HOT_NODE]
    measured = measured_seconds()
    rows = []
    for name in POLICIES:
        finish, moved = planned(name, graph, models)
        rows.append({
            "policy": name,
            "planned s": round(finish, 1),
            "tasks off node 0": moved,
            "tasks": len(graph.tasks),
            "shard s on the link": round(shard_seconds, 2),
            "link s for the moves": round(moved * shard_seconds, 1),
            "measured s": measured[name],
        })
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row)
    print(OUTPUT)


if __name__ == "__main__":
    main()
