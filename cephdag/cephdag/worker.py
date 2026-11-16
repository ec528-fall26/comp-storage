"""The single-slot worker on each node.

A task's life on a worker: read every input from RADOS, run the registered function,
write each output to the pool of its target node, then publish one completion record.
The coordinator polls /state, and once it has consumed a completed job it calls /ack;
only then does the worker forget the job. The durable job record in RADOS is never
deleted, so a retried request always gets the same answer.
"""

import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time

from .cluster import LAB_CLUSTER, Cluster
from .functions import REGISTRY, import_modules
from .graph import (
    WORKER_MEMORY,
    ObjectRef,
    estimate_output_sizes,
    memory_reservation,
    ref_from_dict,
)
from .storage import RadosStore

RUN_ID = re.compile("[0-9a-f]{32}")
TASK_ID = re.compile("t[0-9]{6}")
JOB_KEY = re.compile("[0-9a-f]{32}/(t[0-9]{6}|warm)")
REQUEST_LIMIT = 2**20


def execute_payload(
    payload,
    store,
    pools,
    event=lambda *args: None,
    memory_limit=WORKER_MEMORY,
    journal_pool=None,
):
    """Run one dispatched task and return its completion record.

    The record is published to `journal_pool`, by default the worker's own pool; a
    compute-only worker has none and passes the metadata pool.
    """
    run_id = payload["run_id"]
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run namespace")
    task = payload["task"]
    if not TASK_ID.fullmatch(task["id"]):
        raise ValueError("invalid task identifier")
    refs = [ref_from_dict(ref) for ref in payload["inputs"]]
    if not all(isinstance(ref, ObjectRef) for ref in refs):
        raise ValueError("worker requires persisted input refs")
    targets = payload["targets"]
    if any(node not in range(len(pools)) for node in targets) or len(targets) != len(
        task["output_sizes"]
    ):
        raise ValueError("invalid output targets")
    sizes = [ref.size for ref in refs]
    expected = list(estimate_output_sizes(sizes, task["params"]["ratios"], task["output_types"]))
    if expected != list(task["output_sizes"]):
        raise ValueError("output size contract does not match observed inputs")
    memory = memory_reservation(sizes, expected)
    if memory > memory_limit:
        raise ValueError("task exceeds worker memory reservation")
    function = REGISTRY.get(task["function"])
    if function is None:
        raise ValueError("function version is not deployed on worker")
    start = time.perf_counter()
    buffers = []
    stages = []
    index = 0
    for ref in refs:
        event(index, "read", ref.size)
        began = time.perf_counter()
        data = store.read(ref)
        if len(data) != ref.size or (ref.sha256 and hashlib.sha256(data).hexdigest() != ref.sha256):
            raise ValueError("input size or checksum mismatch")
        buffers.append(data)
        stages.append(
            {
                "kind": "read",
                "index": index,
                "seconds": time.perf_counter() - began,
                "bytes": len(data),
            }
        )
        index += 1
    event(index, "compute", sum(sizes))
    began = time.perf_counter()
    outputs = function.run(buffers, task["params"], task["output_sizes"])
    stages.append(
        {
            "kind": "compute",
            "index": index,
            "seconds": time.perf_counter() - began,
            "bytes": sum(sizes),
        }
    )
    index += 1
    del buffers
    if len(outputs) != len(expected) or not all(isinstance(data, bytes) for data in outputs):
        raise ValueError("function must return one bytes object per declared output")
    # Declared sizes are estimates; the actual outputs must still fit the worker's memory bound.
    if memory_reservation(sizes, [len(data) for data in outputs]) > memory_limit:
        raise ValueError("function output exceeds worker memory reservation")
    stored = []
    for output, (target, data) in enumerate(zip(targets, outputs)):
        event(index, "write", len(data))
        began = time.perf_counter()
        key = f"runs/{run_id}/{task['id']}/output-{output}"
        ref = store.write(pools[target], key, data, task["output_types"][output], task["partition"])
        stored.append(ref.to_dict())
        stages.append(
            {
                "kind": "write",
                "index": index,
                "seconds": time.perf_counter() - began,
                "bytes": len(data),
            }
        )
        index += 1
    del outputs
    event(index, "publish", 0)
    result = {
        "task": task["id"],
        "worker": payload["worker"],
        "outputs": stored,
        "stages": stages,
        "reserved_bytes": memory,
        "seconds": time.perf_counter() - start,
    }
    # The only publication point: a failed earlier write leaves no completion record.
    store.put_manifest(
        journal_pool or pools[payload["worker"]],
        f"runs/{run_id}/{task['id']}/complete.json",
        result,
    )
    result["seconds"] = time.perf_counter() - start
    return result


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def interface_counters(interface="eth0"):
    statistics = Path("/sys/class/net") / interface / "statistics"
    return {kind: int((statistics / f"{kind}_bytes").read_text()) for kind in ("tx", "rx")}


