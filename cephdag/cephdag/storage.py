"""Object storage: Ceph RADOS in the lab, and an in-memory stand-in for tests and examples."""

import copy
import hashlib
import json
import re

from .graph import ObjectRef

MANIFEST_LIMIT = 64 * 2**20


def stored_ref(pool, key, data, dtype, partition):
    return ObjectRef(pool, key, len(data), dtype, partition, hashlib.sha256(data).hexdigest())


def check_read(ref, data):
    if len(data) != ref.size:
        raise ValueError("persisted object size differs from its reference")
    if ref.sha256 and hashlib.sha256(data).hexdigest() != ref.sha256:
        raise ValueError("persisted object checksum mismatch")
    return data


def check_generated(ref, run_id):
    if not re.fullmatch("[0-9a-f]{32}", run_id) or not ref.key.startswith(f"runs/{run_id}/"):
        raise ValueError("cleanup is limited to the exact generated run namespace")


def check_dataset_key(key, name):
    if not name or "/" in name or name.startswith(".") or not key.startswith(f"datasets/{name}/"):
        raise ValueError("dataset removal is limited to the exact dataset namespace")


class RadosStore:
    """Every object lives in namespace `cluster.namespace` of one of the cluster's pools."""

    backend = "ceph-rados"

    def __init__(self, cluster):
        try:
            import rados
        except ImportError as error:
            raise RuntimeError(
                "Ceph python3-rados is required; there is no local-storage fallback"
            ) from error
        self.pools = set(cluster.pools) - {None}
        self.namespace = cluster.namespace
        self.not_found = rados.ObjectNotFound
        self.cluster = rados.Rados(conffile=cluster.ceph_conf)
        self.cluster.conf_set("rados_osd_op_timeout", "120")
        self.cluster.conf_set("rados_mon_op_timeout", "30")
        self.cluster.connect()

    def context(self, pool):
        if pool not in self.pools:
            raise ValueError(f"pool {pool} is not part of the cluster")
        io = self.cluster.open_ioctx(pool)
        io.set_namespace(self.namespace)
        return io

    def read(self, ref):
        with self.context(ref.pool) as io:
            size, _ = io.stat(ref.key)
            if size != ref.size:
                raise ValueError("persisted object size differs from its reference")
            data = io.read(ref.key, ref.size)
        return check_read(ref, data)

    def write(self, pool, key, data, dtype="float32", partition=0):
        ref = stored_ref(pool, key, data, dtype, partition)
        with self.context(pool) as io:
            io.write_full(key, data)
            if io.stat(key)[0] != len(data):
                raise OSError("write size verification failed")
        return ref

    def put_manifest(self, pool, key, value):
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        with self.context(pool) as io:
            io.write_full(key, body)

    def get_manifest(self, pool, key):
        with self.context(pool) as io:
            size, _ = io.stat(key)
            if size > MANIFEST_LIMIT:
                raise ValueError("manifest exceeds 64 MiB bound")
            return json.loads(io.read(key, size))

    def remove_generated(self, ref, run_id):
        check_generated(ref, run_id)
        with self.context(ref.pool) as io:
            io.remove_object(ref.key)

    def remove_dataset_object(self, pool, key, name):
        """Remove object `key` of dataset `name`; False if it did not exist."""
        check_dataset_key(key, name)
        with self.context(pool) as io:
            try:
                io.remove_object(key)
            except self.not_found:
                return False
        return True

    def close(self):
        self.cluster.shutdown()


class MemoryStore:
    """Same interface as RadosStore, kept in a dict. Manifests round-trip through JSON.

    Given the cluster's pools, it refuses any other pool as RadosStore does.
    """

    backend = "memory"
    not_found = KeyError

    def __init__(self, pools=None):
        self.pools = None if pools is None else set(pools) - {None}
        self.objects = {}
        self.manifests = {}

    def _check(self, pool):
        if self.pools is not None and pool not in self.pools:
            raise ValueError(f"pool {pool} is not part of the cluster")

    def read(self, ref):
        self._check(ref.pool)
        return check_read(ref, self.objects[ref.pool, ref.key])

    def write(self, pool, key, data, dtype="float32", partition=0):
        self._check(pool)
        self.objects[pool, key] = bytes(data)
        return stored_ref(pool, key, bytes(data), dtype, partition)

    def put_manifest(self, pool, key, value):
        self._check(pool)
        self.manifests[pool, key] = json.loads(json.dumps(value))

    def get_manifest(self, pool, key):
        self._check(pool)
        return copy.deepcopy(self.manifests[pool, key])

    def remove_generated(self, ref, run_id):
        self._check(ref.pool)
        check_generated(ref, run_id)
        del self.objects[ref.pool, ref.key]

    def remove_dataset_object(self, pool, key, name):
        """Remove object `key` of dataset `name`; False if it did not exist."""
        self._check(pool)
        check_dataset_key(key, name)
        found = (pool, key) in self.objects or (pool, key) in self.manifests
        self.objects.pop((pool, key), None)
        self.manifests.pop((pool, key), None)
        return found

    def close(self):
        pass
