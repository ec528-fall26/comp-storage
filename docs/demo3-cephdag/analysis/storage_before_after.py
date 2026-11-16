"""Local read and write rates of the same skew cells before and after the DROID campaign.

    python3 -I storage_before_after.py CAMPAIGNS_DIR

CAMPAIGNS_DIR holds copies of aakashpc:/mnt/mass/cephdag/campaigns/demo3-skew (run 2026-11-13,
before demo3-droid) and sched-skew (2026-11-14, after it). Both are the 8 GiB skew graph, task i
reading 64 MiB shard i. A read is local when the shard's pool is the worker's node; a write is
local when every output went to the worker's pool. Rates are stage bytes over stage seconds,
median over the tasks of three repeats.
"""

import json
import statistics
import sys
from pathlib import Path

POLICIES = ("greedy-wait", "data-local", "stealing", "heft", "beam-4")


def rates(path, shards):
    run = json.loads(path.read_text())
    reads, writes = [], []
    for task_id, record in run["tasks"].items():
        worker = record["worker"]
        stages = record["result"]["stages"]
        read = [s for s in stages if s["kind"] == "read"]
        write = [s for s in stages if s["kind"] == "write"]
        if shards[int(task_id[1:])]["pool"] == f"node{worker}":
            reads.append(sum(s["bytes"] for s in read) / sum(s["seconds"] for s in read) / 1e6)
        if all(output["pool"] == f"node{worker}" for output in record["result"]["outputs"]):
            writes.append(sum(s["bytes"] for s in write) / sum(s["seconds"] for s in write) / 1e6)
    return run["wall_seconds"], reads, writes


def cell(campaign, iterations, policy):
    walls, reads, writes = [], [], []
    for directory in sorted(campaign.glob(f"independent-i{iterations}-r*-cs/a1")):
        shards = json.loads((directory / "config.json").read_text())["dataset"]["shards"]
        for path in directory.glob(f"run-*-{policy}.json"):
            wall, r, w = rates(path, shards)
            walls.append(wall)
            reads += r
            writes += w
    if not walls:
        return None
    return statistics.median(walls), statistics.median(reads), statistics.median(writes)


def main():
    root = Path(sys.argv[1])
    print("| iterations | policy | wall s before / after | local read MB/s before / after | local write MB/s before / after |")
    print("|---|---|---|---|---|")
    for iterations in (1, 16, 128, 1024):
        for policy in POLICIES:
            before = cell(root / "demo3-skew", iterations, policy)
            after = cell(root / "sched-skew", iterations, policy)
            if before and after:
                print(
                    f"| {iterations} | {policy} | {before[0]:.1f} / {after[0]:.1f} | "
                    f"{before[1]:.0f} / {after[1]:.0f} ({after[1] / before[1] - 1:+.0%}) | "
                    f"{before[2]:.0f} / {after[2]:.0f} ({after[2] / before[2] - 1:+.0%}) |"
                )


if __name__ == "__main__":
    main()
