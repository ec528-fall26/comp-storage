"""Regenerate every CSV, chart and markdown table of the Demo 3 data pack.

Usage: python make_figures.py ROWS_DIR [OUT_DIR]

ROWS_DIR holds one directory per campaign, as copied from aakashpc with
    ssh aakashpc "sh -c 'cd /mnt/mass/cephdag/campaigns && tar cf - CAMPAIGN/*/a*/rows.jsonl'" | tar xf -
OUT_DIR defaults to this script's directory. Forecasts are read from ../sched-forecast and
../gumbel-eval next to this script.
"""

import csv
import glob
import json
import math
import os
import re
import statistics
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO3 = os.path.dirname(HERE)

CS_COLOUR = "#2F6EBA"
CS_LIGHT = "#9DBCE3"
BASE_COLOUR = "#D9822B"
MCTS_COLOUR = "#A23B72"
MCTS_LIGHT = "#D9A0C0"
GREY = "#7F7F7F"
FIGSIZE = (16, 9)
DPI = 100

DROID_CS_NODES = 8
DROID_BASELINE_NODES = 16
LP_PLAN2_FORECAST = {128: 39.2, 1024: 91.6}
NETSWEEP_PREDICTION_I1 = {400: 5.6, 1000: 2.5, 2500: 1.4}

SKEW_POLICIES = [
    "heft-cc",
    "backlog",
    "lp-plan",
    "greedy-wait",
    "stealing",
    "data-local",
    "beam-4",
    "heft",
]
POLICY_COLOURS = {
    "heft-cc": CS_COLOUR,
    "backlog": "#70AD47",
    "lp-plan": "#A5A5A5",
    "greedy-wait": "#C9C9C9",
    "stealing": "#5B5B5B",
    "data-local": CS_LIGHT,
    "beam-4": "#8064A2",
    "heft": "#C0504D",
    "lp-plan2": "#1B7837",
}
SCHED_DROID_POLICIES = [
    ("cs", "data-local"),
    ("cs", "heft-cc"),
    ("cs", "lp-plan2"),
    ("cs", "heft"),
    ("disagg", "fifo-hash"),
]
LP2_OTHER_SESSION = "lp-plan2 (other session)"


def pick_font():
    for name in ["Calibri", "Carlito", "DejaVu Sans"]:
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except ValueError:
            continue
    return "sans-serif"


def style():
    plt.rcParams["font.family"] = pick_font()
    plt.rcParams["font.size"] = 22
    plt.rcParams["axes.labelsize"] = 24
    plt.rcParams["xtick.labelsize"] = 20
    plt.rcParams["ytick.labelsize"] = 20
    plt.rcParams["legend.fontsize"] = 20
    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False
    plt.rcParams["lines.linewidth"] = 3.5
    plt.rcParams["lines.markersize"] = 12


# ---------------------------------------------------------------- loading


def parse_row(raw, campaign, step, directory):
    repeat = re.search(r"-r(\d+)-", step)
    batch = re.match(r"wave(\d+)", step)
    return {
        "campaign": campaign,
        "step": step,
        "directory": directory,
        "dataset": raw.get("dataset", ""),
        "repeat": int(repeat.group(1)) if repeat else 0,
        "batch": int(batch.group(1)) if batch else None,
        "mode": raw["mode"],
        "policy": raw["policy"],
        "iterations": raw["iterations"],
        "fused": bool(raw.get("fused")),
        "wall": raw["wall_seconds"],
        "plan": raw["policy_seconds"],
        "moved_gb": raw["network_payload_bytes"]["bytes"] / 1e9,
        "tasks": raw["tasks"],
        "source_gb": raw["dataset_bytes"] / 1e9,
        "passed": raw["verification"]["passed"],
        "digest": raw["verification"]["outputs_sha256"],
    }


def load_campaign(rows_dir, campaign):
    latest_attempt = {}
    for path in glob.glob(os.path.join(rows_dir, campaign, "*", "a*", "rows.jsonl")):
        step = path.split(os.sep)[-3]
        attempt = int(path.split(os.sep)[-2][1:])
        if attempt > latest_attempt.get(step, (0, None))[0]:
            latest_attempt[step] = (attempt, path)
    rows = []
    for step, (attempt, path) in sorted(latest_attempt.items()):
        with open(path) as handle:
            for line in handle:
                if line.strip():
                    rows.append(parse_row(json.loads(line), campaign, step, os.path.dirname(path)))
    return rows


def select(rows, **conditions):
    return [r for r in rows if all(r[key] == value for key, value in conditions.items())]


def check(rows, campaign):
    failed = [r for r in rows if not r["passed"]]
    cells = {}
    for r in rows:
        cell = (r["iterations"], r["batch"], r["fused"])
        cells.setdefault(cell, set()).add(r["digest"])
    split = [cell for cell, digests in cells.items() if len(digests) > 1]
    return {
        "campaign": campaign,
        "rows": len(rows),
        "failed_verification": len(failed),
        "cells": len(cells),
        "cells_with_more_than_one_digest": len(split),
    }


def run_record(row):
    """The per-run record of a row and the config.json beside it, or (None, None) if the rows
    directory holds only rows.jsonl."""
    pattern = os.path.join(row["directory"], "**", f"run-*-{row['policy']}.json")
    paths = glob.glob(pattern, recursive=True)
    if len(paths) != 1:
        return None, None
    with open(paths[0]) as handle:
        run = json.load(handle)
    with open(os.path.join(os.path.dirname(paths[0]), "config.json")) as handle:
        config = json.load(handle)
    return run, config


def node0_moves(row):
    """Tasks of a cs run whose input is stored on node 0 but ran elsewhere (task i reads source
    shard or partition i in the independent and fused DROID graphs), or None without a record."""
    unfused_droid = row["batch"] is not None and not row["fused"]
    if row["mode"] != "cs" or unfused_droid:
        return None
    run, config = run_record(row)
    if run is None:
        return None
    shards = config["dataset"]["shards"]
    moved = 0
    for task_id, record in run["tasks"].items():
        if shards[int(task_id[1:])]["pool"] == "node0" and record["worker"] != 0:
            moved += 1
    return moved


