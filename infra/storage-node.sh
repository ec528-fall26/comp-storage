#!/usr/bin/env bash
# Storage node setup: single-node Ceph (MicroCeph) with CephFS mounted at
# /mnt/cephfs, Docker with the worker image, and the background-service unit.
#
# Run ON the storage node (Ubuntu 24.04), from the repository root:
#     sudo infra/storage-node.sh
# infra/deploy.sh does this for you. Safe to re-run.
#
# Settings (environment variables):
#   OSD_COUNT=3  OSD_SIZE=16G       loop-file OSDs (3 are needed for 3-way
#                                   replication with an OSD failure domain)
#   MICROCEPH_CHANNEL=squid/stable  Ceph 19.2 (matches Ubuntu's ceph-common)
#   FS_NAME=cstorefs  CEPH_CLIENT=cstore  CEPHFS_MOUNT=/mnt/cephfs
#   WORKER_IMAGE=cstore-worker:demo2  WORKER_CPUS, WORKER_MEMORY (default: vCPUs - 1, RAM / 2 up to 8g)
#   REBUILD_IMAGE=1                 rebuild the worker image even if it exists
#   DOCKER_BUILD_ARGS="..."         extra docker build flags (see src/worker/Dockerfile)
set -euo pipefail

cd "$(dirname "$0")/.."
REPO=$(pwd)
OWNER=${SUDO_USER:-ubuntu}
OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)
OSD_COUNT=${OSD_COUNT:-3}
OSD_SIZE=${OSD_SIZE:-16G}
MICROCEPH_CHANNEL=${MICROCEPH_CHANNEL:-squid/stable}
FS_NAME=${FS_NAME:-cstorefs}
CLIENT=${CEPH_CLIENT:-cstore}
MOUNT=${CEPHFS_MOUNT:-/mnt/cephfs}
WORKER_IMAGE=${WORKER_IMAGE:-cstore-worker:demo2}
# Worker limits: all but one vCPU, and half the RAM up to 8 GiB. Both nodes
# must end up with the same values (same instance type); bin/cstore check verifies.
WORKER_CPUS=${WORKER_CPUS:-$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))}
mem_gib=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1048576 / 2 ))
WORKER_MEMORY=${WORKER_MEMORY:-$(( mem_gib > 8 ? 8 : (mem_gib < 1 ? 1 : mem_gib) ))g}
BUNDLE="$OWNER_HOME/cstore-client"   # what the compute node needs to mount CephFS

