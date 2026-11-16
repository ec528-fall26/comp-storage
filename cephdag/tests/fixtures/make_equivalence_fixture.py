"""Record decisions of the ORIGINAL refinement-v2 code for the equivalence test.

Run with the frozen original on the path, never the new package:

    .venv/bin/python -I -B tests/fixtures/make_equivalence_fixture.py \
        --original ~/.hermes/experiments/computational-storage-refinement-20261103/project \
        --native-library <libcephdag_native.so built from the original native/ source> \
        --output tests/fixtures

-B keeps Python from writing bytecode into the frozen source tree.
"""

import argparse
import copy
import ctypes
import hashlib
import json
from pathlib import Path
import random
import sys
import threading
import types

FUNCTIONS = ("map@1", "join@1", "split@1")
RATIOS = (0, 0.125, 0.25, 0.5, 1, 2)
ITERATIONS = (0, 1, 4, 16, 64)
STATE_COUNT = 300
EPISODE_COUNT = 40
LIVE_COUNT = 60


def redirect_native_library(path):
    real = ctypes.CDLL

    def load(name, *args, **kwargs):
        if "libcephdag_native" in str(name):
            name = path
        return real(name, *args, **kwargs)

    ctypes.CDLL = load


def random_models(rng, original):
    if rng.random() < 0.4:
        return original.Models()
    compute = original.ComputeModel(
        rng.uniform(0.001, 0.05),
        rng.uniform(1e-10, 5e-9),
        rng.uniform(1e-11, 1e-9),
        rng.uniform(1e-10, 5e-9),
    )
    storage = original.StorageModel(
        tuple(rng.uniform(100e6, 600e6) for _ in range(3)),
        tuple(rng.uniform(50e6, 300e6) for _ in range(3)),
    )
    if rng.random() < 0.5:
        rate = rng.uniform(5e6, 60e6)
        network = original.NetworkModel((rate,) * 3, (rate,) * 3)
    else:
        network = original.NetworkModel(
            tuple(rng.uniform(5e6, 60e6) for _ in range(3)),
            tuple(rng.uniform(5e6, 60e6) for _ in range(3)),
        )
    return original.Models(compute, storage, network, True, "fixture-%d" % rng.randrange(10**6))


def random_size(rng):
    if rng.random() < 0.1:
        return 0
    return 4 * rng.randint(1, 2 * 2**20)


def random_graph(rng, original, seed):
    graph = original.Graph(graph_id="%032x" % seed)
    shape = rng.choice(("mixed", "mixed", "wide", "layers"))
    source_count = rng.randint(1, 6) if shape != "wide" else rng.randint(20, 40)
    sources = [
        original.ObjectRef(
            "node%d" % rng.randrange(3), "data/%d" % i, random_size(rng), partition=i
        )
        for i in range(source_count)
    ]
    available = list(sources)
    if shape == "wide":
        for source in sources:
            graph.add(
                "map@1", [source], ratios=[rng.choice(RATIOS)], iterations=rng.choice(ITERATIONS)
            )
        return graph
    task_count = rng.randint(1, 14) if shape == "mixed" else rng.randint(6, 24)
    attempts = 0
    while len(graph.tasks) < task_count and attempts < 200:
        attempts += 1
        function = rng.choice(FUNCTIONS)
        if shape == "layers":
            pool = available[-6:]
        else:
            pool = available
        inputs = (
            [rng.choice(pool)] if function != "join@1" else [rng.choice(pool), rng.choice(pool)]
        )
        outputs = 2 if function == "split@1" else 1
        ratios = [rng.choice(RATIOS) for _ in range(outputs)]
        try:
            refs = graph.add(function, inputs, ratios=ratios, iterations=rng.choice(ITERATIONS))
        except ValueError:
            continue
        available.extend(refs)
    return graph


def random_progress(rng, original, state):
    steps = rng.randint(0, 2 * len(state.graph.tasks) + 2)
    for _ in range(steps):
        ready = state.ready()
        workers = state.free_workers()
        if ready and workers and (not state.running or rng.random() < 0.7):
            tid = rng.choice(ready)
            task = state.graph.tasks[tid]
            targets = tuple(rng.randrange(3) for _ in task.output_sizes)
            state.dispatch(original.Dispatch(tid, rng.choice(workers), targets))
        elif state.running:
            state.advance()
        else:
            break


