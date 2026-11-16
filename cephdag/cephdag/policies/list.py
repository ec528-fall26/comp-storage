"""HEFT list scheduling and locality-aware work stealing.

HEFT plans the whole graph once with insertion calendars, counting durable reads and
writes as part of a worker's busy interval. Its static calendars ignore shared-link
contention; the live coordinator and the simulation do not. Contention-aware HEFT
(heft-cc) also keeps a calendar per send link and per receive link and books every
transfer on both.
"""

import bisect
import heapq
import math

from ..graph import ObjectRef, OutputRef
from ..model import Dispatch, Objective

# The calibrated read rates differ by up to 1.5x between nodes on the same hardware; without
# a margin the plan moves a third of a uniform dataset to chase that difference.
MOVE_MARGIN = 0.05


def insertion_start(calendar, release, duration):
    """Earliest start at or after `release` that fits `duration` into a gap of `calendar`."""
    start = release
    for left, right, _ in sorted(calendar):
        if start + duration <= left + 1e-12:
            return start
        if start < right:
            start = right
    return start


class Calendar:
    """One worker's planned busy intervals, answering insertion_start without a full scan.

    Consecutive sorted intervals that leave no gap form a block. A duration that is large
    against the 1e-12 slack and the rounding of the times can only start at `release` or
    in a gap before a block, so only blocks that start after `release` are examined.
    """

    def __init__(self):
        self.intervals = []
        self.block_starts = []
        self.block_ends = []

    def add(self, start, end, task):
        bisect.insort(self.intervals, (start, end, task))
        block = bisect.bisect_right(self.block_starts, start) - 1
        if block >= 0 and start <= self.block_ends[block]:
            self.block_ends[block] = max(self.block_ends[block], end)
        else:
            block += 1
            self.block_starts.insert(block, start)
            self.block_ends.insert(block, end)
        following = block + 1
        while following < len(self.block_starts) and (
            self.block_starts[following] <= self.block_ends[block]
        ):
            self.block_ends[block] = max(self.block_ends[block], self.block_ends.pop(following))
            self.block_starts.pop(following)

    def earliest_start(self, release, duration):
        """insertion_start(self.intervals, release, duration)."""
        if not self.intervals:
            return release
        latest = max(release, self.block_ends[-1])
        if duration < 2e-12 or duration < 4 * math.ulp(latest + duration):
            return insertion_start(self.intervals, release, duration)
        for block in range(bisect.bisect_right(self.block_starts, release), len(self.block_starts)):
            start = max(release, self.block_ends[block - 1]) if block else release
            if start + duration <= self.block_starts[block] + 1e-12:
                return start
        return latest


