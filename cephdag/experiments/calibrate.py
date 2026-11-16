"""Fit the cost models from dedicated samples run on the lab (never from comparison runs).

    python -m experiments.calibrate --network-mbit 400 --output results \\
        [--droid droid-128-02047 --resolution 128] [--cluster FILE --storage-from FILE]

Each worker runs map@1 tasks at the campaigns' shard size and a smaller one, in rounds in
which every worker runs one sample at the same time, as in a campaign: workers that compute
at once share the host's caches and memory bandwidth, and a 64 MiB task took 3-9 times
longer in a campaign than calibration samples of 4 and 16 MiB run one at a time predicted; compute coefficients come from a
nonnegative least-squares fit of the compute stage over every worker. A node with both a worker and a pool runs
its samples on local inputs, and its storage rates come from those local reads and
writes. A compute-only worker reads and writes in a storage node's pool, so its samples
time compute only. A storage-only node has no worker to measure its disk with: its rates
come from `--storage-from`, the calibration.json of a calibration in which that pool's
node had a worker (the computational-storage mode of the same lab). Compute-only nodes
have no disk the forecast uses; they get the median of the measured rates as a
placeholder. The network rate is the measured shaped bandwidth given on the command line,
the same for every node.

With --droid, every partition of that ingested dataset (a shard held out of every
workload) also runs the DROID pipeline, on the node that stores it or, if that node has
no worker, on a worker that writes back to that node's pool, and each DROID function
gets its own fixed + per-input-byte + per-output-byte fit.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
import itertools
import json
from pathlib import Path
import statistics
import time
import uuid

import numpy as np

from cephdag.client import Client
from cephdag.cluster import LAB_CLUSTER, Cluster
from cephdag.coordinator import HttpTransport, acknowledge
from cephdag.functions import synthetic
from cephdag.graph import Graph, ObjectRef, ref_from_dict
from cephdag.model import ComputeModel, FunctionCost, Models, NetworkModel, StorageModel
from experiments import droid_workload

# On aakashpc, 8 workers computing at once take 6-8 times longer per byte than one alone
# from 16 MiB up (they share one memory bus); 32 and 64 MiB cost about the same per byte.
SAMPLE_MIB = (32, 64)
# (iterations, output ratio); a 64 MiB task with ratio 2 would exceed a worker's memory.
SAMPLE_SETTINGS = ((0, 0.25), (0, 1), (32, 0.25), (32, 1))
MIB = 2**20


def sample_settings(mib):
    """(iterations, output ratio) of each sample of `mib` MiB; the largest size also runs
    128 iterations, to cover the high-intensity end of the campaigns."""
    return SAMPLE_SETTINGS + (((128, 0.25),) if mib == max(SAMPLE_MIB) else ())


def fit_nonnegative(features, targets):
    """Least squares over every subset of columns, keeping the best all-nonnegative solution."""
    best = np.zeros(features.shape[1])
    best_error = float("inf")
    for mask in itertools.product((False, True), repeat=features.shape[1]):
        columns = np.flatnonzero(mask)
        if not len(columns):
            continue
        coefficients = np.linalg.lstsq(features[:, columns], targets, rcond=None)[0]
        if np.any(coefficients < 0):
            continue
        candidate = np.zeros(features.shape[1])
        candidate[columns] = coefficients
        error = float(np.sum((features @ candidate - targets) ** 2))
        if error < best_error:
            best = candidate
            best_error = error
    return best


def sample_nodes(cluster, pool_node, turn):
    """(worker, target) for a sample whose input is in `pool_node`'s pool.

    The node itself runs it if it has a worker; otherwise worker number `turn` among the
    cluster's workers runs it and writes back to that pool.
    """
    if cluster.nodes[pool_node].worker is not None:
        return pool_node, pool_node
    workers = cluster.worker_nodes
    return workers[turn % len(workers)], pool_node


def run_sample(transport, node, calibration_id, task, refs, target=None, timeout=120):
    """Run `task` on `node` with every output stored on `target` (default: `node`)."""
    target = node if target is None else target
    payload = {
        "run_id": calibration_id,
        "worker": node,
        "targets": [target] * len(task.output_sizes),
        "task": task.to_dict(),
        "inputs": [ref.to_dict() for ref in refs],
    }
    transport.post(node, "submit", payload)
    key = f"{calibration_id}/{task.id}"
    deadline = time.monotonic() + timeout
    while True:
        job = transport.post(node, "state", {"keys": [key]})["jobs"][key]
        if job["status"] == "failed":
            raise RuntimeError(job["error"])
        if job["status"] == "completed":
            acknowledge(transport, node, job)
            return job["result"]
        if time.monotonic() > deadline:
            raise TimeoutError("calibration task")
        time.sleep(0.01)


def sample_row(transport, calibration_id, sample):
    """Run `sample`, (node, pool node, task, input ref); return its row."""
    node, pool_node, task, ref = sample
    result = run_sample(transport, node, calibration_id, task, [ref], pool_node)
    progress = {"node": node, "input_mib": ref.size / MIB, "seconds": result["seconds"]}
    print(json.dumps(progress), flush=True)
    return {
        "node": node,
        "pool_node": pool_node,
        "input_bytes": ref.size,
        "output_bytes": sum(task.output_sizes),
        "iterations": task.params["iterations"],
        "result": result,
    }


def stage_seconds(result, kind):
    return sum(stage["seconds"] for stage in result["stages"] if stage["kind"] == kind)


def local_rates(rows, node):
    """Median (read, write) bytes per second of `node`'s largest samples on its own pool."""
    large = [
        row
        for row in rows
        if row["node"] == node
        and row.get("pool_node", node) == node
        and row["input_bytes"] == max(SAMPLE_MIB) * MIB
    ]
    if not large:
        return None
    stages = [stage for row in large for stage in row["result"]["stages"]]
    return tuple(
        statistics.median(
            stage["bytes"] / stage["seconds"] for stage in stages if stage["kind"] == kind
        )
        for kind in ("read", "write")
    )


