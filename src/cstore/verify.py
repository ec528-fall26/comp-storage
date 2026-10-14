"""Verify a converted LeRobot dataset, and compare two of them.

    python -m cstore.verify DATASET_DIR [--expect-episodes N]
                            [--against OTHER_DATASET_DIR] [--json OUT.json]

Checks on one dataset:
  * metadata loads, and the episode count equals what TensorFlow Datasets
    records for the input shard (independent of the converter);
  * frame counts agree across the episode index, the data parquet files, and
    every camera's video files (counted packet by packet);
  * the dataset is readable through LeRobot's own loader: the first and last
    frame are decoded from every camera.

Comparison of two datasets (e.g. the separate-compute and storage-node runs):
  * every file is compared by SHA-256; JSON files are compared after
    canonicalising key order, because LeRobot writes stats.json keys in
    whatever order its parallel encoders finish;
  * if any file differs, the report says which, plus whether the decoded
    content (episodes, frames, tasks, numeric columns) still matches.

Runs inside the worker image (needs lerobot, pyarrow, av).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from pathlib import Path


def canonical_sha256(path: Path) -> str:
    if path.suffix == ".json":
        data = json.dumps(json.loads(path.read_text()), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _video_frames(paths: list[str]) -> int:
    import av

    frames = 0
    for p in paths:
        with av.open(p) as c:
            stream = c.streams.video[0]
            frames += sum(1 for pkt in c.demux(stream) if pkt.size > 0)
    return frames


def _table_digest(paths: list[str], columns: list[str]) -> str:
    """Order-preserving digest of selected parquet columns (decoded values)."""
    import pyarrow.parquet as pq

    h = hashlib.sha256()
    for p in sorted(paths):
        t = pq.read_table(p, columns=columns)
        for col in columns:
            h.update(col.encode())
            h.update(json.dumps(t.column(col).to_pylist(), default=str).encode())
    return h.hexdigest()


def fingerprint(root: Path, decode: bool = True) -> dict:
    import pyarrow.parquet as pq

    info = json.loads((root / "meta" / "info.json").read_text())
    fp: dict = {
        "path": str(root),
        "codebase_version": info.get("codebase_version"),
        "fps": info.get("fps"),
        "total_episodes": info["total_episodes"],
        "total_frames": info["total_frames"],
        "total_tasks": info.get("total_tasks"),
    }

    files = {}
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = Path(dirpath) / n
            files[str(p.relative_to(root))] = canonical_sha256(p)
    fp["files"] = dict(sorted(files.items()))
    fp["bytes"] = sum((root / f).stat().st_size for f in files)

    ep_files = sorted(glob.glob(str(root / "meta" / "episodes" / "*" / "*.parquet")))
    episodes = []
    for p in ep_files:
        episodes += pq.read_table(p, columns=["episode_index", "length", "tasks"]).to_pylist()
    fp["episode_lengths"] = [e["length"] for e in sorted(episodes, key=lambda e: e["episode_index"])]

    data_files = sorted(glob.glob(str(root / "data" / "*" / "*.parquet")))
    fp["data_rows"] = sum(pq.ParquetFile(p).metadata.num_rows for p in data_files)
    schema_cols = pq.ParquetFile(data_files[0]).schema_arrow.names if data_files else []
    fp["data_digest"] = _table_digest(data_files, schema_cols) if data_files else None
    fp["tasks_digest"] = _table_digest([str(root / "meta" / "tasks.parquet")], ["task_index", "task"])

    video_keys = [k for k, v in info["features"].items() if v.get("dtype") == "video"]
    fp["video_frames"] = {
        k: _video_frames(sorted(glob.glob(str(root / "videos" / k / "*" / "*.mp4")))) for k in video_keys
    }

    if decode:
        fp["decoded"] = _decode_check(root, info["total_frames"], video_keys)
    return fp


def _decode_check(root: Path, total_frames: int, video_keys: list[str]) -> dict:
    """Open the dataset with LeRobot's loader and decode first/last frames."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from lerobot.datasets import LeRobotDataset

    ds = LeRobotDataset("cstore/verify", root=root, video_backend="pyav")
    out = {"len": len(ds)}
    for idx in (0, len(ds) - 1):
        item = ds[idx]
        out[f"frame_{idx}"] = {k: list(item[k].shape) for k in video_keys}
    return out


def check(fp: dict, expect_episodes: int | None = None) -> list[str]:
    problems = []
    n, frames = fp["total_episodes"], fp["total_frames"]
    if expect_episodes is not None and n != expect_episodes:
        problems.append(f"episodes: {n}, expected {expect_episodes} from the input shard's metadata")
    if len(fp["episode_lengths"]) != n:
        problems.append(f"episode index lists {len(fp['episode_lengths'])} episodes, info.json says {n}")
    if sum(fp["episode_lengths"]) != frames:
        problems.append(f"episode lengths sum to {sum(fp['episode_lengths'])}, info.json says {frames} frames")
    if fp["data_rows"] != frames:
        problems.append(f"data parquet has {fp['data_rows']} rows, expected {frames}")
    for k, v in fp["video_frames"].items():
        if v != frames:
            problems.append(f"video {k} has {v} frames, expected {frames}")
    dec = fp.get("decoded")
    if dec is not None and dec["len"] != frames:
        problems.append(f"LeRobot loader sees {dec['len']} frames, expected {frames}")
    return problems


def compare(a: dict, b: dict) -> dict:
    names = sorted(set(a["files"]) | set(b["files"]))
    differing = [n for n in names if a["files"].get(n) != b["files"].get(n)]
    semantic_keys = ["total_episodes", "total_frames", "episode_lengths", "data_digest", "tasks_digest", "video_frames"]
    return {
        "files_compared": len(names),
        "identical": not differing,
        "differing_files": differing,
        "same_content": all(a.get(k) == b.get(k) for k in semantic_keys),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verify a LeRobot dataset produced by a cstore job.")
    p.add_argument("dataset", type=Path)
    p.add_argument("--expect-episodes", type=int)
    p.add_argument("--against", type=Path, help="another dataset that should be equivalent")
    p.add_argument("--json", type=Path, help="write the full report here")
    p.add_argument("--no-decode", action="store_true", help="skip decoding frames through LeRobot")
    a = p.parse_args(argv)

    fp = fingerprint(a.dataset, decode=not a.no_decode)
    report = {"dataset": fp, "problems": check(fp, a.expect_episodes)}
    if a.against:
        other = fingerprint(a.against, decode=False)
        report["against"] = str(a.against)
        report["comparison"] = compare(fp, other)

    ok = not report["problems"] and (not a.against or report["comparison"]["same_content"])
    report["ok"] = ok
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(f"dataset {a.dataset}")
    print(f"  episodes {fp['total_episodes']}  frames {fp['total_frames']}  files {len(fp['files'])}  bytes {fp['bytes']}")
    if fp.get("decoded"):
        print(f"  decoded first and last frame of {len(fp['video_frames'])} cameras through LeRobotDataset")
    for prob in report["problems"]:
        print(f"  PROBLEM: {prob}")
    if a.against:
        c = report["comparison"]
        state = "byte-identical" if c["identical"] else ("same content" if c["same_content"] else "DIFFERENT")
        print(f"  vs {a.against}: {state} ({c['files_compared']} files compared)")
        for f in c["differing_files"][:10]:
            print(f"    differs: {f}")
    print("VERIFY OK" if ok else "VERIFY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
