"""Why did data-local's seconds per source GB rise across the demo3-droid batches?

    python3 -I droid_drift.py CAMPAIGN_DIR [OUT_JSON]

CAMPAIGN_DIR is a copy of aakashpc:/mnt/mass/cephdag/campaigns/demo3-droid. Each run file
wave0B-<mode>/a1/wave-0000/run-NNN-<policy>.json holds, per task, the worker, submit and
finish times (coordinator clock) and the worker's read, compute and write stage seconds.
Task 4p is decode_resize of partition p and reads source shard p (its pool is in config.json);
4p+1 encode_video and 4p+2 frame_stats read p's frames, 4p+3 state_parquet reads p's state
(both outputs of 4p). Prints markdown tables of placement, worker time, tail, dispatch gaps and
storage rates per batch, and writes the per-run summaries to OUT_JSON.
"""

import json
import re
from bisect import bisect_right
import sys
from collections import defaultdict
from pathlib import Path

FUNCTIONS = ("decode", "encode", "stats", "parquet")
GB = 1e9


def node_of(pool):
    return int(pool.removeprefix("node"))


def load_run(path, shards):
    run = json.loads(path.read_text())
    tasks = run["tasks"]
    records = []
    for task_id, record in tasks.items():
        partition, kind = divmod(int(task_id[1:]), 4)
        decoded = tasks[f"t{4 * partition:06d}"]["result"]["outputs"]
        source = (shards[partition], decoded[0], decoded[0], decoded[1])[kind]
        result = record["result"]
        stages = result["stages"]
        records.append(
            {
                "function": FUNCTIONS[kind],
                "worker": record["worker"],
                "local": node_of(source["pool"]) == record["worker"],
                "input_bytes": source["size"],
                "output_bytes": sum(output["size"] for output in result["outputs"]),
                "submitted": record["submitted"],
                "finish": record["actual_finish"],
                "decode_finish": tasks[f"t{4 * partition:06d}"]["actual_finish"],
                "seconds": result["seconds"],
                "read": sum(s["seconds"] for s in stages if s["kind"] == "read"),
                "compute": sum(s["seconds"] for s in stages if s["kind"] == "compute"),
                "write": sum(s["seconds"] for s in stages if s["kind"] == "write"),
            }
        )
    return run, records


def workers(records, wall):
    own = defaultdict(list)
    for record in records:
        own[record["worker"]].append(record)
    rows = {}
    for worker, tasks in sorted(own.items()):
        tasks.sort(key=lambda r: r["submitted"])
        gaps = [b["submitted"] - a["finish"] for a, b in zip(tasks, tasks[1:])]
        busy = sum(r["finish"] - r["submitted"] for r in tasks)
        rows[worker] = {
            "tasks": len(tasks),
            "source_gb": sum(r["input_bytes"] for r in tasks if r["function"] == "decode") / GB,
            "busy_s": busy,
            "idle_s": wall - busy,
            "gap_s": sum(gaps),
            "last_finish_s": tasks[-1]["finish"],
        }
    return rows


def rate(records, select, size, seconds):
    chosen = [r for r in records if select(r)]
    return sum(r[size] for r in chosen) / max(1e-9, sum(r[seconds] for r in chosen)) / 1e6


def is_frames_read(record):
    return record["function"] in ("encode", "stats") and record["local"]


def is_source_read(record):
    return record["function"] == "decode" and record["local"]


CONCURRENCY_BINS = ((0, 1), (2, 3), (4, 5), (6, 7))
AGE_BINS = ((0, 1), (1, 3), (3, 10), (10, 1e9))


def read_start(record):
    return record["submitted"] + (record["finish"] - record["submitted"] - record["seconds"]) / 2


def frames_read_by_age(records):
    """Local frames-read MB/s, binned by seconds from the decode's completion to the read's start."""
    rates = []
    for low, high in AGE_BINS:
        chosen = [
            r for r in records
            if is_frames_read(r) and low <= read_start(r) - r["decode_finish"] < high
        ]
        mb_s = sum(r["input_bytes"] for r in chosen) / max(1e-9, sum(r["read"] for r in chosen)) / 1e6
        rates.append((mb_s, len(chosen)))
    return rates


