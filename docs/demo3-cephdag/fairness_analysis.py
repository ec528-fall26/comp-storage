"""Where the time goes in the demo3 pilots, from the per-task stage records.

Usage: python3 fairness_analysis.py RAW_DIR OUT_DIR

RAW_DIR holds a copy of aakashpc:/mnt/mass/cephdag/campaigns/pilot-{sweep,droid,skew}
under RAW_DIR/campaigns/, layout unchanged. Writes OUT_DIR/runs.json (every run, with
1 s link series) and OUT_DIR/summary.json, and prints the markdown tables of FAIRNESS.md.

Timing comes from the coordinator's task records: `submitted` and `actual_finish` are
on the coordinator clock (seconds since the run began); `result.seconds` and
`result.stages` are worker-side durations. Stages run strictly in sequence inside
execute_payload, so a task's stages are laid end to end, starting in the middle of
the slack between its [submitted, actual_finish] window and its execution time.
The slack is the submit and completion-polling latency, tens of milliseconds.
"""

import collections
import json
import sys
from pathlib import Path

LINK = 47087500.0  # bytes/s: verified TCP goodput of one tbf 400 Mbit NIC direction
# Partitions per DROID source shard in pilot-droid (its driver.log); partition j of shard s
# sits on node (s + j) % 8 (experiments/droid_ingest.py, CyclicPlacement with offset s).
DROID_SHARDS = {2: 21, 3: 34, 4: 32, 5: 31}
CASES = [
    ("sweep", 1),
    ("sweep", 2),
    ("sweep", 4),
    ("sweep", 8),
    ("sweep", 16),
    ("sweep", 32),
    ("sweep", 64),
    ("sweep", 128),
    ("droid", None),
    ("skew", 1),
    ("skew", 16),
]
KEY_CASES = [("sweep", 1), ("sweep", 128), ("droid", None), ("skew", 1)]


def node_of(pool):
    return int(pool.removeprefix("node"))


def task_inputs(path, result):
    """{task: [(storage node, bytes)]} for every input, rebuilt from the workload graph."""
    tasks = result["tasks"]
    ids = sorted(tasks)
    reads = {
        task: [s["bytes"] for s in tasks[task]["result"]["stages"] if s["kind"] == "read"]
        for task in ids
    }
    inputs = {}
    if result["case"]["shape"] == "droid":
        sources = [(s + j) % 8 for s, count in DROID_SHARDS.items() for j in range(count)]
        if len(ids) != 4 * len(sources):
            raise ValueError("DROID partition count differs from pilot-droid")
        for part, node in enumerate(sources):
            decode, encode, stats, parquet = ids[4 * part : 4 * part + 4]
            frames, state = tasks[decode]["result"]["outputs"]
            inputs[decode] = [(node, reads[decode][0])]
            inputs[encode] = [(node_of(frames["pool"]), frames["size"])]
            inputs[stats] = [(node_of(frames["pool"]), frames["size"])]
            inputs[parquet] = [(node_of(state["pool"]), state["size"])]
    else:
        # Shard i is on placement[i % len(placement)] (experiments/run.py synthetic_dataset).
        placement = [0] * 7 + list(range(1, 8)) if "pilot-skew" in path else list(range(8))
        for index, task in enumerate(ids):
            inputs[task] = [(placement[index % len(placement)], reads[task][0])]
    return inputs


def harness_row(path, run_id):
    rows = path.parent / "rows.jsonl"
    if not rows.exists():
        rows = path.parent.parent / "rows.jsonl"
    for line in open(rows):
        row = json.loads(line)
        if row["run_id"] == run_id:
            return row
    raise KeyError(run_id)


def timeline_of(result, inputs, mode):
    """Each task with its worker, window and stages; a stage's link is (from, to) or None."""
    timeline = []
    for task, record in result["tasks"].items():
        worker = record["worker"]
        run = record["result"]
        slack = record["actual_finish"] - record["submitted"] - run["seconds"]
        clock = start = record["submitted"] + slack / 2
        reads = iter(inputs[task])
        outputs = iter(run["outputs"])
        stages = []
        for stage in run["stages"]:
            link = None
            if stage["kind"] == "read":
                node, size = next(reads)
                if size != stage["bytes"]:
                    raise ValueError(f"{task}: input size differs from its read stage")
                if mode == "disagg" or node != worker:
                    link = (node, worker)
            elif stage["kind"] == "write":
                node = node_of(next(outputs)["pool"])
                if mode == "disagg" or node != worker:
                    link = (worker, node)
            stages.append(
                dict(
                    kind=stage["kind"],
                    start=clock,
                    end=clock + stage["seconds"],
                    bytes=stage["bytes"],
                    link=link,
                )
            )
            clock += stage["seconds"]
        timeline.append(
            dict(
                task=task,
                worker=worker,
                submitted=record["submitted"],
                finished=record["actual_finish"],
                seconds=run["seconds"],
                slack=slack,
                publish=start + run["seconds"] - clock,
                stages=stages,
            )
        )
    return timeline


