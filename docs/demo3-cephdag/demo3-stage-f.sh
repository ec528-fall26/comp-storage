#!/bin/sh
# Stage F after runjob demo3-stage-e on aakashpc. Waits for stage E to exit 0 and goes on
# only then; on any other ending (non-zero exit, lost run, no finish by the start deadline)
# it exits 1 without touching the lab. Then: move the clone from stage E's mcts-lab commit to
# the gumbel commit (fetched from a bundle), deploy its code into all 17 cephdag8 containers
# with the tree-hash check, and its Rust planner library, which changes the ABI from the
# image's 5 to 7 and which deploy_code.py does not copy, with a sha256 and load check in every
# container; check the calibrated and derived models, check that gumbel-lb-32 plans in the
# coordinator, verify cs, run sched-gumbel (Gumbel root search against restarts, fair-forecast
# MCTS and beam, and heft-cc on the skewed 8 GiB), and leave the lab in cs mode with the DROID
# functions loaded. GATE_ONLY=1 stops after the gate (used by the tests).
set -eu

commit=925ababcb13fb3248ae821574a0af05d9193681c
previous_commit=8cdc2b4186b03050a81ad1c327304a03b2436d7d
library_sha256=8c09d583f5848da80b4d46670c6bc32dc414a32c0234b881c74252b71dabe31d
abi=7
tools=${STAGE_TOOLS:-/mnt/mass/cephdag/tools}
bundle=$tools/cephdag-gumbel-925abab.bundle
library=$tools/libcephdag_native-925abab.so
clone=$HOME/experiments/cephdag-demo3
deploy=/mnt/mass/cephdag/deploy
derived=/mnt/mass/cephdag/derived-models
lab=infra/labs/cephdag8.json
min_free_kib=$((300 * 1024 * 1024))
runs=${STAGE_E_RUNS:-$HOME/runs}
deadline=${START_DEADLINE:-$(TZ=America/New_York date -d '2026-11-15 20:00' +%s)}
poll=${POLL_SECONDS:-240}
# campaign.py prints its progress without flushing; this keeps output.log current.
export PYTHONUNBUFFERED=1

say() {
    echo "$(TZ=America/New_York date '+%F %T %Z') $*"
}

fail() {
    say "stopping: $*"
    exit 1
}

record_state() {
    df -h / /mnt/mass
    timeout 120 docker exec cephdag8-n0 ceph health detail || say "ceph health detail failed"
    timeout 120 docker exec cephdag8-n0 ceph df || say "ceph df failed"
}

run_campaign() {
    say "$1 started"
    if python3 experiments/campaign.py run "experiments/campaigns/$1.json"; then
        say "$1 complete"
    else
        say "$1 failed; the reason is in /mnt/mass/cephdag/campaigns/$1/ledger.jsonl"
        failed="$failed $1"
        return 1
    fi
}

stage_e=""
for candidate in "$runs"/demo3-stage-e-*; do
    if [ -d "$candidate" ]; then
        stage_e=$candidate
    fi
done
if [ -z "$stage_e" ]; then
    fail "no stage E run directory $runs/demo3-stage-e-*"
fi
name=$(basename "$stage_e")
pattern=${STAGE_E_PATTERN:-"$name/run\\.sh|tools/demo3-stage-e\\.sh|experiments/campaigns/sched-mcts\\.json|infra/lab\\.py --lab infra/labs/cephdag8\\.json"}
say "stage E run: $stage_e"

waited=0
CHAIN_RUN=$stage_e CHAIN_PATTERN=$pattern START_DEADLINE=$deadline POLL_SECONDS=$poll \
    sh "$tools/demo3-wait-chain.sh" || waited=$?
case $waited in
    0) ;;
    2) fail "stage E exited $(cat "$stage_e/exit_code"); the lab is left as it is" ;;
    *) fail "stage E was lost or still running at the start deadline; the lab is left as it is" ;;
esac
grep -q 'stage E complete' "$stage_e/output.log" ||
    fail "stage E exited 0 but its log does not end in 'stage E complete'"
say "stage E exited 0"
if [ "${GATE_ONLY:-0}" = 1 ]; then
    exit 0
fi

say "free space and Ceph state before stage F"
record_state
free_kib=$(df -k --output=avail / | tail -n 1)
if [ "$free_kib" -lt "$min_free_kib" ]; then
    fail "under 300 GiB free on /"
fi

echo "$library_sha256  $library" | sha256sum -c - || fail "$library is not the library built from $commit"

cd "$clone"
old=$(git rev-parse HEAD)
if [ "$old" != "$previous_commit" ]; then
    fail "$clone is at $old, not stage E's $previous_commit"
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    fail "$clone has uncommitted changes"
fi
git fetch -q "$bundle" gumbel || fail "cannot fetch gumbel from $bundle"
git checkout -q -B gumbel "$commit" || fail "cannot check out $commit in $clone"
new=$(git rev-parse HEAD)
if [ "$new" != "$commit" ]; then
    fail "$clone is at $new, not $commit"
fi
say "clone $clone moved from $old to $new (branch gumbel)"

previous=$deploy/deployed-commit-$old.json
if [ ! -e "$previous" ]; then
    cp "$deploy/deployed-commit.json" "$previous"
fi
python3 "$tools/deploy_code.py" cephdag8 "$clone" "$deploy/deployed-commit.json" || fail "deploy_code.py failed"
grep -q "\"commit\": \"$commit\"" "$deploy/deployed-commit.json" || fail "deployed-commit.json does not name $commit"
say "deployed $commit to every container; the record of $old is in $previous"
python3 "$tools/deploy_native.py" cephdag8 "$library" "$abi" "$deploy/deployed-native.json" ||
    fail "deploy_native.py failed; see $deploy/deployed-native.json"
say "deployed the planner library $library_sha256 (ABI $abi) to every container"

(cd "$derived" && sha256sum -c SHA256SUMS) || fail "derived or calibrated models differ from $derived/SHA256SUMS"

say "gumbel-lb-32 plans one decision in the coordinator"
docker exec -i cephdag8-n0 python3 - << 'EOF' || fail "gumbel-lb-32 cannot plan in cephdag8-n0"
from cephdag.graph import Graph, ObjectRef
from cephdag.model import Models, Simulation
from cephdag.policies import make_policy

graph = Graph()
for index in range(4):
    graph.add("map@1", [ObjectRef("node0", f"d/{index}", 64 * 2**20)], ratios=[0.25])
pools = tuple(f"node{node}" for node in range(8))
for name in ("gumbel-lb-32", "restarts-32", "native-mcts-fair-128", "beam-fair-4"):
    policy = make_policy(name)
    print(name, policy.choose(Simulation(graph, Models.uncalibrated(8), pools)))
    policy.close()
EOF

say "verify cs"
python3 infra/lab.py --lab "$lab" verify cs || fail "verify cs failed"

failed=""
run_campaign sched-gumbel || true

say "putting the lab in cs mode with the DROID functions loaded"
python3 infra/lab.py --lab "$lab" mode cs --functions cephdag.droid || fail "mode cs failed"
record_state
if [ -n "$failed" ]; then
    fail "campaigns that failed:$failed"
fi
say "stage F complete"
