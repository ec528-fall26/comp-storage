#!/bin/sh
# sched-droid (fused DROID with node 0 holding 9 of every 16 partitions), then sched-lp-low
# and sched-lp-uniform (lp-plan2 at i1/i16 on the skew graph and at i256 on uniform data),
# from the deployed clone; a failed campaign does not stop the next. Leaves the lab at
# 400 Mbit in cs mode with DROID loaded.
set -u

clone=$HOME/experiments/cephdag-demo3
specs=/mnt/mass/cephdag/tools/gaps
lab="python3 infra/lab.py --lab infra/labs/cephdag8.json"

say() {
    echo "$(date '+%Y-%m-%d %H:%M:%S %Z') $*"
}

free_gib() {
    df -B1G --output=avail / | tail -1 | tr -d ' '
}

cd "$clone" || exit 1
say "clone at $(git rev-parse --short HEAD); free $(free_gib) GiB"
status=0
for name in sched-droid sched-lp-low sched-lp-uniform; do
    say "$name started"
    if python3 experiments/campaign.py run "$specs/$name.json"; then
        say "$name complete; free $(free_gib) GiB"
    else
        say "$name failed; free $(free_gib) GiB"
        status=1
    fi
done
$lab shape 400 || status=1
$lab mode cs --functions cephdag.droid || status=1
say "gaps finished, status $status; free $(free_gib) GiB"
exit $status
