"""Stream DROID 1.0.1 RLDS TFRecord shards into the cluster as `episodes` objects.

    python -m experiments.droid_ingest --shards 0-63 --resolution 128 --placement 0,0,1,2

Each source shard becomes one dataset, `droid-<resolution>-<shard>`, written with
Client.put_dataset; nothing is staged on disk. A partition holds whole consecutive
episodes of one shard, as many as fit the worker memory bound at `resolution`; an
episode too long to fit alone is split into step ranges. `datasets/<name>/droid.json`
records the source shard, its MD5, and every episode's record bytes and step range.

The TFRecord framing and the tf.train.Example decoding are custom code, so that the
ingest runs in the lab's Python 3.9 next to python3-rados without TensorFlow.
tests/test_droid.py checks the decoding against TensorFlow's on recorded episodes.
"""

import argparse
import base64
import hashlib
from pathlib import Path
import struct
import time
import urllib.request

import numpy as np

from cephdag import droid
from cephdag.client import Client
from cephdag.cluster import LAB_CLUSTER
from cephdag.graph import WORKER_MEMORY

SOURCE_URL = "https://storage.googleapis.com/gresearch/robotics/droid/1.0.1"
SHARD_COUNT = 2048
STEPS = "steps/"


def shard_name(shard):
    return f"droid_101-train.tfrecord-{shard:05d}-of-{SHARD_COUNT:05d}"


def dataset_name(resolution, shard):
    return f"droid-{resolution}-{shard:05d}"


