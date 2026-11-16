"""Is a policy's planning time on the critical path? Measured from the per-task dispatch times.

    python3 -I planning_critical_path.py SCHED_SKEW_DIR

SCHED_SKEW_DIR is a copy of aakashpc:/mnt/mass/cephdag/campaigns/sched-skew. The coordinator
loop is serial (poll, observe, choose, dispatch or wait), so while policy.choose runs nothing is
dispatched. Each decision is recorded with its end time (at_seconds) and length (seconds). A
task's worker-side finish is submitted + slack / 2 + result.seconds, slack being the part of
submit-to-completion the worker did not report. For node 0's worker and for the worker that
finishes last, the script sums the idle gaps before each of its tasks and the part of those
gaps that falls inside choose() calls (median over 3 repeats).
"""

import json
import sys
from statistics import median
from pathlib import Path

POLICIES = ("greedy-wait", "backlog", "heft-cc", "lp-plan", "data-local", "stealing", "beam-4", "heft")


def union(intervals):
    total, current = 0.0, None
    for left, right in sorted(intervals):
        if current is None or left > current[1]:
            total += 0 if current is None else current[1] - current[0]
            current = [left, right]
        else:
            current[1] = max(current[1], right)
    return total + (0 if current is None else current[1] - current[0])


def worker_gaps(run, worker, decisions):
    tasks = sorted(
        (
            record["submitted"],
            record["actual_finish"]
            - (record["actual_finish"] - record["submitted"] - record["result"]["seconds"]) / 2,
        )
        for record in run["tasks"].values()
        if record["worker"] == worker
    )
    gaps = [(0.0, tasks[0][0])] + [(a[1], b[0]) for a, b in zip(tasks, tasks[1:])]
    idle = sum(right - left for left, right in gaps)
    inside = sum(
        union(
            (max(left, start), min(right, end))
            for start, end in decisions
            if min(right, end) > max(left, start)
        )
        for left, right in gaps
    )
    executing = sum(finish - submit for submit, finish in tasks)
    return len(tasks), executing, idle, inside


def summarise(path):
    run = json.loads(path.read_text())
    decisions = [(d["at_seconds"] - d["seconds"], d["at_seconds"]) for d in run["decisions"]]
    last = max(run["tasks"].values(), key=lambda record: record["actual_finish"])["worker"]
    return {
        "wall": run["wall_seconds"],
        "policy": run["scheduler_seconds"],
        "decisions": len(decisions),
        "waits": sum(d["action"] is None for d in run["decisions"]),
        "longest": max(end - start for start, end in decisions),
        "node0": worker_gaps(run, 0, decisions),
        "last": (last, *worker_gaps(run, last, decisions)),
    }


def main():
    campaign = Path(sys.argv[1])
    print("| iterations | policy | wall s | policy s | decisions (waits), longest s | node 0: tasks, executing s, idle gaps s (ms/task), inside choose s | last worker: id, idle gaps s, inside choose s |")
    print("|---|---|---|---|---|---|---|")
    for iterations in (1, 16, 128, 1024):
        for policy in POLICIES:
            runs = [
                summarise(path)
                for path in sorted(campaign.glob(f"independent-i{iterations}-r*-cs/a1/run-*-{policy}.json"))
            ]
            if not runs:
                continue
            tasks, executing, idle, inside = (median(r["node0"][k] for r in runs) for k in range(4))
            print(
                f"| {iterations} | {policy} | {median(r['wall'] for r in runs):.1f} | "
                f"{median(r['policy'] for r in runs):.2f} | "
                f"{median(r['decisions'] for r in runs):.0f} ({median(r['waits'] for r in runs):.0f}), "
                f"{median(r['longest'] for r in runs):.2f} | "
                f"{tasks:.0f}, {executing:.1f}, {idle:.2f} ({1000 * idle / tasks:.0f}), {inside:.2f} | "
                f"{'/'.join(str(r['last'][0]) for r in runs)}, {median(r['last'][3] for r in runs):.2f}, "
                f"{median(r['last'][4] for r in runs):.2f} |"
            )


if __name__ == "__main__":
    main()
