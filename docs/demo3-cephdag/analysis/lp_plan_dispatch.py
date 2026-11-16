"""Where lp-plan loses to heft-cc on the skewed workload, and what a small dispatcher change
would do in the forecast.

    python3 -I lp_plan_dispatch.py SCHED_SKEW_DIR MODELS_JSON [CEPHDAG_DIR]

SCHED_SKEW_DIR is a copy of aakashpc:/mnt/mass/cephdag/campaigns/sched-skew, MODELS_JSON the
lab's /mnt/mass/cephdag/calibration/cephdag8-cs/models.json, CEPHDAG_DIR a checkout of cephdag
`schedulers` (default: ../../cephdag from this file). Nothing in cephdag is changed: the
variants below are subclasses defined here and run only in the in-process forecast.

Part 1 (measured): lp-plan's plan rebuilt in-process (PlacementLp on the run's own graph and
models) against the per-worker counts it executed, and for lp-plan and heft-cc in each repeat:
per-worker finish times, node 0's send link (moves, time with at least one move in flight,
aggregate MB/s, mean concurrent moves) and, for lp-plan, why the link sat idle between moves.
Task i reads shard i; a task reading node 0's shard on another worker is a move off node 0,
whose read stage is the transfer (stages are laid end to end from submit + slack / 2).

Part 2 (forecast): lp-plan and variants in demo3/sched_forecast.py's max-min fair simulation,
with node 0's single-transfer rate capped at what the lab measured for one flow at a time.
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CEPHDAG = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE.parents[1] / "cephdag"
sys.path[:0] = [str(CEPHDAG), str(HERE.parent)]

import numpy as np  # noqa: E402

from cephdag.graph import Dataset, ref_from_dict  # noqa: E402
from cephdag.model import Models  # noqa: E402
from cephdag.policies import lp, make_policy  # noqa: E402
from experiments.run import build_workload  # noqa: E402
import sched_forecast  # noqa: E402

POOLS = tuple(f"node{n}" for n in range(8))
SHARD_MB = 64 * 2**20 / 1e6
SINGLE_FLOW_BPS = 42.5e6


def case_dir(campaign, iterations, repeat):
    return campaign / f"independent-i{iterations}-r{repeat}-cs" / "a1"


def graph_of(directory, iterations):
    config = json.loads((directory / "config.json").read_text())
    shards = tuple(ref_from_dict(shard) for shard in config["dataset"]["shards"])
    graph = build_workload(Dataset(config["dataset"]["name"], shards), "independent", iterations, 0.25)
    return graph, Models.from_dict(config["models"]), config["dataset"]["shards"]


def load(directory, policy, shards):
    path = next(directory.glob(f"run-*-{policy}.json"))
    run = json.loads(path.read_text())
    tasks = []
    for task_id, record in run["tasks"].items():
        result = record["result"]
        slack = record["actual_finish"] - record["submitted"] - result["seconds"]
        tasks.append(
            {
                "source": int(shards[int(task_id[1:])]["pool"].removeprefix("node")),
                "worker": record["worker"],
                "submitted": record["submitted"],
                "finish": record["actual_finish"],
                "start": record["submitted"] + slack / 2,
                "read": sum(s["seconds"] for s in result["stages"] if s["kind"] == "read"),
            }
        )
    return run, tasks


def union(intervals):
    total, current = 0.0, None
    for left, right in sorted(intervals):
        if current is None or left > current[1]:
            total += 0 if current is None else current[1] - current[0]
            current = [left, right]
        else:
            current[1] = max(current[1], right)
    return total + (0 if current is None else current[1] - current[0])


def idle_link(tasks, moves):
    """Seconds node 0's link carried no move between the first and last move, split into time
    when every worker still due a move was running a task, and time when one was free."""
    by_worker = defaultdict(list)
    for task in tasks:
        by_worker[task["worker"]].append(task)

    def free_at(worker, moment):
        for task in sorted(by_worker[worker], key=lambda t: t["submitted"]):
            if task["submitted"] <= moment < task["finish"]:
                moment = task["finish"]
        return moment

    receivers_busy = dispatcher = 0.0
    for before, after in zip(moves, moves[1:]):
        idle_from, idle_to = before["start"] + before["read"], after["start"]
        if idle_to <= idle_from:
            continue
        due = {m["worker"] for m in moves if m["start"] >= idle_to - 1e-6}
        first_free = min((free_at(w, idle_from) for w in due), default=idle_to)
        first_free = min(max(first_free, idle_from), idle_to)
        receivers_busy += first_free - idle_from
        dispatcher += idle_to - first_free
    return receivers_busy, dispatcher


def measured(run, tasks):
    moves = sorted((t for t in tasks if t["source"] == 0 and t["worker"] != 0), key=lambda t: t["start"])
    counts = np.zeros((8, 8), dtype=int)
    for task in tasks:
        counts[task["source"], task["worker"]] += 1
    last = [max(t["finish"] for t in tasks if t["worker"] == w) for w in range(8)]
    active = union((m["start"], m["start"] + m["read"]) for m in moves)
    pending = sum(m["start"] > last[0] for m in moves)
    return {
        "wall": run["wall_seconds"],
        "counts": counts,
        "last": last,
        "moves": len(moves),
        "other_moves": int(counts.sum() - np.trace(counts)) - len(moves),
        "first_move": moves[0]["start"] if moves else None,
        "link_active": active,
        "link_mb_s": len(moves) * SHARD_MB / active if active else 0.0,
        "concurrency": sum(m["read"] for m in moves) / active if active else 0.0,
        "move_read_s": statistics.median(m["read"] for m in moves) if moves else 0.0,
        "moves_after_n0_done": pending,
        "idle_link": idle_link(tasks, moves) if moves else (0.0, 0.0),
    }


class TakeBack(lp.LpPlan):
    """lp-plan, but a worker whose own plan is done runs a still unstarted task of its own
    node's class that the plan gave to another worker (the source node takes a move back)."""

    def choose(self, state):
        action = super().choose(state)
        if action is not None or not state.ready():
            return action
        plan = self.plan
        for worker in state.free_workers():
            column = plan.workers.index(worker)
            if self.started[:, column].sum() < plan.counts[:, column].sum():
                continue
            for task_id in state.ready():
                index = plan.class_of.get(task_id)
                if index is None or lp.remote_input_bytes(state, task_id, worker):
                    continue
                left = plan.counts[index] - self.started[index]
                if left.max() <= 0:
                    continue
                donor = int(np.argmax(left))
                plan.counts[index, donor] -= 1
                plan.counts[index, column] += 1
                return self._dispatch(state, task_id, worker)
        return None


