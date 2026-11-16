"""Object references, tasks and the static task graph a client submits."""

from dataclasses import asdict, dataclass, replace
import math
import re
import uuid

from .functions import FunctionSpec, lookup

WORKER_MEMORY = 512 * 2**20
ITEM_SIZES = {"float32": 4}


@dataclass(frozen=True)
class ObjectRef:
    """Bytes stored in a RADOS pool. `dtype` is the type tag of those bytes."""

    pool: str
    key: str
    size: int
    dtype: str = "float32"
    partition: int = 0
    sha256: str = ""

    def __post_init__(self):
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+", self.pool)
            or not self.key
            or self.key.startswith("/")
            or ".." in self.key.split("/")
            or type(self.size) is not int
            or self.size < 0
            or not self.dtype
            or type(self.partition) is not int
            or self.partition < 0
        ):
            raise ValueError("invalid object reference")
        if self.sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("invalid checksum")

    def to_dict(self):
        return {"kind": "object", **asdict(self)}


@dataclass(frozen=True)
class OutputRef:
    """An output of a task that has not necessarily run yet."""

    graph: str
    task: str
    output: int
    dtype: str
    partition: int

    def to_dict(self):
        return {"kind": "output", **asdict(self)}


def ref_from_dict(value):
    data = dict(value)
    kind = data.pop("kind")
    if kind == "object":
        return ObjectRef(**data)
    if kind == "output":
        return OutputRef(**data)
    raise ValueError("unknown reference kind")


@dataclass(frozen=True)
class Dataset:
    """Named, partitioned data: one reference per partition."""

    name: str
    shards: tuple

    def __post_init__(self):
        if len({shard.partition for shard in self.shards}) != len(self.shards):
            raise ValueError("duplicate partition")

    def to_dict(self):
        return {"name": self.name, "shards": [shard.to_dict() for shard in self.shards]}

    @classmethod
    def from_dict(cls, data):
        return cls(data["name"], tuple(ref_from_dict(shard) for shard in data["shards"]))


def estimate_output_sizes(input_sizes, ratios, output_types):
    """Each output is `ratio` times the total input, rounded down to whole items."""
    total = sum(input_sizes)
    sizes = []
    for ratio, dtype in zip(ratios, output_types):
        item = ITEM_SIZES.get(dtype, 1)
        sizes.append(int(total * ratio / item) * item)
    return tuple(sizes)


def memory_reservation(input_sizes, output_sizes):
    # Input buffers, scratch arrays, output arrays and their serialized copies.
    return sum(input_sizes) + 3 * max(input_sizes, default=0) + 2 * sum(output_sizes) + 8 * 2**20


@dataclass(frozen=True)
class Task:
    id: str
    function: str
    inputs: tuple
    output_types: tuple
    output_sizes: tuple
    partition: int
    params: dict
    memory: int

    def to_dict(self):
        return {**asdict(self), "inputs": [ref.to_dict() for ref in self.inputs]}

    def sized_for(self, input_sizes):
        """This task with output estimates and reservation recomputed for these input sizes."""
        outputs = estimate_output_sizes(input_sizes, self.params["ratios"], self.output_types)
        return replace(self, output_sizes=outputs, memory=memory_reservation(input_sizes, outputs))


