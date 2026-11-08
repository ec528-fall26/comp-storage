#!/usr/bin/env bash
# Claim: a user describes the slice of DROID they want in JSON, sends it over
# HTTP, and the storage side filters the shards where they live and keeps
# only the matching episodes, as a valid RLDS dataset.
#
#   client --HTTP--> API server --job file--> queue --> service --> worker
#                                                        (droid_rlds_slice)
#
# Where: one Linux machine (WSL is fine); a local folder stands in for CephFS.
# Needs: python3 with tensorflow-cpu, tensorflow-datasets, fastapi, uvicorn
#        (PYTHON=... to choose it), and DROID shards under $CSTORE_ROOT
#        (bin/cstore load-droid --shards 2), or SYNTH=1 for synthetic shards.
# Usage: CSTORE_ROOT=~/cstore-local experiments/10_demo3_local.sh [request.json]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON=${PYTHON:-python3}
PORT=${PORT:-8000}
export CSTORE_ENV=/dev/null
export CSTORE_ROOT=${CSTORE_ROOT:-$HOME/cstore-local}
export SCRATCH_DIR=${SCRATCH_DIR:-${TMPDIR:-/tmp}/cstore-scratch}
export CSTORE_RUNTIME=venv WORKER_PYTHON=$PYTHON
export PYTHONPATH="$REPO/src" TF_CPP_MIN_LOG_LEVEL=3
API="http://localhost:$PORT"
DATA="$CSTORE_ROOT/datasets/droid/rlds/1.0.1"
LOGS="$SCRATCH_DIR/demo-logs"; mkdir -p "$LOGS"
banner() { printf '\n=== %s\n' "$*"; }

if ! ls "$DATA"/droid_101-train.tfrecord-* >/dev/null 2>&1; then
  if [ "${SYNTH:-0}" = 1 ]; then
    banner "no shards found; writing synthetic shards 2 and 8"
    "$PYTHON" -m cstore.functions.synthetic_droid --out "$CSTORE_ROOT/datasets/droid/rlds" \
      --shards 2 8 --episodes-per-shard 8 --min-steps 20 --max-steps 30 >/dev/null 2>&1
  else
    echo "no DROID shards in $DATA; run: bin/cstore load-droid --shards 2   (or SYNTH=1)" >&2; exit 1
  fi
fi

banner "data: $(ls "$DATA" | grep -c tfrecord) shard(s), $(du -sh "$DATA" | cut -f1) in $DATA"

banner "starting the background service and the API server"
"$PYTHON" -m cstore service --poll 1 >"$LOGS/service.log" 2>&1 & SVC=$!
"$PYTHON" -m uvicorn cstore.api:app --port "$PORT" >"$LOGS/api.log" 2>&1 & APIPID=$!
trap 'kill $SVC $APIPID 2>/dev/null || true' EXIT
for _ in $(seq 30); do curl -sf "$API/health" >/dev/null && break; sleep 0.5; done
curl -s "$API/health"; echo

if [ $# -ge 1 ]; then REQ=$(cat "$1"); else
  REQ='{"select": {"task_keywords": ["put", "place", "pour"], "object_keywords": ["cup", "mug", "bowl"], "max_episodes": 20}}'
fi
banner "POST /jobs"
echo "$REQ"
RESP=$(curl -s -X POST "$API/jobs" -H 'Content-Type: application/json' -d "$REQ"); echo "$RESP"
ID=$("$PYTHON" -c 'import sys, json; print(json.load(sys.stdin)["job_id"])' <<<"$RESP")

banner "GET /jobs/$ID  (polling)"
LAST=""
while :; do
  ST=$(curl -s "$API/jobs/$ID")
  S=$("$PYTHON" -c 'import sys, json; print(json.load(sys.stdin)["status"])' <<<"$ST")
  [ "$S" != "$LAST" ] && echo "  $S" && LAST=$S
  case $S in done|failed) break ;; esac
  sleep 1
done
"$PYTHON" -m json.tool <<<"$ST"
[ "$S" = done ] || { tail -20 "$LOGS/service.log"; exit 1; }

banner "GET /jobs/$ID/result  (manifest; the slice itself stays in storage)"
curl -s -OJ --output-dir "$LOGS" "$API/jobs/$ID/result" && echo "  saved $LOGS/$ID-manifest.json"

banner "check: the slice loads with the standard RLDS/TFDS reader"
"$PYTHON" - "$CSTORE_ROOT/outputs/slices/$ID" "$LOGS/$ID-manifest.json" <<'EOF' 2>&1 | grep -v -E "^(WARNING|I0000|W0000)"
import json, sys
import tensorflow_datasets as tfds
out, manifest = sys.argv[1], json.load(open(sys.argv[2]))
ds = tfds.builder_from_directory(out).as_dataset(split="train")
n = 0
for ep in ds:
    steps = list(ep["steps"])
    instr = steps[0]["language_instruction"].numpy().decode()
    print(f"  {len(steps):4d} steps  {ep['episode_metadata']['file_path'].numpy().decode().split('/')[-5]:>10}  {instr!r}")
    n += 1
mb = lambda b: f"{b / 1e6:,.1f} MB"
print(f"\n  loaded {n} episodes (manifest says {manifest['episodes_kept']}) out of {manifest['episodes_scanned']} scanned")
print(f"  bytes scanned in storage: {mb(manifest['bytes_scanned'])}   slice: {mb(manifest['bytes_out'])}"
      f"   ({manifest['bytes_out'] / max(manifest['bytes_scanned'], 1):.1%})")
assert n == manifest["episodes_kept"], "episode count mismatch"
print("  DEMO OK")
EOF