def node0_share(config):
    shards = config["dataset"]["shards"]
    return sum(s["size"] for s in shards if s["pool"] == "node0") / sum(s["size"] for s in shards)


# ---------------------------------------------------------------- arithmetic


def median(values):
    return statistics.median(values)


def best_wall_per_repeat(rows):
    best = {}
    for r in rows:
        best[r["repeat"]] = min(best.get(r["repeat"], math.inf), r["wall"])
    return best


def paired_speedups(base_rows, cs_rows):
    base = best_wall_per_repeat(base_rows)
    cs = best_wall_per_repeat(cs_rows)
    return [base[k] / cs[k] for k in sorted(base) if k in cs]


def one(value, digits=1):
    return round(value, digits)


def joined(values, digits=2):
    return ", ".join(f"{v:.{digits}f}" for v in values)


# ---------------------------------------------------------------- output


def write_csv(out_dir, name, table):
    path = os.path.join(out_dir, name + ".csv")
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(table[0].keys()))
        writer.writeheader()
        writer.writerows(table)
    return path


def markdown(table):
    header = list(table[0].keys())
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in table:
        lines.append("| " + " | ".join(str(row[key]) for key in header) + " |")
    return "\n".join(lines)


def save(fig, out_dir, name):
    path = os.path.join(out_dir, name + ".png")
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return path


def new_figure():
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    return fig, ax


def log2_axis(ax, ticks):
    ax.set_xscale("log", base=2)
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(t) for t in ticks])
    ax.minorticks_off()


# ---------------------------------------------------------------- 1. intensity sweep


def intensity_table(rows, columns, comparisons):
    """columns: (mode, policy) medians; comparisons: (label, base_mode, cs_mode, cs_policy or None)."""
    table = []
    for iterations in sorted({r["iterations"] for r in rows}):
        at = select(rows, iterations=iterations)
        row = {"iterations": iterations}
        for mode, policy in columns:
            cell = select(at, mode=mode, policy=policy)
            row[f"{mode} {policy} s"] = one(median([r["wall"] for r in cell])) if cell else ""
        for label, base_mode, cs_mode, cs_policy in comparisons:
            cs_rows = select(at, mode=cs_mode)
            if cs_policy:
                cs_rows = select(cs_rows, policy=cs_policy)
            speedups = paired_speedups(select(at, mode=base_mode), cs_rows)
            row[f"{label} per repeat"] = joined(speedups)
            row[f"{label} median"] = one(median(speedups), 2)
        for mode in sorted({m for m, _ in columns}):
            moved = [r["moved_gb"] for r in select(at, mode=mode)]
            row[f"{mode} GB moved"] = one(median(moved), 2)
        row["runs"] = len(at)
        table.append(row)
    return table


def figure_speedup_vs_intensity(slots, sweep, out_dir):
    fig, ax = new_figure()
    xs = [r["iterations"] for r in sweep]
    ys = [r["cs over disagg median"] for r in sweep]
    ax.plot(xs, ys, "o--", color=GREY, label="1 worker per node (Nov 13)")
    xs = [r["iterations"] for r in slots]
    ys = [r["cs2 over disagg2 median"] for r in slots]
    ax.plot(xs, ys, "o-", color=CS_COLOUR, label="2 workers per node, slot-matched (Nov 14)")
    ax.annotate(
        f"{ys[0]:.1f}x", (xs[0], ys[0]), textcoords="offset points", xytext=(18, 6), color=CS_COLOUR
    )
    ax.annotate(
        f"{ys[-1]:.2f}x",
        (xs[-1], ys[-1]),
        textcoords="offset points",
        xytext=(-30, 22),
        color=CS_COLOUR,
    )
    ax.axhline(1.0, color="black", linewidth=1.5)
    log2_axis(ax, [1, 4, 16, 64, 256, 1024, 4096])
    ax.set_ylim(0, 7.2)
    ax.set_xlabel("Arithmetic intensity (kernel iterations per element)")
    ax.set_ylabel("Speedup over disaggregated (x)")
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    return save(fig, out_dir, "fig1_speedup_vs_intensity")


def figure_slot_matching(slots, overlap, out_dir):
    fig, ax = new_figure()
    xs = [r["iterations"] for r in overlap]
    ys = [r["cs over disagg2 median"] for r in overlap]
    ax.plot(xs, ys, "o--", color=BASE_COLOUR, label="8 cs workers vs 16 baseline workers")
    xs = [r["iterations"] for r in slots]
    ys = [r["cs2 over disagg2 median"] for r in slots]
    ax.plot(xs, ys, "o-", color=CS_COLOUR, label="16 vs 16 workers (slot-matched)")
    ax.axhline(1.0, color="black", linewidth=1.5)
    log2_axis(ax, [1, 4, 16, 64, 256, 1024, 4096])
    ax.set_ylim(0, 6.2)
    ax.set_xlabel("Arithmetic intensity (kernel iterations per element)")
    ax.set_ylabel("Speedup over disaggregated (x)")
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    return save(fig, out_dir, "fig1b_slot_matching")


# ---------------------------------------------------------------- 2. DROID