def frames_read_by_concurrency(records):
    """Local frames-read MB/s, binned by how many other read or write stages on the host were
    running at the read's midpoint (stages laid end to end from submit + slack / 2)."""
    io = []
    reads = []
    for r in records:
        start = read_start(r)
        io.append((start, start + r["read"]))
        write_start = start + r["read"] + r["compute"]
        io.append((write_start, write_start + r["write"]))
        if is_frames_read(r) and r["read"] > 0:
            reads.append((start + r["read"] / 2, r))
    io.sort()
    starts = [a for a, _ in io]
    ends = sorted(b for _, b in io)
    rates = []
    for low, high in CONCURRENCY_BINS:
        chosen = []
        for middle, r in reads:
            others = bisect_right(starts, middle) - bisect_right(ends, middle) - 1
            if low <= others <= high:
                chosen.append(r)
        mb_s = sum(r["input_bytes"] for r in chosen) / max(1e-9, sum(r["read"] for r in chosen)) / 1e6
        rates.append((mb_s, len(chosen)))
    return rates


def summarise(path, shards):
    run, records = load_run(path, shards)
    wall = run["wall_seconds"]
    source_gb = sum(shard["size"] for shard in shards) / GB
    per_pool = defaultdict(lambda: [0, 0.0])
    for shard in shards:
        per_pool[node_of(shard["pool"])][0] += 1
        per_pool[node_of(shard["pool"])][1] += shard["size"] / GB
    rows = workers(records, wall)

    def summed(select, field):
        return sum(r[field] for r in records if select(r)) / source_gb

    tenths = []
    for tenth in range(10):
        inside = [r for r in records if min(9, int(10 * r["submitted"] / wall)) == tenth]
        tenths.append(rate(inside, is_frames_read, "input_bytes", "read"))
    return {
        "batch": int(re.search(r"wave(\d+)-", str(path)).group(1)),
        "order_in_batch": int(path.name.split("-")[1]),
        "policy": run["scheduler"],
        "wall_s": wall,
        "source_gb": source_gb,
        "s_per_gb": wall / source_gb,
        "policy_s": run["scheduler_seconds"],
        "partitions_per_pool": [per_pool[n][0] for n in sorted(per_pool)],
        "source_gb_per_pool": [per_pool[n][1] for n in sorted(per_pool)],
        "workers": rows,
        "tail_s": wall - min(row["last_finish_s"] for row in rows.values()),
        "remote_decodes": sum(r["function"] == "decode" and not r["local"] for r in records),
        # Seconds per source GB, summed over the 8 workers (divide by 8 for a share of wall).
        "summed_s_per_gb": {
            "source_read": summed(is_source_read, "read"),
            "frames_read": summed(is_frames_read, "read"),
            "remote_read": summed(lambda r: not r["local"], "read"),
            "write": summed(lambda r: True, "write"),
            "compute": summed(lambda r: True, "compute"),
            "slack": sum(r["finish"] - r["submitted"] - r["seconds"] for r in records) / source_gb,
            "gaps": sum(row["gap_s"] for row in rows.values()) / source_gb,
        },
        "mb_s": {
            "source_read": rate(records, is_source_read, "input_bytes", "read"),
            "frames_read": rate(records, is_frames_read, "input_bytes", "read"),
            "decode_write": rate(
                records, lambda r: r["function"] == "decode", "output_bytes", "write"
            ),
            "encode_write": rate(
                records, lambda r: r["function"] == "encode", "output_bytes", "write"
            ),
        },
        "frames_read_mb_s_by_tenth": tenths,
        "frames_read_mb_s_by_node": [
            rate(records, lambda r, n=n: is_frames_read(r) and r["worker"] == n, "input_bytes", "read")
            for n in range(8)
        ],
        "frames_read_mb_s_by_concurrency": frames_read_by_concurrency(records),
        "frames_read_mb_s_by_age": frames_read_by_age(records),
    }


def table(header, rows):
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for row in rows:
        print("| " + " | ".join(row) + " |")
    print()


