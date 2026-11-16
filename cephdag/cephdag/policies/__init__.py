"""Scheduling policies.

A policy has one method, `choose(state)`, called by the coordinator at every event with a
model.Simulation of the live cluster. It returns a model.Dispatch, or None to wait for
the next event (a stage or task finishing).
"""

import re

from .backlog import Backlog
from .baselines import DataLocal, FifoHash, RandomPolicy, SingleNode
from .greedy import Greedy, GreedyWait
from .list import ContentionHeft, Heft, WorkStealing
from .lp import LpPlan, LpPlan2

FIXED = {
    "random": RandomPolicy,
    "single-node": SingleNode,
    "data-local": DataLocal,
    "fifo-hash": FifoHash,
    "greedy": Greedy,
    "greedy-wait": GreedyWait,
    "heft": Heft,
    "heft-cc": ContentionHeft,
    "stealing": WorkStealing,
    "backlog": Backlog,
    "lp-plan": LpPlan,
    "lp-plan2": LpPlan2,
}
NAMES = (
    *FIXED,
    "beam-<width>",
    "native-mcts-heuristic-<iterations>",
    "native-mcts-uniform-<iterations>",
)


def make_policy(name, **options):
    """Build a policy from its name, e.g. 'heft', 'beam-4' or 'native-mcts-heuristic-128'."""
    if name in FIXED:
        return FIXED[name](**options)
    beam = re.fullmatch(r"beam-(\d+)", name)
    if beam:
        from .native import Beam

        return Beam(width=int(beam[1]), **options)
    mcts = re.fullmatch(r"native-mcts-(heuristic|uniform)-(\d+)", name)
    if mcts:
        from .native import NativeMcts

        return NativeMcts(iterations=int(mcts[2]), rollout_policy=mcts[1], **options)
    raise ValueError(f"unknown scheduler: {name}; choose one of {', '.join(NAMES)}")