def droid_table(rows):
    table = []
    for batch in sorted({r["batch"] for r in rows}):
        local = select(rows, batch=batch, mode="cs", policy="data-local")[0]
        beam = select(rows, batch=batch, mode="cs", policy="beam-4")[0]
        heft = select(rows, batch=batch, mode="disagg", policy="heft")[0]
        table.append(
            {
                "batch": batch,
                "source GB": one(local["source_gb"]),
                "tasks": local["tasks"],
                "cs data-local s": one(local["wall"]),
                "cs beam-4 s": one(beam["wall"]),
                "beam-4 planning s": one(beam["plan"]),
                "disagg heft s": one(heft["wall"]),
                "speedup": one(heft["wall"] / local["wall"], 2),
                "data-local GB moved": one(local["moved_gb"], 2),
                "beam-4 GB moved": one(beam["moved_gb"], 2),
                "heft GB moved": one(heft["moved_gb"], 1),
                "data-local s per source GB": one(local["wall"] / local["source_gb"], 2),
            }
        )
    return table


def droid_totals(rows):
    local = select(rows, mode="cs", policy="data-local")
    beam = select(rows, mode="cs", policy="beam-4")
    heft = select(rows, mode="disagg", policy="heft")
    local_s = sum(r["wall"] for r in local)
    heft_s = sum(r["wall"] for r in heft)
    first = min(local, key=lambda r: r["batch"])
    first_rate = first["wall"] / first["source_gb"]
    at_first_rate_s = sum(first_rate * r["source_gb"] for r in local)
    return {
        "batches": len(local),
        "source GB": one(sum(r["source_gb"] for r in local)),
        "tasks": sum(r["tasks"] for r in local),
        "cs data-local s": one(local_s),
        "cs data-local h": one(local_s / 3600, 2),
        "cs beam-4 s": one(sum(r["wall"] for r in beam)),
        "beam-4 planning s": one(sum(r["plan"] for r in beam)),
        "disagg heft s": one(heft_s),
        "disagg heft h": one(heft_s / 3600, 2),
        "speedup": one(heft_s / local_s, 2),
        "data-local GB moved": one(sum(r["moved_gb"] for r in local), 1),
        "beam-4 GB moved": one(sum(r["moved_gb"] for r in beam), 1),
        "heft GB moved": one(sum(r["moved_gb"] for r in heft), 0),
        "cs node-hours": one(DROID_CS_NODES * local_s / 3600, 1),
        "baseline node-hours": one(DROID_BASELINE_NODES * heft_s / 3600, 1),
        "node-hour ratio": one((DROID_BASELINE_NODES * heft_s) / (DROID_CS_NODES * local_s), 2),
        "speedup at batch 0 s per GB (estimate)": one(heft_s / at_first_rate_s, 2),
    }


def figure_droid_batches(table, out_dir):
    fig, ax = new_figure()
    batches = [r["batch"] for r in table]
    cs_min = [r["cs data-local s"] / 60 for r in table]
    base_min = [r["disagg heft s"] / 60 for r in table]
    width = 0.38
    ax.bar(
        [b - width / 2 for b in batches],
        cs_min,
        width,
        color=CS_COLOUR,
        label="Computational storage",
    )
    ax.bar(
        [b + width / 2 for b in batches], base_min, width, color=BASE_COLOUR, label="Disaggregated"
    )
    for row in table:
        ax.text(
            row["batch"] + width / 2,
            row["disagg heft s"] / 60 + 1,
            f"{row['speedup']:.1f}x",
            ha="center",
            va="bottom",
            fontsize=20,
        )
    ax.set_xticks(batches)
    ax.set_xlabel("DROID batch (about 70 GB of source each)")
    ax.set_ylabel("Wall time (min)")
    ax.set_ylim(0, max(base_min) * 1.15)
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0.0, 1.0), ncol=2)
    fig.tight_layout()
    return save(fig, out_dir, "fig2_droid_batches")


def droid_fused_table(slots_droid, sensitivity_droid):
    table = []
    for fused in [False, True]:
        cs = select(slots_droid, mode="cs", fused=fused)[0]
        cs2 = select(slots_droid, mode="cs2", fused=fused)[0]
        disagg2 = select(slots_droid, mode="disagg2", fused=fused)[0]
        old_cs = select(sensitivity_droid, mode="cs", fused=fused)[0]
        old_heft = select(sensitivity_droid, mode="disagg", policy="heft", fused=fused)[0]
        old_disagg2 = select(sensitivity_droid, mode="disagg2", fused=fused)[0]
        table.append(
            {
                "pipeline": "fused" if fused else "unfused",
                "tasks": cs2["tasks"],
                "cs s": one(cs["wall"]),
                "cs2 s": one(cs2["wall"]),
                "disagg2 s": one(disagg2["wall"]),
                "disagg2 / cs2": one(disagg2["wall"] / cs2["wall"], 2),
                "cs2 GB moved": one(cs2["moved_gb"], 2),
                "disagg2 GB moved": one(disagg2["moved_gb"], 2),
                "stage A cs s": one(old_cs["wall"]),
                "stage A disagg heft s": one(old_heft["wall"]),
                "stage A disagg2 s": one(old_disagg2["wall"]),
                "stage A disagg heft / cs": one(old_heft["wall"] / old_cs["wall"], 2),
                "stage A disagg2 / cs": one(old_disagg2["wall"] / old_cs["wall"], 2),
            }
        )
    return table


def figure_droid_fused(table, out_dir):
    fig, ax = new_figure()
    width = 0.36
    xs = [0, 1]
    cs = [r["cs2 s"] for r in table]
    base = [r["disagg2 s"] for r in table]
    ax.bar([x - width / 2 for x in xs], cs, width, color=CS_COLOUR, label="Computational storage")
    ax.bar([x + width / 2 for x in xs], base, width, color=BASE_COLOUR, label="Disaggregated")
    for x, row in zip(xs, table):
        ax.text(
            x + width / 2,
            row["disagg2 s"] + 1.5,
            f"{row['disagg2 / cs2']:.1f}x",
            ha="center",
            va="bottom",
        )
    ax.set_xticks(xs)
    ax.set_xticklabels(["Unfused (frames stored)", "Fused (frames kept in memory)"])
    ax.set_ylabel("Wall time (s)")
    ax.set_ylim(0, max(base) * 1.2)
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    return save(fig, out_dir, "fig2b_droid_fused")


