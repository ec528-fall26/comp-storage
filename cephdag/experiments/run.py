"""Run one workload under several policies on the lab, writing one raw JSON file per run.

    python -m experiments.run --workload independent --iterations 1 --ratio 0.25 \\
        --policies greedy,heft,beam-4 --repeats 3 --models results/models.json \\
        --output results/independent-small

Every run is checked: final outputs are read back against their checksums, all
policies must produce byte-identical outputs, sampled values are recomputed by an
independent scalar oracle, and only the run's own generated payloads are removed.

The same commands run on a computational-storage cluster file and on a disaggregated
one (`--cluster`); source datasets are opened by name, created once and never removed
by a run (experiments.datasets deletes them). Each run appends one line to
<output>/rows.jsonl: mode, cluster and models file digests, policy, workload, dataset,
times, network bytes and the verification outcome, including a digest of every output's
checksum, which must be the same in both modes (experiments/campaign.py checks it).

The DROID pipeline runs over datasets written by experiments.droid_ingest, as a
sequence of waves of at most --wave-gib source each; every wave is a separate graph
run under every policy, verified and cleaned before the next wave starts:

    python -m experiments.run --workload droid --droid-shards 0-63 --resolution 128 \\
        --wave-gib 16 --policies greedy,heft,beam-4 --repeats 1 \\
        --models results/models.json --output results/droid

With --droid-fused each partition is one droid_fused task, which keeps frames and state in
memory and writes only the videos, statistics and Parquet; their digest
(final_outputs_sha256 in the DROID verification) equals the unfused graph's.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import shutil
import time
import uuid

from cephdag import droid
from cephdag.client import Client
from cephdag.cluster import LAB_CLUSTER
from cephdag.coordinator import acknowledge, post_with_retry
from cephdag.functions import synthetic
from cephdag.graph import Dataset, Graph, ObjectRef, ref_from_dict
from cephdag.model import Models, NetworkModel
from experiments import droid_ingest, droid_workload
from experiments.oracle import verify_oracle

SHAPES = ("independent", "chain", "branch-join", "competing", "fanout", "divergent", "droid")
SOURCE_SEED = 20261030
SOURCE_PLACEMENT = (0, 0, 1, 2)
SEEDED_POLICIES = ("random", "native-mcts-")


def build_workload(dataset, shape, iterations, ratio):
    graph = Graph()
    if shape == "independent":
        graph.map("map@1", dataset, iterations=iterations, ratios=[ratio])
    elif shape == "chain":
        first = graph.map("map@1", dataset, iterations=iterations, ratios=[ratio])
        second = graph.map("map@1", first, iterations=iterations, ratios=[ratio])
        graph.map("map@1", second, iterations=iterations, ratios=[ratio])
    elif shape == "branch-join":
        left = graph.map("map@1", dataset, iterations=iterations, ratios=[ratio / 2])
        right = graph.map("map@1", dataset, iterations=iterations + 1, ratios=[ratio / 2])
        graph.map("join@1", left, right, iterations=iterations, ratios=[0.5])
    elif shape == "competing":
        for index, source in enumerate(dataset.shards):
            left, right = (4, 1) if index % 2 == 0 else (1, 4)
            (first,) = graph.add("map@1", [source], iterations=iterations * left, ratios=[ratio])
            (second,) = graph.add("map@1", [source], iterations=iterations * right, ratios=[ratio])
            (third,) = graph.add("map@1", [first], iterations=iterations * right, ratios=[1])
            (fourth,) = graph.add("map@1", [second], iterations=iterations * left, ratios=[1])
            (joined,) = graph.add("join@1", [first, fourth], iterations=iterations, ratios=[0.25])
            graph.add("join@1", [third, joined], iterations=iterations, ratios=[0.25])
    elif shape == "fanout":
        for source in dataset.shards:
            (mapped,) = graph.add("map@1", [source], iterations=iterations, ratios=[ratio])
            graph.add("map@1", [mapped], iterations=iterations, ratios=[0.25])
            graph.add("map@1", [mapped], iterations=iterations * 4, ratios=[0.25])
    elif shape == "divergent":
        for index, source in enumerate(dataset.shards):
            large, small = graph.add(
                "split@1", [source], iterations=iterations, ratios=[ratio, 0.125]
            )
            graph.add("map@1", [large], iterations=iterations * 4, ratios=[0.25])
            # A peer shard in another pool pulls the consumers of this partition two ways.
            peer = dataset.shards[(index + 2) % len(dataset.shards)]
            graph.add("join@1", [small, peer], iterations=iterations, ratios=[0.125])
    else:
        raise ValueError(f"unknown workload shape: {shape}")
    return graph


def workload_graph(dataset, case):
    if case["shape"] == "droid" and case.get("fused"):
        return droid_workload.build_fused_graph(dataset, case["resolution"], case["steps"])
    if case["shape"] == "droid":
        return droid_workload.build_graph(dataset, case["resolution"], case["steps"])
    return build_workload(dataset, case["shape"], case["iterations"], case["ratio"])


def verify_outputs(store, result, graph, case):
    if case["shape"] == "droid":
        verified = droid_workload.verify(store, result, graph, case["resolution"])
        return {"droid_verification": verified}
    return {"independent_oracle": verify_oracle(store, result, graph, SOURCE_SEED)}


def require_function_costs(models, functions):
    missing = sorted(set(functions) - {function for function, _ in models.compute.functions})
    if missing:
        raise RuntimeError(f"models lack calibrated costs for {missing}; see calibrate --droid")


def require_capacity(free_bytes, input_bytes, output_bytes, reserve=8 * 2**30):
    """The lab's OSDs live on a bounded Docker disk; refuse runs that could fill it."""
    if free_bytes < input_bytes + output_bytes * 1.2 + reserve:
        raise RuntimeError(
            f"insufficient Docker disk headroom: free={free_bytes}, new_input={input_bytes}, "
            f"outputs={output_bytes}, reserve={reserve}"
        )


