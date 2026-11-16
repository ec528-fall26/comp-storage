#!/bin/sh
# Stage D after runjob demo3-stage-c on aakashpc. Waits for stage C to exit 0 and goes on
# only then; on any other ending (non-zero exit, lost run, no finish by the start deadline)
# it exits 1 without touching the lab. Then: move the clone to the lp-plan2 commit (fetched
# from the -next repository, which holds it as branch lp-plan2), deploy it into all 17
# cephdag8 containers with the tree-hash check, check the calibrated and derived models,
# verify cs, run sched-lp (lp-plan2 against lp-plan and heft-cc on the skewed 8 GiB), and
# leave the lab in cs mode with the DROID functions loaded. GATE_ONLY=1 stops after the gate
# (used by the tests).
set -eu

commit=ac2f85d3fa22c94eebeb3b8e2deda93c6d883eb0
tools=/mnt/mass/cephdag/tools
clone=$HOME/experiments/cephdag-demo3
next=$HOME/experiments/cephdag-demo3-next
deploy=/mnt/mass/cephdag/deploy
derived=/mnt/mass/cephdag/derived-models
lab=infra/labs/cephdag8.json
min_free_kib=$((300 * 1024 * 1024))
runs=${STAGE_C_RUNS:-$HOME/runs}
deadline=${START_DEADLINE:-$(TZ=America/New_York date -d '2026-11-15 12:00' +%s)}
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

stage_c=""
for candidate in "$runs"/demo3-stage-c-*; do
    if [ -d "$candidate" ]; then
        stage_c=$candidate
    fi
done
if [ -z "$stage_c" ]; then
    fail "no stage C run directory $runs/demo3-stage-c-*"
fi
name=$(basename "$stage_c")
pattern=${STAGE_C_PATTERN:-"$name/run\\.sh|tools/demo3-stage-c\\.sh|experiments/campaigns/(demo3-slots-droid|demo3-slots)\\.json|infra/lab\\.py --lab infra/labs/cephdag8\\.json"}
say "stage C run: $stage_c"

waited=0
CHAIN_RUN=$stage_c CHAIN_PATTERN=$pattern START_DEADLINE=$deadline POLL_SECONDS=$poll \
    sh "$tools/demo3-wait-chain.sh" || waited=$?
case $waited in
    0) ;;
    2) fail "stage C exited $(cat "$stage_c/exit_code"); the lab is left as it is" ;;
    *) fail "stage C was lost or still running at the start deadline; the lab is left as it is" ;;
esac
grep -q 'stage C complete' "$stage_c/output.log" ||
    fail "stage C exited 0 but its log does not end in 'stage C complete'"
say "stage C exited 0"
if [ "${GATE_ONLY:-0}" = 1 ]; then
    exit 0
fi

say "free space and Ceph state before stage D"
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
git fetch -q "$next" lp-plan2 || fail "cannot fetch lp-plan2 from $next"
git merge-base --is-ancestor "$old" "$commit" ||
    fail "$commit does not descend from the clone's $old"
git checkout -q -B lp-plan2 "$commit" || fail "cannot check out $commit in $clone"
new=$(git rev-parse HEAD)
if [ "$new" != "$commit" ]; then
    fail "$clone is at $new, not $commit"
fi
say "clone $clone moved from $old to $new (branch lp-plan2)"

previous=$deploy/deployed-commit-$old.json
if [ ! -e "$previous" ]; then
    cp "$deploy/deployed-commit.json" "$previous"
fi
python3 "$tools/deploy_code.py" cephdag8 "$clone" "$deploy/deployed-commit.json" || fail "deploy_code.py failed"
grep -q "\"commit\": \"$commit\"" "$deploy/deployed-commit.json" || fail "deployed-commit.json does not name $commit"
say "deployed $commit to every container; the record of $old is in $previous"

(cd "$derived" && sha256sum -c SHA256SUMS) || fail "derived or calibrated models differ from $derived/SHA256SUMS"

say "verify cs"
python3 infra/lab.py --lab "$lab" verify cs || fail "verify cs failed"

failed=""
run_campaign sched-lp || true

say "putting the lab in cs mode with the DROID functions loaded"
python3 infra/lab.py --lab "$lab" mode cs --functions cephdag.droid || fail "mode cs failed"
record_state
if [ -n "$failed" ]; then
    fail "campaigns that failed:$failed"
fi
say "stage D complete"
