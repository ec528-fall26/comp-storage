#!/usr/bin/env bash
# Create the two EC2 instances for the demo: a storage node and a compute node,
# same instance type, same subnet (one AZ, so no cross-AZ transfer charges).
# Run on your machine from the repository root, with AWS CLI v2 configured
# for the account holding the course credits:
#
#     infra/aws/up.sh            # then: infra/deploy.sh
#     infra/aws/down.sh          # when finished: terminates everything this created
#
# Settings: AWS_REGION (us-east-1), INSTANCE_TYPE (m6i.xlarge: 4 vCPU, 16 GiB),
# CSTORE_NAME (cstore-demo2; tags and names every resource).
# Cost: two m6i.xlarge are about $0.38/hour in us-east-1, plus ~$12/month of
# gp3 storage while the volumes exist. Run infra/aws/down.sh when done.
set -euo pipefail
cd "$(dirname "$0")/../.."

REGION=${AWS_REGION:-us-east-1}
TYPE=${INSTANCE_TYPE:-m6i.xlarge}
NAME=${CSTORE_NAME:-cstore-demo2}
STATE=infra/aws/.state
export AWS_DEFAULT_REGION=$REGION AWS_PAGER=""

command -v aws >/dev/null || { echo "AWS CLI v2 is required: https://aws.amazon.com/cli/" >&2; exit 1; }
[ ! -f "$STATE/hosts.env" ] || { echo "$STATE/hosts.env exists: instances may already be up (run infra/aws/down.sh first)" >&2; exit 1; }
mkdir -p "$STATE"
chmod 700 "$STATE"

aws sts get-caller-identity --query Arn --output text

echo "== Ubuntu 24.04 AMI"
AMI=$(aws ssm get-parameter \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
echo "$AMI"

echo "== network: default VPC, one default subnet"
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
[ "$VPC" != "None" ] || { echo "no default VPC in $REGION" >&2; exit 1; }
SUBNET=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC" Name=default-for-az,Values=true \
  --query 'sort_by(Subnets, &AvailabilityZone)[0].SubnetId' --output text)
echo "vpc $VPC subnet $SUBNET"

echo "== key pair $NAME (private key in $STATE/$NAME.pem)"
aws ec2 create-key-pair --key-name "$NAME" --key-type ed25519 \
  --tag-specifications "ResourceType=key-pair,Tags=[{Key=Project,Value=$NAME}]" \
  --query KeyMaterial --output text > "$STATE/$NAME.pem"
chmod 600 "$STATE/$NAME.pem"

echo "== security group: ssh from this machine only; all traffic between the two nodes"
SG=$(aws ec2 create-security-group --group-name "$NAME" --description "cstore demo nodes" --vpc-id "$VPC" \
  --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=$NAME}]" \
  --query GroupId --output text)
MYIP=$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP/32" >/dev/null
aws ec2 authorize-security-group-ingress --group-id "$SG" \
  --ip-permissions "IpProtocol=-1,UserIdGroupPairs=[{GroupId=$SG}]" >/dev/null
echo "$SG (ssh allowed from $MYIP)"

launch() {  # launch ROLE ROOT_GB
  aws ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" --count 1 \
    --key-name "$NAME" --security-group-ids "$SG" --subnet-id "$SUBNET" \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$2,VolumeType=gp3,Iops=3000,Throughput=250,DeleteOnTermination=true}" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME-$1},{Key=Project,Value=$NAME}]" \
                         "ResourceType=volume,Tags=[{Key=Project,Value=$NAME}]" \
    --query 'Instances[0].InstanceId' --output text
}
echo "== instances ($TYPE)"
STORAGE_ID=$(launch storage 100)
COMPUTE_ID=$(launch compute 40)
echo "storage $STORAGE_ID, compute $COMPUTE_ID; waiting until both pass status checks (~3 min)"
aws ec2 wait instance-status-ok --instance-ids "$STORAGE_ID" "$COMPUTE_ID"

addr() { aws ec2 describe-instances --instance-ids "$1" --query "Reservations[0].Instances[0].$2" --output text; }
cat > "$STATE/hosts.env" <<EOF
# Written by infra/aws/up.sh on $(date -u +%Y-%m-%dT%H:%MZ); used by infra/deploy.sh and infra/aws/down.sh
AWS_REGION=$REGION
CSTORE_NAME=$NAME
SSH_KEY=$STATE/$NAME.pem
SG_ID=$SG
STORAGE_ID=$STORAGE_ID
COMPUTE_ID=$COMPUTE_ID
STORAGE_SSH=ubuntu@$(addr "$STORAGE_ID" PublicIpAddress)
COMPUTE_SSH=ubuntu@$(addr "$COMPUTE_ID" PublicIpAddress)
STORAGE_PRIVATE_IP=$(addr "$STORAGE_ID" PrivateIpAddress)
COMPUTE_PRIVATE_IP=$(addr "$COMPUTE_ID" PrivateIpAddress)
EOF
cat "$STATE/hosts.env"
echo
echo "Instances are up. Next: infra/deploy.sh"
