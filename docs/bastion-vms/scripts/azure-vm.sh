#!/usr/bin/env bash
# azure-vm.sh — public Azure VM to run cloud-provisioner bootstraps off the laptop (create/status/start/stop/grant/revoke/autostop)
set -euo pipefail

ACTION="${1:-}"; shift || true
NAME="azure-vm-janr"
RG="bastion-vm-janr"
LOCATION="westeurope"
SIZE="Standard_D4as_v5"
DISK=32
DATA_SIZE=16
IMAGE="Canonical:ubuntu-24_04-lts:server:latest"
ADMIN="azureuser"
KEY_FILE="$HOME/.ssh/azure_rsa"
MY_IP="" USER_NAME="" PUBKEY="" IP="" DRY=0
AUTOSTOP="18:00"
AUTOSTOP_SRC="$(dirname "$(readlink -f "$0")")/vm-autostop.sh"
TOOLS_SRC="$(dirname "$(readlink -f "$0")")/bastion-tools.sh"
TOOLS_DEFAULTS="$(dirname "$(readlink -f "$0")")/tool-versions.azure"
AS_ARGS=()
OWNER="${VM_OWNER:-$USER}"   # owner tag and name of the first SSH rule
# autostop/tools take positional args (subcommand [arg]) before the --options
if [[ "$ACTION" == autostop || "$ACTION" == tools || "$ACTION" == upload || "$ACTION" == kubeconfig ]]; then
  while [[ $# -gt 0 && "$1" != --* ]]; do AS_ARGS+=("$1"); shift; done
fi

usage() {
  cat <<EOF
Usage: $0 <action> [options]

Actions:
  create  --my-ip IP              RG + NSG + VM (system identity, VM Contributor on itself) — SSH open to IP/32 only
  status                          Power state, public IP, NSG rules
  start | stop                    Start / DEALLOCATE (a plain stop keeps billing compute)
  ssh                             Print the ssh command for the admin user ($ADMIN)
  grant   --user N --pubkey F --ip IP   Linux user N (docker, deployers, sudo) + NSG rule user-N from IP/32
  revoke  --user N                Delete Linux user N and NSG rule user-N
  autostop <sub> [arg]            Manage after-hours auto-stop on the VM (vm-autostop.sh):
                                  install [HH:MM] | uninstall | enable [HH:MM] | disable | skip-today | cancel | status | stop-if-idle [MIN]
  tools <sub>                     asdf tools + az on the VM (bastion-tools.sh, defaults tool-versions.azure):
                                  sync | status | completions | install-az
  upload <file> [name]            Copy to /deployments/binaries/ (cloud-provisioner, named cloud-provisioner-<version> from
                                  '<file> version' unless [name]) /deployments/archives/ (*.tar, *.tar.gz, *.tgz) or /deployments/descriptors/ (*.yaml, *.yml; secrets* 640); never overwrites
  kubeconfig <cluster>            Copy /deployments/<cluster>/.kube/config (root 0600) to ~/.kube/<cluster>.kubeconfig (0600)

Options:
  --name NAME (default $NAME)  --rg RG (default $RG)  --location L (default $LOCATION)
  --size S (default $SIZE)  --disk GB root (default $DISK)  --key-file F (default $KEY_FILE, .pub must exist)
  --data-size GB  /deployments disk (default $DATA_SIZE), lives in RG <rg>-data, created once, survives VM/RG deletion
  --dry-run  Print mutating az calls instead of running them
  --autostop HH:MM|off  Auto-stop time installed by create (default $AUTOSTOP, Europe/Madrid, method azure-deallocate)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --rg) RG="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --size) SIZE="$2"; shift 2 ;;
    --disk) DISK="$2"; shift 2 ;;
    --data-size) DATA_SIZE="$2"; shift 2 ;;
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

NSG="${NAME}-nsg"
DATA_RG="${RG}-data"
DATA_DISK="${NAME}-data"
export AZURE_CORE_ONLY_SHOW_ERRORS=1

run() { if [[ $DRY -eq 1 ]]; then echo "[dry-run] $*" >&2; else "$@"; fi; }
die() { echo "ERROR: $*" >&2; echo "RESULT: FAIL at [$ACTION]"; exit 1; }
valid_ip() { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die "invalid IPv4: '$1'"; }
valid_user() { [[ "$1" =~ ^[a-z][a-z0-9_-]{1,30}$ ]] || die "invalid user name: '$1'"; }

vm_exists() { az vm show -g "$RG" -n "$NAME" --query id -o tsv >/dev/null 2>&1; }
public_ip() { az vm show -d -g "$RG" -n "$NAME" --query publicIps -o tsv; }
power_state() { az vm show -d -g "$RG" -n "$NAME" --query powerState -o tsv; }
remote() { ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "$ADMIN@$1" "${@:2}"; }

