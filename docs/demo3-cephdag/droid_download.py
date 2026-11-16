"""Download DROID 1.0.1 RLDS shards from public GCS, checking every file against its MD5.

Takes shards in index order until the requested number of bytes is reached, plus any
shards named with --also. Finished files are never downloaded again, so a rerun resumes.

    python3 -I droid_download.py /mnt/mass/cephdag/droid/raw --bytes 500e9 --also 2047
"""

import argparse
import base64
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

BUCKET = "gresearch"
PREFIX = "robotics/droid/1.0.1/"
SHARD = re.compile(r"droid_101-train\.tfrecord-(\d{5})-of-02048$")
CHUNK = 8 * 2**20


def list_objects():
    objects = []
    token = None
    while True:
        query = {"prefix": PREFIX, "fields": "items(name,size,md5Hash),nextPageToken"}
        if token:
            query["pageToken"] = token
        url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o?" + urllib.parse.urlencode(query)
        with urllib.request.urlopen(url, timeout=60) as response:
            page = json.load(response)
        objects += page.get("items", [])
        token = page.get("nextPageToken")
        if not token:
            return objects


def select(objects, byte_budget, extra_indices):
    shards = sorted(
        (int(SHARD.search(item["name"]).group(1)), item)
        for item in objects
        if SHARD.search(item["name"])
    )
    chosen = []
    total = 0
    for index, item in shards:
        if total >= byte_budget:
            break
        chosen.append((index, item))
        total += int(item["size"])
    chosen_indices = {index for index, _ in chosen}
    chosen += [(index, item) for index, item in shards if index in extra_indices - chosen_indices]
    metadata = [item for item in objects if item["name"].endswith(".json")]
    return [item for _, item in chosen], metadata


def download(item, destination):
    target = destination / Path(item["name"]).name
    expected = item["md5Hash"]
    if target.exists() and target.stat().st_size == int(item["size"]):
        return target.name, int(item["size"]), "present"
    partial = target.with_suffix(target.suffix + ".part")
    url = f"https://storage.googleapis.com/{BUCKET}/{item['name']}"
    for attempt in range(4):
        digest = hashlib.md5()
        try:
            with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as out:
                while chunk := response.read(CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
        except OSError:
            time.sleep(10 * (attempt + 1))
            continue
        actual = base64.b64encode(digest.digest()).decode()
        if actual == expected and partial.stat().st_size == int(item["size"]):
            partial.rename(target)
            return target.name, int(item["size"]), "downloaded"
        time.sleep(10 * (attempt + 1))
    partial.unlink(missing_ok=True)
    raise RuntimeError(f"{item['name']}: no download matched its MD5 after 4 attempts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--bytes", type=float, required=True)
    parser.add_argument("--also", type=int, nargs="*", default=[])
    parser.add_argument("--streams", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()

    shards, metadata = select(list_objects(), arguments.bytes, set(arguments.also))
    total = sum(int(item["size"]) for item in shards)
    print(f"{len(shards)} shards, {total / 1e9:.1f} GB, plus {len(metadata)} metadata files")
    if arguments.dry_run:
        return
    arguments.destination.mkdir(parents=True, exist_ok=True)
    (arguments.destination / "selection.json").write_text(json.dumps(shards + metadata, indent=1))

    started = time.monotonic()
    done = 0
    with concurrent.futures.ThreadPoolExecutor(arguments.streams) as pool:
        futures = [pool.submit(download, item, arguments.destination) for item in metadata + shards]
        for future in concurrent.futures.as_completed(futures):
            name, size, how = future.result()
            done += size
            rate = done / max(time.monotonic() - started, 1e-9) / 1e6
            print(f"{how} {name} {size / 1e6:.0f} MB; {done / 1e9:.1f} of {total / 1e9:.1f} GB, {rate:.0f} MB/s", flush=True)
    print("complete")


if __name__ == "__main__":
    main()
