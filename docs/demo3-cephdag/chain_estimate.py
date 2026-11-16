"""Hours each demo3 campaign should take, from measured pilot walls, on the lab host.

    python3 chain_estimate.py CLONE RESULTS_HOST SPEC [SPEC ...]

A run step costs, for each of its policies, the pilot wall of that mode, policy and
intensity scaled linearly to the step's source bytes (log-log interpolation between piloted
intensities, linear extrapolation in iterations beyond the last), plus the final-output
verification read and a fixed per-run overhead (PILOTS.md, 'Measured costs'). A session
starts with a full verify and repeats it at the first mode switch 6 h later; every other
mode change costs a light switch. Prints each run step's seconds and each campaign's hours.

disagg2 and cs2 (two workers per container) and fused DROID have no pilot: they are priced
at the disagg, cs and unfused rates, an upper bound if a second slot and fusion only help. The
scheduling policies heft-cc, backlog and lp-plan are priced at heft's rate, the slowest cs
policy at every skew and uniform intensity measured, an upper bound if they do no worse than
heft, unless the pilot campaign ran them (sched-skew, which prices sched-lp, did). lp-plan2
has no pilot: it is priced at lp-plan's rate, an upper bound if it does no worse than lp-plan.
native-mcts-heuristic-128 has no cephdag8 pilot either: it is priced at heft's rate, above its
fair-sharing forecast at i1, i128 and i1024 (heft measured 45.5/56.4/108.1 s on sched-skew,
MCTS forecast 26.6/49.5/95.5 s). sched-gumbel's searches (native-mcts-fair-128, gumbel-lb-32,
restarts-32) are priced at heft's rate too, above their in-process forecasts (demo3/GUMBEL.md);
beam-fair-4 at beam-4's, whose decisions it shares in 99% of the forecast cases.
A policy's price follows PILOTED_POLICY until a policy the pilot ran: sched-droid's lp-plan2
goes to lp-plan and then to heft, since pilot-droid ran neither. sched-droid (fused,
node 0 holding 9 of 16 partitions) is priced at pilot-droid's unfused, evenly placed rates:
cs heft 15.2 s/GB, above the skewed fused forecasts of heft, heft-cc (with its 17 s plan) and
lp-plan2 (10-13 s/GB, demo3/sched-droid/check.json); cs data-local 10.8 s/GB, about its
forecast (10.7-11.4); disagg fifo-hash 49.7 s/GB, 4x the fused rate measured on 3 shards.
"""

import json
import math
import sys
from pathlib import Path

GIB = 2**30
VERIFY_READ_BPS = 50e6
RUN_OVERHEAD_SECONDS = {"synthetic": 2, "droid": 17}
SWITCH_SECONDS = 7
FULL_VERIFY_SECONDS = {"cs": 200, "disagg": 380}
VERIFY_INTERVAL_SECONDS = 6 * 3600
CREATE_BPS = 0.37e9
INGEST_BPS = 132e6
DELETE_SECONDS = 30
DROID_OUTPUT_PER_SOURCE = 2.57 / 3.61

PILOTS = {
    "demo3-sweep": [("pilot2-sweep", None)],
    "demo3-scale": [("pilot2-sweep", None)],
    "demo3-skew": [("pilot-skew", {1}), ("pilot2-skew", None)],
    "demo3-droid": [("pilot-droid", None)],
    "demo3-sensitivity-sweep": [("pilot2-sweep", None)],
    "demo3-sensitivity-droid": [("pilot-droid", None)],
    "demo3-overlap-sweep": [("demo3-sweep", None)],
    "demo3-slots": [("demo3-sweep", None)],
    "demo3-slots-droid": [("pilot-droid", None)],
    "sched-pilot": [("demo3-skew", None)],
    "sched-skew": [("demo3-skew", None)],
    "sched-uniform": [("demo3-sweep", None)],
    "sched-lp": [("sched-skew", None)],
    "sched-mcts": [("sched-skew", None)],
    "sched-gumbel": [("sched-skew", None)],
    "sched-droid": [("pilot-droid", None)],
    "sched-lp-low": [("sched-skew", None)],
    "sched-lp-uniform": [("sched-uniform", None)],
}
PILOTED_MODE = {"disagg2": "disagg", "cs2": "cs"}
PILOTED_POLICY = {
    "heft-cc": "heft",
    "backlog": "heft",
    "lp-plan": "heft",
    "lp-plan2": "lp-plan",
    "native-mcts-heuristic-128": "heft",
    "native-mcts-fair-128": "heft",
    "gumbel-lb-32": "heft",
    "restarts-32": "heft",
    "beam-fair-4": "beam-4",
}


