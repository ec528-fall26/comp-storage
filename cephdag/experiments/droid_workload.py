"""The DROID pipeline as graphs over ingested partitions, run as a sequence of waves.

A wave is a run of consecutive partitions whose source objects add up to at most
`wave_bytes`; each wave is its own graph run, so only one wave's frames (2.16 times its
source at 128x128) exist at a time, and run.py removes them once the wave's outputs are
verified. The fused graph runs each partition as one droid_fused task, which stores no
frames or state.
"""

from dataclasses import dataclass, replace
import hashlib
import io
import json

import numpy as np

from cephdag import droid
from cephdag.graph import Dataset, Graph, ref_from_dict
from cephdag.model import FunctionCost

# Output estimates measured on DROID 1.0.1 shard 00217 at 128x128; the worker records
# the real sizes, which replace these once a task has run.
VIDEO_RATIO = 0.33
PARQUET_RATIO = 0.69
CHECKED_FRAMES = 8
FINAL_TYPES = ("droid-video", "droid-stats", "parquet")


@dataclass(frozen=True)
class Wave:
    """Partitions renumbered 0..n-1 within the wave, with their steps and origin."""

    dataset: Dataset
    steps: tuple
    sources: tuple


def load_partitions(client, names):
    """[(ref, steps, (dataset name, part))] of every partition of the ingested datasets."""
    partitions = []
    for name in names:
        dataset = client.dataset(name)
        index = client.store.get_manifest(
            client.cluster.metadata_pool, f"datasets/{name}/droid.json"
        )
        if len(index["partitions"]) != len(dataset.shards):
            raise ValueError(f"{name}: index and dataset disagree on the partitions")
        for part, (ref, entry) in enumerate(zip(dataset.shards, index["partitions"])):
            if entry["object_bytes"] != ref.size:
                raise ValueError(f"{name}: partition {part} size differs from its index")
            partitions.append((ref, entry["steps"], (name, part)))
    return partitions


def waves(partitions, wave_bytes):
    """Consecutive groups of partitions, each at most `wave_bytes` of source (or one partition)."""
    groups = []
    current = []
    total = 0
    for partition in partitions:
        size = partition[0].size
        if current and total + size > wave_bytes:
            groups.append(current)
            current = []
            total = 0
        current.append(partition)
        total += size
    if current:
        groups.append(current)
    return [
        Wave(
            Dataset(
                f"droid-wave-{number}",
                tuple(replace(ref, partition=index) for index, (ref, _, _) in enumerate(group)),
            ),
            tuple(steps for _, steps, _ in group),
            tuple(source for _, _, source in group),
        )
        for number, group in enumerate(groups)
    ]


def stats_ratio(resolution):
    """Statistics bytes per frames byte."""
    return droid.STATS_COLUMNS * 8 / (resolution * resolution * 3)


def build_graph(dataset, resolution, steps):
    """Per partition: decode_resize, whose frames feed encode_video and frame_stats, and
    whose state feeds state_parquet. Frames and state estimates are exact but for headers
    and rounding."""
    graph = Graph()
    for ref, count in zip(dataset.shards, steps):
        frames_ratio = droid.frame_bytes(count, resolution) / ref.size
        state_ratio = count * droid.STATE_COLUMNS * 8 / ref.size
        frames, state = graph.add(
            "decode_resize@1",
            [ref],
            ratios=[frames_ratio, state_ratio],
            iterations=0,
            params={"resolution": resolution},
        )
        graph.add("encode_video@1", [frames], ratios=[VIDEO_RATIO], iterations=0)
        graph.add("frame_stats@1", [frames], ratios=[stats_ratio(resolution)], iterations=0)
        graph.add("state_parquet@1", [state], ratios=[PARQUET_RATIO], iterations=0)
    return graph


