#!/bin/sh
# Stage C after runjob demo3-stage-b on aakashpc. Waits for stage B to exit 0 and goes on
# only then; on any other ending (non-zero exit, lost run, no finish by the start deadline)
# it exits 1 without touching the lab. Then: move the clone to the cs2 commit (fetched from
# the -next repository, which holds it as branch cs2), deploy it into all 17 cephdag8
# containers with the tree-hash check, check the derived models and derive the cs2 ones
# beside them, verify cs, run demo3-slots-droid and then demo3-slots (a failed one is
# reported and the next still runs), and leave the lab in cs mode with the DROID functions
# loaded. GATE_ONLY=1 stops after the gate (used by the tests).
set -eu

commit=3bc0c2814377a2c03280e659d59626e159a11b68
tools=/mnt/mass/cephdag/tools
clone=$HOME/experiments/cephdag-demo3
next=$HOME/experiments/cephdag-demo3-next
deploy=/mnt/mass/cephdag/deploy
calibration=/mnt/mass/cephdag/calibration
derived=/mnt/mass/cephdag/derived-models
cs2_models_sha256=b867a596e14614c1434a77ec7a95d156ee1dd47ff251d8ca2d42be521dcc4148
lab=infra/labs/cephdag8.json
min_free_kib=$((300 * 1024 * 1024))
runs=${STAGE_B_RUNS:-$HOME/runs}
deadline=${START_DEADLINE:-$(TZ=America/New_York date -d '2026-11-15 08:00' +%s)}
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

stage_b=""
for candidate in "$runs"/demo3-stage-b-*; do
    if [ -d "$candidate" ]; then
        stage_b=$candidate
    fi
done
if [ -z "$stage_b" ]; then
    fail "no stage B run directory $runs/demo3-stage-b-*"
fi
name=$(basename "$stage_b")
pattern=${STAGE_B_PATTERN:-"$name/run\\.sh|tools/demo3-stage-b\\.sh|experiments/campaigns/(sched-pilot|sched-skew|sched-uniform|demo3-overlap-sweep|demo3-scale)\\.json|infra/lab\\.py --lab infra/labs/cephdag8\\.json"}
say "stage B run: $stage_b"

waited=0
CHAIN_RUN=$stage_b CHAIN_PATTERN=$pattern START_DEADLINE=$deadline POLL_SECONDS=$poll \
    sh "$tools/demo3-wait-chain.sh" || waited=$?
case $waited in
    0) ;;
    2) fail "stage B exited $(cat "$stage_b/exit_code"); the lab is left as it is" ;;
    *) fail "stage B was lost or still running at the start deadline; the lab is left as it is" ;;
esac
grep -q 'stage B complete' "$stage_b/output.log" ||
    fail "stage B exited 0 but its log does not end in 'stage B complete'"
say "stage B exited 0"
if [ "${GATE_ONLY:-0}" = 1 ]; then
    exit 0
fi

say "free space and Ceph state before stage C"
record_state
free_kib=$(df -k --output=avail / | tail -n 1)
if [ "$free_kib" -lt "$min_free_kib" ]; then
    fail "under 300 GiB free on /"
fi

cd "$clone"
old=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    fail "$clone has uncommitted changes"
fi
git fetch -q "$next" cs2 || fail "cannot fetch cs2 from $next"
git merge-base --is-ancestor "$old" "$commit" ||
    fail "$commit does not descend from the clone's $old"
git checkout -q -B cs2 "$commit" || fail "cannot check out $commit in $clone"
new=$(git rev-parse HEAD)
if [ "$new" != "$commit" ]; then
    fail "$clone is at $new, not $commit"
fi
say "clone $clone moved from $old to $new (branch cs2)"

previous=$deploy/deployed-commit-$old.json
if [ ! -e "$previous" ]; then
    cp "$deploy/deployed-commit.json" "$previous"
fi
python3 "$tools/deploy_code.py" cephdag8 "$clone" "$deploy/deployed-commit.json" || fail "deploy_code.py failed"
grep -q "\"commit\": \"$commit\"" "$deploy/deployed-commit.json" || fail "deployed-commit.json does not name $commit"
say "deployed $commit to every container; the record of $old is in $previous"

(cd "$derived" && sha256sum -c SHA256SUMS) || fail "derived or calibrated models differ from $derived/SHA256SUMS"
cs2_models=$derived/cephdag8-cs2/models.json
if [ ! -e "$cs2_models" ]; then
    python3 -m experiments.derive_models "$calibration/cephdag8-cs/models.json" "$cs2_models" \
        --cluster cephdag/clusters/cephdag8-cs2.json --fused || fail "cannot derive the cs2 models"
fi
echo "$cs2_models_sha256  $cs2_models" | sha256sum -c || fail "$cs2_models differs from the models derived on JarvisVM"
say "derived models in place, cs2 included"

say "verify cs"
python3 infra/lab.py --lab "$lab" verify cs || fail "verify cs failed"

failed=""
run_campaign demo3-slots-droid || true
run_campaign demo3-slots || true

say "putting the lab in cs mode with the DROID functions loaded"
python3 infra/lab.py --lab "$lab" mode cs --functions cephdag.droid || fail "mode cs failed"
record_state
if [ -n "$failed" ]; then
    fail "campaigns that failed:$failed"
fi
say "stage C complete"
