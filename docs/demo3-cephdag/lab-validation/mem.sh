for c in $(docker ps --filter name=cephdag8 --format '{{.Names}}' | sort -V); do
  docker exec $c sh -c 'printf "%s peak=%dMiB cur=%dMiB max=%s oom=%s oom_kill=%s " "$0" $(( $(cat /sys/fs/cgroup/memory.peak) / 1048576 )) $(( $(cat /sys/fs/cgroup/memory.current) / 1048576 )) $(cat /sys/fs/cgroup/memory.max) $(grep "^oom " /sys/fs/cgroup/memory.events | cut -d" " -f2) $(grep "^oom_kill " /sys/fs/cgroup/memory.events | cut -d" " -f2); ps -eo rss,comm --sort=-rss | head -3 | tail -2 | awk "{printf \"%s=%dMiB \", \$2, \$1/1024}"; echo' $c
done
docker exec cephdag8-n0 ceph tell osd.\* dump_mempools 2>/dev/null | python3 -c '
import sys, json, re
text = sys.stdin.read()
for m in re.finditer(r"osd\.(\d+): (\{.*?\n\})", text, re.S):
    d = json.loads(m.group(2))
    print("osd." + m.group(1), "mempool total", d["mempool"]["total"]["bytes"] // 2**20, "MiB")
' 2>&1 | head -8
