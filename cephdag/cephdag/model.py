"""Calibrated cost models and the event simulation every policy uses as its forecast."""

from collections import Counter
from collections.abc import Mapping
import copy
from dataclasses import asdict, dataclass
import hashlib
import itertools
import math

from .graph import WORKER_MEMORY, ObjectRef


@dataclass(frozen=True)
class FunctionCost:
    """Compute seconds of one function: fixed, plus per input byte, plus per output byte."""

    fixed_seconds: float
    input_seconds_per_byte: float
    output_seconds_per_byte: float

    def seconds(self, task, input_sizes):
        return max(
            0.0,
            self.fixed_seconds
            + sum(input_sizes) * self.input_seconds_per_byte
            + sum(task.output_sizes) * self.output_seconds_per_byte,
        )


@dataclass(frozen=True)
class ComputeModel:
    """Compute seconds of a task.

    `functions` holds sorted (function key, FunctionCost) pairs. Every other function,
    the numeric builtins included, uses the coefficients below; only those have the
    iterations term.
    """

    fixed_seconds: float = 0.001
    input_seconds_per_byte: float = 1e-9
    iteration_seconds_per_byte: float = 4e-10
    output_seconds_per_byte: float = 1e-9
    functions: tuple = ()

    def cost(self, function):
        """The FunctionCost registered for `function`, or None if it uses the coefficients here."""
        for name, cost in self.functions:
            if name == function:
                return cost
        return None

    def coefficients(self, function):
        """(fixed, per input byte, per largest-input byte and iteration, per output byte)."""
        cost = self.cost(function)
        if cost is None:
            return (
                self.fixed_seconds,
                self.input_seconds_per_byte,
                self.iteration_seconds_per_byte,
                self.output_seconds_per_byte,
            )
        return (cost.fixed_seconds, cost.input_seconds_per_byte, 0.0, cost.output_seconds_per_byte)

    def seconds(self, task, input_sizes):
        cost = self.cost(task.function)
        if cost is not None:
            return cost.seconds(task, input_sizes)
        return max(
            0.0,
            self.fixed_seconds
            + sum(input_sizes) * self.input_seconds_per_byte
            + max(input_sizes, default=0)
            * task.params["iterations"]
            * self.iteration_seconds_per_byte
            + sum(task.output_sizes) * self.output_seconds_per_byte,
        )


@dataclass(frozen=True)
class StorageModel:
    read_bps: tuple = (300e6, 300e6, 300e6)
    write_bps: tuple = (150e6, 150e6, 150e6)


@dataclass(frozen=True)
class NetworkModel:
    tx_bps: tuple = (12.5e6, 12.5e6, 12.5e6)
    rx_bps: tuple = (12.5e6, 12.5e6, 12.5e6)


@dataclass(frozen=True)
class Models:
    """Per-node rates; node n is entry n of every rate tuple."""

    compute: ComputeModel = ComputeModel()
    storage: StorageModel = StorageModel()
    network: NetworkModel = NetworkModel()
    calibrated: bool = False
    calibration_id: str = "uncalibrated-defaults"

    @classmethod
    def uncalibrated(cls, node_count):
        def repeat(rates):
            return (rates[0],) * node_count

        defaults = cls()
        return cls(
            storage=StorageModel(
                repeat(defaults.storage.read_bps), repeat(defaults.storage.write_bps)
            ),
            network=NetworkModel(repeat(defaults.network.tx_bps), repeat(defaults.network.rx_bps)),
        )

    @property
    def node_count(self):
        return len(self.storage.read_bps)

    def to_dict(self):
        """The models.json form; without per-function costs it is the original form."""
        value = asdict(self)
        del value["compute"]["functions"]
        if self.compute.functions:
            value["compute"]["functions"] = {
                function: asdict(cost) for function, cost in self.compute.functions
            }
        return value

    @classmethod
    def from_dict(cls, value):
        compute = dict(value["compute"])
        functions = compute.pop("functions", {})
        compute["functions"] = tuple(
            sorted((function, FunctionCost(**cost)) for function, cost in functions.items())
        )
        storage = value["storage"]
        network = value["network"]
        return cls(
            ComputeModel(**compute),
            StorageModel(tuple(storage["read_bps"]), tuple(storage["write_bps"])),
            NetworkModel(tuple(network["tx_bps"]), tuple(network["rx_bps"])),
            value["calibrated"],
            value["calibration_id"],
        )

    def capacities(self):
        """Rate of every shared resource, named like 'tx:2'; a CPU runs one task."""
        rates = {
            "read": self.storage.read_bps,
            "write": self.storage.write_bps,
            "tx": self.network.tx_bps,
            "rx": self.network.rx_bps,
        }
        if any(len(values) != self.node_count for values in rates.values()):
            raise ValueError("every rate tuple needs one entry per node")
        capacity = {}
        for node in range(self.node_count):
            for kind in ("read", "write", "tx", "rx"):
                rate = rates[kind][node]
                if not math.isfinite(rate) or rate <= 0:
                    raise ValueError("all resource rates must be finite and positive")
                capacity[f"{kind}:{node}"] = rate
            capacity[f"cpu:{node}"] = 1.0
        return capacity


