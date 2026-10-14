"""Worker entry point: run one job and publish its output.

    python -m cstore.worker --job-file JOB.json --root /mnt/cephfs/cstore \
        --scratch /scratch --result /scratch/result.json

Runs inside the worker container (or a venv with the worker dependencies).
Three phases, each timed:

  1. convert    the job's function reads its inputs from CephFS and writes
                its output to local scratch disk;
  2. write-back the finished output is copied to CephFS (each file fsynced),
                then published with a single rename, so readers never see a
                partial dataset;
  3. report     a small JSON result is left in scratch for the caller.

Converting into local scratch and publishing at the end is deliberate:
LeRobot stages every frame as a PNG and every episode as a temporary MP4
under the dataset root before assembling the final files. Pointing that root
at CephFS would move several times the final output size over the network
in the separate-compute mode. Hugging Face's own pipeline likewise converts
locally and uploads the finished dataset.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import socket
import sys
import time
import traceback
import uuid
from pathlib import Path

from .functions import resolve
from .jobs import Job


def tree_size(path: Path) -> tuple[int, int]:
    if path.is_file():
        return path.stat().st_size, 1
    total = files = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            total += (Path(dirpath) / name).stat().st_size
            files += 1
    return total, files


def publish_tree(src: Path, dst: Path) -> dict:
    """Copy ``src`` to ``dst`` on CephFS and make it visible atomically."""
    if dst.exists():
        raise FileExistsError(f"output {dst} already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.partial-{uuid.uuid4().hex[:8]}")
    total = files = 0
    for dirpath, _, filenames in os.walk(src):
        rel = Path(dirpath).relative_to(src)
        (tmp / rel).mkdir(parents=True, exist_ok=True)
        for name in filenames:
            s, d = Path(dirpath) / name, tmp / rel / name
            with open(s, "rb") as fi, open(d, "wb") as fo:
                shutil.copyfileobj(fi, fo, length=8 << 20)
                fo.flush()
                os.fsync(fo.fileno())
            total += s.stat().st_size
            files += 1
    os.rename(tmp, dst)
    return {"bytes": total, "files": files}


def _cpu() -> dict:
    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "user_s": round(me.ru_utime + kids.ru_utime, 2),
        "sys_s": round(me.ru_stime + kids.ru_stime, 2),
        "max_rss_mb": round(max(me.ru_maxrss, kids.ru_maxrss) / 1024, 1),
    }


def execute(job: Job, root: Path, scratch: Path) -> dict:
    result: dict = {"job_id": job.job_id, "function": job.function, "host": socket.gethostname()}
    inputs = [root / p for p in job.inputs]
    missing = [str(p) for p in inputs if not p.exists()]
    if missing:
        raise FileNotFoundError(f"missing inputs: {missing}")
    sizes = [tree_size(p) for p in inputs]
    result["input_bytes"] = sum(s for s, _ in sizes)
    result["input_files"] = sum(n for _, n in sizes)

    fn = resolve(job.function)
    t0 = time.time()
    local_out, info = fn(job, root, scratch)
    t1 = time.time()
    published = publish_tree(local_out, root / job.output)
    t2 = time.time()

    result.update(info)
    result.update(
        {
            "output": job.output,
            "output_bytes": published["bytes"],
            "output_files": published["files"],
            "convert_s": round(t1 - t0, 3),
            "writeback_s": round(t2 - t1, 3),
            "cpu": _cpu(),
        }
    )
    shutil.rmtree(local_out, ignore_errors=True)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run one cstore job.")
    p.add_argument("--job-file", type=Path, required=True)
    p.add_argument("--root", type=Path, required=True, help="CephFS root (CSTORE_ROOT)")
    p.add_argument("--scratch", type=Path, required=True, help="local scratch directory")
    p.add_argument("--result", type=Path, required=True, help="where to write the result JSON")
    a = p.parse_args(argv)

    job = Job.load(a.job_file)
    a.scratch.mkdir(parents=True, exist_ok=True)
    try:
        result = {"ok": True, **execute(job, a.root, a.scratch)}
        code = 0
    except Exception as exc:  # report every failure in the result file
        traceback.print_exc()
        result = {"ok": False, "job_id": job.job_id, "error": f"{type(exc).__name__}: {exc}"}
        code = 1
    a.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
