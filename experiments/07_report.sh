#!/usr/bin/env bash
# Produces the numbers for the slides: one row per job and mode (episodes,
# frames, input/output size, wall time split into convert and write-back,
# cross-host bytes at the storage node's NIC, verification), totals per mode,
# the cross-host reduction and runtime ratio, and output equivalence.
# Saved as results/<run>/summary.md, summary.json and summary.svg (chart).
#
# Where: the compute node, after 03-05.
# Runtime: seconds.
# Expected output: a Markdown table; the storage-node row has near-zero
#        cross-host MB, and the closing lines give the reduction (close to
#        100% for full locality) and the runtime ratio.
# shellcheck source=experiments/common.sh
source "$(dirname "$0")/common.sh"

RUN_ID=$(current_run)
banner "run $RUN_ID"
"$CSTORE" report --run-id "$RUN_ID"
echo "saved: $(ROOT_DIR)/results/$RUN_ID/summary.{md,json,svg}"
