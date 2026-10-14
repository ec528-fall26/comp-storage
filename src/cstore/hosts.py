"""Helpers for acting on this host and on the storage node over ssh."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .config import REPO_ROOT, Config


def ssh(cfg: Config, command: str, check: bool = True) -> subprocess.CompletedProcess:
    if not cfg.storage_host:
        raise RuntimeError("STORAGE_HOST is not set (see infra/cluster.env)")
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", cfg.storage_host, command],
        capture_output=True,
        text=True,
        check=check,
    )


def storage_cstore(cfg: Config, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run ``bin/cstore ARGS`` in the repository checkout on the storage node."""
    cmd = f"cd {shlex.quote(cfg.storage_repo)} && ./bin/cstore " + " ".join(shlex.quote(a) for a in args)
    return ssh(cfg, cmd, check=check)


def last_json_line(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"no JSON object in output:\n{text[-2000:]}")


def _sudo(cmd: list[str]) -> list[str]:
    return cmd if os.geteuid() == 0 else ["sudo", "-n", *cmd]


def ceph_cli() -> str | None:
    for name in ("microceph.ceph", "ceph"):
        if shutil.which(name):
            return name
    return None


def drop_caches(osd: bool = False) -> list[str]:
    """Empty the page cache (and, on the storage node, BlueStore's caches).

    Both execution modes start cold, so neither benefits from data a previous
    run left in memory: the kernel page cache holds CephFS file data on
    clients and loop-device data on the storage node, and each OSD keeps its
    own BlueStore cache.
    """
    done = []
    subprocess.run(["sync"], check=True)
    subprocess.run(_sudo(["sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"]), check=True)
    done.append("page cache")
    if osd:
        ceph = ceph_cli()
        if ceph is None:
            raise RuntimeError("no ceph CLI on this host; is this the storage node?")
        subprocess.run(_sudo([ceph, "tell", "osd.*", "cache", "drop"]), check=True, capture_output=True)
        done.append("osd caches")
    return done


def service_active() -> bool:
    if not shutil.which("systemctl"):
        return False
    r = subprocess.run(["systemctl", "is-active", "--quiet", "cstore-service"])
    return r.returncode == 0


def repo_relative(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)