# First free NSG priority from 1000 upward
next_priority() {
  local used p=1000
  used=$(az network nsg rule list -g "$RG" --nsg-name "$NSG" --query '[].priority' -o tsv 2>/dev/null || true)
  while grep -qx "$p" <<<"$used"; do p=$((p + 10)); done
  echo "$p"
}

add_ssh_rule() {
  run az network nsg rule create -g "$RG" --nsg-name "$NSG" -n "user-$2" --priority "$(next_priority)" \
    --direction Inbound --access Allow --protocol Tcp --destination-port-ranges 22 \
    --source-address-prefixes "$1/32" --description "user:$2" -o none
}

custom_data() {
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
groupadd -f -g 2000 deployers
dev=/dev/disk/azure/scsi1/lun0
for _ in $(seq 1 60); do [ -e "$dev" ] && break; sleep 5; done
[ -e "$dev" ] || { echo "data disk LUN0 not found"; exit 1; }
dev=$(readlink -f "$dev")
blkid "$dev" >/dev/null 2>&1 || mkfs.ext4 -q -L deployments "$dev"
mkdir -p /deployments
grep -q '^LABEL=deployments ' /etc/fstab || echo 'LABEL=deployments /deployments ext4 defaults,nofail 0 2' >> /etc/fstab
mount /deployments
install -d -m 2775 -g deployers /deployments /deployments/descriptors /deployments/binaries /deployments/archives
EOF
  echo "usermod -aG docker,deployers $ADMIN"
  if [[ "$AUTOSTOP" != off ]]; then
    embed_file "$AUTOSTOP_SRC" /usr/local/sbin/vm-autostop 755
    echo "/usr/local/sbin/vm-autostop install $AUTOSTOP azure-deallocate"
  fi
  embed_file "$TOOLS_DEFAULTS" /usr/local/share/bastion-tools/tool-versions.default 644
  embed_file "$TOOLS_SRC" /usr/local/sbin/bastion-tools 755
  # Tools last and non-fatal: a failed download must not skip the data mount or vm-autostop
  cat <<'EOF'
set +e
/usr/local/sbin/bastion-tools install || echo "WARNING: bastion-tools install failed"
/usr/local/sbin/bastion-tools install-az || echo "WARNING: azure-cli install failed"
/usr/local/sbin/bastion-tools sync || echo "WARNING: bastion-tools sync had failures (see above)"
touch /var/lib/cloud/instance/bastion-ready
EOF
}

# Embedded files travel gzipped + base64 (same format as the EKS user data)
embed_file() {
  printf "mkdir -p %s\nbase64 -d <<'B64' | gunzip > %s\n%s\nB64\nchmod %s %s\n" \
    "$(dirname "$2")" "$2" "$(gzip -9c "$1" | base64 -w 76)" "$3" "$2"
}

# Reuses the data disk if it exists (that is what keeps /deployments across recreation), else creates it; prints its id
ensure_data_disk() {
  local id state
  id=$(az disk show -g "$DATA_RG" -n "$DATA_DISK" --query id -o tsv 2>/dev/null || true)
  if [[ -n "$id" ]]; then
    state=$(az disk show --ids "$id" --query diskState -o tsv)
    [[ "$state" == Unattached ]] || die "data disk $DATA_DISK is '$state' (must be Unattached — attached to another VM?)"
    echo "Reusing data disk $DATA_DISK ($DATA_RG)" >&2; ensure_data_lock; echo "$id"; return
  fi
  run az group create -n "$DATA_RG" -l "$LOCATION" --tags owner=$OWNER persistent=true -o none >&2
  if [[ $DRY -eq 1 ]]; then
    echo "[dry-run] az disk create -g $DATA_RG -n $DATA_DISK --size-gb $DATA_SIZE --sku StandardSSD_LRS" >&2
    ensure_data_lock
    echo "/subscriptions/<sub>/resourceGroups/$DATA_RG/providers/Microsoft.Compute/disks/$DATA_DISK"; return
  fi
  local id_new
  id_new=$(az disk create -g "$DATA_RG" -n "$DATA_DISK" -l "$LOCATION" --size-gb "$DATA_SIZE" --sku StandardSSD_LRS \
    --tags owner=$OWNER persistent=true --query id -o tsv) || return 1
  ensure_data_lock
  echo "$id_new"
}

