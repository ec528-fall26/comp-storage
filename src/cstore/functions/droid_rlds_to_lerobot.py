"""RLDS -> LeRobot conversion of DROID, using Hugging Face's own converter.

The transformation is ``examples/port_datasets/port_droid.py`` from LeRobot
v0.6.1, vendored unmodified in ``src/third_party/lerobot/``. Its SHA-256 is
checked before every run, so both execution modes provably run the same
converter. This module only decides where it reads from and writes to.

Runs inside the worker image (needs lerobot, tensorflow, tensorflow-datasets).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
from types import ModuleType

from ..jobs import Job

PORT_DROID = Path(__file__).resolve().parents[2] / "third_party" / "lerobot" / "port_droid.py"
# sha256 of examples/port_datasets/port_droid.py at LeRobot tag v0.6.1
# (commit 7e241bd630a3719a56157a497ce5d08f244784f1).
PORT_DROID_SHA256 = "3af72c2ca6711cc7a1fd2c1774fda9ad2862a025e029e80a4051bab0c1312b71"


def load_port_droid() -> ModuleType:
    digest = hashlib.sha256(PORT_DROID.read_bytes()).hexdigest()
    if digest != PORT_DROID_SHA256:
        raise RuntimeError(f"{PORT_DROID} was modified (sha256 {digest}); refusing to run a different converter")
    spec = importlib.util.spec_from_file_location("port_droid", PORT_DROID)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_episodes(dataset_info: Path, shard_index: int | None) -> int:
    """Episode count TensorFlow Datasets records for the shard (or dataset)."""
    info = json.loads(dataset_info.read_text())
    train = next(s for s in info["splits"] if s["name"] == "train")
    lengths = [int(x) for x in train["shardLengths"]]
    return sum(lengths) if shard_index is None else lengths[shard_index]


def run(job: Job, root: Path, scratch: Path) -> tuple[Path, dict]:
    lerobot_home = scratch / "lerobot"
    # LeRobot reads HF_LEROBOT_HOME at import time, so set it before loading.
    os.environ["HF_LEROBOT_HOME"] = str(lerobot_home)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    raw_dir = root / job.params["raw_dir"]
    shard_index = job.params.get("shard_index")
    num_shards = job.params.get("num_shards")
    expected = expected_episodes(raw_dir / "1.0.1" / "dataset_info.json", shard_index)

    port_droid = load_port_droid()
    repo_id = f"cstore/{job.job_id}"
    port_droid.port_droid(
        raw_dir=raw_dir,
        repo_id=repo_id,
        push_to_hub=False,
        num_shards=num_shards,
        shard_index=shard_index,
    )

    out = lerobot_home / repo_id
    meta = json.loads((out / "meta" / "info.json").read_text())
    return out, {
        "converter": "lerobot v0.6.1 examples/port_datasets/port_droid.py",
        "converter_sha256": PORT_DROID_SHA256,
        "expected_episodes": expected,
        "episodes": meta["total_episodes"],
        "frames": meta["total_frames"],
    }