# ---------------------------------------------------------------- 3. scale


def scale_table(rows):
    table = []
    for iterations in sorted({r["iterations"] for r in rows}):
        at = select(rows, iterations=iterations)
        local = select(at, mode="cs", policy="data-local")[0]
        heft = select(at, mode="cs", policy="heft")[0]
        base = select(at, mode="disagg", policy="fifo-hash")[0]
        best_cs = min(local["wall"], heft["wall"])
        table.append(
            {
                "iterations": iterations,
                "source GB": one(local["source_gb"]),
                "tasks": local["tasks"],
                "cs data-local s": one(local["wall"]),
                "cs heft s": one(heft["wall"]),
                "disagg fifo-hash s": one(base["wall"]),
                "speedup best cs": one(base["wall"] / best_cs, 2),
                "speedup data-local": one(base["wall"] / local["wall"], 2),
                "cs data-local GB moved": one(local["moved_gb"], 1),
                "cs heft GB moved": one(heft["moved_gb"], 1),
                "disagg GB moved": one(base["moved_gb"], 1),
            }
        )
    return table


def figure_scale(table, out_dir):
    fig, ax = new_figure()
    width = 0.27
    xs = list(range(len(table)))
    series = [
        (
            "cs data-local s",
            "cs data-local GB moved",
            CS_COLOUR,
            "Computational storage, data-local",
        ),
        ("cs heft s", "cs heft GB moved", CS_LIGHT, "Computational storage, plain HEFT"),
        ("disagg fifo-hash s", "disagg GB moved", BASE_COLOUR, "Disaggregated"),
    ]
    for offset, (wall_key, moved_key, colour, label) in zip([-width, 0, width], series):
        minutes = [r[wall_key] / 60 for r in table]
        ax.bar([x + offset for x in xs], minutes, width, color=colour, label=label)
        for x, row in zip(xs, table):
            ax.text(
                x + offset,
                row[wall_key] / 60 + 0.4,
                f"{row[moved_key]:.0f} GB",
                ha="center",
                va="bottom",
                fontsize=18,
            )
    ax.set_xticks(xs)
    ax.set_xticklabels(
        [f"{r['iterations']} iteration" + ("s" if r["iterations"] > 1 else "") for r in table]
    )
    ax.set_xlabel("Arithmetic intensity (bar labels: data moved over the network)")
    ax.set_ylabel("Wall time (min)")
    ax.set_ylim(0, max(r["disagg fifo-hash s"] for r in table) / 60 * 1.2)
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    return save(fig, out_dir, "fig3_scale_256gib")


# ---------------------------------------------------------------- 4. schedulers


def policy_cells(rows, policies):
    table = []
    for policy in policies:
        for iterations in sorted({r["iterations"] for r in rows}):
            cell = select(rows, policy=policy, iterations=iterations)
            if not cell:
                continue
            walls = [r["wall"] for r in cell]
            table.append(
                {
                    "policy": policy,
                    "iterations": iterations,
                    "median s": one(median(walls)),
                    "min s": one(min(walls)),
                    "max s": one(max(walls)),
                    "GB moved": one(median([r["moved_gb"] for r in cell]), 2),
                    "planning s": one(median([r["plan"] for r in cell]), 2),
                    "runs": len(cell),
                }
            )
    return table


def pivot(cells, policies, with_range=True, best_of=None):
    """Policies by intensity; the best median per column in bold, among `best_of` (the cells of
    the table's own campaign) if given."""
    table = []
    intensities = sorted({c["iterations"] for c in cells})
    best_of = best_of or cells
    best = {i: min(c["median s"] for c in best_of if c["iterations"] == i) for i in intensities}
    for policy in policies:
        row = {"policy": policy}
        for i in intensities:
            match = [c for c in cells if c["policy"] == policy and c["iterations"] == i]
            if not match:
                row[f"i{i}"] = ""
                continue
            c = match[0]
            text = f"{c['median s']}"
            if with_range:
                text += f" [{c['min s']}-{c['max s']}]"
            text += f", {c['GB moved']} GB"
            if c["median s"] == best[i]:
                text = "**" + text + "**"
            row[f"i{i}"] = text
        table.append(row)
    return table


def figure_skew(cells, out_dir):
    fig, ax = new_figure()
    intensities = sorted({c["iterations"] for c in cells})
    best = {i: min(c["median s"] for c in cells if c["iterations"] == i) for i in intensities}
    width = 0.105
    for k, policy in enumerate(SKEW_POLICIES):
        xs = []
        ys = []
        for g, i in enumerate(intensities):
            match = [c for c in cells if c["policy"] == policy and c["iterations"] == i][0]
            xs.append(g + (k - 3.5) * width)
            ys.append(match["median s"] / best[i])
        ax.bar(xs, ys, width, color=POLICY_COLOURS[policy], label=policy)
    ax.axhline(1.0, color="black", linewidth=1.5)
    ax.set_xticks(range(len(intensities)))
    ax.set_xticklabels([f"{i} iteration" + ("s" if i > 1 else "") for i in intensities])
    ax.set_xlabel("Arithmetic intensity, skewed data (half the shards on one node)")
    ax.set_ylabel("Wall time / best policy (x)")
    ax.set_ylim(0.8, 2.75)
    ax.legend(frameon=False, ncol=4, loc="upper right")
    fig.tight_layout()
    return save(fig, out_dir, "fig4_skew_policies")


def lp_table(rows, bounds):
    table = []
    for iterations in sorted({r["iterations"] for r in rows}):
        for policy in ["lp-plan", "lp-plan2", "heft-cc"]:
            cell = select(rows, iterations=iterations, policy=policy)
            cell.sort(key=lambda r: r["repeat"])
            table.append(
                {
                    "iterations": iterations,
                    "policy": policy,
                    "median s": one(median([r["wall"] for r in cell])),
                    "repeats s": joined([r["wall"] for r in cell], 1),
                    "GB moved": one(median([r["moved_gb"] for r in cell]), 2),
                    "planning s": one(median([r["plan"] for r in cell]), 3),
                    "LP bound s": one(bounds[f"skew-i{iterations}"]),
                }
            )
    return table


