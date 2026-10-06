#!/usr/bin/env bash
# eks-vm.sh — public EC2 VM to run cloud-provisioner bootstraps for EKS off the laptop (create/status/start/stop/grant/revoke)
set -euo pipefail

ACTION="${1:-}"; shift || true
NAME="eks-vm-janr"
REGION="eu-west-1"
PROFILE="cloud-provisioner"
TYPE="m6a.xlarge"
DISK=32
DATA_SIZE=16
DATA_VOL=""
KEY="janr-cred"
KEY_FILE="$HOME/.ssh/janr-cred.pem"
IAM_NAME="bastion-vm-ssm"
MY_IP="" USER_NAME="" PUBKEY="" IP="" DRY=0
AUTOSTOP="18:00"
AUTOSTOP_SRC="$(dirname "$(readlink -f "$0")")/vm-autostop.sh"
TOOLS_SRC="$(dirname "$(readlink -f "$0")")/bastion-tools.sh"
TOOLS_DEFAULTS="$(dirname "$(readlink -f "$0")")/tool-versions.eks"
AS_ARGS=()
OWNER="${VM_OWNER:-$USER}"   # owner tag and name of the first SSH rule
# autostop/tools take positional args (subcommand [arg]) before the --options
if [[ "$ACTION" == autostop || "$ACTION" == tools || "$ACTION" == upload ]]; then
  while [[ $# -gt 0 && "$1" != --* ]]; do AS_ARGS+=("$1"); shift; done
fi

usage() {
  cat <<EOF
Usage: $0 <action> [options]

Actions:
  create  --my-ip IP              Create SG + IAM profile (SSM only) + VM. SSH open to IP/32 only
  status                          Instance id, state, public IP, SG rules
  start | stop                    Start/stop the VM (public IP changes on every start)
  ssh                             Print the ssh command for the admin user (ubuntu)
  grant   --user N --pubkey F --ip IP   Linux user N (docker, deployers, sudo) + SG rule IP/32 "user:N"
  revoke  --user N                Delete Linux user N and every SG rule "user:N"
  autostop <sub> [arg]            Manage after-hours auto-stop on the VM (vm-autostop.sh):
                                  install [HH:MM] | uninstall | enable [HH:MM] | disable | skip-today | cancel | status | stop-if-idle [MIN]
  tools <sub>                     asdf tools on the VM (bastion-tools.sh, defaults tool-versions.eks):
                                  sync | status | completions
  upload <file> [name]            Copy to /deployments/binaries/ (cloud-provisioner, named cloud-provisioner-<version> from
                                  '<file> version' unless [name]) /deployments/archives/ (*.tar, *.tar.gz, *.tgz) or /deployments/descriptors/ (*.yaml, *.yml; secrets* 640); never overwrites

Options:
  --name NAME (default $NAME)  --region R (default $REGION)  --profile P (default $PROFILE)
  --type T (default $TYPE)  --disk GB root (default $DISK)  --data-size GB /deployments volume (default $DATA_SIZE, created once, survives recreation)
  --key K (default $KEY)  --key-file F (default $KEY_FILE)
  --dry-run  Print mutating AWS calls instead of running them (create/grant/revoke/start/stop)
  --autostop HH:MM|off  Auto-stop time installed by create (default $AUTOSTOP, Europe/Madrid)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --type) TYPE="$2"; shift 2 ;;
    --disk) DISK="$2"; shift 2 ;;
    --data-size) DATA_SIZE="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --key-file) KEY_FILE="$2"; shift 2 ;;
    --my-ip) MY_IP="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --pubkey) PUBKEY="$2"; shift 2 ;;
    --ip) IP="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --autostop) AUTOSTOP="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_PAGER=""
SG_NAME="${NAME}-sg"

