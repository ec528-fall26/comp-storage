"""Models derived from calibrated ones, without calibrating again.

    python3 -m experiments.derive_models SOURCE OUTPUT [--cluster CLUSTER] [--fused]

--cluster: in lab modes cs2 and disagg2 every container that runs workers runs two, and the
cluster file lists each second worker as one more node, without a pool, after all the
others (Cluster.hosts). SOURCE describes one node per host (the models of cs or disagg);
each node of CLUSTER gets the rates of its host.

--fused: adds a cost for droid_fused@1, the unfused tasks' predicted seconds per source byte
of a partition whose frames are --frames-per-source times its source (2.16 measured on
cephdag8; droid_workload.fused_cost).
"""

import argparse
from collections import Counter
import dataclasses
import json
from pathlib import Path

from cephdag import droid
from cephdag.cluster import Cluster
from cephdag.model import Models, NetworkModel, StorageModel
from experiments import droid_workload


def with_hosts(models, hosts):
    """`models` for a cluster whose node i runs on the host that is node hosts[i] of `models`."""
    if sorted(set(hosts)) != list(range(models.node_count)):
        raise ValueError(f"the models describe {models.node_count} hosts, not {sorted(set(hosts))}")

    def repeat(rates):
        return tuple(rates[host] for host in hosts)

    slots = max(Counter(hosts).values())
    return dataclasses.replace(
        models,
        storage=StorageModel(repeat(models.storage.read_bps), repeat(models.storage.write_bps)),
        network=NetworkModel(repeat(models.network.tx_bps), repeat(models.network.rx_bps)),
        calibration_id=f"{models.calibration_id}+slots{slots}",
    )


def with_fused(models, frames_per_source):
    cost = droid_workload.fused_cost(models.compute, frames_per_source)
    functions = dict(models.compute.functions)
    functions[droid.FUSED] = cost
    compute = dataclasses.replace(models.compute, functions=tuple(sorted(functions.items())))
    return dataclasses.replace(
        models, compute=compute, calibration_id=f"{models.calibration_id}+fused"
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cluster", type=Path)
    parser.add_argument("--fused", action="store_true")
    parser.add_argument("--frames-per-source", type=float, default=2.16)
    args = parser.parse_args()
    models = Models.from_dict(json.loads(args.source.read_text()))
    if args.cluster:
        models = with_hosts(models, Cluster.load(args.cluster).hosts)
    if args.fused:
        models = with_fused(models, args.frames_per_source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "x") as output:
        output.write(json.dumps(models.to_dict(), indent=2) + "\n")
    print(f"wrote {args.output}: {models.node_count} nodes, {models.calibration_id}")
    if args.fused:
        cost = models.compute.cost(droid.FUSED)
        print(f"{droid.FUSED}: {cost.input_seconds_per_byte * 1e9:.1f} s per source GB")


if __name__ == "__main__":
    main()