def by_worker(timeline):
    workers = collections.defaultdict(list)
    for task in timeline:
        workers[task["worker"]].append(task)
    for tasks in workers.values():
        tasks.sort(key=lambda t: t["submitted"])
    return dict(sorted(workers.items()))


def duration(task, kind):
    return sum(s["end"] - s["start"] for s in task["stages"] if s["kind"] == kind)


def bounds(timeline, mode, models):
    """Throughput lower bounds: busiest NIC direction, per-node local work, single-slot serial."""
    read_bps = models["storage"]["read_bps"]
    write_bps = models["storage"]["write_bps"]
    nic = collections.defaultdict(float)
    local = collections.defaultdict(float)
    compute = collections.defaultdict(float)
    serial = collections.defaultdict(float)
    for task in timeline:
        w = task["worker"]
        for s in task["stages"]:
            if s["kind"] == "compute":
                seconds = s["end"] - s["start"]
                compute[w] += seconds
            elif s["link"] is None:
                seconds = s["bytes"] / (read_bps[w] if s["kind"] == "read" else write_bps[w])
                local[w] += seconds
            else:
                nic[s["link"][0], "tx"] += s["bytes"]
                nic[s["link"][1], "rx"] += s["bytes"]
                seconds = s["bytes"] / LINK
            serial[w] += seconds
    (busiest, busiest_bytes) = max(nic.items(), key=lambda kv: kv[1]) if nic else (None, 0.0)
    link_seconds = busiest_bytes / LINK
    node_seconds = max(local[w] + compute[w] for w in compute)
    if mode == "cs":
        brief = node_seconds + link_seconds  # the brief's definition
        valid = max(node_seconds, link_seconds)
    else:
        brief = valid = max(link_seconds, max(compute.values()))
    return dict(
        busiest_link=f"{busiest[0]}-{busiest[1]}" if busiest else None,
        link_seconds=link_seconds,
        node_seconds=node_seconds,
        brief_bound=brief,
        lower_bound=valid,
        serial_bound=max(serial.values()),
        compute_total=sum(compute.values()),
        nic_bytes={f"{n}-{d}": b for (n, d), b in sorted(nic.items())},
    )


def worker_breakdown(timeline, wall):
    """Seconds per worker in each stage, in dispatch overhead and idle; sums to wall."""
    out = {}
    for w, tasks in by_worker(timeline).items():
        c = collections.defaultdict(float)
        for t in tasks:
            for kind in ("read", "compute", "write"):
                c[kind] += duration(t, kind)
            c["publish"] += t["publish"]
            c["slack"] += t["slack"]
            for s in t["stages"]:
                if s["link"] is not None:
                    c["rx_bytes" if s["kind"] == "read" else "tx_bytes"] += s["bytes"]
        c["head"] = tasks[0]["submitted"]
        c["gap"] = sum(b["submitted"] - a["finished"] for a, b in zip(tasks, tasks[1:]))
        c["tail"] = wall - tasks[-1]["finished"]
        c["tasks"] = len(tasks)
        out[w] = dict(c)
    return out


def dispatch_gaps(timeline):
    return [
        b["submitted"] - a["finished"]
        for tasks in by_worker(timeline).values()
        for a, b in zip(tasks, tasks[1:])
    ]


def link_activity(timeline, wall):
    """Per node and direction: bytes per 1 s bin, and seconds with 0, 1, 2, ... flows."""
    flows = collections.defaultdict(list)
    for task in timeline:
        for s in task["stages"]:
            if s["link"] is not None:
                flows[s["link"][0], "tx"].append((s["start"], s["end"], s["bytes"]))
                flows[s["link"][1], "rx"].append((s["start"], s["end"], s["bytes"]))
    bins = int(wall) + 1
    report = {}
    for (node, direction), items in sorted(flows.items()):
        series = [0.0] * bins
        for a, b, size in items:
            rate = size / max(b - a, 1e-9)
            for k in range(max(0, int(a)), min(bins, int(b) + 1)):
                series[k] += rate * max(0.0, min(b, k + 1) - max(a, k))
        events = sorted([(a, 1) for a, _, _ in items] + [(b, -1) for _, b, _ in items])
        level, last, seconds_at = 0, 0.0, collections.defaultdict(float)
        for t, delta in events:
            seconds_at[level] += t - last
            level, last = level + delta, t
        seconds_at[0] += max(0.0, wall - last)
        report[f"{node}-{direction}"] = dict(
            bytes=sum(size for _, _, size in items),
            mean_utilisation=sum(series) / (LINK * wall),
            active_fraction=sum(v for k, v in seconds_at.items() if k >= 1) / wall,
            shared_fraction=sum(v for k, v in seconds_at.items() if k >= 2) / wall,
            bins_over_90=sum(1 for x in series if x > 0.9 * LINK) / bins,
            utilisation=[round(x / LINK, 3) for x in series],
        )
    return report


