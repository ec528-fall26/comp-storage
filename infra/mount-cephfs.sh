#!/usr/bin/env bash
# Mount CephFS at $CEPHFS_MOUNT with the kernel client, falling back to
# ceph-fuse if this kernel has no ceph module. Used by both node scripts, so
# both nodes end up with the same client type (they run the same kernel).
#
# Needs /etc/ceph/ceph.conf and /etc/ceph/ceph.client.$CEPH_CLIENT.keyring.
#     sudo infra/mount-cephfs.sh
set -euo pipefail
CLIENT=${CEPH_CLIENT:-cstore}
FS_NAME=${FS_NAME:-cstorefs}
MOUNT=${CEPHFS_MOUNT:-/mnt/cephfs}

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
mkdir -p "$MOUNT"
if mountpoint -q "$MOUNT"; then
  echo "$MOUNT already mounted ($(findmnt -no FSTYPE "$MOUNT"))"
  exit 0
fi

if modprobe ceph 2>/dev/null ||
   { DEBIAN_FRONTEND=noninteractive apt-get install -y -q "linux-modules-extra-$(uname -r)" >/dev/null 2>&1 &&
     modprobe ceph; }; then
  line=":/ $MOUNT ceph name=$CLIENT,fs=$FS_NAME,noatime,_netdev 0 0"
else
  echo "this kernel has no CephFS module; using ceph-fuse instead"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q ceph-fuse
  line="none $MOUNT fuse.ceph ceph.id=$CLIENT,ceph.client_fs=$FS_NAME,_netdev,defaults 0 0"
fi

# one fstab entry for this mount point, so the mount survives reboots
awk -v m="$MOUNT" '$2 != m' /etc/fstab > /etc/fstab.cstore.tmp
echo "$line" >> /etc/fstab.cstore.tmp
cat /etc/fstab.cstore.tmp > /etc/fstab && rm -f /etc/fstab.cstore.tmp

mount "$MOUNT"
echo "mounted $MOUNT: $(findmnt -no FSTYPE,SOURCE "$MOUNT")"
