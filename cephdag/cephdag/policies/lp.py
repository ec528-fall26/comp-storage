"""Fluid placement plan: one LP over task classes, solved at the first decision, and a cheap
dispatcher that keeps every worker's mix of classes on the plan."""

import json
import time

import numpy as np

from ..graph import ObjectRef
from ..model import Dispatch
from .backlog import local_targets, require_own_hosts
from .simplex import minimize


def task_class(task):
    """Tasks of one class run the same function with the same parameters on inputs stored on
    the same nodes. Only tasks that read nothing but source data have a class."""
    if not all(isinstance(ref, ObjectRef) for ref in task.inputs):
        return None
    pools = tuple(ref.pool for ref in task.inputs)
    return task.function, json.dumps(task.params, sort_keys=True), pools


class PlacementLp:
    """How many tasks of each class each worker should run, from the calibrated models.

    With x[c, w] the (fractional) number of class-c tasks run on worker w and n[c] the size of
    class c, it solves

        minimize T  subject to
            sum over w of x[c, w]                    = n[c]   for every class c
            sum over c of x[c, w] * seconds[c, w]   <= T      for every worker w
            sum over c, w of x[c, w] * busy[c, w, r] <= T     for every disk and link r
            x >= 0

    seconds[c, w] is the model time of one class-c task alone on w (read, compute, write in
    sequence, as the worker runs them); busy[c, w, r] is how long it keeps the read or write
    side of a disk, or a send or receive link, busy: its bytes there over the rate of r.
    Many plans reach the least T; a second LP keeps T at that value and picks the one that
    moves the fewest bytes over the network.
    Outputs stay on the worker's node, or go where the storage system hashes them on a
    role-split cluster. Tasks without a class are not planned.
    """

    def __init__(self, state):
        require_own_hosts(state, "lp-plan")
        built = time.perf_counter()
        self.graph = state.graph
        self.models = state.models
        self.workers = tuple(state.workers)
        members = {}
        for task_id in sorted(state.graph.tasks):
            key = task_class(state.graph.tasks[task_id])
            if key is not None:
                members.setdefault(key, []).append(task_id)
        self.classes = sorted(members)
        self.class_of = {
            task_id: index for index, key in enumerate(self.classes) for task_id in members[key]
        }
        sizes = np.array([len(members[key]) for key in self.classes], dtype=float)
        resources = sorted(name for name in state.capacity if not name.startswith("cpu:"))
        shape = (len(self.classes), len(self.workers))
        seconds = np.zeros(shape)
        busy = np.zeros((*shape, len(resources)))
        moved = np.zeros(shape)
        for index, key in enumerate(self.classes):
            for column, worker in enumerate(self.workers):
                for task_id in members[key]:
                    action = Dispatch(task_id, worker, local_targets(state, task_id, worker))
                    moved[index, column] += state.transfer_bytes(action)
                    for stage in state.stages_for(action):
                        rates = [state.capacity[name] for name in stage.resources]
                        seconds[index, column] += stage.remaining / min(rates)
                        for name, rate in zip(stage.resources, rates):
                            if not name.startswith("cpu:"):
                                busy[index, column, resources.index(name)] += stage.remaining / rate
        seconds /= sizes[:, None]
        busy /= sizes[:, None, None]
        moved /= sizes[:, None]
        self.build_seconds = time.perf_counter() - built

        solved = time.perf_counter()
        count = seconds.size
        worker_rows = np.zeros((len(self.workers), count + 1))
        for column in range(len(self.workers)):
            worker_rows[column, column : count : len(self.workers)] = seconds[:, column]
        resource_rows = np.hstack([busy.reshape(count, -1).T, np.zeros((len(resources), 1))])
        upper = np.vstack([worker_rows, resource_rows])
        upper[:, -1] = -1
        equal = np.zeros((len(self.classes), count + 1))
        for index in range(len(self.classes)):
            equal[index, index * len(self.workers) : (index + 1) * len(self.workers)] = 1
        cost = np.zeros(count + 1)
        cost[-1] = 1
        self.makespan = minimize(cost, upper, np.zeros(len(upper)), equal, sizes)[-1]
        upper = np.vstack([upper, cost])
        bounds = np.zeros(len(upper))
        bounds[-1] = self.makespan * (1 + 1e-6)
        solution = minimize(np.append(moved.ravel(), 0), upper, bounds, equal, sizes)
        self.shares = solution[:count].reshape(shape)
        self.moved_bytes = float(np.sum(self.shares * moved))
        self.solve_seconds = time.perf_counter() - solved
        self.counts = self._round(self.shares, sizes)

    @staticmethod
    def _round(shares, sizes):
        """Whole task counts per class and worker that add up to each class's size, keeping
        the largest fractional parts."""
        counts = np.floor(shares + 1e-9)
        for index, size in enumerate(sizes):
            missing = int(round(size - counts[index].sum()))
            fractions = shares[index] - counts[index]
            for column in sorted(range(len(fractions)), key=lambda c: (-fractions[c], c))[:missing]:
                counts[index, column] += 1
        return counts.astype(int)