run() { if [[ $DRY -eq 1 ]]; then echo "[dry-run] $*" >&2; else "$@"; fi; }
die() { echo "ERROR: $*" >&2; echo "RESULT: FAIL at [$ACTION]"; exit 1; }
valid_ip() { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die "invalid IPv4: '$1'"; }
valid_user() { [[ "$1" =~ ^[a-z][a-z0-9_-]{1,30}$ ]] || die "invalid user name: '$1'"; }

instance_id() {
  aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text | grep -v '^None$' || true
}
public_ip() { aws ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text; }
default_vpc() { aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text; }
sg_id() {
  aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$1" \
    --query 'SecurityGroups[0].GroupId' --output text | grep -v '^None$' || true
}
remote() { ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "ubuntu@$1" "${@:2}"; }

user_data() {
  cat <<'EOF'
#!/bin/bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io jq unzip git python3 unattended-upgrades
systemctl enable --now docker unattended-upgrades
# kind documented known issue: 4 parallel bootstraps exhaust the Ubuntu inotify default (128) and kube-proxy dies with 'too many open files'
printf 'fs.inotify.max_user_watches = 524288\nfs.inotify.max_user_instances = 512\n' > /etc/sysctl.d/90-kind.conf
sysctl --system >/dev/null
EOF
  # On Nitro the EBS volume id (without '-') is the NVMe serial
  printf 'DATA_SERIAL=%s\n' "${DATA_VOL//-/}"
  cat <<'EOF'
groupadd -f -g 2000 deployers
dev=""
for _ in $(seq 1 120); do dev=$(lsblk -dnpo NAME,SERIAL | awk -v s="$DATA_SERIAL" '$2==s{print $1}'); [ -n "$dev" ] && break; sleep 5; done
[ -n "$dev" ] || { echo "data volume $DATA_SERIAL never attached"; exit 1; }
blkid "$dev" >/dev/null 2>&1 || mkfs.ext4 -q -L deployments "$dev"
mkdir -p /deployments
grep -q '^LABEL=deployments ' /etc/fstab || echo 'LABEL=deployments /deployments ext4 defaults,nofail 0 2' >> /etc/fstab
mount /deployments
install -d -m 2775 -g deployers /deployments /deployments/descriptors /deployments/binaries /deployments/archives
usermod -aG docker,deployers ubuntu
EOF
  if [[ "$AUTOSTOP" != off ]]; then
    embed_file "$AUTOSTOP_SRC" /usr/local/sbin/vm-autostop 755
    echo "/usr/local/sbin/vm-autostop install $AUTOSTOP"
  fi
  embed_file "$TOOLS_DEFAULTS" /usr/local/share/bastion-tools/tool-versions.default 644
  embed_file "$TOOLS_SRC" /usr/local/sbin/bastion-tools 755
  # asdf-managed tools last and non-fatal: a failed download must not skip the data mount or vm-autostop
  cat <<'EOF'
set +e
/usr/local/sbin/bastion-tools install || echo "WARNING: bastion-tools install failed"
/usr/local/sbin/bastion-tools sync || echo "WARNING: bastion-tools sync had failures (see above)"
touch /var/lib/cloud/instance/bastion-ready
EOF
}

# EC2 user data is limited to 16 KB raw, so embedded files travel gzipped + base64
embed_file() {
  printf "mkdir -p %s\nbase64 -d <<'B64' | gunzip > %s\n%s\nB64\nchmod %s %s\n" \
    "$(dirname "$2")" "$2" "$(gzip -9c "$1" | base64 -w 76)" "$3" "$2"
}

ensure_iam() {
  if aws iam get-instance-profile --instance-profile-name "$IAM_NAME" >/dev/null 2>&1; then
    echo "IAM instance profile $IAM_NAME already exists"; return
  fi
  run aws iam create-role --role-name "$IAM_NAME" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  run aws iam attach-role-policy --role-name "$IAM_NAME" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  run aws iam create-instance-profile --instance-profile-name "$IAM_NAME" >/dev/null
  run aws iam add-role-to-instance-profile --instance-profile-name "$IAM_NAME" --role-name "$IAM_NAME"
  # New instance profiles take a few seconds to be usable by run-instances
  [[ $DRY -eq 1 ]] || sleep 15
}

