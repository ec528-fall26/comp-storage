"""Record what TensorFlow reads from DROID 1.0.1 RLDS records, for tests/test_droid.py.

Run with TensorFlow in its own environment (not the 3.9 test venv), in isolated mode:

    python -I tests/fixtures/make_droid_fixture.py SHARD_PATH tests/fixtures

Writes droid-mini.tfrecord (three real episodes cut to a few steps, only the features
the ingest reads) and droid-digests.json: for that file and for every episode of the
full shard, the steps, record bytes, a SHA-256 per camera of the JPEG bytes, a SHA-256
of the float64 state rows (joint_position then action) and the first instruction.
"""

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import tensorflow as tf

CAMERAS = ("exterior_image_1_left", "exterior_image_2_left", "wrist_image_left")
KEPT = (
    "episode_metadata/file_path",
    "steps/action",
    "steps/is_first",
    "steps/language_instruction",
    "steps/observation/joint_position",
    *(f"steps/observation/{camera}" for camera in CAMERAS),
)
MINI = {7: 8, 13: 6, 21: 5}


def digest(record):
    feature = tf.train.Example.FromString(record).features.feature
    cameras = [list(feature[f"steps/observation/{camera}"].bytes_list.value) for camera in CAMERAS]
    joints = np.array(feature["steps/observation/joint_position"].float_list.value, np.float32)
    action = np.array(feature["steps/action"].float_list.value, np.float32)
    steps = len(cameras[0])
    state = np.concatenate([joints.reshape(steps, 7), action.reshape(steps, 7)], axis=1)
    return {
        "steps": steps,
        "record_bytes": len(record),
        "cameras": [hashlib.sha256(b"".join(images)).hexdigest() for images in cameras],
        "state": hashlib.sha256(state.astype("<f8").tobytes()).hexdigest(),
        "instruction": feature["steps/language_instruction"].bytes_list.value[0].decode(),
    }


def truncated(record, steps):
    source = tf.train.Example.FromString(record).features.feature
    example = tf.train.Example()
    for key in KEPT:
        feature = source[key]
        kind = feature.WhichOneof("kind")
        values = list(getattr(feature, kind).value)
        if key.startswith("steps/"):
            values = values[
                : steps * (len(values) // len(source["steps/is_first"].int64_list.value))
            ]
        getattr(example.features.feature[key], kind).value.extend(values)
    return example.SerializeToString()


def main():
    shard = Path(sys.argv[1])
    output = Path(sys.argv[2])
    records = [record.numpy() for record in tf.data.TFRecordDataset(str(shard))]
    mini = output / "droid-mini.tfrecord"
    with tf.io.TFRecordWriter(str(mini)) as writer:
        for index, steps in MINI.items():
            writer.write(truncated(records[index], steps))
    mini_records = [record.numpy() for record in tf.data.TFRecordDataset(str(mini))]
    digests = {
        "shard": shard.name,
        "shard_episodes": [digest(record) for record in records],
        "mini_source_episodes": list(MINI),
        "mini_episodes": [digest(record) for record in mini_records],
    }
    (output / "droid-digests.json").write_text(json.dumps(digests, indent=1) + "\n")


if __name__ == "__main__":
    main()
