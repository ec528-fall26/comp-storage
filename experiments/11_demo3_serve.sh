#!/usr/bin/env bash
# Start the storage side of Demo 3 on this machine and leave it running.
#
#   terminal 1:  experiments/11_demo3_serve.sh                (Ctrl+C stops it)
#   terminal 2:  bin/cstore-client --keywords put,mug --max-episodes 20
#
# Starts the HTTP API server and the background service; a local folder
# ($CSTORE_ROOT) stands in for CephFS. Needs a python with tensorflow-datasets,
# fastapi and uvicorn (set PYTHON), and DROID shards: bin/cstore load-droid --shards 2
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON=${PYTHON:-python3}
PORT=${PORT:-8000}
export CSTORE_ENV=/dev/null CSTORE_RUNTIME=venv WORKER_PYTHON=$PYTHON
export CSTORE_ROOT=${CSTORE_ROOT:-$HOME/cstore-local}
export SCRATCH_DIR=${SCRATCH_DIR:-${TMPDIR:-/tmp}/cstore-scratch}
export PYTHONPATH="$REPO/src" TF_CPP_MIN_LOG_LEVEL=3
DATA="$CSTORE_ROOT/datasets/droid/rlds/1.0.1"

ls "$DATA"/droid_101-train.tfrecord-* >/dev/null 2>&1 || {
  echo "no DROID shards in $DATA; run: bin/cstore load-droid --shards 2" >&2; exit 1; }

mkdir -p "$SCRATCH_DIR"
"$PYTHON" -m uvicorn cstore.api:app --port "$PORT" >"$SCRATCH_DIR/api.log" 2>&1 & API=$!
trap 'kill $API 2>/dev/null || true' EXIT
for _ in $(seq 30); do curl -sf "http://localhost:$PORT/health" >/dev/null && break; sleep 0.5; done

echo "API server on http://localhost:$PORT, data in $DATA"
echo "slices are written to $CSTORE_ROOT/outputs/slices/"
echo "service log (Ctrl+C stops everything):"
"$PYTHON" -m cstore service --poll 1
