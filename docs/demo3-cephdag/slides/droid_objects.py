"""Bytes of every object the DROID pipeline reads and writes, over the 496 GB run.

Sums the stored size of each kind of object in the computational-storage data-local run of
every wave of the demo3-droid campaign (every configuration of a wave produced the same
outputs) and writes ../demo-data/t10_droid_objects.csv: the source episodes and the five
kinds of output, in total and per partition.

    $PY -I droid_objects.py [CAMPAIGN_DIR]
"""

import collections
import csv
import json
import sys
from pathlib import Path

CAMPAIGN = Path.home() / "datasets/cephdag-demo3/campaigns/demo3-droid"
OUTPUT = Path(__file__).resolve().parent.parent / "demo-data" / "t10_droid_objects.csv"
RUNS = "wave*-cs/a1/wave-*/run-*-data-local.json"
SOURCE = "episodes"
OBJECTS = {
    "droid-frames": "frames",
    "droid-state": "state",
    "droid-video": "video",
    "droid-stats": "stats",
    "parquet": "parquet",
}
PARTITION_OUTPUT = "droid-frames"


def totals(records):
    """(bytes per object name, partitions, tasks) summed over the run records."""
    size = collections.Counter()
    tasks = 0
    for record in records:
        size[SOURCE] += record["dataset_bytes"]
        for task in record["tasks"].values():
            tasks += 1
            for output in task["result"]["outputs"]:
                size[OBJECTS[output["dtype"]]] += output["size"]
    partitions = sum(
        output["dtype"] == PARTITION_OUTPUT
        for record in records
        for task in record["tasks"].values()
        for output in task["result"]["outputs"]
    )
    return size, partitions, tasks


def table(size, partitions, tasks, runs):
    return [
        {
            "object": name,
            "GB": round(size[name] / 1e9, 2),
            "MB per partition": round(size[name] / partitions / 1e6, 3),
            "times source": round(size[name] / size[SOURCE], 5),
            "partitions": partitions,
            "tasks": tasks,
            "runs": runs,
        }
        for name in (SOURCE, *OBJECTS.values())
    ]


def main():
    campaign = Path(sys.argv[1]) if len(sys.argv) > 1 else CAMPAIGN
    run_files = sorted(campaign.glob(RUNS))
    records = [json.loads(path.read_text()) for path in run_files]
    rows = table(*totals(records), len(run_files))
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row["object"], row["GB"], row["MB per partition"], row["times source"])
    print(OUTPUT)


if __name__ == "__main__":
    main()
