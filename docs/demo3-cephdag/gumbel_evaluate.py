"""Run the in-process evaluation of the Gumbel arc and print its table.

    cd <cephdag gumbel checkout> && .venv/bin/python -I ../../demo3/gumbel_evaluate.py MODELS OUT

Cases and policies as in demo3/GUMBEL.md; searches with seeds 1-3, deterministic policies once;
each run with planning charged to the wall (the lab's case) and free (the plan alone). Runs one at
a time so that the measured planning time is not inflated by other runs.
"""

import json
from pathlib import Path
import statistics
import subprocess
import sys

CASES = ("skew-i1", "skew-i16", "skew-i128", "skew-i1024", "uniform-i256", "uniform-i1024")
FIXED = ("data-local", "heft-cc", "beam-4", "beam-fair-4")
SEARCHES = (
    "native-mcts-heuristic-128",
    "native-mcts-fair-128",
    "gumbel-lb-16",
    "gumbel-lb-32",
    "gumbel-lb-64",
    "restarts-16",
    "restarts-32",
    "restarts-64",
)
SEEDS = (1, 2, 3)
EVAL = Path(__file__).resolve().parent / "gumbel_eval.py"


def evaluate(models, output, policies, label, options, charged):
    command = [sys.executable, "-I", str(EVAL), models, output, "--cases", ",".join(CASES)]
    command += ["--policies", ",".join(policies), "--label", label]
    command += ["--options", json.dumps(options)]
    if charged:
        command.append("--charged")
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)


def table(rows, charged):
    lines = [
        "| case | policy | wall s (seeds) | planning s | GB moved | moved off node 0 | searches |"
        " transitions | cut by 40 ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for case in CASES:
        for policy in FIXED + SEARCHES:
            mine = [r for r in rows if r["case"] == case and r["policy"] == policy]
            mine = [r for r in mine if r["charged"] == charged]
            if not mine:
                continue
            walls = [r["wall_seconds"] for r in mine]
            spread = "" if len(walls) == 1 else f" ({min(walls):.1f}-{max(walls):.1f})"
            mean = statistics.mean
            lines.append(
                f"| {case} | {policy} | {mean(walls):.2f}{spread} "
                f"| {mean(r['planning_seconds'] for r in mine):.2f} "
                f"| {mean(r['moved_bytes'] for r in mine) / 1e9:.2f} "
                f"| {mean(r['moved_off_node0'] for r in mine):.0f} "
                f"| {mean(r['searches'] for r in mine):.0f} "
                f"| {mean(r['simulated_transitions'] for r in mine):.0f} "
                f"| {mean(r['truncated_searches'] for r in mine):.0f} |"
            )
    return "\n".join(lines)


def main():
    models, output = sys.argv[1], sys.argv[2]
    for charged in (True, False):
        evaluate(models, output, FIXED, "once", {}, charged)
        for seed in SEEDS:
            evaluate(models, output, SEARCHES, f"seed{seed}", {"seed": seed}, charged)
    rows = [json.loads(line) for line in Path(output).read_text().splitlines()]
    for charged in (True, False):
        print("planning charged" if charged else "planning free")
        print(table(rows, charged))


if __name__ == "__main__":
    main()