# Reuses the tagged data volume if it exists (that is what keeps /deployments across recreation), else creates it
ensure_data_volume() {
  local az="${REGION}a" found vid state vaz
  found=$(aws ec2 describe-volumes --filters "Name=tag:Name,Values=$NAME-data" \
    --query 'Volumes[0].[VolumeId,State,AvailabilityZone]' --output text)
  if [[ -n "$found" && "$found" != None* ]]; then
    read -r vid state vaz <<<"$found"
    [[ "$state" == available ]] || die "data volume $vid is '$state' (must be available — attached to another instance?)"
    [[ "$vaz" == "$az" ]] || die "data volume $vid is in $vaz, VM goes to $az"
    echo "Reusing data volume $vid ($vaz)" >&2; DATA_VOL=$vid; return
  fi
  if [[ $DRY -eq 1 ]]; then
    echo "[dry-run] aws ec2 create-volume --availability-zone $az --size $DATA_SIZE --volume-type gp3 --encrypted (Name=$NAME-data)" >&2
    DATA_VOL="vol-0dryrun0000000000"; return
  fi
  DATA_VOL=$(aws ec2 create-volume --availability-zone "$az" --size "$DATA_SIZE" --volume-type gp3 --encrypted \
    --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=$NAME-data},{Key=owner,Value=$OWNER},{Key=persistent,Value=true},{Key=NoAutoDelete,Value=true}]" \
    --query VolumeId --output text) || die "create-volume"
  aws ec2 wait volume-available --volume-ids "$DATA_VOL"
  echo "Created data volume $DATA_VOL (${DATA_SIZE}GB, $az)" >&2
}

# AWS rejects two rules with the same CIDR/port, so there is one rule per source IP and its description lists its users: user:a+b
ssh_rule_for_ip() {
  aws ec2 describe-security-group-rules --filters "Name=group-id,Values=$1" \
    --query "SecurityGroupRules[?!IsEgress && FromPort==\`22\` && CidrIpv4=='$2/32'].[SecurityGroupRuleId,Description]" --output text
}

set_rule_users() {
  run aws ec2 modify-security-group-rules --group-id "$1" --security-group-rules \
    "SecurityGroupRuleId=$2,SecurityGroupRule={IpProtocol=tcp,FromPort=22,ToPort=22,CidrIpv4=$3/32,Description=user:$4}" >/dev/null
}

add_ssh_rule() {
  local sg=$1 ip=$2 user=$3 found="" rid desc users
  [[ "$sg" == sg-DRYRUN ]] || found=$(ssh_rule_for_ip "$sg" "$ip")
  if [[ -z "$found" || "$found" == None* ]]; then
    run aws ec2 authorize-security-group-ingress --group-id "$sg" \
      --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$ip/32,Description=user:$user}]" >/dev/null
    return
  fi
  read -r rid desc <<<"$found"
  users=${desc#user:}
  if [[ "+$users+" == *"+$user+"* ]]; then echo "SG rule $ip/32 already lists $user" >&2; return; fi
  set_rule_users "$sg" "$rid" "$ip" "$users+$user"
  echo "SG rule $ip/32 now shared: $users+$user" >&2
}

