"""Measure the DROID functions on one local shard: CPU and wall seconds, output sizes.

    python -m experiments.droid_profile SHARD_PATH --resolution 128 --output profile.json

Ingests the shard into an in-memory store (timing the ingest), then runs every function
in this process on every partition, one at a time. Source bytes are the TFRecord bytes
of the episodes in a partition, split by steps when an episode spans partitions.
"""

import argparse
import json
from pathlib import Path
import time

from cephdag import Client, droid
from cephdag.cluster import Cluster, Node
from cephdag.functions import lookup
from cephdag.storage import MemoryStore
from experiments import droid_ingest

MB = 1e6


def timed(function, inputs, params):
    cpu = time.process_time()
    wall = time.perf_counter()
    outputs = lookup(function).run(inputs, params, [])
    return outputs, time.process_time() - cpu, time.perf_counter() - wall


def partition_source_bytes(index, entry):
    episodes = {episode["episode"]: episode for episode in index["episodes"]}
    total = 0.0
    for episode, _, steps in entry["episodes"]:
        whole = episodes[episode]
        total += whole["record_bytes"] * steps / whole["steps"]
    return total


def profile(shard, resolution):
    cluster = Cluster((Node("node0", "http://127.0.0.1:1"),), "node0")
    client = Client(cluster, store=MemoryStore())
    began = time.perf_counter()
    with open(shard, "rb") as stream:
        dataset, index = droid_ingest.ingest_shard(
            client, stream, shard.name, "profile", resolution, droid_ingest.CyclicPlacement([0])
        )
    ingest_seconds = time.perf_counter() - began
    rows = []
    for ref, entry in zip(dataset.shards, index["partitions"]):
        episodes = client.store.read(ref)
        params = {"resolution": resolution}
        (frames, state), decode_cpu, decode_wall = timed("decode_resize@1", [episodes], params)
        (video,), video_cpu, video_wall = timed("encode_video@1", [frames], {})
        (stats,), stats_cpu, stats_wall = timed("frame_stats@1", [frames], {})
        (parquet,), parquet_cpu, parquet_wall = timed("state_parquet@1", [state], {})
        rows.append(
            {
                "source_bytes": partition_source_bytes(index, entry),
                "object_bytes": len(episodes),
                "steps": entry["steps"],
                "episodes": len(entry["episodes"]),
                "bytes": {
                    "frames": len(frames),
                    "state": len(state),
                    "video": len(video),
                    "stats": len(stats),
                    "parquet": len(parquet),
                },
                "cpu_seconds": {
                    "decode_resize@1": decode_cpu,
                    "encode_video@1": video_cpu,
                    "frame_stats@1": stats_cpu,
                    "state_parquet@1": parquet_cpu,
                },
                "wall_seconds": {
                    "decode_resize@1": decode_wall,
                    "encode_video@1": video_wall,
                    "frame_stats@1": stats_wall,
                    "state_parquet@1": parquet_wall,
                },
            }
        )
    return rows, index, ingest_seconds


def summary(rows, index, ingest_seconds):
    source = sum(row["source_bytes"] for row in rows)
    objects = sum(row["object_bytes"] for row in rows)

    def total(kind):
        return sum(row["bytes"][kind] for row in rows)

    return {
        "partitions": len(rows),
        "episodes": len(index["episodes"]),
        "source_mb": source / MB,
        "object_mb": objects / MB,
        "ingest_seconds_from_local_file": ingest_seconds,
        "ingest_mb_per_second": index["source_bytes"] / MB / ingest_seconds,
        "cpu_seconds_per_source_mb": {
            function: sum(row["cpu_seconds"][function] for row in rows) / (source / MB)
            for function in droid.FUNCTIONS
        },
        "wall_seconds_per_source_mb": {
            function: sum(row["wall_seconds"][function] for row in rows) / (source / MB)
            for function in droid.FUNCTIONS
        },
        "ratios": {
            "frames_per_object": total("frames") / objects,
            "frames_per_source": total("frames") / source,
            "video_per_frames": total("video") / total("frames"),
            "stats_per_frames": total("stats") / total("frames"),
            "parquet_per_state": total("parquet") / total("state"),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("shard", type=Path)
    parser.add_argument("--resolution", type=int, default=128)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, index, ingest_seconds = profile(args.shard, args.resolution)
    result = {"summary": summary(rows, index, ingest_seconds), "rows": rows}
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