def main():
    campaign = Path(sys.argv[1])
    runs = []
    for config in sorted(campaign.glob("wave0*-*/a1/wave-0000/config.json")):
        shards = json.loads(config.read_text())["dataset"]["shards"]
        runs += [summarise(path, shards) for path in sorted(config.parent.glob("run-*.json"))]
    runs.sort(key=lambda s: (s["policy"], s["batch"]))
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(runs, indent=1))

    print("Placement, worker time, tail and dispatch per run (worker busy = submit to finish).")
    table(
        ["policy", "batch", "ran", "wall s", "s/GB", "partitions per pool", "source GB per pool, max/mean",
         "tasks per worker", "busy % per worker", "gaps s per worker", "tail s", "remote decodes"],
        [
            [
                s["policy"], str(s["batch"]), "first" if s["order_in_batch"] == 0 else "second",
                f"{s['wall_s']:.0f}", f"{s['s_per_gb']:.2f}",
                f"{min(s['partitions_per_pool'])}-{max(s['partitions_per_pool'])}",
                f"{max(s['source_gb_per_pool']) / (s['source_gb'] / 8):.3f}",
                f"{min(w['tasks'] for w in s['workers'].values())}-{max(w['tasks'] for w in s['workers'].values())}",
                f"{min(100 * w['busy_s'] / s['wall_s'] for w in s['workers'].values()):.1f}-"
                f"{max(100 * w['busy_s'] / s['wall_s'] for w in s['workers'].values()):.1f}",
                f"{sum(w['gap_s'] for w in s['workers'].values()) / 8:.1f}",
                f"{s['tail_s']:.1f}", str(s["remote_decodes"]),
            ]
            for s in runs
        ],
    )
    print("Worker seconds per source GB, summed over the 8 workers (divide by 8 for wall).")
    keys = ("source_read", "frames_read", "remote_read", "write", "compute", "slack", "gaps")
    table(
        ["policy", "batch", *keys, "sum / 8", "s/GB"],
        [
            [s["policy"], str(s["batch"])]
            + [f"{s['summed_s_per_gb'][k]:.2f}" for k in keys]
            + [f"{sum(s['summed_s_per_gb'].values()) / 8:.2f}", f"{s['s_per_gb']:.2f}"]
            for s in runs
        ],
    )
    print("Storage rates, MB/s of stage bytes over stage seconds (local stages; cs only).")
    table(
        ["policy", "batch", "source read", "frames read", "decode write", "encode write"],
        [
            [s["policy"], str(s["batch"])] + [f"{v:.0f}" for v in s["mb_s"].values()]
            for s in runs
            if s["policy"] != "heft"
        ],
    )
    print("Local frames-read MB/s by tenth of the run (by submit time).")
    table(
        ["policy", "batch", *[f"{10 * k}-{10 * k + 10}%" for k in range(10)]],
        [
            [s["policy"], str(s["batch"])] + [f"{v:.0f}" for v in s["frames_read_mb_s_by_tenth"]]
            for s in runs
            if s["policy"] != "heft"
        ],
    )
    print("Local frames-read MB/s by node.")
    table(
        ["policy", "batch", *[f"n{n}" for n in range(8)]],
        [
            [s["policy"], str(s["batch"])] + [f"{v:.0f}" for v in s["frames_read_mb_s_by_node"]]
            for s in runs
            if s["policy"] != "heft"
        ],
    )
    print("Local frames-read MB/s by other read/write stages running on the host at its midpoint (reads).")
    table(
        ["policy", "batch", *[f"{low}-{high}" for low, high in CONCURRENCY_BINS]],
        [
            [s["policy"], str(s["batch"])]
            + [f"{v:.0f} ({n})" for v, n in s["frames_read_mb_s_by_concurrency"]]
            for s in runs
            if s["policy"] != "heft"
        ],
    )
    print("Local frames-read MB/s by seconds from the decode's completion to the read's start (reads).")
    table(
        ["policy", "batch", *[f"{low}-{high:.0f} s" if high < 1e9 else f"{low}+ s" for low, high in AGE_BINS]],
        [
            [s["policy"], str(s["batch"])] + [f"{v:.0f} ({n})" for v, n in s["frames_read_mb_s_by_age"]]
            for s in runs
            if s["policy"] != "heft"
        ],
    )
    disagg = {s["batch"]: s for s in runs if s["policy"] == "heft"}
    local = {s["batch"]: s for s in runs if s["policy"] == "data-local"}
    print("Speedup over disagg heft: measured, without data-local's tail, and at batch 0's s/GB.")
    table(
        ["batch", "measured", "tail removed", "data-local at batch-0 s/GB"],
        [
            [
                str(b),
                f"{disagg[b]['wall_s'] / local[b]['wall_s']:.2f}",
                f"{disagg[b]['wall_s'] / (local[b]['wall_s'] - local[b]['tail_s']):.2f}",
                f"{disagg[b]['wall_s'] / (local[0]['s_per_gb'] * local[b]['source_gb']):.2f}",
            ]
            for b in sorted(local)
        ],
    )


if __name__ == "__main__":
    main()