def reference_rates(reference, pool):
    """(read, write) of `pool` in a reference calibration.json, or None."""
    if reference is None or pool not in reference["pools"]:
        return None
    node = reference["pools"].index(pool)
    if reference["storage_sources"][node] != "measured":
        return None
    storage = reference["models"]["storage"]
    return storage["read_bps"][node], storage["write_bps"][node]


def storage_rates(rows, cluster, reference=None):
    """(read rates, write rates, source of each node's rates) for every node of `cluster`."""
    rates = {}
    sources = {}
    for node in cluster.storage_nodes:
        measured = local_rates(rows, node)
        if measured is not None:
            rates[node] = measured
            sources[node] = "measured"
            continue
        pool = cluster.nodes[node].pool
        rates[node] = reference_rates(reference, pool)
        if rates[node] is None:
            raise ValueError(
                f"pool {pool} has no worker on its node; pass --storage-from with the "
                "calibration.json of a calibration in which it had one"
            )
        sources[node] = f"reference {reference['calibration_id']}"
    placeholder = tuple(statistics.median(rate[kind] for rate in rates.values()) for kind in (0, 1))
    reads = []
    writes = []
    for node in range(len(cluster.nodes)):
        read, write = rates.get(node, placeholder)
        reads.append(read)
        writes.append(write)
    names = [sources.get(node, "unused: compute-only") for node in range(len(cluster.nodes))]
    return tuple(reads), tuple(writes), names


def fit_models(rows, cluster, network_mbit, calibration_id, reference=None):
    """(Models, compute fit RMSE, source of each node's storage rates)."""
    features = np.array(
        [
            [
                1,
                row["input_bytes"] / MIB,
                row["input_bytes"] / MIB * row["iterations"],
                row["output_bytes"] / MIB,
            ]
            for row in rows
        ]
    )
    targets = np.array([stage_seconds(row["result"], "compute") for row in rows])
    fit = fit_nonnegative(features, targets)
    reads, writes, sources = storage_rates(rows, cluster, reference)
    node_count = len(cluster.nodes)
    rate = network_mbit * 1e6 / 8
    models = Models(
        ComputeModel(fit[0], *(fit[1:] / MIB)),
        StorageModel(reads, writes),
        NetworkModel((rate,) * node_count, (rate,) * node_count),
        True,
        calibration_id,
    )
    rmse = float(np.sqrt(np.mean((features @ fit - targets) ** 2)))
    return models, rmse, sources


def droid_samples(client, run_id, name, resolution):
    """One row per DROID task on every partition of dataset `name`, run where it is stored
    (see sample_nodes).

    The samples' outputs are removed afterwards; they live under runs/<run_id>/.
    """
    (wave,) = droid_workload.waves(droid_workload.load_partitions(client, [name]), float("inf"))
    graph = droid_workload.build_graph(wave.dataset, resolution, wave.steps)
    stored = {}
    rows = []
    # Tasks were added in dependency order, so every input exists when its task runs.
    for task in graph.tasks.values():
        refs = [
            ref if isinstance(ref, ObjectRef) else stored[ref.task][ref.output]
            for ref in task.inputs
        ]
        pool_node = client.cluster.node_of(refs[0].pool)
        node, target = sample_nodes(client.cluster, pool_node, task.partition)
        sized = task.sized_for([ref.size for ref in refs])
        result = run_sample(client.transport, node, run_id, sized, refs, target, timeout=600)
        stored[task.id] = [ref_from_dict(output) for output in result["outputs"]]
        rows.append(
            {
                "node": node,
                "pool_node": pool_node,
                "function": task.function,
                "input_bytes": sum(ref.size for ref in refs),
                "output_bytes": sum(ref.size for ref in stored[task.id]),
                "result": result,
            }
        )
    for outputs in stored.values():
        for ref in outputs:
            client.store.remove_generated(ref, run_id)
    return rows


