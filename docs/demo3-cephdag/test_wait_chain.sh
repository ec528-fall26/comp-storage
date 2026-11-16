#!/bin/sh
# Exercise demo3-wait-chain.sh, and demo3-stage-a.sh up to its gate (GATE_ONLY=1), against fake
# run directories and a lock file under /mnt/mass/cephdag/tmp; never touches the real runs, the
# real demo3-scale lock or the lab.
set -u
wait_script=/mnt/mass/cephdag/tools/demo3-wait-chain.sh
stage_a_script=/mnt/mass/cephdag/tools/demo3-stage-a.sh
root=/mnt/mass/cephdag/tmp/followon-test
lock=$root/scale/driver.lock
probe=$root/lock_probe.py
rm -rf "$root"
mkdir -p "$root/scale"

# The chain's attempt at the lock, as campaign.py makes it: exit 0 if the lock is held elsewhere.
cat > "$probe" << 'EOF'
import fcntl
import sys

with open(sys.argv[1], "w") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)
sys.exit(1)
EOF

fake_run() {
    mkdir -p "$root/$1"
    echo "fake chain last line" > "$root/$1/output.log"
    echo "$root/$1"
}

expect() {
    if [ "$2" = "$3" ]; then
        echo "PASS $1 (exit $3)"
    else
        echo "FAIL $1: expected exit $2, got $3"
    fi
}

stage_a() {
    CHAIN_RUN=$1 CHAIN_PATTERN=$2 SCALE_LOCK=$lock GATE_ONLY=1 POLL_SECONDS=1 sh "$stage_a_script"
}

echo "--- 1: exit_code 0, no process"
run=$(fake_run exit0)
echo 0 > "$run/exit_code"
CHAIN_RUN=$run CHAIN_PATTERN=fakechain-none POLL_SECONDS=1 sh "$wait_script"
expect "exit 0 finishes" 0 $?

echo "--- 2: exit_code 3"
run=$(fake_run exit3)
echo 3 > "$run/exit_code"
CHAIN_RUN=$run CHAIN_PATTERN=fakechain-none POLL_SECONDS=1 sh "$wait_script"
expect "nonzero exit is reported as 2" 2 $?

echo "--- 3: running, then exit_code 0 written while the process lingers"
run=$(fake_run running)
sh -c 'sleep 5; : fakechain-three' < /dev/null > /dev/null 2>&1 &
(sleep 2; echo 0 > "$run/exit_code") &
CHAIN_RUN=$run CHAIN_PATTERN=fakechain-three POLL_SECONDS=1 sh "$wait_script"
expect "waits for the process, then finishes" 0 $?

echo "--- 4: no exit_code, no process"
run=$(fake_run lost)
CHAIN_RUN=$run CHAIN_PATTERN=fakechain-none POLL_SECONDS=1 sh "$wait_script"
expect "lost run stops" 1 $?

echo "--- 5: still running at the deadline"
run=$(fake_run late)
sh -c 'sleep 3; : fakechain-five' < /dev/null > /dev/null 2>&1 &
CHAIN_RUN=$run CHAIN_PATTERN=fakechain-five POLL_SECONDS=1 START_DEADLINE=$(($(date +%s) + 1)) sh "$wait_script"
expect "deadline stops" 1 $?

echo "--- 6: default pattern against the live chain, fake exit_code 0"
run=$(fake_run live)
echo 0 > "$run/exit_code"
CHAIN_RUN=$run POLL_SECONDS=1 timeout 4 sh "$wait_script"
expect "live chain processes keep it waiting" 124 $?

echo "--- 7: stage A holds the lock while the chain runs DROID; the chain then stops at the lock"
run=$(fake_run interplay)
echo "demo3-droid complete at fake time" >> "$run/output.log"
sh -c '
    sleep 3
    if python3 "$0" "$1"; then
        echo "stopped: another driver runs campaign demo3-scale" >> "$2/output.log"
        echo 1 > "$2/exit_code"
    else
        echo 0 > "$2/exit_code"
    fi
    : fakechain-seven' "$probe" "$lock" "$run" < /dev/null > /dev/null 2>&1 &
stage_a "$run" fakechain-seven &
stage_a_pid=$!
sleep 1.5
python3 "$probe" "$lock"
expect "lock held while waiting" 0 $?
wait "$stage_a_pid"
expect "DROID complete and the chain stopped at the lock: proceeds" 0 $?
expect "the fake chain's lock attempt was refused" 1 "$(cat "$run/exit_code")"

echo "--- 8: the chain failed during DROID"
run=$(fake_run droid-failed)
printf 'demo3-skew complete at fake time\nstopped: wave04-cs: fake failure\n' >> "$run/output.log"
echo 1 > "$run/exit_code"
stage_a "$run" fakechain-none
expect "failure during DROID stops" 1 $?

echo "--- 9: the chain exited 0, so demo3-scale ran"
run=$(fake_run scale-ran)
printf 'demo3-droid complete at fake time\ndemo3-scale complete at fake time\n' >> "$run/output.log"
echo 0 > "$run/exit_code"
stage_a "$run" fakechain-none
expect "chain exit 0 stops" 1 $?

echo "--- 10: DROID complete, then a failure other than the lock"
run=$(fake_run other-failure)
printf 'demo3-droid complete at fake time\nstopped: fake preflight problem\n' >> "$run/output.log"
echo 1 > "$run/exit_code"
stage_a "$run" fakechain-none
expect "other failure after DROID stops" 1 $?

echo "--- 11: the lock is already held by someone else"
run=$(fake_run lock-taken)
sh -c 'sleep 6; : fakechain-eleven' < /dev/null > /dev/null 2>&1 &
flock "$lock" sleep 4 &
holder_pid=$!
sleep 0.5
CHAIN_RUN=$run CHAIN_PATTERN=fakechain-eleven SCALE_LOCK=$lock GATE_ONLY=1 POLL_SECONDS=1 \
    timeout 3 sh "$stage_a_script"
expect "held lock stops at once" 1 $?
wait "$holder_pid"

echo "--- 12: default pattern against the live chain, fake DROID complete and lock refusal"
run=$(fake_run live-stage-a)
printf 'demo3-droid complete at fake time\nstopped: another driver runs campaign demo3-scale\n' >> "$run/output.log"
echo 1 > "$run/exit_code"
CHAIN_RUN=$run SCALE_LOCK=$lock GATE_ONLY=1 POLL_SECONDS=1 timeout 4 sh "$stage_a_script"
expect "live chain processes keep stage A waiting" 124 $?

wait
rm -rf "$root"