step() { printf '\n== %s\n' "$*"; }
ceph() { microceph.ceph "$@"; }
wait_for() {  # wait_for SECONDS DESCRIPTION COMMAND...
  local limit=$1 what=$2; shift 2
  for _ in $(seq "$limit"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "timed out after ${limit}s waiting for $what" >&2; return 1
}

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

step "packages: ceph-common (mount helper), docker.io, rsync"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q ceph-common docker.io rsync

step "quiet background downloads (they would pollute the traffic counts)"
# Short-lived testbed: hold snap refreshes and stop apt's periodic jobs, so no
# snap or package download crosses the NIC while a job is being measured.
snap refresh --hold >/dev/null 2>&1 || true
systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true

step "MicroCeph ($MICROCEPH_CHANNEL)"
snap list microceph >/dev/null 2>&1 || snap install microceph --channel="$MICROCEPH_CHANNEL"
snap refresh --hold microceph
if ! ceph -s >/dev/null 2>&1; then
  microceph cluster bootstrap
fi
wait_for 120 "the monitor" ceph -s

step "OSDs: $OSD_COUNT loop-file disks of $OSD_SIZE"
have=$(ceph osd ls | wc -l)
if [ "$have" -lt "$OSD_COUNT" ]; then
  microceph disk add "loop,$OSD_SIZE,$((OSD_COUNT - have))"
fi
osds_up() { [ "$(ceph osd stat -f json | python3 -c 'import json,sys; print(json.load(sys.stdin)["num_up_osds"])')" -ge "$OSD_COUNT" ]; }
wait_for 300 "$OSD_COUNT OSDs up" osds_up

step "memory targets for a small VM (Ceph's defaults assume dedicated servers)"
# 1.5 GiB per OSD on 16 GiB machines, 1 GiB on smaller ones (default 4 GiB),
# leaving room for the worker container next to three OSDs.
if [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -ge 12000000 ]; then osd_mem=1610612736; else osd_mem=1073741824; fi
ceph config set osd osd_memory_target "$osd_mem"
ceph config set mds mds_cache_memory_limit 1073741824 # 1 GiB (default 4 GiB)

step "CephFS file system '$FS_NAME'"
if ! ceph fs ls | grep -q "name: $FS_NAME,"; then
  ceph osd pool create "${FS_NAME}_meta" 16
  ceph osd pool create "${FS_NAME}_data" 32
  ceph fs new "$FS_NAME" "${FS_NAME}_meta" "${FS_NAME}_data"
fi
mds_active() { ceph mds stat | grep -q "up:active"; }
wait_for 180 "an active MDS" mds_active

step "client.$CLIENT key and minimal ceph.conf in /etc/ceph"
mkdir -p /etc/ceph
# (write to a temporary name first: the ceph CLI may itself read /etc/ceph/ceph.conf)
ceph config generate-minimal-conf > /etc/ceph/ceph.conf.new
if ! ceph auth get "client.$CLIENT" > "/etc/ceph/ceph.client.$CLIENT.keyring.new" 2>/dev/null; then
  ceph fs authorize "$FS_NAME" "client.$CLIENT" / rw > "/etc/ceph/ceph.client.$CLIENT.keyring.new"
fi
chmod 600 "/etc/ceph/ceph.client.$CLIENT.keyring.new"
mv /etc/ceph/ceph.conf.new /etc/ceph/ceph.conf
mv "/etc/ceph/ceph.client.$CLIENT.keyring.new" "/etc/ceph/ceph.client.$CLIENT.keyring"

step "mount CephFS at $MOUNT"
CEPH_CLIENT=$CLIENT FS_NAME=$FS_NAME CEPHFS_MOUNT=$MOUNT infra/mount-cephfs.sh
mkdir -p "$MOUNT/cstore"
chown "$OWNER:$OWNER" "$MOUNT/cstore"
probe="$MOUNT/cstore/.probe-$(hostname)"
echo ok > "$probe" && [ "$(cat "$probe")" = ok ] && rm -f "$probe"

step "client bundle for the compute node: $BUNDLE"
mkdir -p "$BUNDLE"
cp /etc/ceph/ceph.conf "/etc/ceph/ceph.client.$CLIENT.keyring" "$BUNDLE/"
chown -R "$OWNER:$OWNER" "$BUNDLE"
chmod 600 "$BUNDLE/ceph.client.$CLIENT.keyring"

step "Docker and the worker image ($WORKER_IMAGE)"
systemctl enable --now docker
usermod -aG docker "$OWNER"
if docker image inspect "$WORKER_IMAGE" >/dev/null 2>&1 && [ "${REBUILD_IMAGE:-0}" != 1 ]; then
  echo "image exists ($(docker image inspect --format '{{.Id}}' "$WORKER_IMAGE")); REBUILD_IMAGE=1 rebuilds it"
else
  # DOCKER_BUILD_ARGS: extra flags, e.g. to take torch from PyPI if
  # download.pytorch.org is unreachable (see src/worker/Dockerfile)
  # shellcheck disable=SC2086
  docker build ${DOCKER_BUILD_ARGS:-} -f src/worker/Dockerfile -t "$WORKER_IMAGE" "$REPO"
fi

step "local scratch space"
mkdir -p /var/tmp/cstore
chown "$OWNER:$OWNER" /var/tmp/cstore

step "settings for this node: infra/cluster.env"
cat > infra/cluster.env <<EOF
# Written by infra/storage-node.sh on $(date -u +%Y-%m-%dT%H:%MZ)
CSTORE_ROLE=storage
CSTORE_ROOT=$MOUNT/cstore
WORKER_IMAGE=$WORKER_IMAGE
WORKER_CPUS=$WORKER_CPUS
WORKER_MEMORY=$WORKER_MEMORY
SCRATCH_DIR=/var/tmp/cstore
CSTORE_CODE_REV=${CSTORE_CODE_REV:-unknown}
EOF
chown "$OWNER:$OWNER" infra/cluster.env

step "background service unit (installed, not started; see experiments/06)"
sed -e "s#@REPO@#$REPO#g" -e "s#@OWNER@#$OWNER#g" -e "s#@MOUNT@#$MOUNT#g" \
  infra/cstore-service.service > /etc/systemd/system/cstore-service.service
systemctl daemon-reload

step "status"
ceph -s
df -h "$MOUNT"
echo
echo "storage node ready: CephFS '$FS_NAME' mounted at $MOUNT; worker image $WORKER_IMAGE"
