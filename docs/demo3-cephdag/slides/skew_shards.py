"""Shards per storage node in the sched-skew dataset, from the campaign spec's placement.

Shard i is on node placement[i % len(placement)] (cephdag experiments/run.py, synthetic_dataset).
Writes ../demo-data/t9_skew_shards.csv.

    $PY -I skew_shards.py [CEPHDAG_REPO] [GIT_REF]
"""

import collections
import csv
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "cephdag"
REF = sys.argv[2] if len(sys.argv) > 2 else "sched-gaps"
SPEC = "experiments/campaigns/sched-skew.json"
OUTPUT = Path(__file__).resolve().parent.parent / "demo-data" / "t9_skew_shards.csv"


def main():
    text = subprocess.run(["git", "-C", str(REPO), "show", f"{REF}:{SPEC}"], check=True, capture_output=True,
                          text=True).stdout
    synthetic = json.loads(text)["synthetic"]
    placement = synthetic["placement"]
    shards = synthetic["resident_gib"] * 1024 // synthetic["shard_mib"]
    counts = collections.Counter(placement[index % len(placement)] for index in range(shards))
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["node", "shards", "shard MiB", "dataset", "source"])
        for node in sorted(set(placement)):
            writer.writerow([node, counts[node], synthetic["shard_mib"], synthetic["dataset"], f"cephdag {REF}:{SPEC}"])
    print(dict(sorted(counts.items())), sum(counts.values()), OUTPUT)


if __name__ == "__main__":
    main()
