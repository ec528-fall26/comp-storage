"""Shared test fixtures: the lab's pools, in-process workers and a recording transport."""

import hashlib
import json
import threading
import time

from cephdag.cluster import Cluster, Node
from cephdag.storage import MemoryStore
from cephdag.worker import DurableWorker

LAB_POOLS = ("node0", "node1", "node2")


def rng_digest(generator):
    return hashlib.sha256(repr(generator.getstate()).encode()).hexdigest()


def lab_cluster(node_count=3):
    nodes = tuple(
        Node(f"node{node}", f"http://127.0.0.1:{8080 + node}") for node in range(node_count)
    )
    return Cluster(nodes, "node0")


def zero_counters():
    return {"tx": 0, "rx": 0}


class LocalTransport:
    """Delivers requests to in-process workers through a JSON round trip and records each call."""

    def __init__(self, workers):
        self.workers = workers
        self.calls = []
        self.lock = threading.Lock()

    def post(self, node, path, payload):
        body = json.dumps(payload)
        with self.lock:
            self.calls.append({"node": node, "path": path, "body": body})
        return json.loads(json.dumps(self.workers[node].handle("/" + path, json.loads(body))))


class FaultStore(MemoryStore):
    """A MemoryStore that counts payload writes and can fail the n-th one."""

    def __init__(self, fail_write=None):
        super().__init__()
        self.writes = 0
        self.fail_write = fail_write

    def write(self, pool, key, data, dtype="float32", partition=0):
        self.writes += 1
        if self.writes == self.fail_write:
            raise OSError("injected storage failure")
        return super().write(pool, key, data, dtype, partition)


def terminal_job(worker, key, timeout=5):
    deadline = time.monotonic() + timeout
    while True:
        job = worker.snapshot([key])["jobs"][key]
        if job["status"] != "running":
            return job
        if time.monotonic() > deadline:
            raise TimeoutError("job did not finish")
        time.sleep(0.002)


def local_cluster(node_count=3, store=None):
    """(cluster, store, workers, transport) for a cluster running entirely in this process."""
    cluster = lab_cluster(node_count)
    store = store or MemoryStore(cluster.pools)
    workers = [
        DurableWorker(node, store, cluster.pools, counters=zero_counters)
        for node in range(node_count)
    ]
    return cluster, store, workers, LocalTransport(workers)


def cs_nodes(storage, slots=1):
    """Cluster-file nodes of a computational-storage lab: each node stores and computes, each on
    its own host; with `slots`, each host's further workers follow as nodes without a pool, as
    in lab mode cs2."""
    return [
        {"pool": f"node{i}", "worker": f"http://127.0.2.{1 + i}:8080"} for i in range(storage)
    ] + [
        {"pool": None, "worker": f"http://127.0.2.{1 + i}:{8080 + slot}"}
        for slot in range(1, slots)
        for i in range(storage)
    ]


def disagg_nodes(storage, compute, slots=1):
    """Cluster-file nodes of the same lab in disaggregated mode: storage-only nodes, then
    compute-only nodes, each on its own host; with `slots`, each host's further workers
    follow as more compute-only nodes, as in lab mode disagg2."""
    return [{"pool": f"node{i}", "worker": None} for i in range(storage)] + [
        {"pool": None, "worker": f"http://127.0.1.{1 + j}:{8080 + slot}"}
        for slot in range(slots)
        for j in range(compute)
    ]


def role_lab(nodes, store=None):
    """(cluster, store, transport) running in this process for cluster-file `nodes`. Workers
    journal in their host's pool, or in the metadata pool if it has none, as lab workers do."""
    cluster = Cluster.from_dict({"nodes": nodes})
    store = store or MemoryStore(cluster.pools)
    workers = {
        node: DurableWorker(
            node,
            store,
            cluster.pools,
            counters=zero_counters,
            journal_pool=cluster.journal_pool(node),
        )
        for node in cluster.worker_nodes
    }
    return cluster, store, LocalTransport(workers)


def wait_until(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not reached")
        time.sleep(0.002)
