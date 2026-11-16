"""Time one fluid-LP build and solve per case under the calibrated model. Usage: .venv/bin/python -I time_lp.py MODELS_JSON"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headroom  # noqa: E402

models = json.loads(Path(sys.argv[1]).read_text())
for name, case in (('independent', headroom.independent_case()), ('competing', headroom.competing_case())):
    start = time.perf_counter()
    seconds, _, _ = headroom.lower_bound(*case, headroom.Stage(models))
    print(f'{name}: bound {seconds:.2f} s, build+solve {time.perf_counter() - start:.3f} s (scipy HiGHS, unoptimized build)')
