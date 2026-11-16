#!/bin/sh
# cs2 against disagg2 on 8 GiB at 1 and 256 iterations, with every link shaped to 1000, 2500
# and then 400 Mbit, in one session; leaves the lab at 400 Mbit in cs mode with DROID loaded.
set -u

clone=$HOME/experiments/cephdag-demo3
specs=/mnt/mass/cephdag/tools/netsweep
lab="python3 infra/lab.py --lab infra/labs/cephdag8.json"

say() {
    echo "$(date '+%Y-%m-%d %H:%M:%S %Z') $*"
}

cd "$clone" || exit 1
say "clone at $(git rev-parse --short HEAD); free $(df -B1G --output=avail / | tail -1 | tr -d ' ') GiB"
status=0
for mbit in 1000 2500 400; do
    say "netsweep-$mbit started"
    if python3 experiments/campaign.py run "$specs/netsweep-$mbit.json"; then
        say "netsweep-$mbit complete"
    else
        say "netsweep-$mbit failed"
        status=1
    fi
done
$lab shape 400
$lab mode cs --functions cephdag.droid
say "netsweep finished, status $status; free $(df -B1G --output=avail / | tail -1 | tr -d ' ') GiB"
exit $status
