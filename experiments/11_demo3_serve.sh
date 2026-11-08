#!/usr/bin/env bash
# Start the storage side of Demo 3 and leave it running, for live requests.
#
#   terminal 1:  experiments/11_demo3_serve.sh          (this script; Ctrl+C stops it)
#   terminal 2:  bin/cstore-client --server http://localhost:8000 submit --tasks put --objects mug --wait
#
# Starts the background service (claims jobs from the queue) and the HTTP API
# server, then prints what happens to each job as it arrives. Same settings as
# experiments/10_demo3_local.sh; override them the same way (PYTHON, CSTORE_ROOT, PORT).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON=${PYTHON:-python3}
PORT=${PORT:-8000}
export CSTORE_ENV=/dev/null
export CSTORE_ROOT=${CSTORE_ROOT:-$HOME/cstore-local}
export SCRATCH_DIR=${SCRATCH_DIR:-${TMPDIR:-/tmp}/cstore-scratch}
export CSTORE_RUNTIME=venv WORKER_PYTHON=$PYTHON
export PYTHONPATH="$REPO/src" TF_CPP_MIN_LOG_LEVEL=3
LOGS="$SCRATCH_DIR/demo-logs"; mkdir -p "$LOGS"
DATA="$CSTORE_ROOT/datasets/droid/rlds/1.0.1"

ls "$DATA"/droid_101-train.tfrecord-* >/dev/null 2>&1 || {
  echo "no DROID shards in $DATA; run: bin/cstore load-droid --shards 2" >&2; exit 1; }

"$PYTHON" -m uvicorn cstore.api:app --port "$PORT" >"$LOGS/api.log" 2>&1 & APIPID=$!
trap 'kill $APIPID 2>/dev/null || true; echo; echo stopped.' EXIT
for _ in $(seq 30); do curl -sf "http://localhost:$PORT/health" >/dev/null && break; sleep 0.5; done
curl -sf "http://localhost:$PORT/health" >/dev/null || { echo "API server did not start; see $LOGS/api.log" >&2; exit 1; }

cat <<EOF

Storage side is up.
  data:        $(ls "$DATA" | grep -c tfrecord) shard(s) in $DATA
  API server:  http://localhost:$PORT   (log: $LOGS/api.log)
  slices go to $CSTORE_ROOT/outputs/slices/<job_id>

In a second WSL terminal:
  cd $REPO
  export CSTORE_SERVER=http://localhost:$PORT
  bin/cstore-client options
  bin/cstore-client submit --tasks put --objects mug --wait
  bin/cstore-client submit --labs TRI --max-episodes 5 --wait
  bin/cstore-client submit --tasks put,place --objects cup,bowl --dry-run
  bin/cstore-client submit -i
  bin/cstore-client submit --objects marker --download ./slice

Background service log follows (Ctrl+C stops everything):
EOF
exec "$PYTHON" -m cstore service --poll 1