def figure_lp(table, out_dir):
    fig, ax = new_figure()
    intensities = sorted({r["iterations"] for r in table})
    policies = [
        ("lp-plan", "#A5A5A5", "LP plan, old dispatcher"),
        ("lp-plan2", "#70AD47", "LP plan, fixed dispatcher"),
        ("heft-cc", CS_COLOUR, "heft-cc"),
    ]
    width = 0.26
    for k, (policy, colour, label) in enumerate(policies):
        xs = []
        ys = []
        for g, i in enumerate(intensities):
            row = [r for r in table if r["iterations"] == i and r["policy"] == policy][0]
            xs.append(g + (k - 1) * width)
            ys.append(row["median s"])
        ax.bar(xs, ys, width, color=colour, label=label)
    for g, i in enumerate(intensities):
        bound = [r for r in table if r["iterations"] == i][0]["LP bound s"]
        ax.hlines(
            bound,
            g - 1.6 * width,
            g + 1.6 * width,
            colors="black",
            linestyles="dashed",
            linewidth=2.5,
        )
        ax.text(g + 1.6 * width, bound, " LP bound (model)", va="center", fontsize=18)
    ax.set_xticks(range(len(intensities)))
    ax.set_xticklabels([f"{i} iterations" for i in intensities])
    ax.set_ylabel("Wall time (s)")
    ax.set_xlim(-0.6, len(intensities) - 0.1)
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    return save(fig, out_dir, "fig4b_lp_plan2")


def paired_policy_table(rows, policies, reference):
    """Per intensity and policy: median [min-max], each repeat over `reference` in the same
    repeat, GB moved, tasks moved off node 0 (median over repeats) and planning seconds."""
    table = []
    for iterations in sorted({r["iterations"] for r in rows}):
        at = select(rows, iterations=iterations)
        reference_walls = {r["repeat"]: r["wall"] for r in select(at, policy=reference)}
        for policy in policies:
            cell = sorted(select(at, policy=policy), key=lambda r: r["repeat"])
            if not cell:
                continue
            walls = [r["wall"] for r in cell]
            ratios = [
                r["wall"] / reference_walls[r["repeat"]]
                for r in cell
                if r["repeat"] in reference_walls
            ]
            moves = [node0_moves(r) for r in cell]
            table.append(
                {
                    "iterations": iterations,
                    "policy": policy,
                    "median s": one(median(walls)),
                    "min s": one(min(walls)),
                    "max s": one(max(walls)),
                    "repeats s": joined(walls, 1),
                    f"over {reference} per repeat": joined(ratios, 3),
                    f"over {reference} median": one(median(ratios), 3) if ratios else "",
                    "GB moved": one(median([r["moved_gb"] for r in cell]), 2),
                    "tasks moved off node 0": median(moves) if None not in moves else "",
                    "planning s": one(median([r["plan"] for r in cell]), 3),
                    "runs": len(cell),
                }
            )
    return table


# ---------------------------------------------------------------- 7. schedulers on DROID


def sched_droid_table(rows):
    """Per (mode, policy): wall per wave, each over data-local in the same wave, bytes moved,
    tasks moved off node 0 per wave and planning seconds."""
    waves = sorted({r["batch"] for r in rows})
    local = {w: select(rows, batch=w, mode="cs", policy="data-local")[0]["wall"] for w in waves}
    table = []
    for mode, policy in SCHED_DROID_POLICIES:
        cell = sorted(select(rows, mode=mode, policy=policy), key=lambda r: r["batch"])
        if not cell:
            continue
        moves = [node0_moves(r) for r in cell]
        table.append(
            {
                "mode": mode,
                "policy": policy,
                "median s": one(median([r["wall"] for r in cell])),
                "per wave s": joined([r["wall"] for r in cell], 1),
                "over data-local per wave": joined(
                    [r["wall"] / local[r["batch"]] for r in cell], 3
                ),
                "GB moved per wave": joined([r["moved_gb"] for r in cell]),
                "tasks moved off node 0 per wave": ", ".join(str(m) for m in moves)
                if None not in moves
                else "",
                "planning s per wave": joined([r["plan"] for r in cell]),
                "runs": len(cell),
            }
        )
    return table


def sched_droid_waves(rows):
    table = []
    for wave in sorted({r["batch"] for r in rows}):
        at = select(rows, batch=wave)
        best = min(select(at, mode="cs"), key=lambda r: r["wall"])
        base = select(at, mode="disagg")
        _, config = run_record(best)
        table.append(
            {
                "wave": wave,
                "dataset": best["dataset"],
                "source GB": one(best["source_gb"], 2),
                "tasks": best["tasks"],
                "node 0 share of source": one(node0_share(config), 3) if config else "",
                "best cs policy": best["policy"],
                "best cs s": one(best["wall"]),
                "disagg fifo-hash s": one(base[0]["wall"]) if base else "",
                "disagg over best cs": one(base[0]["wall"] / best["wall"], 2) if base else "",
                "runs": len(at),
                "failed verification": len([r for r in at if not r["passed"]]),
                "output digests": len({r["digest"] for r in at}),
            }
        )
    return table


