#!/usr/bin/env bash
# Claim: the testbed is what we describe -- a single-node Ceph cluster
# (MicroCeph) whose CephFS is mounted both on the storage node and on a
# separate compute node, with the identical worker image on both, and the
# compute->storage traffic going through the network interface we count.
#
# Where: the compute node, after infra/deploy.sh.
# Runtime: ~10 s.
# Expected output: the Ceph status (1 mon, 1 mgr, 1 mds, 3 osds up; file
#        system cstorefs active), then one line per check, all "OK", ending
#        with "all checks passed". A "WARN" on Ceph health right after setup
#        (placement groups still settling) is not fatal; re-run after a minute.
# shellcheck source=experiments/common.sh
source "$(dirname "$0")/common.sh"
require_role compute

banner "Ceph cluster on the storage node"
on_storage "sudo -n microceph.ceph -s && sudo -n microceph.ceph osd df tree && sudo -n microceph.ceph fs status"

banner "testbed checks"
"$CSTORE" check
