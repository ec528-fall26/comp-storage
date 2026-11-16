#!/bin/sh
# Exercise demo3-stage-e.sh up to its gate (GATE_ONLY=1) against fake stage D run directories
# under /mnt/mass/cephdag/tmp; never touches the real stage D run, the clone or the lab.
set -u
script=${STAGE_E_SCRIPT:-/mnt/mass/cephdag/tools/demo3-stage-e.sh}
root=/mnt/mass/cephdag/tmp/stage-e-test
rm -rf "$root"
mkdir -p "$root"

fake_run() {
    mkdir -p "$root/$1/demo3-stage-d-$2"
    echo "fake stage D last line" > "$root/$1/demo3-stage-d-$2/output.log"
    echo "$root/$1/demo3-stage-d-$2"
}

finished() {
    echo "fake time stage D complete" >> "$1/output.log"
    echo 0 > "$1/exit_code"
}

expect() {
    if [ "$2" = "$3" ]; then
        echo "PASS $1 (exit $3)"
    else
        echo "FAIL $1: expected exit $2, got $3"
    fi
}

stage_e() {
    STAGE_D_RUNS=$root/$1 STAGE_D_PATTERN=$2 GATE_ONLY=1 POLL_SECONDS=1 sh "$script"
}

echo "--- 1: no stage D run directory"
mkdir -p "$root/none"
stage_e none fakestaged-none
expect "missing run stops" 1 $?

echo "--- 2: stage D exited 0 and logged its completion"
run=$(fake_run finished 20261114-012459)
finished "$run"
stage_e finished fakestaged-none
expect "exit 0 passes the gate" 0 $?

echo "--- 3: stage D exited 1"
run=$(fake_run failed 20261114-012459)
echo "stopping: fake failure" >> "$run/output.log"
echo 1 > "$run/exit_code"
stage_e failed fakestaged-none
expect "non-zero exit stops" 1 $?

echo "--- 4: no exit code and no process"
fake_run lost 20261114-012459 > /dev/null
stage_e lost fakestaged-none
expect "lost run stops" 1 $?

echo "--- 5: running, then exit code 0 written while the process lingers"
run=$(fake_run running 20261114-012459)
sh -c 'sleep 5; : fakestaged-five' < /dev/null > /dev/null 2>&1 &
(sleep 2; finished "$run") &
stage_e running fakestaged-five
expect "waits for the process, then passes" 0 $?

echo "--- 6: still running at the start deadline"
fake_run late 20261114-012459 > /dev/null
sh -c 'sleep 3; : fakestaged-six' < /dev/null > /dev/null 2>&1 &
START_DEADLINE=$(($(date +%s) + 1)) stage_e late fakestaged-six
expect "deadline stops" 1 $?

echo "--- 7: exit code 0 but no completion line in the log"
run=$(fake_run unlogged 20261114-012459)
echo 0 > "$run/exit_code"
stage_e unlogged fakestaged-none
expect "exit 0 without 'stage D complete' stops" 1 $?

echo "--- 8: two stage D runs; the newer one decides"
old=$(fake_run two 20261113-120000)
echo 1 > "$old/exit_code"
run=$(fake_run two 20261114-012459)
finished "$run"
stage_e two fakestaged-none
expect "newer run's exit 0 passes" 0 $?
new_failed=$(fake_run two-reversed 20261114-012459)
echo 1 > "$new_failed/exit_code"
run=$(fake_run two-reversed 20261113-120000)
finished "$run"
stage_e two-reversed fakestaged-none
expect "newer run's exit 1 stops" 1 $?

echo "--- 9: default pattern against the live stage D, fake exit 0"
run=$(fake_run live 20990101-000000)
finished "$run"
STAGE_D_RUNS=$root/live GATE_ONLY=1 POLL_SECONDS=1 timeout 4 sh "$script"
expect "live stage D processes keep it waiting" 124 $?

echo "--- 10: a stage C run directory is not taken for stage D"
mkdir -p "$root/stage-c/demo3-stage-c-20261114-012459"
echo "fake time stage D complete" > "$root/stage-c/demo3-stage-c-20261114-012459/output.log"
echo 0 > "$root/stage-c/demo3-stage-c-20261114-012459/exit_code"
stage_e stage-c fakestaged-none
expect "only demo3-stage-d-* counts" 1 $?

wait
rm -rf "$root"
