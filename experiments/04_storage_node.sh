#!/usr/bin/env bash
# Claim: the same conversion run on the storage node -- same shards, same
# worker image, same CPU and memory limits, same cold caches -- keeps the
# data off the network: cross-host bytes fall to ssh/control traffic only.
# Also reports the runtime next to the baseline's.
#
# Single storage node caveat: here every input is local by construction, so
# this measures the best case (full locality). Partial locality and placement
# across several storage nodes are Demo 3.
#
# Where: the compute node (it starts the job on the storage node over ssh and
#        reads both nodes' NIC counters).
# Runtime: ~8 min per shard (same estimate as 03).
# Expected output: "ok; wall ... s; cross-host ... MB; ...; episodes N of N"
#        per shard. Match: cross-host under 1% of the 03 figure (only ssh and
#        control traffic). Wall time: within ~10% of 03 either way; see the
#        design document for why it is not expected to be faster.
# shellcheck source=experiments/common.sh
source "$(dirname "$0")/common.sh"
require_role compute

RUN_ID=$(current_run)
SHARDS=${SHARDS:-$(cfg droid_shards)}

stop_service_on_storage
banner "run $RUN_ID: on the storage node, shards $SHARDS"
# shellcheck disable=SC2086
"$CSTORE" bench --mode storage --run-id "$RUN_ID" --shards $SHARDS
