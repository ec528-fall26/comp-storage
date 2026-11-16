"""The fluid LP lower bound of each forecast case, solved by lp-plan's simplex and by HiGHS.

    cd cephdag && VENV_WITH_SCIPY/bin/python -I ../demo3/sched_lp_bound.py MODELS_JSON

Needs a Python with numpy 2.0.2 and a scipy that imports with it (1.13.1); the lab's scipy
1.9.3 does not. Prints, per case, the LP makespan and bytes moved from cephdag's
PlacementLp, the same LP solved by scipy.optimize.linprog (HiGHS), and both solve times.
"""

import json
from pathlib import Path
import sys
import time

import numpy as np
from scipy.optimize import linprog

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path.cwd()))

from cephdag.model import Models, Simulation  # noqa: E402
from cephdag.policies import lp  # noqa: E402
import sched_forecast  # noqa: E402

SIMPLEX = lp.minimize


def highs(cost, upper, upper_bounds, equal, equal_bounds):
    result = linprog(cost, upper, upper_bounds, equal, equal_bounds, method="highs")
    if result.status != 0:
        raise ValueError(result.message)
    return result.x


def solved(state, solver):
    lp.minimize = solver
    try:
        return lp.PlacementLp(state)
    finally:
        lp.minimize = SIMPLEX


def main():
    models = Models.from_dict(json.loads(Path(sys.argv[1]).read_text()))
    for case in sched_forecast.CASES:
        state = Simulation(sched_forecast.graph_for(case), models, sched_forecast.POOLS)
        ours = solved(state, SIMPLEX)
        theirs = solved(state, highs)
        started = time.perf_counter()
        solved(state, highs)
        record = {
            "case": case,
            "classes": len(ours.classes),
            "makespan_seconds": round(float(ours.makespan), 3),
            "highs_makespan_seconds": round(float(theirs.makespan), 3),
            "moved_gb": round(ours.moved_bytes / 1e9, 3),
            "highs_moved_gb": round(theirs.moved_bytes / 1e9, 3),
            "moved_off_node0_tasks": int(ours.counts[0].sum() - ours.counts[0, 0]),
            "simplex_solve_ms": round(ours.solve_seconds * 1000, 1),
            "highs_solve_ms": round(theirs.solve_seconds * 1000, 1),
            "build_ms": round(ours.build_seconds * 1000, 1),
            "highs_total_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        assert np.isclose(ours.makespan, theirs.makespan, rtol=1e-6), record
        print(json.dumps(record), flush=True)


if __name__ == "__main__":
    main()
