D=/mnt/mass/cephdag/deploy
echo "--- kernel"; uname -r; findmnt -n -o SOURCE,FSTYPE /mnt/mass; ls /sys/module | grep -E "^(ipip|sch_tbf)$" | tr '\n' ' '; echo
echo "--- limits"
for c in $(docker ps -a --filter name=cephdag8 --format '{{.Names}}' | sort -V); do docker inspect $c --format "$c {{.State.Status}} cpus={{.HostConfig.NanoCpus}} mem={{.HostConfig.Memory}} pids={{.HostConfig.PidsLimit}}"; done | tr '\n' ';'; echo
echo "--- routes/qdisc"; docker exec cephdag8-n5 ip route get 172.30.144.10 | head -1; docker exec cephdag8-n5 tc qdisc show dev eth0; docker exec cephdag8-router tc qdisc show | grep -c "tbf.*400Mbit"
echo "--- disk"; df -h / /mnt/mass | tail -2
for v in 0 1 2 3 4 5 6 7; do docker run --rm --network none -v cephdag8_osd$v:/v:ro --entrypoint sh cephdag-lab:20.2.4-amd64-scale20261112 -c "echo cephdag8_osd$v apparent=\$(du -s --apparent-size --block-size=1G /v | cut -f1)G actual=\$(du -s --block-size=1M /v | cut -f1)M"; done | tr '\n' ';'; echo
du -sh /mnt/mass/cephdag/deploy /mnt/mass/cephdag/smoke /mnt/mass/cephdag/calibration /mnt/mass/cephdag/campaigns 2>/dev/null | tr '\n' ';'; echo
echo "--- health"; docker exec cephdag8-n0 ceph health detail | grep -v "^    pool '"; docker exec cephdag8-n0 ceph -s | grep -E "osd:|pgs:|usage"
echo "--- rados"; docker exec cephdag8-n0 python3 /results/tools/pool_usage.py
echo "--- old lab"; docker ps -a --format '{{.Names}} {{.Status}}' | grep -E '^cephdag-(n[0-2]|router) |gbrain'
docker volume ls --format '{{.Name}}' | grep cephdag | tr '\n' ' '; echo
echo "--- repo"; git -C ~/experiments/cephdag-demo3 log --oneline -1; git -C ~/experiments/cephdag-demo3 status --short | head -3; cat $D/image-commit.txt