@dataclass(frozen=True)
class Objective:
    """Time first; network bytes break ties unless given a weight."""

    seconds_weight: float = 1.0
    network_byte_weight: float = 0.0

    def key(self, seconds, network_bytes):
        return (
            self.seconds_weight * seconds + self.network_byte_weight * network_bytes,
            network_bytes,
        )


def stable_hash(name):
    """The same 64-bit number for `name` in every process, unlike hash()."""
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big")


def hashed_targets(task, storage_nodes):
    """The node of each output of `task`, by a stable hash of its id and output index, as
    CRUSH places objects."""
    return tuple(
        storage_nodes[stable_hash(f"{task.id}/{index}") % len(storage_nodes)]
        for index in range(len(task.output_sizes))
    )


@dataclass(frozen=True)
class Dispatch:
    """Run `task` on `worker` and store output i in the pool of node `targets[i]`."""

    task: str
    worker: int
    targets: tuple


@dataclass
class Stage:
    kind: str
    remaining: float
    resources: tuple


@dataclass
class InFlight:
    action: Dispatch
    stages: list
    stage_index: int = 0


class Outputs(Mapping):
    """The stored outputs of each completed task, by task id; cheap to copy.

    Copies share `base`, which is never modified. Each copy keeps its own completions in
    `recent` and folds them into a new base once there are more than FOLD_AT of them.
    """

    FOLD_AT = 128

    def __init__(self, completed=()):
        self.base = dict(completed)
        self.recent = {}

    def __getitem__(self, task_id):
        if task_id in self.recent:
            return self.recent[task_id]
        return self.base[task_id]

    def __contains__(self, task_id):
        return task_id in self.recent or task_id in self.base

    def __iter__(self):
        return itertools.chain(self.base, self.recent)

    def __len__(self):
        return len(self.base) + len(self.recent)

    def items(self):
        return itertools.chain(self.base.items(), self.recent.items())

    def __setitem__(self, task_id, outputs):
        if task_id in self.base:
            self.base = {**self.base, task_id: outputs}
        else:
            self.recent[task_id] = outputs

    def copy(self):
        if len(self.recent) > self.FOLD_AT:
            self.base = {**self.base, **self.recent}
            self.recent = {}
        other = Outputs()
        other.base = self.base
        other.recent = dict(self.recent)
        return other


