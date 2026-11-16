"""The DROID pipeline: ingest, object format, functions, cost model, waves and verification.

Most tests use tests/fixtures/droid-mini.tfrecord: three real DROID 1.0.1 episodes from
shard 00217, cut to a few steps. The tests marked `real shard` need that whole shard
(477 MB) at CEPHDAG_DROID_SHARD and skip without it.
"""

import base64
import contextlib
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image
import pyarrow.parquet as pq

from cephdag import droid
from cephdag.client import Client
from cephdag.graph import WORKER_MEMORY, Dataset, ObjectRef
from cephdag.model import ComputeModel, FunctionCost, Models
from experiments import calibrate, droid_ingest, droid_workload, run
from tests.helpers import cs_nodes, disagg_nodes, local_cluster, role_lab

FIXTURES = Path(__file__).parent / "fixtures"
MINI = FIXTURES / "droid-mini.tfrecord"
DIGESTS = json.loads((FIXTURES / "droid-digests.json").read_text())
SHARD = Path(
    os.environ.get(
        "CEPHDAG_DROID_SHARD",
        "/home/aakash/datasets/droid/droid_101-train.tfrecord-00217-of-02048",
    )
)
REAL_SHARD = unittest.skipUnless(
    SHARD.exists(), f"real shard absent: set CEPHDAG_DROID_SHARD (looked at {SHARD})"
)
# Per-function costs measured on this VM at 128x128 (experiments.droid_profile).
MEASURED = (
    ("decode_resize@1", FunctionCost(0.0, 3.1e-8, 0.0)),
    ("encode_video@1", FunctionCost(0.0, 1.6e-8, 0.0)),
    ("frame_stats@1", FunctionCost(0.0, 8.7e-9, 0.0)),
    ("state_parquet@1", FunctionCost(0.002, 0.0, 0.0)),
)
DROID_MODELS = Models(ComputeModel(functions=MEASURED))


def digest(episode):
    images = episode["images"]
    return {
        "steps": len(episode["state"]),
        "record_bytes": episode["record_bytes"],
        "cameras": [hashlib.sha256(b"".join(images[camera::3])).hexdigest() for camera in range(3)],
        "state": hashlib.sha256(episode["state"].tobytes()).hexdigest(),
        "instruction": episode["instruction"],
    }


def read_episodes(stream):
    return [
        droid_ingest.parse_episode(record, index)
        for index, record in enumerate(droid_ingest.records(stream))
    ]


def local_client(models=DROID_MODELS):
    cluster, store, workers, transport = local_cluster()
    return Client(cluster, models, store=store, transport=transport), workers


def ingest(client, stream, name, md5=None, placement=(0, 0, 1, 2)):
    return droid_ingest.ingest_shard(
        client,
        stream,
        "fixture.tfrecord",
        name,
        128,
        droid_ingest.CyclicPlacement(placement),
        md5,
    )


def mini_md5():
    return base64.b64encode(hashlib.md5(MINI.read_bytes()).digest()).decode()


def run_waves(client, names, policies, wave_bytes, fused=False):
    """Every wave under every policy through run.run_matrix; returns all results."""
    plan = droid_workload.waves(droid_workload.load_partitions(client, names), wave_bytes)
    results = []
    settings = {"seed": 1, "budget_ms": None, "ceph_data": "/"}
    quiet = contextlib.redirect_stdout(io.StringIO())
    with mock.patch.object(run, "require_capacity"), tempfile.TemporaryDirectory() as directory:
        with quiet:
            for number, wave in enumerate(plan):
                case = {"shape": "droid", "resolution": 128, "wave": number, "steps": wave.steps}
                case["fused"] = fused
                rows = run.make_rows(policies, 1, number)
                results += run.run_matrix(
                    client, wave.dataset, case, rows, settings, Path(directory)
                )
    return plan, results


