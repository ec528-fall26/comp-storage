#!/usr/bin/env bash
# Delete everything infra/aws/up.sh created -- both instances (their volumes go
# with them), the security group and the key pair -- including after a partial
# up.sh run.
#     infra/aws/down.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
STATE=infra/aws/.state
[ -f "$STATE/hosts.env" ] || { echo "nothing to tear down ($STATE/hosts.env not found)" >&2; exit 1; }
# shellcheck disable=SC1091
source "$STATE/hosts.env"
export AWS_DEFAULT_REGION=$AWS_REGION AWS_PAGER=""

ids=()
[ -n "${STORAGE_ID:-}" ] && ids+=("$STORAGE_ID")
[ -n "${COMPUTE_ID:-}" ] && ids+=("$COMPUTE_ID")
if [ ${#ids[@]} -gt 0 ]; then
  echo "== terminating ${ids[*]}"
  aws ec2 terminate-instances --instance-ids "${ids[@]}" >/dev/null
  aws ec2 wait instance-terminated --instance-ids "${ids[@]}"
fi
if [ -n "${SG_ID:-}" ]; then
  echo "== deleting security group $SG_ID"
  aws ec2 delete-security-group --group-id "$SG_ID"
fi
if [ -n "${KEY_NAME:-}" ]; then
  echo "== deleting key pair $KEY_NAME"
  aws ec2 delete-key-pair --key-name "$KEY_NAME"
fi

echo "== leftovers tagged Project=$CSTORE_NAME (should print nothing)"
aws ec2 describe-instances --filters "Name=tag:Project,Values=$CSTORE_NAME" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" --query 'Reservations[].Instances[].InstanceId' --output text
aws ec2 describe-volumes --filters "Name=tag:Project,Values=$CSTORE_NAME" --query 'Volumes[].VolumeId' --output text

rm -rf "$STATE"
echo "done"
