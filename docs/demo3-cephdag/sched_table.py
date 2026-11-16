"""Markdown tables of the scheduler forecasts beside the measured walls.

    python3 -I sched_table.py FORECAST_JSON RESULTS_DIR LP_BOUND_JSONL

FORECAST_JSON is sched_forecast.py's output, RESULTS_DIR holds campaigns/demo3-skew and
campaigns/demo3-sweep (their ledgers and the rows of completed runs, as on aakashpc under
/mnt/mass/cephdag), LP_BOUND_JSONL is sched_lp_bound.py's output. Measured cells are the
median over repeats of cs rows: skew from demo3-skew, uniform from demo3-sweep.
"""

import json
from pathlib import Path
import statistics
import sys

CASES = ("skew-i1", "skew-i16", "skew-i128", "skew-i1024", "uniform-i256", "uniform-i1024")
POLICIES = (
    "heft-cc",
    "backlog",
    "lp-plan",
    "data-local",
    "greedy-wait",
    "stealing",
    "heft",
    "beam-4",
)
SHAPES = {"demo3-skew": "skew", "demo3-sweep": "uniform"}


def measured(results):
    """{case/policy: (median wall seconds, median moved bytes, repeats)} of the cs rows."""
    rows = {}
    for campaign, shape in SHAPES.items():
        directory = Path(results) / "campaigns" / campaign
        for line in (directory / "ledger.jsonl").read_text().splitlines():
            event = json.loads(line)
            if event["event"] != "completed" or not event["step"].endswith("-cs"):
                continue
            output = Path(results) / Path(event["output"]).relative_to("/results")
            for row_line in (output / "rows.jsonl").read_text().splitlines():
                row = json.loads(row_line)
                key = f"{shape}-i{row['iterations']}/{row['policy']}"
                rows.setdefault(key, []).append(row)
    return {
        key: (
            statistics.median(row["wall_seconds"] for row in group),
            statistics.median(row["network_payload_bytes"]["bytes"] for row in group),
            len(group),
        )
        for key, group in rows.items()
    }


def table(forecasts, walls, sharing):
    lines = [
        "| policy | " + " | ".join(CASES) + " |",
        "|---|" + "---|" * len(CASES),
    ]
    for policy in POLICIES:
        cells = []
        for case in CASES:
            forecast = forecasts.get(f"{sharing}/{case}/{policy}")
            cell = "-"
            if forecast:
                cell = f"{forecast['wall_seconds']:.1f} s, {forecast['moved_bytes'] / 1e9:.2f} GB"
            wall = walls.get(f"{case}/{policy}")
            if wall:
                cell += f" ({wall[0]:.1f} s, {wall[1] / 1e9:.2f} GB)"
            cells.append(cell)
        lines.append(f"| {policy} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    forecasts = json.loads(Path(sys.argv[1]).read_text())
    walls = measured(sys.argv[2])
    bounds = [json.loads(line) for line in Path(sys.argv[3]).read_text().splitlines()]
    for sharing in ("fair", "equal"):
        print(f"\n{sharing} sharing: forecast wall, bytes moved (measured median wall, bytes)\n")
        print(table(forecasts, walls, sharing))
    print("\n| case | LP bound | moved | off node 0 | build | simplex solve | HiGHS solve |")
    print("|---|---|---|---|---|---|---|")
    for bound in bounds:
        print(
            f"| {bound['case']} | {bound['makespan_seconds']:.1f} s | {bound['moved_gb']:.2f} GB"
            f" | {bound['moved_off_node0_tasks']} | {bound['build_ms']:.0f} ms"
            f" | {bound['simplex_solve_ms']:.0f} ms | {bound['highs_solve_ms']:.1f} ms |"
        )


if __name__ == "__main__":
    main()