# CanNotDelete on the data RG: blocks deleting the RG or the disk, still allows attach/detach (modify)
ensure_data_lock() {
  if [[ -n "$(az lock list -g "$DATA_RG" --query "[?level=='CanNotDelete'].name" -o tsv 2>/dev/null)" ]]; then
    echo "Data RG $DATA_RG already has a CanNotDelete lock" >&2; return
  fi
  run az lock create --name "${DATA_DISK}-nodelete" --lock-type CanNotDelete --resource-group "$DATA_RG" \
    --notes "Persistent /deployments disk of $NAME - remove this lock on purpose before deleting" -o none >&2
}

do_create() {
  [[ -n "$MY_IP" ]] || die "--my-ip is required"; valid_ip "$MY_IP"
  [[ -f "$KEY_FILE.pub" ]] || die "public key not found: $KEY_FILE.pub"
  [[ "$AUTOSTOP" == off || -f "$AUTOSTOP_SRC" ]] || die "vm-autostop.sh not found at $AUTOSTOP_SRC"
  [[ -f "$TOOLS_SRC" && -f "$TOOLS_DEFAULTS" ]] || die "bastion-tools.sh / tool-versions.azure not found next to $AUTOSTOP_SRC"
  [[ "$AUTOSTOP" == off || "$AUTOSTOP" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || die "--autostop must be HH:MM or off"
  ! vm_exists || die "VM $NAME already exists in $RG (use status/start)"
  echo "Subscription: $(az account show --query name -o tsv)  RG $RG ($LOCATION)  size $SIZE  root ${DISK}GB  data ${DATA_SIZE}GB  image $IMAGE"

  local data_id; data_id=$(ensure_data_disk) || die "data disk"
  local cd; cd=$(mktemp); custom_data > "$cd"
  # az vm create encodes --custom-data as latin-1 and aborts on anything else (seen with an em dash)
  if LC_ALL=C grep -q $'[\x80-\xff]' "$cd"; then rm -f "$cd"; die "custom-data has non-ASCII characters (az CLI rejects them)"; fi
  run az group create -n "$RG" -l "$LOCATION" --tags owner=$OWNER purpose=cloud-provisioner-bootstrap -o none
  if az network nsg show -g "$RG" -n "$NSG" -o none 2>/dev/null; then
    echo "NSG $NSG already exists"
  else
    run az network nsg create -g "$RG" -n "$NSG" -l "$LOCATION" --tags owner=$OWNER -o none
    add_ssh_rule "$MY_IP" "$OWNER"
  fi
  run az vm create -g "$RG" -n "$NAME" -l "$LOCATION" --image "$IMAGE" --size "$SIZE" \
    --admin-username "$ADMIN" --ssh-key-values "$KEY_FILE.pub" \
    --nsg "$NSG" --nsg-rule NONE --public-ip-sku Standard \
    --os-disk-size-gb "$DISK" --storage-sku StandardSSD_LRS --attach-data-disks "$data_id" \
    --assign-identity '[system]' --custom-data "$cd" \
    --tags owner=$OWNER purpose=cloud-provisioner-bootstrap -o none
  rm -f "$cd"
  [[ $DRY -eq 0 ]] || { run az role assignment create --role "Virtual Machine Contributor" --assignee-object-id "<vm-principal-id>" --scope "<vm-id>"; echo "RESULT: OK — dry-run"; return; }

  local vmid pid
  vmid=$(az vm show -g "$RG" -n "$NAME" --query id -o tsv)
  pid=$(az vm show -g "$RG" -n "$NAME" --query identity.principalId -o tsv)
  # Lets vm-autostop deallocate this VM (and only this VM) through ARM
  az role assignment create --role "Virtual Machine Contributor" --assignee-object-id "$pid" \
    --assignee-principal-type ServicePrincipal --scope "$vmid" -o none
  local pip; pip=$(public_ip)
  echo "Public IP: $pip (static) — cloud-init takes ~3-5 min; check: ssh -i $KEY_FILE $ADMIN@$pip test -f /var/lib/cloud/instance/bastion-ready"
  echo "RESULT: OK — $NAME $pip"
}

do_status() {
  vm_exists || die "no VM $NAME in $RG"
  az vm show -d -g "$RG" -n "$NAME" --query '[name,hardwareProfile.vmSize,powerState,publicIps]' -o tsv
  az network nsg rule list -g "$RG" --nsg-name "$NSG" --query '[].[name,sourceAddressPrefix,destinationPortRange,priority]' -o tsv
}

do_power() {
  vm_exists || die "no VM $NAME in $RG"
  if [[ "$ACTION" == "start" ]]; then
    run az vm start -g "$RG" -n "$NAME" -o none
    [[ $DRY -eq 1 ]] || echo "Public IP: $(public_ip)"
  else
    run az vm deallocate -g "$RG" -n "$NAME" -o none
  fi
  echo "RESULT: OK — $ACTION $NAME"
}

do_grant() {
  valid_user "$USER_NAME"; valid_ip "$IP"
  [[ -f "$PUBKEY" ]] || die "pubkey file not found: $PUBKEY"
  grep -qE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+) ' "$PUBKEY" || die "$PUBKEY is not an SSH public key"
  [[ "$(power_state)" == "VM running" ]] || die "VM not running"
  local pip key_b64; pip=$(public_ip); key_b64=$(base64 -w0 "$PUBKEY")
  add_ssh_rule "$IP" "$USER_NAME"
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
  [[ "$USER_NAME" != "$ADMIN" ]] || die "refusing to revoke the admin user"
  vm_exists || die "no VM $NAME in $RG"
  if az network nsg rule show -g "$RG" --nsg-name "$NSG" -n "user-$USER_NAME" -o none 2>/dev/null; then
    run az network nsg rule delete -g "$RG" --nsg-name "$NSG" -n "user-$USER_NAME"
  fi
  if [[ "$(power_state)" != "VM running" ]]; then
    echo "WARNING: VM not running — NSG rule removed, Linux user $USER_NAME still present; rerun revoke after start"
  else
    run remote "$(public_ip)" sudo bash -s -- "$USER_NAME" <<'EOF'
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
  local f=${AS_ARGS[0]} name=${AS_ARGS[1]:-} sub ver pip
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
  [[ "$(power_state)" == "VM running" ]] || die "VM not running"
  pip=$(public_ip)
  ! remote "$pip" test -e "/deployments/$sub/$name" || die "/deployments/$sub/$name already exists on the VM (pick another name)"
  remote "$pip" "mkdir -p /deployments/$sub"
  scp -q -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "$f" "$ADMIN@$pip:/deployments/$sub/$name"
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
  [[ ${#AS_ARGS[@]} -gt 0 ]] || die "tools needs a subcommand: sync | status | completions | install-az"
  [[ "$(power_state)" == "VM running" ]] || die "VM not running"
  local pip; pip=$(public_ip)
  scp -q -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "$TOOLS_SRC" "$TOOLS_DEFAULTS" "$ADMIN@$pip:/tmp/"
  remote "$pip" "sudo install -D -m 644 /tmp/$(basename "$TOOLS_DEFAULTS") /usr/local/share/bastion-tools/tool-versions.default && sudo bash /tmp/bastion-tools.sh install && sudo bastion-tools ${AS_ARGS[*]}"
}

do_autostop() {
  [[ ${#AS_ARGS[@]} -gt 0 ]] || die "autostop needs a subcommand (see --help)"
  [[ "$(power_state)" == "VM running" ]] || die "VM not running"
  local pip; pip=$(public_ip)
  if [[ "${AS_ARGS[0]}" == install ]]; then
    scp -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "$AUTOSTOP_SRC" "$ADMIN@$pip:/tmp/vm-autostop.sh"
    remote "$pip" sudo bash /tmp/vm-autostop.sh install "${AS_ARGS[1]:-18:00}" azure-deallocate
  else
    remote "$pip" sudo /usr/local/sbin/vm-autostop "${AS_ARGS[@]}"
  fi
}

# Workload kubeconfig is root 0600 on the VM, so scp as $ADMIN cannot read it: sudo cat over ssh
do_kubeconfig() {
  local c=${AS_ARGS[0]:-} out tmp
  [[ "$c" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "kubeconfig needs a cluster name: kubeconfig <cluster>"
  [[ "$(power_state)" == "VM running" ]] || die "VM not running"
  out="$HOME/.kube/$c.kubeconfig"; mkdir -p "$HOME/.kube"
  tmp=$(mktemp "$HOME/.kube/.$c.XXXXXX"); chmod 600 "$tmp"
  remote "$(public_ip)" "sudo cat /deployments/$c/.kube/config" >"$tmp" || { rm -f "$tmp"; die "no /deployments/$c/.kube/config on the VM"; }
  grep -q '^apiVersion:' "$tmp" || { rm -f "$tmp"; die "not a kubeconfig: /deployments/$c/.kube/config"; }
  mv "$tmp" "$out"
  echo "kubectl --kubeconfig $out get nodes"
  echo "RESULT: OK — kubeconfig $out"
}

case "$ACTION" in
  create) do_create ;;
  status) do_status ;;
  start|stop) do_power ;;
  ssh) vm_exists || die "no VM $NAME in $RG"; echo "ssh -i $KEY_FILE $ADMIN@$(public_ip)" ;;
  grant) do_grant ;;
  revoke) do_revoke ;;
  autostop) do_autostop ;;
  tools) do_tools ;;
  upload) do_upload ;;
  kubeconfig) do_kubeconfig ;;
  -h|--help|"") usage ;;
  *) echo "Unknown action: $ACTION" >&2; usage; exit 1 ;;
esac
