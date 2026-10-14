#!/usr/bin/env bash
# Claim (the separate-compute baseline): converting DROID shards on a compute
# node separate from storage moves roughly input + output bytes across hosts.
# For each shard: caches dropped on both nodes, NIC counters read on both,
# the job runs in the worker container on THIS (compute) node, reading the
# shard from CephFS and writing the LeRobot dataset back to CephFS, counters
# read again. Starts a new benchmark run (RUN_ID) that 04, 05 and 07 reuse.
#
# Where: the compute node.
# Runtime: ~8 min per shard on m6i.xlarge (estimate: the converter is
#        CPU-bound at roughly 20-25 steps/s on 2-3 vCPUs; shard 2 has ~10k steps).
# Expected output: "ok; wall ... s; cross-host ... MB; output ... MB;
#        episodes N of N" per shard. Match: cross-host between 1.0x and 1.15x
#        of (input 649 MB + output) -- every input byte crosses the network
#        once and the output once more, plus protocol overhead.
# shellcheck source=experiments/common.sh
source "$(dirname "$0")/common.sh"
require_role compute

RUN_ID=${RUN_ID:-demo2-$(date +%Y%m%d-%H%M%S)}
mkdir -p "$(ROOT_DIR)/results"
echo "$RUN_ID" > "$(current_run_file)"
SHARDS=${SHARDS:-$(cfg droid_shards)}

stop_service_on_storage
banner "run $RUN_ID: separate compute (worker on $(hostname)), shards $SHARDS"
# shellcheck disable=SC2086
"$CSTORE" bench --mode separate --run-id "$RUN_ID" --shards $SHARDS