def build_fused_graph(dataset, resolution, steps):
    """One droid_fused task per partition, whose outputs are estimated as the unfused
    graph's videos, statistics and Parquet. Refuses a partition whose fused task would not
    fit the worker's memory."""
    graph = Graph()
    for ref, count in zip(dataset.shards, steps):
        if not droid.fits_fused(ref.size, count, resolution, graph.memory_limit):
            raise ValueError(
                f"partition {ref.partition} ({ref.size} bytes, {count} steps) does not fit "
                "one fused task in the worker's memory"
            )
        frames = droid.frame_bytes(count, resolution)
        state = count * droid.STATE_COLUMNS * 8
        ratios = [
            frames * VIDEO_RATIO / ref.size,
            frames * stats_ratio(resolution) / ref.size,
            state * PARQUET_RATIO / ref.size,
        ]
        graph.add(
            droid.FUSED, [ref], ratios=ratios, iterations=0, params={"resolution": resolution}
        )
    return graph


def fused_cost(compute, frames_per_source, resolution=128):
    """A cost for droid_fused: the unfused tasks' predicted seconds for a partition whose
    frames are `frames_per_source` times its source, per source byte."""
    frames = frames_per_source
    steps = frames / droid.frame_bytes(1, resolution)
    state = steps * droid.STATE_COLUMNS * 8
    bytes_per_source = {
        "decode_resize@1": (1.0, frames + state),
        "encode_video@1": (frames, frames * VIDEO_RATIO),
        "frame_stats@1": (frames, frames * stats_ratio(resolution)),
        "state_parquet@1": (state, state * PARQUET_RATIO),
    }
    fixed = 0.0
    per_source_byte = 0.0
    for function, (read, written) in bytes_per_source.items():
        cost = compute.cost(function)
        fixed += cost.fixed_seconds
        per_source_byte += read * cost.input_seconds_per_byte
        per_source_byte += written * cost.output_seconds_per_byte
    return FunctionCost(fixed, per_source_byte, 0.0)


def decoded_streams(video):
    import av

    header, payload = droid.unpack(video, "video")
    for stream in header["streams"]:
        data = payload[stream["offset"] : stream["offset"] + stream["length"]]
        with av.open(io.BytesIO(data)) as container:
            frames = [frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)]
        yield stream, np.stack(frames) if frames else np.empty((0,), np.uint8)


def check(condition, message):
    if not condition:
        raise AssertionError(f"DROID verification: {message}")


def partition_refs(tasks):
    """(source, frames, state, video, stats, Parquet) references of one partition's tasks;
    frames and state are None if a fused task kept them in memory."""
    if droid.FUSED in tasks:
        fused, (video_ref, stats_ref, parquet_ref) = tasks[droid.FUSED]
        return fused.inputs[0], None, None, video_ref, stats_ref, parquet_ref
    decode, (frames_ref, state_ref) = tasks["decode_resize@1"]
    _, (video_ref,) = tasks["encode_video@1"]
    _, (stats_ref,) = tasks["frame_stats@1"]
    _, (parquet_ref,) = tasks["state_parquet@1"]
    return decode.inputs[0], frames_ref, state_ref, video_ref, stats_ref, parquet_ref


