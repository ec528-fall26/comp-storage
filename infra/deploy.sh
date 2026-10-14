#!/usr/bin/env bash
# Turn two fresh Ubuntu 24.04 hosts into the storage node and the compute node.
# Run on YOUR machine (laptop), from the repository root:
#
#     infra/deploy.sh                          # after infra/aws/up.sh (reads infra/aws/.state/hosts.env)
#     infra/deploy.sh STORAGE_SSH COMPUTE_SSH  # any two hosts, e.g. ubuntu@203.0.113.7 ubuntu@203.0.113.8
#
# The two hosts must reach each other on a private network (same VPC/subnet),
# allow all TCP between them, and give the ssh user passwordless sudo.
# Takes about 15 minutes, most of it building the worker image.
#
# Steps: copy this checkout to both hosts -> storage node setup (MicroCeph,
# CephFS, worker image) -> CephFS client bundle to the compute node -> compute
# node setup (mount, Docker) -> ssh trust compute->storage -> copy the worker
# image storage->compute -> end-to-end check (bin/cstore check).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -ge 2 ]; then
  STORAGE_SSH=$1
  COMPUTE_SSH=$2
elif [ -f infra/aws/.state/hosts.env ]; then
  # shellcheck disable=SC1091
  source infra/aws/.state/hosts.env
else
  echo "usage: infra/deploy.sh STORAGE_SSH COMPUTE_SSH   (or run infra/aws/up.sh first)" >&2
  exit 1
fi
WORKER_IMAGE=${WORKER_IMAGE:-cstore-worker:demo2}
REMOTE_DIR=comp-storage                     # relative to the remote user's home
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)
[ -n "${SSH_KEY:-}" ] && SSH_OPTS+=(-i "$SSH_KEY")
REV=$(git describe --always --dirty 2>/dev/null || echo unknown)

# shellcheck disable=SC2029  # remote commands are built here on purpose
s() { ssh "${SSH_OPTS[@]}" "$STORAGE_SSH" "$@"; }
# shellcheck disable=SC2029
c() { ssh "${SSH_OPTS[@]}" "$COMPUTE_SSH" "$@"; }
step() { printf '\n######## %s\n' "$*"; }

step "copy this checkout ($REV) to both hosts"
for host in "$STORAGE_SSH" "$COMPUTE_SSH"; do
  ssh "${SSH_OPTS[@]}" "$host" 'command -v rsync >/dev/null || sudo apt-get install -y -q rsync >/dev/null'
  rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
    --exclude .git --exclude __pycache__ --exclude infra/aws/.state --exclude infra/cluster.env \
    ./ "$host:$REMOTE_DIR/"
done

step "storage node: MicroCeph + CephFS + worker image"
s "cd $REMOTE_DIR && sudo CSTORE_CODE_REV=$REV WORKER_IMAGE=$WORKER_IMAGE DOCKER_BUILD_ARGS='${DOCKER_BUILD_ARGS:-}' infra/storage-node.sh"
# the address of the storage node's default-route interface (its private IP on AWS)
STORAGE_IP=$(s "ip -4 -o route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) if (\$i == \"src\") print \$(i + 1)}'")
REMOTE_USER=$(s 'whoami')
echo "storage node private IP: $STORAGE_IP"

step "CephFS client bundle: storage -> compute"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
scp "${SSH_OPTS[@]}" -q "$STORAGE_SSH:cstore-client/*" "$tmp/"
c "mkdir -p cstore-client"
scp "${SSH_OPTS[@]}" -q "$tmp"/* "$COMPUTE_SSH:cstore-client/"

step "compute node: CephFS mount + Docker"
c "cd $REMOTE_DIR && sudo CSTORE_CODE_REV=$REV WORKER_IMAGE=$WORKER_IMAGE infra/compute-node.sh $STORAGE_IP ~/cstore-client"

step "ssh from the compute node to the storage node"
pub=$(c 'cat ~/.ssh/id_ed25519.pub')
s "grep -qxF '$pub' ~/.ssh/authorized_keys || echo '$pub' >> ~/.ssh/authorized_keys"
c "ssh -o StrictHostKeyChecking=accept-new $REMOTE_USER@$STORAGE_IP true"

step "same worker image on both nodes: copy it storage -> compute"
c "ssh $REMOTE_USER@$STORAGE_IP 'docker save $WORKER_IMAGE' | docker load"

step "end-to-end check (from the compute node)"
c "cd $REMOTE_DIR && ./bin/cstore check"

echo
echo "Cluster ready. Log in to the compute node and run the experiments from ~/$REMOTE_DIR:"
echo "    ssh ${SSH_OPTS[*]} $COMPUTE_SSH"
echo "    cd $REMOTE_DIR && experiments/run_all.sh"