class HeftPlan:
    """Static HEFT plan: per-worker queues of task ids and the planned action of each task."""

    move_margin = 0.0

    def __init__(self, state, objective):
        self.graph = state.graph
        self.models = state.models
        self.node_index = state.node_index
        self.storage_nodes = state.storage_nodes
        self.hosts = state.hosts
        workers = state.workers
        dependencies, successors = self.graph.index()
        order = self._topological_order(dependencies, successors)
        tasks = self.graph.tasks
        self.input_sizes = {
            task: [self.graph.size(ref) for ref in tasks[task].inputs] for task in order
        }
        self.compute_seconds = {
            task: self.models.compute.seconds(tasks[task], self.input_sizes[task]) for task in order
        }
        self.local = {
            task: [self.local_seconds(task, worker) for worker in workers] for task in order
        }
        self.edge = {
            (task, successor): sum(
                self.graph.size(ref)
                for ref in self.graph.tasks[successor].inputs
                if isinstance(ref, OutputRef) and ref.task == task
            )
            for task in order
            for successor in successors[task]
        }
        average = {task: sum(self.local[task]) / len(workers) for task in order}
        upward_rank = {}
        for task in reversed(order):
            upward_rank[task] = average[task] + max(
                (
                    self.mean_edge(task, successor, workers) + upward_rank[successor]
                    for successor in successors[task]
                ),
                default=0,
            )
        self.actions = {}
        self.intervals = {}
        calendars = [Calendar() for _ in range(state.node_count)]
        # The next task planned is the highest-ranked one whose parents are all planned.
        unplanned_parents = {task: len(dependencies[task]) for task in order}
        plannable = [(-upward_rank[task], task) for task in order if not dependencies[task]]
        heapq.heapify(plannable)
        while plannable:
            _, task = heapq.heappop(plannable)
            release = max((self.intervals[parent][1] for parent in dependencies[task]), default=0)
            best = None
            for worker in workers:
                reading = self.read_and_compute(task, worker)
                for targets in state.target_options(task):
                    start, end, remote, transfers = self.place(
                        reading, task, worker, targets, release, calendars[worker]
                    )
                    compared = end * (1 + self.move_margin) if remote else end
                    key = (*objective.key(compared, remote), end, task, worker, targets)
                    if best is None or key < best[0]:
                        best = (key, start, end, transfers)
            key, start, end, transfers = best
            action = Dispatch(task, *key[-2:])
            self.actions[task] = action
            self.intervals[task] = (start, end)
            calendars[action.worker].add(start, end, task)
            self.book(transfers, task)
            for successor in successors[task]:
                unplanned_parents[successor] -= 1
                if unplanned_parents[successor] == 0:
                    heapq.heappush(plannable, (-upward_rank[successor], successor))
        position = {task: index for index, task in enumerate(order)}
        self.queues = [
            [
                task
                for _, _, task in sorted(
                    calendar.intervals, key=lambda slot: (slot[0], slot[1], position[slot[2]])
                )
            ]
            for calendar in calendars
        ]

    @staticmethod
    def _topological_order(dependencies, successors):
        """Tasks by depth, sorted by id within each depth."""
        unseen_parents = {task: len(parents) for task, parents in dependencies.items()}
        level = sorted(task for task, count in unseen_parents.items() if count == 0)
        order = []
        while level:
            order.extend(level)
            following = []
            for task in level:
                for successor in successors[task]:
                    unseen_parents[successor] -= 1
                    if unseen_parents[successor] == 0:
                        following.append(successor)
            level = sorted(following)
        if len(order) < len(dependencies):
            raise ValueError("cyclic graph")
        return order

    def local_seconds(self, task_id, worker):
        return (
            sum(self.input_sizes[task_id]) / self.read_rate(worker)
            + self.compute_seconds[task_id]
            + sum(self.graph.tasks[task_id].output_sizes) / self.write_rate(worker)
        )

    def read_rate(self, worker):
        """How fast `worker` reads at best: from its host's pool, or else the fastest remote
        one."""
        storage = self.models.storage
        network = self.models.network
        host = self.hosts[worker]
        if host in self.storage_nodes:
            return storage.read_bps[host]
        return max(
            min(storage.read_bps[node], network.tx_bps[node], network.rx_bps[host])
            for node in self.storage_nodes
        )

    def write_rate(self, worker):
        """How fast `worker` writes at best: to its host's pool, or else the fastest remote
        one."""
        storage = self.models.storage
        network = self.models.network
        host = self.hosts[worker]
        if host in self.storage_nodes:
            return storage.write_bps[host]
        return max(
            min(storage.write_bps[node], network.tx_bps[host], network.rx_bps[node])
            for node in self.storage_nodes
        )

    def penalty(self, size, source, target):
        """Extra seconds to read `size` bytes on worker `target` when they are stored on
        `source`."""
        host = self.hosts[target]
        if source == host:
            return 0.0
        storage = self.models.storage
        network = self.models.network
        rate = min(storage.read_bps[source], network.tx_bps[source], network.rx_bps[host])
        return size / rate - size / self.read_rate(target)

    def mean_edge(self, task, successor, workers):
        """Mean penalty of `successor` reading what `task` wrote, over every pool and worker."""
        size = self.edge[task, successor]
        total = sum(
            self.penalty(size, source, target)
            for source in self.storage_nodes
            for target in workers
        )
        return total / (len(self.storage_nodes) * len(workers))

    def duration(self, action):
        """(seconds, remote bytes) of `action` alone, with inputs where the plan put them."""
        reading = self.read_and_compute(action.task, action.worker)
        return self.add_writes(reading, action.task, action.worker, action.targets)

    def place(self, reading, task_id, worker, targets, release, calendar):
        """(start, end, remote bytes, transfers to book) of the task on `worker`, inserted into
        its `calendar` no earlier than `release`. Transfers take the link as if alone."""
        seconds, remote = self.add_writes(reading, task_id, worker, targets)
        start = calendar.earliest_start(release, seconds)
        return start, start + seconds, remote, ()

    def book(self, transfers, task_id):
        """Links are not booked: every transfer is planned as if it had them to itself."""

    def reads(self, task_id, worker):
        """(source node, bytes, rate) of each input of `task_id` read on `worker`."""
        storage = self.models.storage
        network = self.models.network
        host = self.hosts[worker]
        for ref, size in zip(self.graph.tasks[task_id].inputs, self.input_sizes[task_id]):
            if isinstance(ref, ObjectRef):
                node = self.node_index[ref.pool]
            else:
                node = self.actions[ref.task].targets[ref.output]
            rate = storage.read_bps[node]
            if node != host:
                rate = min(rate, network.tx_bps[node], network.rx_bps[host])
            yield node, size, rate

    def writes(self, task_id, worker, targets):
        """(target node, bytes, rate) of each output of `task_id` written from `worker`."""
        storage = self.models.storage
        network = self.models.network
        host = self.hosts[worker]
        for node, size in zip(targets, self.graph.tasks[task_id].output_sizes):
            rate = storage.write_bps[node]
            if node != host:
                rate = min(rate, network.tx_bps[host], network.rx_bps[node])
            yield node, size, rate

    def read_and_compute(self, task_id, worker):
        """(seconds, remote bytes) of reading every input on `worker` and computing."""
        seconds = 0.0
        remote = 0
        for node, size, rate in self.reads(task_id, worker):
            if node != self.hosts[worker]:
                remote += size
            seconds += size / rate
        seconds += self.compute_seconds[task_id]
        return seconds, remote

    def add_writes(self, reading, task_id, worker, targets):
        """`reading` from read_and_compute plus writing each output to its target node."""
        seconds, remote = reading
        for node, size, rate in self.writes(task_id, worker, targets):
            if node != self.hosts[worker]:
                remote += size
            seconds += size / rate
        return seconds, remote


