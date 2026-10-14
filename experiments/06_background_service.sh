#!/usr/bin/env bash
# Claim: the background conversion service converts loaded data with no
# manual step. We only start the service on the storage node. It notices
# that the shards loaded in 02 have no LeRobot version under
# outputs/service/, queues one job per shard in the CephFS-backed queue,
# runs them in worker containers on the storage node, and records each one.
# We watch the queue from the compute node, then verify the outputs.
#
# Where: the compute node (the service runs on the storage node).
# Runtime: ~8 min per loaded shard (default 1 shard), plus ~1 min to verify.
# Expected output: the queue moving from pending -> running -> done for
#        svc-shard-00002 (and one job per other loaded shard), then
#        "VERIFY OK" for each.
#        Re-running finds nothing new to convert; to repeat from scratch,
#        delete outputs/service and queue/ under CSTORE_ROOT first.
# shellcheck source=experiments/common.sh
source "$(dirname "$0")/common.sh"
require_role compute

ROOT=$(ROOT_DIR)
SHARDS=${SHARDS:-$(cfg droid_shards)}
TIMEOUT=${TIMEOUT:-2400}

banner "start the service on the storage node"
on_storage "sudo systemctl start cstore-service && systemctl --no-pager --lines=0 status cstore-service"

banner "watch the queue (every 20 s)"
start=$(date +%s)
while :; do
  "$CSTORE" status --limit 6
  missing=0
  for i in $SHARDS; do
    [ -f "$ROOT/outputs/service/$(printf 'shard-%05d' "$i")/meta/info.json" ] || missing=$((missing + 1))
  done
  [ "$missing" -eq 0 ] && break
  if [ $(( $(date +%s) - start )) -gt "$TIMEOUT" ]; then
    echo "timed out after ${TIMEOUT}s; see: ssh $(cfg storage_host) journalctl -u cstore-service" >&2
    exit 1
  fi
  sleep 20
  echo
done

banner "service log (storage node)"
on_storage "journalctl -u cstore-service --no-pager -o cat --since '-2h' | tail -n 20"

banner "verify the service's outputs"
fail=0
for i in $SHARDS; do
  job="svc-shard-$(printf '%05d' "$i")"
  expect=$(python3 -c "import json; print(json.load(open('$ROOT/results/service/$job.json'))['worker']['expected_episodes'])")
  "$CSTORE" verify "$ROOT/outputs/service/$(printf 'shard-%05d' "$i")" --expect-episodes "$expect" \
    --json "$ROOT/results/service/verify-$job.json" || fail=1
done

banner "stop the service (so it cannot interfere with later benchmarks)"
on_storage "sudo systemctl stop cstore-service"
exit $fail
