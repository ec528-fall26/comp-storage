#!/usr/bin/env bash
# Claim (the separate-compute baseline): converting DROID shards on a compute
# node separate from storage moves roughly input + output bytes across hosts.
# For each shard: caches dropped on both nodes, NIC counters read on both,
# the job runs in the worker container on THIS (compute) node, reading the
# shard from CephFS and writing the LeRobot dataset back to CephFS, counters
# read again. Starts a new benchmark run (RUN_ID) that 04, 05 and 07 reuse.
#
# Where: the compute node.
# Runtime: ~5-8 min per shard (shard 2, 9,829 steps, took 304 s with 7 worker
#        CPUs; the converter is CPU-bound at ~65 ms of CPU per step).
# Expected output: "ok; wall ... s; cross-host ... MB; output ... MB;
#        episodes 39 of 39" for shard 2. Match: cross-host between 1.0x and
#        1.15x of input + output (649 MB + ~142 MB, so ~790-910 MB) -- every
#        input byte crosses the network once and the output once more, plus
#        protocol overhead.
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
