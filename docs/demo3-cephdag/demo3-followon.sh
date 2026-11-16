#!/bin/sh
# Follow-on to runjob demo3-campaigns on aakashpc. Once its chain has exited 0: move the clone
# to the merged demo3 commit, deploy it into all 17 cephdag8 containers, check the derived
# models, verify cs, run the sensitivity sweep, sensitivity DROID and overlap sweep campaigns
# in that order (a failed one is reported and the next still runs), and leave the lab in cs
# mode with workers up.
set -eu

commit=bb7de4b79a87705f052bcc53cd07ce46ee339872
tools=/mnt/mass/cephdag/tools
clone=$HOME/experiments/cephdag-demo3
next=$HOME/experiments/cephdag-demo3-next
deploy=/mnt/mass/cephdag/deploy
calibration=/mnt/mass/cephdag/calibration
derived=/mnt/mass/cephdag/derived-models
lab=infra/labs/cephdag8.json
campaigns="demo3-sensitivity-sweep demo3-sensitivity-droid demo3-overlap-sweep"
min_free_kib=$((300 * 1024 * 1024))

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

derive() {
    source=$calibration/$1/models.json
    output=$derived/$2/models.json
    shift 2
    if [ ! -e "$output" ]; then
        python3 -m experiments.derive_models "$source" "$output" "$@"
    fi
}

sh "$tools/demo3-wait-chain.sh"

say "free space and Ceph state before the follow-on"
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
git fetch -q "$next" demo3 || fail "cannot fetch demo3 from $next"
git merge -q --ff-only "$commit" || fail "cannot fast-forward $clone from $old to $commit"
new=$(git rev-parse HEAD)
if [ "$new" != "$commit" ]; then
    fail "$clone is at $new, not $commit"
fi
say "clone $clone moved from $old to $new"

previous=$deploy/deployed-commit-$old.json
if [ ! -e "$previous" ]; then
    cp "$deploy/deployed-commit.json" "$previous"
fi
python3 "$tools/deploy_code.py" cephdag8 "$clone" "$deploy/deployed-commit.json" || fail "deploy_code.py failed"
grep -q "\"commit\": \"$commit\"" "$deploy/deployed-commit.json" || fail "deployed-commit.json does not name $commit"
say "deployed $commit to every container; the record of $old is in $previous"

derive cephdag8-disagg cephdag8-disagg2 --slots 2 --storage-nodes 8 --fused
derive cephdag8-cs cephdag8-cs-fused --fused
derive cephdag8-disagg cephdag8-disagg-fused --fused
(cd "$derived" && sha256sum -c SHA256SUMS) || fail "derived or calibrated models differ from $derived/SHA256SUMS"
say "derived models in place"

say "verify cs"
python3 infra/lab.py --lab "$lab" verify cs || fail "verify cs failed"

failed=""
for name in $campaigns; do
    say "$name started"
    if python3 experiments/campaign.py run "experiments/campaigns/$name.json"; then
        say "$name complete"
    else
        say "$name failed; the reason is in /mnt/mass/cephdag/campaigns/$name/ledger.jsonl"
        failed="$failed $name"
    fi
done

say "putting the lab in cs mode with the DROID functions loaded"
python3 infra/lab.py --lab "$lab" mode cs --functions cephdag.droid || fail "mode cs failed"
record_state
if [ -n "$failed" ]; then
    fail "campaigns that failed:$failed"
fi
say "follow-on complete"
