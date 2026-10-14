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
# Expected output: queue snapshots showing svc-shard-00002 (one job per
#        loaded shard) running and then done, the service's log, then
#        "VERIFY OK" for each output.
#        Re-running finds nothing new to convert; to repeat from scratch,
#        delete outputs/service and queue/ under CSTORE_ROOT first.
# shellcheck source=experiments/common.sh
source "$(dirname "$0")/common.sh"
require_role compute

ROOT=$(ROOT_DIR)
TIMEOUT=${TIMEOUT:-3600}
# The service converts every loaded shard, so wait for all of them.
LOADED=$(find "$ROOT/datasets/droid/rlds/1.0.1" -maxdepth 1 -name 'droid_101-train.tfrecord-*-of-02048' -printf '%f\n' |
         sed -E 's/.*tfrecord-0*([0-9]+)-of-02048/\1/' | sort -n | tr '\n' ' ')
[ -n "$LOADED" ] || { echo "no shards loaded; run experiments/02_load_droid.sh first" >&2; exit 1; }
echo "loaded shards: $LOADED"

banner "start the service on the storage node"
on_storage "sudo systemctl start cstore-service && systemctl --no-pager --lines=0 status cstore-service"

banner "watch the queue (every 20 s)"
start=$(date +%s)
while :; do
  "$CSTORE" status --limit 6
  missing=0
  for i in $LOADED; do
    id=$(printf '%05d' "$i")
    if compgen -G "$ROOT/queue/failed/*-svc-shard-$id.json" >/dev/null; then
      echo "job svc-shard-$id FAILED; details: $ROOT/results/service/svc-shard-$id.json" >&2
      on_storage "sudo systemctl stop cstore-service"
      exit 1
    fi
    [ -f "$ROOT/outputs/service/shard-$id/meta/info.json" ] || missing=$((missing + 1))
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
for i in $LOADED; do
  id=$(printf '%05d' "$i")
  expect=$(python3 -c "import json; print(json.load(open('$ROOT/results/service/svc-shard-$id.json'))['worker']['expected_episodes'])")
  "$CSTORE" verify "$ROOT/outputs/service/shard-$id" --expect-episodes "$expect" \
    --json "$ROOT/results/service/verify-svc-shard-$id.json" || fail=1
done

banner "stop the service (so it cannot interfere with later benchmarks)"
on_storage "sudo systemctl stop cstore-service"
exit $fail