def figure_sched_droid(rows, waves, out_dir):
    fig, ax = new_figure()
    width = 0.16
    for k, (mode, policy) in enumerate(SCHED_DROID_POLICIES):
        colour = BASE_COLOUR if mode == "disagg" else POLICY_COLOURS[policy]
        label = "disaggregated (fifo-hash)" if mode == "disagg" else policy
        xs = []
        ys = []
        plans = []
        for g, wave in enumerate(waves):
            cell = select(rows, batch=wave["wave"], mode=mode, policy=policy)
            if cell:
                xs.append(g + (k - 2) * width)
                ys.append(cell[0]["wall"])
                plans.append(cell[0]["plan"])
        ax.bar(xs, ys, width, color=colour, label=label)
        ax.bar(xs, plans, width, color="none", edgecolor="white", hatch="//", linewidth=0)
        for x, y in zip(xs, ys):
            ax.text(x, y + 2, f"{y:.0f}", ha="center", va="bottom", fontsize=18)
    ax.set_xticks(range(len(waves)))
    labels = []
    for wave in waves:
        first, last = re.findall(r"(\d{5})", wave["dataset"])
        label = f"Shards {int(first)}-{int(last)}, {wave['source GB']:.1f} GB"
        share = wave["node 0 share of source"]
        if share != "":
            label += f", {100 * share:.0f}% on node 0"
        labels.append(label)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Fused DROID pipeline, node 0 holding 9 of every 16 partitions")
    ax.set_ylabel("Wall time (s)")
    ax.set_ylim(0, max(r["wall"] for r in rows) * 1.25)
    handles, _ = ax.get_legend_handles_labels()
    handles.append(
        Patch(facecolor=GREY, edgecolor="white", hatch="//", label="planning (workers idle)")
    )
    ax.legend(handles=handles, frameon=False, ncol=3, loc="upper left")
    fig.tight_layout()
    return save(fig, out_dir, "fig7_sched_droid")


# ---------------------------------------------------------------- 5. search


def load_forecasts():
    with open(os.path.join(DEMO3, "sched-forecast", "forecast.json")) as handle:
        fair = {
            key[len("fair/") :]: value["wall_seconds"]
            for key, value in json.load(handle).items()
            if key.startswith("fair/")
        }
    simulated = []
    with open(os.path.join(DEMO3, "gumbel-eval", "evaluation.jsonl")) as handle:
        for line in handle:
            simulated.append(json.loads(line))
    for row in simulated:
        if (
            row["policy"] == "native-mcts-heuristic-128"
            and row["label"] == "seed1"
            and not row["charged"]
        ):
            fair[f"{row['case']}/native-mcts-heuristic-128"] = row["wall_seconds"]
    for iterations, wall in LP_PLAN2_FORECAST.items():
        fair[f"skew-i{iterations}/lp-plan2"] = wall
    bounds = {}
    with open(os.path.join(DEMO3, "sched-forecast", "lp_bound.jsonl")) as handle:
        for line in handle:
            row = json.loads(line)
            bounds[row["case"]] = row["makespan_seconds"]
    return fair, simulated, bounds


def mcts_table(rows, forecast):
    table = []
    for iterations in sorted({r["iterations"] for r in rows}):
        for policy in ["heft-cc", "data-local", "beam-4", "native-mcts-heuristic-128"]:
            cell = select(rows, iterations=iterations, policy=policy)
            cell.sort(key=lambda r: r["repeat"])
            predicted = forecast.get(f"skew-i{iterations}/{policy}")
            table.append(
                {
                    "iterations": iterations,
                    "policy": policy,
                    "median s": one(median([r["wall"] for r in cell])),
                    "repeats s": joined([r["wall"] for r in cell], 1),
                    "GB moved": one(median([r["moved_gb"] for r in cell]), 2),
                    "planning s": one(median([r["plan"] for r in cell]), 2),
                    "forecast s": one(predicted) if predicted else "",
                }
            )
    return table


def forecast_pairs(skew, uniform, lp, mcts, forecast):
    pairs = []
    sources = [
        ("sched-skew", "skew", skew, None),
        ("sched-uniform", "uniform", uniform, None),
        ("sched-lp", "skew", lp, ["lp-plan2"]),
        ("sched-mcts", "skew", mcts, ["native-mcts-heuristic-128"]),
    ]
    for campaign, case, rows, only in sources:
        for policy in sorted({r["policy"] for r in rows}):
            if only and policy not in only:
                continue
            for iterations in sorted({r["iterations"] for r in rows}):
                key = f"{case}-i{iterations}/{policy}"
                cell = select(rows, policy=policy, iterations=iterations)
                if key not in forecast or not cell:
                    continue
                measured = median([r["wall"] for r in cell])
                pairs.append(
                    {
                        "campaign": campaign,
                        "case": f"{case}-i{iterations}",
                        "policy": policy,
                        "forecast s": one(forecast[key]),
                        "measured s": one(measured),
                        "error %": one(100 * (forecast[key] - measured) / measured),
                        "runs": len(cell),
                    }
                )
    return pairs


def figure_forecast(pairs, out_dir):
    fig, ax = new_figure()
    groups = [
        (
            "List and LP schedulers",
            lambda p: p["policy"] not in ("native-mcts-heuristic-128", "beam-4"),
            CS_COLOUR,
            "o",
        ),
        ("Beam search", lambda p: p["policy"] == "beam-4", "#8064A2", "s"),
        ("MCTS", lambda p: p["policy"] == "native-mcts-heuristic-128", MCTS_COLOUR, "D"),
    ]
    for label, belongs, colour, marker in groups:
        chosen = [p for p in pairs if belongs(p)]
        ax.scatter(
            [p["forecast s"] for p in chosen],
            [p["measured s"] for p in chosen],
            s=160,
            color=colour,
            marker=marker,
            label=label,
            zorder=3,
        )
    limits = [15, 180]
    ax.plot(limits, limits, color="black", linewidth=1.5)
    ax.plot(limits, [v * 1.1 for v in limits], color=GREY, linewidth=1, linestyle="dashed")
    ax.plot(limits, [v / 1.1 for v in limits], color=GREY, linewidth=1, linestyle="dashed")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ticks = [20, 30, 50, 100, 150]
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(t) for t in ticks])
    ax.set_yticks(ticks)
    ax.set_yticklabels([str(t) for t in ticks])
    ax.minorticks_off()
    ax.set_xlim(limits)
    ax.set_ylim(limits)
    ax.set_xlabel("Forecast wall time, simulator (s)")
    ax.set_ylabel("Measured wall time, lab (s)")
    ax.legend(frameon=False, loc="upper left")
    ax.text(150, 150 / 1.1 / 1.12, "±10%", color=GREY, fontsize=18, ha="center")
    fig.tight_layout()
    return save(fig, out_dir, "fig5_forecast_vs_measured")


