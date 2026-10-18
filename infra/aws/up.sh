#!/usr/bin/env bash
# Create the two EC2 instances for the demo: a storage node and a compute node,
# same instance type, same subnet (one AZ, so no cross-AZ transfer charges).
# Run on your machine from the repository root, with AWS CLI v2 configured
# for the account holding the course credits:
#
#     infra/aws/up.sh            # then: infra/deploy.sh
#     infra/aws/down.sh          # when finished (or if this script fails part way)
#
# Settings: AWS_REGION (us-east-1), INSTANCE_TYPE (m6i.2xlarge: 8 vCPU, 32 GiB,
# the type the design document's measurements used), CSTORE_NAME (cstore-demo2;
# tags and names every resource). The first measured run used
# AWS_REGION=us-east-2; any region works.
# Cost: two m6i.2xlarge are about $0.77/hour in us-east-1/us-east-2, plus
# ~$12/month of gp3 storage while the volumes exist. Run infra/aws/down.sh when done.
#
# New AWS accounts can have an on-demand vCPU quota (per region) below the 16
# vCPUs this needs; run-instances then fails with VcpuLimitExceeded. Request
# more in Service Quotas ("Running On-Demand Standard ... instances",
# L-1216C47A), or use INSTANCE_TYPE=m6i.xlarge (4 vCPU, 16 GiB per node, needs
# 8 vCPUs; conversions take about twice as long).
set -euo pipefail
cd "$(dirname "$0")/../.."

REGION=${AWS_REGION:-us-east-1}
TYPE=${INSTANCE_TYPE:-m6i.2xlarge}
NAME=${CSTORE_NAME:-cstore-demo2}
STATE=infra/aws/.state
ENV_FILE=$STATE/hosts.env
export AWS_DEFAULT_REGION=$REGION AWS_PAGER=""

command -v aws >/dev/null || { echo "AWS CLI v2 is required: https://aws.amazon.com/cli/" >&2; exit 1; }
[ ! -f "$ENV_FILE" ] || { echo "$ENV_FILE exists: resources may already exist (run infra/aws/down.sh first)" >&2; exit 1; }
mkdir -p "$STATE"
chmod 700 "$STATE"

# Record each resource as soon as it exists, so infra/aws/down.sh can remove
# whatever was created even if a later step fails.
record() { echo "$1=$2" >> "$ENV_FILE"; }
trap 'echo; echo "up.sh failed part way; run infra/aws/down.sh to remove what it created" >&2' ERR

aws sts get-caller-identity --query Arn --output text
{
  echo "# Written by infra/aws/up.sh on $(date -u +%Y-%m-%dT%H:%MZ); used by infra/deploy.sh and infra/aws/down.sh"
  echo "AWS_REGION=$REGION"
  echo "CSTORE_NAME=$NAME"
} > "$ENV_FILE"

echo "== Ubuntu 24.04 AMI"
# Canonical's published SSM parameter; accounts whose organization blocks SSM
# fall back to searching Canonical's own images (owner 099720109477) in EC2.
# AMI_ID=ami-... skips the lookup.
AMI=${AMI_ID:-}
if [ -z "$AMI" ] && ! AMI=$(aws ssm get-parameter \
     --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
     --query Parameter.Value --output text 2>/dev/null); then
  echo "(SSM is not available to this account; searching Canonical's images instead)"
  AMI=$(aws ec2 describe-images --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" Name=state,Values=available \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text)
fi
case $AMI in ami-*) ;; *) echo "no Ubuntu 24.04 AMI found (got '$AMI'); set AMI_ID=ami-..." >&2; exit 1 ;; esac
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
record KEY_NAME "$NAME"
record SSH_KEY "$STATE/$NAME.pem"

echo "== security group: ssh from this machine only; all traffic between the two nodes"
SG=$(aws ec2 create-security-group --group-name "$NAME" --description "cstore demo nodes" --vpc-id "$VPC" \
  --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=$NAME}]" \
  --query GroupId --output text)
record SG_ID "$SG"
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
record STORAGE_ID "$STORAGE_ID"
COMPUTE_ID=$(launch compute 40)
record COMPUTE_ID "$COMPUTE_ID"
echo "storage $STORAGE_ID, compute $COMPUTE_ID; waiting until both pass status checks (~3 min)"
aws ec2 wait instance-status-ok --instance-ids "$STORAGE_ID" "$COMPUTE_ID"

addr() { aws ec2 describe-instances --instance-ids "$1" --query "Reservations[0].Instances[0].$2" --output text; }
record STORAGE_SSH "ubuntu@$(addr "$STORAGE_ID" PublicIpAddress)"
record COMPUTE_SSH "ubuntu@$(addr "$COMPUTE_ID" PublicIpAddress)"
record STORAGE_PRIVATE_IP "$(addr "$STORAGE_ID" PrivateIpAddress)"
record COMPUTE_PRIVATE_IP "$(addr "$COMPUTE_ID" PrivateIpAddress)"
cat "$ENV_FILE"
echo
echo "Instances are up. Next: infra/deploy.sh"
