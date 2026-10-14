#!/usr/bin/env bash
# Run every cluster experiment in order (01 through 07) on the compute node.
# Total runtime with the default single shard: ~30 min.
set -euo pipefail
cd "$(dirname "$0")"
for script in 01_check_cluster.sh 02_load_droid.sh 03_baseline_separate.sh 04_storage_node.sh \
              05_verify_outputs.sh 06_background_service.sh 07_report.sh; do
  printf '\n\n################ %s\n' "$script"
  "./$script"
done
