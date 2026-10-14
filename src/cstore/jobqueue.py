"""A job queue kept as files on CephFS.

    queue/pending/<seq>-<job_id>.json    submitted, not yet claimed
    queue/running/<seq>-<job_id>.json    claimed by a worker
    queue/done/<seq>-<job_id>.json       finished successfully
    queue/failed/<seq>-<job_id>.json     finished with an error

A worker claims a job by renaming it from pending/ to running/. Rename is
atomic, so when several workers race for the same job exactly one rename
succeeds and the others get FileNotFoundError and move on. Any node that
mounts the file system can submit or watch jobs; no extra service is needed.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from .config import Config
from .jobs import Job, atomic_write_json

STATES = ("pending", "running", "done", "failed")


def _dir(cfg: Config, state: str) -> Path:
    return cfg.queue / state


def init(cfg: Config) -> None:
    for state in STATES:
        _dir(cfg, state).mkdir(parents=True, exist_ok=True)


def _entry_name(job: Job) -> str:
    return f"{int(job.submitted_at * 1000):013d}-{job.job_id}.json"


def submit(cfg: Config, job: Job) -> Path:
    init(cfg)
    if job.job_id in known_job_ids(cfg):
        raise ValueError(f"a job named {job.job_id!r} is already in the queue")
    path = _dir(cfg, "pending") / _entry_name(job)
    atomic_write_json(path, {"job": asdict(job), "state": {"submitted_at": job.submitted_at}})
    return path


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def claim(cfg: Config, worker: str | None = None) -> tuple[Job, Path] | None:
    """Claim the oldest pending job, or return None if there is none."""
    init(cfg)
    worker = worker or socket.gethostname()
    for name in sorted(os.listdir(_dir(cfg, "pending"))):
        if not name.endswith(".json") or name.startswith("."):
            continue
        src = _dir(cfg, "pending") / name
        dst = _dir(cfg, "running") / name
        try:
            os.rename(src, dst)
        except FileNotFoundError:
            continue  # another worker claimed it first
        entry = _read(dst)
        entry["state"].update({"claimed_by": worker, "claimed_at": time.time()})
        atomic_write_json(dst, entry)
        return Job.from_dict(entry["job"]), dst
    return None


def finish(cfg: Config, running_path: Path, ok: bool, info: dict[str, Any]) -> Path:
    entry = _read(running_path)
    entry["state"].update({"finished_at": time.time(), "ok": ok, **info})
    dst = _dir(cfg, "done" if ok else "failed") / running_path.name
    atomic_write_json(dst, entry)
    running_path.unlink()
    return dst


def requeue(cfg: Config, job_id: str) -> Path:
    """Move a failed or stuck job back to pending."""
    for state in ("failed", "running"):
        for name in os.listdir(_dir(cfg, state)):
            if name.endswith(f"-{job_id}.json"):
                src = _dir(cfg, state) / name
                entry = _read(src)
                entry["state"] = {"submitted_at": entry["state"].get("submitted_at"), "requeued_at": time.time()}
                dst = _dir(cfg, "pending") / name
                atomic_write_json(dst, entry)
                src.unlink()
                return dst
    raise FileNotFoundError(f"no failed or running job named {job_id!r}")


def entries(cfg: Config) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (state, entry) for every job, oldest first within each state."""
    for state in STATES:
        d = _dir(cfg, state)
        if not d.is_dir():
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".json") and not name.startswith("."):
                try:
                    yield state, _read(d / name)
                except (FileNotFoundError, json.JSONDecodeError):
                    continue  # moved or being rewritten while we listed


def known_job_ids(cfg: Config) -> set[str]:
    ids = set()
    for state in STATES:
        d = _dir(cfg, state)
        if d.is_dir():
            for name in os.listdir(d):
                if name.endswith(".json") and not name.startswith("."):
                    ids.add(name[14:-5])  # strip "<13-digit seq>-" and ".json"
    return ids


def write_heartbeat(cfg: Config, info: dict[str, Any]) -> None:
    atomic_write_json(cfg.queue / "heartbeat" / f"{socket.gethostname()}.json", {"time": time.time(), **info})


def heartbeats(cfg: Config) -> list[dict[str, Any]]:
    d = cfg.queue / "heartbeat"
    out = []
    if d.is_dir():
        for name in sorted(os.listdir(d)):
            if name.endswith(".json") and not name.startswith("."):
                try:
                    out.append({"host": name[:-5], **_read(d / name)})
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
    return out
