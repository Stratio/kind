#!/usr/bin/env bash
# vm-run.sh — runs a memory-heavy command (go build/test/lint, make, docker build) on eks-vm-janr instead of the laptop.
# Syncs the working tree (uncommitted changes included, .git excluded) to /deployments/build/<dir>; one run at a time per VM.
set -euo pipefail

usage() {
  cat <<EOF
Usage: $0 [--host] [--dir DIR] [--get PATH]... [--mem 6g] [--cpus 3] -- <command>
  --host        run on the VM host (docker build) instead of the golang:<go.mod minor> container
  --dir DIR     tree to sync (default: git toplevel of the current directory)
  --get PATH    copy PATH (relative to the tree) back into the local tree after the command
  --mem/--cpus  container limits (default 6g / 3): the VM is shared with create cluster bootstraps
Exit code is the command's. Waits up to 2 h for the VM build lock if another run holds it.
EOF
}

HOST=0; DIR=""; GETS=(); MEM=6g; CPUS=3
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST=1; shift ;;
    --dir) DIR=$2; shift 2 ;;
    --get) GETS+=("$2"); shift 2 ;;
    --mem) MEM=$2; shift 2 ;;
    --cpus) CPUS=$2; shift 2 ;;
    --) shift; break ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
  esac
done
[[ $# -gt 0 ]] || { usage >&2; exit 1; }
# One arg = a shell command string; several = argv, re-quoted so `-- bash -c 'a && b'` keeps its quoting.
if [[ $# -eq 1 ]]; then CMD=$1; else CMD=$(printf '%q ' "$@"); fi
DIR=$(readlink -f "${DIR:-$(git rev-parse --show-toplevel)}")
NAME=$(basename "$DIR")
REMOTE=/deployments/build/$NAME
KEY_FILE="$HOME/.ssh/janr-cred.pem"
VM_SH="$(dirname "$(readlink -f "$0")")/eks-vm.sh"

read -r _ _ STATE IP _ < <(bash "$VM_SH" status | head -1)
[[ "$STATE" == running ]] || { echo "eks-vm-janr is $STATE — start it: bash $VM_SH start" >&2; exit 1; }
SSH_OPTS=(-i "$KEY_FILE" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
q() { printf '%q' "$1"; }

if [[ $HOST -eq 1 ]]; then
  RUN="cd $(q "$REMOTE") && bash -c $(q "$CMD")"
else
  [[ -f "$DIR/go.mod" ]] || { echo "no go.mod in $DIR — use --host" >&2; exit 1; }
  GOVER=$(awk '$1=="toolchain"{sub(/^go/,"",$2); print $2; exit} $1=="go"{v=$2} END{if(v)print v}' "$DIR/go.mod" | head -1)
  IMG="golang:$(cut -d. -f1,2 <<<"$GOVER")"
  RUN="docker run --rm --memory $MEM --cpus $CPUS -v $(q "$REMOTE"):/src -w /src -v vmrun-gomod:/go/pkg/mod -v vmrun-gocache:/root/.cache/go-build -e GOTOOLCHAIN=auto $IMG bash -c $(q "$CMD")"
fi

echo "vm-run: syncing $DIR -> eks-vm-janr:$REMOTE" >&2
ssh "${SSH_OPTS[@]}" "ubuntu@$IP" "sudo install -d -m 755 /deployments/build"
rsync -az --delete --rsync-path="sudo rsync" -e "ssh ${SSH_OPTS[*]}" \
  --exclude .git --exclude /bin/ --exclude /_artifacts/ --exclude /dist/ "$DIR/" "ubuntu@$IP:$REMOTE/"

echo "vm-run: running on eks-vm-janr ($IP)${IMG:+ in $IMG}: $CMD" >&2
set +e
ssh "${SSH_OPTS[@]}" "ubuntu@$IP" "sudo flock -w 7200 /deployments/build/.lock bash -c $(q "$RUN")"
rc=$?
set -e

for p in "${GETS[@]}"; do
  mkdir -p "$DIR/$(dirname "$p")"
  rsync -az --rsync-path="sudo rsync" -e "ssh ${SSH_OPTS[*]}" "ubuntu@$IP:$REMOTE/$p" "$DIR/$(dirname "$p")/" && echo "vm-run: fetched $p" >&2
done
echo "RESULT: $([[ $rc -eq 0 ]] && echo OK || echo "FAIL rc=$rc") — $NAME: $CMD" >&2
exit $rc
