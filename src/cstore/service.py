"""Background conversion service.

Runs on a storage node (as the ``cstore-service`` systemd unit) and loops:

  1. with ``--watch``, look for DROID shards in the dataset directory that
     have no converted output yet, and queue a conversion job for each;
  2. claim the oldest queued job (from any submitter) and run it in a worker
     container, exactly as a directly-run job would be;
  3. record the result and move the job to done/ or failed/.

So converting new data needs no manual step: load a shard, and the LeRobot
version appears under ``outputs/service/`` once the service gets to it.
"""

from __future__ import annotations

import signal
import socket
import time

from . import jobqueue, runner
from .config import Config
from .jobs import atomic_write_json, droid_shard_job

SERVICE_RUN_ID = "service"


class _Stop:
    requested = False


def _on_term(signum, frame):
    _Stop.requested = True


def shard_indices(cfg: Config) -> list[int]:
    if not (cfg.droid_version_dir / "dataset_info.json").exists():
        return []
    out = []
    for f in cfg.droid_version_dir.glob("droid_101-train.tfrecord-*-of-02048"):
        out.append(int(f.name.split("-")[2]))
    return sorted(out)


def enqueue_missing(cfg: Config, log=print) -> int:
    """Queue a conversion for every loaded shard that has no output yet."""
    known = jobqueue.known_job_ids(cfg)
    queued = 0
    for i in shard_indices(cfg):
        job_id = f"svc-shard-{i:05d}"
        output = f"outputs/{SERVICE_RUN_ID}/shard-{i:05d}"
        if job_id in known or (cfg.root / output).exists():
            continue
        jobqueue.submit(cfg, droid_shard_job(i, output, job_id=job_id))
        log(f"queued {job_id}: new shard {i} has no converted output")
        queued += 1
    return queued


def serve(cfg: Config, watch: bool = True, poll: float = 5.0, once: bool = False, log=print) -> None:
    previous = {sig: signal.signal(sig, _on_term) for sig in (signal.SIGTERM, signal.SIGINT)}
    _Stop.requested = False
    jobqueue.init(cfg)
    host = socket.gethostname()
    log(f"cstore service on {host}: root {cfg.root}, runtime {cfg.runtime}, watch={watch}, poll={poll}s")
    while not _Stop.requested:
        if watch:
            enqueue_missing(cfg, log)
        claimed = jobqueue.claim(cfg, worker=host)
        if claimed is None:
            jobqueue.write_heartbeat(cfg, {"state": "idle", "watch": watch})
            if once:
                break
            time.sleep(poll)
            continue

        job, running_path = claimed
        jobqueue.write_heartbeat(cfg, {"state": "running", "job_id": job.job_id, "watch": watch})
        log(f"running {job.job_id} ({job.function})")
        record = runner.run_job(cfg, job, mode="service", run_id=SERVICE_RUN_ID)
        record["queue_wait_s"] = round(record["started_at"] - job.submitted_at, 3)
        record["submitted_to_done_s"] = round(record["finished_at"] - job.submitted_at, 3)
        result_path = cfg.results / SERVICE_RUN_ID / f"{job.job_id}.json"
        atomic_write_json(result_path, record)
        jobqueue.finish(
            cfg,
            running_path,
            record["ok"],
            {"result": str(result_path), "wall_s": record["wall_s"], "host": host},
        )
        w = record.get("worker") or {}
        log(
            f"{'finished' if record['ok'] else 'FAILED'} {job.job_id}: wall {record['wall_s']:.1f} s, "
            f"submitted-to-done {record['submitted_to_done_s']:.1f} s, output {w.get('output_bytes', 0) / 1e6:.1f} MB"
        )
    jobqueue.write_heartbeat(cfg, {"state": "stopped", "watch": watch})
    for sig, handler in previous.items():
        signal.signal(sig, handler)
    log("cstore service stopped")