class ReaderTests(unittest.TestCase):
    def test_reader_matches_tensorflow_on_the_fixture_episodes(self):
        with MINI.open("rb") as stream:
            episodes = read_episodes(stream)
        self.assertEqual([digest(episode) for episode in episodes], DIGESTS["mini_episodes"])

    @REAL_SHARD
    def test_reader_matches_tensorflow_on_every_episode_of_the_real_shard(self):
        with SHARD.open("rb") as stream:
            digests = [digest(episode) for episode in read_episodes(stream)]
        self.assertEqual(len(digests), 32)
        self.assertEqual(digests, DIGESTS["shard_episodes"])

    def test_a_truncated_stream_is_rejected(self):
        data = MINI.read_bytes()
        for cut in (5, 100, len(data) - 3):
            with self.subTest(cut=cut), self.assertRaisesRegex(ValueError, "truncated"):
                read_episodes(io.BytesIO(data[:cut]))


class FormatTests(unittest.TestCase):
    def test_episodes_object_round_trip(self):
        with MINI.open("rb") as stream:
            episodes = read_episodes(stream)
        data = droid.pack_episodes("fixture", episodes)
        header, state, images = droid.unpack_episodes(data)
        self.assertEqual([entry["steps"] for entry in header["episodes"]], [8, 6, 5])
        self.assertEqual(header["episodes"][0]["instruction"], "Close the upper drawer")
        np.testing.assert_array_equal(state, np.concatenate([e["state"] for e in episodes]))
        self.assertEqual(
            [bytes(image) for image in images], sum((e["images"] for e in episodes), [])
        )
        with self.assertRaisesRegex(ValueError, "expected a frames object"):
            droid.read_frames(data)
        with self.assertRaisesRegex(ValueError, "wrong length"):
            droid.unpack_episodes(data[:-1])
        with self.assertRaisesRegex(ValueError, "not a DROID object"):
            droid.unpack_episodes(b"x" + data)


