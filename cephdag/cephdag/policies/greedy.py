"""Earliest-finish greedy policies built directly on the event simulation."""

from ..model import Dispatch, Objective


def forecast_finish(state, action):
    """Simulate `action` from `state` until that task completes; return the future state."""
    future = state.clone()
    future.dispatch(action)
    while action.task not in future.completed:
        future.advance()
    return future


class Greedy:
    """Dispatch now the action whose task is forecast to finish first (work-conserving)."""

    def __init__(self, objective=None):
        self.objective = objective or Objective()

    def choose(self, state):
        best = None
        best_key = None
        for action in state.candidates():
            future = forecast_finish(state, action)
            moved = future.remote_bytes - state.remote_bytes
            key = (
                *self.objective.key(future.time, moved),
                action.task,
                action.worker,
                action.targets,
            )
            if best_key is None or key < best_key:
                best = action
                best_key = key
        return best


class GreedyWait(Greedy):
    """Like Greedy, but also considers busy workers; returns None (wait) if one of them wins."""

    def choose(self, state):
        ready = state.ready(24)
        if not ready or not state.free_workers():
            return None
        best = None
        best_key = None
        wait = False
        for worker in state.workers:
            available = state.clone()
            while worker not in available.free_workers():
                available.advance()
            for task_id in ready:
                for targets in state.target_options(task_id):
                    action = Dispatch(task_id, worker, targets)
                    future = forecast_finish(available, action)
                    moved = available.transfer_bytes(action)
                    key = (*self.objective.key(future.time, moved), task_id, worker, targets)
                    if best_key is None or key < best_key:
                        best = action
                        best_key = key
                        wait = worker not in state.free_workers()
        return None if wait else best