def fit_function_costs(rows):
    """((function, FunctionCost), ...) from a nonnegative fit per function; and RMSE per function."""
    costs = []
    errors = {}
    for function in sorted({row["function"] for row in rows}):
        selected = [row for row in rows if row["function"] == function]
        features = np.array(
            [[1, row["input_bytes"] / MIB, row["output_bytes"] / MIB] for row in selected]
        )
        targets = np.array([stage_seconds(row["result"], "compute") for row in selected])
        fit = fit_nonnegative(features, targets)
        costs.append((function, FunctionCost(float(fit[0]), *(float(x) for x in fit[1:] / MIB))))
        errors[function] = float(np.sqrt(np.mean((features @ fit - targets) ** 2)))
    return tuple(costs), errors


def calibrate(
    cluster,
    store,
    transport,
    network_mbit,
    output,
    droid=None,
    resolution=128,
    reference=None,
):
    """Measure, fit and write calibration.json and models.json to `output`; return the models.

    `reference` is the calibration.json of a calibration that measured the pools of
    this cluster's storage-only nodes.
    """
    calibration_id = uuid.uuid4().hex
    graph = Graph()
    samples = {}
    compute_only = [node for node in cluster.worker_nodes if cluster.nodes[node].pool is None]
    for node in cluster.worker_nodes:
        pool_node = node
        if node in compute_only:
            pool_node = cluster.storage_nodes[compute_only.index(node) % len(cluster.storage_nodes)]
        pool = cluster.nodes[pool_node].pool
        samples[node] = []
        for mib in SAMPLE_MIB:
            data = synthetic(mib * MIB, 70000 + node * 100 + mib)
            ref = store.write(pool, f"calibration/{calibration_id}/input-{node}-{mib}", data)
            for iterations, ratio in sample_settings(mib):
                (out,) = graph.add("map@1", [ref], iterations=iterations, ratios=[ratio])
                samples[node].append((node, pool_node, graph.tasks[out.task], ref))
    rows = []
    # Round k runs sample k of every worker at once, as a campaign keeps every worker busy.
    with ThreadPoolExecutor(max_workers=len(samples)) as executor:
        for round_samples in zip(*samples.values()):
            rows += executor.map(partial(sample_row, transport, calibration_id), round_samples)
    models, rmse, sources = fit_models(rows, cluster, network_mbit, calibration_id, reference)
    artifact = {
        "calibration_id": calibration_id,
        "pools": list(cluster.pools),
        "workers": list(cluster.worker_nodes),
        "storage_sources": sources,
        "samples": rows,
        "compute_fit_rmse_seconds": rmse,
        "network_mbit_measured": network_mbit,
        "sample_mib": list(SAMPLE_MIB),
        "source": "separate calibration inputs; rounds of one task on every worker at once",
    }
    if droid:
        client = Client(cluster, models, store=store, transport=transport)
        droid_rows = droid_samples(client, uuid.uuid4().hex, droid, resolution)
        functions, errors = fit_function_costs(droid_rows)
        models = replace(models, compute=replace(models.compute, functions=functions))
        artifact.update(
            droid_dataset=droid,
            droid_resolution=resolution,
            droid_samples=droid_rows,
            droid_fit_rmse_seconds=errors,
        )
    artifact["models"] = models.to_dict()
    output.mkdir(parents=True, exist_ok=True)
    (output / "calibration.json").write_text(json.dumps(artifact, indent=2))
    (output / "models.json").write_text(json.dumps(models.to_dict(), indent=2))
    store.put_manifest(cluster.metadata_pool, f"calibration/{calibration_id}/models.json", artifact)
    print(json.dumps({"frozen_models": models.to_dict(), "fit_rmse": rmse}), flush=True)
    return models


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--network-mbit", type=float, required=True)
    parser.add_argument("--cluster", type=Path, default=LAB_CLUSTER)
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--droid", default=None, help="held-out ingested DROID dataset name")
    parser.add_argument("--resolution", type=int, default=128)
    parser.add_argument(
        "--storage-from",
        type=Path,
        default=None,
        help="calibration.json that measured the pools of nodes without a worker",
    )
    args = parser.parse_args()
    from cephdag.storage import RadosStore

    cluster = Cluster.load(args.cluster)
    reference = json.loads(args.storage_from.read_text()) if args.storage_from else None
    store = RadosStore(cluster)
    try:
        calibrate(
            cluster,
            store,
            HttpTransport(cluster),
            args.network_mbit,
            args.output,
            args.droid,
            args.resolution,
            reference,
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
