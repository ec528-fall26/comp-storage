#!/bin/sh
# Recalibrate both modes of cephdag8 after the chunked numeric kernel (cephdag 9ca8ce7), keeping
# the v3 calibration (whole-array kernel, 32/64 MiB in rounds) as models-v3.json /
# calibration-v3.json, and rerun the 2 GiB / 64 MiB synthetic smoke at 1 and 128 iterations in
# both modes to compare actual and modelled compute-stage times. Stops at the first failure and
# leaves the lab in cs mode at 400 Mbit.
set -eu
cd ~/experiments/cephdag-demo3
L="--lab infra/labs/cephdag8.json"
D=/mnt/mass/cephdag/deploy
LOG=$D/logs/recalibrate-v4
OUT=/results/smoke/ratio-v4
mkdir -p "$LOG"
step() {
  name=$1; shift; began=$(date +%s)
  echo "== $(date +%T) $name: $*"
  if "$@" > "$LOG/$name.log" 2>&1; then echo "   ok in $(( $(date +%s) - began )) s: $(tail -c 200 "$LOG/$name.log" | tail -1)"
  else echo "FAILED $name, see $LOG/$name.log"; tail -20 "$LOG/$name.log"; exit 1; fi
}
coordinator() { docker exec cephdag8-n0 python3 -m "$@"; }
run() {
  name=$1; mode=$2; shift 2
  step "$name" coordinator experiments.run "$@" --repeats 1 --no-warm \
    --models "/results/calibration/cephdag8-$mode/models.json" \
    --cluster "cephdag/clusters/cephdag8-$mode.json" --output "$OUT/$name"
}
RATE=376.7
P=0,1,2,3,4,5,6,7
SYNTH="--workload independent --ratio 0.25 --dataset ratio-2g-64m --placement $P --source-gib 2 --dataset-gib 2 --shard-mib 64 --seed 9103"

git log --oneline -1
step keep-v3 docker exec cephdag8-n0 sh -c 'for m in cs disagg; do d=/results/calibration/cephdag8-$m; test -e $d/models-v3.json || { cp -p $d/models.json $d/models-v3.json; cp -p $d/calibration.json $d/calibration-v3.json; }; done; ls -l /results/calibration/cephdag8-*'
step stop-workers python3 infra/lab.py $L stop-workers
step mode-cs python3 infra/lab.py $L mode cs --functions cephdag.droid
step check-cs python3 infra/lab.py $L check cs
step calibrate-cs coordinator experiments.calibrate --network-mbit $RATE \
  --cluster cephdag/clusters/cephdag8-cs.json --droid droid-128-02047 --output /results/calibration/cephdag8-cs
step create-ratio coordinator experiments.datasets create-synthetic ratio-2g-64m --gib 2 --shard-mib 64 \
  --placement $P --cluster cephdag/clusters/cephdag8-cs.json
run cs-i1 cs $SYNTH --iterations 1 --policies data-local,heft
run cs-i128 cs $SYNTH --iterations 128 --policies data-local,heft

step switch-disagg-mode python3 infra/lab.py $L mode disagg --functions cephdag.droid
step switch-disagg-shape python3 infra/lab.py $L shape 400
step switch-disagg-check python3 infra/lab.py $L check disagg
step calibrate-disagg coordinator experiments.calibrate --network-mbit $RATE \
  --cluster cephdag/clusters/cephdag8-disagg.json --droid droid-128-02047 \
  --storage-from /results/calibration/cephdag8-cs/calibration.json --output /results/calibration/cephdag8-disagg
run disagg-i1 disagg $SYNTH --iterations 1 --policies fifo-hash,heft
run disagg-i128 disagg $SYNTH --iterations 128 --policies fifo-hash,heft

step switch-cs-mode python3 infra/lab.py $L mode cs --functions cephdag.droid
step switch-cs-shape python3 infra/lab.py $L shape 400
step switch-cs-check python3 infra/lab.py $L check cs
step delete-ratio coordinator experiments.datasets delete ratio-2g-64m --cluster cephdag/clusters/cephdag8-cs.json
C=/mnt/mass/cephdag/calibration
for m in cs disagg; do
  echo "== ratios with $m v4 models (chunked kernel)"; python3 /mnt/mass/cephdag/tools/compute_ratio.py $C/cephdag8-$m/models.json /mnt/mass/cephdag/smoke/ratio-v4/$m-i*
  echo "== ratios with $m v3 models (whole-array kernel)"; python3 /mnt/mass/cephdag/tools/compute_ratio.py $C/cephdag8-$m/models-v3.json /mnt/mass/cephdag/smoke/ratio-v4/$m-i*
done
docker exec cephdag8-n0 ceph -s
echo "recalibration complete"