class LpPlan:
    """Follow the placement LP's whole-task counts.

    A free worker takes the next ready task of the class in which it lags its planned mix
    most: class c on worker w is due count[c, w] * (tasks w has started + 1) / (tasks planned
    for w) and lags by that less the class-c tasks w has started. A planned task whose input
    must cross the network waits while its source's send link is busy, so each link carries
    one planned transfer at a time at full rate, as in the LP.

    Tasks no worker's remaining plan covers (the unplanned tasks of later stages) go, like
    data-local, to the free worker that reads the fewest of their input bytes remotely. A
    worker whose own plan is done does not take tasks planned for another: that would undo
    the plan exactly where data-local loses, moving a shard off a busy node shortly before
    the node would have finished it. If nothing is running, any ready task is taken, so a
    run never stalls.
    """

    waits_for_links = False

    def __init__(self):
        self.plan = None
        self.last_info = {}

    def choose(self, state):
        self.last_info = {}
        if (
            self.plan is None
            or self.plan.graph is not state.graph
            or self.plan.models != state.models
        ):
            self.plan = PlacementLp(state)
            self.started = np.zeros_like(self.plan.counts)
            self.last_info = {
                "lp_build_seconds": self.plan.build_seconds,
                "lp_solve_seconds": self.plan.solve_seconds,
                "lp_makespan_seconds": self.plan.makespan,
                "lp_classes": len(self.plan.classes),
            }
        free = state.free_workers()
        ready = state.ready()
        if not free or not ready:
            return None
        ready_by_class = {}
        for task_id in ready:
            ready_by_class.setdefault(self.plan.class_of.get(task_id), []).append(task_id)
        for worker in free:
            task_id = self._planned(state, worker, ready_by_class)
            if task_id is not None:
                return self._dispatch(state, task_id, worker)
        unclaimed = [
            task_id
            for index, tasks in ready_by_class.items()
            for task_id in tasks[: len(tasks) - self._planned_left(index)]
        ]
        if not state.running:
            unclaimed = ready
        if not unclaimed:
            return None
        _, task_id, worker = min(
            (remote_input_bytes(state, task_id, worker), task_id, worker)
            for task_id in sorted(unclaimed)[:24]
            for worker in free
        )
        return self._dispatch(state, task_id, worker)

    def _planned(self, state, worker, ready_by_class):
        column = self.plan.workers.index(worker)
        planned = self.plan.counts[:, column]
        started = self.started[:, column]
        position = (started.sum() + 1) / max(1, planned.sum())
        best = None
        for index, tasks in ready_by_class.items():
            if index is None or started[index] >= planned[index]:
                continue
            task_id = tasks[0]
            if not self.waits_for_links and self._needs_busy_link(state, task_id, worker):
                continue
            lag = planned[index] * position - started[index]
            if best is None or lag > best[0]:
                best = (lag, task_id)
        if best is None or self._needs_busy_link(state, best[1], worker):
            return None
        return best[1]

    def _planned_left(self, index):
        """Tasks of class `index` that some worker's plan still has to start."""
        if index is None:
            return 0
        return int(np.maximum(self.plan.counts[index] - self.started[index], 0).sum())

    def _needs_busy_link(self, state, task_id, worker):
        return remote_input_bytes(state, task_id, worker) and self._link_busy(state, task_id)

    @staticmethod
    def _link_busy(state, task_id):
        senders = {f"tx:{state.location(ref)}" for ref in state.graph.tasks[task_id].inputs}
        return any(
            link in stage.resources
            for flight in state.running.values()
            for stage in flight.stages
            for link in senders
        )

    def _dispatch(self, state, task_id, worker):
        index = self.plan.class_of.get(task_id)
        if index is not None:
            self.started[index, self.plan.workers.index(worker)] += 1
        return Dispatch(task_id, worker, local_targets(state, task_id, worker))


class LpPlan2(LpPlan):
    """lp-plan with two dispatcher changes that keep the hot node's send link and the hot
    node itself busy (demo3/DIAGNOSIS.md, section 2).

    A worker whose most lagging class is a transfer over a busy link waits for the link.
    lp-plan's worker started a task of another class there instead, and the link stayed idle
    until that task was done.

    A worker whose own plan is done takes a ready task of a class stored on its own node that
    the plan gave to another worker and nobody has started; the plan now counts it as this
    worker's. lp-plan leaves the hot node idle at the end while transfers off it still wait
    for its link.
    """

    waits_for_links = True

    def choose(self, state):
        action = super().choose(state)
        if action is not None:
            return action
        for worker in state.free_workers():
            column = self.plan.workers.index(worker)
            if self.started[:, column].sum() < self.plan.counts[:, column].sum():
                continue
            for task_id in state.ready():
                index = self.plan.class_of.get(task_id)
                if index is None or remote_input_bytes(state, task_id, worker):
                    continue
                left = self.plan.counts[index] - self.started[index]
                if left.max() > 0:
                    self.plan.counts[index, np.argmax(left)] -= 1
                    self.plan.counts[index, column] += 1
                    return self._dispatch(state, task_id, worker)
        return None


def remote_input_bytes(state, task_id, worker):
    return sum(
        state.resolve(ref).size
        for ref in state.graph.tasks[task_id].inputs
        if state.location(ref) != worker
    )