class ContentionHeftPlan(HeftPlan):
    """HEFT plan that books every transfer on the sender's send link and the receiver's
    receive link, beside each worker's calendar, so transfers sharing a link run one after
    another instead of overlapping. A task's reads, compute and writes run in sequence on its
    worker; a transfer waits until both its links are free, and the worker waits with it.
    A placement that moves bytes must finish MOVE_MARGIN earlier than one that does not."""

    move_margin = MOVE_MARGIN

    def __init__(self, state, objective):
        self.sending = [Calendar() for _ in range(state.node_count)]
        self.receiving = [Calendar() for _ in range(state.node_count)]
        super().__init__(state, objective)

    def place(self, reading, task_id, worker, targets, release, calendar):
        _, remote = self.add_writes(reading, task_id, worker, targets)
        start = release
        while True:
            end, transfers = self.booked_end(task_id, worker, targets, start)
            fit = calendar.earliest_start(start, end - start)
            if fit == start:
                return start, end, remote, transfers
            start = fit

    def book(self, transfers, task_id):
        for sender, receiver, start, end in transfers:
            self.sending[sender].add(start, end, task_id)
            self.receiving[receiver].add(start, end, task_id)

    def booked_end(self, task_id, worker, targets, start):
        """When the task ends if `worker` starts it at `start`, and its transfers as
        (sender, receiver, start, end)."""
        time = start
        transfers = []
        host = self.hosts[worker]
        for node, size, rate in self.reads(task_id, worker):
            time = self.transfer_end(node, host, size / rate, time, transfers)
        time += self.compute_seconds[task_id]
        for node, size, rate in self.writes(task_id, worker, targets):
            time = self.transfer_end(host, node, size / rate, time, transfers)
        return time, transfers

    def transfer_end(self, sender, receiver, seconds, release, transfers):
        if sender == receiver:
            return release + seconds
        start = release
        while True:
            start = self.sending[sender].earliest_start(start, seconds)
            both = self.receiving[receiver].earliest_start(start, seconds)
            if both == start:
                break
            start = both
        transfers.append((sender, receiver, start, start + seconds))
        return start + seconds


