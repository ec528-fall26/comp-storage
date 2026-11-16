"""DROID episode processing: the object format and the registered pipeline functions.

Per partition of whole consecutive episodes (October pipeline, droid-dag-v1):

    episodes --decode_resize--> frames --encode_video--> videos
                            |          --frame_stats---> stats
                            +-> state  --state_parquet-> parquet

droid_fused runs all four on one partition in one task and keeps frames and state in
memory; its outputs are byte for byte those of the four tasks.

Every DROID object is MAGIC, a little-endian u32 header length, a JSON header and a
payload. An `episodes` object keeps the camera images JPEG-encoded as in the RLDS source,
so decoding stays inside the timed pipeline. Its payload is the float64 state rows
(STATE_COLUMNS per step), then a u32 length per image, then the JPEG bytes; images are
ordered step-major, camera-minor. Workers load this module with
`--functions cephdag.droid`; Pillow, PyAV and PyArrow are imported only when a function
runs, so clients can build graphs without them.
"""

import io
import json
import struct

import numpy as np

from .functions import function
from .graph import WORKER_MEMORY, memory_reservation

MAGIC = b"CDAGDRD1"
CAMERAS = ("exterior_image_1_left", "exterior_image_2_left", "wrist_image_left")
STATE_COLUMNS = 14
JOINTS = 7
STATS_COLUMNS = 6
FRAME_RATE = 15
HEADER_ALLOWANCE = 64 * 2**10
# Half of RADOS's default osd_max_object_size.
MAX_OBJECT_BYTES = 64 * 2**20
STATS_CHUNK_FRAMES = 64
FUNCTIONS = ("decode_resize@1", "encode_video@1", "frame_stats@1", "state_parquet@1")
FUSED = "droid_fused@1"


def pack(header, *blocks):
    body = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    return b"".join([MAGIC, struct.pack("<I", len(body)), body, *blocks])


def unpack(data, kind):
    """(header, payload) of a DROID object; the payload is a zero-copy view."""
    view = memoryview(data)
    if bytes(view[: len(MAGIC)]) != MAGIC:
        raise ValueError("not a DROID object")
    (length,) = struct.unpack_from("<I", view, len(MAGIC))
    start = len(MAGIC) + 4
    header = json.loads(bytes(view[start : start + length]))
    if header["kind"] != kind:
        raise ValueError(f"expected a {kind} object, got {header['kind']}")
    return header, view[start + length :]


def total_steps(header):
    return sum(episode["steps"] for episode in header["episodes"])


def pack_episodes(source, episodes):
    """One `episodes` object. Each episode is a dict with episode, first_step, instruction,
    state (steps x STATE_COLUMNS) and images (steps x cameras JPEG bytes, step-major)."""
    entries = []
    states = []
    images = []
    for episode in episodes:
        state = np.ascontiguousarray(episode["state"], dtype="<f8")
        steps = len(state)
        if state.shape != (steps, STATE_COLUMNS) or len(episode["images"]) != steps * len(CAMERAS):
            raise ValueError("episode state and images disagree on the number of steps")
        entries.append(
            {
                "episode": episode["episode"],
                "first_step": episode["first_step"],
                "steps": steps,
                "instruction": episode["instruction"],
            }
        )
        states.append(state.tobytes())
        images += episode["images"]
    header = {"kind": "episodes", "source": source, "cameras": list(CAMERAS), "episodes": entries}
    lengths = np.array([len(image) for image in images], dtype="<u4").tobytes()
    return pack(header, *states, lengths, *images)


def unpack_episodes(data):
    """(header, state rows, list of JPEG views in step-major, camera-minor order)."""
    header, payload = unpack(data, "episodes")
    steps = total_steps(header)
    state_bytes = steps * STATE_COLUMNS * 8
    count = steps * len(header["cameras"])
    state = np.frombuffer(payload[:state_bytes], dtype="<f8").reshape(steps, STATE_COLUMNS)
    lengths = np.frombuffer(payload[state_bytes : state_bytes + 4 * count], dtype="<u4")
    offsets = state_bytes + 4 * count + np.concatenate([[0], np.cumsum(lengths, dtype=np.int64)])
    if offsets[-1] != len(payload):
        raise ValueError("episodes object payload has the wrong length")
    images = [payload[offsets[index] : offsets[index + 1]] for index in range(count)]
    return header, state, images