class Simulation:
    """Forecast of the cluster: each task reads its inputs, computes, then writes each output.

    Concurrent stages share every sender NIC, receiver NIC and storage device they use,
    each getting an equal share of its tightest resource. Completed outputs keep the size
    actually stored; outputs not yet produced use the graph's estimate.

    Ready and not-yet-started tasks are flags in two bytearrays, and completed outputs are
    an `Outputs` mapping, so a clone copies nothing as large as the graph except the flags.

    `pools[n]` is node n's pool, or None if it stores nothing; `workers` are the nodes that
    run tasks, every node by default. Tasks run only on workers and outputs go only to pools.
    `hosts[n]` is the node whose host n runs on (Cluster.hosts), n itself by default: a worker
    reads and writes its host's pool locally and sends and receives on its host's links.
    On a role-split cluster, where some host lacks a worker or a pool, the storage system
    places data as in a conventional cluster: each output goes where hashed_targets puts it,
    and a policy chooses only the task and the worker.
    """

    def __init__(
        self, graph, models, pools, *, workers=None, hosts=None, memory_limit=WORKER_MEMORY
    ):
        self.graph = graph
        self.models = models
        self.pools = tuple(pools)
        self.node_count = models.node_count
        if len(self.pools) != self.node_count:
            raise ValueError(
                f"models describe {self.node_count} nodes but there are {len(self.pools)} pools"
            )
        self.node_index = {pool: node for node, pool in enumerate(self.pools) if pool is not None}
        self.storage_nodes = tuple(self.node_index.values())
        self.workers = tuple(range(self.node_count)) if workers is None else tuple(workers)
        if (
            not self.storage_nodes
            or not self.workers
            or any(node not in range(self.node_count) for node in self.workers)
        ):
            raise ValueError("a simulation needs at least one pool and one worker among its nodes")
        self.hosts = tuple(range(self.node_count)) if hosts is None else tuple(hosts)
        worker_hosts = {self.hosts[worker] for worker in self.workers}
        self.role_split = any(
            self.pools[host] is None or host not in worker_hosts for host in set(self.hosts)
        )
        self.capacity = models.capacities()
        self.memory_limit = memory_limit
        self.time = 0.0
        self.remote_bytes = 0
        self.running = {}
        self.assigned_work = [0.0] * self.node_count
        self.dependencies, self.successors = graph.index()
        self.order, self.position = graph.positions()
        self.work, self._work_values, self._unassigned_work = self._work_estimates()
        self._dispatched = []
        self._completed = Outputs()
        # Completed tasks that unfinished tasks still read, with how many such readers.
        self._readers = {}
        # Whether a completed output's stored size differs from the graph's estimate.
        self.resized = False
        self._ready = bytearray(not self.dependencies[task_id] for task_id in self.order)
        # In graph.tasks order, the order unassigned work is summed in.
        self._work_index = graph.insertion_index()
        self._unstarted = bytearray(b"\x01") * len(self.order)

    def _work_estimates(self):
        """(seconds of each task, the same in graph.tasks order, their sum), cached per model."""
        storage = self.models.storage
        read_bps = max(storage.read_bps[node] for node in self.storage_nodes)
        write_bps = max(storage.write_bps[node] for node in self.storage_nodes)
        cache_key = (self.models.compute, read_bps, write_bps)
        if cache_key not in self.graph._work_cache:
            work = {}
            for task_id, task in self.graph.tasks.items():
                sizes = [self.graph.size(ref) for ref in task.inputs]
                work[task_id] = (
                    self.models.compute.seconds(task, sizes)
                    + sum(sizes) / read_bps
                    + sum(task.output_sizes) / write_bps
                )
            values = list(work.values())
            self.graph._work_cache[cache_key] = (work, values, sum(values))
        return self.graph._work_cache[cache_key]

    @property
    def completed(self):
        return self._completed

    @completed.setter
    def completed(self, completed):
        self._completed = Outputs(completed)
        self._count_readers()

    def _count_readers(self):
        completed = set(self.completed)
        self._readers = {}
        for task_id in self.completed:
            readers = sum(task not in completed for task in self.successors[task_id])
            if readers:
                self._readers[task_id] = readers
        self.resized = any(
            ref.size != size
            for task_id, outputs in self.completed.items()
            for ref, size in zip(outputs, self.graph.tasks[task_id].output_sizes)
        )

    def still_read(self):
        """The completed tasks whose outputs some unfinished task still reads."""
        return list(self._readers)

    @property
    def ready_order(self):
        """Every ready task id in sorted order, as a new list; assign to replace the ready set."""
        return self.ready()

    @ready_order.setter
    def ready_order(self, task_ids):
        self._ready = bytearray(len(self.order))
        for task_id in task_ids:
            self._ready[self.position[task_id]] = 1

    @property
    def ready_set(self):
        return set(self.ready())

    @ready_set.setter
    def ready_set(self, task_ids):
        self.ready_order = task_ids

    @property
    def unassigned_work(self):
        """Work of the tasks not started at the last refresh, less the tasks dispatched since."""
        if self._unassigned_work is None:
            unstarted_at_refresh = bytearray(self._unstarted)
            for task_id in self._dispatched:
                unstarted_at_refresh[self._work_index[task_id]] = 1
            work = sum(itertools.compress(self._work_values, unstarted_at_refresh))
            for task_id in self._dispatched:
                work -= self.work[task_id]
            self.unassigned_work = work
        return self._unassigned_work

    @unassigned_work.setter
    def unassigned_work(self, work):
        self._unassigned_work = work
        self._dispatched = []

    def refresh(self):
        """Recompute the ready tasks and unassigned work after editing completed/running."""
        self.ready_order = self.graph.ready(self.completed, self.running)
        self._count_readers()
        started = set(self.completed).union(self.running)
        self._unstarted = bytearray(task_id not in started for task_id in self.graph.tasks)
        # Summed when first needed: most policies never read it.
        self._unassigned_work = None
        self._dispatched = []

    def clone(self):
        other = copy.copy(self)
        other._completed = self._completed.copy()
        other.running = {
            task_id: InFlight(
                flight.action, [copy.copy(stage) for stage in flight.stages], flight.stage_index
            )
            for task_id, flight in self.running.items()
        }
        other.assigned_work = list(self.assigned_work)
        other._readers = dict(self._readers)
        other._ready = bytearray(self._ready)
        other._unstarted = bytearray(self._unstarted)
        other._dispatched = list(self._dispatched)
        return other

    @property
    def done(self):
        return len(self.completed) == len(self.graph.tasks)

    def ready(self, limit=None):
        """The first `limit` ready task ids in sorted order, or all of them."""
        if limit is None:
            return list(itertools.compress(self.order, self._ready))
        found = []
        position = self._ready.find(1)
        while position >= 0 and len(found) < limit:
            found.append(self.order[position])
            position = self._ready.find(1, position + 1)
        return found

    def is_ready(self, task_id):
        position = self.position.get(task_id)
        return position is not None and self._ready[position] == 1

    def free_workers(self):
        busy = {flight.action.worker for flight in self.running.values()}
        return [node for node in self.workers if node not in busy]

    def resolve(self, ref):
        """The stored object behind `ref`; task outputs must already be completed."""
        if isinstance(ref, ObjectRef):
            return ref
        return self.completed[ref.task][ref.output]

    def location(self, ref):
        return self.node_index[self.resolve(ref).pool]

    def candidates(self, *, task_limit=24, all_targets=True):
        """Feasible dispatches of the first `task_limit` ready tasks onto free workers.

        Without `all_targets`, every output stays on the host of the worker that computes it,
        or goes to any pool if that host has none. On a role-split cluster the outputs go only
        where hashed_targets puts them.
        """
        for task_id in self.ready(task_limit):
            for worker in self.free_workers():
                task = self.graph.tasks[task_id]
                host = self.hosts[worker]
                if all_targets or self.pools[host] is None or self.role_split:
                    placements = self.target_options(task_id)
                else:
                    placements = [(host,) * len(task.output_sizes)]
                for targets in placements:
                    if task.memory <= self.memory_limit:
                        yield Dispatch(task_id, worker, tuple(targets))

    def target_options(self, task_id):
        """Every placement of the outputs of `task_id` a policy may choose: any pool for each
        output, or on a role-split cluster only the hashed one."""
        task = self.graph.tasks[task_id]
        if self.role_split:
            return [hashed_targets(task, self.storage_nodes)]
        return itertools.product(self.storage_nodes, repeat=len(task.output_sizes))

    def stages_for(self, action):
        task = self.graph.tasks[action.task]
        inputs = [self.resolve(ref) for ref in task.inputs]
        host = self.hosts[action.worker]
        stages = []
        for stored in inputs:
            node = self.node_index[stored.pool]
            resources = [f"read:{node}"]
            if node != host:
                resources += [f"tx:{node}", f"rx:{host}"]
            stages.append(Stage("read", stored.size, tuple(resources)))
        compute = self.models.compute.seconds(task, [stored.size for stored in inputs])
        stages.append(Stage("compute", compute, (f"cpu:{action.worker}",)))
        for target, size in zip(action.targets, task.output_sizes):
            resources = [f"write:{target}"]
            if target != host:
                resources += [f"tx:{host}", f"rx:{target}"]
            stages.append(Stage("write", size, tuple(resources)))
        return stages

    def transfer_bytes(self, action):
        task = self.graph.tasks[action.task]
        host = self.hosts[action.worker]
        read = sum(self.resolve(ref).size for ref in task.inputs if self.location(ref) != host)
        write = sum(size for node, size in zip(action.targets, task.output_sizes) if node != host)
        return read + write

    def isolated_seconds(self, action):
        """Duration of `action` if it ran alone on the cluster."""
        return sum(
            stage.remaining / min(self.capacity[resource] for resource in stage.resources)
            for stage in self.stages_for(action)
        )

    def dispatch(self, action):
        if (
            not self.is_ready(action.task)
            or action.worker not in self.free_workers()
            or len(action.targets) != len(self.graph.tasks[action.task].output_sizes)
            or any(node not in self.storage_nodes for node in action.targets)
        ):
            raise ValueError("infeasible dispatch")
        task = self.graph.tasks[action.task]
        if task.memory > self.memory_limit:
            raise ValueError("task exceeds available worker memory")
        self.running[action.task] = InFlight(action, self.stages_for(action))
        self._ready[self.position[action.task]] = 0
        self._unstarted[self._work_index[action.task]] = 0
        if self._unassigned_work is None:
            self._dispatched.append(action.task)
        else:
            self._unassigned_work -= self.work[action.task]
        self.remote_bytes += self.transfer_bytes(action)
        self.assigned_work[action.worker] += self.isolated_seconds(action)
        self._complete_finished()

    def observe_dispatch(self, task_id):
        """Take a task submitted to a real worker out of the ready set (the coordinator's view)."""
        self._ready[self.position[task_id]] = 0
        self._unstarted[self._work_index[task_id]] = 0
        self._unassigned_work = None
        self._dispatched = []

    def observe_completion(self, task_id, outputs):
        """Record the stored outputs of a submitted task that finished on its worker."""
        self.completed[task_id] = outputs
        estimates = self.graph.tasks[task_id].output_sizes
        self.resized = self.resized or any(
            ref.size != size for ref, size in zip(outputs, estimates)
        )
        self._release_successors(task_id)

    def _complete_finished(self):
        finished = []
        for task_id, flight in self.running.items():
            while flight.stages and flight.stages[0].remaining <= 1e-9:
                flight.stages.pop(0)
                flight.stage_index += 1
            if not flight.stages:
                task = self.graph.tasks[task_id]
                self.completed[task_id] = tuple(
                    ObjectRef(
                        self.pools[node],
                        f"forecast/{task_id}/{index}",
                        size,
                        task.output_types[index],
                        task.partition,
                    )
                    for index, (node, size) in enumerate(
                        zip(flight.action.targets, task.output_sizes)
                    )
                )
                finished.append(task_id)
        for task_id in finished:
            del self.running[task_id]
            self._release_successors(task_id)

    def _release_successors(self, task_id):
        if self.successors[task_id]:
            self._readers[task_id] = len(self.successors[task_id])
        for parent in self.dependencies[task_id]:
            if parent in self._readers:
                self._readers[parent] -= 1
                if self._readers[parent] == 0:
                    del self._readers[parent]
        for successor in self.successors[task_id]:
            if (
                successor not in self.completed
                and successor not in self.running
                and all(parent in self.completed for parent in self.dependencies[successor])
            ):
                self._ready[self.position[successor]] = 1

    def advance(self):
        """Move time to the next stage boundary and return the elapsed seconds."""
        self._complete_finished()
        if not self.running:
            if self.done:
                return 0.0
            raise RuntimeError("cannot wait without a running task")
        usage = Counter(
            resource for flight in self.running.values() for resource in flight.stages[0].resources
        )
        rates = {
            task_id: min(
                self.capacity[resource] / usage[resource] for resource in flight.stages[0].resources
            )
            for task_id, flight in self.running.items()
        }
        step = min(
            flight.stages[0].remaining / rates[task_id] for task_id, flight in self.running.items()
        )
        for task_id, flight in self.running.items():
            flight.stages[0].remaining = max(0, flight.stages[0].remaining - step * rates[task_id])
        self.time += step
        self._complete_finished()
        return step

    def finish_running(self):
        while self.running:
            self.advance()

    def tail_estimate(self):
        """Optimistic finish time: remaining work spread evenly over all workers, no transfers."""
        active = sum(
            sum(
                stage.remaining / min(self.capacity[resource] for resource in stage.resources)
                for stage in flight.stages
            )
            for flight in self.running.values()
        )
        return self.time + (max(0, self.unassigned_work) + active) / len(self.workers)
