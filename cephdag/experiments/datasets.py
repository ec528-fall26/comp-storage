"""Create a synthetic source dataset ahead of the runs, or delete source datasets, which
experiment runs never remove.

    python -m experiments.datasets create-synthetic NAME --gib 256 --shard-mib 64 \\
        --placement 0,1,2,3,4,5,6,7 [--cluster FILE]
    python -m experiments.datasets delete NAME... [--cluster FILE]
    python -m experiments.datasets delete --droid-shards 0-78 --resolution 128

Creating a dataset that exists checks that it has the requested shards. A dataset that
does not exist is reported and skipped by delete, so a delete can be repeated.
"""

import argparse
import json

from cephdag.client import Client
from cephdag.cluster import LAB_CLUSTER
from experiments.droid_ingest import dataset_name, parse_range
from experiments.run import synthetic_dataset

DROID_MANIFESTS = ("droid.json",)


def delete(client, names):
    """Delete every dataset in `names`; return {name: shards removed, or None if absent}."""
    removed = {}
    for name in names:
        try:
            removed[name] = client.delete_dataset(name, DROID_MANIFESTS)
        except client.store.not_found:
            removed[name] = None
    return removed


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["create-synthetic", "delete"])
    parser.add_argument("names", nargs="*")
    parser.add_argument("--droid-shards", type=parse_range, default=range(0))
    parser.add_argument("--resolution", type=int, default=128)
    parser.add_argument("--gib", type=float, help="create-synthetic: total size")
    parser.add_argument("--shard-mib", type=int, help="create-synthetic: shard size")
    parser.add_argument("--placement", help="create-synthetic: node of each shard, cycled")
    parser.add_argument(
        "--ceph-data", default="/var/lib/ceph", help="filesystem holding the OSD data"
    )
    parser.add_argument("--cluster", default=LAB_CLUSTER)
    args = parser.parse_args()
    names = args.names + [dataset_name(args.resolution, shard) for shard in args.droid_shards]
    if not names:
        parser.error("name at least one dataset")
    if args.command == "create-synthetic" and (
        len(names) != 1 or None in (args.gib, args.shard_mib, args.placement)
    ):
        parser.error("create-synthetic needs one name, --gib, --shard-mib and --placement")
    client = Client(args.cluster)
    try:
        if args.command == "delete":
            print(json.dumps({"deleted": delete(client, names)}), flush=True)
        else:
            placement = [int(node) for node in args.placement.split(",")]
            dataset = synthetic_dataset(
                client,
                int(args.gib * 2**30),
                args.shard_mib * 2**20,
                args.ceph_data,
                names[0],
                placement,
            )
            print(json.dumps({"dataset": names[0], "shards": len(dataset.shards)}), flush=True)
    finally:
        client.store.close()


if __name__ == "__main__":
    main()
