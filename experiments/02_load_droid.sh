#!/usr/bin/env bash
# Claim: the experiments run on fixed, real inputs -- DROID 1.0.1 RLDS shards
# (default: shard 2, 649 MB; SHARDS="2 8" adds shard 8, 686 MB) from
# gs://gresearch/robotics/droid/1.0.1, the files Hugging Face's porting guide
# converts, each checked against a pinned size and MD5 before it becomes
# visible in CephFS.
#
# The storage node downloads them straight into CephFS ("load"). This ingest
# traffic comes from the internet, before any job, and is reported
# separately from job traffic.
#
# Where: the compute node (the download itself runs on the storage node).
# Runtime: ~1 min per shard (one-time; re-running skips files already verified).
# Expected output: one "md5 ok" (or "already present and verified") line per
#        file, then the episode count of each shard, and the files listed from
#        the compute node's CephFS mount.
# shellcheck source=experiments/common.sh
source "$(dirname "$0")/common.sh"
require_role compute

SHARDS=${SHARDS:-$(cfg droid_shards)}
banner "storage node: download shards $SHARDS into CephFS"
# shellcheck disable=SC2086
on_storage "cd $(cfg storage_repo) && ./bin/cstore load-droid --shards $SHARDS"

banner "visible from the compute node through CephFS"
ls -l "$(ROOT_DIR)/datasets/droid/rlds/1.0.1/"