def frame_bytes(steps, resolution):
    return steps * len(CAMERAS) * resolution * resolution * 3


def fits_worker(object_bytes, steps, resolution, memory_limit=WORKER_MEMORY):
    """Whether a partition object is small enough to store and every task of it fits the
    worker's memory reservation.

    The frames consumers are checked as if their output were as large as the frames,
    so that an underestimated output can never push a task over the bound.
    """
    frames = frame_bytes(steps, resolution) + HEADER_ALLOWANCE
    state = steps * STATE_COLUMNS * 8 + HEADER_ALLOWANCE
    decode = memory_reservation([object_bytes], [frames, state])
    consume = memory_reservation([frames], [frames])
    return object_bytes <= MAX_OBJECT_BYTES and max(decode, consume) <= memory_limit


def fits_fused(object_bytes, steps, resolution, memory_limit=WORKER_MEMORY):
    """Whether one droid_fused task of a partition fits the worker's memory reservation: the
    decode task's, with the frames held as well while the outputs, each bounded by the frames
    as in fits_worker, are made."""
    frames = frame_bytes(steps, resolution) + HEADER_ALLOWANCE
    state = steps * STATE_COLUMNS * 8 + HEADER_ALLOWANCE
    return memory_reservation([object_bytes], [frames, state]) + frames <= memory_limit


def read_frames(data):
    header, payload = unpack(data, "frames")
    size = header["resolution"]
    frames = np.frombuffer(payload, dtype=np.uint8)
    return header, frames.reshape(total_steps(header) * len(header["cameras"]), size, size, 3)


def read_state(data):
    header, payload = unpack(data, "state")
    return header, np.frombuffer(payload, dtype="<f8").reshape(total_steps(header), STATE_COLUMNS)


def resize_jpeg(jpeg, size):
    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as image:
        if image.mode != "RGB":
            image = image.convert("RGB")
        return np.asarray(image.resize((size, size), Image.Resampling.BILINEAR))


def decode(data, size):
    """(frames header, frames, state header, state) of an episodes object: every JPEG
    resized bilinearly to size x size RGB, and the state rows."""
    header, state, images = unpack_episodes(data)
    frames = np.empty((len(images), size, size, 3), dtype=np.uint8)
    for index, jpeg in enumerate(images):
        frames[index] = resize_jpeg(jpeg, size)
    episodes = [
        {key: episode[key] for key in ("episode", "first_step", "steps")}
        for episode in header["episodes"]
    ]
    common = {"source": header["source"], "cameras": header["cameras"]}
    frames_header = {"kind": "frames", "resolution": size, **common, "episodes": episodes}
    state_header = {"kind": "state", **common, "episodes": header["episodes"]}
    return frames_header, frames, state_header, state


@function("decode_resize", "1", inputs=["droid-episodes"], outputs=["droid-frames", "droid-state"])
def decode_resize(inputs, params, output_sizes):
    """Decode every JPEG, resize it bilinearly to resolution x resolution RGB, keep the state."""
    frames_header, frames, state_header, state = decode(inputs[0], params["resolution"])
    return pack(frames_header, frames.tobytes()), pack(state_header, state.tobytes())


def encode_stream(frames):
    """Lossless FFV1 in Matroska, single-threaded and bit-exact so reruns give equal bytes."""
    import av

    buffer = io.BytesIO()
    with av.open(buffer, "w", format="matroska", options={"fflags": "+bitexact"}) as container:
        stream = container.add_stream("ffv1", rate=FRAME_RATE)
        stream.width = frames.shape[2]
        stream.height = frames.shape[1]
        stream.pix_fmt = "bgr0"
        stream.thread_count = 1
        stream.codec_context.flags |= av.codec.context.Flags.bitexact
        for array in frames:
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            container.mux(stream.encode(frame))
        container.mux(stream.encode())
    return buffer.getvalue()


