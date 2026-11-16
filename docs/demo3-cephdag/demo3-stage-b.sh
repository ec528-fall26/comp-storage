#!/bin/sh
# Stage B after runjob demo3-stage-a on aakashpc. Waits for stage A to exit 0 and goes on
# only then; on any other ending (non-zero exit, lost run, no finish by the start deadline)
# it exits 1 without touching the lab. Then: move the clone to the schedulers commit
# (fetched from the -next repository, which holds it as branch schedulers), deploy it into
# all 17 cephdag8 containers with the tree-hash check, check the derived models, verify cs,
# run sched-pilot, sched-skew, sched-uniform, demo3-overlap-sweep and demo3-scale in that
# order (a failed one is reported and the next still runs, except that a failed sched-pilot
# skips sched-skew and sched-uniform, which run the same new policies for hours), and leave
# the lab in cs mode with workers up. GATE_ONLY=1 stops after the gate (used by the tests).
set -eu

commit=cbd15615a606b0137279ce6c78114cde3ece978b
tools=/mnt/mass/cephdag/tools
clone=$HOME/experiments/cephdag-demo3
next=$HOME/experiments/cephdag-demo3-next
deploy=/mnt/mass/cephdag/deploy
derived=/mnt/mass/cephdag/derived-models
lab=infra/labs/cephdag8.json
min_free_kib=$((300 * 1024 * 1024))
runs=${STAGE_A_RUNS:-$HOME/runs}
deadline=${START_DEADLINE:-$(TZ=America/New_York date -d '2026-11-15 06:00' +%s)}
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

stage_a=""
for candidate in "$runs"/demo3-stage-a-*; do
    if [ -d "$candidate" ]; then
        stage_a=$candidate
    fi
done
if [ -z "$stage_a" ]; then
    fail "no stage A run directory $runs/demo3-stage-a-*"
fi
name=$(basename "$stage_a")
pattern=${STAGE_A_PATTERN:-"$name/run\\.sh|tools/demo3-stage-a\\.sh|experiments/campaigns/demo3-sensitivity-(sweep|droid)\\.json|infra/lab\\.py --lab infra/labs/cephdag8\\.json"}
say "stage A run: $stage_a"

waited=0
CHAIN_RUN=$stage_a CHAIN_PATTERN=$pattern START_DEADLINE=$deadline \
    sh "$tools/demo3-wait-chain.sh" || waited=$?
case $waited in
    0) ;;
    2) fail "stage A exited $(cat "$stage_a/exit_code"); the lab is left as it is" ;;
    *) fail "stage A was lost or still running at the start deadline; the lab is left as it is" ;;
esac
grep -q 'stage A complete' "$stage_a/output.log" ||
    fail "stage A exited 0 but its log does not end in 'stage A complete'"
say "stage A exited 0"
if [ "${GATE_ONLY:-0}" = 1 ]; then
    exit 0
fi

say "free space and Ceph state before stage B"
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
git fetch -q "$next" schedulers || fail "cannot fetch schedulers from $next"
git merge-base --is-ancestor "$old" "$commit" ||
    fail "$commit does not descend from the clone's $old"
git checkout -q -B schedulers "$commit" || fail "cannot check out $commit in $clone"
new=$(git rev-parse HEAD)
if [ "$new" != "$commit" ]; then
    fail "$clone is at $new, not $commit"
fi
say "clone $clone moved from $old to $new (branch schedulers)"

previous=$deploy/deployed-commit-$old.json
if [ ! -e "$previous" ]; then
    cp "$deploy/deployed-commit.json" "$previous"
fi
python3 "$tools/deploy_code.py" cephdag8 "$clone" "$deploy/deployed-commit.json" || fail "deploy_code.py failed"
grep -q "\"commit\": \"$commit\"" "$deploy/deployed-commit.json" || fail "deployed-commit.json does not name $commit"
say "deployed $commit to every container; the record of $old is in $previous"

(cd "$derived" && sha256sum -c SHA256SUMS) || fail "derived or calibrated models differ from $derived/SHA256SUMS"
say "derived models in place"

say "verify cs"
python3 infra/lab.py --lab "$lab" verify cs || fail "verify cs failed"

failed=""
if run_campaign sched-pilot; then
    run_campaign sched-skew || true
    run_campaign sched-uniform || true
else
    say "sched-skew and sched-uniform skipped: they run the policies the pilot failed on"
    failed="$failed sched-skew(skipped) sched-uniform(skipped)"
fi
run_campaign demo3-overlap-sweep || true
run_campaign demo3-scale || true

say "putting the lab in cs mode with the DROID functions loaded"
python3 infra/lab.py --lab "$lab" mode cs --functions cephdag.droid || fail "mode cs failed"
record_state
if [ -n "$failed" ]; then
    fail "campaigns that failed:$failed"
fi
say "stage B complete"
