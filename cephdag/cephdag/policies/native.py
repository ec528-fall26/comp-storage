"""Beam search and MCTS in the Rust crate under native/, called through ctypes.

The crate implements the same event simulation as model.Simulation for 1 to MAX_NODES
nodes. Buffers carry only numbers: task indexes, byte sizes below 2**53 and model rates.
"""

from array import array
import ctypes
from pathlib import Path
import random
import sys
import time

from ..graph import ObjectRef
from ..model import Dispatch, Objective

ABI_VERSION = 5
MAX_NODES = 12
KINDS = ("read", "write", "tx", "rx", "cpu")
LIBRARY = Path(__file__).resolve().parents[2] / "native" / "target" / "release"


def _buffer(values):
    limit = 2**53 - 1
    # max/min run in C, so the per-value type check below only runs when something is large.
    if values and (max(values) > limit or min(values) < -limit):
        if any(isinstance(value, int) and abs(value) > limit for value in values):
            raise ValueError("native integer exceeds exact numeric wire range")
    data = array("d", values)
    return data, (ctypes.c_double * len(data)).from_buffer(data)


def _load_library():
    suffix = "dylib" if sys.platform == "darwin" else "so"
    library = ctypes.CDLL(str(LIBRARY / f"libcephdag_native.{suffix}"))
    library.planner_abi_version.argtypes = []
    library.planner_abi_version.restype = ctypes.c_uint32
    if library.planner_abi_version() != ABI_VERSION:
        raise RuntimeError("native ABI mismatch; rebuild the Rust library")
    doubles = ctypes.POINTER(ctypes.c_double)
    library.planner_graph.argtypes = [doubles, ctypes.c_size_t]
    library.planner_graph.restype = ctypes.c_void_p
    library.planner_free.argtypes = [ctypes.c_void_p]
    library.planner_free.restype = None
    library.planner_error.argtypes = []
    library.planner_error.restype = ctypes.c_char_p
    for name in ("planner_simulate", "planner_search", "planner_beam"):
        function = getattr(library, name)
        function.argtypes = [ctypes.c_void_p, doubles, ctypes.c_size_t, doubles, ctypes.c_size_t]
        function.restype = ctypes.c_ssize_t
    return library


