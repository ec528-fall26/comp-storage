"""How the client talks to the cluster.

The client never builds HTTP calls itself; it hands a request to a
*transport*. Two transports implement the same four methods:

    MockTransport   no network. Prints the HTTP call it would make, keeps job
                    state in a local folder, and simulates the server: which
                    episodes match, which storage nodes run which part, and
                    how many bytes move. For developing the client before the
                    server exists, and for tests.
    HttpTransport   the real thing: talks to the API server on a storage node
                    (POST /jobs, GET /jobs/<id>, GET /jobs/<id>/result,
                    GET /options). Untested until that server exists.

Pick one with make_transport("mock") or make_transport("http://<ip>:8000").
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .request import CAMERAS


class TransportError(RuntimeError):
    """The server (real or mock) could not do what was asked."""


class Transport:
    def submit(self, request: dict[str, Any]) -> str:
        raise NotImplementedError

    def status(self, job_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def download(self, job_id: str, dest: Path) -> list[Path]:
        raise NotImplementedError

    def options(self) -> dict[str, list[str]]:
        raise NotImplementedError


def make_transport(server: str | None, log: Callable[[str], None] | None = None) -> Transport:
    server = server or os.environ.get("CSTORE_SERVER") or "mock"
    if server == "mock":
        return MockTransport(log=log)
    if server.startswith(("http://", "https://")):
        return HttpTransport(server, token=os.environ.get("CSTORE_TOKEN"))
    raise TransportError(f"server must be 'mock' or an http(s):// URL, got {server!r}")


# ---------------------------------------------------------------------------
# Real HTTP transport
# ---------------------------------------------------------------------------

class HttpTransport(Transport):
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None) -> urllib.request.addinfourl:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise TransportError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise TransportError(f"cannot reach {self.base}: {e.reason}") from None

    def _json(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        with self._call(method, path, body) as r:
            return json.loads(r.read().decode())

    def submit(self, request: dict[str, Any]) -> str:
        return self._json("POST", "/jobs", request)["job_id"]

    def status(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/jobs/{job_id}")

    def download(self, job_id: str, dest: Path) -> list[Path]:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        with self._call("GET", f"/jobs/{job_id}/result") as r:
            name = r.headers.get_filename() or f"{job_id}-result.bin"
            out = dest / Path(name).name
            with open(out, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        return [out]

    def options(self) -> dict[str, list[str]]:
        return self._json("GET", "/options")


# ---------------------------------------------------------------------------
# Mock transport: a local stand-in for the API server + storage nodes
# ---------------------------------------------------------------------------

# Values a user can pick from. Tasks and objects follow the DROID visualizer's
# categories (derived from the language instructions); labs are DROID's
# contributing labs. All of this is mock data until GET /options exists.
MOCK_OPTIONS = {
    "tasks": ["put", "move", "pick", "remove", "take", "open", "close", "place", "turn"],
    "objects": ["marker", "cloth", "cup", "bottle", "block", "drawer", "lid", "mug", "bowl", "towel"],
    "labs": ["AUTOLab", "CLVR", "GuptaLab", "ILIAD", "IPRL", "IRIS", "PennPAL", "RAD", "RAIL", "REAL", "RPL", "TRI", "WEIRD"],
    "cameras": list(CAMERAS),
}

# Rough sizes for byte estimates, from the Demo 2 shard (649 MB, 39 episodes).
SHARD_BYTES = 650_000_000
EPISODES_PER_SHARD = 40
IMAGE_SHARE = 0.95          # images are almost all of an RLDS episode's bytes
IDLE_SHARE = 0.30           # assumed fraction of idle steps (placeholder)
MOCK_NODES = ("storage-1", "storage-2", "storage-3")
MOCK_SHARDS_PER_NODE = 4
SHARD_TEMPLATE = "droid_101-train.tfrecord-{index:05d}-of-02048"


def _mb(n: int) -> str:
    return f"{n / 1e6:,.1f} MB"


def _mock_catalog(seed: int = 528) -> list[dict[str, Any]]:
    """A fake, deterministic episode catalog spread across three nodes.

    Stands in for the location index (D3): every episode has an id, a shard,
    the node that holds the shard, a lab, and a language instruction.
    """
    rng = random.Random(seed)
    episodes = []
    shard = 0
    for node in MOCK_NODES:
        for _ in range(MOCK_SHARDS_PER_NODE):
            for k in range(EPISODES_PER_SHARD):
                task = rng.choice(MOCK_OPTIONS["tasks"])
                obj = rng.choice(MOCK_OPTIONS["objects"])
                place = rng.choice(["in the bowl", "on the table", "to the left", "into the drawer", ""])
                episodes.append({
                    "episode_id": f"ep-{shard:05d}-{k:02d}",
                    "shard": SHARD_TEMPLATE.format(index=shard),
                    "node": node,
                    "lab": rng.choice(MOCK_OPTIONS["labs"]),
                    "instruction": f"{task} the {obj} {place}".strip(),
                })
            shard += 1
    return episodes


def plan_request(request: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate what the server would do: match episodes and split by node.

    Matching rule (v0): an episode matches if its instruction contains any
    task keyword AND any object keyword (an empty list matches everything),
    its lab is listed (if labs are given), and its id is listed (if ids are
    given). Then cap at max_episodes.
    """
    s, t = request["select"], request["trim"]
    tasks, objects = s["task_keywords"], s["object_keywords"]
    labs, ids = set(s["labs"]), set(s["episode_ids"])

    def words(text: str) -> set[str]:
        return set(text.lower().split())

    matched = []
    for ep in catalog:
        w = words(ep["instruction"])
        if tasks and not any(k in w for k in tasks):
            continue
        if objects and not any(k in w for k in objects):
            continue
        if labs and ep["lab"] not in labs:
            continue
        if ids and ep["episode_id"] not in ids:
            continue
        matched.append(ep)
    if s["max_episodes"] is not None:
        matched = matched[: s["max_episodes"]]

    # Byte estimates. In place: only the trimmed slice crosses the network.
    # Baseline: a compute node must pull every shard that holds a match.
    ep_bytes = SHARD_BYTES / EPISODES_PER_SHARD
    keep = (1 - IMAGE_SHARE) + IMAGE_SHARE * len(t["cameras"]) / len(CAMERAS)
    keep /= t["frame_stride"]
    if t["drop_idle"]:
        keep *= 1 - IDLE_SHARE
    shards = sorted({ep["shard"] for ep in matched})

    per_node: dict[str, dict[str, Any]] = {}
    for ep in matched:
        n = per_node.setdefault(ep["node"], {"shards": set(), "episodes": []})
        n["shards"].add(ep["shard"])
        n["episodes"].append(ep["episode_id"])

    return {
        "episodes_matched": len(matched),
        "shards_touched": len(shards),
        "nodes": {
            node: {"shards": sorted(v["shards"]), "episodes": v["episodes"]}
            for node, v in sorted(per_node.items())
        },
        "estimate": {
            "bytes_out_in_place": int(len(matched) * ep_bytes * keep),
            "bytes_baseline_shards": len(shards) * SHARD_BYTES,
            "note": "mock estimate from Demo 2 shard sizes; idle share is a placeholder",
        },
    }


