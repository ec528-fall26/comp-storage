"""Actual over modelled compute-stage time of every task of synthetic runs, per run.

    python3 compute_ratio.py MODELS_JSON RUN_DIR [RUN_DIR ...]

Each RUN_DIR holds experiments.run output (run-NNN-<policy>.json). The model is the
builtin map@1 cost of MODELS_JSON: fixed + input bytes x per-input + input bytes x
iterations x per-iteration + output bytes x per-output. Prints one JSON line per run.
"""

import json
from pathlib import Path
import statistics
import sys


def modelled_seconds(compute, input_bytes, iterations, output_bytes):
    return (
        compute["fixed_seconds"]
        + input_bytes * compute["input_seconds_per_byte"]
        + input_bytes * iterations * compute["iteration_seconds_per_byte"]
        + output_bytes * compute["output_seconds_per_byte"]
    )


def stage_total(stages, kind):
    return sum(stage["seconds"] for stage in stages if stage["kind"] == kind)


def main():
    compute = json.loads(Path(sys.argv[1]).read_text())["compute"]
    for directory in sys.argv[2:]:
        for path in sorted(Path(directory).glob("run-*.json")):
            run = json.loads(path.read_text())
            iterations = run["case"]["iterations"]
            ratios = []
            for task in run["tasks"].values():
                stages = task["result"]["stages"]
                input_bytes = sum(stage["bytes"] for stage in stages if stage["kind"] == "read")
                output_bytes = sum(output["size"] for output in task["result"]["outputs"])
                model = modelled_seconds(compute, input_bytes, iterations, output_bytes)
                ratios.append(stage_total(stages, "compute") / model)
            print(
                json.dumps(
                    {
                        "run": f"{Path(directory).name}/{path.name}",
                        "iterations": iterations,
                        "tasks": len(ratios),
                        "median": round(statistics.median(ratios), 3),
                        "min": round(min(ratios), 3),
                        "max": round(max(ratios), 3),
                    }
                )
            )


if __name__ == "__main__":
    main()
