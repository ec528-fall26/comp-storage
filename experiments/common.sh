# shellcheck shell=bash
# Shared helpers for the experiment scripts (sourced, not run).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC2034  # used by the scripts that source this file
CSTORE="$REPO/bin/cstore"

# cfg NAME -> a setting from infra/cluster.env / the environment (see src/cstore/config.py)
cfg() {
  PYTHONPATH="$REPO/src" python3 -c "
from cstore import config
v = getattr(config.load(), '$1')
print(' '.join(map(str, v)) if isinstance(v, tuple) else v)"
}

banner() { printf '\n=== %s\n' "$*"; }

require_role() {
  local have
  have=$(cfg role)
  if [ "$have" != "$1" ]; then
    echo "Run this on the $1 node (this host is: ${have:-not set up; see infra/deploy.sh})." >&2
    exit 1
  fi
}

on_storage() {  # run a shell command on the storage node
  ssh -o BatchMode=yes "$(cfg storage_host)" "$@"
}

ROOT_DIR() { cfg root; }

# The benchmark run that experiments 03-05 and 07 share. 03 starts a new one;
# the others reuse it. Override with RUN_ID=... to look at an older run.
current_run_file() { echo "$(ROOT_DIR)/results/CURRENT_RUN"; }
current_run() {
  if [ -n "${RUN_ID:-}" ]; then echo "$RUN_ID"; return; fi
  local f; f=$(current_run_file)
  [ -f "$f" ] || { echo "no benchmark run yet; run experiments/03_baseline_separate.sh first" >&2; exit 1; }
  cat "$f"
}

stop_service_on_storage() {
  if on_storage "systemctl is-active --quiet cstore-service"; then
    echo "stopping the background service on the storage node so it does not compete for CPU"
    on_storage "sudo systemctl stop cstore-service"
  fi
}
