#!/bin/sh
# Validate the freshly bootstrapped cephdag8 lab on aakashpc: shaping, mode switches, verify in
# both modes, DROID ingest, calibration of both modes, smoke runs, a tiny campaign dry run, then
# delete the smoke datasets and leave the lab in cs mode at 400 Mbit. Stops at the first failure.
set -eu
cd ~/experiments/cephdag-demo3
L="--lab infra/labs/cephdag8.json"
D=/mnt/mass/cephdag/deploy
LOG=$D/logs/validate
mkdir -p "$LOG"
step() {
  name=$1; shift; began=$(date +%s)
  echo "== $(date +%T) $name: $*"
  if "$@" > "$LOG/$name.log" 2>&1; then echo "   ok in $(( $(date +%s) - began )) s: $(tail -c 200 "$LOG/$name.log" | tail -1)"
  else echo "FAILED $name, see $LOG/$name.log"; tail -20 "$LOG/$name.log"; exit 1; fi
}
coordinator() { docker exec cephdag8-n0 python3 -m "$@"; }
run() {  # run NAME MODE ARGS...: experiments.run in the coordinator with this mode's models and cluster
  name=$1; mode=$2; shift 2
  step "$name" coordinator experiments.run "$@" --repeats 1 --no-warm \
    --models "/results/calibration/cephdag8-$mode/models.json" \
    --cluster "cephdag/clusters/cephdag8-$mode.json" --output "/results/smoke/validate/$name"
}
P=0,1,2,3,4,5,6,7
SYNTH="--workload independent --ratio 0.25 --dataset smoke-2g-64m --placement $P --source-gib 2 --dataset-gib 2 --shard-mib 64 --seed 9103"
DROID="--workload droid --droid-shards 0-1 --resolution 128 --wave-gib 1000000 --seed 9203"

uname -r; git log --oneline -1; df -h / | tail -1
step shape python3 infra/lab.py $L shape 400
step mode-cs python3 infra/lab.py $L mode cs --functions cephdag.droid
step mode-cs-again python3 infra/lab.py $L mode cs --functions cephdag.droid
step verify-cs python3 infra/lab.py $L verify cs
step mode-disagg python3 infra/lab.py $L mode disagg --functions cephdag.droid
step mode-disagg-again python3 infra/lab.py $L mode disagg --functions cephdag.droid
step limit-c3-after-disagg docker exec cephdag8-c3 nft list table netdev cephdag_eth0
step verify-disagg python3 infra/lab.py $L verify disagg
V=results/deployment/cephdag8
cp $V/validated-cs.json $V/validated-disagg.json $V/bandwidth-cs.json $V/bandwidth-disagg.json $D/
RATE=$(python3 -c "import json; print(round(min(min(json.load(open('$V/validated-' + m + '.json'))['aggregate_mbit'].values()) for m in ('cs', 'disagg')), 1))")
echo "weakest shared rate over both modes: $RATE Mbit/s"

step mode-cs-2 python3 infra/lab.py $L mode cs --functions cephdag.droid
step ingest-02047 coordinator experiments.droid_ingest --shards 2047 --resolution 128 --placement $P \
  --source-dir /results/droid/raw --cluster cephdag/clusters/cephdag8-cs.json
step ingest-0-1 coordinator experiments.droid_ingest --shards 0-1 --resolution 128 --placement $P \
  --source-dir /results/droid/raw --cluster cephdag/clusters/cephdag8-cs.json
step create-smoke coordinator experiments.datasets create-synthetic smoke-2g-64m --gib 2 --shard-mib 64 \
  --placement $P --cluster cephdag/clusters/cephdag8-cs.json
step calibrate-cs coordinator experiments.calibrate --network-mbit "$RATE" \
  --cluster cephdag/clusters/cephdag8-cs.json --droid droid-128-02047 --output /results/calibration/cephdag8-cs
run cs-i1 cs $SYNTH --iterations 1 --policies data-local,heft,beam-4
run cs-i128 cs $SYNTH --iterations 128 --policies data-local,heft,beam-4
run cs-i2048 cs $SYNTH --iterations 2048 --policies beam-4
run cs-droid cs $DROID --policies greedy,beam-4

step mode-disagg-2 python3 infra/lab.py $L mode disagg --functions cephdag.droid
step calibrate-disagg coordinator experiments.calibrate --network-mbit "$RATE" \
  --cluster cephdag/clusters/cephdag8-disagg.json --droid droid-128-02047 \
  --storage-from /results/calibration/cephdag8-cs/calibration.json --output /results/calibration/cephdag8-disagg
run disagg-i1 disagg $SYNTH --iterations 1 --policies fifo-hash,heft
run disagg-i128 disagg $SYNTH --iterations 128 --policies fifo-hash,heft
run disagg-droid disagg $DROID --policies fifo-hash
python3 /mnt/mass/cephdag/tools/summarize_rows.py /mnt/mass/cephdag/smoke/validate | tee $D/smoke-validate-rows.txt
sh /mnt/mass/cephdag/tools/mem.sh | tee $D/memory-after-smoke.txt

step dryrun python3 experiments/campaign.py run $D/dryrun-tiny.json
step dryrun-rerun python3 experiments/campaign.py run $D/dryrun-tiny.json
cat /mnt/mass/cephdag/campaigns/dryrun-tiny/ledger.jsonl
step delete-smoke coordinator experiments.datasets delete smoke-2g-64m --droid-shards 0-1 \
  --cluster cephdag/clusters/cephdag8-cs.json
step mode-cs-final python3 infra/lab.py $L mode cs --functions cephdag.droid
step shape-final python3 infra/lab.py $L shape 400
sh /mnt/mass/cephdag/tools/mem.sh | tee $D/memory-final.txt
docker exec cephdag8-n0 ceph -s | tee $D/ceph-status-final.txt
docker exec cephdag8-n0 ceph df | tee $D/ceph-df-final.txt
df -h / | tail -1
echo "validation complete"
