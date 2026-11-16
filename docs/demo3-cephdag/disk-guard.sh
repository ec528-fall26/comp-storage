#!/bin/sh
# Stops the demo3 stages if free space on aakashpc's NVMe root falls below FLOOR GiB.
set -u

floor_gib=${1:-300}
runjob=/mnt/mass/cephdag/tools/runjob.py
watched="demo3-stage-b demo3-stage-c demo3-stage-d demo3-stage-e demo3-netsweep demo3-gaps"

say() {
    echo "$(date '+%Y-%m-%d %H:%M:%S %Z') $*"
}

free_gib() {
    df -B1G --output=avail / | tail -1 | tr -d ' '
}

running_stages() {
    for name in $watched; do
        if python3 "$runjob" status "$name" | grep -q '^state: running'; then
            printf '%s ' "$name"
        fi
    done
}

last_report=0
while :; do
    running=$(running_stages)
    if [ -z "$running" ]; then
        say "no watched stage is running; guard done"
        exit 0
    fi
    free=$(free_gib)
    if [ "$free" -lt "$floor_gib" ]; then
        say "free space $free GiB is below $floor_gib GiB; stopping: $running"
        for name in $running; do
            python3 "$runjob" stop "$name"
        done
        docker exec cephdag8-n0 pkill -f 'experiments[./]run' || say "no coordinator to stop in cephdag8-n0"
        exit 1
    fi
    now=$(date +%s)
    if [ $((now - last_report)) -ge 600 ]; then
        say "free $free GiB (floor $floor_gib); running: $running"
        last_report=$now
    fi
    sleep 60
done
