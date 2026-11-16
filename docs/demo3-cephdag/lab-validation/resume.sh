set -eu
cd ~/experiments/cephdag-demo3
L="--lab infra/labs/cephdag8.json"
LOG=/mnt/mass/cephdag/deploy/logs/reboot
mkdir -p $LOG
for step in resume "shape 400" "mode cs --functions cephdag.droid" "mode cs --functions cephdag.droid"; do
  s=$(date +%s); name=$(echo $step | cut -d' ' -f1)
  if python3 infra/lab.py $L $step > $LOG/$name.log 2>&1; then echo "[$step] ok in $(( $(date +%s)-s )) s: $(tail -1 $LOG/$name.log | cut -c1-150)"
  else echo "[$step] FAILED"; tail -15 $LOG/$name.log; exit 1; fi
done
docker ps --filter name=cephdag8 --format '{{.Names}}' | sort -V | tr '\n' ' '; echo
docker logs cephdag8-router 2>&1 | tail -3
docker exec cephdag8-n3 ip -br link | tr -s ' ' | cut -d' ' -f1,2 | tr '\n' ';'; echo
docker exec cephdag8-n3 tc qdisc show dev eth0
docker exec cephdag8-router tc qdisc show | grep -c "tbf.*rate 400Mbit"
docker exec cephdag8-n3 ip route get 172.30.150.10
docker exec cephdag8-n3 nft list tables 2>&1 | grep -c cephdag || true
docker exec cephdag8-n0 ceph -s | grep -E "health|osd:|pgs:"