# Drops a user from every rule that lists it; the rule itself goes away only when nobody is left
remove_ssh_user() {
  local sg=$1 user=$2 rid cidr desc users kept
  while read -r rid cidr desc; do
    [[ -n "$rid" ]] || continue
    users=${desc#user:}
    [[ "+$users+" == *"+$user+"* ]] || continue
    # grep exits 1 when it removes the only user; without || true, set -e would end the script silently here
    kept=$(tr '+' '\n' <<<"$users" | { grep -vx -- "$user" || true; } | paste -sd+ -)
    if [[ -z "$kept" ]]; then
      run aws ec2 revoke-security-group-ingress --group-id "$sg" --security-group-rule-ids "$rid" >/dev/null
      echo "SG rule $cidr removed (no users left)" >&2
    else
      set_rule_users "$sg" "$rid" "${cidr%/32}" "$kept"
      echo "SG rule $cidr kept for: $kept" >&2
    fi
  done < <(aws ec2 describe-security-group-rules --filters "Name=group-id,Values=$sg" \
    --query "SecurityGroupRules[?!IsEgress && starts_with(Description || '', 'user:')].[SecurityGroupRuleId,CidrIpv4,Description]" --output text)
}

do_create() {
  [[ -n "$MY_IP" ]] || die "--my-ip is required"; valid_ip "$MY_IP"
  [[ -f "$KEY_FILE" ]] || die "key file not found: $KEY_FILE"
  [[ "$AUTOSTOP" == off || -f "$AUTOSTOP_SRC" ]] || die "vm-autostop.sh not found at $AUTOSTOP_SRC"
  [[ -f "$TOOLS_SRC" && -f "$TOOLS_DEFAULTS" ]] || die "bastion-tools.sh / tool-versions.eks not found next to $AUTOSTOP_SRC"
  [[ "$AUTOSTOP" == off || "$AUTOSTOP" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || die "--autostop must be HH:MM or off"
  local existing; existing=$(instance_id)
  [[ -z "$existing" ]] || die "instance $NAME already exists: $existing (use status/start)"
  local vpc subnet ami sg
  vpc=$(default_vpc)
  subnet=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" Name=default-for-az,Values=true \
    "Name=availability-zone,Values=${REGION}a" --query 'Subnets[0].SubnetId' --output text)
  ami=$(aws ssm get-parameters --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
    --query 'Parameters[0].Value' --output text)
  echo "VPC $vpc  subnet $subnet  AMI $ami (Ubuntu 24.04)  type $TYPE  root ${DISK}GB  data ${DATA_SIZE}GB"
  ensure_data_volume

  sg=$(sg_id "$vpc")
  if [[ -z "$sg" ]]; then
    sg=$(run aws ec2 create-security-group --group-name "$SG_NAME" --vpc-id "$vpc" \
      --description "SSH to $NAME, one /32 rule per authorized user" \
      --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=$SG_NAME},{Key=owner,Value=$OWNER}]" \
      --query GroupId --output text) || die "create-security-group"
    [[ $DRY -eq 0 ]] || sg="sg-DRYRUN"
    add_ssh_rule "$sg" "$MY_IP" "$OWNER"
  else
    echo "Security group $SG_NAME already exists: $sg"
  fi
  ensure_iam

  local id
  id=$(run aws ec2 run-instances --image-id "$ami" --instance-type "$TYPE" --key-name "$KEY" \
    --subnet-id "$subnet" --security-group-ids "$sg" --associate-public-ip-address \
    --iam-instance-profile "Name=$IAM_NAME" \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$DISK,VolumeType=gp3,DeleteOnTermination=true}" \
    --user-data "$(user_data)" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=owner,Value=$OWNER},{Key=purpose,Value=cloud-provisioner-bootstrap}]" \
      "ResourceType=volume,Tags=[{Key=Name,Value=$NAME},{Key=owner,Value=$OWNER}]" \
    --query 'Instances[0].InstanceId' --output text) || die "run-instances"
  [[ $DRY -eq 0 ]] || { echo "[dry-run] aws ec2 attach-volume --volume-id $DATA_VOL --instance-id <id> --device /dev/sdf" >&2; echo "RESULT: OK — dry-run"; return; }
  echo "Instance $id launched, waiting for running..."
  aws ec2 wait instance-running --instance-ids "$id"
  # Attached after launch => DeleteOnTermination=false: terminating the VM keeps the volume
  aws ec2 attach-volume --volume-id "$DATA_VOL" --instance-id "$id" --device /dev/sdf >/dev/null || die "attach-volume"
  local pip; pip=$(public_ip "$id")
  echo "Public IP: $pip — user-data takes ~3-5 min; check: ssh -i $KEY_FILE ubuntu@$pip test -f /var/lib/cloud/instance/bastion-ready"
  echo "RESULT: OK — $NAME $id $pip"
}

do_status() {
  local id; id=$(instance_id); [[ -n "$id" ]] || die "no instance named $NAME"
  aws ec2 describe-instances --instance-ids "$id" \
    --query 'Reservations[0].Instances[0].[InstanceId,InstanceType,State.Name,PublicIpAddress,LaunchTime]' --output text
  local sg; sg=$(sg_id "$(default_vpc)")
  [[ -z "$sg" ]] || aws ec2 describe-security-group-rules --filters "Name=group-id,Values=$sg" \
    --query 'SecurityGroupRules[?!IsEgress].[CidrIpv4,FromPort,Description]' --output text
}

do_power() {
  local id; id=$(instance_id); [[ -n "$id" ]] || die "no instance named $NAME"
  if [[ "$ACTION" == "start" ]]; then
    run aws ec2 start-instances --instance-ids "$id" >/dev/null
    [[ $DRY -eq 1 ]] || { aws ec2 wait instance-running --instance-ids "$id"; echo "Public IP: $(public_ip "$id")"; }
  else
    run aws ec2 stop-instances --instance-ids "$id" >/dev/null
  fi
  echo "RESULT: OK — $ACTION $id"
}

do_grant() {
  valid_user "$USER_NAME"; valid_ip "$IP"
  [[ -f "$PUBKEY" ]] || die "pubkey file not found: $PUBKEY"
  grep -qE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+) ' "$PUBKEY" || die "$PUBKEY is not an SSH public key"
  local id pip sg key_b64
  id=$(instance_id); [[ -n "$id" ]] || die "no instance named $NAME"
  pip=$(public_ip "$id"); [[ "$pip" != "None" ]] || die "instance not running"
  sg=$(sg_id "$(default_vpc)")
  key_b64=$(base64 -w0 "$PUBKEY")
  add_ssh_rule "$sg" "$IP" "$USER_NAME"
  run remote "$pip" sudo bash -s -- "$USER_NAME" "$key_b64" <<'EOF'
set -euo pipefail
u=$1
id "$u" >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker,deployers "$u"
install -d -m 700 -o "$u" -g "$u" "/home/$u/.ssh"
echo "$2" | base64 -d > "/home/$u/.ssh/authorized_keys"
chown "$u:$u" "/home/$u/.ssh/authorized_keys"; chmod 600 "/home/$u/.ssh/authorized_keys"
echo "$u ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$u"; chmod 440 "/etc/sudoers.d/90-$u"
visudo -cf "/etc/sudoers.d/90-$u"
EOF
  echo "RESULT: OK — $USER_NAME can ssh $USER_NAME@$pip from $IP"
}