SEARCH_POLICIES = [
    "heft-cc",
    "data-local",
    "beam-4",
    "native-mcts-heuristic-128",
    "native-mcts-fair-128",
    "gumbel-lb-32",
    "restarts-32",
]
CASES = ["skew-i1", "skew-i16", "skew-i128", "skew-i1024", "uniform-i256", "uniform-i1024"]


def simulated_table(simulated):
    table = []
    charged = [r for r in simulated if r["charged"]]
    for policy in SEARCH_POLICIES:
        row = {"policy": policy}
        for case in CASES:
            walls = [
                r["wall_seconds"] for r in charged if r["policy"] == policy and r["case"] == case
            ]
            row[case] = one(statistics.mean(walls))
        table.append(row)
    heft_cc = table[0]
    for row in table:
        row["mean wall / heft-cc"] = one(statistics.mean(row[c] / heft_cc[c] for c in CASES), 3)
    return table


def figure_simulated(table, out_dir):
    fig, ax = new_figure()
    heft_cc = [r for r in table if r["policy"] == "heft-cc"][0]
    shown = [
        ("native-mcts-heuristic-128", MCTS_COLOUR, "MCTS-128 (old)"),
        ("native-mcts-fair-128", MCTS_LIGHT, "MCTS-128, fair model"),
        ("gumbel-lb-32", "#8064A2", "Gumbel MCTS-32"),
        ("restarts-32", "#A5A5A5", "Random restarts-32"),
    ]
    width = 0.2
    for k, (policy, colour, label) in enumerate(shown):
        row = [r for r in table if r["policy"] == policy][0]
        xs = [g + (k - 1.5) * width for g in range(len(CASES))]
        ys = [row[c] / heft_cc[c] for c in CASES]
        ax.bar(xs, ys, width, color=colour, label=label)
    ax.axhline(1.0, color=CS_COLOUR, linewidth=3, label="heft-cc")
    ax.set_xticks(range(len(CASES)))
    ax.set_xticklabels([c.replace("-i", "\ni") for c in CASES])
    ax.set_ylabel("Simulated wall / heft-cc (x)")
    ax.set_ylim(0.9, 1.5)
    ax.legend(frameon=False, ncol=3, loc="upper left")
    fig.tight_layout()
    return save(fig, out_dir, "fig5b_search_in_simulation")


# ---------------------------------------------------------------- 6. network speed


def netsweep_table(rows_dir):
    table = []
    for path in sorted(glob.glob(os.path.join(rows_dir, "netsweep-*"))):
        campaign = os.path.basename(path)
        mbit = int(campaign.split("-")[1])
        rows = load_campaign(rows_dir, campaign)
        for iterations in sorted({r["iterations"] for r in rows}):
            at = select(rows, iterations=iterations)
            cs = select(at, mode="cs2")
            base = select(at, mode="disagg2")
            speedups = paired_speedups(base, cs)
            if not speedups:
                continue
            table.append(
                {
                    "link Mbit": mbit,
                    "iterations": iterations,
                    "cs2 s": one(median(best_wall_per_repeat(cs).values())),
                    "disagg2 s": one(median(best_wall_per_repeat(base).values())),
                    "policies": " / ".join(sorted({r["policy"] for r in at})),
                    "speedup per repeat": joined(speedups),
                    "speedup median": one(median(speedups), 2),
                    "cs2 GB moved": one(median([r["moved_gb"] for r in cs]), 2),
                    "disagg2 GB moved": one(median([r["moved_gb"] for r in base]), 2),
                    "runs": len(at),
                    "failed verification": len([r for r in at if not r["passed"]]),
                }
            )
    table.sort(key=lambda r: (r["iterations"], r["link Mbit"]))
    return table


def figure_netsweep(table, out_dir):
    fig, ax = new_figure()
    for iterations, colour in [(1, CS_COLOUR), (256, "#70AD47")]:
        measured = [r for r in table if r["iterations"] == iterations]
        if measured:
            ax.plot(
                [r["link Mbit"] for r in measured],
                [r["speedup median"] for r in measured],
                "o-",
                color=colour,
                label=f"{iterations} iteration" + ("s" if iterations > 1 else "") + ", measured",
            )
    mbits = sorted(NETSWEEP_PREDICTION_I1)
    ax.plot(
        mbits,
        [NETSWEEP_PREDICTION_I1[m] for m in mbits],
        "o--",
        color=GREY,
        markerfacecolor="white",
        label="1 iteration, predicted",
    )
    ax.axhline(1.0, color="black", linewidth=1.5)
    ax.set_xscale("log")
    ax.set_xticks([400, 1000, 2500])
    ax.set_xticklabels(["400", "1000", "2500"])
    ax.minorticks_off()
    ax.set_xlim(300, 3300)
    ax.set_ylim(0, 7)
    ax.set_xlabel("Link speed per node (Mbit/s)")
    ax.set_ylabel("Speedup over disaggregated (x)")
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    return save(fig, out_dir, "fig6_network_speed")


# ---------------------------------------------------------------- main