def read_exactly(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("truncated TFRecord stream")
    return data


def records(stream):
    """Each record of a TFRecord stream: u64 length, u32 CRC, data, u32 CRC.

    The CRCs are not checked here; the whole stream's MD5 is compared with the source's.
    """
    while True:
        head = stream.read(12)
        if not head:
            return
        if len(head) != 12:
            raise ValueError("truncated TFRecord header")
        (length,) = struct.unpack_from("<Q", head)
        yield read_exactly(stream, length + 4)[:length]


def varint(data, position):
    value = 0
    shift = 0
    while True:
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, position
        shift += 7


def fields(message):
    """(field number, wire type, value) of every field of a serialized protobuf message."""
    position = 0
    while position < len(message):
        key, position = varint(message, position)
        wire = key & 7
        if wire == 0:
            value, position = varint(message, position)
        elif wire == 2:
            length, position = varint(message, position)
            value = message[position : position + length]
            position += length
        elif wire in (1, 5):
            width = 8 if wire == 1 else 4
            value = message[position : position + width]
            position += width
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield key >> 3, wire, value


def example_features(record):
    """Feature name -> serialized Feature of a tf.train.Example (features=1; map entries=1)."""
    features = {}
    for number, _, message in fields(memoryview(record)):
        if number != 1:
            continue
        for entry_number, _, entry in fields(message):
            if entry_number != 1:
                continue
            parts = {number: value for number, _, value in fields(entry)}
            features[bytes(parts[1]).decode()] = parts.get(2, b"")
    return features


def feature_list(feature, kind):
    """The values of a Feature whose oneof is kind 1 (bytes), 2 (float) or 3 (int64)."""
    values = [value for number, _, value in fields(feature) if number == kind]
    if len(values) > 1:
        raise ValueError("feature holds more than one list")
    return values[0] if values else b""


def bytes_values(feature):
    return [bytes(value) for number, _, value in fields(feature_list(feature, 1)) if number == 1]


def float_values(feature):
    chunks = []
    for number, _, value in fields(feature_list(feature, 2)):
        if number == 1:
            chunks.append(np.frombuffer(value, dtype="<f4"))
    return np.concatenate(chunks) if chunks else np.empty(0, dtype="<f4")


def parse_episode(record, index):
    """The episode in one RLDS record: state rows, step-major JPEGs, first instruction."""
    features = example_features(record)
    cameras = [bytes_values(features[f"{STEPS}observation/{camera}"]) for camera in droid.CAMERAS]
    steps = len(cameras[0])
    joints = float_values(features[f"{STEPS}observation/joint_position"])
    action = float_values(features[f"{STEPS}action"])
    if any(len(images) != steps for images in cameras) or (
        joints.shape != (steps * droid.JOINTS,) or action.shape != (steps * droid.JOINTS,)
    ):
        raise ValueError(f"episode {index}: cameras and state disagree on the number of steps")
    state = np.concatenate(
        [joints.reshape(steps, droid.JOINTS), action.reshape(steps, droid.JOINTS)], axis=1
    ).astype("<f8")
    instructions = bytes_values(features.get(f"{STEPS}language_instruction", b""))
    return {
        "episode": index,
        "first_step": 0,
        "instruction": instructions[0].decode() if instructions else "",
        "state": state,
        "images": [images[step] for step in range(steps) for images in cameras],
        "record_bytes": len(record),
    }


def episode_bytes(episode):
    return episode["state"].nbytes + sum(4 + len(image) for image in episode["images"])


def step_range(episode, start, stop):
    cameras = len(droid.CAMERAS)
    return {
        **episode,
        "first_step": episode["first_step"] + start,
        "state": episode["state"][start:stop],
        "images": episode["images"][start * cameras : stop * cameras],
    }


def steps_of(episodes):
    return sum(len(episode["state"]) for episode in episodes)


def fits(episodes, resolution, memory_limit):
    size = sum(episode_bytes(episode) for episode in episodes) + droid.HEADER_ALLOWANCE
    return droid.fits_worker(size, steps_of(episodes), resolution, memory_limit)


def split_to_fit(episode, resolution, memory_limit):
    """Consecutive step ranges of one episode, each as long as fits a partition alone."""
    pieces = []
    start = 0
    steps = len(episode["state"])
    while start < steps:
        stop = start + 1
        if not fits([step_range(episode, start, stop)], resolution, memory_limit):
            raise ValueError("a single step does not fit the worker memory bound")
        while stop < steps and fits(
            [step_range(episode, start, stop + 1)], resolution, memory_limit
        ):
            stop += 1
        pieces.append(step_range(episode, start, stop))
        start = stop
    return pieces


def partitions(episodes, resolution, memory_limit=WORKER_MEMORY):
    """Group consecutive episodes into lists that fit the worker memory bound."""
    current = []
    for episode in episodes:
        if fits(current + [episode], resolution, memory_limit):
            current.append(episode)
            continue
        if current:
            yield current
        current = []
        if fits([episode], resolution, memory_limit):
            current = [episode]
            continue
        pieces = split_to_fit(episode, resolution, memory_limit)
        yield from ([piece] for piece in pieces[:-1])
        current = pieces[-1:]
    if current:
        yield current


class HashingReader:
    def __init__(self, stream):
        self.stream = stream
        self.md5 = hashlib.md5()
        self.bytes = 0

    def read(self, size):
        data = self.stream.read(size)
        self.md5.update(data)
        self.bytes += len(data)
        return data


class CyclicPlacement:
    """placement[i] for put_dataset, cycling a node pattern from an offset."""

    def __init__(self, pattern, offset=0):
        self.pattern = tuple(pattern)
        self.offset = offset

    def __getitem__(self, index):
        return self.pattern[(self.offset + index) % len(self.pattern)]


def ingest_shard(client, stream, source, name, resolution, placement, expected_md5=None):
    """Write one source stream as dataset `name`; return (dataset, index).

    The dataset manifest is written only after the whole stream has been read and its
    MD5 matched, so a truncated or corrupted download leaves no dataset behind.
    """
    reader = HashingReader(stream)
    index = {
        "source": source,
        "resolution_bound": resolution,
        "memory_limit": WORKER_MEMORY,
        "episodes": [],
        "partitions": [],
    }

    def episodes():
        for position, record in enumerate(records(reader)):
            episode = parse_episode(record, position)
            index["episodes"].append(
                {
                    "episode": position,
                    "steps": len(episode["state"]),
                    "record_bytes": episode["record_bytes"],
                }
            )
            yield episode

    def parts():
        for group in partitions(episodes(), resolution):
            data = droid.pack_episodes(source, group)
            index["partitions"].append(
                {
                    "object_bytes": len(data),
                    "steps": steps_of(group),
                    "episodes": [
                        [episode["episode"], episode["first_step"], len(episode["state"])]
                        for episode in group
                    ],
                }
            )
            yield data
        digest = base64.b64encode(reader.md5.digest()).decode()
        if expected_md5 is not None and digest != expected_md5:
            raise ValueError(f"{source}: MD5 {digest} differs from the source's {expected_md5}")
        index["source_md5"] = digest
        index["source_bytes"] = reader.bytes

    dataset = client.put_dataset(name, parts(), placement=placement, dtype="droid-episodes")
    client.store.put_manifest(client.cluster.metadata_pool, f"datasets/{name}/droid.json", index)
    return dataset, index


def open_source(shard, source_dir):
    """(stream, expected base64 MD5 or None) for a local file or the public GCS object."""
    if source_dir is not None:
        return open(source_dir / shard_name(shard), "rb"), None
    response = urllib.request.urlopen(f"{SOURCE_URL}/{shard_name(shard)}", timeout=120)
    return response, published_md5(response.headers.get_all("x-goog-hash") or [])


def published_md5(headers):
    """The MD5 among GCS x-goog-hash headers, e.g. ['crc32c=VL1BHg==', 'md5=kbFO...==']."""
    for header in headers:
        for part in header.split(","):
            kind, _, value = part.strip().partition("=")
            if kind == "md5":
                return value
    raise ValueError("the source publishes no MD5 to check the download against")


def parse_range(text):
    first, _, last = text.partition("-")
    return range(int(first), int(last or first) + 1)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shards", type=parse_range, required=True, help="e.g. 217 or 0-63")
    parser.add_argument("--resolution", type=int, default=128)
    parser.add_argument("--placement", default="0,0,1,2", help="node pattern, cycled")
    parser.add_argument("--source-dir", type=Path, default=None, help="local shards, not GCS")
    parser.add_argument("--cluster", type=Path, default=LAB_CLUSTER)
    args = parser.parse_args()
    client = Client(args.cluster)
    pattern = [int(node) for node in args.placement.split(",")]
    for shard in args.shards:
        name = dataset_name(args.resolution, shard)
        try:
            client.dataset(name)
            print(f"{name}: exists, skipped", flush=True)
            continue
        except client.store.not_found:
            pass
        began = time.monotonic()
        stream, expected_md5 = open_source(shard, args.source_dir)
        with stream:
            placement = CyclicPlacement(pattern, shard)
            dataset, index = ingest_shard(
                client, stream, shard_name(shard), name, args.resolution, placement, expected_md5
            )
        seconds = time.monotonic() - began
        print(
            f"{name}: {len(index['episodes'])} episodes, {len(dataset.shards)} partitions, "
            f"{index['source_bytes'] / 1e6:.1f} MB in {seconds:.1f} s "
            f"({index['source_bytes'] / 1e6 / seconds:.1f} MB/s)",
            flush=True,
        )
    client.store.close()


if __name__ == "__main__":
    main()