class Graph:
    """A static DAG. Building it performs no computation or I/O."""

    def __init__(self, *, memory_limit=WORKER_MEMORY, graph_id=None):
        self.id = graph_id or uuid.uuid4().hex
        self.memory_limit = memory_limit
        self.functions = {}
        self.tasks = {}
        self.frozen = False
        self._index = None
        self._positions = None
        self._insertion_index = None
        self._work_cache = {}

    def size(self, ref):
        """Bytes of `ref`; for a task output this is the declared estimate."""
        if isinstance(ref, ObjectRef):
            return ref.size
        if not isinstance(ref, OutputRef) or ref.graph != self.id:
            raise ValueError("foreign graph reference")
        task = self.tasks.get(ref.task)
        if (
            task is None
            or ref.output < 0
            or ref.output >= len(task.output_sizes)
            or ref.dtype != task.output_types[ref.output]
            or ref.partition != task.partition
        ):
            raise ValueError("invalid output reference")
        return task.output_sizes[ref.output]

    def spec(self, function):
        return self.functions.get(function) or lookup(function).spec

    def add(self, function, inputs, *, ratios=None, iterations=1, partition=None, params=None):
        """Add one task and return references to its outputs."""
        if self.frozen:
            raise RuntimeError("graph is frozen after submit")
        spec = self.spec(function)
        inputs = tuple(inputs)
        if tuple(ref.dtype for ref in inputs) != spec.input_types:
            raise ValueError("function input signature mismatch")
        sizes = [self.size(ref) for ref in inputs]
        ratios = tuple(ratios if ratios is not None else [1] * len(spec.output_types))
        if (
            len(ratios) != len(spec.output_types)
            or any(not math.isfinite(ratio) or ratio < 0 for ratio in ratios)
            or isinstance(iterations, bool)
            or not isinstance(iterations, int)
            or iterations < 0
        ):
            raise ValueError("invalid output ratio or iteration count")
        reserved = {"ratios", "iterations"} & set(params or {})
        if reserved:
            raise ValueError(f"reserved parameter names: {sorted(reserved)}")
        outputs = estimate_output_sizes(sizes, ratios, spec.output_types)
        memory = memory_reservation(sizes, outputs)
        if memory > self.memory_limit:
            raise ValueError(f"task memory reservation {memory} exceeds {self.memory_limit}")
        if partition is None:
            partition = inputs[0].partition if inputs else 0
        if partition < 0:
            raise ValueError("invalid partition")
        task_id = f"t{len(self.tasks):06d}"
        task_params = {"ratios": list(ratios), "iterations": iterations, **(params or {})}
        task = Task(
            task_id, spec.key, inputs, spec.output_types, outputs, partition, task_params, memory
        )
        self.functions[spec.key] = spec
        self.tasks[task_id] = task
        self._index = None
        self._positions = None
        self._insertion_index = None
        self._work_cache = {}
        return tuple(
            OutputRef(self.id, task_id, index, dtype, partition)
            for index, dtype in enumerate(spec.output_types)
        )

    def map(self, function, *datasets, **options):
        """Apply a one-output function to aligned partitions of one or more datasets."""
        if not datasets or any(len(data.shards) != len(datasets[0].shards) for data in datasets):
            raise ValueError("partition alignment mismatch")
        rows = list(zip(*(data.shards for data in datasets)))
        if any(len({ref.partition for ref in row}) != 1 for row in rows):
            raise ValueError("partition alignment mismatch")
        if len(self.spec(function).output_types) != 1:
            raise ValueError("map requires a one-output function; use add for several outputs")
        return Dataset(function, tuple(self.add(function, row, **options)[0] for row in rows))

    def reduce(self, function, dataset, **options):
        """Combine all partitions with a two-input function as a balanced pairwise tree."""
        refs = list(dataset.shards)
        if not refs:
            raise ValueError("cannot reduce an empty dataset")
        while len(refs) > 1:
            following = []
            for index in range(0, len(refs), 2):
                if index + 1 == len(refs):
                    following.append(refs[index])
                else:
                    pair = refs[index : index + 2]
                    following.append(self.add(function, pair, partition=index // 2, **options)[0])
            refs = following
        return refs[0]

    def index(self):
        """(dependencies, successors) per task id, cached until the graph changes."""
        if self._index is None:
            dependencies = {
                task_id: frozenset(ref.task for ref in task.inputs if isinstance(ref, OutputRef))
                for task_id, task in self.tasks.items()
            }
            successors = {task_id: [] for task_id in self.tasks}
            for task_id, parents in dependencies.items():
                for parent in parents:
                    if parent not in successors:
                        raise ValueError("unknown dependency")
                    successors[parent].append(task_id)
            self._index = (dependencies, successors)
        return self._index

    def positions(self):
        """(task ids in sorted order, position of each id in it), cached until the graph changes."""
        if self._positions is None:
            order = tuple(sorted(self.tasks))
            self._positions = (order, {task_id: position for position, task_id in enumerate(order)})
        return self._positions

    def insertion_index(self):
        """Position of each task id in `tasks`, cached until the graph changes."""
        if self._insertion_index is None:
            self._insertion_index = {task_id: index for index, task_id in enumerate(self.tasks)}
        return self._insertion_index

    def dependencies(self, task_id):
        return self.index()[0][task_id]

    def ready(self, completed, running):
        finished = set(completed)
        occupied = finished | set(running)
        return [
            task_id
            for task_id in self.tasks
            if task_id not in occupied and self.dependencies(task_id) <= finished
        ]

    def freeze(self):
        """Validate every reference, size and reservation, reject cycles, forbid changes."""
        if self.frozen:
            return self
        for task in self.tasks.values():
            sizes = [self.size(ref) for ref in task.inputs]
            ratios = task.params.get("ratios", [])
            iterations = task.params.get("iterations")
            if (
                len(ratios) != len(task.output_sizes)
                or any(not math.isfinite(ratio) or ratio < 0 for ratio in ratios)
                or type(iterations) is not int
                or iterations < 0
            ):
                raise ValueError("invalid task parameters")
            expected = task.sized_for(sizes)
            if (
                task.output_sizes != expected.output_sizes
                or task.memory != expected.memory
                or task.memory > self.memory_limit
            ):
                raise ValueError("invalid serialized sizes or memory reservation")
        dependencies, successors = self.index()
        waiting = {task_id: len(parents) for task_id, parents in dependencies.items()}
        reachable = [task_id for task_id, count in waiting.items() if count == 0]
        for task_id in reachable:  # the list grows while it is read
            for successor in successors[task_id]:
                waiting[successor] -= 1
                if waiting[successor] == 0:
                    reachable.append(successor)
        if len(reachable) < len(self.tasks):
            raise ValueError("graph contains a cycle")
        self.frozen = True
        return self

    def to_dict(self):
        return {
            "schema": 1,
            "id": self.id,
            "memory_limit": self.memory_limit,
            "functions": [asdict(spec) for spec in self.functions.values()],
            "tasks": [task.to_dict() for task in self.tasks.values()],
        }

    @classmethod
    def from_dict(cls, data):
        if data.get("schema") != 1:
            raise ValueError("unsupported graph schema")
        graph = cls(memory_limit=data["memory_limit"], graph_id=data["id"])
        for raw in data["functions"]:
            spec = FunctionSpec(
                raw["name"], raw["version"], tuple(raw["input_types"]), tuple(raw["output_types"])
            )
            graph.functions[spec.key] = spec
        for raw in data["tasks"]:
            task = Task(
                raw["id"],
                raw["function"],
                tuple(ref_from_dict(ref) for ref in raw["inputs"]),
                tuple(raw["output_types"]),
                tuple(raw["output_sizes"]),
                raw["partition"],
                raw["params"],
                raw["memory"],
            )
            if task.id in graph.tasks or task.memory > graph.memory_limit:
                raise ValueError("duplicate task or exceeded memory limit")
            spec = graph.functions.get(task.function)
            if spec is None or spec.input_types != tuple(ref.dtype for ref in task.inputs):
                raise ValueError("unregistered function or signature mismatch")
            if task.output_types != spec.output_types or len(task.output_sizes) != len(
                spec.output_types
            ):
                raise ValueError("invalid output signature")
            graph.tasks[task.id] = task
        return graph.freeze()
