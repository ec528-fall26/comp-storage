#!/usr/bin/env bash
# Compute node setup: mount the storage node's CephFS at /mnt/cephfs, install
# Docker, and write this node's settings. The worker image itself is copied
# from the storage node by infra/deploy.sh, so both nodes run the identical
# image (same image ID).
#
# Run ON the compute node (Ubuntu 24.04), from the repository root:
#     sudo infra/compute-node.sh STORAGE_PRIVATE_IP CLIENT_BUNDLE_DIR
# where CLIENT_BUNDLE_DIR holds ceph.conf and ceph.client.cstore.keyring from
# the storage node (~/cstore-client there). infra/deploy.sh does all of this.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO=$(pwd)
STORAGE_IP=${1:?usage: sudo infra/compute-node.sh STORAGE_PRIVATE_IP CLIENT_BUNDLE_DIR}
BUNDLE=${2:?usage: sudo infra/compute-node.sh STORAGE_PRIVATE_IP CLIENT_BUNDLE_DIR}
OWNER=${SUDO_USER:-ubuntu}
OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)
FS_NAME=${FS_NAME:-cstorefs}
CLIENT=${CEPH_CLIENT:-cstore}
MOUNT=${CEPHFS_MOUNT:-/mnt/cephfs}
WORKER_IMAGE=${WORKER_IMAGE:-cstore-worker:demo2}
# Worker limits: all but one vCPU, and half the RAM up to 8 GiB. Both nodes
# must end up with the same values (same instance type); bin/cstore check verifies.
WORKER_CPUS=${WORKER_CPUS:-$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))}
mem_gib=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1048576 / 2 ))
WORKER_MEMORY=${WORKER_MEMORY:-$(( mem_gib > 8 ? 8 : (mem_gib < 1 ? 1 : mem_gib) ))g}

step() { printf '\n== %s\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

step "packages: ceph-common (CephFS client), rsync, Docker (unless already installed)"
apt-get update -q
pkgs=(ceph-common rsync)
# Ubuntu's docker.io conflicts with Docker's own packages (docker-ce); keep an existing install.
command -v docker >/dev/null || pkgs+=(docker.io)
DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${pkgs[@]}"

step "quiet background downloads (they would pollute the traffic counts)"
# Short-lived testbed: hold snap refreshes and stop apt's periodic jobs, so no
# snap or package download crosses the NIC while a job is being measured.
snap refresh --hold >/dev/null 2>&1 || true
systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true

step "CephFS client configuration from $BUNDLE"
install -d /etc/ceph
install -m 644 "$BUNDLE/ceph.conf" /etc/ceph/ceph.conf
install -m 600 "$BUNDLE/ceph.client.$CLIENT.keyring" "/etc/ceph/ceph.client.$CLIENT.keyring"

step "mount CephFS at $MOUNT"
CEPH_CLIENT=$CLIENT FS_NAME=$FS_NAME CEPHFS_MOUNT=$MOUNT infra/mount-cephfs.sh
[ -d "$MOUNT/cstore" ] || { echo "$MOUNT/cstore is missing; set up the storage node first" >&2; exit 1; }

step "Docker"
systemctl enable --now docker
usermod -aG docker "$OWNER"

step "local scratch space"
mkdir -p /var/tmp/cstore
chown "$OWNER:$OWNER" /var/tmp/cstore

step "ssh key this node uses to reach the storage node"
if [ ! -f "$OWNER_HOME/.ssh/id_ed25519" ]; then
  sudo -u "$OWNER" ssh-keygen -q -t ed25519 -N "" -f "$OWNER_HOME/.ssh/id_ed25519"
fi

step "settings for this node: infra/cluster.env"
cat > infra/cluster.env <<EOF
# Written by infra/compute-node.sh on $(date -u +%Y-%m-%dT%H:%MZ)
CSTORE_ROLE=compute
CSTORE_ROOT=$MOUNT/cstore
STORAGE_HOST=$OWNER@$STORAGE_IP
STORAGE_REPO=$REPO
WORKER_IMAGE=$WORKER_IMAGE
WORKER_CPUS=$WORKER_CPUS
WORKER_MEMORY=$WORKER_MEMORY
SCRATCH_DIR=/var/tmp/cstore
DROID_SHARDS=2
CSTORE_CODE_REV=${CSTORE_CODE_REV:-unknown}
EOF
chown "$OWNER:$OWNER" infra/cluster.env

echo
echo "compute node ready: CephFS mounted at $MOUNT; storage node is $OWNER@$STORAGE_IP"