def perturb(rng, original, state):
    if state.running and rng.random() < 0.5:
        flight = rng.choice(list(state.running.values()))
        flight.stages[0].remaining *= rng.uniform(0.05, 1.0)
    if state.completed and rng.random() < 0.2:
        tid = rng.choice(sorted(state.completed))
        refs = []
        for ref in state.completed[tid]:
            observed = 4 * rng.randint(0, max(1, ref.size // 2))
            refs.append(
                original.ObjectRef(
                    ref.pool, "observed/" + ref.key, observed, ref.dtype, ref.partition
                )
            )
        state.completed[tid] = tuple(refs)


def random_jobs(rng, state, now):
    pending = {tid: flight.action for tid, flight in state.running.items()}
    jobs = {}
    history = dict(pending)
    for tid, action in pending.items():
        stage_count = len(state.stages_for(action))
        index = rng.randrange(stage_count + 1)
        started = now - rng.uniform(0.0, 3.0)
        events = []
        moment = started - rng.uniform(0.0, 4.0)
        for earlier in range(index):
            end = moment + rng.uniform(0.0, 1.0)
            events.append({"index": earlier, "start": moment, "end": end})
            moment = end
        events.append({"index": index, "start": started, "end": None})
        job = {"stage_index": index, "stage_started": started}
        if rng.random() < 0.8:
            job["stage_events"] = events
        jobs[tid] = job
    node_of = {"node0": 0, "node1": 1, "node2": 2}
    for tid in sorted(state.completed)[:4]:
        targets = tuple(node_of[ref.pool] for ref in state.completed[tid])
        history[tid] = ORIGINAL.Dispatch(tid, rng.randrange(3), targets)
        finished = now - rng.uniform(0.0, 5.0)
        jobs[tid] = {
            "stage_index": 0,
            "stage_started": finished - 1,
            "finished": finished,
            "stage_events": [{"index": 0, "start": finished - 1, "end": finished}],
        }
    return pending, jobs, history


def dump_action(action):
    if action is None:
        return None
    return {"task": action.task, "worker": action.worker, "targets": list(action.targets)}


def dump_state(state):
    return {
        "time": state.time,
        "remote_bytes": state.remote_bytes,
        "unassigned_work": state.unassigned_work,
        "assigned_work": list(state.assigned_work),
        "memory_limit": state.memory_limit,
        "completed": {
            tid: [ref.to_dict() for ref in refs] for tid, refs in state.completed.items()
        },
        "running": {
            tid: {
                "action": dump_action(flight.action),
                "stage_index": flight.stage_index,
                "stages": [
                    {
                        "kind": stage.kind,
                        "remaining": stage.remaining,
                        "resources": list(stage.resources),
                    }
                    for stage in flight.stages
                ],
            }
            for tid, flight in state.running.items()
        },
        "ready_order": list(state.ready_order),
    }


def rng_digest(generator):
    return hashlib.sha256(repr(generator.getstate()).encode()).hexdigest()


def make_policies(original, seed, objective):
    return {
        "random": original.RandomScheduling(seed),
        "single-node": original.SingleNode(),
        "data-local": original.DataLocal(),
        "greedy": original.EarliestFinish(objective),
        "greedy-wait": original.WaitAwareGreedy(objective),
        "heft": original.ListScheduler("heft", objective),
        "stealing": original.WorkStealing(objective),
        "beam-4": original.NativeBeam(width=4, budget_ms=1e9, objective=objective),
        "native-mcts-heuristic-32": original.NativeMCTS(
            iterations=32, budget_ms=1e9, seed=seed, rollout_policy="heuristic", objective=objective
        ),
        "native-mcts-uniform-32": original.NativeMCTS(
            iterations=32, budget_ms=1e9, seed=seed, rollout_policy="uniform", objective=objective
        ),
    }


def choose(policy, state):
    try:
        action = policy.choose(state)
    except Exception as error:
        return {"error": type(error).__name__}
    record = {"action": dump_action(action)}
    if hasattr(policy, "random"):
        record["rng"] = rng_digest(policy.random)
    return record


def close(policy):
    if hasattr(policy, "close"):
        policy.close()


def forecasts(rng, original, state):
    options = list(original.candidates(state))
    rng.shuffle(options)
    rows = []
    for action in options[:3]:
        future = state.clone()
        future.dispatch(action)
        while action.task not in future.completed:
            future.advance()
        everything = future.clone()
        everything.finish_running()
        rows.append(
            {
                "action": dump_action(action),
                "finish_time": future.time,
                "remote_bytes": future.remote_bytes,
                "isolated_seconds": state.isolated_seconds(action),
                "transfer_bytes": state.transfer_bytes(action),
                "all_running_finish_time": everything.time,
            }
        )
    return {"tail_estimate": state.tail_estimate(), "dispatches": rows}


def objective_for(original, index):
    if index % 4 == 3:
        return original.Objective(1.0, 1e-9)
    return original.Objective()


def build_states(rng, original):
    states = []
    for index in range(STATE_COUNT):
        graph = random_graph(rng, original, index)
        models = random_models(rng, original)
        state = original.Simulation(graph, models)
        random_progress(rng, original, state)
        if rng.random() < 0.75:
            while state.running and not (state.free_workers() and state.ready()):
                state.advance()
        perturb(rng, original, state)
        if rng.random() < 0.3:
            state.refresh_observed()
        states.append((index, graph, models, state))
    return states


def record_states(rng, original, states):
    rows = []
    for index, graph, models, state in states:
        objective = objective_for(original, index)
        policies = make_policies(original, index, objective)
        decisions = {}
        for name, policy in policies.items():
            decisions[name] = choose(policy, state.clone())
            close(policy)
        rows.append(
            {
                "graph": graph.to_dict(),
                "models": models.to_dict(),
                "objective": [objective.seconds_weight, objective.network_byte_weight],
                "seed": index,
                "state": dump_state(state),
                "decisions": decisions,
                "forecasts": forecasts(rng, original, state),
            }
        )
    return rows


def record_episodes(rng, original):
    rows = []
    for index in range(EPISODE_COUNT):
        graph = random_graph(rng, original, 10_000 + index)
        models = random_models(rng, original)
        objective = objective_for(original, index)
        episodes = {}
        for name, policy in make_policies(original, index, objective).items():
            state = original.Simulation(graph, models)
            actions = []
            outcome = "done"
            try:
                for _ in range(500):
                    if state.done:
                        break
                    action = policy.choose(state)
                    actions.append(dump_action(action))
                    if action is None:
                        state.advance()
                    else:
                        state.dispatch(action)
            except Exception as error:
                outcome = type(error).__name__
            close(policy)
            episodes[name] = {"actions": actions, "time": state.time, "outcome": outcome}
        rows.append(
            {
                "graph": graph.to_dict(),
                "models": models.to_dict(),
                "objective": [objective.seconds_weight, objective.network_byte_weight],
                "seed": index,
                "episodes": episodes,
            }
        )
    return rows


def record_live_states(rng, original):
    rows = []
    for index in range(LIVE_COUNT):
        graph = random_graph(rng, original, 20_000 + index)
        models = random_models(rng, original)
        state = original.Simulation(graph, models)
        random_progress(rng, original, state)
        if not state.running:
            continue
        now = rng.uniform(10.0, 100.0)
        elapsed = rng.uniform(1.0, 50.0)
        pending, jobs, history = random_jobs(rng, state, now)
        use_history = rng.random() < 0.8
        live = original.live_model(
            graph,
            models,
            state.completed,
            pending,
            jobs,
            list(state.assigned_work),
            now=now,
            elapsed=elapsed,
            history_actions=history if use_history else None,
        )
        rows.append(
            {
                "graph": graph.to_dict(),
                "models": models.to_dict(),
                "completed": {
                    tid: [r.to_dict() for r in refs] for tid, refs in state.completed.items()
                },
                "pending": {tid: dump_action(action) for tid, action in pending.items()},
                "jobs": jobs,
                "history": {tid: dump_action(action) for tid, action in history.items()},
                "assigned": list(state.assigned_work),
                "now": now,
                "elapsed": elapsed,
                "used_history": use_history,
                "result": dump_state(live),
            }
        )
    return rows


def record_workloads(original):
    shards = tuple(
        original.ObjectRef(
            "node%d" % (0, 0, 1, 2)[i % 4], "datasets/x/shard-%06d" % i, 4096, partition=i
        )
        for i in range(5)
    )
    manifest = original.Manifest("x", shards, 4096)
    rows = {}
    cases = [
        ("independent", 1, 0.25),
        ("chain", 64, 1),
        ("branch-join", 1, 2),
        ("competing", 8, 1 / 16),
        ("fanout", 1, 0.5),
        ("divergent", 16, 0.5),
    ]
    for shape, iterations, ratio in cases:
        graph = original.refinement_build(manifest, shape, iterations, ratio)
        rows["%s-%s-%s" % (shape, iterations, ratio)] = [
            task.to_dict() for task in graph.tasks.values()
        ]
    return {"dataset": manifest.to_dict(), "graphs": rows}


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.manifests = {}
        self.not_found = KeyError

    def read(self, ref):
        return self.objects[ref.pool, ref.key]

    def write(self, pool, key, data, dtype="float32", partition=0):
        self.objects[pool, key] = bytes(data)
        return ORIGINAL.ObjectRef(
            pool, key, len(data), dtype, partition, hashlib.sha256(data).hexdigest()
        )

    def put_manifest(self, pool, key, value):
        self.manifests[pool, key] = json.loads(json.dumps(value))

    def get_manifest(self, pool, key):
        return copy.deepcopy(self.manifests[pool, key])

    def close(self):
        pass


def record_wire(original):
    from unittest.mock import patch

    store = MemoryStore()
    source_bytes = original.synthetic(4096, 5)
    source = store.write("node1", "datasets/wire/part-000000", source_bytes, partition=0)
    graph = original.Graph(graph_id="f" * 32)
    left, right = graph.add("split@1", [source], ratios=[0.5, 1], iterations=2)
    (middle,) = graph.add("map@1", [left], ratios=[0.5], iterations=3)
    graph.add("join@1", [middle, right], ratios=[0.25], iterations=1)
    workers = [original.DurableWorker(node, store) for node in range(3)]
    calls = []
    lock = threading.Lock()

    def rpc(node, path, payload):
        with lock:
            calls.append({"node": node, "path": path, "body": json.dumps(payload)})
        worker = workers[node]
        if path == "state":
            return worker.snapshot(payload["keys"])
        if path == "submit":
            return worker.submit(payload)
        if path == "ack":
            return worker.acknowledge(payload)
        if path == "warm":
            return worker.warm(payload)
        raise ValueError(path)

    run_id = "e" * 32
    with (
        patch("cephdag.worker.Path.read_text", return_value="0"),
        patch.object(original.runtime, "RadosStore", return_value=store),
        patch.object(original.runtime, "rpc", side_effect=rpc),
    ):
        result = original.runtime.run(graph, original.Models(), "greedy", run_id=run_id)
        snapshot = workers[0].snapshot([])
    completed_job = None
    for record in result["tasks"].values():
        assert record["result"]["outputs"]
    for call in calls:
        if call["path"] == "ack":
            key = json.loads(call["body"])["key"]
            node = call["node"]
            completed_job = store.get_manifest("node%d" % node, "runs/%s/worker-job.json" % key)
            break
    return {
        "source": {"ref": source.to_dict(), "seed": 5, "size": 4096},
        "graph": graph.to_dict(),
        "run_id": run_id,
        "scheduler": "greedy",
        "calls": calls,
        "final_outputs": result["final_outputs"],
        "result_keys": sorted(result),
        "completed_job_keys": sorted(completed_job),
        "worker_result_keys": sorted(completed_job["result"]),
        "snapshot_keys": sorted(snapshot),
    }


ORIGINAL = types.SimpleNamespace()


def load_original(path, native_library):
    sys.path.insert(0, str(Path(path).expanduser().resolve()))
    redirect_native_library(native_library)
    from cephdag import (
        graph,
        list_schedulers,
        model,
        native_planner,
        runtime,
        schedulers,
        search_alternatives,
        worker,
        workloads,
    )
    from experiments import refinement_run

    for module in (
        graph,
        model,
        schedulers,
        list_schedulers,
        native_planner,
        search_alternatives,
        worker,
    ):
        for name in dir(module):
            if not name.startswith("_"):
                setattr(ORIGINAL, name, getattr(module, name))
    ORIGINAL.runtime = runtime
    ORIGINAL.live_model = runtime.live_model
    ORIGINAL.synthetic = workloads.synthetic
    ORIGINAL.refinement_build = refinement_run.build
    return ORIGINAL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True)
    parser.add_argument("--native-library", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 9):
        raise SystemExit("generate the fixture with Python 3.9, the deployment interpreter")
    original = load_original(args.original, args.native_library)
    library = Path(args.native_library).read_bytes()
    rng = random.Random(20261112)
    states = build_states(rng, original)
    fixture = {
        "generator": "tests/fixtures/make_equivalence_fixture.py",
        "python": sys.version.split()[0],
        "native_library_sha256": hashlib.sha256(library).hexdigest(),
        "states": record_states(rng, original, states),
        "episodes": record_episodes(rng, original),
        "live_states": record_live_states(rng, original),
        "workloads": record_workloads(original),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "equivalence.json").write_text(json.dumps(fixture, separators=(",", ":")) + "\n")
    (args.output / "wire.json").write_text(json.dumps(record_wire(original), indent=1) + "\n")
    decisions = sum(len(row["decisions"]) for row in fixture["states"])
    episodes = sum(len(row["episodes"]) for row in fixture["episodes"])
    print(
        json.dumps(
            {
                "states": len(fixture["states"]),
                "decisions": decisions,
                "episodes": episodes,
                "live_states": len(fixture["live_states"]),
            }
        )
    )


if __name__ == "__main__":
    main()
