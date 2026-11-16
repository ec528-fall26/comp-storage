"""Run the pre-registered grid of demo3/gumbel-tuning-plan.md and print every configuration's score.

cd <cephdag gumbel checkout> && .venv/bin/python -I ../../demo3/gumbel_tune.py MODELS OUT_JSONL
"""

from concurrent.futures import ThreadPoolExecutor
import itertools
import json
from pathlib import Path
import statistics
import subprocess
import sys

CASES = ("hot5-i1", "hot5-i64", "hot5-i1024", "uniform8-i256")
SEEDS = (1, 2)
EVAL = Path(__file__).resolve().parent / "gumbel_eval.py"


def grid():
    for sampled, below, leaf, temperature in itertools.product(
        (8, 16), ("uct", "gumbel"), ("tail", "bound"), (1, 0.1)
    ):
        yield (
            "gumbel-32",
            {
                "sampled": sampled,
                "below": below,
                "leaf": leaf,
                "temperature": temperature,
            },
        )
    for leaf, temperature in itertools.product(
        ("tail", "bound"), (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8)
    ):
        yield "restarts-32", {"leaf": leaf, "temperature": temperature}


def label(policy, options):
    return policy + ":" + ",".join(f"{key}={value}" for key, value in sorted(options.items()))


def run(models, output, policy, options, seed):
    options = {**options, "seed": seed, "budget_ms": 1e9}
    command = [
        sys.executable,
        "-I",
        str(EVAL),
        models,
        output,
        "--cases",
        ",".join(CASES),
        "--policies",
        policy,
        "--options",
        json.dumps(options),
        "--label",
        f"{label(policy, options)}",
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)


def main():
    models, output = sys.argv[1], sys.argv[2]
    subprocess.run(
        [sys.executable, "-I", str(EVAL), models, output, "--cases", ",".join(CASES)]
        + ["--policies", "data-local", "--label", "reference"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    jobs = [(policy, options, seed) for policy, options in grid() for seed in SEEDS]
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda job: run(models, output, *job), jobs))
    rows = [json.loads(line) for line in Path(output).read_text().splitlines()]
    reference = {row["case"]: row["wall_seconds"] for row in rows if row["label"] == "reference"}
    scores = []
    for policy, options in grid():
        mine = [
            row
            for row in rows
            if row["policy"] == policy
            and {key: row["options"][key] for key in options} == options
            and row["label"] != "reference"
        ]
        assert len(mine) == len(CASES) * len(SEEDS), (policy, options, len(mine))
        ratio = statistics.mean(row["wall_seconds"] / reference[row["case"]] for row in mine)
        transitions = statistics.mean(row["simulated_transitions"] for row in mine)
        by_case = {
            case: statistics.mean(row["wall_seconds"] for row in mine if row["case"] == case)
            for case in CASES
        }
        scores.append((ratio, transitions, label(policy, options), by_case))
    for policy in ("gumbel-32", "restarts-32"):
        print(policy)
        for ratio, transitions, name, by_case in sorted(
            s for s in scores if s[2].startswith(policy)
        ):
            walls = " ".join(f"{case}={wall:.2f}" for case, wall in by_case.items())
            print(f"  {ratio:.4f} {transitions:9.0f} {name} {walls}")


if __name__ == "__main__":
    main()
