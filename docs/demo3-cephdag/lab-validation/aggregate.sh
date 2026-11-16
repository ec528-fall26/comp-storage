set -eu
cd ~/experiments/cephdag-demo3
L="--lab infra/labs/cephdag8.json"
python3 infra/lab.py $L mode disagg --functions cephdag.droid | tail -1
cat /sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' cephdag8-router).scope/cpu.stat 2>/dev/null | grep -E "usage_usec|nr_throttled|throttled_usec" | tr '\n' ' ' || true
before=$(docker exec cephdag8-router cat /sys/fs/cgroup/cpu.stat | tr '\n' ' '); echo "router before: $before"
for i in 0 1 2 3 4 5 6 7; do docker exec -d cephdag8-n$i iperf3 -s -p 5301 -1; done
sleep 1
for i in 0 1 2 3 4 5 6 7; do
  docker exec cephdag8-c$i iperf3 -c 172.30.$((144 + i)).10 -p 5301 -t 10 -J > /tmp/agg-$i.json &
done
wait
after=$(docker exec cephdag8-router cat /sys/fs/cgroup/cpu.stat | tr '\n' ' '); echo "router after: $after"
python3 -c "
import json
rates = [json.load(open(f'/tmp/agg-{i}.json'))['end']['sum_received']['bits_per_second'] / 1e6 for i in range(8)]
print('8 simultaneous c_i -> n_i flows, Mbit/s:', [round(r) for r in rates], 'aggregate', round(sum(rates)))"
for i in 0 1 2 3 4 5 6 7; do docker exec cephdag8-n$i pkill -x iperf3 || true; done
python3 infra/lab.py $L mode cs --functions cephdag.droid | tail -1
python3 infra/lab.py $L shape 400 && echo "shaped 400"
docker ps --filter name=cephdag8 --format '{{.Names}}' | sort -V | tr '\n' ' '; echo
