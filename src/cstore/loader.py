"""Load DROID 1.0.1 RLDS shards from Google's public bucket into CephFS.

The experiments use fixed inputs: every file is checked against the size and
MD5 pinned below (copied from the bucket's object metadata on 2026-10-14),
so a run either uses exactly these bytes or stops. Shards outside the pinned
set are checked against the bucket's metadata at download time.

Files are streamed to a hidden temporary name and renamed into place only
after the checksum matches, so the background service never sees a partial
shard.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import Config
from .jobs import DROID_SHARD_TEMPLATE, atomic_write_json

BUCKET = "gresearch"
PREFIX = "robotics/droid/1.0.1"
PUBLIC_URL = f"https://storage.googleapis.com/{BUCKET}/{PREFIX}/"
METADATA_URL = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/"

# name -> (size in bytes, base64 MD5) as reported by the bucket
PINNED = {
    "dataset_info.json": (31403, "YekWK5XSGxXM3QQ/9kbqNQ=="),
    "features.json": (16841, "6cmmZ00pBe7YooPUovUpoA=="),
    "droid_101-train.tfrecord-00000-of-02048": (1098702527, "ThCMjKjQNe+lWhbc0yhn3g=="),
    "droid_101-train.tfrecord-00001-of-02048": (1057583575, "PDAGDRlBb5E+H6knJ/W0wg=="),
    "droid_101-train.tfrecord-00002-of-02048": (648726823, "u3zxYCy2ADnwXe1z45XZ9w=="),
    "droid_101-train.tfrecord-00003-of-02048": (1070328309, "tRaIBQ6+HXyIkKqc8pvm2w=="),
    "droid_101-train.tfrecord-00004-of-02048": (978932348, "pHIg4CVlflwskZKYxFl43g=="),
    "droid_101-train.tfrecord-00005-of-02048": (923421353, "F4Y5bPQiXat30Dd6K9bBlQ=="),
    "droid_101-train.tfrecord-00006-of-02048": (996316097, "/m4WOmno2aFlyRRs2HkC3w=="),
    "droid_101-train.tfrecord-00007-of-02048": (1269202411, "9z1dzVrasLEPpNOOacL8DQ=="),
    "droid_101-train.tfrecord-00008-of-02048": (686098298, "v+mWYmSZx32cEh93KQdrdg=="),
    "droid_101-train.tfrecord-00009-of-02048": (986762864, "2w6IgpwB2c9KBQ9QD2eMSA=="),
    "droid_101-train.tfrecord-00010-of-02048": (881506813, "OSJ0zisbQ0oyVko3AgNnJQ=="),
}


def expected(name: str) -> tuple[int, str]:
    if name in PINNED:
        return PINNED[name]
    url = METADATA_URL + urllib.parse.quote(f"{PREFIX}/{name}", safe="") + "?fields=size,md5Hash"
    with urllib.request.urlopen(url, timeout=60) as r:
        meta = json.load(r)
    return int(meta["size"]), meta["md5Hash"]


def _md5_b64(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def fetch(name: str, dest_dir: Path, log=print) -> dict:
    size, md5 = expected(name)
    dest = dest_dir / name
    if dest.exists() and dest.stat().st_size == size and _md5_b64(dest) == md5:
        log(f"  {name}: already present and verified")
        return {"name": name, "bytes": 0, "seconds": 0.0, "skipped": True}

    tmp = dest_dir / f".{name}.partial"
    h = hashlib.md5()
    got = 0
    t0 = time.time()
    with urllib.request.urlopen(PUBLIC_URL + name, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(8 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            got += len(chunk)
        f.flush()
        os.fsync(f.fileno())
    seconds = time.time() - t0
    actual = base64.b64encode(h.digest()).decode()
    if got != size or actual != md5:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{name}: got {got} bytes md5 {actual}, expected {size} bytes md5 {md5}")
    os.rename(tmp, dest)
    log(f"  {name}: {got / 1e6:.0f} MB in {seconds:.1f} s, md5 ok")
    return {"name": name, "bytes": got, "seconds": round(seconds, 2), "skipped": False}


def load(cfg: Config, shards: list[int], log=print) -> dict:
    dest = cfg.droid_version_dir
    dest.mkdir(parents=True, exist_ok=True)
    names = ["dataset_info.json", "features.json"] + [DROID_SHARD_TEMPLATE.format(index=i) for i in shards]
    log(f"loading {len(shards)} DROID shard(s) into {dest}")
    t0 = time.time()
    files = [fetch(n, dest, log) for n in names]
    summary = {
        "dest": str(dest),
        "shards": shards,
        "files": files,
        "downloaded_bytes": sum(f["bytes"] for f in files),
        "seconds": round(time.time() - t0, 2),
    }
    info = json.loads((dest / "dataset_info.json").read_text())
    train = next(s for s in info["splits"] if s["name"] == "train")
    summary["episodes_per_shard"] = {i: int(train["shardLengths"][i]) for i in shards}
    atomic_write_json(cfg.results / "load" / f"load-{int(t0)}.json", summary)
    return summary