class DurableWorker:
    """One task slot, a bounded set of jobs in RAM, and an exact durable record of every job.

    Before running anything the worker writes a 'running' record to RADOS; that record
    fences a retried submit after a crash. On finishing it writes the terminal record
    with an ack token. A job leaves memory only when the coordinator acknowledges it
    with that token and the durable copy reads back identical.
    """

    max_retained = 64

    def __init__(
        self,
        node,
        store,
        pools,
        *,
        memory_limit=WORKER_MEMORY,
        counters=interface_counters,
        journal_pool=None,
    ):
        self.node = node
        self.store = store
        self.pools = tuple(pools)
        # Where job records are kept: the node's own pool unless it has none.
        self.journal_pool = journal_pool or self.pools[node]
        self.memory_limit = memory_limit
        self.counters = counters
        self.lock = threading.Lock()
        self.active = None
        self.jobs = {}

    @staticmethod
    def _key(payload, warm=False):
        run_id = payload["run_id"]
        task = "warm" if warm else payload["task"]["id"]
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run namespace")
        if task != "warm" and not TASK_ID.fullmatch(task):
            raise ValueError("invalid task identifier")
        return f"{run_id}/{task}"

    def _record_location(self, key):
        return self.journal_pool, f"runs/{key}/worker-job.json"

    def _read(self, key):
        try:
            return self.store.get_manifest(*self._record_location(key))
        except self.store.not_found:
            return None

    def _write(self, key, value):
        self.store.put_manifest(*self._record_location(key), value)

    def submit(self, payload):
        return self._start(payload)

    def warm(self, payload):
        """Read local objects once so that page caches start in the same state for every run."""
        return self._start(payload, warm=True)

    def _start(self, payload, warm=False):
        key = self._key(payload, warm)
        payload_fingerprint = fingerprint(payload)
        if not warm and payload["worker"] != self.node:
            raise ValueError("dispatch addressed to wrong worker")
        refs = [ref_from_dict(ref) for ref in payload["refs"]] if warm else []
        if warm and any(
            not isinstance(ref, ObjectRef) or ref.pool != self.pools[self.node] for ref in refs
        ):
            raise ValueError("warmup must be local to the owning OSD")
        with self.lock:
            existing = self.jobs.get(key) or self._read(key)
            if existing:
                if existing["fingerprint"] != payload_fingerprint:
                    raise ValueError("job key reused with different payload")
                if existing["status"] == "running" and key not in self.jobs:
                    raise RuntimeError("indeterminate prior execution; manual recovery required")
                return copy.deepcopy(existing)
            if self.active is not None:
                raise RuntimeError("worker slot already reserved")
            if len(self.jobs) >= self.max_retained:
                raise RuntimeError("acknowledgment required: retained job bound reached")
            now = time.perf_counter()
            job = {
                "key": key,
                "fingerprint": payload_fingerprint,
                "status": "running",
                "stage_index": 0,
                "stage": "starting",
                "started": now,
                "stage_started": now,
                "stage_events": [],
            }
            self._write(key, job)
            self.jobs[key] = job
            self.active = key
            response = copy.deepcopy(job)
        thread = threading.Thread(
            target=self._execute, args=(key, job, payload, refs, warm), daemon=True
        )
        thread.start()
        return response

    def _execute(self, key, job, payload, refs, warm):
        def event(index, kind, size):
            with self.lock:
                now = time.perf_counter()
                if job["stage_events"]:
                    job["stage_events"][-1]["end"] = now
                job["stage_events"].append(
                    {"index": index, "kind": kind, "bytes": size, "start": now, "end": None}
                )
                job.update(stage_index=index, stage=kind, stage_bytes=size, stage_started=now)

        try:
            if warm:
                start = time.perf_counter()
                size = 0
                for ref in refs:
                    size += len(self.store.read(ref))
                outcome = {
                    "status": "completed",
                    "result": {"bytes": size, "seconds": time.perf_counter() - start},
                }
            else:
                result = execute_payload(
                    payload, self.store, self.pools, event, self.memory_limit, self.journal_pool
                )
                outcome = {"status": "completed", "result": result}
        except Exception as error:
            outcome = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
        with self.lock:
            terminal = copy.deepcopy(job)
            now = time.perf_counter()
            if terminal["stage_events"]:
                terminal["stage_events"][-1]["end"] = now
            terminal.update(outcome, finished=now)
            terminal["ack_token"] = fingerprint(terminal)
            try:
                self._write(key, terminal)
            except Exception as error:
                # Durability is uncertain: keep the slot fenced and every detail for recovery.
                payload_fingerprint = job["fingerprint"]
                job.clear()
                job.update(
                    key=key,
                    fingerprint=payload_fingerprint,
                    status="publication_failed",
                    error=f"{type(error).__name__}: {error}",
                    terminal=terminal,
                )
                return
            job.clear()
            job.update(terminal)
            self.active = None

    def recover_publication(self, payload):
        """Republish the exact terminal record after a failed publication; never re-run the task."""
        key = payload["key"]
        if not JOB_KEY.fullmatch(key):
            raise ValueError("invalid key")
        with self.lock:
            job = self.jobs.get(key) or self._read(key)
            if not job:
                raise ValueError("unknown job")
            terminal = job.get("terminal") if job["status"] == "publication_failed" else job
            if terminal["status"] not in ("completed", "failed") or payload.get(
                "token"
            ) != terminal.get("ack_token"):
                raise ValueError("terminal recovery token mismatch")
            persisted = self._read(key)
            if persisted != terminal:
                if (
                    not persisted
                    or persisted.get("status") != "running"
                    or persisted.get("fingerprint") != terminal["fingerprint"]
                ):
                    raise RuntimeError("durable job conflicts with recovery")
                try:
                    self._write(key, terminal)
                except Exception:
                    # A write whose reply was lost still counts once it reads back exactly.
                    if self._read(key) != terminal:
                        raise
                if self._read(key) != terminal:
                    raise RuntimeError("terminal recovery readback differs")
            if key in self.jobs:
                self.jobs[key] = copy.deepcopy(terminal)
            if self.active == key:
                self.active = None
            return copy.deepcopy(terminal)

    def acknowledge(self, payload):
        key = payload["key"]
        if not JOB_KEY.fullmatch(key):
            raise ValueError("invalid key")
        with self.lock:
            job = self.jobs.get(key) or self._read(key)
            if not job or job["status"] not in ("completed", "failed"):
                raise ValueError("job not terminal")
            if payload.get("token") != job.get("ack_token"):
                raise ValueError("completion token mismatch")
            # Read back before forgetting, also when retrying an ACK whose reply was lost.
            if self._read(key) != job:
                raise RuntimeError("durable completion differs")
            self.jobs.pop(key, None)
        return {"key": key, "acknowledged": True}

    def snapshot(self, keys):
        if len(keys) > self.max_retained:
            raise ValueError("too many requested jobs")
        if any(not JOB_KEY.fullmatch(key) for key in keys):
            raise ValueError("invalid key")
        with self.lock:
            jobs = {}
            for key in keys:
                job = self.jobs.get(key) or self._read(key)
                if job:
                    jobs[key] = copy.deepcopy(job)
            active = self.active
            retained = len(self.jobs)
        return {
            "node": self.node,
            "active": active,
            "jobs": jobs,
            "counters": self.counters(),
            "time": time.perf_counter(),
            "retained_jobs": retained,
            "retention_limit": self.max_retained,
            "retention_protocol": 1,
        }

    def handle(self, path, payload):
        routes = {
            "/submit": self.submit,
            "/state": lambda body: self.snapshot(body.get("keys", [])),
            "/warm": self.warm,
            "/ack": self.acknowledge,
            "/recover-publication": self.recover_publication,
        }
        if path not in routes:
            raise ValueError("unknown endpoint")
        return routes[path](payload)


def serve(worker, host, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > REQUEST_LIMIT:
                    raise ValueError("request exceeds 1 MiB")
                body = json.dumps(
                    worker.handle(self.path, json.loads(self.rfile.read(length)))
                ).encode()
                self.send_response(200)
            except Exception as error:
                body = json.dumps({"error": f"{type(error).__name__}: {error}"}).encode()
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Run the worker of one cluster node.")
    parser.add_argument("--node", type=int, required=True)
    parser.add_argument("--cluster", default=str(LAB_CLUSTER))
    parser.add_argument(
        "--functions", nargs="*", default=[], help="extra modules that register functions"
    )
    parser.add_argument(
        "--interface", default="eth0", help="network interface whose byte counters are reported"
    )
    args = parser.parse_args()
    import_modules(args.functions)
    cluster = Cluster.load(args.cluster)
    worker = DurableWorker(
        args.node,
        RadosStore(cluster),
        cluster.pools,
        counters=lambda: interface_counters(args.interface),
        journal_pool=cluster.journal_pool(args.node),
    )
    serve(worker, *cluster.nodes[args.node].address)


if __name__ == "__main__":
    main()
