"""Job specifications.

A job is the unit the system schedules: one function applied to a declared
set of input files, producing one output directory. The declared inputs are
the job's *dependencies*; Demo 3's placement policy uses them to decide where
a job should run. All paths are relative to the CephFS root, so the same spec
means the same thing on every node.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DROID_FUNCTION = "droid_rlds_to_lerobot"
DROID_TOTAL_SHARDS = 2048
DROID_SHARD_TEMPLATE = "droid_101-train.tfrecord-{index:05d}-of-02048"


@dataclass
class Job:
    job_id: str
    function: str
    inputs: list[str]
    output: str
    params: dict[str, Any] = field(default_factory=dict)
    submitted_at: float = field(default_factory=time.time)
    submitted_by: str = field(default_factory=socket.gethostname)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)

    @classmethod
    def load(cls, path: Path) -> "Job":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def save(self, path: Path) -> None:
        atomic_write_text(path, self.to_json() + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    """Write a file so that readers see either nothing or the whole file.

    The rename is atomic on CephFS, as on any POSIX file system, so a reader
    on another node never observes a half-written job or result.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, path)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True) + "\n")


def droid_shard_job(
    shard_index: int,
    output: str,
    job_id: str | None = None,
    raw_dir: str = "datasets/droid/rlds",
) -> Job:
    """Convert one DROID 1.0.1 RLDS shard to a LeRobot dataset.

    Dependencies: the shard's record file plus the two small metadata files
    TensorFlow Datasets needs to open it.
    """
    version_dir = f"{raw_dir}/1.0.1"
    return Job(
        job_id=job_id or f"shard-{shard_index:05d}",
        function=DROID_FUNCTION,
        inputs=[
            f"{version_dir}/dataset_info.json",
            f"{version_dir}/features.json",
            f"{version_dir}/{DROID_SHARD_TEMPLATE.format(index=shard_index)}",
        ],
        output=output,
        params={
            "raw_dir": raw_dir,
            "num_shards": DROID_TOTAL_SHARDS,
            "shard_index": shard_index,
        },
    )


def whole_dataset_job(raw_dir: str, output: str, job_id: str) -> Job:
    """Convert every shard of a (small) dataset in one job; used by the smoke test."""
    version_dir = f"{raw_dir}/1.0.1"
    return Job(
        job_id=job_id,
        function=DROID_FUNCTION,
        inputs=[version_dir],
        output=output,
        params={"raw_dir": raw_dir, "num_shards": None, "shard_index": None},
    )
