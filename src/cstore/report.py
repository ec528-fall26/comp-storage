"""Summarise the measurements of one benchmark run.

Reads ``results/<run_id>/<mode>/*.json`` (written by ``cstore bench``) and
``results/<run_id>/verify/*.json`` (written by ``cstore verify-run``), prints
a Markdown summary, and saves summary.md, summary.json and summary.svg (a
two-panel chart for slides) next to the results.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .config import Config
from .jobs import atomic_write_json, atomic_write_text

MB = 1e6
MODES = ("separate", "storage")
LABELS = {"separate": "Separate compute", "storage": "On storage node"}


def _load(dir_: Path) -> list[dict]:
    if not dir_.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(dir_.glob("*.json"))]


def summarize(cfg: Config, run_id: str) -> tuple[str, dict]:
    base = cfg.results / run_id
    modes = {m: _load(base / m) for m in MODES}
    verify = {p.stem: json.loads(p.read_text()) for p in sorted((base / "verify").glob("*.json"))}

    lines = [f"# Run `{run_id}`", ""]
    lines += [
        "| mode | job | episodes | frames | input MB | output MB | wall s | convert s | write-back s "
        "| cross-host MB | storage NIC in / out MB | verified |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    totals = {}
    for mode, recs in modes.items():
        t = {"jobs": 0, "ok": 0, "wall_s": 0.0, "convert_s": 0.0, "writeback_s": 0.0,
             "cross_host_bytes": 0, "input_bytes": 0, "output_bytes": 0}  # fmt: skip
        for r in recs:
            w = r.get("worker") or {}
            n = r.get("net_storage_node", {})
            job_id = r["job"]["job_id"]
            v = verify.get(f"{mode}-{job_id}")
            vtxt = "-" if v is None else ("ok" if v.get("ok") else "FAILED")
            lines.append(
                f"| {mode} | {job_id} | {w.get('episodes', '-')} | {w.get('frames', '-')} "
                f"| {w.get('input_bytes', 0) / MB:.1f} | {w.get('output_bytes', 0) / MB:.1f} "
                f"| {r['wall_s']:.1f} | {w.get('convert_s', 0):.1f} | {w.get('writeback_s', 0):.1f} "
                f"| {n.get('total_bytes', 0) / MB:.1f} | {n.get('rx_bytes', 0) / MB:.1f} / {n.get('tx_bytes', 0) / MB:.1f} "
                f"| {vtxt} |"
            )
            t["jobs"] += 1
            t["ok"] += int(bool(r.get("ok")))
            t["wall_s"] += r["wall_s"]
            t["convert_s"] += w.get("convert_s", 0)
            t["writeback_s"] += w.get("writeback_s", 0)
            t["cross_host_bytes"] += n.get("total_bytes", 0)
            t["input_bytes"] += w.get("input_bytes", 0)
            t["output_bytes"] += w.get("output_bytes", 0)
        totals[mode] = t

    lines += ["", "| mode | jobs ok | total wall s | cross-host MB | input MB | output MB |", "|---|---|---:|---:|---:|---:|"]
    for mode, t in totals.items():
        lines.append(
            f"| {mode} | {t['ok']}/{t['jobs']} | {t['wall_s']:.1f} | {t['cross_host_bytes'] / MB:.1f} "
            f"| {t['input_bytes'] / MB:.1f} | {t['output_bytes'] / MB:.1f} |"
        )

    s, c = totals["separate"], totals["storage"]
    findings: dict = {}
    if s["jobs"] and s["input_bytes"]:
        findings["separate_cross_host_over_input_plus_output"] = s["cross_host_bytes"] / (
            s["input_bytes"] + s["output_bytes"]
        )
        findings["output_over_input"] = s["output_bytes"] / s["input_bytes"]
    if s["jobs"] and c["jobs"] and s["cross_host_bytes"]:
        findings["cross_host_reduction"] = 1 - c["cross_host_bytes"] / s["cross_host_bytes"]
        findings["wall_ratio_storage_over_separate"] = c["wall_s"] / s["wall_s"] if s["wall_s"] else None
        lines += [
            "",
            f"Cross-host traffic: {s['cross_host_bytes'] / MB:.1f} MB with separate compute "
            f"({findings['separate_cross_host_over_input_plus_output']:.2f}x input+output), "
            f"{c['cross_host_bytes'] / MB:.1f} MB on the storage node "
            f"({100 * findings['cross_host_reduction']:.1f}% less).",
            f"Runtime: {s['wall_s']:.1f} s with separate compute, {c['wall_s']:.1f} s on the storage node "
            f"(ratio {findings['wall_ratio_storage_over_separate']:.2f}).",
        ]
    comparisons = {k: v for k, v in verify.items() if k.startswith("compare-")}
    if comparisons:
        same = sum(1 for v in comparisons.values() if v.get("comparison", {}).get("same_content"))
        identical = sum(1 for v in comparisons.values() if v.get("comparison", {}).get("identical"))
        findings["outputs_same_content"] = f"{same}/{len(comparisons)}"
        findings["outputs_byte_identical"] = f"{identical}/{len(comparisons)}"
        lines.append(
            f"Equivalence: {identical}/{len(comparisons)} shards produced byte-identical outputs in both modes "
            f"({same}/{len(comparisons)} with the same decoded content)."
        )

    text = "\n".join(lines) + "\n"
    summary = {"run_id": run_id, "totals": totals, "findings": findings}
    atomic_write_text(base / "summary.md", text)
    atomic_write_json(base / "summary.json", summary)
    if s["jobs"] or c["jobs"]:
        atomic_write_text(base / "summary.svg", chart_svg(run_id, totals))
    return text, summary


def chart_svg(run_id: str, totals: dict) -> str:
    """Two bar panels: cross-host traffic and wall time (convert / write-back)."""
    w, h = 960, 420
    panel_w, top, bottom = 400, 70, 340
    ink, muted, grid = "#1f2328", "#59636e", "#d0d7de"
    colors = {"separate": "#4a5b6e", "storage": "#2f81f7"}
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="Helvetica, Arial, sans-serif">',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="40" y="34" font-size="20" fill="{ink}" font-weight="600">DROID RLDS → LeRobot: separate compute vs. on the storage node</text>',
        f'<text x="40" y="56" font-size="13" fill="{muted}">run {escape(run_id)}; same shards, image and limits; cold caches</text>',
    ]

    def panel(x0: float, title: str, values: dict[str, list[tuple[float, str]]], unit: str) -> None:
        peak = max((sum(v for v, _ in segs) for segs in values.values()), default=0) or 1
        out.append(f'<text x="{x0}" y="{top + 10}" font-size="15" fill="{ink}" font-weight="600">{title}</text>')
        out.append(f'<line x1="{x0}" y1="{bottom}" x2="{x0 + panel_w}" y2="{bottom}" stroke="{grid}"/>')
        bar_w = 110
        for k, mode in enumerate(MODES):
            segs = values.get(mode, [])
            bx = x0 + 50 + k * 180
            y = bottom
            for v, shade in segs:
                bh = (bottom - top - 50) * v / peak
                y -= bh
                out.append(f'<rect x="{bx}" y="{y:.1f}" width="{bar_w}" height="{bh:.1f}" fill="{shade}"/>')
            total = sum(v for v, _ in segs)
            out.append(
                f'<text x="{bx + bar_w / 2}" y="{y - 8:.1f}" font-size="14" fill="{ink}" text-anchor="middle">'
                f"{total:,.1f} {unit}</text>"
            )
            out.append(
                f'<text x="{bx + bar_w / 2}" y="{bottom + 20}" font-size="13" fill="{muted}" text-anchor="middle">'
                f"{LABELS[mode]}</text>"
            )

    panel(
        40,
        "Cross-host traffic (storage node NIC)",
        {m: [(totals[m]["cross_host_bytes"] / MB, colors[m])] for m in MODES},
        "MB",
    )
    wall = {}
    for m in MODES:
        t = totals[m]
        other = max(t["wall_s"] - t["convert_s"] - t["writeback_s"], 0)
        wall[m] = [(t["convert_s"], colors[m]), (t["writeback_s"], "#8c959f"), (other, "#d0d7de")]
    panel(520, "Wall time", wall, "s")
    out.append(
        f'<text x="520" y="{h - 20}" font-size="12" fill="{muted}">bar = read + convert, then write-back '
        "(grey), then container start/stop (light)</text>"
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"