class NativeGraph:
    """A Rust-side copy of one graph and model; rebuilt when either changes."""

    def __init__(self, state):
        if len(state.workers) != state.node_count or len(state.storage_nodes) != state.node_count:
            raise ValueError(
                "native policies need every node to have both a worker and a pool; this "
                f"cluster has {len(state.workers)} workers and {len(state.storage_nodes)} pools "
                f"on {state.node_count} nodes"
            )
        if not 1 <= state.node_count <= MAX_NODES:
            raise ValueError(
                f"native policies support 1 to {MAX_NODES} nodes, not {state.node_count}"
            )
        self.library = _load_library()
        self.graph = state.graph
        self.models = state.models
        self.node_index = state.node_index
        self.ids = sorted(self.graph.tasks)
        self.index = {task_id: position for position, task_id in enumerate(self.ids)}
        self.node_count = state.node_count
        values = [len(self.ids), self.node_count]
        values += [
            state.capacity[f"{kind}:{node}"] for kind in KINDS for node in range(self.node_count)
        ]
        compute = state.models.compute
        # The crate matches Python's sum(): compensated on CPython 3.12+, sequential before.
        values.append(int(sys.implementation.name == "cpython" and sys.version_info >= (3, 12)))
        for task_id in self.ids:
            task = self.graph.tasks[task_id]
            values.append(len(task.inputs))
            for ref in task.inputs:
                if isinstance(ref, ObjectRef):
                    values += [-1, 0, self.node_index[ref.pool], ref.size]
                else:
                    values += [self.index[ref.task], ref.output, 0, self.graph.size(ref)]
            sizes = [self.graph.size(ref) for ref in task.inputs]
            values += [len(task.output_sizes), *task.output_sizes]
            values += [
                compute.seconds(task, sizes),
                task.params["iterations"],
                state.work[task_id],
                task.memory,
                *compute.coefficients(task.function),
            ]
        data, buffer = _buffer(values)
        self.handle = self.library.planner_graph(buffer, len(data))
        if not self.handle:
            raise ValueError(self.library.planner_error().decode())

    def close(self):
        if getattr(self, "handle", None):
            self.library.planner_free(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()

    def __del__(self):
        self.close()

    def action_values(self, action):
        if action is None:
            return [-1]
        return [self.index[action.task], action.worker, *action.targets]

    def read_action(self, out):
        position = int(next(out))
        if position < 0:
            return None
        task_id = self.ids[position]
        worker = int(next(out))
        targets = tuple(int(next(out)) for _ in self.graph.tasks[task_id].output_sizes)
        return Dispatch(task_id, worker, targets)

    def state_values(self, state, completed_tasks):
        """`state` as numbers, with the placements of `completed_tasks` only.

        A search reads only the outputs that unfinished tasks still read, so policies send
        state.still_read(); the crate counts the rest without knowing where they are.
        """
        if state.graph is not self.graph:
            raise ValueError("native handle belongs to another graph")
        values = [state.time, state.remote_bytes, state.unassigned_work, *state.assigned_work]
        values += [state.memory_limit, len(state.completed), int(state.resized)]
        values.append(len(completed_tasks))
        for task_id in completed_tasks:
            refs = state.completed[task_id]
            values += [
                self.index[task_id],
                *(self.node_index[ref.pool] for ref in refs),
                *(ref.size for ref in refs),
            ]
        values.append(len(state.running))
        for flight in state.running.values():
            values += self.action_values(flight.action)
            values.append(len(flight.stages))
            for stage in flight.stages:
                values += [stage.remaining, len(stage.resources)]
                for resource in stage.resources:
                    kind, node = resource.split(":")
                    values.append(KINDS.index(kind) * self.node_count + int(node))
        ready = state.ready()
        values += [len(ready), *(self.index[task_id] for task_id in ready)]
        return values

    def call(self, name, values, capacity=100000):
        if not self.handle:
            raise ValueError("native handle is closed")
        data, buffer = _buffer(values)
        out = (ctypes.c_double * capacity)()
        size = getattr(self.library, name)(self.handle, buffer, len(data), out, capacity)
        if size < 0:
            raise ValueError(self.library.planner_error().decode())
        return list(out[:size])

    def simulate(self, state, actions):
        """Apply dispatches (None means advance) natively; used to cross-check model.Simulation."""
        values = self.state_values(state, list(state.completed)) + [len(actions)]
        for action in actions:
            values += self.action_values(action)
        capacity = 16 + self.node_count + len(self.ids) * 12
        out = iter(self.call("planner_simulate", values, capacity))
        result = {"time": next(out), "remote_bytes": next(out), "tail": next(out)}
        result["assigned_work"] = [next(out) for _ in range(self.node_count)]
        result["completed_count"] = int(next(out))
        completed = {}
        for _ in range(int(next(out))):
            task_id = self.ids[int(next(out))]
            completed[task_id] = [int(next(out)) for _ in self.graph.tasks[task_id].output_sizes]
        result["completed"] = completed
        result["running"] = [self.ids[int(next(out))] for _ in range(int(next(out)))]
        result["ready"] = [self.ids[int(next(out))] for _ in range(int(next(out)))]
        return result


class NativePolicy:
    """Owns the NativeGraph handle and rebuilds it when the graph or model changes."""

    def __init__(self):
        self.native = None
        self.last_info = {}

    def _graph_for(self, state):
        if (
            self.native is None
            or self.native.graph is not state.graph
            or self.native.models != state.models
        ):
            self.close()
            self.native = NativeGraph(state)
        return self.native

    def close(self):
        if self.native:
            self.native.close()
            self.native = None


class Beam(NativePolicy):
    """Beam search over the next `horizon` dispatches and waits, keeping `width` states."""

    def __init__(self, width=4, horizon=12, budget_ms=40, objective=None):
        super().__init__()
        self.width = width
        self.horizon = horizon
        self.budget_ms = budget_ms
        self.objective = objective or Objective()

    def choose(self, state):
        if not state.ready(1) or not state.free_workers():
            self.last_info = {"iterations": 0}
            return None
        start = time.perf_counter()
        native = self._graph_for(state)
        values = native.state_values(state, state.still_read())
        setup = time.perf_counter() - start
        values += [
            self.width,
            self.horizon,
            max(0, self.budget_ms / 1000 - setup),
            self.objective.seconds_weight,
            self.objective.network_byte_weight,
        ]
        out = iter(native.call("planner_beam", values, 64))
        expansions, levels, truncated, core_seconds, dispatches, advances, _, _, _ = [
            next(out) for _ in range(9)
        ]
        action = native.read_action(out)
        self.last_info = {
            "iterations": int(expansions),
            "levels_completed": int(levels),
            "deadline_truncated": bool(truncated),
            "simulated_transitions": int(dispatches + advances),
            "core_seconds": core_seconds,
            "seconds": time.perf_counter() - start,
        }
        return action


class NativeMcts(NativePolicy):
    """Monte Carlo tree search with heuristic or uniform rollouts; kept as a comparison."""

    def __init__(
        self,
        iterations=48,
        budget_ms=40,
        horizon=12,
        seed=1,
        objective=None,
        rollout_policy="heuristic",
    ):
        super().__init__()
        if rollout_policy not in ("heuristic", "uniform"):
            raise ValueError("unknown rollout policy")
        self.iterations = iterations
        self.budget_ms = budget_ms
        self.horizon = horizon
        self.random = random.Random(seed)
        self.objective = objective or Objective()
        self.rollout_policy = rollout_policy

    def choose(self, state):
        start = time.perf_counter()
        native = self._graph_for(state)
        values = native.state_values(state, state.still_read())
        setup = time.perf_counter() - start
        values += [
            self.iterations,
            self.horizon,
            max(0.0, self.budget_ms / 1000 - setup),
            int(self.rollout_policy == "uniform"),
            self.objective.seconds_weight,
            self.objective.network_byte_weight,
            *self.random.getstate()[1],
        ]
        out = iter(native.call("planner_search", values, 1024))
        completed = int(next(out))
        core_seconds = next(out)
        dispatches, advances, _, _, _, _ = [int(next(out)) for _ in range(6)]
        action = native.read_action(out)
        generator = tuple(int(next(out)) for _ in range(625))
        self.random.setstate((3, generator, self.random.getstate()[2]))
        self.last_info = {
            "iterations": completed,
            "iterations_requested": self.iterations,
            "simulated_transitions": dispatches + advances,
            "core_seconds": core_seconds,
            "seconds": time.perf_counter() - start,
        }
        return action
