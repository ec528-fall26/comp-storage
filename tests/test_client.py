"""Unit tests for the job-API client (standard library only, no network).

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import cstore_client as cc  # noqa: E402
from cstore_client import cli  # noqa: E402
from cstore_client.request import RequestError, SliceRequest  # noqa: E402
from cstore_client.transport import MockTransport, _mock_catalog, plan_request  # noqa: E402


class RequestTests(unittest.TestCase):
    def test_empty_request_is_valid_with_defaults(self):
        r = SliceRequest.from_dict({})
        self.assertEqual(r.trim.cameras, list(cc.CAMERAS))
        self.assertEqual(r.trim.frame_stride, 1)
        self.assertEqual(r.select.max_episodes, 50)

    def test_build_normalises_keywords(self):
        r = SliceRequest.build(tasks="Put, pick ,put", objects=["CUP"])
        self.assertEqual(r.select.task_keywords, ["put", "pick"])
        self.assertEqual(r.select.object_keywords, ["cup"])

    def test_round_trip_json(self):
        r = SliceRequest.build(tasks=["put"], drop_idle=True, cameras=["wrist"], frame_stride=15)
        self.assertEqual(SliceRequest.from_dict(json.loads(r.to_json())), r)

    def test_rejects_bad_values(self):
        for kwargs in ({"cameras": ["top"]}, {"frame_stride": 0}, {"max_episodes": -1},
                       {"output_format": "mp4"}, {"frame_stride": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RequestError):
                SliceRequest.build(**kwargs)

    def test_rejects_unknown_fields(self):
        with self.assertRaises(RequestError):
            SliceRequest.from_dict({"select": {"scene": ["kitchen"]}})
        with self.assertRaises(RequestError):
            SliceRequest.from_dict({"filters": {}})


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.catalog = _mock_catalog()

    def test_keywords_match_instruction(self):
        req = SliceRequest.build(tasks=["put"], objects=["cup"], max_episodes=None).to_dict()
        plan = plan_request(req, self.catalog)
        self.assertGreater(plan["episodes_matched"], 0)
        by_id = {e["episode_id"]: e for e in self.catalog}
        for part in plan["nodes"].values():
            for ep in part["episodes"]:
                words = by_id[ep]["instruction"].split()
                self.assertIn("put", words)
                self.assertIn("cup", words)

    def test_each_episode_runs_on_the_node_holding_it(self):
        req = SliceRequest.build(max_episodes=None).to_dict()
        plan = plan_request(req, self.catalog)
        by_id = {e["episode_id"]: e for e in self.catalog}
        for node, part in plan["nodes"].items():
            for ep in part["episodes"]:
                self.assertEqual(by_id[ep]["node"], node)
                self.assertIn(by_id[ep]["shard"], part["shards"])

    def test_trimming_shrinks_the_estimate(self):
        full = plan_request(SliceRequest.build(tasks=["put"]).to_dict(), self.catalog)
        trimmed = plan_request(SliceRequest.build(tasks=["put"], drop_idle=True, cameras=["wrist"],
                                                  frame_stride=15).to_dict(), self.catalog)
        self.assertLess(trimmed["estimate"]["bytes_out_in_place"], full["estimate"]["bytes_out_in_place"])
        self.assertEqual(trimmed["estimate"]["bytes_baseline_shards"], full["estimate"]["bytes_baseline_shards"])

    def test_max_episodes_caps(self):
        plan = plan_request(SliceRequest.build(max_episodes=7).to_dict(), self.catalog)
        self.assertEqual(plan["episodes_matched"], 7)


class MockClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log: list[str] = []
        self.t = MockTransport(state_dir=self.tmp.name, delay=0, log=self.log.append)

    def tearDown(self):
        self.tmp.cleanup()

    def test_submit_wait_download(self):
        job = cc.submit(tasks=["put"], objects=["cup"], cameras=["wrist"], transport=self.t)
        final = job.wait(poll=0)
        self.assertEqual(final["status"], "done")
        files = job.download(Path(self.tmp.name) / "out")
        manifest = json.loads(files[0].read_text())
        self.assertEqual(manifest["job_id"], job.job_id)
        self.assertTrue(manifest["mock"])
        self.assertTrue(any(line.startswith("[mock] POST /jobs") for line in self.log))

    def test_node_jobs_match_existing_queue_format(self):
        job = cc.submit(tasks=["pick"], transport=self.t)
        job.wait(poll=0)
        manifest = json.loads(job.download(Path(self.tmp.name) / "o")[0].read_text())
        for nj in manifest["queued_node_jobs"]:
            self.assertEqual(set(nj), {"job_id", "function", "inputs", "output", "params"})

    def test_unknown_job(self):
        with self.assertRaises(cc.TransportError):
            self.t.status("job-nope")

    def test_state_survives_new_transport(self):
        job = cc.submit(transport=self.t)
        again = MockTransport(state_dir=self.tmp.name, delay=0)
        self.assertEqual(again.status(job.job_id)["job_id"], job.job_id)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"CSTORE_MOCK_DIR": self.tmp.name, "CSTORE_MOCK_DELAY": "0"}
        self._old = {k: __import__("os").environ.get(k) for k in self.env}
        __import__("os").environ.update(self.env)

    def tearDown(self):
        import os
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_json_submit_and_download(self):
        dest = Path(self.tmp.name) / "slice"
        code, out, err = self.run_cli("--json", "submit", "--tasks", "put", "--download", str(dest))
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["status"], "done")
        self.assertTrue(Path(result["files"][0]).exists())
        self.assertEqual(err, "")  # --json keeps mock chatter off both streams

    def test_bad_request_exit_code(self):
        code, _, err = self.run_cli("submit", "--cameras", "top")
        self.assertEqual(code, 2)
        self.assertIn("bad request", err)

    def test_config_then_flag_override(self):
        cfg = Path(self.tmp.name) / "req.json"
        self.assertEqual(self.run_cli("init", str(cfg))[0], 0)
        code, out, _ = self.run_cli("submit", "--config", str(cfg), "--frame-stride", "5", "--dry-run")
        self.assertEqual(code, 0)
        req = json.loads(out)
        self.assertEqual(req["trim"]["frame_stride"], 5)
        self.assertEqual(req["select"]["task_keywords"], ["put"])

    def test_options(self):
        code, out, _ = self.run_cli("--json", "options")
        self.assertEqual(code, 0)
        self.assertIn("cup", json.loads(out)["objects"])

    def test_bad_server(self):
        code, _, err = self.run_cli("--server", "ftp://x", "options")
        self.assertEqual(code, 1)

    def test_interactive_answers(self):
        answers = iter(["1,3", "", "", "10", "y", "1", "15"])
        start = SliceRequest()
        opts = {"tasks": ["put", "move", "pick"], "objects": ["cup"], "labs": ["IRIS"]}
        req = cli.interactive_request(opts, start, ask=lambda _: next(answers), out=lambda _: None)
        self.assertEqual(req.select.task_keywords, ["put", "pick"])
        self.assertEqual(req.select.max_episodes, 10)
        self.assertTrue(req.trim.drop_idle)
        self.assertEqual(req.trim.cameras, ["wrist"])
        self.assertEqual(req.trim.frame_stride, 15)


class HttpTransportTests(unittest.TestCase):
    """Run the real HttpTransport against a tiny in-process server that speaks
    the v0 endpoints, so swapping mock -> http later is only a setting."""

    def setUp(self):
        import http.server
        import threading

        jobs: dict[str, dict] = {}
        test = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json", headers=None):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                test.auth = self.headers.get("Authorization")
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path != "/jobs" or "select" not in body:
                    return self._send(400, {"error": "bad"})
                jobs["j1"] = body
                self._send(201, {"job_id": "j1"})

            def do_GET(self):
                if self.path == "/options":
                    return self._send(200, {"tasks": ["put"]})
                if self.path == "/jobs/j1":
                    return self._send(200, {"job_id": "j1", "status": "done"})
                if self.path == "/jobs/j1/result":
                    return self._send(200, b"slice-bytes", "application/octet-stream",
                                      {"Content-Disposition": 'attachment; filename="j1.tar"'})
                self._send(404, {"error": "no such job"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_round_trip(self):
        t = cc.HttpTransport(self.url, token="secret")
        job = cc.submit(tasks=["put"], transport=t)
        self.assertEqual(job.job_id, "j1")
        self.assertEqual(self.auth, "Bearer secret")
        self.assertEqual(job.wait(poll=0)["status"], "done")
        files = job.download(self.tmp.name)
        self.assertEqual(files[0].name, "j1.tar")
        self.assertEqual(files[0].read_bytes(), b"slice-bytes")
        self.assertEqual(t.options(), {"tasks": ["put"]})

    def test_http_errors_become_transport_errors(self):
        t = cc.HttpTransport(self.url)
        with self.assertRaises(cc.TransportError):
            t.status("missing")
        with self.assertRaises(cc.TransportError):
            cc.HttpTransport("http://127.0.0.1:1").options()


if __name__ == "__main__":
    unittest.main()