def video_object(header, frames):
    """One FFV1 video per camera per episode of `frames`, described by its frames `header`."""
    cameras = len(header["cameras"])
    streams = []
    videos = []
    offset = 0
    first_frame = 0
    for position, episode in enumerate(header["episodes"]):
        frame_count = episode["steps"] * cameras
        episode_frames = frames[first_frame : first_frame + frame_count]
        for camera in range(cameras):
            video = encode_stream(episode_frames[camera::cameras])
            streams.append(
                {"episode": position, "camera": camera, "offset": offset, "length": len(video)}
            )
            videos.append(video)
            offset += len(video)
        first_frame += frame_count
    return pack({**header, "kind": "video", "streams": streams}, *videos)


@function("encode_video", "1", inputs=["droid-frames"], outputs=["droid-video"])
def encode_video(inputs, params, output_sizes):
    """One FFV1 video per camera per episode."""
    return (video_object(*read_frames(inputs[0])),)


def stats_object(header, frames):
    """Exact per-frame channel sums and sums of squares, uint64 (r, g, b, r², g², b²)."""
    stats = np.empty((len(frames), STATS_COLUMNS), dtype="<u8")
    for start in range(0, len(frames), STATS_CHUNK_FRAMES):
        chunk = frames[start : start + STATS_CHUNK_FRAMES]
        pixels = chunk.reshape(len(chunk), -1, 3).astype(np.uint64)
        stats[start : start + len(chunk), :3] = pixels.sum(axis=1)
        stats[start : start + len(chunk), 3:] = (pixels * pixels).sum(axis=1)
    return pack({**header, "kind": "stats"}, stats.tobytes())


@function("frame_stats", "1", inputs=["droid-frames"], outputs=["droid-stats"])
def frame_stats(inputs, params, output_sizes):
    return (stats_object(*read_frames(inputs[0])),)


def state_table(header, state):
    import pyarrow as pa

    episodes = []
    steps = []
    instructions = []
    for episode in header["episodes"]:
        count = episode["steps"]
        episodes.append(np.full(count, episode["episode"], dtype=np.int64))
        steps.append(np.arange(episode["first_step"], episode["first_step"] + count))
        instructions += [episode["instruction"]] * count
    columns = {
        "source": pa.array([header["source"]] * len(state)).dictionary_encode(),
        "episode": np.concatenate(episodes or [np.empty(0, np.int64)]),
        "step": np.concatenate(steps or [np.empty(0, np.int64)]),
        "instruction": pa.array(instructions, pa.string()).dictionary_encode(),
    }
    for joint in range(JOINTS):
        columns[f"joint_position_{joint}"] = state[:, joint]
    for joint in range(JOINTS):
        columns[f"action_{joint}"] = state[:, JOINTS + joint]
    return pa.table(columns)


def parquet_object(header, state):
    """State rows as a zstd Parquet table with episode, step and instruction columns."""
    import pyarrow.parquet as pq

    buffer = io.BytesIO()
    pq.write_table(state_table(header, state), buffer, compression="zstd")
    return buffer.getvalue()


@function("state_parquet", "1", inputs=["droid-state"], outputs=["parquet"])
def state_parquet(inputs, params, output_sizes):
    return (parquet_object(*read_state(inputs[0])),)


@function(
    "droid_fused",
    "1",
    inputs=["droid-episodes"],
    outputs=["droid-video", "droid-stats", "parquet"],
)
def droid_fused(inputs, params, output_sizes):
    """decode_resize, then encode_video, frame_stats and state_parquet, in memory."""
    frames_header, frames, state_header, state = decode(inputs[0], params["resolution"])
    return (
        video_object(frames_header, frames),
        stats_object(frames_header, frames),
        parquet_object(state_header, state),
    )
