#!/usr/bin/env bash
# Claim: the whole pipeline works end to end. Synthetic shards in the exact
# DROID 1.0.1 RLDS layout are converted to LeRobot format by Hugging Face's
# unmodified port_droid.py inside the worker container -- once directly and
# once through the background service's queue -- written back with an atomic
# publish, and verified through LeRobot's own loader.
#
# Where: any Linux machine with Docker, or a Mac with Docker Desktop (no Ceph,
#        no cloud, no DROID download; a local directory stands in for CephFS).
# Runtime: ~10 min the first time (builds the 4 GB worker image), ~2 min after.
# Expected output: three "VERIFY OK" blocks, the direct and service outputs of
#        shard 2 reported "byte-identical", and finally "SMOKE TEST PASSED".
# shellcheck source=experiments/common.sh
source "$(dirname "$0")/common.sh"

# Docker Desktop on macOS shares the home directory with containers by default.
if [ "$(uname -s)" = Darwin ]; then SMOKE_DIR=${SMOKE_DIR:-$HOME/.cstore-smoke}; fi
SMOKE_DIR=${SMOKE_DIR:-/var/tmp/cstore-smoke}
IMAGE=${WORKER_IMAGE:-cstore-worker:demo2}

banner "worker image $IMAGE"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "present: $(docker image inspect --format '{{.Id}}' "$IMAGE")"
else
  echo "building (one time, ~10 min)"
  # shellcheck disable=SC2086  # DOCKER_BUILD_ARGS: optional extra flags, see src/worker/Dockerfile
  docker build ${DOCKER_BUILD_ARGS:-} -f "$REPO/src/worker/Dockerfile" -t "$IMAGE" "$REPO"
fi

banner "smoke test in $SMOKE_DIR"
mkdir -p "$SMOKE_DIR"
CSTORE_ENV=/dev/null \
CSTORE_ROOT="$SMOKE_DIR/root" \
SCRATCH_DIR="$SMOKE_DIR/scratch" \
CSTORE_RUNTIME=docker \
WORKER_IMAGE="$IMAGE" \
WORKER_CPUS="${WORKER_CPUS:-2}" \
WORKER_MEMORY="${WORKER_MEMORY:-6g}" \
  "$CSTORE" smoke