def require_validated(evidence, fsid, bandwidth):
    rates = evidence.get("aggregate_mbit", {})
    if (
        evidence.get("fsid") != fsid
        or set(rates) != {"shared_tx", "shared_rx"}
        or not all(0.7 * bandwidth <= rate <= 1.2 * bandwidth for rate in rates.values())
    ):
        raise RuntimeError("live cluster/topology validation missing or mismatched")


def synthetic_dataset(
    client, total_bytes, shard_bytes, ceph_data, name=None, placement=SOURCE_PLACEMENT
):
    """Open the deterministic synthetic dataset `name`, creating it on first use.

    Shard i is on node placement[i % len(placement)]; an existing dataset must have
    exactly the requested shards there.
    """
    name = name or f"synthetic-{total_bytes}-{shard_bytes}-{SOURCE_SEED}"
    count = -(-total_bytes // shard_bytes)
    sizes = [min(shard_bytes, total_bytes - index * shard_bytes) for index in range(count)]
    nodes = [placement[index % len(placement)] for index in range(count)]
    try:
        dataset = client.dataset(name)
    except client.store.not_found:
        require_capacity(shutil.disk_usage(ceph_data).free, total_bytes, total_bytes / 32)
        parts = (synthetic(size, SOURCE_SEED + index) for index, size in enumerate(sizes))
        dataset = client.put_dataset(name, parts, placement=nodes)
    expected = [(client.cluster.nodes[node].pool, size) for node, size in zip(nodes, sizes)]
    if [(shard.pool, shard.size) for shard in dataset.shards] != expected:
        raise RuntimeError(f"dataset {name} exists with other shard sizes or placement")
    return dataset


def prefix(dataset, size):
    selected = []
    total = 0
    for shard in dataset.shards:
        if total >= size:
            break
        selected.append(shard)
        total += shard.size
    if total != size:
        raise ValueError("prefix size must align to shards and fit the source dataset")
    return Dataset(f"{dataset.name}-prefix-{size}", tuple(selected))


def warm(client, dataset, timeout=1800):
    """Read every source shard on its own node so that each run starts from the same caches.

    Only nodes with both a worker and a pool can read their own shards; shards on
    storage-only nodes are not warmed.
    """
    run_id = uuid.uuid4().hex
    key = f"{run_id}/warm"
    cluster = client.cluster
    nodes = [node for node in cluster.worker_nodes if node in cluster.storage_nodes]
    for node in nodes:
        pool = client.cluster.nodes[node].pool
        refs = [shard.to_dict() for shard in dataset.shards if shard.pool == pool]
        post_with_retry(client.transport, node, "warm", {"run_id": run_id, "refs": refs})
    results = {}
    deadline = time.monotonic() + timeout
    while len(results) < len(nodes):
        for node in nodes:
            if node in results:
                continue
            job = client.transport.post(node, "state", {"keys": [key]})["jobs"][key]
            if job["status"] in ("failed", "publication_failed"):
                raise RuntimeError(job["error"])
            if job["status"] == "completed":
                results[node] = job["result"]
                acknowledge(client.transport, node, job)
        if time.monotonic() > deadline:
            raise TimeoutError("warm pass")
        time.sleep(0.05)
    return results


def output_signature(result):
    return [
        [task_id, [output["sha256"] for output in task["result"]["outputs"]]]
        for task_id, task in sorted(result["tasks"].items())
    ]


def outputs_digest(result):
    """One digest of every output's checksum by task; placement does not enter it."""
    return hashlib.sha256(json.dumps(output_signature(result)).encode()).hexdigest()


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cluster_mode(cluster):
    """'cs' if every host stores and computes, 'disagg' if no host does both, else 'mixed'.
    A cluster whose workers share hosts k at a time (lab modes cs2 and disagg2) is 'cs<k>' or
    'disagg<k>'."""
    workers = Counter(cluster.hosts[node] for node in cluster.worker_nodes)
    both = [cluster.pools[host] is not None and host in workers for host in set(cluster.hosts)]
    if all(both):
        kind = "cs"
    elif any(both):
        return "mixed"
    else:
        kind = "disagg"
    slots = max(workers.values())
    return kind if slots == 1 else f"{kind}{slots}"


def payload_traffic(graph, result, cluster):
    """Bytes of task inputs and outputs that crossed between hosts, from their stored sizes:
    in total, and sent (tx) and received (rx) per node, counted at each host's node."""
    pools = cluster.pools
    nodes = {pool: node for node, pool in enumerate(pools) if pool is not None}
    sent = [0] * len(pools)
    received = [0] * len(pools)
    outputs = {
        task_id: [ref_from_dict(output) for output in record["result"]["outputs"]]
        for task_id, record in result["tasks"].items()
    }
    for task_id, record in result["tasks"].items():
        worker = cluster.hosts[record["worker"]]
        moves = []
        for ref in graph.tasks[task_id].inputs:
            stored = ref if isinstance(ref, ObjectRef) else outputs[ref.task][ref.output]
            moves.append((nodes[stored.pool], worker, stored.size))
        moves += [(worker, nodes[stored.pool], stored.size) for stored in outputs[task_id]]
        for source, destination, size in moves:
            if source != destination:
                sent[source] += size
                received[destination] += size
    per_node = {str(node): {"tx": sent[node], "rx": received[node]} for node in range(len(pools))}
    return {"bytes": sum(sent), "per_node": per_node}


def interface_traffic(result, cluster):
    """Bytes each worker host's interface sent and received during the run, under the node of
    its first worker. Nodes without a worker report no counters."""
    return {
        str(after["node"]): {
            kind: after["counters"][kind] - before["counters"][kind] for kind in ("tx", "rx")
        }
        for before, after in zip(result["counter_before"], result["counter_after"])
        if after["node"] in cluster.interface_nodes
    }


def result_row(result, graph, cluster, files, verification):
    """The compact record of one run that rows.jsonl keeps."""
    case = result["case"]
    return {
        "run_id": result["run_id"],
        "mode": cluster_mode(cluster),
        **files,
        "policy": result["scheduler"],
        "repeat": result["config"]["repeat"],
        "workload": case["shape"],
        "iterations": case.get("iterations"),
        "ratio": case.get("ratio"),
        "resolution": case.get("resolution"),
        "fused": case.get("fused"),
        "wave": case.get("wave"),
        "dataset": result["dataset"],
        "dataset_bytes": result["dataset_bytes"],
        "tasks": len(graph.tasks),
        "wall_seconds": result["wall_seconds"],
        "policy_seconds": result["scheduler_seconds"],
        "network_payload_bytes": payload_traffic(graph, result, cluster),
        "network_interface_bytes": interface_traffic(result, cluster),
        "network_tx_bytes": result["network_tx_bytes"],
        "warmed": bool(result["warmup"]),
        "verification": verification,
    }


def cleanup_outputs(store, metadata_pool, result):
    """Remove exactly the payloads this run generated; inputs and metadata stay."""
    run_id = result["run_id"]
    removed = 0
    for task in result["tasks"].values():
        for output in task["result"]["outputs"]:
            store.remove_generated(ref_from_dict(output), run_id)
            removed += output["size"]
    tombstone = {
        "payloads_retained": False,
        "verified_before_removal": True,
        "removed_bytes": removed,
    }
    store.put_manifest(metadata_pool, f"runs/{run_id}/retention.json", tombstone)
    return removed


def make_rows(policies, repeats, seed):
    """Every policy once per repeat, in a seeded random order per repeat."""
    generator = random.Random(seed)
    rows = []
    for repeat in range(repeats):
        order = list(policies)
        generator.shuffle(order)
        rows += [{"repeat": repeat, "scheduler": policy} for policy in order]
    return rows


def policy_options(policy, seed, budget_ms):
    options = {}
    if policy.startswith(SEEDED_POLICIES):
        options["seed"] = seed
    if budget_ms is not None and policy.startswith(("beam-", "native-mcts-")):
        options["budget_ms"] = budget_ms
    return options


def run_matrix(client, dataset, case, rows, settings, directory):
    """Run every row; return the raw results.

    `settings` holds seed, budget_ms and ceph_data, and optionally warm (default True),
    files (digests recorded in each row), dataset (the name to record) and rows (the
    rows.jsonl path, default <directory>/rows.jsonl). A run whose verification fails is
    recorded, its outputs are kept, and the error is raised.
    """
    results = []
    expected = None
    rows_file = settings.get("rows", directory / "rows.jsonl")
    for index, row in enumerate(rows):
        graph = workload_graph(dataset, case)
        output_bytes = sum(sum(task.output_sizes) for task in graph.tasks.values())
        require_capacity(shutil.disk_usage(settings["ceph_data"]).free, 0, output_bytes)
        warmed = warm(client, dataset) if settings.get("warm", True) else {}
        seed = settings["seed"] + 1009 * row["repeat"]
        options = policy_options(row["scheduler"], seed, settings["budget_ms"])
        result = client.submit(graph, row["scheduler"], **options).wait().metrics()
        result.update(
            config=row,
            case=case,
            dataset=settings.get("dataset", dataset.name),
            dataset_bytes=sum(shard.size for shard in dataset.shards),
            warmup=warmed,
        )
        digest = outputs_digest(result)
        verification = {"passed": False, "outputs_sha256": digest}
        try:
            result.update(verify_outputs(client.store, result, graph, case))
            if expected is not None and digest != expected:
                raise RuntimeError(f"outputs {digest} differ from {expected} of earlier runs")
        except Exception as error:
            verification["error"] = f"{type(error).__name__}: {error}"
            raise
        else:
            verification["passed"] = True
            if case["shape"] == "droid":
                droid_check = result["droid_verification"]
                verification["final_outputs_sha256"] = droid_check["final_outputs_sha256"]
            expected = digest
            result["cross_scheduler_checksums_match"] = True
        finally:
            result["verification"] = verification
            (directory / f"run-{index:03d}-{row['scheduler']}.json").write_text(
                json.dumps(result, indent=2)
            )
            line = result_row(
                result, graph, client.cluster, settings.get("files", {}), verification
            )
            with open(rows_file, "a") as rows_out:
                rows_out.write(json.dumps(line) + "\n")
        removed = cleanup_outputs(client.store, client.cluster.metadata_pool, result)
        results.append(result)
        summary = {
            "index": index,
            "runs": len(rows),
            "scheduler": row["scheduler"],
            "wall_seconds": result["wall_seconds"],
            "scheduler_seconds": result["scheduler_seconds"],
            "network_tx_bytes": result["network_tx_bytes"],
            "removed_bytes": removed,
        }
        print(json.dumps(summary), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--workload", choices=SHAPES, required=True)
    parser.add_argument("--iterations", type=int, help="required except for droid")
    parser.add_argument("--ratio", type=float, help="required except for droid")
    parser.add_argument(
        "--droid-shards", type=droid_ingest.parse_range, help="ingested source shards, e.g. 0-63"
    )
    parser.add_argument("--resolution", type=int, default=128)
    parser.add_argument(
        "--droid-fused", action="store_true", help="one droid_fused task per partition"
    )
    parser.add_argument("--wave-gib", type=float, default=16.0)
    parser.add_argument("--policies", required=True, help="comma-separated policy names")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=9103)
    parser.add_argument(
        "--budget-ms", type=float, default=None, help="planning budget for beam and native MCTS"
    )
    parser.add_argument("--dataset-gib", type=float, default=0.0625)
    parser.add_argument(
        "--source-gib", type=float, default=None, help="use a prefix of this larger dataset"
    )
    parser.add_argument("--shard-mib", type=int, default=8)
    parser.add_argument(
        "--dataset", default=None, help="synthetic dataset name, created on first use"
    )
    parser.add_argument(
        "--placement",
        default=",".join(map(str, SOURCE_PLACEMENT)),
        help="node of each new synthetic shard, cycled",
    )
    parser.add_argument(
        "--no-warm", action="store_true", help="do not read the sources before each run"
    )
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument(
        "--validation", type=Path, default=None, help="topology evidence; sets the network rate"
    )
    parser.add_argument("--bandwidth-mbit", type=int, default=None)
    parser.add_argument("--cluster", type=Path, default=LAB_CLUSTER)
    parser.add_argument(
        "--ceph-data", default="/var/lib/ceph", help="filesystem holding the OSD data"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    models = Models.from_dict(json.loads(args.models.read_text()))
    if not models.calibrated:
        raise RuntimeError("uncalibrated models cannot enter a comparison")
    client = Client(args.cluster, models)
    files = {
        "cluster_file": str(args.cluster),
        "cluster_sha256": file_sha256(args.cluster),
        "models_sha256": file_sha256(args.models),
    }
    if args.validation:
        evidence = json.loads(args.validation.read_text())
        require_validated(evidence, client.store.cluster.get_fsid(), args.bandwidth_mbit)
        rate = min(evidence["aggregate_mbit"].values()) * 1e6 / 8
        network = NetworkModel((rate,) * models.node_count, (rate,) * models.node_count)
        client.models = Models(models.compute, models.storage, network, True, models.calibration_id)
    policies = args.policies.split(",")
    settings = {
        "seed": args.seed,
        "budget_ms": args.budget_ms,
        "ceph_data": args.ceph_data,
        "warm": not args.no_warm,
        "files": files,
        "rows": args.output / "rows.jsonl",
    }
    args.output.mkdir(parents=True, exist_ok=False)
    if args.workload == "droid":
        if args.droid_shards is None:
            parser.error("--droid-shards is required for the droid workload")
        require_function_costs(
            client.models, (droid.FUSED,) if args.droid_fused else droid.FUNCTIONS
        )
        names = [droid_ingest.dataset_name(args.resolution, shard) for shard in args.droid_shards]
        first, last = args.droid_shards[0], args.droid_shards[-1]
        settings["dataset"] = f"droid-{args.resolution}-{first:05d}..{last:05d}"
        partitions = droid_workload.load_partitions(client, names)
        plan = droid_workload.waves(partitions, int(args.wave_gib * 2**30))
        for number, wave in enumerate(plan):
            case = {
                "shape": "droid",
                "resolution": args.resolution,
                "fused": args.droid_fused,
                "wave": number,
                "waves": len(plan),
                "steps": list(wave.steps),
                "sources": [list(source) for source in wave.sources],
            }
            rows = make_rows(policies, args.repeats, args.seed + number)
            directory = args.output / f"wave-{number:04d}"
            run_case(client, wave.dataset, case, rows, settings, args, directory)
    else:
        if args.iterations is None or args.ratio is None:
            parser.error("--iterations and --ratio are required for this workload")
        source_bytes = int((args.source_gib or args.dataset_gib) * 2**30)
        placement = [int(node) for node in args.placement.split(",")]
        dataset = synthetic_dataset(
            client, source_bytes, args.shard_mib * 2**20, args.ceph_data, args.dataset, placement
        )
        if args.source_gib:
            dataset = prefix(dataset, int(args.dataset_gib * 2**30))
        case = {"shape": args.workload, "iterations": args.iterations, "ratio": args.ratio}
        rows = make_rows(policies, args.repeats, args.seed)
        run_case(client, dataset, case, rows, settings, args, args.output)
    client.store.close()
    print(json.dumps({"complete": str(args.output)}), flush=True)


def run_case(client, dataset, case, rows, settings, args, directory):
    directory.mkdir(parents=True, exist_ok=True)
    config = {
        "args": {key: str(value) for key, value in vars(args).items()},
        "case": case,
        "rows": rows,
        "models": client.models.to_dict(),
        "dataset": dataset.to_dict(),
    }
    (directory / "config.json").write_text(json.dumps(config, indent=2))
    run_matrix(client, dataset, case, rows, settings, directory)


if __name__ == "__main__":
    main()
