"""Which nodes exist: each owns one RADOS pool, runs one single-slot worker, or both."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

LAB_CLUSTER = Path(__file__).with_name("lab-cluster.json")


@dataclass(frozen=True)
class Node:
    """A network host. `pool` is None on a compute-only node, `worker` on a storage-only one."""

    pool: Optional[str]
    worker: Optional[str]

    @property
    def address(self):
        """(host, port) the worker listens on."""
        url = urlsplit(self.worker)
        return url.hostname, url.port


@dataclass(frozen=True)
class Cluster:
    """Node n is entry n of `nodes`; the coordinator stores run metadata in `metadata_pool`."""

    nodes: tuple
    metadata_pool: str
    ceph_conf: str = "/etc/ceph/ceph.conf"
    namespace: str = "cephdag"

    def __post_init__(self):
        if not self.nodes:
            raise ValueError("a cluster needs at least one node")
        if any(node.pool is None and node.worker is None for node in self.nodes):
            raise ValueError("every node needs a pool, a worker or both")
        pools = [self.nodes[node].pool for node in self.storage_nodes]
        if len(set(pools)) != len(pools):
            raise ValueError("every node needs its own pool")
        if not self.worker_nodes:
            raise ValueError("a cluster needs at least one worker")
        if self.metadata_pool not in pools:
            raise ValueError("the metadata pool must belong to a node")

    @property
    def pools(self):
        """The pool of each node, None where the node stores nothing."""
        return tuple(node.pool for node in self.nodes)

    @property
    def storage_nodes(self):
        return tuple(index for index, node in enumerate(self.nodes) if node.pool is not None)

    @property
    def worker_nodes(self):
        return tuple(index for index, node in enumerate(self.nodes) if node.worker is not None)

    @property
    def interface_nodes(self):
        """The first worker node of each host: the workers on a host share its interface."""
        first = {}
        for node in self.worker_nodes:
            first.setdefault(self.nodes[node].address[0], node)
        return tuple(first.values())

    @property
    def hosts(self):
        """The node whose host each node runs on: the node itself, except that a worker without
        a pool runs on the host of the first worker at its address. Lab modes cs2 and disagg2
        list a container's second worker as such a node; it shares the host's interface and
        reads and writes the host's pool, if it has one, locally."""
        first = {}
        hosts = []
        for index, node in enumerate(self.nodes):
            if node.worker is None:
                hosts.append(index)
                continue
            host = first.setdefault(node.address[0], index)
            hosts.append(host if node.pool is None else index)
        return tuple(hosts)

    def journal_pool(self, node):
        """Where `node`'s worker keeps its job records: its host's pool, or the metadata pool
        if its host stores nothing."""
        return self.nodes[self.hosts[node]].pool or self.metadata_pool

    def node_of(self, pool):
        return self.pools.index(pool)

    @classmethod
    def from_dict(cls, data):
        nodes = tuple(Node(node["pool"], node["worker"]) for node in data["nodes"])
        first_pool = next((node.pool for node in nodes if node.pool is not None), None)
        return cls(
            nodes,
            data.get("metadata_pool", first_pool),
            data.get("ceph_conf", "/etc/ceph/ceph.conf"),
            data.get("namespace", "cephdag"),
        )

    @classmethod
    def load(cls, path=LAB_CLUSTER):
        return cls.from_dict(json.loads(Path(path).read_text()))
