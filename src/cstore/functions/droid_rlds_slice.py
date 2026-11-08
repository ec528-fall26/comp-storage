"""Keep the DROID episodes whose instruction contains all the given keywords.

Runs where the shards are stored. The job's ``params`` carry the request:

    {"keywords": ["put", "mug"], "max_episodes": 20}

Every shard in ``job.inputs`` is read front to back (RLDS has no index, so
there is no shortcut). An episode is kept if one of its instructions contains
ALL the keywords as whole words, ignoring case. Kept episodes are copied byte
for byte into a new, smaller RLDS dataset that loads like the original:

    <output>/droid_101-train.tfrecord-00000-of-00001   the kept episodes
    <output>/features.json                            copied: episodes are unchanged
    <output>/dataset_info.json                        copied, shard list rewritten
    <output>/manifest.json                            what was kept, bytes in and out

If nothing matches, only manifest.json is written.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

OUT_SHARD = "droid_101-train.tfrecord-00000-of-00001"
INSTRUCTION_KEYS = ("steps/language_instruction", "steps/language_instruction_2", "steps/language_instruction_3")


def episode_info(record: bytes) -> dict:
    """The fields selection needs, read without decoding any images."""
    import tensorflow as tf

    feats = tf.train.Example.FromString(record).features.feature
    instructions = set()
    for key in INSTRUCTION_KEYS:
        if key in feats:
            instructions.update(v.decode("utf-8", "replace") for v in feats[key].bytes_list.value)
    instructions.discard("")
    return {
        "file_path": feats["episode_metadata/file_path"].bytes_list.value[0].decode(),
        "instructions": sorted(instructions),
    }


def matches(instructions: list[str], keywords: list[str]) -> bool:
    patterns = [re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in keywords]
    return any(all(p.search(text) for p in patterns) for text in instructions)


def run(job, root: Path, scratch: Path) -> tuple[Path, dict]:
    import tensorflow as tf

    keywords = job.params["keywords"]
    cap = job.params["max_episodes"]
    shards = [root / p for p in job.inputs if ".tfrecord-" in p]
    dataset_dir = shards[0].parent

    out = scratch / "slice"
    out.mkdir(parents=True)
    kept, scanned, bytes_scanned = [], 0, 0
    with tf.io.TFRecordWriter(str(out / OUT_SHARD)) as writer:
        for shard in shards:
            if len(kept) >= cap:
                break
            bytes_scanned += shard.stat().st_size
            for record in tf.data.TFRecordDataset(str(shard)):
                raw = record.numpy()
                scanned += 1
                info = episode_info(raw)
                if matches(info["instructions"], keywords):
                    writer.write(raw)
                    kept.append({**info, "shard": shard.name})
                    if len(kept) >= cap:
                        break

    bytes_out = (out / OUT_SHARD).stat().st_size
    if kept:
        shutil.copy(dataset_dir / "features.json", out / "features.json")
        info = json.loads((dataset_dir / "dataset_info.json").read_text())
        train = next(s for s in info["splits"] if s["name"] == "train")
        train.update(shardLengths=[str(len(kept))], numBytes=str(bytes_out))
        info["splits"] = [train]
        (out / "dataset_info.json").write_text(json.dumps(info, indent=2) + "\n")
    else:
        (out / OUT_SHARD).unlink()  # TFDS cannot load an empty dataset; leave only the manifest

    summary = {"episodes_scanned": scanned, "episodes_kept": len(kept),
               "bytes_scanned": bytes_scanned, "bytes_out": bytes_out}  # fmt: skip
    manifest = {"job_id": job.job_id, "keywords": keywords, "max_episodes": cap, **summary, "episodes": kept}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return out, summary
