#!/usr/bin/env bash
# Claim: both execution modes produce correct and equivalent outputs.
# For every job of the run: the LeRobot dataset loads with LeRobot's own
# loader; its episode count equals the count TensorFlow Datasets records for
# the input shard; frame counts agree across the episode index, the parquet
# data and all three camera videos; first and last frames decode. Then the
# separate-compute and storage-node outputs of each shard are compared file
# by file (SHA-256; JSON compared after sorting keys).
#
# Where: the compute node. Runs in the worker container (needs LeRobot).
# Runtime: ~1-2 min.
# Expected output: "VERIFY OK" for every output, and for each shard
#        "vs ...: byte-identical". If the two nodes' CPUs differed the video
#        encoder could emit different bytes; the report would then say
#        "same content" (decoded data equal) instead.
# shellcheck source=experiments/common.sh
source "$(dirname "$0")/common.sh"
require_role compute

RUN_ID=$(current_run)
banner "verifying run $RUN_ID"
"$CSTORE" verify-run --run-id "$RUN_ID"
