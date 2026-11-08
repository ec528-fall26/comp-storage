"""Select whole DROID episodes by content, where the shards live (Demo 3 v0).

The job's ``params["request"]`` is the user's slice request (the same JSON the
client sends to ``POST /jobs``; see cstore_client.request). This function
scans every shard listed in ``job.inputs`` front to back, keeps the episodes
that match ``request["select"]``, and writes them as a new, smaller RLDS
dataset that ``tfds.builder_from_directory`` loads like the original:

    <output>/dataset_info.json                      copied, shard list rewritten
    <output>/features.json                          copied unchanged
    <output>/droid_101-train.tfrecord-00000-of-00001  kept episodes, original bytes
    <output>/manifest.json                          what was kept, and the byte counts

Kept episodes are copied byte for byte (stage 1): the schema is unchanged, so
the output is always loadable. Trimming inside episodes (cameras, frame
stride, idle frames) is stage 2; for now a non-default ``trim`` is accepted
and reported as not applied.

Matching (same rule as the client's mock): an episode matches if its
instructions contain any task word AND any object word, its lab is listed,
and its id is listed. An empty list means "any". Words match whole words,
with an optional plural "s"/"es" ("cup" matches "cups", not "cupboard").
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

OUT_SHARD = "droid_101-train.tfrecord-00000-of-00001"
INSTRUCTION_KEYS = (
    "steps/language_instruction",
    "steps/language_instruction_2",
    "steps/language_instruction_3",
)
DEFAULT_TRIM = {"drop_idle": False, "cameras": ["wrist", "exterior_1", "exterior_2"], "frame_stride": 1}


def episode_meta(record: bytes) -> dict[str, Any]:
    """Read the few fields selection needs from one serialized episode.

    A TFDS episode is one ``tf.train.Example``; nested step fields are stored
    flattened as ``steps/<name>`` with one value per step. Parsing the proto
    does not decode any images, so this is cheap next to reading the bytes.
    """
    import tensorflow as tf

    feats = tf.train.Example.FromString(record).features.feature

    def texts(key: str) -> list[str]:
        return [v.decode("utf-8", "replace") for v in feats[key].bytes_list.value] if key in feats else []

    instructions = []
    for key in INSTRUCTION_KEYS:
        first = next((t for t in texts(key) if t.strip()), "")
        if first and first not in instructions:
            instructions.append(first)

    file_path = (texts("episode_metadata/file_path") or [""])[0]
    parts = [p for p in file_path.split("/") if p]
    # .../<lab>/<success|failure>/<date>/<timestamp>/trajectory.h5
    outcome, lab = None, None
    for i, p in enumerate(parts):
        if p in ("success", "failure"):
            outcome, lab = p, parts[i - 1] if i else None
            break
    stamp = parts[-2] if len(parts) >= 2 else ""
    n_steps = len(feats["steps/is_first"].int64_list.value) if "steps/is_first" in feats else 0
    return {
        "episode_id": f"{lab}/{stamp}" if lab else stamp,
        "lab": lab,
        "outcome": outcome,
        "instructions": instructions,
        "steps": n_steps,
        "file_path": file_path,
        "bytes": len(record),
    }


def _has_word(text: str, words: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}(?:s|es)?\b", text) for w in words)


def matches(meta: dict[str, Any], select: dict[str, Any]) -> bool:
    text = " ".join(meta["instructions"]).lower()
    tasks = select.get("task_keywords") or []
    objects = select.get("object_keywords") or []
    labs = [l.lower() for l in select.get("labs") or []]
    ids = select.get("episode_ids") or []
    if tasks and not _has_word(text, tasks):
        return False
    if objects and not _has_word(text, objects):
        return False
    if labs and (meta["lab"] or "").lower() not in labs:
        return False
    if ids and not any(i == meta["episode_id"] or i in meta["file_path"] for i in ids):
        return False
    return True


def _rewrite_info(src: Path, dst: Path, kept: int, num_bytes: int) -> None:
    info = json.loads(src.read_text())
    train = next(sp for sp in info["splits"] if sp["name"] == "train")
    train["shardLengths"] = [str(kept)]
    train["numBytes"] = str(num_bytes)
    info["splits"] = [train]
    dst.write_text(json.dumps(info, indent=2) + "\n")


def run(job, root: Path, scratch: Path) -> tuple[Path, dict]:
    import tensorflow as tf

    request = job.params["request"]
    select = request.get("select") or {}
    trim = {**DEFAULT_TRIM, **(request.get("trim") or {})}
    cap = select.get("max_episodes")
    version_dir = root / job.params["dataset_dir"]
    shards = [root / p for p in job.inputs if ".tfrecord-" in p]

    warnings = []
    if trim["drop_idle"] or trim["frame_stride"] != 1 or sorted(trim["cameras"]) != sorted(DEFAULT_TRIM["cameras"]):
        warnings.append("trim (cameras / frame_stride / drop_idle) is not implemented yet; full episodes returned")

    out = scratch / "slice"
    out.mkdir(parents=True)
    kept: list[dict[str, Any]] = []
    scanned = 0
    shards_read: list[str] = []
    bytes_scanned = 0
    with tf.io.TFRecordWriter(str(out / OUT_SHARD)) as writer:
        for shard in shards:
            if cap is not None and len(kept) >= cap:
                break
            shards_read.append(shard.name)
            bytes_scanned += shard.stat().st_size
            for rec in tf.data.TFRecordDataset(str(shard)):
                raw = rec.numpy()
                meta = episode_meta(raw)
                scanned += 1
                if matches(meta, select):
                    writer.write(raw)
                    kept.append({**meta, "source_shard": shard.name})
                    if cap is not None and len(kept) >= cap:
                        break

    out_bytes = (out / OUT_SHARD).stat().st_size
    _rewrite_info(version_dir / "dataset_info.json", out / "dataset_info.json", len(kept), out_bytes)
    shutil.copy(version_dir / "features.json", out / "features.json")

    summary = {
        "episodes_scanned": scanned,
        "episodes_kept": len(kept),
        "shards_available": len(shards),
        "shards_read": len(shards_read),
        "bytes_scanned": bytes_scanned,
        "bytes_out": out_bytes,
        "steps_kept": sum(k["steps"] for k in kept),
        "warnings": warnings,
    }
    manifest = {"job_id": job.job_id, "request": request, **summary, "shards": shards_read, "episodes": kept}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return out, summary
