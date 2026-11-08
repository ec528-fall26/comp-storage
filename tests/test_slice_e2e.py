"""End-to-end test of the Demo 3 slice API, on synthetic DROID shards.

    python3 -m unittest tests.test_slice_e2e -v

Runs the real pieces on this machine: the background service (in venv mode,
no Docker), the HTTP API server, and the cstore-client command. A local
folder stands in for CephFS. Needs tensorflow-datasets, fastapi and uvicorn;
skipped otherwise.

The request contract under test:

    POST /jobs  {"keywords": ["put", "cup"], "max_episodes": 20}

An episode is kept if one of its instructions contains ALL the keywords as
whole words (case-insensitive). The result must be a valid RLDS dataset.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATASET_DIR = "datasets/droid/rlds/1.0.1"

try:
    import fastapi  # noqa: F401
    import tensorflow as tf
    import tensorflow_datasets as tfds
    import uvicorn  # noqa: F401

    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def reference_matches(shard_files: list[Path], keywords: list[str]) -> list[str]:
    """Independent, deliberately plain scan: file_path of every matching episode.

    Written separately from the filter so the test does not just repeat its logic.
    """
    wanted = {k.lower() for k in keywords}
    found = []
    for f in shard_files:
        for raw in tf.data.TFRecordDataset(str(f)):
            feats = tf.train.Example.FromString(raw.numpy()).features.feature
            texts = set()
            for key in ("steps/language_instruction", "steps/language_instruction_2", "steps/language_instruction_3"):
                if key in feats:
                    texts.update(v.decode() for v in feats[key].bytes_list.value)
            if any(wanted <= set(re.findall(r"[a-z0-9]+", t.lower())) for t in texts if t):
                found.append(feats["episode_metadata/file_path"].bytes_list.value[0].decode())
    return found


def loaded_file_paths(out_dir: Path) -> list[str]:
    ds = tfds.builder_from_directory(str(out_dir)).as_dataset(split="train")
    return [ep["episode_metadata"]["file_path"].numpy().decode() for ep in ds]


@unittest.skipUnless(HAVE_DEPS, "needs tensorflow-datasets, fastapi and uvicorn")
class SliceEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cstore-e2e-"))
        cls.root = cls.tmp / "root"
        cls.port = _free_port()
        cls.api = f"http://127.0.0.1:{cls.port}"
        cls.env = dict(
            os.environ,
            CSTORE_ENV="/dev/null",
            CSTORE_ROOT=str(cls.root),
            SCRATCH_DIR=str(cls.tmp / "scratch"),
            CSTORE_RUNTIME="venv",
            WORKER_PYTHON=sys.executable,
            PYTHONPATH=str(REPO / "src"),
            TF_CPP_MIN_LOG_LEVEL="3",
        )
        subprocess.run(
            [sys.executable, "-m", "cstore.functions.synthetic_droid", "--out", str(cls.root / "datasets/droid/rlds"),
             "--shards", "2", "8", "--episodes-per-shard", "8", "--min-steps", "12", "--max-steps", "18"],
            env=cls.env, check=True, capture_output=True,
        )  # fmt: skip
        cls.shards = sorted((cls.root / DATASET_DIR).glob("droid_101-train.tfrecord-*"))
        log = open(cls.tmp / "servers.log", "w")
        cls.procs = [
            subprocess.Popen([sys.executable, "-m", "cstore", "service", "--poll", "0.5"],
                             env=cls.env, stdout=log, stderr=subprocess.STDOUT),
            subprocess.Popen([sys.executable, "-m", "uvicorn", "cstore.api:app", "--port", str(cls.port)],
                             env=cls.env, stdout=log, stderr=subprocess.STDOUT),
        ]  # fmt: skip
        for _ in range(60):
            try:
                if _http("GET", f"{cls.api}/health")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("API server did not start; see " + str(cls.tmp / "servers.log"))

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.terminate()
            p.wait(timeout=20)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_job(self, body: dict) -> dict:
        code, resp = _http("POST", f"{self.api}/jobs", body)
        self.assertEqual(code, 202, resp)
        job_id = resp["job_id"]
        for _ in range(240):
            code, status = _http("GET", f"{self.api}/jobs/{job_id}")
            self.assertEqual(code, 200, status)
            if status["status"] in ("done", "failed"):
                return status
            time.sleep(0.5)
        self.fail(f"job {job_id} did not finish")

    def test_keeps_exactly_the_episodes_with_all_keywords(self):
        expected = reference_matches(self.shards, ["put", "cup"])
        broader = reference_matches(self.shards, ["cup"])
        self.assertTrue(0 < len(expected) < len(broader), "test data must tell ALL apart from ANY")

        status = self.run_job({"keywords": ["Put", "cup"], "max_episodes": 100})
        self.assertEqual(status["status"], "done", status)
        out = self.root / status["output"]
        kept = loaded_file_paths(out)  # also proves the slice is valid RLDS
        self.assertEqual(sorted(kept), sorted(expected))
        self.assertEqual(status["episodes_kept"], len(expected))
        self.assertEqual(status["episodes_scanned"], 16)
        self.assertLess(status["bytes_out"], status["bytes_scanned"])

    def test_max_episodes_caps_the_result(self):
        matches = reference_matches(self.shards, ["cup"])
        status = self.run_job({"keywords": ["cup"], "max_episodes": 2})
        self.assertEqual(status["status"], "done", status)
        kept = loaded_file_paths(self.root / status["output"])
        self.assertEqual(len(kept), 2)
        self.assertTrue(set(kept) <= set(matches))

    def test_no_match_reports_zero_and_writes_only_the_manifest(self):
        status = self.run_job({"keywords": ["cups"], "max_episodes": 10})  # whole words only
        self.assertEqual(status["status"], "done", status)
        self.assertEqual(status["episodes_kept"], 0)
        self.assertEqual(sorted(p.name for p in (self.root / status["output"]).iterdir()), ["manifest.json"])

    def test_bad_requests_are_rejected(self):
        bad = [
            {},
            {"keywords": []},
            {"keywords": "cup"},
            {"keywords": ["cup", 3]},
            {"keywords": ["cup"], "max_episodes": 0},
            {"select": {"task_keywords": ["cup"]}},  # the old request shape
        ]
        for body in bad:
            with self.subTest(body=body):
                code, _ = _http("POST", f"{self.api}/jobs", body)
                self.assertIn(code, (400, 422), f"{body} was accepted")

    def test_unknown_job_is_404(self):
        self.assertEqual(_http("GET", f"{self.api}/jobs/nope")[0], 404)

    def test_client_command(self):
        r = subprocess.run(
            [str(REPO / "bin/cstore-client"), "--server", self.api, "--keywords", "close,drawer", "--max-episodes", "3"],
            env=self.env, capture_output=True, text=True, timeout=180,
        )  # fmt: skip
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("done", r.stdout)
        self.assertIn("episodes kept: 3", r.stdout)


if __name__ == "__main__":
    unittest.main()
