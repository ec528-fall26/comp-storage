"""Cheap reference policies."""

import random

from ..model import Dispatch, hashed_targets


class RandomPolicy:
    """A uniformly random ready task, free worker and output placement (hashed on a
    role-split cluster)."""

    def __init__(self, seed=1):
        self.random = random.Random(seed)

    def choose(self, state):
        ready = state.ready()
        workers = state.free_workers()
        if not ready or not workers:
            return None
        task = state.graph.tasks[self.random.choice(ready)]
        worker = self.random.choice(workers)
        pools = state.storage_nodes
        if state.role_split:
            targets = hashed_targets(task, pools)
        else:
            targets = tuple(pools[self.random.randrange(len(pools))] for _ in task.output_sizes)
        return Dispatch(task.id, worker, targets)


class SingleNode:
    """Run every task on one node and store every output there; inputs stay where they are."""

    def __init__(self, node=0):
        self.node = node

    def choose(self, state):
        if self.node not in state.workers or self.node not in state.storage_nodes:
            raise ValueError(f"node {self.node} needs both a worker and a pool")
        if self.node not in state.free_workers():
            return None
        ready = state.ready(1)
        if not ready:
            return None
        task = state.graph.tasks[ready[0]]
        return Dispatch(task.id, self.node, (self.node,) * len(task.output_sizes))


class DataLocal:
    """Run where the fewest input bytes must move, keep outputs local, then balance load."""

    def choose(self, state):
        def remote_input_bytes(action):
            inputs = state.graph.tasks[action.task].inputs
            host = state.hosts[action.worker]
            return sum(state.resolve(ref).size for ref in inputs if state.location(ref) != host)

        def key(action):
            return (
                remote_input_bytes(action),
                state.assigned_work[action.worker],
                action.task,
                action.worker,
            )

        return min(state.candidates(all_targets=False), key=key, default=None)


class FifoHash:
    """A conventional system that ignores where data is.

    The oldest ready task (task ids number tasks in the order they were added) goes to the
    free worker with the lowest index, and each output to the pool picked by a stable hash
    of the task id and output index, as CRUSH places objects.
    """

    def choose(self, state):
        ready = state.ready(1)
        workers = state.free_workers()
        if not ready or not workers:
            return None
        task = state.graph.tasks[ready[0]]
        return Dispatch(task.id, workers[0], hashed_targets(task, state.storage_nodes))
