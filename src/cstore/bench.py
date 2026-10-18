"""Measure one execution mode: run DROID shard jobs and record runtime and
cross-host bytes.

    separate   the worker runs on the compute node (this host) and reads and
               writes CephFS over the network: the baseline.
    storage    the same job, same container image and limits, runs on the
               storage node.

Run from the compute node. Before each job both nodes drop their caches, so
every job starts cold; NIC counters are read on both nodes immediately
before and after the job. One storage node means every cross-host byte
passes through its NIC, so ``net_storage_node.total_bytes`` is the
cross-host traffic of the job, counted once.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from . import hosts, netcount, runner
from .config import Config
from .jobs import DROID_SHARD_TEMPLATE, atomic_write_json, droid_shard_job
from .worker import tree_size

MODES = ("separate", "storage")


def _storage_snapshot(cfg: Config) -> dict:
    return hosts.last_json_line(hosts.storage_cstore(cfg, ["netsnap"]).stdout)


def _cold(cfg: Config) -> None:
    hosts.drop_caches(osd=False)
    hosts.storage_cstore(cfg, ["drop-caches", "--osd"])


def preflight(cfg: Config, mode: str, shards: list[int]) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    remote = hosts.ssh(cfg, "hostname").stdout.strip()
    if remote == socket.gethostname():
        if os.environ.get("CSTORE_SINGLE_HOST_TEST") != "1":
            raise RuntimeError("run the benchmark from the compute node, not the storage node")
        print("WARNING: CSTORE_SINGLE_HOST_TEST=1: both 'nodes' are this host; the numbers mean nothing")
    missing = [i for i in shards if not (cfg.droid_version_dir / DROID_SHARD_TEMPLATE.format(index=i)).exists()]
    if missing:
        raise RuntimeError(f"shards {missing} are not loaded; run experiments/02_load_droid.sh first")
    active = hosts.storage_cstore(cfg, ["service-active"], check=False)
    if active.returncode == 0:
        raise RuntimeError(
            "the background service is running on the storage node and would compete for CPU; "
            "stop it first: ssh $STORAGE_HOST sudo systemctl stop cstore-service"
        )


def bench(cfg: Config, mode: str, shards: list[int], run_id: str, cold: bool = True, log=print) -> list[dict]:
    preflight(cfg, mode, shards)
    records = []
    for i in shards:
        job = droid_shard_job(i, output=f"outputs/{run_id}/{mode}/shard-{i:05d}")
        job_file = cfg.jobs / run_id / mode / f"{job.job_id}.json"
        job.save(job_file)
        in_bytes = sum(tree_size(cfg.root / p)[0] for p in job.inputs)
        log(f"[{mode}] {job.job_id}: {in_bytes / 1e6:.0f} MB of input; cache {'cold' if cold else 'warm'}")

        if cold:
            _cold(cfg)
        storage_before = _storage_snapshot(cfg)
        compute_before = netcount.snapshot(cfg.net_iface)

        if mode == "separate":
            record = runner.run_job(cfg, job, mode, run_id)
        else:
            out = hosts.storage_cstore(
                cfg, ["run", "--job-file", str(job_file), "--mode", mode, "--run-id", run_id, "--no-write"], check=False
            )
            try:
                record = hosts.last_json_line(out.stdout)
            except ValueError:
                raise RuntimeError(f"the job did not start on the storage node:\n{out.stderr[-3000:]}") from None

        compute_after = netcount.snapshot(cfg.net_iface)
        storage_after = _storage_snapshot(cfg)
        record["net_storage_node"] = netcount.delta(storage_before, storage_after)
        record["net_compute_node"] = netcount.delta(compute_before, compute_after)
        record["cache"] = "cold" if cold else "warm"
        record["input_bytes_declared"] = in_bytes

        result_file = cfg.results / run_id / mode / f"{job.job_id}.json"
        atomic_write_json(result_file, record)
        records.append(record)

        x = record["net_storage_node"]["total_bytes"]
        w = record.get("worker") or {}
        status = "ok" if record["ok"] else f"FAILED (log: {record.get('log')} on {record.get('host')})"
        log(
            f"[{mode}] {job.job_id}: {status}; wall {record['wall_s']:.1f} s "
            f"(convert {w.get('convert_s', 0):.1f} s, write-back {w.get('writeback_s', 0):.1f} s); "
            f"cross-host {x / 1e6:.1f} MB; output {w.get('output_bytes', 0) / 1e6:.1f} MB; "
            f"episodes {w.get('episodes')} of {w.get('expected_episodes')}"
        )
        log(f"[{mode}] result: {Path(result_file)}")
    return records