class IngestTests(unittest.TestCase):
    def test_ingest_writes_placed_partitions_and_an_index(self):
        client, _ = local_client()
        with MINI.open("rb") as stream:
            dataset, index = ingest(client, stream, "droid-128-00000", mini_md5())
        self.assertEqual(client.dataset("droid-128-00000"), dataset)
        stored = client.store.get_manifest("node0", "datasets/droid-128-00000/droid.json")
        self.assertEqual(stored, json.loads(json.dumps(index)))
        self.assertEqual(index["source_bytes"], MINI.stat().st_size)
        self.assertEqual([episode["steps"] for episode in index["episodes"]], [8, 6, 5])
        # Three short episodes fit one partition; it goes to the first node of the pattern.
        self.assertEqual(len(dataset.shards), 1)
        self.assertEqual(dataset.shards[0].pool, "node0")
        self.assertEqual(dataset.shards[0].dtype, "droid-episodes")
        self.assertEqual(index["partitions"][0]["episodes"], [[0, 0, 8], [1, 0, 6], [2, 0, 5]])
        self.assertEqual(index["partitions"][0]["object_bytes"], dataset.shards[0].size)

    def test_a_wrong_md5_leaves_no_dataset(self):
        client, _ = local_client()
        with MINI.open("rb") as stream, self.assertRaisesRegex(ValueError, "MD5"):
            ingest(client, stream, "droid-128-00001", "AAAAAAAAAAAAAAAAAAAAAA==")
        with self.assertRaises(KeyError):
            client.dataset("droid-128-00001")

    def test_partitions_fit_the_bound_and_split_only_episodes_too_long_alone(self):
        with MINI.open("rb") as stream:
            episodes = read_episodes(stream)
        limit = 12 * 2**20
        groups = list(droid_ingest.partitions(episodes, 128, limit))
        self.assertTrue(all(droid_ingest.fits(group, 128, limit) for group in groups))
        self.assertFalse(droid_ingest.fits(episodes[:1], 128, limit))
        pieces = [piece for group in groups for piece in group]
        for episode in episodes:
            parts = [piece for piece in pieces if piece["episode"] == episode["episode"]]
            self.assertEqual([part["first_step"] for part in parts][0], 0)
            np.testing.assert_array_equal(
                np.concatenate([part["state"] for part in parts]), episode["state"]
            )
            self.assertEqual(sum((part["images"] for part in parts), []), episode["images"])
        # Larger bound: whole episodes are packed together and never split.
        whole = list(droid_ingest.partitions(episodes, 128, WORKER_MEMORY))
        self.assertEqual([[piece["episode"] for piece in group] for group in whole], [[0, 1, 2]])

    def test_the_bound_covers_object_size_and_the_frames_consumers_at_each_resolution(self):
        self.assertTrue(droid.fits_worker(droid.MAX_OBJECT_BYTES, 1, 128))
        self.assertFalse(droid.fits_worker(droid.MAX_OBJECT_BYTES + 1, 1, 128))
        # A consumer reserves 6x the frames (input, scratch, output) plus 8 MiB:
        # 6 * (596 * 3 * 128 * 128 * 3 + 64 KiB) + 8 MiB is just under 512 MiB.
        self.assertTrue(droid.fits_worker(32 * 2**20, 596, 128))
        self.assertFalse(droid.fits_worker(32 * 2**20, 597, 128))
        self.assertTrue(droid.fits_worker(8 * 2**20, 149, 256))
        self.assertFalse(droid.fits_worker(8 * 2**20, 150, 256))

    def test_a_fused_task_reserves_the_decode_task_and_the_frames_it_holds(self):
        # 4 x 66,092,448 + 3 x (596 frames + 64 KiB) + 2 x (596 state rows + 64 KiB) + 8 MiB
        # is 512 MiB. Only a partition whose frames are under 1.33 times its source (DROID's
        # are 2.17 times) can fit every unfused task and not the fused one.
        self.assertTrue(droid.fits_fused(66_092_448, 596, 128))
        self.assertFalse(droid.fits_fused(66_092_449, 596, 128))
        self.assertTrue(droid.fits_worker(droid.MAX_OBJECT_BYTES, 596, 128))

    @REAL_SHARD
    def test_every_partition_of_the_real_shard_fits_one_fused_task(self):
        with SHARD.open("rb") as stream:
            episodes = read_episodes(stream)
        parts = list(droid_ingest.partitions(episodes, 128))
        for part in parts:
            steps = sum(len(episode["state"]) for episode in part)
            size = len(droid.pack_episodes(SHARD.name, part))
            self.assertTrue(droid.fits_fused(size, steps, 128), (size, steps))
        self.assertEqual(len(parts), 15)

    def test_the_published_md5_is_found_among_gcs_hash_headers(self):
        headers = ["crc32c=VL1BHg==", "md5=kbFOhd9woKUl2zRDeqik1w=="]
        self.assertEqual(droid_ingest.published_md5(headers), "kbFOhd9woKUl2zRDeqik1w==")
        self.assertEqual(droid_ingest.published_md5(["crc32c=a, md5=b"]), "b")
        with self.assertRaisesRegex(ValueError, "no MD5"):
            droid_ingest.published_md5(["crc32c=VL1BHg=="])

    def test_cyclic_placement_starts_at_its_offset(self):
        placement = droid_ingest.CyclicPlacement((0, 0, 1, 2), 3)
        self.assertEqual([placement[index] for index in range(5)], [2, 0, 0, 1, 2])


class FunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with MINI.open("rb") as stream:
            cls.episodes = read_episodes(stream)
        cls.source = droid.pack_episodes("fixture", cls.episodes)
        cls.frames, cls.state = droid.decode_resize([cls.source], {"resolution": 128}, [])

    def test_the_fused_function_returns_the_unfused_pipelines_outputs_byte_for_byte(self):
        (video,) = droid.encode_video([self.frames], {}, [])
        (stats,) = droid.frame_stats([self.frames], {}, [])
        (parquet,) = droid.state_parquet([self.state], {}, [])
        fused = droid.droid_fused([self.source], {"resolution": 128}, [])
        self.assertEqual(fused, (video, stats, parquet))

    def test_decode_resize_gives_one_bilinear_frame_per_image_and_the_state(self):
        header, frames = droid.read_frames(self.frames)
        self.assertEqual(frames.shape, (19 * 3, 128, 128, 3))
        jpeg = self.episodes[1]["images"][4]  # episode 1, step 1, camera 1
        with Image.open(io.BytesIO(jpeg)) as image:
            self.assertEqual(image.size, (320, 180))
            expected = np.asarray(image.resize((128, 128), Image.Resampling.BILINEAR))
        np.testing.assert_array_equal(frames[8 * 3 + 4], expected)
        _, state = droid.read_state(self.state)
        np.testing.assert_array_equal(state[8:14], self.episodes[1]["state"])
        self.assertLess(0, len(self.frames) - droid.frame_bytes(19, 128))
        self.assertLess(len(self.frames) - droid.frame_bytes(19, 128), droid.HEADER_ALLOWANCE)

    def test_videos_are_lossless_per_camera_and_episode_and_reproducible(self):
        (video,) = droid.encode_video([self.frames], {}, [])
        self.assertEqual(droid.encode_video([self.frames], {}, []), (video,))
        _, frames = droid.read_frames(self.frames)
        streams = list(droid_workload.decoded_streams(video))
        self.assertEqual(
            [(s["episode"], s["camera"]) for s, _ in streams],
            [(e, c) for e in range(3) for c in range(3)],
        )
        stream, pixels = streams[5]  # episode 1, camera 2
        np.testing.assert_array_equal(pixels, frames[8 * 3 : 14 * 3][2::3])

    def test_frame_statistics_are_exact_sums_and_squares(self):
        (stats,) = droid.frame_stats([self.frames], {}, [])
        _, payload = droid.unpack(stats, "stats")
        rows = np.frombuffer(payload, dtype="<u8").reshape(-1, 6)
        _, frames = droid.read_frames(self.frames)
        self.assertEqual(len(rows), len(frames))
        values = [int(value) for value in frames[30, :, :, 1].ravel()]
        self.assertEqual(rows[30, 1], sum(values))
        self.assertEqual(rows[30, 4], sum(value * value for value in values))

    def test_state_parquet_round_trips_with_episode_step_and_instruction(self):
        (parquet,) = droid.state_parquet([self.state], {}, [])
        table = pq.read_table(io.BytesIO(parquet))
        self.assertEqual(table.num_rows, 19)
        self.assertEqual(table.column("episode").to_pylist(), [0] * 8 + [1] * 6 + [2] * 5)
        self.assertEqual(table.column("step").to_pylist()[8:10], [0, 1])
        self.assertEqual(table.column("instruction").to_pylist()[-1], "Move the cup away from you")
        np.testing.assert_array_equal(
            table.column("action_6").to_numpy(),
            np.concatenate([e["state"][:, 13] for e in self.episodes]),
        )


class CalibrationTests(unittest.TestCase):
    def test_per_function_fit_recovers_the_coefficients(self):
        rows = []
        for function, (fixed, per_input, per_output) in (
            ("a@1", (0.2, 0.0, 0.5)),
            ("b@1", (0.0, 1.5, 0.0)),
        ):
            for input_mib, output_mib in ((1, 2), (4, 1), (8, 16), (16, 3)):
                seconds = fixed + per_input * input_mib + per_output * output_mib
                stages = [{"kind": "compute", "seconds": seconds}]
                rows.append(
                    {
                        "function": function,
                        "input_bytes": input_mib * calibrate.MIB,
                        "output_bytes": output_mib * calibrate.MIB,
                        "result": {"stages": stages},
                    }
                )
        costs, errors = calibrate.fit_function_costs(rows)
        self.assertEqual([function for function, _ in costs], ["a@1", "b@1"])
        a, b = (cost for _, cost in costs)
        self.assertAlmostEqual(a.fixed_seconds, 0.2)
        self.assertAlmostEqual(a.output_seconds_per_byte * calibrate.MIB, 0.5)
        self.assertAlmostEqual(b.input_seconds_per_byte * calibrate.MIB, 1.5)
        self.assertLess(max(errors.values()), 1e-9)

    def test_droid_samples_run_every_function_where_the_data_is_and_clean_up(self):
        client, workers = local_client()
        with MINI.open("rb") as stream:
            ingest(client, stream, "droid-128-02047", placement=(2,))
        rows = calibrate.droid_samples(client, "c" * 32, "droid-128-02047", 128)
        self.assertEqual([row["function"] for row in rows], list(droid.FUNCTIONS))
        self.assertEqual({row["node"] for row in rows}, {2})
        self.assertEqual(
            rows[0]["output_bytes"], sum(o["size"] for o in rows[0]["result"]["outputs"])
        )
        costs, _ = calibrate.fit_function_costs(rows)
        self.assertEqual([function for function, _ in costs], sorted(droid.FUNCTIONS))
        self.assertFalse([key for _, key in client.store.objects if key.startswith("runs/")])
        self.assertTrue(all(not worker.jobs for worker in workers))