def flow_rates(timeline):
    """Median MB/s of remote reads and writes of at least 1 MiB (smaller ones are
    latency-bound), by how many such flows shared the busier of their two links."""
    remote = [
        s for t in timeline for s in t["stages"] if s["link"] is not None and s["bytes"] >= 2**20
    ]
    groups = collections.defaultdict(list)
    for s in remote:
        mid = (s["start"] + s["end"]) / 2
        live = [o for o in remote if o["start"] <= mid < o["end"]]
        sharing = max(
            sum(1 for o in live if o["link"][0] == s["link"][0]),
            sum(1 for o in live if o["link"][1] == s["link"][1]),
        )
        groups[s["kind"], min(sharing, 3)].append(s["bytes"] / (s["end"] - s["start"]) / 1e6)
    return {
        f"{kind}-{'x3+' if n == 3 else f'x{n}'}": dict(
            count=len(v), median_mbs=sorted(v)[len(v) // 2]
        )
        for (kind, n), v in sorted(groups.items())
    }


def readahead(timeline, breakdown, compute_scale=1.0, keep_dispatch=False):
    """Wall if each worker read the next task's inputs while computing and writing the
    current one: a two-stage pipeline (reads | compute + write + publish) with the same
    assignment, order and stage durations. Dispatch gaps and slack are hidden, or kept
    in full with keep_dispatch."""
    finish = 0.0
    for w, tasks in by_worker(timeline).items():
        reads_done = rest_done = 0.0
        for t in tasks:
            read, compute = duration(t, "read"), duration(t, "compute")
            reads_done += read
            rest = t["seconds"] - read - compute * (1 - compute_scale)
            rest_done = max(rest_done, reads_done) + rest
        kept = breakdown[w]["slack"] + breakdown[w]["gap"] if keep_dispatch else 0.0
        finish = max(finish, breakdown[w]["head"] + rest_done + kept)
    return finish


def analyse(path):
    result = json.loads(path.read_text())
    mode = "disagg" if min(r["worker"] for r in result["tasks"].values()) >= 8 else "cs"
    inputs = task_inputs(str(path), result)
    timeline = timeline_of(result, inputs, mode)
    wall = result["wall_seconds"]
    b = bounds(timeline, mode, result["models"])
    harness = harness_row(path, result["run_id"])["network_payload_bytes"]["per_node"]
    for key, size in b["nic_bytes"].items():
        node, direction = key.split("-")
        if harness[node][direction] != size:
            raise ValueError(f"{path}: rebuilt {key} bytes differ from rows.jsonl")
    breakdown = worker_breakdown(timeline, wall)
    decisions = [d["at_seconds"] for d in result["decisions"]]
    return dict(
        file=str(path),
        run_id=result["run_id"],
        mode=mode,
        policy=result["scheduler"],
        campaign=path.parts[path.parts.index("campaigns") + 1].removeprefix("pilot-"),
        iterations=result["case"].get("iterations"),
        tasks=len(result["tasks"]),
        wall=wall,
        bounds=b,
        workers=breakdown,
        gaps=dispatch_gaps(timeline),
        slack=[t["slack"] for t in timeline],
        publish=[t["publish"] for t in timeline],
        decision_intervals=[later - earlier for earlier, later in zip(decisions, decisions[1:])],
        links=link_activity(timeline, wall),
        flow_rates=flow_rates(timeline),
        readahead=readahead(timeline, breakdown),
        timeline=timeline,
    )


def quantiles(values):
    values = sorted(values)
    return {
        "p50": values[len(values) // 2],
        "p90": values[int(len(values) * 0.9)],
        "max": values[-1],
    }


def case_summary(runs):
    """Best wall per mode now, and with read-ahead in one or both modes."""
    cs = [r for r in runs if r["mode"] == "cs"]
    dg = [r for r in runs if r["mode"] == "disagg"]
    per_task = lambda rs: sum(r["bounds"]["compute_total"] / r["tasks"] for r in rs) / len(rs)
    # With read-ahead, disagg workers would compute concurrently as cs workers do, and the
    # shared memory bus would slow each computation to cs's per-task time.
    scale = max(1.0, per_task(cs) / per_task(dg))
    for r in runs:
        # No schedule beats the busiest link, however much it overlaps.
        floor = r["bounds"]["link_seconds"]
        r["readahead"] = max(floor, r["readahead"])
        r["readahead_kept"] = max(floor, readahead(r["timeline"], r["workers"], keep_dispatch=True))
        r["readahead_adjusted"] = max(
            floor, readahead(r["timeline"], r["workers"], scale if r["mode"] == "disagg" else 1.0)
        )
    best = lambda rs, key: min(r[key] for r in rs)
    best_policy = lambda rs, key: min(rs, key=lambda r: r[key])["policy"]
    s = dict(
        cs_now=best(cs, "wall"),
        cs_now_policy=best_policy(cs, "wall"),
        disagg_now=best(dg, "wall"),
        disagg_now_policy=best_policy(dg, "wall"),
        cs_readahead=best(cs, "readahead"),
        cs_readahead_kept=best(cs, "readahead_kept"),
        disagg_readahead=best(dg, "readahead"),
        disagg_readahead_kept=best(dg, "readahead_kept"),
        disagg_readahead_adjusted=best(dg, "readahead_adjusted"),
        compute_scale=scale,
        disagg_floor=min(r["bounds"]["lower_bound"] for r in dg),
    )
    s.update(
        speedup_now=s["disagg_now"] / s["cs_now"],
        speedup_disagg_only=s["disagg_readahead"] / s["cs_now"],
        speedup_both_hidden=s["disagg_readahead"] / s["cs_readahead"],
        speedup_both_kept=s["disagg_readahead_kept"] / s["cs_readahead_kept"],
        speedup_both_adjusted=s["disagg_readahead_adjusted"] / s["cs_readahead"],
        speedup_floor=s["disagg_floor"] / s["cs_readahead"],
    )
    return s


def label(case):
    return f"{case[0]} i{case[1]}" if case[1] else case[0]


def tables(runs, summary):
    key = [r for r in runs if (r["campaign"], r["iterations"]) in KEY_CASES]
    key.sort(
        key=lambda r: (KEY_CASES.index((r["campaign"], r["iterations"])), r["mode"], r["wall"])
    )
    out = [
        "### Wall time against lower bounds",
        "",
        "| case | mode | policy | wall s | busiest link (GB) | link s | node s | bound s | "
        "wall/bound | single-slot serial s | wall/serial |",
        "|" + "---|" * 11,
    ]
    for r in key:
        b = r["bounds"]
        link = b["busiest_link"] or "none"
        gb = b["nic_bytes"].get(link, 0) / 1e9
        out.append(
            f"| {label((r['campaign'], r['iterations']))} | {r['mode']} | {r['policy']} | "
            f"{r['wall']:.1f} | {link} ({gb:.2f}) | {b['link_seconds']:.1f} | "
            f"{b['node_seconds']:.1f} | {b['lower_bound']:.1f} | "
            f"{r['wall'] / b['lower_bound']:.2f} | {b['serial_bound']:.1f} | "
            f"{r['wall'] / b['serial_bound']:.2f} |"
        )
    out += [
        "",
        "### Where each worker's time goes (mean over workers, % of wall)",
        "",
        "| case | mode | policy | read | compute | write | dispatch (slack+gap+head+publish) "
        "| idle at end | read: remote bytes at line rate | read: rest (local, sharing, per-read cost) "
        "| write: remote bytes at line rate | write: rest |",
        "|" + "---|" * 12,
    ]
    for r in key:
        ws = list(r["workers"].values())
        mean = lambda k: sum(w.get(k, 0.0) for w in ws) / len(ws) / r["wall"] * 100
        line_rx = sum(w.get("rx_bytes", 0) for w in ws) / len(ws) / LINK / r["wall"] * 100
        line_tx = sum(w.get("tx_bytes", 0) for w in ws) / len(ws) / LINK / r["wall"] * 100
        dispatch = mean("slack") + mean("gap") + mean("head") + mean("publish")
        out.append(
            f"| {label((r['campaign'], r['iterations']))} | {r['mode']} | {r['policy']} | "
            f"{mean('read'):.0f} | {mean('compute'):.0f} | {mean('write'):.0f} | {dispatch:.1f} | "
            f"{mean('tail'):.1f} | {line_rx:.0f} | {mean('read') - line_rx:.0f} | {line_tx:.0f} | "
            f"{mean('write') - line_tx:.0f} |"
        )
    out += [
        "",
        "### Storage-node links (mean over the 8 storage nodes)",
        "",
        "| case | mode | policy | tx active % of wall | tx with ≥2 flows % | tx mean util % | "
        "rx active % | rx with ≥2 flows % | reads alone MB/s (n) | reads shared MB/s (n) | "
        "writes alone MB/s (n) | writes shared MB/s (n) |",
        "|" + "---|" * 12,
    ]
    for r in key:

        def mean_link(direction, field):
            values = [
                r["links"][f"{n}-{direction}"][field]
                for n in range(8)
                if f"{n}-{direction}" in r["links"]
            ]
            return sum(values) / len(values) * 100 if values else 0.0

        def rate(kind, shared):
            items = [
                v
                for k, v in r["flow_rates"].items()
                if k.startswith(kind) and (k != f"{kind}-x1") == shared
            ]
            if not items:
                return "–"
            n = sum(v["count"] for v in items)
            m = sum(v["median_mbs"] * v["count"] for v in items) / n
            return f"{m:.0f} ({n})"

        out.append(
            f"| {label((r['campaign'], r['iterations']))} | {r['mode']} | {r['policy']} | "
            f"{mean_link('tx', 'active_fraction'):.0f} | {mean_link('tx', 'shared_fraction'):.0f} | "
            f"{mean_link('tx', 'mean_utilisation'):.0f} | {mean_link('rx', 'active_fraction'):.0f} | "
            f"{mean_link('rx', 'shared_fraction'):.0f} | {rate('read', False)} | "
            f"{rate('read', True)} | {rate('write', False)} | {rate('write', True)} |"
        )
    out += [
        "",
        "### Dispatch overhead per task (seconds)",
        "",
        "| case | mode | policy | slack p50 / p90 | gap p50 / p90 / max | coordinator "
        "decision interval p50 / p90 | completion record write p50 / p90 |",
        "|" + "---|" * 7,
    ]
    for r in key:
        s, g, d, p = (quantiles(r[k]) for k in ("slack", "gaps", "decision_intervals", "publish"))
        out.append(
            f"| {label((r['campaign'], r['iterations']))} | {r['mode']} | {r['policy']} | "
            f"{s['p50']:.3f} / {s['p90']:.3f} | {g['p50']:.3f} / {g['p90']:.3f} / {g['max']:.2f} | "
            f"{d['p50']:.3f} / {d['p90']:.3f} | {p['p50']:.3f} / {p['p90']:.3f} |"
        )
    out += [
        "",
        "### Speedup now and with read-ahead (best policy per mode, seconds)",
        "",
        "| case | cs now | disagg now | speedup now | cs read-ahead, gaps hidden / kept | "
        "disagg read-ahead, gaps hidden / kept | disagg, contention-adjusted (×compute) | "
        "disagg floor | speedup, read-ahead in disagg only | speedup, read-ahead in both "
        "(hidden / kept) | speedup, both, adjusted | disagg floor / cs read-ahead |",
        "|" + "---|" * 12,
    ]
    for case in CASES:
        s = summary[label(case)]
        out.append(
            f"| {label(case)} | {s['cs_now']:.1f} ({s['cs_now_policy']}) | "
            f"{s['disagg_now']:.1f} ({s['disagg_now_policy']}) | {s['speedup_now']:.2f} | "
            f"{s['cs_readahead']:.1f} / {s['cs_readahead_kept']:.1f} | "
            f"{s['disagg_readahead']:.1f} / {s['disagg_readahead_kept']:.1f} | "
            f"{s['disagg_readahead_adjusted']:.1f} (×{s['compute_scale']:.2f}) | "
            f"{s['disagg_floor']:.1f} | {s['speedup_disagg_only']:.2f} | "
            f"{s['speedup_both_hidden']:.2f} / {s['speedup_both_kept']:.2f} | "
            f"{s['speedup_both_adjusted']:.2f} | {s['speedup_floor']:.2f} |"
        )
    return "\n".join(out)


def main():
    raw, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    runs = [analyse(path) for path in sorted(raw.glob("campaigns/pilot-*/**/run-*.json"))]
    summary = {}
    for case in CASES:
        selected = [r for r in runs if (r["campaign"], r["iterations"]) == case]
        summary[label(case)] = case_summary(selected)
    print(tables(runs, summary))
    for r in runs:
        del r["timeline"]
    (out / "runs.json").write_text(json.dumps(runs))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
