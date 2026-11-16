#!/bin/sh
# Exercise demo3-stage-c.sh up to its gate (GATE_ONLY=1) against fake stage B run directories
# under /mnt/mass/cephdag/tmp; never touches the real stage B run, the clone or the lab.
set -u
script=${STAGE_C_SCRIPT:-/mnt/mass/cephdag/tools/demo3-stage-c.sh}
root=/mnt/mass/cephdag/tmp/stage-c-test
rm -rf "$root"
mkdir -p "$root"

fake_run() {
    mkdir -p "$root/$1/demo3-stage-b-$2"
    echo "fake stage B last line" > "$root/$1/demo3-stage-b-$2/output.log"
    echo "$root/$1/demo3-stage-b-$2"
}

finished() {
    echo "fake time stage B complete" >> "$1/output.log"
    echo 0 > "$1/exit_code"
}

expect() {
    if [ "$2" = "$3" ]; then
        echo "PASS $1 (exit $3)"
    else
        echo "FAIL $1: expected exit $2, got $3"
    fi
}

stage_c() {
    STAGE_B_RUNS=$root/$1 STAGE_B_PATTERN=$2 GATE_ONLY=1 POLL_SECONDS=1 sh "$script"
}

echo "--- 1: no stage B run directory"
mkdir -p "$root/none"
stage_c none fakestageb-none
expect "missing run stops" 1 $?

echo "--- 2: stage B exited 0 and logged its completion"
run=$(fake_run finished 20261114-012459)
finished "$run"
stage_c finished fakestageb-none
expect "exit 0 passes the gate" 0 $?

echo "--- 3: stage B exited 1"
run=$(fake_run failed 20261114-012459)
echo "stopping: fake failure" >> "$run/output.log"
echo 1 > "$run/exit_code"
stage_c failed fakestageb-none
expect "non-zero exit stops" 1 $?

echo "--- 4: no exit code and no process"
fake_run lost 20261114-012459 > /dev/null
stage_c lost fakestageb-none
expect "lost run stops" 1 $?

echo "--- 5: running, then exit code 0 written while the process lingers"
run=$(fake_run running 20261114-012459)
sh -c 'sleep 5; : fakestageb-five' < /dev/null > /dev/null 2>&1 &
(sleep 2; finished "$run") &
stage_c running fakestageb-five
expect "waits for the process, then passes" 0 $?

echo "--- 6: still running at the start deadline"
fake_run late 20261114-012459 > /dev/null
sh -c 'sleep 3; : fakestageb-six' < /dev/null > /dev/null 2>&1 &
START_DEADLINE=$(($(date +%s) + 1)) stage_c late fakestageb-six
expect "deadline stops" 1 $?

echo "--- 7: exit code 0 but no completion line in the log"
run=$(fake_run unlogged 20261114-012459)
echo 0 > "$run/exit_code"
stage_c unlogged fakestageb-none
expect "exit 0 without 'stage B complete' stops" 1 $?

echo "--- 8: two stage B runs; the newer one decides"
old=$(fake_run two 20261113-120000)
echo 1 > "$old/exit_code"
run=$(fake_run two 20261114-012459)
finished "$run"
stage_c two fakestageb-none
expect "newer run's exit 0 passes" 0 $?
new_failed=$(fake_run two-reversed 20261114-012459)
echo 1 > "$new_failed/exit_code"
run=$(fake_run two-reversed 20261113-120000)
finished "$run"
stage_c two-reversed fakestageb-none
expect "newer run's exit 1 stops" 1 $?

echo "--- 9: default pattern against the live stage B, fake exit 0"
run=$(fake_run live 20990101-000000)
finished "$run"
STAGE_B_RUNS=$root/live GATE_ONLY=1 POLL_SECONDS=1 timeout 4 sh "$script"
expect "live stage B processes keep it waiting" 124 $?

echo "--- 10: a stage A run directory is not taken for stage B"
mkdir -p "$root/stage-a/demo3-stage-a-20261114-003718"
echo 0 > "$root/stage-a/demo3-stage-a-20261114-003718/exit_code"
stage_c stage-a fakestageb-none
expect "only demo3-stage-b-* counts" 1 $?

wait
rm -rf "$root"