class Heft:
    """Follow the static HEFT plan: start each free worker's next planned task once it is ready."""

    plan_type = HeftPlan

    def __init__(self, objective=None):
        self.objective = objective or Objective()
        self.plan = None
        self.skipped = []

    def choose(self, state):
        if (
            self.plan is None
            or self.plan.graph is not state.graph
            or self.plan.models != state.models
        ):
            self.plan = self.plan_type(state, self.objective)
            self.skipped = [0] * state.node_count
        choices = []
        for worker in state.free_workers():
            head = self._head(state, worker)
            if head is not None and state.is_ready(head):
                choices.append(self.plan.actions[head])
        return min(
            choices,
            key=lambda action: (self.plan.intervals[action.task][0], action.task, action.worker),
            default=None,
        )

    def _head(self, state, worker):
        """The first task in `worker`'s queue that has not started, or None.

        The tasks skipped by earlier calls are not checked again: in a run that follows the
        plan they stay started. The scan restarts from the front if the last skipped task has
        not started, as in a new run.
        """
        queue = self.plan.queues[worker]
        index = self.skipped[worker]
        if index and not has_started(state, queue[index - 1]):
            index = 0
        while index < len(queue) and has_started(state, queue[index]):
            index += 1
        self.skipped[worker] = index
        return queue[index] if index < len(queue) else None


class ContentionHeft(Heft):
    """HEFT whose plan books each transfer on the links it uses (ContentionHeftPlan)."""

    plan_type = ContentionHeftPlan


def has_started(state, task):
    return task in state.completed or task in state.running


class WorkStealing:
    """Queue each ready task at its data-local worker; a free worker steals it only if sooner.

    Only pending tasks move; running tasks and stored data never do.
    """

    def __init__(self, objective=None):
        self.objective = objective or Objective()
        self.owner = {}
        self.best_placement = {}
        self.graph = None
        self.models = None
        self.last_info = {}

    def choose(self, state):
        if self.graph is not state.graph or self.models != state.models:
            self.best_placement = {}
            self.owner = {}
            self.graph = state.graph
            self.models = state.models
        self.last_info = {"steals": 0}
        ready = state.ready()
        free = state.free_workers()
        if not ready or not free:
            return None
        for task in ready:
            if task not in self.owner:
                self.owner[task] = self._local_worker(state, task)
        queued_finish = self._queued_finish_times(state, ready)
        choices = []
        for task in ready:
            for worker in free:
                action, seconds, remote = self._placement(state, task, worker)
                finish = state.time + seconds
                if worker == self.owner[task] or finish + 1e-9 < queued_finish[task]:
                    choices.append((self.objective.key(finish, remote), task, worker, action))
        if not choices:
            return None
        _, task, worker, action = min(
            choices, key=lambda choice: (choice[0], choice[1], choice[2], choice[3].targets)
        )
        if worker != self.owner[task]:
            self.last_info = {"steals": 1, "victim": self.owner[task], "thief": worker}
            self.owner[task] = worker
        return action

    @staticmethod
    def _local_worker(state, task):
        inputs = state.graph.tasks[task].inputs

        def remote_bytes(worker):
            host = state.hosts[worker]
            return sum(state.resolve(ref).size for ref in inputs if state.location(ref) != host)

        return min(state.workers, key=lambda worker: (remote_bytes(worker), worker))

    def _placement(self, state, task, worker):
        """Best output placement of `task` on `worker`: (action, isolated seconds, remote bytes)."""
        if (task, worker) not in self.best_placement:
            options = []
            for targets in state.target_options(task):
                action = Dispatch(task, worker, targets)
                seconds = state.isolated_seconds(action)
                remote = state.transfer_bytes(action)
                options.append(
                    (self.objective.key(seconds, remote), targets, action, seconds, remote)
                )
            _, _, action, seconds, remote = min(options)
            self.best_placement[task, worker] = (action, seconds, remote)
        return self.best_placement[task, worker]

    def _queued_finish_times(self, state, ready):
        """When each ready task would finish if every queue ran in task order after running work."""
        available = [state.time] * state.node_count
        future = state.clone()
        busy = {flight.action.worker for flight in future.running.values()}
        while future.running:
            future.advance()
            still_busy = {flight.action.worker for flight in future.running.values()}
            for worker in busy - still_busy:
                available[worker] = future.time
            busy = still_busy
        finish = {}
        for task in ready:
            worker = self.owner[task]
            _, seconds, _ = self._placement(state, task, worker)
            available[worker] += seconds
            finish[task] = available[worker]
        return finish
