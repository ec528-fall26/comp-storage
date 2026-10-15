"""Generate a small synthetic dataset with the DROID 1.0.1 RLDS layout.

This exists only for the offline smoke test (experiments/00_smoke_local.sh),
so the whole pipeline can be exercised on one machine without downloading
DROID. The real experiments use real DROID 1.0.1 shards.

The output has the same layout as ``gs://gresearch/robotics/droid/1.0.1``
after loading a few shards: ``dataset_info.json`` describes all 2048 shards,
and only the chosen shards' record files are present::

    <out>/1.0.1/dataset_info.json
    <out>/1.0.1/features.json
    <out>/1.0.1/droid_101-train.tfrecord-00002-of-02048
    <out>/1.0.1/droid_101-train.tfrecord-00008-of-02048

so the same job specs and the same conversion path work on it exactly as on
real data. The schema matches the real files field for field (checked
against shard 2 on 2026-10-15): in particular, each episode carries only
``file_path`` and ``recording_folderpath``, in the real path layout, so the
smoke test also exercises the metadata the converter has to fill in.

Runs inside the worker image (needs TensorFlow Datasets).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import numpy as np  # noqa: E402
import tensorflow as tf  # noqa: E402
import tensorflow_datasets as tfds  # noqa: E402

HEIGHT, WIDTH = 180, 320
TOTAL_SHARDS = 2048
INSTRUCTIONS = [
    "Put the marker in the cup.",
    "Close the drawer.",
    "Use a cup to pour.",
    "Move the bowl to the left.",
]


def _features() -> tfds.features.FeaturesDict:
    f64 = np.float64

    def tensor(n: int) -> tfds.features.Tensor:
        return tfds.features.Tensor(shape=(n,), dtype=f64)

    def image() -> tfds.features.Image:
        return tfds.features.Image(shape=(HEIGHT, WIDTH, 3), dtype=np.uint8, encoding_format="jpeg")

    return tfds.features.FeaturesDict(
        {
            "steps": tfds.features.Dataset(
                {
                    "is_first": tfds.features.Scalar(dtype=np.bool_),
                    "is_last": tfds.features.Scalar(dtype=np.bool_),
                    "is_terminal": tfds.features.Scalar(dtype=np.bool_),
                    "language_instruction": tfds.features.Text(),
                    "language_instruction_2": tfds.features.Text(),
                    "language_instruction_3": tfds.features.Text(),
                    "observation": tfds.features.FeaturesDict(
                        {
                            "gripper_position": tensor(1),
                            "cartesian_position": tensor(6),
                            "joint_position": tensor(7),
                            "wrist_image_left": image(),
                            "exterior_image_1_left": image(),
                            "exterior_image_2_left": image(),
                        }
                    ),
                    "action_dict": tfds.features.FeaturesDict(
                        {
                            "gripper_position": tensor(1),
                            "gripper_velocity": tensor(1),
                            "cartesian_position": tensor(6),
                            "cartesian_velocity": tensor(6),
                            "joint_position": tensor(7),
                            "joint_velocity": tensor(7),
                        }
                    ),
                    "discount": tfds.features.Scalar(dtype=np.float32),
                    "reward": tfds.features.Scalar(dtype=np.float32),
                    "action": tensor(7),
                }
            ),
            # Exactly the two per-episode fields the public 1.0.1 files carry.
            "episode_metadata": tfds.features.FeaturesDict(
                {
                    "file_path": tfds.features.Text(),
                    "recording_folderpath": tfds.features.Text(),
                }
            ),
        }
    )


def _frame(rng: np.random.Generator, t: int, n: int, cam: int) -> np.ndarray:
    """A smooth moving scene with a little sensor noise, so the JPEG and AV1
    encoders see realistic-ish content rather than a constant image."""
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    phase = 2 * np.pi * t / max(n, 1)
    img = np.empty((HEIGHT, WIDTH, 3), dtype=np.float32)
    img[..., 0] = 90 + 60 * np.sin(xx / 41.0 + phase + cam)
    img[..., 1] = 110 + 50 * np.cos(yy / 29.0 - phase)
    img[..., 2] = 120 + 40 * np.sin((xx + yy) / 57.0 + 0.5 * cam)
    # a "gripper" blob that moves across the frame
    cx = int((0.15 + 0.7 * t / max(n - 1, 1)) * WIDTH)
    cy = int(HEIGHT * (0.5 + 0.25 * np.sin(phase + cam)))
    img[max(cy - 14, 0) : cy + 14, max(cx - 20, 0) : cx + 20] = (230, 200, 40)
    img += rng.normal(0, 4, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def _episode(index: int, seed: int, min_steps: int, max_steps: int) -> dict:
    rng = np.random.default_rng(seed + index)
    n = int(rng.integers(min_steps, max_steps + 1))
    outcome = "failure" if index % 4 == 3 else "success"
    instr = INSTRUCTIONS[index % len(INSTRUCTIONS)]
    ts = np.arange(n, dtype=np.float64)
    joints = np.stack([np.sin(ts / 15.0 + j) for j in range(7)], axis=1)
    grip = (np.sin(ts / 9.0) > 0).astype(np.float64)[:, None]
    cart = np.stack([np.cos(ts / 20.0 + j) for j in range(6)], axis=1)
    steps = []
    for t in range(n):
        steps.append(
            {
                "is_first": t == 0,
                "is_last": t == n - 1,
                "is_terminal": t == n - 1,
                "language_instruction": instr,
                "language_instruction_2": instr.lower(),
                "language_instruction_3": "",
                "observation": {
                    "gripper_position": grip[t],
                    "cartesian_position": cart[t],
                    "joint_position": joints[t],
                    "wrist_image_left": _frame(rng, t, n, 0),
                    "exterior_image_1_left": _frame(rng, t, n, 1),
                    "exterior_image_2_left": _frame(rng, t, n, 2),
                },
                "action_dict": {
                    "gripper_position": grip[min(t + 1, n - 1)],
                    "gripper_velocity": grip[min(t + 1, n - 1)] - grip[t],
                    "cartesian_position": cart[min(t + 1, n - 1)],
                    "cartesian_velocity": cart[min(t + 1, n - 1)] - cart[t],
                    "joint_position": joints[min(t + 1, n - 1)],
                    "joint_velocity": joints[min(t + 1, n - 1)] - joints[t],
                },
                "discount": np.float32(1.0),
                "reward": np.float32(1.0 if t == n - 1 and outcome == "success" else 0.0),
                # 6-D cartesian velocity + gripper position, as in DROID's "action"
                "action": np.concatenate([cart[min(t + 1, n - 1)] - cart[t], grip[t]]),
            }
        )
    # Same path layout as the real files: .../<lab>/<success|failure>/<date>/<timestamp>/
    day = 1 + index % 9
    folder = (
        f"/synthetic/r2d2-data-full/SyntheticLab/{outcome}/2026-09-0{day}/"
        f"Tue_Sep__{day}_10:{index % 60:02d}:00_2026"
    )
    return {
        "steps": steps,
        "episode_metadata": {
            "file_path": f"{folder}/trajectory.h5",
            "recording_folderpath": f"{folder}/recordings/MP4",
        },
    }


class SyntheticDroid101(tfds.core.GeneratorBasedBuilder):
    """Synthetic stand-in named like the published shards (``droid_101``)."""

    name = "droid_101"
    VERSION = tfds.core.Version("1.0.1")

    def __init__(self, *, episodes: int, seed: int, min_steps: int, max_steps: int, **kwargs):
        self._episodes = episodes
        self._seed = seed
        self._min_steps = min_steps
        self._max_steps = max_steps
        super().__init__(**kwargs)

    def _info(self) -> tfds.core.DatasetInfo:
        return tfds.core.DatasetInfo(
            builder=self,
            description="Synthetic data with the DROID 1.0.1 RLDS schema (cstore smoke test).",
            features=_features(),
        )

    def _split_generators(self, dl_manager):
        return {"train": self._generate_examples()}

    def _generate_examples(self):
        for i in range(self._episodes):
            yield f"episode_{i:04d}", _episode(i, self._seed, self._min_steps, self._max_steps)


def generate(
    out_dir: Path, shards: list[int], episodes_per_shard: int, seed: int, min_steps: int, max_steps: int
) -> Path:
    """Write the dataset and return the ``<out_dir>/1.0.1`` directory."""
    final = out_dir / "1.0.1"
    if final.exists():
        shutil.rmtree(final)
    n = len(shards) * episodes_per_shard
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        builder = SyntheticDroid101(
            data_dir=tmp, episodes=n, seed=seed, min_steps=min_steps, max_steps=max_steps
        )
        builder.download_and_prepare()
        produced = Path(tmp) / "droid_101" / "1.0.1"

        # TFDS wrote one record file; spread its episodes over the chosen
        # shards of a 2048-shard split, like the published dataset.
        records = []
        for f in sorted(produced.glob("droid_101-train.tfrecord-*")):
            records += [r.numpy() for r in tf.data.TFRecordDataset(str(f))]
        final.mkdir(parents=True)
        lengths = [0] * TOTAL_SHARDS
        for k, shard in enumerate(shards):
            path = final / f"droid_101-train.tfrecord-{shard:05d}-of-{TOTAL_SHARDS:05d}"
            with tf.io.TFRecordWriter(str(path)) as w:
                for r in records[k * episodes_per_shard : (k + 1) * episodes_per_shard]:
                    w.write(r)
            lengths[shard] = episodes_per_shard

        info = json.loads((produced / "dataset_info.json").read_text())
        train = next(sp for sp in info["splits"] if sp["name"] == "train")
        train["shardLengths"] = [str(x) for x in lengths]
        train["numBytes"] = str(sum(f.stat().st_size for f in final.glob("droid_101-*")))
        (final / "dataset_info.json").write_text(json.dumps(info, indent=2) + "\n")
        shutil.copy(produced / "features.json", final / "features.json")
    return final


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, required=True, help="raw dir; the dataset goes in <out>/1.0.1")
    p.add_argument("--shards", type=int, nargs="+", default=[2, 8], help="shard indices to create")
    p.add_argument("--episodes-per-shard", type=int, default=3)
    p.add_argument("--min-steps", type=int, default=40)
    p.add_argument("--max-steps", type=int, default=60)
    p.add_argument("--seed", type=int, default=528)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    final = generate(a.out, a.shards, a.episodes_per_shard, a.seed, a.min_steps, a.max_steps)
    files = sorted(final.iterdir())
    print(f"wrote {final}: shards {a.shards}, {a.episodes_per_shard} episodes each, "
          f"{sum(f.stat().st_size for f in files)} bytes in {len(files)} files")


if __name__ == "__main__":
    main()
