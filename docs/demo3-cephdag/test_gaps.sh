#!/bin/sh
# gaps.sh against a recording fake python3: every campaign runs in order even when one fails,
# the lab ends at 400 Mbit in cs mode with DROID loaded, and the exit status reports a failure.
set -u

here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/home/experiments/cephdag-demo3"
git -C "$work/home/experiments/cephdag-demo3" init -q
git -C "$work/home/experiments/cephdag-demo3" -c user.name=t -c user.email=t@t \
    commit -q --allow-empty -m fake

cat >"$work/bin/python3" <<'EOF'
#!/bin/sh
echo "$*" >>"$FAKE_LOG"
case "$*" in
*"run $FAKE_FAIL"*) exit 1 ;;
esac
exit 0
EOF
chmod +x "$work/bin/python3"

failures=0

check() {
    if [ "$1" = "$2" ]; then
        echo "ok: $3"
    else
        echo "FAIL: $3: expected [$2], got [$1]"
        failures=$((failures + 1))
    fi
}

run_case() {
    : >"$work/log"
    HOME=$work/home PATH=$work/bin:$PATH FAKE_LOG=$work/log FAKE_FAIL=$1 \
        sh "$here/gaps.sh" >"$work/out" 2>&1
    echo $?
}

expected="experiments/campaign.py run /mnt/mass/cephdag/tools/gaps/sched-droid.json
experiments/campaign.py run /mnt/mass/cephdag/tools/gaps/sched-lp-low.json
experiments/campaign.py run /mnt/mass/cephdag/tools/gaps/sched-lp-uniform.json
infra/lab.py --lab infra/labs/cephdag8.json shape 400
infra/lab.py --lab infra/labs/cephdag8.json mode cs --functions cephdag.droid"

status=$(run_case none)
check "$status" 0 "all campaigns pass: exit 0"
check "$(cat "$work/log")" "$expected" "all campaigns pass: commands in order"

status=$(run_case /mnt/mass/cephdag/tools/gaps/sched-droid.json)
check "$status" 1 "sched-droid fails: exit 1"
check "$(cat "$work/log")" "$expected" "sched-droid fails: the others still run, lab reset"
check "$(grep -c 'sched-droid failed' "$work/out")" 1 "sched-droid fails: reported"

status=$(run_case /mnt/mass/cephdag/tools/gaps/sched-lp-uniform.json)
check "$status" 1 "last campaign fails: exit 1"
check "$(cat "$work/log")" "$expected" "last campaign fails: lab reset"

[ "$failures" -eq 0 ]
