"""beam-4 against beam-fair-4 decision by decision, on beam-4's own trajectory in the fair world.

    cd <cephdag gumbel checkout> && .venv/bin/python -I ../../demo3/gumbel-eval/beam_fair_decisions.py \\
        MODELS_JSON

On the 8 GiB skew graph at i1, i128 and i1024, beam-4 runs in demo3/sched_forecast.py's
FairSimulation; at every state with a ready task and a free worker beam-fair-4 is asked too (no
time cut for either). Prints how many decisions there were and how many differ, with the first.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cephdag.model import Models  # noqa: E402
from cephdag.policies import make_policy  # noqa: E402
from tests.test_fair_forecast import POOLS, SKEW, lab_graph  # noqa: E402

from sched_forecast import FairSimulation  # noqa: E402


def compare(graph, models):
    state = FairSimulation(graph, models, POOLS)
    equal = make_policy("beam-4", budget_ms=1e9)
    fair = make_policy("beam-fair-4", budget_ms=1e9)
    decisions = 0
    differing = []
    while not state.done:
        action = equal.choose(state)
        if state.ready(1) and state.free_workers():
            decisions += 1
            other = fair.choose(state)
            if other != action:
                differing.append((state.time, action, other))
        if action is None:
            state.advance()
        else:
            state.dispatch(action)
    return decisions, differing


def main():
    models = Models.from_dict(json.loads(Path(sys.argv[1]).read_text()))
    for iterations in (1, 128, 1024):
        decisions, differing = compare(lab_graph(SKEW, 8, iterations), models)
        first = differing[0] if differing else None
        print(f"i{iterations} decisions {decisions} differ {len(differing)} first {first}")


if __name__ == "__main__":
    main()
