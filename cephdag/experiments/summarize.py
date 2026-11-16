"""Median and range of each metric per case and policy, from the raw run files.

python -m experiments.summarize results/independent-small results/competing
"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

METRICS = ("wall_seconds", "scheduler_seconds", "network_tx_bytes")


def case_name(result):
    case = result["case"]
    if case["shape"] == "droid":
        return f"droid-{case['resolution']}-wave-{case['wave']:04d}"
    return f"{case['shape']}-{case['iterations']}-{case['ratio']}"


def summarize(results):
    """{(case, policy): {metric: {"median", "min", "max"}, "runs": n}}"""
    groups = defaultdict(list)
    for result in results:
        groups[case_name(result), result["scheduler"]].append(result)
    summary = {}
    for key, group in sorted(groups.items()):
        row = {"runs": len(group)}
        for metric in METRICS:
            values = [result[metric] for result in group]
            row[metric] = {
                "median": statistics.median(values),
                "min": min(values),
                "max": max(values),
            }
        summary[key] = row
    return summary


def table(summary):
    lines = [
        "| case | policy | runs | wall s median (min-max) | planning s | network GiB |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for (case, policy), row in summary.items():
        wall = row["wall_seconds"]
        lines.append(
            f"| {case} | {policy} | {row['runs']} "
            f"| {wall['median']:.2f} ({wall['min']:.2f}-{wall['max']:.2f}) "
            f"| {row['scheduler_seconds']['median']:.2f} "
            f"| {row['network_tx_bytes']['median'] / 2**30:.3f} |"
        )
    return "\n".join(lines)


def load(directories):
    return [
        json.loads(path.read_text())
        for directory in directories
        for path in sorted(directory.glob("run-*.json"))
    ]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("directories", nargs="+", type=Path)
    args = parser.parse_args()
    print(table(summarize(load(args.directories))))


if __name__ == "__main__":
    main()
