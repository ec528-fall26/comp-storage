"""Drain-time-aware locality: move work off a node only while its backlog outlasts the move."""

from ..model import Dispatch, hashed_targets


def require_own_hosts(state, policy):
    """Refuse a cluster whose workers share hosts (lab modes cs2 and disagg2): `policy` keeps
    one queue or plan per node and would treat a host's second worker as remote."""
    if len(set(state.hosts)) < state.node_count:
        raise ValueError(f"{policy} needs every worker on a host of its own")


def local_targets(state, task_id, worker):
    """Outputs in the worker's own pool, or where the storage system hashes them on a
    role-split cluster."""
    task = state.graph.tasks[task_id]
    if state.role_split:
        return hashed_targets(task, state.storage_nodes)
    return (worker,) * len(task.output_sizes)


class Backlog:
    """A free worker runs its own ready local tasks first. Otherwise it takes a task whose
    data sits on node s only if s's backlog outlasts moving the task:

        backlog(s) = slowdown(s) * sum of model seconds of the tasks queued on s
                   > (bytes already queued on s's send link) / (s's send rate)
                     + model seconds of the task on this worker, input read over the link

    slowdown(s) is how much longer than the model the local tasks finished on s have
    actually taken (1 before any has). A node without a worker has an endless backlog.
    Of the moves that pass, the one with the largest margin goes first.
    """

    def __init__(self):
        self.graph = None
        self.last_info = {}

    def choose(self, state):
        if self.graph is not state.graph:
            require_own_hosts(state, "backlog")
            self.graph = state.graph
            self.home = {}
            self.model_seconds = {}
            self.started = {}
            self.measured = {node: 0.0 for node in range(state.node_count)}
            self.modelled = {node: 0.0 for node in range(state.node_count)}
        self.last_info = {}
        self._measure(state)
        free = state.free_workers()
        if not free:
            return None
        queues = {}
        for task_id in state.ready():
            queues.setdefault(self._home(state, task_id), []).append(task_id)
        for worker in free:
            if queues.get(worker):
                return self._dispatch(state, queues[worker][0], worker)
        best = None
        for source, queue in queues.items():
            backlog = self._backlog(state, source, queue)
            task_id = queue[-1]
            for worker in free:
                margin = backlog - self._moved_finish(state, task_id, worker)
                if margin > 0 and (best is None or margin > best[0]):
                    best = (margin, task_id, worker)
        if best is None:
            return None
        margin, task_id, worker = best
        self.last_info = {"moved_from": self.home[task_id], "margin_seconds": margin}
        return self._dispatch(state, task_id, worker)

    def _home(self, state, task_id):
        """The node that stores the most input bytes of the task."""
        if task_id not in self.home:
            stored = {}
            for ref in state.graph.tasks[task_id].inputs:
                node = state.location(ref)
                stored[node] = stored.get(node, 0) + state.resolve(ref).size
            self.home[task_id] = min(stored, key=lambda node: (-stored[node], node))
        return self.home[task_id]

    def _seconds_at_home(self, state, task_id):
        if task_id not in self.model_seconds:
            home = self.home[task_id]
            action = Dispatch(task_id, home, local_targets(state, task_id, home))
            self.model_seconds[task_id] = state.isolated_seconds(action)
        return self.model_seconds[task_id]

    def _backlog(self, state, source, queue):
        if source not in state.workers:
            return float("inf")
        modelled = sum(self._seconds_at_home(state, task_id) for task_id in queue)
        if self.modelled[source] > 0:
            return modelled * self.measured[source] / self.modelled[source]
        return modelled

    def _moved_finish(self, state, task_id, worker):
        """Seconds until the task would finish on `worker`: the transfers already queued on
        each send link it reads from, then the task itself."""
        action = Dispatch(task_id, worker, local_targets(state, task_id, worker))
        senders = {state.location(ref) for ref in state.graph.tasks[task_id].inputs} - {worker}
        queued = sum(
            self._queued_bytes(state, f"tx:{sender}") / state.capacity[f"tx:{sender}"]
            for sender in senders
        )
        return queued + state.isolated_seconds(action)

    @staticmethod
    def _queued_bytes(state, link):
        return sum(
            stage.remaining
            for flight in state.running.values()
            for stage in flight.stages
            if link in stage.resources
        )

    def _measure(self, state):
        """Add the local tasks finished since the last call to their node's measured and
        modelled seconds."""
        for task_id, (worker, started_at) in list(self.started.items()):
            if task_id in state.completed:
                del self.started[task_id]
                if self.home[task_id] == worker:
                    self.measured[worker] += state.time - started_at
                    self.modelled[worker] += self._seconds_at_home(state, task_id)

    def _dispatch(self, state, task_id, worker):
        self.started[task_id] = (worker, state.time)
        return Dispatch(task_id, worker, local_targets(state, task_id, worker))