def main():
    rows_dir = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else HERE
    os.makedirs(out_dir, exist_ok=True)
    style()

    campaigns = [
        "demo3-slots",
        "demo3-sweep",
        "demo3-overlap-sweep",
        "demo3-droid",
        "demo3-slots-droid",
        "demo3-sensitivity-droid",
        "demo3-scale",
        "sched-skew",
        "sched-uniform",
        "sched-lp",
        "sched-mcts",
        "sched-droid",
        "sched-lp-low",
        "sched-lp-uniform",
    ]
    rows = {name: load_campaign(rows_dir, name) for name in campaigns}
    checks = [check(rows[name], name) for name in campaigns]
    forecast, simulated, bounds = load_forecasts()

    tables = {}
    tables["t1_slots"] = intensity_table(
        rows["demo3-slots"],
        [("cs2", "data-local"), ("cs2", "heft"), ("disagg2", "fifo-hash")],
        [
            ("cs2 over disagg2", "disagg2", "cs2", None),
            ("cs2 data-local over disagg2", "disagg2", "cs2", "data-local"),
        ],
    )
    tables["t1_sweep"] = intensity_table(
        rows["demo3-sweep"],
        [("cs", "heft"), ("cs", "data-local"), ("disagg", "fifo-hash"), ("disagg", "heft")],
        [("cs over disagg", "disagg", "cs", None)],
    )
    tables["t1_overlap"] = intensity_table(
        rows["demo3-overlap-sweep"],
        [("cs", "data-local"), ("cs", "heft"), ("disagg", "fifo-hash"), ("disagg2", "fifo-hash")],
        [("cs over disagg", "disagg", "cs", None), ("cs over disagg2", "disagg2", "cs", None)],
    )
    tables["t2_droid_batches"] = droid_table(rows["demo3-droid"])
    tables["t2_droid_totals"] = [droid_totals(rows["demo3-droid"])]
    tables["t2_droid_fused"] = droid_fused_table(
        rows["demo3-slots-droid"], rows["demo3-sensitivity-droid"]
    )
    tables["t3_scale"] = scale_table(rows["demo3-scale"])
    skew_cells = policy_cells(rows["sched-skew"], SKEW_POLICIES)
    skew_lp2 = policy_cells(rows["sched-lp"] + rows["sched-lp-low"], ["lp-plan2"])
    for cell in skew_lp2:
        cell["policy"] = LP2_OTHER_SESSION
    tables["t4_sched_skew_cells"] = skew_cells + skew_lp2
    tables["t4_sched_skew"] = pivot(
        skew_cells + skew_lp2, SKEW_POLICIES + [LP2_OTHER_SESSION], best_of=skew_cells
    )
    uniform_policies = ["heft-cc", "data-local", "backlog", "lp-plan", "heft"]
    uniform_cells = policy_cells(rows["sched-uniform"], uniform_policies)
    uniform_lp2 = policy_cells(rows["sched-lp-uniform"], ["lp-plan2"])
    for cell in uniform_lp2:
        cell["policy"] = LP2_OTHER_SESSION
    tables["t4_sched_uniform_cells"] = uniform_cells + uniform_lp2
    tables["t4_sched_uniform"] = pivot(
        uniform_cells + uniform_lp2, uniform_policies + [LP2_OTHER_SESSION], best_of=uniform_cells
    )
    tables["t4_sched_lp_low"] = paired_policy_table(
        rows["sched-lp-low"], ["lp-plan2", "heft-cc", "backlog", "data-local"], "heft-cc"
    )
    tables["t4_sched_lp_uniform"] = paired_policy_table(
        rows["sched-lp-uniform"], ["lp-plan2", "heft-cc", "data-local"], "heft-cc"
    )
    tables["t4_sched_lp"] = lp_table(rows["sched-lp"], bounds)
    tables["t5_sched_mcts"] = mcts_table(rows["sched-mcts"], forecast)
    tables["t5_forecast_vs_measured"] = forecast_pairs(
        rows["sched-skew"], rows["sched-uniform"], rows["sched-lp"], rows["sched-mcts"], forecast
    )
    tables["t5_search_simulated"] = simulated_table(simulated)
    tables["t6_netsweep"] = netsweep_table(rows_dir)
    tables["t7_sched_droid"] = sched_droid_table(rows["sched-droid"])
    tables["t7_sched_droid_waves"] = sched_droid_waves(rows["sched-droid"])
    tables["checks"] = checks + [
        check(load_campaign(rows_dir, os.path.basename(p)), os.path.basename(p))
        for p in sorted(glob.glob(os.path.join(rows_dir, "netsweep-*")))
    ]

    figures = [
        figure_speedup_vs_intensity(tables["t1_slots"], tables["t1_sweep"], out_dir),
        figure_slot_matching(tables["t1_slots"], tables["t1_overlap"], out_dir),
        figure_droid_batches(tables["t2_droid_batches"], out_dir),
        figure_droid_fused(tables["t2_droid_fused"], out_dir),
        figure_scale(tables["t3_scale"], out_dir),
        figure_skew(skew_cells, out_dir),
        figure_lp(tables["t4_sched_lp"], out_dir),
        figure_forecast(tables["t5_forecast_vs_measured"], out_dir),
        figure_simulated(tables["t5_search_simulated"], out_dir),
        figure_netsweep(tables["t6_netsweep"], out_dir),
        figure_sched_droid(rows["sched-droid"], tables["t7_sched_droid_waves"], out_dir),
    ]

    sections = []
    for name, table in tables.items():
        if not table:
            sections.append(f"## {name}\n\nno rows\n")
            continue
        write_csv(out_dir, name, table)
        sections.append(f"## {name}\n\n{markdown(table)}\n")
    with open(os.path.join(out_dir, "tables.md"), "w") as handle:
        handle.write("# Generated tables (make_figures.py)\n\n" + "\n".join(sections))

    errors = [abs(p["error %"]) for p in tables["t5_forecast_vs_measured"]]
    within = len([e for e in errors if e <= 10])
    print(
        f"forecast pairs {len(errors)}, median |error| {median(errors):.1f}%, within 10%: {within}"
    )
    for path in figures:
        print(path)


if __name__ == "__main__":
    main()