do_revoke() {
  valid_user "$USER_NAME"
  [[ "$USER_NAME" != "ubuntu" ]] || die "refusing to revoke the admin user"
  local id sg pip
  id=$(instance_id); [[ -n "$id" ]] || die "no instance named $NAME"
  sg=$(sg_id "$(default_vpc)")
  remove_ssh_user "$sg" "$USER_NAME"
  pip=$(public_ip "$id")
  if [[ "$pip" == "None" ]]; then
    echo "WARNING: instance stopped — SG rules removed, Linux user $USER_NAME still present; rerun revoke after start"
  else
    run remote "$pip" sudo bash -s -- "$USER_NAME" <<'EOF'
set -euo pipefail
pkill -u "$1" || true
id "$1" >/dev/null 2>&1 && userdel -r "$1" || true
rm -f "/etc/sudoers.d/90-$1"
EOF
  fi
  echo "RESULT: OK — revoked $USER_NAME"
}

# Same layout as the GKE VM: binaries/cloud-provisioner-<version|PLT>, archives/<tarballs and docker image tars>
do_upload() {
  [[ ${#AS_ARGS[@]} -ge 1 ]] || die "upload needs a local file: upload <file> [name]"
  local f=${AS_ARGS[0]} name=${AS_ARGS[1]:-} sub ver id pip
  [[ -f "$f" ]] || die "file not found: $f"
  case "$f" in
    *.tar|*.tar.gz|*.tgz) sub=archives; name=${name:-$(basename "$f")} ;;
    *.yaml|*.yml) sub=descriptors; name=${name:-$(basename "$f")} ;;
    *) sub=binaries
       if [[ -z "$name" ]]; then
         ver=$("$f" version 2>/dev/null | sed -nE 's/^[^ ]+ Version:([^ ]+).*/\1/p')
         [[ -n "$ver" ]] || die "cannot read a version from $f; pass the target name: upload $f cloud-provisioner-PLT-XXXX"
         name="cloud-provisioner-$ver"
       fi ;;
  esac
  id=$(instance_id); [[ -n "$id" ]] || die "no instance named $NAME"
  pip=$(public_ip "$id"); [[ "$pip" != "None" ]] || die "instance not running"
  ! remote "$pip" test -e "/deployments/$sub/$name" || die "/deployments/$sub/$name already exists on the VM (pick another name)"
  remote "$pip" "mkdir -p /deployments/$sub"
  scp -q -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "$f" "ubuntu@$pip:/deployments/$sub/$name"
  if [[ $sub == binaries ]]; then
    remote "$pip" "chmod 775 /deployments/$sub/$name && /deployments/$sub/$name version"
  else
    # secrets*.yml hold cloud credentials: owner + deployers only
    local mode=664; [[ $name == secrets* ]] && mode=640
    remote "$pip" "chmod $mode /deployments/$sub/$name && ls -lh /deployments/$sub/$name"
  fi
  echo "RESULT: OK — /deployments/$sub/$name"
}

