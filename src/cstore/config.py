"""Configuration: environment variables, optionally loaded from an env file.

Every setting can be given as an environment variable. Values not set in the
environment are read from ``infra/cluster.env`` (or the file named by
``CSTORE_ENV``), which the setup scripts write. Environment variables win.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULTS = {
    # CephFS directory that holds everything the system reads and writes.
    "CSTORE_ROOT": "/mnt/cephfs/cstore",
    # ssh destination of the storage node, as seen from the compute node.
    "STORAGE_HOST": "",
    # Path of this repository on the storage node.
    "STORAGE_REPO": "",
    # How workers run: "docker" (default) or "venv" (no isolation; smoke tests).
    "CSTORE_RUNTIME": "docker",
    "WORKER_IMAGE": "cstore-worker:demo2",
    "WORKER_CPUS": "3",
    "WORKER_MEMORY": "8g",
    # Python with the worker dependencies, for CSTORE_RUNTIME=venv.
    "WORKER_PYTHON": "",
    # Local (non-CephFS) disk for intermediate files.
    "SCRATCH_DIR": "/var/tmp/cstore",
    # Network interface whose counters are recorded; empty = default route.
    "NET_IFACE": "",
    # DROID 1.0.1 shard indices used by the experiments (shard 2: 649 MB).
    "DROID_SHARDS": "2",
    # "storage" or "compute"; written by the setup scripts.
    "CSTORE_ROLE": "",
    # Code version deployed to the nodes (git describe), recorded in results.
    "CSTORE_CODE_REV": "",
}


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        parts = shlex.split(value, comments=True)
        values[key] = " ".join(parts)
    return values


@dataclass(frozen=True)
class Config:
    root: Path
    storage_host: str
    storage_repo: str
    runtime: str
    worker_image: str
    worker_cpus: str
    worker_memory: str
    worker_python: str
    scratch_dir: Path
    net_iface: str
    droid_shards: tuple[int, ...]
    role: str
    code_rev: str
    env_file: Path

    # Directory layout under the CephFS root -------------------------------
    @property
    def datasets(self) -> Path:
        return self.root / "datasets"

    @property
    def droid_raw_dir(self) -> Path:
        """Directory handed to port_droid.py as --raw-dir.

        TensorFlow Datasets resolves ``tfds.builder("droid/rlds", ...)`` by
        looking for ``droid/rlds/<version>/dataset_info.json``, so the
        published files live one level below, in ``droid/rlds/1.0.1``.
        """
        return self.datasets / "droid" / "rlds"

    @property
    def droid_version_dir(self) -> Path:
        return self.droid_raw_dir / "1.0.1"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def results(self) -> Path:
        return self.root / "results"

    @property
    def queue(self) -> Path:
        return self.root / "queue"


def load() -> Config:
    env_file = Path(os.environ.get("CSTORE_ENV", REPO_ROOT / "infra" / "cluster.env"))
    file_values = _read_env_file(env_file)

    def get(key: str) -> str:
        if key in os.environ:
            return os.environ[key]
        return file_values.get(key, DEFAULTS[key])

    shards = tuple(int(s) for s in get("DROID_SHARDS").replace(",", " ").split())
    return Config(
        root=Path(get("CSTORE_ROOT")),
        storage_host=get("STORAGE_HOST"),
        storage_repo=get("STORAGE_REPO") or str(REPO_ROOT),
        runtime=get("CSTORE_RUNTIME"),
        worker_image=get("WORKER_IMAGE"),
        worker_cpus=get("WORKER_CPUS"),
        worker_memory=get("WORKER_MEMORY"),
        worker_python=get("WORKER_PYTHON"),
        scratch_dir=Path(get("SCRATCH_DIR")),
        net_iface=get("NET_IFACE"),
        droid_shards=shards,
        role=get("CSTORE_ROLE"),
        code_rev=get("CSTORE_CODE_REV"),
        env_file=env_file,
    )
