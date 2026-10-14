"""Command-line interface: ``bin/cstore <command> ...``.

Host-side commands use only the Python standard library, so they run with
the system python3 on any node. Commands that need the conversion stack
(verification, synthetic data) run inside the worker container.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import config, hosts, jobqueue, netcount


def _shards(values: list[str] | None, cfg: config.Config) -> list[int]:
    if not values:
        return list(cfg.droid_shards)
    return [int(x) for v in values for x in v.replace(",", " ").split()]


def cmd_check(a, cfg):
    from .check import run_checks

    return 0 if run_checks(cfg) else 1


def cmd_config(a, cfg):
    from dataclasses import asdict

    print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in asdict(cfg).items()}, sort_keys=True))


def cmd_netsnap(a, cfg):
    print(json.dumps(netcount.snapshot(cfg.net_iface)))


def cmd_drop_caches(a, cfg):
    print("dropped: " + ", ".join(hosts.drop_caches(osd=a.osd)))


def cmd_service_active(a, cfg):
    return 0 if hosts.service_active() else 3


def cmd_run(a, cfg):
    from .jobs import Job, atomic_write_json
    from .runner import run_job

    job = Job.load(a.job_file)
    record = run_job(cfg, job, a.mode, a.run_id)
    if not a.no_write:
        atomic_write_json(cfg.results / a.run_id / a.mode / f"{job.job_id}.json", record)
    print(json.dumps(record, sort_keys=True))
    return 0 if record["ok"] else 1


def cmd_bench(a, cfg):
    from .bench import bench

    records = bench(cfg, a.mode, _shards(a.shards, cfg), a.run_id, cold=not a.warm)
    return 0 if all(r["ok"] for r in records) else 1


def cmd_verify(a, cfg):
    from .workertools import run_module

    args = [str(a.dataset)]
    if a.expect_episodes is not None:
        args += ["--expect-episodes", str(a.expect_episodes)]
    if a.against:
        args += ["--against", str(a.against)]
    if a.json:
        args += ["--json", str(a.json)]
    return run_module(cfg, "cstore.verify", args)


def cmd_verify_run(a, cfg):
    """Verify every output of a benchmark run and compare the two modes."""
    from .workertools import run_module

    base = cfg.results / a.run_id
    outputs: dict[str, dict[str, dict]] = {}
    failures = 0
    for mode in ("separate", "storage"):
        for p in sorted((base / mode).glob("*.json")):
            rec = json.loads(p.read_text())
            job_id = rec["job"]["job_id"]
            if not rec.get("ok"):
                print(f"[{mode}] {job_id}: job failed, nothing to verify")
                failures += 1
                continue
            out = cfg.root / rec["job"]["output"]
            outputs.setdefault(job_id, {})[mode] = rec
            print(f"[{mode}] {job_id}:")
            rc = run_module(
                cfg,
                "cstore.verify",
                [str(out), "--expect-episodes", str(rec["worker"]["expected_episodes"]),
                 "--json", str(base / "verify" / f"{mode}-{job_id}.json")],
            )  # fmt: skip
            failures += rc != 0
    for job_id, by_mode in sorted(outputs.items()):
        if set(by_mode) == {"separate", "storage"}:
            print(f"[compare] {job_id}: separate vs storage")
            rc = run_module(
                cfg,
                "cstore.verify",
                [str(cfg.root / by_mode["separate"]["job"]["output"]),
                 "--against", str(cfg.root / by_mode["storage"]["job"]["output"]),
                 "--no-decode", "--json", str(base / "verify" / f"compare-{job_id}.json")],
            )  # fmt: skip
            failures += rc != 0
    return 1 if failures else 0


def cmd_submit(a, cfg):
    from .jobs import Job, droid_shard_job

    if a.job_file:
        job = Job.load(a.job_file)
    else:
        job = droid_shard_job(
            a.shard,
            output=a.output or f"outputs/submitted/shard-{a.shard:05d}-{int(time.time())}",
            job_id=a.job_id or f"sub-shard-{a.shard:05d}-{int(time.time())}",
        )
    path = jobqueue.submit(cfg, job)
    print(f"submitted {job.job_id} -> {path}")


def _ago(t: float | None) -> str:
    if not t:
        return "-"
    s = time.time() - t
    return f"{s:.0f}s ago" if s < 120 else f"{s / 60:.0f}m ago"


def cmd_status(a, cfg):
    rows = list(jobqueue.entries(cfg))
    counts = {s: 0 for s in jobqueue.STATES}
    for state, _ in rows:
        counts[state] += 1
    print("queue: " + "  ".join(f"{s} {n}" for s, n in counts.items()))
    for hb in jobqueue.heartbeats(cfg):
        print(f"service on {hb['host']}: {hb.get('state')} {hb.get('job_id', '')} (heartbeat {_ago(hb.get('time'))})")
    active = [(s, e) for s, e in rows if s in ("pending", "running")]
    finished = sorted(
        ((s, e) for s, e in rows if s in ("done", "failed")), key=lambda r: r[1]["state"].get("finished_at") or 0
    )
    for state, e in active + finished[-a.limit :]:
        job, st = e["job"], e["state"]
        extra = ""
        if state in ("done", "failed"):
            extra = f"wall {st.get('wall_s', 0):.1f} s on {st.get('host', '?')}, finished {_ago(st.get('finished_at'))}"
        elif state == "running":
            extra = f"on {st.get('claimed_by')}, started {_ago(st.get('claimed_at'))}"
        else:
            extra = f"submitted {_ago(st.get('submitted_at'))} by {job.get('submitted_by')}"
        print(f"  {state:8s} {job['job_id']:28s} -> {job['output']}  {extra}")


def cmd_requeue(a, cfg):
    print(f"requeued -> {jobqueue.requeue(cfg, a.job_id)}")


def cmd_service(a, cfg):
    from .service import serve

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    serve(cfg, watch=a.watch, poll=a.poll, once=a.once, log=log)


def cmd_load_droid(a, cfg):
    from .loader import load

    summary = load(cfg, _shards(a.shards, cfg))
    eps = ", ".join(f"shard {k}: {v} episodes" for k, v in summary["episodes_per_shard"].items())
    print(f"done: {summary['downloaded_bytes'] / 1e6:.0f} MB downloaded in {summary['seconds']:.0f} s ({eps})")


def cmd_report(a, cfg):
    from .report import summarize

    text, _ = summarize(cfg, a.run_id)
    print(text)


def cmd_synth(a, cfg):
    from .workertools import run_module

    a.out.mkdir(parents=True, exist_ok=True)
    return run_module(
        cfg,
        "cstore.functions.synthetic_droid",
        ["--out", str(a.out), "--shards", *map(str, _shards(a.shards, cfg)), "--episodes-per-shard", str(a.episodes)],
        mounts=[] if str(a.out.resolve()).startswith(str(cfg.root)) else [a.out.resolve()],
    )


def cmd_smoke(a, cfg):
    """Offline end-to-end check on one machine, with synthetic data.

    Same layout, job specs, converter and verification as the real
    experiments; only the bytes are synthetic and nothing leaves this host.
    """
    from .jobs import atomic_write_json, droid_shard_job
    from .runner import run_job
    from .service import serve
    from .workertools import run_module

    import shutil

    run_id = f"smoke-{time.strftime('%Y%m%d-%H%M%S')}"
    shards = [2, 8]
    marker = cfg.root / ".cstore-smoke"
    if cfg.root.is_dir() and any(cfg.root.iterdir()) and not marker.exists():
        print(f"refusing to use {cfg.root}: it is not empty and was not made by the smoke test")
        return 1
    if marker.exists():  # start from scratch so the run is self-contained
        for child in cfg.root.iterdir():
            if child != marker:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
    cfg.root.mkdir(parents=True, exist_ok=True)
    marker.touch()
    print(f"working in {cfg.root} (a local directory standing in for CephFS)")

    print(f"[1/4] writing synthetic shards {shards} ({a.episodes} episodes each) with the DROID 1.0.1 layout")
    cfg.droid_raw_dir.mkdir(parents=True, exist_ok=True)
    rc = run_module(
        cfg,
        "cstore.functions.synthetic_droid",
        ["--out", str(cfg.droid_raw_dir), "--shards", *map(str, shards), "--episodes-per-shard", str(a.episodes)],
    )
    if rc:
        return 1

    print(f"[2/4] converting shard {shards[0]} directly, with port_droid.py in the worker runtime ({cfg.runtime})")
    job = droid_shard_job(shards[0], output=f"outputs/{run_id}/direct/shard-{shards[0]:05d}")
    rec = run_job(cfg, job, "direct", run_id)
    atomic_write_json(cfg.results / run_id / "direct" / f"{job.job_id}.json", rec)
    if not rec["ok"]:
        print("conversion FAILED; last log lines:\n  " + "\n  ".join(rec.get("log_tail", [])))
        return 1
    w = rec["worker"]
    print(
        f"      {w['episodes']} episodes, {w['frames']} frames; {w['input_bytes'] / 1e6:.1f} MB in, "
        f"{w['output_bytes'] / 1e6:.2f} MB out; convert {w['convert_s']:.1f} s, write-back {w['writeback_s']:.2f} s"
    )

    print("[3/4] background service: queue a job per unconverted shard, run them, exit when idle")
    serve(cfg, watch=True, poll=1.0, once=True, log=lambda m: print(f"      {m}"))

    print("[4/4] verifying outputs through LeRobot's loader, and direct vs service for the same shard")
    vdir = cfg.results / run_id / "verify"
    failures = 0
    for i in shards:
        out = cfg.root / "outputs" / "service" / f"shard-{i:05d}"
        failures += run_module(
            cfg, "cstore.verify", [str(out), "--expect-episodes", str(a.episodes), "--json", str(vdir / f"service-{i}.json")]
        )
    failures += run_module(
        cfg,
        "cstore.verify",
        [str(cfg.root / job.output), "--expect-episodes", str(a.episodes),
         "--against", str(cfg.root / "outputs" / "service" / f"shard-{shards[0]:05d}"),
         "--json", str(vdir / "direct-vs-service.json")],
    )  # fmt: skip
    print("SMOKE TEST PASSED" if not failures else "SMOKE TEST FAILED")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cstore", description="Computational storage over CephFS (EC528).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", help="check the two-node setup (run on the compute node)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("config", help="print the effective settings as JSON")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("netsnap", help="print this host's NIC byte counters as JSON")
    s.set_defaults(fn=cmd_netsnap)

    s = sub.add_parser("drop-caches", help="drop the page cache (and OSD caches with --osd)")
    s.add_argument("--osd", action="store_true")
    s.set_defaults(fn=cmd_drop_caches)

    s = sub.add_parser("service-active", help="exit 0 if the cstore-service unit is running here")
    s.set_defaults(fn=cmd_service_active)

    s = sub.add_parser("run", help="run one job on this host")
    s.add_argument("--job-file", type=Path, required=True)
    s.add_argument("--mode", default="manual")
    s.add_argument("--run-id", default="manual")
    s.add_argument("--no-write", action="store_true", help="print the record but do not save it")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("bench", help="measure one execution mode (run on the compute node)")
    s.add_argument("--mode", choices=["separate", "storage"], required=True)
    s.add_argument("--run-id", required=True)
    s.add_argument("--shards", nargs="*")
    s.add_argument("--warm", action="store_true", help="do not drop caches before each job")
    s.set_defaults(fn=cmd_bench)

    s = sub.add_parser("verify", help="verify one output dataset (in the worker runtime)")
    s.add_argument("dataset", type=Path)
    s.add_argument("--expect-episodes", type=int)
    s.add_argument("--against", type=Path)
    s.add_argument("--json", type=Path)
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("verify-run", help="verify all outputs of a benchmark run and compare modes")
    s.add_argument("--run-id", required=True)
    s.set_defaults(fn=cmd_verify_run)

    s = sub.add_parser("submit", help="queue a job for the background service")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--shard", type=int)
    g.add_argument("--job-file", type=Path)
    s.add_argument("--job-id")
    s.add_argument("--output")
    s.set_defaults(fn=cmd_submit)

    s = sub.add_parser("status", help="show the job queue and service heartbeats")
    s.add_argument("--limit", type=int, default=20, help="how many finished jobs to list")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("requeue", help="move a failed or stuck job back to pending")
    s.add_argument("job_id")
    s.set_defaults(fn=cmd_requeue)

    s = sub.add_parser("service", help="run the background conversion service in the foreground")
    s.add_argument("--watch", action="store_true", help="queue conversions for newly loaded shards")
    s.add_argument("--poll", type=float, default=5.0)
    s.add_argument("--once", action="store_true", help="exit when the queue is empty")
    s.set_defaults(fn=cmd_service)

    s = sub.add_parser("load-droid", help="download DROID 1.0.1 shards into CephFS (checksummed)")
    s.add_argument("--shards", nargs="*")
    s.set_defaults(fn=cmd_load_droid)

    s = sub.add_parser("report", help="summarise a benchmark run")
    s.add_argument("--run-id", required=True)
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("synth", help="write a small synthetic dataset with the DROID 1.0.1 layout")
    s.add_argument("--out", type=Path, required=True, help="raw dir; files go in <out>/1.0.1")
    s.add_argument("--shards", nargs="*")
    s.add_argument("--episodes", type=int, default=3, help="episodes per shard")
    s.set_defaults(fn=cmd_synth)

    s = sub.add_parser("smoke", help="offline end-to-end check on this machine (synthetic data)")
    s.add_argument("--episodes", type=int, default=3, help="episodes per synthetic shard")
    s.set_defaults(fn=cmd_smoke)
    return p


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with child output
    a = build_parser().parse_args(argv)
    cfg = config.load()
    rc = a.fn(a, cfg)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
