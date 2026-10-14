"""Run worker-side tools (verification, synthetic data) in the worker runtime."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import REPO_ROOT, Config


def run_module(cfg: Config, module: str, args: list[str], mounts: list[Path] = ()) -> int:
    if cfg.runtime == "docker":
        cmd = [
            "docker", "run", "--rm", "--network", "none",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "-e", "USER=cstore", "-e", "LOGNAME=cstore",
            "-v", f"{cfg.root}:{cfg.root}",
        ]  # fmt: skip
        for m in mounts:
            cmd += ["-v", f"{m}:{m}"]
        cmd += [cfg.worker_image, "python", "-m", module, *args]
        return subprocess.run(cmd).returncode
    if cfg.runtime == "venv":
        if not cfg.worker_python:
            raise RuntimeError("CSTORE_RUNTIME=venv needs WORKER_PYTHON")
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"), TF_CPP_MIN_LOG_LEVEL="3")
        return subprocess.run([cfg.worker_python, "-m", module, *args], env=env).returncode
    raise ValueError(f"unknown CSTORE_RUNTIME {cfg.runtime!r}")
