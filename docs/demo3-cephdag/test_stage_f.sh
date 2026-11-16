#!/bin/sh
# Exercise demo3-stage-f.sh up to its gate (GATE_ONLY=1) against fake stage E run directories,
# and deploy_native.py against a fake docker whose containers are directories. Never touches
# the real stage E run, the clone, the containers or the lab. Runs on the lab host (defaults)
# or anywhere with TEST_ROOT, STAGE_TOOLS and LIBRARY set (JarvisVM: a scratch directory, a
# directory holding demo3-wait-chain.sh, and a library built from the gumbel commit).
set -u
script=${STAGE_F_SCRIPT:-/mnt/mass/cephdag/tools/demo3-stage-f.sh}
tools=${STAGE_TOOLS:-/mnt/mass/cephdag/tools}
library=${LIBRARY:-$tools/libcephdag_native-925abab.so}
root=${TEST_ROOT:-/mnt/mass/cephdag/tmp}/stage-f-test
rm -rf "$root"
mkdir -p "$root"

fake_run() {
    mkdir -p "$root/$1/demo3-stage-e-$2"
    echo "fake stage E last line" > "$root/$1/demo3-stage-e-$2/output.log"
    echo "$root/$1/demo3-stage-e-$2"
}

finished() {
    echo "fake time stage E complete" >> "$1/output.log"
    echo 0 > "$1/exit_code"
}

expect() {
    if [ "$2" = "$3" ]; then
        echo "PASS $1 (exit $3)"
    else
        echo "FAIL $1: expected exit $2, got $3"
    fi
}

stage_f() {
    STAGE_E_RUNS=$root/$1 STAGE_E_PATTERN=$2 STAGE_TOOLS=$tools GATE_ONLY=1 POLL_SECONDS=1 \
        sh "$script"
}

echo "--- 1: no stage E run directory"
mkdir -p "$root/none"
stage_f none fakestagee-none
expect "missing run stops" 1 $?

echo "--- 2: stage E exited 0 and logged its completion"
run=$(fake_run finished 20261114-142900)
finished "$run"
stage_f finished fakestagee-none
expect "exit 0 passes the gate" 0 $?

echo "--- 3: stage E exited 1"
run=$(fake_run failed 20261114-142900)
echo "stopping: fake failure" >> "$run/output.log"
echo 1 > "$run/exit_code"
stage_f failed fakestagee-none
expect "non-zero exit stops" 1 $?

echo "--- 4: no exit code and no process"
fake_run lost 20261114-142900 > /dev/null
stage_f lost fakestagee-none
expect "lost run stops" 1 $?

echo "--- 5: running, then exit code 0 written while the process lingers"
run=$(fake_run running 20261114-142900)
sh -c 'sleep 5; : fakestagee-five' < /dev/null > /dev/null 2>&1 &
(sleep 2; finished "$run") &
stage_f running fakestagee-five
expect "waits for the process, then passes" 0 $?

echo "--- 6: still running at the start deadline"
fake_run late 20261114-142900 > /dev/null
sh -c 'sleep 3; : fakestagee-six' < /dev/null > /dev/null 2>&1 &
START_DEADLINE=$(($(date +%s) + 1)) stage_f late fakestagee-six
expect "deadline stops" 1 $?

echo "--- 7: exit code 0 but no completion line in the log"
run=$(fake_run unlogged 20261114-142900)
echo 0 > "$run/exit_code"
stage_f unlogged fakestagee-none
expect "exit 0 without 'stage E complete' stops" 1 $?

echo "--- 8: two stage E runs; the newer one decides"
old=$(fake_run two 20261113-120000)
echo 1 > "$old/exit_code"
run=$(fake_run two 20261114-142900)
finished "$run"
stage_f two fakestagee-none
expect "newer run's exit 0 passes" 0 $?
new_failed=$(fake_run two-reversed 20261114-142900)
echo 1 > "$new_failed/exit_code"
run=$(fake_run two-reversed 20261113-120000)
finished "$run"
stage_f two-reversed fakestagee-none
expect "newer run's exit 1 stops" 1 $?

echo "--- 9: default pattern, fake exit 0: waits while live stage E processes exist"
run=$(fake_run live 20990101-000000)
finished "$run"
STAGE_E_RUNS=$root/live STAGE_TOOLS=$tools GATE_ONLY=1 POLL_SECONDS=1 timeout 4 sh "$script"
code=$?
if pgrep -f "tools/demo3-stage-e\\.sh|experiments/campaigns/sched-mcts\\.json" > /dev/null; then
    expect "live stage E processes keep it waiting" 124 $code
else
    expect "no live stage E process, so the fake exit 0 passes" 0 $code
fi

echo "--- 10: a stage D run directory is not taken for stage E"
mkdir -p "$root/stage-d/demo3-stage-d-20261114-012459"
echo "fake time stage E complete" > "$root/stage-d/demo3-stage-d-20261114-012459/output.log"
echo 0 > "$root/stage-d/demo3-stage-d-20261114-012459/exit_code"
stage_f stage-d fakestagee-none
expect "only demo3-stage-e-* counts" 1 $?

echo "--- 11: deploy_native.py against a fake docker"
fake=$root/fake-docker
mkdir -p "$fake/bin" "$fake/containers"
for container in cephdag8-n0 cephdag8-n1 cephdag8-c0; do
    mkdir -p "$fake/containers/$container/app/native/target/release"
    echo "image library" > "$fake/containers/$container/app/native/target/release/libcephdag_native.so"
done
touch "$fake/containers/cephdag8-c0/stopped"
mkdir -p "$fake/containers/other-n0"
cat > "$fake/bin/docker" << 'EOF'
#!/bin/sh
# A fake docker: containers are directories under $FAKE_CONTAINERS.
set -eu
case $1 in
    ps)
        ls "$FAKE_CONTAINERS" | grep '^cephdag8-' ;;
    inspect)
        if [ -e "$FAKE_CONTAINERS/$4/stopped" ]; then echo false; else echo true; fi ;;
    cp)
        if [ "$2" = - ]; then
            container=${3%%:*}
            tar -x -C "$FAKE_CONTAINERS/$container${3#*:}"
        else
            container=${2%%:*}
            path=${2#*:}
            tar -c -C "$FAKE_CONTAINERS/$container$(dirname "$path")" "$(basename "$path")"
        fi ;;
    exec)
        container=$2
        shift 4
        python3 -c "$(echo "$1" | sed "s|/app/|$FAKE_CONTAINERS/$container/app/|")" ;;
esac
EOF
chmod +x "$fake/bin/docker"
deploy_native() {
    PATH=$fake/bin:$PATH FAKE_CONTAINERS=$fake/containers \
        python3 "$tools/deploy_native.py" cephdag8 "$library" "$1" "$fake/deployed-native.json"
}
deploy_native 7
expect "every container gets the library, running ones load ABI 7" 0 $?
held=$(sha256sum "$fake"/containers/cephdag8-*/app/native/target/release/libcephdag_native.so |
    cut -d' ' -f1 | sort -u)
expect "all three hold the library" "$(sha256sum "$library" | cut -d' ' -f1)" "$held"
expect "the stopped container records no ABI" 1 \
    "$(grep -c '"abi": null' "$fake/deployed-native.json")"
expect "the image's library is kept" "image library" "$(cat "$fake"/libcephdag_native.so.before-*)"
expect "another lab's container is untouched" 0 "$(find "$fake/containers/other-n0" -type f | wc -l)"
deploy_native 5 > /dev/null 2>&1
expect "a library with another ABI fails" 1 $?

wait
rm -rf "$root"