class WaitForLink(lp.LpPlan):
    """lp-plan, but a worker whose most-lagging class is a move whose link is busy waits for
    the link instead of starting a local task."""

    def _planned(self, state, worker, ready_by_class):
        column = self.plan.workers.index(worker)
        planned = self.plan.counts[:, column]
        started = self.started[:, column]
        position = (started.sum() + 1) / max(1, planned.sum())
        best = None
        for index, tasks in ready_by_class.items():
            if index is None or started[index] >= planned[index]:
                continue
            lag = planned[index] * position - started[index]
            if best is None or lag > best[0]:
                best = (lag, tasks[0])
        if best is None:
            return None
        task_id = best[1]
        if lp.remote_input_bytes(state, task_id, worker) and self._link_busy(state, task_id):
            return None
        return task_id


class Both(TakeBack, WaitForLink):
    pass


class LabSimulation(sched_forecast.FairSimulation):
    """Max-min fair sharing, and a transfer alone on node 0's send link runs at the rate the lab
    measured for one flow at a time (two or more together fill the link)."""

    def advance(self):
        self._complete_finished()
        if not self.running:
            if self.done:
                return 0.0
            raise RuntimeError("cannot wait without a running task")
        uses = {task: flight.stages[0].resources for task, flight in self.running.items()}
        alone = [task for task, names in uses.items() if "tx:0" in names]
        capacity = dict(self.capacity)
        if len(alone) == 1:
            capacity["flow"] = SINGLE_FLOW_BPS
            uses[alone[0]] = (*uses[alone[0]], "flow")
        rates = sched_forecast.fair_rates(uses, capacity)
        step = min(flight.stages[0].remaining / rates[task] for task, flight in self.running.items())
        for task, flight in self.running.items():
            flight.stages[0].remaining = max(0, flight.stages[0].remaining - step * rates[task])
        self.time += step
        self._complete_finished()
        return step