def pilot_rates(results, pilots):
    """{(mode, policy): {iterations: wall seconds per source byte}} of the pilot rows."""
    rates = {}
    for campaign, kept in pilots:
        directory = Path(results) / "campaigns" / campaign
        for line in (directory / "ledger.jsonl").read_text().splitlines():
            event = json.loads(line)
            if event["event"] != "completed" or "mode" not in event:
                continue
            output = Path(results) / Path(event["output"]).relative_to("/results")
            for row_line in (output / "rows.jsonl").read_text().splitlines():
                row = json.loads(row_line)
                if kept is not None and row["iterations"] not in kept:
                    continue
                per_byte = row["wall_seconds"] / row["dataset_bytes"]
                rates.setdefault((row["mode"], row["policy"]), {})[row["iterations"]] = per_byte
    return rates


def piloted_points(rates, mode, policy):
    """The pilot points of `policy`, or of the policy it is priced at, followed until one ran."""
    while (mode, policy) not in rates:
        policy = PILOTED_POLICY[policy]
    return rates[(mode, policy)]


def interpolated(points, iterations):
    """Seconds per byte at `iterations` from {iterations: seconds per byte}."""
    if iterations in points:
        return points[iterations]
    known = sorted(points)
    if iterations < known[0]:
        return points[known[0]]
    if iterations > known[-1]:
        low, high = known[-2], known[-1]
        slope = (points[high] - points[low]) / (high - low)
        return points[high] + slope * (iterations - high)
    high = next(value for value in known if value > iterations)
    low = max(value for value in known if value < iterations)
    fraction = math.log(iterations / low) / math.log(high / low)
    return math.exp(
        math.log(points[low]) + fraction * (math.log(points[high]) - math.log(points[low]))
    )


def source_bytes(campaign, spec, step):
    if spec["kind"] == "droid":
        return campaign.droid_wave_bytes(spec, *step["shards"])
    return step["workload"]["gib"] * GIB


def output_bytes(spec, step, source):
    if spec["kind"] == "droid":
        return source * DROID_OUTPUT_PER_SOURCE
    return source * step["workload"]["ratio"]


def run_seconds(campaign, spec, step, rates):
    source = source_bytes(campaign, spec, step)
    per_run = (
        output_bytes(spec, step, source) / VERIFY_READ_BPS + RUN_OVERHEAD_SECONDS[spec["kind"]]
    )
    total = 0.0
    for policy in spec["modes"][step["mode"]]["policies"]:
        mode = PILOTED_MODE.get(step["mode"], step["mode"])
        points = piloted_points(rates, mode, policy)
        total += interpolated(points, step.get("iterations")) * source + per_run
    return total


def other_seconds(campaign, spec, step):
    if step["kind"] == "bulk" and spec["kind"] == "droid":
        return campaign.droid_wave_bytes(spec, *step["shards"]) / INGEST_BPS
    if step["kind"] == "bulk":
        return spec["synthetic"]["resident_gib"] * GIB / CREATE_BPS
    return DELETE_SECONDS


def campaign_seconds(campaign, spec, rates, say):
    elapsed = 0.0
    verified_at = None
    mode = None
    for step in campaign.steps_of(spec):
        if step["kind"] != "run":
            elapsed += other_seconds(campaign, spec, step)
            continue
        if step["mode"] != mode:
            mode = step["mode"]
            elapsed += SWITCH_SECONDS
            if verified_at is None or elapsed - verified_at >= VERIFY_INTERVAL_SECONDS:
                elapsed += FULL_VERIFY_SECONDS[PILOTED_MODE.get(mode, mode)]
                verified_at = elapsed
        seconds = run_seconds(campaign, spec, step, rates)
        elapsed += seconds
        say(f"{spec['name']} {step['id']} {seconds:.0f} s, ends at {elapsed / 3600:.2f} h")
    return elapsed


def main():
    clone, results, *specs = sys.argv[1:]
    sys.path.insert(0, str(Path(clone) / "experiments"))
    import campaign

    total = 0.0
    for path in specs:
        spec = json.loads(Path(path).read_text())
        rates = pilot_rates(results, PILOTS[spec["name"]])
        seconds = campaign_seconds(campaign, spec, rates, print)
        total += seconds
        print(f"== {spec['name']}: {seconds / 3600:.2f} h; chain so far {total / 3600:.2f} h")


if __name__ == "__main__":
    main()
