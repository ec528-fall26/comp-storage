"""Unit tests for the host-side code (standard library only, no Docker/Ceph).

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cstore import config, jobqueue, netcount, report  # noqa: E402
from cstore.functions import droid_rlds_to_lerobot  # noqa: E402
from cstore.jobs import Job, droid_shard_job  # noqa: E402
from cstore.worker import publish_tree, tree_size  # noqa: E402

NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000      10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0
  ens5:123456789  4000    0    0    0     0          0         0 98765432    3000    0    0    0     0       0          0
"""
ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
ens5\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
ens5\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0
"""


def make_cfg(root: Path, **overrides) -> config.Config:
    env = {"CSTORE_ENV": "/nonexistent", "CSTORE_ROOT": str(root), "SCRATCH_DIR": str(root / "scratch"), **overrides}
    with mock.patch.dict(os.environ, env, clear=False):
        return config.load()


class NetCountTest(unittest.TestCase):
    def test_parse(self):
        stats = netcount.parse_net_dev(NET_DEV)
        self.assertEqual(stats["ens5"]["rx_bytes"], 123456789)
        self.assertEqual(stats["ens5"]["tx_bytes"], 98765432)
        self.assertEqual(stats["lo"]["rx_packets"], 10)

    def test_default_route(self):
        self.assertEqual(netcount.default_route_iface(ROUTE), "ens5")

    def test_delta(self):
        a = {"time": 10.0, "iface": "ens5", "rx_bytes": 100, "tx_bytes": 50, "rx_packets": 1, "tx_packets": 1}
        b = {"time": 12.5, "iface": "ens5", "rx_bytes": 1100, "tx_bytes": 250, "rx_packets": 5, "tx_packets": 3}
        d = netcount.delta(a, b)
        self.assertEqual((d["rx_bytes"], d["tx_bytes"], d["total_bytes"], d["seconds"]), (1000, 200, 1200, 2.5))

    def test_snapshot_reads_proc(self):
        snap = netcount.snapshot()
        self.assertIn("rx_bytes", snap)

    def test_snapshot_without_proc(self):  # macOS running the local smoke test
        with mock.patch.object(netcount, "PROC_NET_DEV", Path("/nonexistent/net/dev")):
            a, b = netcount.snapshot(), netcount.snapshot()
        self.assertEqual(a["iface"], "unavailable")
        self.assertEqual(netcount.delta(a, b)["total_bytes"], 0)

    def test_fs_type_without_findmnt(self):  # macOS has no findmnt
        from cstore import runner

        with mock.patch.object(runner.subprocess, "run", side_effect=FileNotFoundError("findmnt")):
            self.assertIsNone(runner.fs_type(Path("/")))


class ConfigTest(unittest.TestCase):
    def test_env_file_and_precedence(self):
        with tempfile.TemporaryDirectory() as d:
            env_file = Path(d) / "cluster.env"
            env_file.write_text(
                '# comment\nexport CSTORE_ROOT=/mnt/x\nDROID_SHARDS="3 5"  # trailing\nWORKER_CPUS=7\n'
            )
            with mock.patch.dict(os.environ, {"CSTORE_ENV": str(env_file), "WORKER_CPUS": "2"}, clear=False):
                os.environ.pop("CSTORE_ROOT", None)
                cfg = config.load()
            self.assertEqual(cfg.root, Path("/mnt/x"))
            self.assertEqual(cfg.droid_shards, (3, 5))
            self.assertEqual(cfg.worker_cpus, "2")  # the environment wins over the file
            self.assertEqual(cfg.droid_version_dir, Path("/mnt/x/datasets/droid/rlds/1.0.1"))


class JobTest(unittest.TestCase):
    def test_droid_shard_job(self):
        job = droid_shard_job(8, output="outputs/r/separate/shard-00008")
        self.assertEqual(job.job_id, "shard-00008")
        self.assertIn("datasets/droid/rlds/1.0.1/droid_101-train.tfrecord-00008-of-02048", job.inputs)
        self.assertEqual(job.params, {"raw_dir": "datasets/droid/rlds", "num_shards": 2048, "shard_index": 8})

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            job = droid_shard_job(2, output="o")
            job.save(Path(d) / "j.json")
            self.assertEqual(Job.load(Path(d) / "j.json"), job)


class QueueTest(unittest.TestCase):
    def test_submit_claim_finish(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_cfg(Path(d))
            a = droid_shard_job(2, output="out/a", job_id="a")
            b = droid_shard_job(8, output="out/b", job_id="b")
            b.submitted_at = a.submitted_at + 1
            jobqueue.submit(cfg, b)
            jobqueue.submit(cfg, a)
            with self.assertRaises(ValueError):
                jobqueue.submit(cfg, a)  # duplicate id
            job, path = jobqueue.claim(cfg, worker="w1")
            self.assertEqual(job.job_id, "a")  # oldest first
            self.assertEqual(path.parent.name, "running")
            done = jobqueue.finish(cfg, path, True, {"wall_s": 1.0})
            self.assertEqual(done.parent.name, "done")
            self.assertEqual(json.loads(done.read_text())["state"]["claimed_by"], "w1")
            states = [s for s, _ in jobqueue.entries(cfg)]
            self.assertEqual(sorted(states), ["done", "pending"])
            self.assertEqual(jobqueue.known_job_ids(cfg), {"a", "b"})

    def test_claim_race_loser_moves_on(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_cfg(Path(d))
            jobqueue.submit(cfg, droid_shard_job(2, output="o", job_id="only"))
            real_rename = os.rename

            def other_worker_wins(src, dst):  # someone else renamed it first
                if "pending" in str(src):
                    raise FileNotFoundError(src)
                return real_rename(src, dst)

            with mock.patch("cstore.jobqueue.os.rename", other_worker_wins):
                self.assertIsNone(jobqueue.claim(cfg))

    def test_requeue(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_cfg(Path(d))
            jobqueue.submit(cfg, droid_shard_job(2, output="o", job_id="x"))
            _, path = jobqueue.claim(cfg)
            jobqueue.finish(cfg, path, False, {"error": "boom"})
            jobqueue.requeue(cfg, "x")
            self.assertEqual([s for s, _ in jobqueue.entries(cfg)], ["pending"])


class PublishTest(unittest.TestCase):
    def test_publish_tree(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            (src / "meta").mkdir(parents=True)
            (src / "meta" / "info.json").write_text("{}")
            (src / "data.bin").write_bytes(b"x" * 1000)
            (src / "empty").mkdir()
            dst = Path(d) / "cephfs" / "outputs" / "run" / "shard"
            info = publish_tree(src, dst)
            self.assertEqual(info, {"bytes": 1002, "files": 2})
            self.assertTrue((dst / "empty").is_dir())
            self.assertEqual(tree_size(dst), (1002, 2))
            self.assertEqual([p.name for p in dst.parent.iterdir()], ["shard"])  # no partial left
            with self.assertRaises(FileExistsError):
                publish_tree(src, dst)


class ConverterPinTest(unittest.TestCase):
    def test_vendored_port_droid_is_unmodified(self):
        digest = hashlib.sha256(droid_rlds_to_lerobot.PORT_DROID.read_bytes()).hexdigest()
        self.assertEqual(digest, droid_rlds_to_lerobot.PORT_DROID_SHA256)

    def test_expected_episodes(self):
        with tempfile.TemporaryDirectory() as d:
            info = Path(d) / "dataset_info.json"
            info.write_text(json.dumps({"splits": [{"name": "train", "shardLengths": ["4", "0", "3"]}]}))
            self.assertEqual(droid_rlds_to_lerobot.expected_episodes(info, 2), 3)
            self.assertEqual(droid_rlds_to_lerobot.expected_episodes(info, None), 7)


class ReportTest(unittest.TestCase):
    def _record(self, mode, job_id, wall, cross, in_b, out_b):
        return {
            "ok": True,
            "mode": mode,
            "wall_s": wall,
            "job": {"job_id": job_id},
            "worker": {"episodes": 3, "frames": 140, "input_bytes": in_b, "output_bytes": out_b,
                       "convert_s": wall - 3, "writeback_s": 1.0},
            "net_storage_node": {"total_bytes": cross, "rx_bytes": cross // 4, "tx_bytes": cross - cross // 4},
        }  # fmt: skip

    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_cfg(Path(d))
            base = cfg.results / "r1"
            for mode, cross in (("separate", 1_300_000_000), ("storage", 300_000)):
                (base / mode).mkdir(parents=True)
                rec = self._record(mode, "shard-00002", 200.0, cross, 1_000_000_000, 250_000_000)
                (base / mode / "shard-00002.json").write_text(json.dumps(rec))
            (base / "verify").mkdir()
            (base / "verify" / "compare-shard-00002.json").write_text(
                json.dumps({"comparison": {"identical": True, "same_content": True}})
            )
            text, summary = report.summarize(cfg, "r1")
            f = summary["findings"]
            self.assertAlmostEqual(f["cross_host_reduction"], 1 - 300_000 / 1_300_000_000)
            self.assertAlmostEqual(f["separate_cross_host_over_input_plus_output"], 1.3e9 / 1.25e9)
            self.assertEqual(f["outputs_byte_identical"], "1/1")
            self.assertIn("byte-identical", text)
            self.assertTrue((base / "summary.svg").read_text().startswith("<svg"))


class CliTest(unittest.TestCase):
    def test_parser_has_every_command(self):
        from cstore.cli import build_parser

        sub = next(a for a in build_parser()._actions if a.dest == "cmd")
        for name in ("check", "load-droid", "bench", "verify-run", "service", "submit", "status", "report", "smoke"):
            self.assertIn(name, sub.choices)




class LoaderTest(unittest.TestCase):
    """Download, checksum and publish logic against a local HTTP server."""

    def setUp(self):
        import base64
        import functools
        import http.server
        import threading

        from cstore import loader

        self.loader = loader
        self.tmp = tempfile.TemporaryDirectory()
        served = Path(self.tmp.name) / "served"
        served.mkdir()
        info = {"splits": [{"name": "train", "shardLengths": ["5", "0", "7"] + ["0"] * 2045}]}
        files = {
            "dataset_info.json": json.dumps(info).encode(),
            "features.json": b"{}",
            "droid_101-train.tfrecord-00002-of-02048": os.urandom(300_000),
        }
        self.pinned = {}
        for name, data in files.items():
            (served / name).write_bytes(data)
            self.pinned[name] = (len(data), base64.b64encode(hashlib.md5(data).digest()).decode())
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
        handler.log_message = lambda *a, **k: None
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def test_load_verifies_and_skips(self):
        cfg = make_cfg(Path(self.tmp.name) / "root")
        with mock.patch.object(self.loader, "PUBLIC_URL", self.url), mock.patch.object(self.loader, "PINNED", self.pinned):
            summary = self.loader.load(cfg, [2], log=lambda m: None)
            self.assertEqual(summary["episodes_per_shard"], {2: 7})
            self.assertEqual(summary["downloaded_bytes"], sum(size for size, _ in self.pinned.values()))
            names = sorted(p.name for p in cfg.droid_version_dir.iterdir())
            self.assertEqual(names, sorted(self.pinned))  # nothing partial left behind
            again = self.loader.load(cfg, [2], log=lambda m: None)
            self.assertEqual(again["downloaded_bytes"], 0)  # verified copies are skipped

    def test_checksum_mismatch_publishes_nothing(self):
        cfg = make_cfg(Path(self.tmp.name) / "root")
        bad = dict(self.pinned)
        size, _ = bad["features.json"]
        bad["features.json"] = (size, "AAAAAAAAAAAAAAAAAAAAAA==")
        cfg.droid_version_dir.mkdir(parents=True)
        with mock.patch.object(self.loader, "PUBLIC_URL", self.url), mock.patch.object(self.loader, "PINNED", bad):
            with self.assertRaises(RuntimeError):
                self.loader.fetch("features.json", cfg.droid_version_dir, log=lambda m: None)
        self.assertEqual(list(cfg.droid_version_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
