#!/bin/sh
# Run the demo3 pilot campaigns one after another with campaign.py, each one repeat and
# deleting its own datasets at the end. On the first failure stop the workers (the lab's safe
# state) and exit; otherwise leave the lab in cs mode, shaped at 400 Mbit and checked.
# Usage: sh pilots.sh pilot-sweep pilot-droid pilot-skew
set -u
cd ~/experiments/cephdag-demo3
L="--lab infra/labs/cephdag8.json"
git log --oneline -1
for name in "$@"; do
  spec=experiments/campaigns/$name.json
  echo "== $(date +%T) $name"
  python3 experiments/campaign.py plan "$spec"
  if ! python3 experiments/campaign.py run "$spec"; then
    echo "FAILED $name at $(date +%T); stopping the workers"
    python3 infra/lab.py $L stop-workers
    exit 1
  fi
  python3 /mnt/mass/cephdag/tools/pilot_table.py /mnt/mass/cephdag "$name"
done
python3 infra/lab.py $L mode cs --functions cephdag.droid
python3 infra/lab.py $L shape 400
python3 infra/lab.py $L check cs
docker exec cephdag8-n0 ceph -s
echo "pilots complete at $(date +%T)"
