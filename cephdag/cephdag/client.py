"""The user-facing interface: store datasets, submit graphs, follow jobs, read results."""

import json
from pathlib import Path
import threading
import uuid

from . import coordinator
from .cluster import Cluster
from .graph import Dataset, Graph, ref_from_dict
from .model import Models


class Job:
    """A graph running in a background thread on the coordinator."""

    def __init__(self, run_id, execute):
        self.run_id = run_id
        self._status = "running"
        self._result = None
        self._error = None
        self._done = threading.Event()

        def target():
            try:
                self._result = execute()
                self._status = "completed"
            except Exception as error:
                self._error = f"{type(error).__name__}: {error}"
                self._status = "failed"
            finally:
                self._done.set()

        threading.Thread(target=target, daemon=True).start()

    def status(self):
        return self._status

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise TimeoutError("job is still running")
        if self._error:
            raise RuntimeError(self._error)
        return self

    def outputs(self):
        """References to the outputs no task consumed, i.e. the results of the graph."""
        return tuple(ref_from_dict(ref) for ref in self.metrics()["final_outputs"])

    def metrics(self):
        if self._status != "completed":
            raise RuntimeError(f"job is {self._status}")
        return self._result


class Client:
    """Talk to one cluster. `models` is Models, a models.json path, or None for defaults."""

    def __init__(self, cluster, models=None, *, store=None, transport=None):
        self.cluster = cluster if isinstance(cluster, Cluster) else Cluster.load(cluster)
        if models is None:
            models = Models.uncalibrated(len(self.cluster.nodes))
        elif not isinstance(models, Models):
            models = Models.from_dict(json.loads(Path(models).read_text()))
        if models.node_count != len(self.cluster.nodes):
            raise ValueError("models and cluster disagree on the number of nodes")
        self.models = models
        if store is None:
            from .storage import RadosStore

            store = RadosStore(self.cluster)
        self.store = store
        self.transport = transport or coordinator.HttpTransport(self.cluster)

    def _manifest_key(self, name):
        if not name or "/" in name or name.startswith("."):
            raise ValueError("invalid dataset name")
        return f"datasets/{name}/manifest.json"

    def put_dataset(self, name, parts, *, placement=None, dtype="float32"):
        """Store each part (bytes) as one object on node placement[i], by default round-robin
        over the nodes that have a pool."""
        key = self._manifest_key(name)
        try:
            self.store.get_manifest(self.cluster.metadata_pool, key)
        except self.store.not_found:
            pass
        else:
            raise ValueError(f"dataset {name} already exists")
        shards = []
        for index, data in enumerate(parts):
            pools = self.cluster.storage_nodes
            node = placement[index] if placement is not None else pools[index % len(pools)]
            pool = self.cluster.nodes[node].pool
            shards.append(
                self.store.write(pool, f"datasets/{name}/part-{index:06d}", data, dtype, index)
            )
        dataset = Dataset(name, tuple(shards))
        self.store.put_manifest(self.cluster.metadata_pool, key, dataset.to_dict())
        return dataset

    def dataset(self, name):
        return Dataset.from_dict(
            self.store.get_manifest(self.cluster.metadata_pool, self._manifest_key(name))
        )

    def delete_dataset(self, name, extra_manifests=()):
        """Remove dataset `name`: its shards, the manifests `extra_manifests` (file names
        under datasets/<name>/ in the metadata pool), and its manifest last, so that an
        interrupted delete can be repeated. Return the number of shards removed."""
        dataset = self.dataset(name)
        removed = 0
        for shard in dataset.shards:
            removed += self.store.remove_dataset_object(shard.pool, shard.key, name)
        metadata = self.cluster.metadata_pool
        for manifest in extra_manifests:
            self.store.remove_dataset_object(metadata, f"datasets/{name}/{manifest}", name)
        self.store.remove_dataset_object(metadata, self._manifest_key(name), name)
        return removed

    def submit(self, graph, scheduler="greedy", *, timeout=7200, **options):
        """Start running `graph` under a policy name (see policies.NAMES) or a policy object."""
        graph.freeze()
        # A serialized copy, so that later changes to `graph` cannot change submitted work.
        frozen = Graph.from_dict(graph.to_dict())
        run_id = uuid.uuid4().hex

        def execute():
            return coordinator.run(
                frozen,
                self.models,
                scheduler,
                cluster=self.cluster,
                store=self.store,
                transport=self.transport,
                run_id=run_id,
                timeout=timeout,
                scheduler_options=options,
            )

        return Job(run_id, execute)

    def read(self, ref):
        return self.store.read(ref)
