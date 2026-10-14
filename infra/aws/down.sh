#!/usr/bin/env bash
# Delete everything infra/aws/up.sh created: both instances (their volumes go
# with them), the security group and the key pair.
#     infra/aws/down.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
STATE=infra/aws/.state
[ -f "$STATE/hosts.env" ] || { echo "nothing to tear down ($STATE/hosts.env not found)" >&2; exit 1; }
# shellcheck disable=SC1091
source "$STATE/hosts.env"
export AWS_DEFAULT_REGION=$AWS_REGION AWS_PAGER=""

echo "== terminating $STORAGE_ID $COMPUTE_ID"
aws ec2 terminate-instances --instance-ids "$STORAGE_ID" "$COMPUTE_ID" >/dev/null
aws ec2 wait instance-terminated --instance-ids "$STORAGE_ID" "$COMPUTE_ID"

echo "== deleting security group $SG_ID and key pair $CSTORE_NAME"
aws ec2 delete-security-group --group-id "$SG_ID"
aws ec2 delete-key-pair --key-name "$CSTORE_NAME"

echo "== leftovers tagged Project=$CSTORE_NAME (should be none)"
aws ec2 describe-volumes --filters "Name=tag:Project,Values=$CSTORE_NAME" --query 'Volumes[].VolumeId' --output text

rm -rf "$STATE"
echo "done"
