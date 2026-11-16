set -eu
cd ~/experiments/cephdag-demo3
L="--lab infra/labs/cephdag8.json"
LOG=/mnt/mass/cephdag/deploy/logs/validate
for step in stop-workers stop resume "mode cs --functions cephdag.droid" "shape 400" "verify cs"; do
  s=$(date +%s); name=$(echo $step | cut -d' ' -f1-2 | tr ' ' '-')
  if python3 infra/lab.py $L $step > $LOG/sr-$name.log 2>&1; then echo "[$step] ok in $(( $(date +%s)-s )) s: $(tail -1 $LOG/sr-$name.log | cut -c1-150)"
  else echo "[$step] FAILED"; tail -15 $LOG/sr-$name.log; exit 1; fi
  if [ "$step" = stop ]; then docker ps -a --filter name=cephdag8 --format '{{.Names}} {{.Status}}' | sort -V | tr '\n' ';'; echo; fi
done
docker ps --filter name=cephdag8 --format '{{.Names}}' | sort -V | tr '\n' ' '; echo
docker exec cephdag8-n0 ceph -s | grep -E "health|osd:|pgs:"
python3 -c "
import json
v = json.load(open('results/deployment/cephdag8/validated-cs.json'))
print('verify cs after resume:', {k: round(x, 1) for k, x in v['aggregate_mbit'].items()}, set(v['routing'].values()), set(v['limits'].values()))"
docker exec cephdag8-n3 nft list table netdev cephdag_eth0 | grep limit
docker exec cephdag8-router nft -j list tables | python3 -c "import json,sys; print(sum(1 for x in json.load(sys.stdin)['nftables'] if x.get('table', {}).get('name', '').startswith('cephdag_eth')), 'router links policed')"
for n in 0 7; do docker exec cephdag8-n$n ps -eo args | grep "^python3 -m cephdag.worker"; done
