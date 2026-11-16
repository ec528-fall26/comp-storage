"""Where a task's time goes: per-task read, compute and write seconds in the slot-matched runs.

Reads every per-run record of the demo3-slots campaign at 1 and 256 kernel iterations, for
computational storage (cs2) and the disaggregated baseline (disagg2), and writes one row per
mode, intensity and stage to ../demo-data/t8_task_stages.csv.

    $PY -I task_stages.py [CAMPAIGN_DIR]
"""

import csv
import json
import statistics
import sys
from pathlib import Path

CAMPAIGN = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "datasets/cephdag-demo3/campaigns/demo3-slots"
OUTPUT = Path(__file__).resolve().parent.parent / "demo-data" / "t8_task_stages.csv"
MODES = ("disagg2", "cs2")
INTENSITIES = (1, 256)
STAGES = ("read", "compute", "write")


def stage_seconds(run_files):
    seconds = {stage: [] for stage in STAGES}
    for path in run_files:
        record = json.loads(path.read_text())
        for task in record["tasks"].values():
            for stage in task["result"]["stages"]:
                seconds[stage["kind"]].append(stage["seconds"])
    return seconds


def main():
    rows = []
    for intensity in INTENSITIES:
        for mode in MODES:
            run_files = sorted(CAMPAIGN.glob(f"independent-i{intensity}-r*-{mode}/a1/run-*.json"))
            seconds = stage_seconds(run_files)
            total = sum(sum(values) for values in seconds.values())
            for stage in STAGES:
                rows.append({
                    "mode": mode,
                    "iterations": intensity,
                    "stage": stage,
                    "median s": round(statistics.median(seconds[stage]), 3),
                    "sum s": round(sum(seconds[stage]), 1),
                    "share of task time": round(sum(seconds[stage]) / total, 3),
                    "tasks": len(seconds[stage]),
                    "runs": " ".join(f"{path.parent.parent.name}/{path.name}" for path in run_files),
                })
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row["mode"], row["iterations"], row["stage"], row["median s"], row["sum s"], row["share of task time"],
              row["tasks"])
    print(OUTPUT)


if __name__ == "__main__":
    main()