do_tools() {
  [[ ${#AS_ARGS[@]} -gt 0 ]] || die "tools needs a subcommand: sync | status | completions"
  local id pip; id=$(instance_id); [[ -n "$id" ]] || die "no instance named $NAME"
  pip=$(public_ip "$id"); [[ "$pip" != "None" ]] || die "instance not running"
  scp -q -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "$TOOLS_SRC" "$TOOLS_DEFAULTS" "ubuntu@$pip:/tmp/"
  remote "$pip" "sudo install -D -m 644 /tmp/$(basename "$TOOLS_DEFAULTS") /usr/local/share/bastion-tools/tool-versions.default && sudo bash /tmp/bastion-tools.sh install && sudo bastion-tools ${AS_ARGS[*]}"
}

do_autostop() {
  [[ ${#AS_ARGS[@]} -gt 0 ]] || die "autostop needs a subcommand (see --help)"
  local id pip; id=$(instance_id); [[ -n "$id" ]] || die "no instance named $NAME"
  pip=$(public_ip "$id"); [[ "$pip" != "None" ]] || die "instance not running"
  if [[ "${AS_ARGS[0]}" == install ]]; then
    scp -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "$AUTOSTOP_SRC" "ubuntu@$pip:/tmp/vm-autostop.sh"
    remote "$pip" sudo bash /tmp/vm-autostop.sh "${AS_ARGS[@]}"
  else
    remote "$pip" sudo /usr/local/sbin/vm-autostop "${AS_ARGS[@]}"
  fi
}

case "$ACTION" in
  create) do_create ;;
  autostop) do_autostop ;;
  tools) do_tools ;;
  upload) do_upload ;;
  status) do_status ;;
  start|stop) do_power ;;
  ssh) id=$(instance_id); [[ -n "$id" ]] || die "no instance named $NAME"; echo "ssh -i $KEY_FILE ubuntu@$(public_ip "$id")" ;;
  grant) do_grant ;;
  revoke) do_revoke ;;
  -h|--help|"") usage ;;
  *) echo "Unknown action: $ACTION" >&2; usage; exit 1 ;;
esac