class WorkloadTests(unittest.TestCase):
    def test_waves_split_consecutive_partitions_by_source_bytes(self):
        refs = [
            ObjectRef(f"node{i % 3}", f"p{i}", size, "droid-episodes", 0)
            for i, size in enumerate((5, 5, 3, 9, 1))
        ]
        partitions = [(ref, 10 + index, ("d", index)) for index, ref in enumerate(refs)]
        plan = droid_workload.waves(partitions, 10)
        self.assertEqual(
            [[ref.key for ref in wave.dataset.shards] for wave in plan],
            [["p0", "p1"], ["p2"], ["p3", "p4"]],
        )
        self.assertEqual([ref.partition for ref in plan[2].dataset.shards], [0, 1])
        self.assertEqual(plan[2].steps, (13, 14))
        self.assertEqual(plan[2].sources, (("d", 3), ("d", 4)))

    def test_graph_has_two_consumers_of_the_frames_and_exact_frame_estimates(self):
        client, _ = local_client()
        with MINI.open("rb") as stream:
            ingest(client, stream, "droid-128-00000")
        (wave,) = droid_workload.waves(
            droid_workload.load_partitions(client, ["droid-128-00000"]), 2**40
        )
        graph = droid_workload.build_graph(wave.dataset, 128, wave.steps)
        functions = [task.function for task in graph.tasks.values()]
        self.assertEqual(functions, list(droid.FUNCTIONS))
        decode = graph.tasks["t000000"]
        # Exact but for float rounding of the ratio; the real size adds the header.
        self.assertAlmostEqual(decode.output_sizes[0], droid.frame_bytes(19, 128), delta=1)
        consumers = [
            task.function
            for task in graph.tasks.values()
            if any(
                getattr(ref, "task", None) == "t000000" and ref.output == 0 for ref in task.inputs
            )
        ]
        self.assertEqual(consumers, ["encode_video@1", "frame_stats@1"])


class FusedWorkloadTests(unittest.TestCase):
    def test_one_fused_task_per_partition_with_the_unfused_final_estimates(self):
        refs = [ObjectRef(f"node{i}", f"p{i}", 30 * 2**20, "droid-episodes", i) for i in range(2)]
        dataset = Dataset("d", tuple(refs))
        unfused = droid_workload.build_graph(dataset, 128, (500, 400))
        fused = droid_workload.build_fused_graph(dataset, 128, (500, 400))
        self.assertEqual([task.function for task in fused.tasks.values()], [droid.FUSED] * 2)
        self.assertEqual([task.inputs for task in fused.tasks.values()], [(ref,) for ref in refs])
        finals = {}
        for task in unfused.tasks.values():
            if task.function != "decode_resize@1":
                finals.setdefault(task.partition, []).append(task.output_sizes[0])
        for task in fused.tasks.values():
            for fused_size, unfused_size in zip(task.output_sizes, finals[task.partition]):
                self.assertAlmostEqual(fused_size, unfused_size, delta=2)
        too_dense = ObjectRef("node0", "p", droid.MAX_OBJECT_BYTES, "droid-episodes", 0)
        with self.assertRaisesRegex(ValueError, "partition 0 .* does not fit one fused task"):
            droid_workload.build_fused_graph(Dataset("d", (too_dense,)), 128, (596,))

    def test_the_fused_cost_is_the_unfused_tasks_cost_at_the_given_frames_ratio(self):
        compute = ComputeModel(
            functions=(
                ("decode_resize@1", FunctionCost(0.001, 4e-9, 5.5e-9)),
                ("encode_video@1", FunctionCost(0.0005, 6e-9, 1.35e-8)),
                ("frame_stats@1", FunctionCost(0.001, 5e-9, 0.0)),
                ("state_parquet@1", FunctionCost(0.0, 2.2e-7, 0.0)),
            )
        )
        steps = 500
        frames = droid.frame_bytes(steps, 128)
        source = round(frames / 2.16)
        ref = ObjectRef("node0", "p", source, "droid-episodes", 0)
        dataset = Dataset("d", (ref,))
        unfused = droid_workload.build_graph(dataset, 128, (steps,))
        expected = sum(
            compute.seconds(task, [unfused.size(ref) for ref in task.inputs])
            for task in unfused.tasks.values()
        )
        cost = droid_workload.fused_cost(compute, frames / source)
        (fused,) = droid_workload.build_fused_graph(dataset, 128, (steps,)).tasks.values()
        self.assertAlmostEqual(cost.seconds(fused, [source]), expected, delta=1e-6 * expected)
        # Adding the four functions' coefficients instead would charge state_parquet's
        # per-byte cost of the tiny state on every source byte.
        added = sum(cost.input_seconds_per_byte for _, cost in compute.functions)
        self.assertGreater(added * source, 3 * expected)