def node_jobs(job_id: str, request: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """The per-node jobs the server would write into the CephFS queue.

    Same shape as cstore.jobs.Job (job_id, function, inputs, output, params),
    so the existing queue and service can run them unchanged.
    """
    out = []
    for node, part in plan["nodes"].items():
        out.append({
            "job_id": f"{job_id}-{node}",
            "function": "droid_slice",
            "inputs": [
                "datasets/droid/rlds/1.0.1/dataset_info.json",
                "datasets/droid/rlds/1.0.1/features.json",
                *[f"datasets/droid/rlds/1.0.1/{s}" for s in part["shards"]],
            ],
            "output": f"outputs/slices/{job_id}/{node}",
            "params": {"request": request, "episodes": part["episodes"], "run_on": node},
        })
    return out


class MockTransport(Transport):
    """Local stand-in for the server. State lives in a folder so separate CLI
    runs (submit, then status, then download) see the same jobs.

    Simulated timing: a job is 'queued' for the first `delay` seconds, then
    'running' for `delay` more, then 'done'. Set CSTORE_MOCK_DELAY=0 for
    instant jobs.
    """

    def __init__(self, state_dir: str | Path | None = None, delay: float | None = None,
                 log: Callable[[str], None] | None = None):
        default_dir = Path(tempfile.gettempdir()) / "cstore-client-mock"
        self.dir = Path(state_dir or os.environ.get("CSTORE_MOCK_DIR") or default_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.delay = float(os.environ.get("CSTORE_MOCK_DELAY", 2.0) if delay is None else delay)
        self.log = log or (lambda msg: None)
        self.catalog = _mock_catalog()

    def _http(self, method: str, path: str, body: dict | None = None) -> None:
        line = f"[mock] {method} {path}"
        if body is not None:
            line += "  body=" + json.dumps(body, separators=(",", ":"))
        self.log(line)

    def _path(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
            raise TransportError(f"bad job id {job_id!r}")
        return self.dir / f"{job_id}.json"

    def _load(self, job_id: str) -> dict[str, Any]:
        p = self._path(job_id)
        if not p.exists():
            raise TransportError(f"no job {job_id!r} (mock state in {self.dir})")
        return json.loads(p.read_text())

    def submit(self, request: dict[str, Any]) -> str:
        self._http("POST", "/jobs", request)
        digest = hashlib.sha1(json.dumps(request, sort_keys=True).encode()).hexdigest()[:6]
        job_id = f"job-{int(time.time() * 1000) % 10**8:08d}-{digest}"
        plan = plan_request(request, self.catalog)
        record = {
            "job_id": job_id,
            "request": request,
            "submitted_at": time.time(),
            "plan": plan,
            "node_jobs": node_jobs(job_id, request, plan),
        }
        self._path(job_id).write_text(json.dumps(record, indent=2))
        self.log(f"[mock] server: {plan['episodes_matched']} episodes match, "
                 f"in {plan['shards_touched']} shard(s) on {len(plan['nodes'])} node(s)")
        for nj in record["node_jobs"]:
            node = nj["params"]["run_on"]
            part = plan["nodes"][node]
            self.log(f"[mock]   queue -> {nj['job_id']}: {len(part['episodes'])} episodes "
                     f"from {len(part['shards'])} shard(s), runs on {node}")
        est = plan["estimate"]
        self.log(f"[mock] estimate: ~{_mb(est['bytes_out_in_place'])} returned in place vs "
                 f"~{_mb(est['bytes_baseline_shards'])} pulled by a separate compute node")
        return job_id

    def status(self, job_id: str) -> dict[str, Any]:
        self._http("GET", f"/jobs/{job_id}")
        rec = self._load(job_id)
        age = time.time() - rec["submitted_at"]
        if rec["plan"]["episodes_matched"] == 0:
            state = "done"
        elif age < self.delay:
            state = "queued"
        elif age < 2 * self.delay:
            state = "running"
        else:
            state = "done"
        est = rec["plan"]["estimate"]
        return {
            "job_id": job_id,
            "status": state,
            "episodes_matched": rec["plan"]["episodes_matched"],
            "shards_touched": rec["plan"]["shards_touched"],
            "nodes": sorted(rec["plan"]["nodes"]),
            "bytes_out": est["bytes_out_in_place"] if state == "done" else None,
            "bytes_baseline": est["bytes_baseline_shards"],
        }

    def download(self, job_id: str, dest: Path) -> list[Path]:
        self._http("GET", f"/jobs/{job_id}/result")
        st = self.status(job_id)
        if st["status"] != "done":
            raise TransportError(f"job {job_id} is {st['status']}, not done")
        rec = self._load(job_id)
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        manifest = {
            "job_id": job_id,
            "mock": True,
            "request": rec["request"],
            "episodes": [e for n in rec["plan"]["nodes"].values() for e in n["episodes"]],
            "ran_on": rec["plan"]["nodes"],
            "estimate": rec["plan"]["estimate"],
            "queued_node_jobs": rec["node_jobs"],
        }
        out = dest / f"{job_id}-manifest.json"
        out.write_text(json.dumps(manifest, indent=2))
        return [out]

    def options(self) -> dict[str, list[str]]:
        self._http("GET", "/options")
        return {k: list(v) for k, v in MOCK_OPTIONS.items()}
