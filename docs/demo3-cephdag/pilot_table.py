"""Tables of a finished (or partial) campaign from its ledger and rows, on the lab host.

    python3 pilot_table.py RESULTS_HOST CAMPAIGN

Prints, as markdown: every row (case, mode, policy, wall s, policy s, network payload GB,
verified, tasks per worker), then per case the best wall time of each mode and the speedup
of computational storage (cs best over disagg best), then the ledger's step durations.
"""

from collections import Counter
import json
from pathlib import Path
import sys


def rows_of(results, campaign):
    directory = Path(results) / "campaigns" / campaign
    events = [json.loads(line) for line in (directory / "ledger.jsonl").read_text().splitlines()]
    completed = [event for event in events if event["event"] == "completed"]
    rows = []
    for event in completed:
        if "mode" not in event:
            continue
        output = Path(results) / Path(event["output"]).relative_to("/results")
        for line in (output / "rows.jsonl").read_text().splitlines():
            row = json.loads(line)
            row["case"] = event["case"]
            row["workers"] = workers_used(output, row["policy"])
            rows.append(row)
    return rows, events


def workers_used(output, policy):
    """Tasks run per worker node, from the run's own record."""
    (path,) = output.glob(f"**/run-*-{policy}.json")
    run = json.loads(path.read_text())
    return dict(sorted(Counter(task["worker"] for task in run["tasks"].values()).items()))


def main():
    results, campaign = sys.argv[1:]
    rows, events = rows_of(results, campaign)
    print(
        "| case | mode | policy | wall s | policy s | network payload GB | verified | tasks per worker |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for row in rows:
        payload = row["network_payload_bytes"]["bytes"] / 1e9
        counts = " ".join(str(count) for count in row["workers"].values())
        print(
            f"| {row['case']} | {row['mode']} | {row['policy']} | {row['wall_seconds']:.2f} "
            f"| {row['policy_seconds']:.3f} | {payload:.3f} | {row['verification']['passed']} "
            f"| {counts} |"
        )
    print()
    print("| case | cs best | s | disagg best | s | speedup cs/disagg |")
    print("|---|---|---|---|---|---|")
    summary = []
    for case in dict.fromkeys(row["case"] for row in rows):
        best = {}
        for mode in ("cs", "disagg"):
            candidates = [row for row in rows if row["case"] == case and row["mode"] == mode]
            if candidates:
                best[mode] = min(candidates, key=lambda row: row["wall_seconds"])
        if len(best) < 2:
            continue
        cs, disagg = best["cs"], best["disagg"]
        speedup = disagg["wall_seconds"] / cs["wall_seconds"]
        summary.append(
            {"case": case, "cs": cs["policy"], "disagg": disagg["policy"], "speedup": speedup}
        )
        print(
            f"| {case} | {cs['policy']} | {cs['wall_seconds']:.2f} | {disagg['policy']} "
            f"| {disagg['wall_seconds']:.2f} | {speedup:.2f} |"
        )
    print()
    print("| step | attempt | seconds |")
    print("|---|---|---|")
    for event in events:
        if event["event"] == "completed":
            print(f"| {event['step']} | {event['attempt']} | {event['seconds']:.0f} |")
        elif event["event"] == "failed":
            print(f"| {event['step']} | FAILED | {event['reason'][:200]} |")
    started = [event["time"] for event in events if event["event"] == "started"]
    finished = [event["time"] for event in events if event["event"] in ("completed", "failed")]
    if started and finished:
        print(f"\nfirst start to last end: {(max(finished) - min(started)) / 60:.1f} min")


if __name__ == "__main__":
    main()