class EndToEndTests(unittest.TestCase):
    def test_fixture_episodes_run_in_waves_under_greedy_and_heft(self):
        client, workers = local_client()
        for shard in (0, 1):
            with MINI.open("rb") as stream:
                ingest(client, stream, f"droid-128-{shard:05d}", placement=((0,), (1,))[shard])
        plan, results = run_waves(
            client, ["droid-128-00000", "droid-128-00001"], ["greedy", "heft"], 1
        )
        self.assertEqual(len(plan), 2)
        self.assertEqual(len(results), 4)
        for result in results:
            self.assertEqual(result["droid_verification"]["decoded"], 57)
            self.assertEqual(result["dispatches"], 4)
        self.assertFalse([key for _, key in client.store.objects if key.startswith("runs/")])
        self.assertTrue(all(not worker.jobs for worker in workers))

    def test_computational_storage_and_disaggregated_modes_agree_on_every_output(self):
        cs, store, cs_transport = role_lab(cs_nodes(2))
        disagg, _, disagg_transport = role_lab(disagg_nodes(2, 2), store)
        compute = ComputeModel(functions=MEASURED)
        cs_client = Client(cs, Models.uncalibrated(2), store=store, transport=cs_transport)
        cs_client.models = dataclasses.replace(cs_client.models, compute=compute)
        disagg_client = Client(
            disagg, Models.uncalibrated(4), store=store, transport=disagg_transport
        )
        disagg_client.models = dataclasses.replace(disagg_client.models, compute=compute)
        names = ["droid-128-00000", "droid-128-00001"]
        for shard, name in enumerate(names):
            with MINI.open("rb") as stream:
                ingest(cs_client, stream, name, placement=(shard,))
        _, cs_results = run_waves(cs_client, names, ["greedy", "beam-4"], 2**40)
        _, disagg_results = run_waves(disagg_client, names, ["fifo-hash", "greedy", "heft"], 2**40)
        results = cs_results + disagg_results
        self.assertEqual([result["scheduler"] for result in results].count("beam-4"), 1)
        self.assertEqual(len({run.outputs_digest(result) for result in results}), 1)
        for result in results:
            self.assertEqual(result["droid_verification"]["decoded"], 2 * 57)
        for result in disagg_results:
            records = result["tasks"].values()
            workers = {record["worker"] for record in records}
            pools = {output["pool"] for record in records for output in record["result"]["outputs"]}
            self.assertTrue(workers and workers <= {2, 3}, workers)
            self.assertTrue(pools and pools <= {"node0", "node1"}, pools)
        self.assertFalse([key for _, key in store.objects if key.startswith("runs/")])
        self.assertEqual(len(cs_client.dataset(names[0]).shards), 1)
        self.assertEqual(len(disagg_client.dataset(names[1]).shards), 1)

    def test_the_fused_graph_stores_only_the_final_outputs_and_they_equal_the_unfused(self):
        compute = ComputeModel(functions=MEASURED)
        compute = dataclasses.replace(
            compute,
            functions=MEASURED + ((droid.FUSED, droid_workload.fused_cost(compute, 2.16)),),
        )
        client, _ = local_client(Models(compute))
        names = ["droid-128-00000", "droid-128-00001"]
        for shard, name in enumerate(names):
            with MINI.open("rb") as stream:
                ingest(client, stream, name, placement=(shard,))
        _, unfused = run_waves(client, names, ["greedy"], 2**40)
        _, fused = run_waves(client, names, ["fifo-hash", "heft", "data-local"], 2**40, fused=True)
        digests = {
            result["droid_verification"]["final_outputs_sha256"] for result in unfused + fused
        }
        self.assertEqual(len(digests), 1)
        for result in fused:
            self.assertEqual(result["dispatches"], 2)
            self.assertEqual(result["droid_verification"]["decoded"], 2 * 57)
            self.assertEqual(result["droid_verification"]["partitions"], 2)
            stored = [
                output["dtype"]
                for record in result["tasks"].values()
                for output in record["result"]["outputs"]
            ]
            self.assertEqual(sorted(stored), sorted(droid_workload.FINAL_TYPES * 2))
        written = [
            sum(
                output["size"]
                for record in result["tasks"].values()
                for output in record["result"]["outputs"]
            )
            for result in (unfused[0], fused[0])
        ]
        self.assertLess(written[1] * 3, written[0])
        self.assertFalse([key for _, key in client.store.objects if key.startswith("runs/")])

    def test_verification_rejects_a_wrong_fused_video(self):
        client, _ = local_client()
        with MINI.open("rb") as stream:
            ingest(client, stream, "droid-128-00000")
        (wave,) = droid_workload.waves(
            droid_workload.load_partitions(client, ["droid-128-00000"]), 2**40
        )
        graph = droid_workload.build_fused_graph(wave.dataset, 128, wave.steps)
        result = client.submit(graph, "fifo-hash").wait(timeout=120).metrics()
        droid_workload.verify(client.store, result, graph, 128)
        (source,) = graph.tasks["t000000"].inputs
        header, frames, _, _ = droid.decode(client.store.read(source), 128)
        video = result["tasks"]["t000000"]["result"]["outputs"][0]
        tampered = droid.video_object(header, frames[::-1])
        client.store.objects[video["pool"], video["key"]] = tampered
        video.update(size=len(tampered), sha256="")
        with self.assertRaisesRegex(AssertionError, "video pixels"):
            droid_workload.verify(client.store, result, graph, 128)

    def test_verification_rejects_wrong_statistics(self):
        client, _ = local_client()
        with MINI.open("rb") as stream:
            ingest(client, stream, "droid-128-00000")
        (wave,) = droid_workload.waves(
            droid_workload.load_partitions(client, ["droid-128-00000"]), 2**40
        )
        graph = droid_workload.build_graph(wave.dataset, 128, wave.steps)
        result = client.submit(graph, "greedy").wait(timeout=120).metrics()
        droid_workload.verify(client.store, result, graph, 128)
        output = result["tasks"]["t000002"]["result"]["outputs"][0]
        header, payload = droid.unpack(client.store.objects[output["pool"], output["key"]], "stats")
        tampered = np.frombuffer(payload, dtype="<u8") + 1
        client.store.objects[output["pool"], output["key"]] = droid.pack(header, tampered.tobytes())
        output["sha256"] = ""
        with self.assertRaisesRegex(AssertionError, "frame statistics"):
            droid_workload.verify(client.store, result, graph, 128)

    @REAL_SHARD
    def test_real_episodes_run_the_full_pipeline_under_greedy_and_heft(self):
        with SHARD.open("rb") as stream:
            prefix = io.BytesIO()
            for _ in range(5):
                head = stream.read(12)
                (length,) = struct.unpack_from("<Q", head)
                prefix.write(head + stream.read(length + 4))
        client, workers = local_client()
        prefix.seek(0)
        dataset, index = ingest(client, prefix, "droid-128-00217")
        self.assertEqual(
            [episode["steps"] for episode in index["episodes"]], [568, 200, 393, 163, 163]
        )
        self.assertGreater(len(dataset.shards), 1)
        plan, results = run_waves(client, ["droid-128-00217"], ["greedy", "heft"], 2**40)
        frames = sum(episode["steps"] for episode in index["episodes"]) * 3
        for result in results:
            self.assertEqual(result["dispatches"], 4 * len(dataset.shards))
            self.assertGreater(result["droid_verification"]["decoded"], 0)
        outputs = [run.output_signature(result) for result in results]
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(sum(s["steps"] for s in index["partitions"]) * 3, frames)
        self.assertFalse([key for _, key in client.store.objects if key.startswith("runs/")])
        self.assertTrue(all(not worker.jobs for worker in workers))


if __name__ == "__main__":
    unittest.main()