def verify_partition(store, tasks, resolution, generator):
    """Check one partition's outputs against its frames, state and source object. The frames
    and state of a fused partition, never stored, are derived again from the source."""
    source, frames_ref, state_ref, video_ref, stats_ref, parquet_ref = partition_refs(tasks)
    data = store.read(source)
    source_header, source_state, images = droid.unpack_episodes(data)
    if frames_ref is None:
        header, frames, _, state = droid.decode(data, resolution)
    else:
        header, frames = droid.read_frames(store.read(frames_ref))
        _, state = droid.read_state(store.read(state_ref))
    check(header["resolution"] == resolution, "frames resolution")
    check(len(frames) == len(images), "one frame per source image")
    sampled = generator.choice(len(frames), size=min(CHECKED_FRAMES, len(frames)), replace=False)
    # Same resize code as the worker: this catches frame order and camera mix-ups, it is
    # not an independent resize oracle.
    for index in sampled:
        check(np.array_equal(droid.resize_jpeg(images[index], resolution), frames[index]), "frame")

    cameras = len(header["cameras"])
    starts = np.cumsum([0] + [episode["steps"] * cameras for episode in header["episodes"]])
    decoded = 0
    for stream, pixels in decoded_streams(store.read(video_ref)):
        expected = frames[starts[stream["episode"]] : starts[stream["episode"] + 1]]
        check(np.array_equal(pixels, expected[stream["camera"] :: cameras]), "video pixels")
        decoded += len(pixels)
    check(decoded == len(frames), "every frame is in a video")

    _, payload = droid.unpack(store.read(stats_ref), "stats")
    stats = np.frombuffer(payload, dtype="<u8").reshape(-1, droid.STATS_COLUMNS)
    check(len(stats) == len(frames), "one statistics row per frame")
    for index in sampled:
        pixels = frames[index].reshape(-1, 3).astype(np.uint64)
        recomputed = np.concatenate([pixels.sum(axis=0), np.einsum("ij,ij->j", pixels, pixels)])
        check(np.array_equal(stats[index], recomputed), "frame statistics")

    import pyarrow.parquet as pq

    check(np.array_equal(state, source_state), "state equals the source rows")
    table = pq.read_table(io.BytesIO(store.read(parquet_ref)))
    names = [f"joint_position_{joint}" for joint in range(droid.JOINTS)]
    names += [f"action_{joint}" for joint in range(droid.JOINTS)]
    columns = np.column_stack([table.column(name).to_numpy() for name in names])
    check(columns.tobytes() == state.tobytes(), "Parquet state round trip")
    steps = np.concatenate(
        [
            np.arange(episode["first_step"], episode["first_step"] + episode["steps"])
            for episode in source_header["episodes"]
        ]
    )
    check(np.array_equal(table.column("step").to_numpy(), steps), "Parquet step column")
    return {"frames": len(frames), "decoded": decoded, "sampled": len(sampled), "rows": len(state)}


def verify(store, result, graph, resolution, samples=3, seed=20261112):
    """Sampled independent checks of up to `samples` partitions, spread over the wave."""
    tasks = {}
    for task_id, entry in result["tasks"].items():
        task = graph.tasks[task_id]
        outputs = tuple(ref_from_dict(output) for output in entry["result"]["outputs"])
        tasks.setdefault(task.partition, {})[task.function] = (task, outputs)
    partitions = sorted(tasks)
    count = min(samples, len(partitions))
    chosen = sorted({round(i * (len(partitions) - 1) / max(1, count - 1)) for i in range(count)})
    totals = {"partitions": 0, "frames": 0, "decoded": 0, "sampled": 0, "rows": 0}
    for position in chosen:
        partition = partitions[position]
        generator = np.random.default_rng(seed + partition)
        checked = verify_partition(store, tasks[partition], resolution, generator)
        totals["partitions"] += 1
        for key, value in checked.items():
            totals[key] += value
    method = (
        "FFV1 decoded back and compared with the frames; sampled statistics recomputed by "
        "einsum; Parquet read back against the state; sampled frames re-derived from the "
        "source JPEGs with the worker's resize (all frames, for a fused partition)"
    )
    return {**totals, "final_outputs_sha256": final_digest(result, graph), "method": method}


def final_digest(result, graph):
    """One digest of the partition, type and checksum of every video, statistics and Parquet
    output: equal for the fused and the unfused graph of the same partitions."""
    finals = sorted(
        (graph.tasks[task_id].partition, output["dtype"], output["sha256"])
        for task_id, entry in result["tasks"].items()
        for output in entry["result"]["outputs"]
        if output["dtype"] in FINAL_TYPES
    )
    return hashlib.sha256(json.dumps(finals).encode()).hexdigest()