VARIANTS = {"lp-plan": lp.LpPlan, "take-back": TakeBack, "wait-for-link": WaitForLink, "both": Both}


def forecast(policy, graph, models, simulation):
    state = simulation(graph, models, POOLS)
    while not state.done:
        action = policy.choose(state)
        if action is None:
            state.advance()
        else:
            state.dispatch(action)
    return state.time, state.remote_bytes / 1e9


def main():
    campaign = Path(sys.argv[1])
    lab_models = Models.from_dict(json.loads(Path(sys.argv[2]).read_text()))
    print("## Measured, sched-skew (3 repeats)\n")
    for iterations in (1, 16, 128, 1024):
        graph, models, shards = graph_of(case_dir(campaign, iterations, 0), iterations)
        if models != lab_models:
            raise SystemExit("run models differ from MODELS_JSON")
        plan = lp.PlacementLp(sched_forecast.Simulation(graph, models, POOLS))
        print(
            f"i{iterations}: LP T {plan.makespan:.1f} s; planned tasks from node 0 per worker "
            f"{plan.counts[0].tolist()}; planned link busy {plan.counts[0, 1:].sum() * SHARD_MB / 47.0875:.1f} s\n"
        )
        print("| policy | repeat | wall s | from node 0 per worker | other moves | worker last finish s | first move s | n0 link active s | MB/s | moves in flight | move read s | idle link: receivers busy / one free, s | moves started after n0 done |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for repeat in range(3):
            directory = case_dir(campaign, iterations, repeat)
            for policy in ("lp-plan", "heft-cc"):
                m = measured(*load(directory, policy, shards))
                if policy == "lp-plan" and not (m["counts"][0] == plan.counts[0]).all():
                    print(f"(executed counts differ from the plan: {m['counts'][0].tolist()})")
                print(
                    f"| {policy} | {repeat} | {m['wall']:.1f} | {m['counts'][0].tolist()} | {m['other_moves']} | "
                    f"{', '.join(f'{v:.0f}' for v in m['last'])} | {m['first_move']:.1f} | {m['link_active']:.1f} | "
                    f"{m['link_mb_s']:.1f} | {m['concurrency']:.2f} | {m['move_read_s']:.2f} | "
                    f"{m['idle_link'][0]:.1f} / {m['idle_link'][1]:.1f} | {m['moves_after_n0_done']} |"
                )
        print()

    print("## Forecast, lab models, max-min fair sharing\n")
    print(f"'lab link' caps a move alone on node 0's link at {SINGLE_FLOW_BPS / 1e6:.1f} MB/s, as measured.\n")
    print("| case | sharing | " + " | ".join(VARIANTS) + " | heft-cc |")
    print("|---|---|" + "---|" * (len(VARIANTS) + 1))
    for iterations in (1, 16, 128, 1024):
        graph, models, _ = graph_of(case_dir(campaign, iterations, 0), iterations)
        for name, simulation in (("fair", sched_forecast.FairSimulation), ("lab link", LabSimulation)):
            cells = []
            for variant in VARIANTS.values():
                wall, moved = forecast(variant(), graph, models, simulation)
                cells.append(f"{wall:.1f} s, {moved:.2f} GB")
            wall, moved = forecast(make_policy("heft-cc"), graph, models, simulation)
            cells.append(f"{wall:.1f} s, {moved:.2f} GB")
            print(f"| i{iterations} | {name} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
