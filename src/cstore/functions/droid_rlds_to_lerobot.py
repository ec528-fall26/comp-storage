"""RLDS -> LeRobot conversion of DROID, using Hugging Face's own converter.

The transformation is ``examples/port_datasets/port_droid.py`` from LeRobot
v0.6.1, vendored unmodified in ``src/third_party/lerobot/``. Its SHA-256 is
checked before every run, so both execution modes provably run the same
converter. This module decides where it reads from and writes to, and fills
in episode metadata the public release lacks (below).

Missing episode metadata. ``port_droid.py`` reads seven per-episode fields,
but the public DROID 1.0.1 RLDS files (gs://gresearch/robotics/droid/1.0.1)
carry only one of them, ``file_path`` (their other field,
``recording_folderpath``, is not read). Before each episode reaches the
converter, the six missing ones are supplied:

* ``building`` and ``date`` are parsed from ``file_path``, which has the
  form ``.../<lab>/<success|failure>/<YYYY-MM-DD>/<timestamp>/trajectory.h5``;
* ``collector_id`` is left empty and the three camera extrinsics are zeros,
  because the RLDS files do not record them.

Fields already present are never touched, so a release that does carry them
converts exactly as Hugging Face's script would. Which fields were filled is
reported in the job result.

Runs inside the worker image (needs lerobot, tensorflow, tensorflow-datasets).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
from pathlib import Path
from types import ModuleType

from ..jobs import Job

PORT_DROID = Path(__file__).resolve().parents[2] / "third_party" / "lerobot" / "port_droid.py"
# sha256 of examples/port_datasets/port_droid.py at LeRobot tag v0.6.1
# (commit 7e241bd630a3719a56157a497ce5d08f244784f1).
PORT_DROID_SHA256 = "3af72c2ca6711cc7a1fd2c1774fda9ad2862a025e029e80a4051bab0c1312b71"

# Per-episode fields port_droid.py reads besides file_path, and what we
# supply when a release lacks them.
TEXT_METADATA = ("building", "collector_id", "date")
EXTRINSICS_METADATA = ("extrinsics_wrist_cam", "extrinsics_exterior_cam_1", "extrinsics_exterior_cam_2")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


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


def metadata_from_file_path(file_path: str) -> dict[str, str]:
    """Lab and date from a DROID trajectory path.

    ``gs://.../r2d2-data-full/AUTOLab/failure/2023-11-18/Sat_Nov_18_19:44:45_2023/trajectory.h5``
    gives ``{"building": "AUTOLab", "date": "2023-11-18"}``. Unrecognised
    paths give empty strings.
    """
    parts = file_path.split("/")
    for i, part in enumerate(parts):
        if part in ("success", "failure") and i >= 1:
            date = parts[i + 1] if i + 1 < len(parts) and _DATE.fullmatch(parts[i + 1]) else ""
            return {"building": parts[i - 1], "date": date}
    return {"building": "", "date": ""}


def fill_missing_metadata(port_droid: ModuleType) -> dict:
    """Wrap port_droid.generate_lerobot_frames so each episode it receives has
    every metadata field it reads. Returns a dict that counts what was filled."""
    import numpy as np
    import tensorflow as tf

    report = {"episodes": 0, "filled_fields": [], "file_path_unparsed": 0}
    original = port_droid.generate_lerobot_frames

    def generate_lerobot_frames(tf_episode):
        m = tf_episode["episode_metadata"]
        missing = [k for k in TEXT_METADATA + EXTRINSICS_METADATA if k not in m]
        report["episodes"] += 1
        if missing:
            m = dict(m)
            derived = metadata_from_file_path(m["file_path"].numpy().decode())
            if not derived["building"]:
                report["file_path_unparsed"] += 1
            for k in missing:
                if k in TEXT_METADATA:
                    m[k] = tf.constant(derived.get(k, ""))
                else:
                    m[k] = tf.constant(np.zeros(6, dtype=np.float64))
            report["filled_fields"] = sorted(set(report["filled_fields"]) | set(missing))
            tf_episode = {**tf_episode, "episode_metadata": m}
        return original(tf_episode)

    # port_droid.port_droid looks this name up in its module at call time.
    port_droid.generate_lerobot_frames = generate_lerobot_frames
    return report


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
    filled = fill_missing_metadata(port_droid)
    repo_id = f"cstore/{job.job_id}"
    port_droid.port_droid(
        raw_dir=raw_dir,
        repo_id=repo_id,
        push_to_hub=False,
        num_shards=num_shards,
        shard_index=shard_index,
    )
    if filled["filled_fields"]:
        logging.info(
            "supplied metadata missing from the input for %d episode(s): %s",
            filled["episodes"],
            ", ".join(filled["filled_fields"]),
        )

    out = lerobot_home / repo_id
    meta = json.loads((out / "meta" / "info.json").read_text())
    return out, {
        "converter": "lerobot v0.6.1 examples/port_datasets/port_droid.py",
        "converter_sha256": PORT_DROID_SHA256,
        "expected_episodes": expected,
        "episodes": meta["total_episodes"],
        "frames": meta["total_frames"],
        "metadata_filled": filled,
    }
