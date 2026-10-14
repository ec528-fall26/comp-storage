"""Run one job on this host, inside a resource-limited worker container.

The same image, CPU limit and memory limit are used on every node, so the
only thing that differs between execution modes is *where* the worker runs.
The container gets no network (``--network none``): it reaches its inputs
only through the host's CephFS mount, bind-mounted at the same path, so all
of its data movement is CephFS traffic that the host NIC counters see.

``CSTORE_RUNTIME=venv`` runs the worker as a plain process instead (no
isolation or limits); the offline smoke test and development use it.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from . import netcount
from .config import REPO_ROOT, Config
from .jobs import Job


def image_id(cfg: Config) -> str | None:
    if cfg.runtime != "docker":
        return None
    out = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", cfg.worker_image],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or None


def fs_type(path: Path) -> str | None:
    """File system type under ``path``: "ceph" (kernel client), "fuse.ceph", ..."""
    try:
        out = subprocess.run(["findmnt", "-no", "FSTYPE", "--target", str(path)], capture_output=True, text=True)
    except FileNotFoundError:  # no findmnt (macOS)
        return None
    return out.stdout.strip() or None


def worker_command(cfg: Config, work: Path, name: str) -> tuple[list[str], dict[str, str]]:
    worker_args = ["--root", str(cfg.root)]
    if cfg.runtime == "docker":
        args = [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",
            f"--cpus={cfg.worker_cpus}",
            f"--memory={cfg.worker_memory}", f"--memory-swap={cfg.worker_memory}",
            "--shm-size=1g",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{cfg.root}:{cfg.root}",
            "-v", f"{work}:/scratch",
            # The container runs as the host user, whose uid may have no passwd
            # entry in the image; USER/LOGNAME keep getpass.getuser() working.
            "-e", "HOME=/scratch", "-e", "USER=cstore", "-e", "LOGNAME=cstore",
            cfg.worker_image,
            "python", "-m", "cstore.worker",
            "--job-file", "/scratch/job.json", "--scratch", "/scratch/work", "--result", "/scratch/result.json",
            *worker_args,
        ]  # fmt: skip
        return args, dict(os.environ)
    if cfg.runtime == "venv":
        if not cfg.worker_python:
            raise RuntimeError("CSTORE_RUNTIME=venv needs WORKER_PYTHON (a python with the worker dependencies)")
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"), HOME=str(work), TF_CPP_MIN_LOG_LEVEL="3")
        args = [
            cfg.worker_python, "-m", "cstore.worker",
            "--job-file", str(work / "job.json"), "--scratch", str(work / "work"),
            "--result", str(work / "result.json"), *worker_args,
        ]  # fmt: skip
        return args, env
    raise ValueError(f"unknown CSTORE_RUNTIME {cfg.runtime!r} (use docker or venv)")


def _tail(path: Path, lines: int = 40) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-lines:]
    except FileNotFoundError:
        return []


def run_job(cfg: Config, job: Job, mode: str, run_id: str) -> dict:
    """Run ``job`` here and return a result record (not yet written anywhere)."""
    tag = uuid.uuid4().hex[:6]
    work = cfg.scratch_dir / "jobs" / f"{run_id}-{job.job_id}-{tag}"
    (work / "work").mkdir(parents=True)
    (work / "job.json").write_text(job.to_json())
    log_path = cfg.scratch_dir / "logs" / run_id / f"{job.job_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd, env = worker_command(cfg, work, f"cstore-{job.job_id}-{tag}")
    net_before = netcount.snapshot(cfg.net_iface)
    started = time.time()
    with open(log_path, "w") as log:
        rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env).returncode
    finished = time.time()
    net_after = netcount.snapshot(cfg.net_iface)

    result_file = work / "result.json"
    worker = json.loads(result_file.read_text()) if result_file.exists() else None
    ok = rc == 0 and bool(worker and worker.get("ok"))
    record = {
        "run_id": run_id,
        "mode": mode,
        "host": socket.gethostname(),
        "ok": ok,
        "job": asdict(job),
        "started_at": started,
        "finished_at": finished,
        "wall_s": round(finished - started, 3),
        "worker": worker,
        "net_this_host": netcount.delta(net_before, net_after),
        "runtime": {
            "kind": cfg.runtime,
            "image": cfg.worker_image if cfg.runtime == "docker" else None,
            "image_id": image_id(cfg),
            "cpus": cfg.worker_cpus if cfg.runtime == "docker" else None,
            "memory": cfg.worker_memory if cfg.runtime == "docker" else None,
        },
        "root_fs_type": fs_type(cfg.root),
        "code_rev": cfg.code_rev or None,
        "log": str(log_path),
        "exit_code": rc,
    }
    if not ok:
        record["log_tail"] = _tail(log_path)
    shutil.rmtree(work, ignore_errors=True)
    return record
