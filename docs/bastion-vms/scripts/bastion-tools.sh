#!/usr/bin/env bash
# bastion-tools - asdf-managed CLI tools and bash completions on the bastion VMs (run as root)
set -euo pipefail

# cloud-init runs user data without HOME and asdf >= 0.16 refuses to start without it ("error loading config")
export HOME=${HOME:-/root}
BIN=/usr/local/sbin/bastion-tools
ASDF_VERSION=${ASDF_VERSION:-v0.20.2}
export ASDF_DATA_DIR=/deployments/.asdf
TOOLS_FILE=/deployments/.tool-versions
DEFAULTS=/usr/local/share/bastion-tools/tool-versions.default
COMP_DIR=/usr/share/bash-completion/completions

usage() {
  cat <<EOF
Usage: bastion-tools <command>   (run as root)

  install           Install this script, asdf $ASDF_VERSION (binary) and /etc/profile.d/asdf.sh
  install-az        Azure CLI from Microsoft's apt repo (Azure VM only)
  sync              Add missing asdf plugins, 'asdf install' everything in $TOOLS_FILE, regenerate completions
                    ($TOOLS_FILE is seeded from $DEFAULTS only if it does not exist)
  completions       Regenerate bash completions only
  status            asdf version, default tool versions, completions present

Per-cluster versions: put a .tool-versions in /deployments/<cluster>/ (asdf resolves it walking up from the cwd),
then run 'bastion-tools sync' from that folder or 'asdf install' there.
EOF
}

log() { echo "bastion-tools: $*"; }
need_root() { [[ $EUID -eq 0 ]] || { echo "run as root (sudo -i)" >&2; exit 1; }; }

cmd_install() {
  # Refuse stdin runs (bash -s): $0 would be the shell itself, not this script
  [[ -f "$0" ]] && grep -q '^# bastion-tools' "$0" || { echo "run install from the script file" >&2; exit 1; }
  [[ "$(readlink -f "$0")" == "$BIN" ]] || install -m 755 "$0" "$BIN"
  if ! /usr/local/bin/asdf version 2>/dev/null | grep -q "${ASDF_VERSION#v}"; then
    local tmp; tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/asdf.tgz" "https://github.com/asdf-vm/asdf/releases/download/$ASDF_VERSION/asdf-$ASDF_VERSION-linux-amd64.tar.gz"
    tar -xzf "$tmp/asdf.tgz" -C "$tmp" asdf
    install -m 755 "$tmp/asdf" /usr/local/bin/asdf
    rm -rf "$tmp"
  fi
  install -d -m 2775 -g deployers "$ASDF_DATA_DIR"
  # Outside /deployments asdf falls back to ~/.tool-versions; /etc/skel covers users added later by grant (useradd -m)
  local h
  for h in /root /etc/skel /home/*; do
    if [[ -d "$h" && ! -e "$h/.tool-versions" && ! -L "$h/.tool-versions" ]]; then ln -s "$TOOLS_FILE" "$h/.tool-versions"; fi
  done
  cat > /etc/profile.d/asdf.sh <<EOF
export ASDF_DATA_DIR=$ASDF_DATA_DIR
export PATH="\$ASDF_DATA_DIR/shims:\$PATH"
EOF
  log "asdf $(/usr/local/bin/asdf version) installed, data dir $ASDF_DATA_DIR"
}

# Azure CLI from Microsoft's apt repo (asdf azure-cli plugin calls 'asdf local', removed in asdf 0.16)
cmd_install_az() {
  install -d -m 755 /etc/apt/keyrings
  curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor --yes -o /etc/apt/keyrings/microsoft.gpg
  chmod go+r /etc/apt/keyrings/microsoft.gpg
  cat > /etc/apt/sources.list.d/azure-cli.sources <<EOF
Types: deb
URIs: https://packages.microsoft.com/repos/azure-cli/
Suites: $(lsb_release -cs)
Components: main
Architectures: $(dpkg --print-architecture)
Signed-by: /etc/apt/keyrings/microsoft.gpg
EOF
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q azure-cli
  log "azure-cli $(az version --query '"azure-cli"' -o tsv 2>/dev/null) installed (update: apt-get install --only-upgrade azure-cli)"
}

cmd_completions() {
  export PATH="$ASDF_DATA_DIR/shims:/usr/local/bin:$PATH"
  cd /deployments
  local t
  asdf completion bash > "$COMP_DIR/asdf" 2>/dev/null || log "WARNING: no completion for asdf"
  for t in kubectl helm clusterctl clusterawsadm; do
    if command -v "$t" >/dev/null && "$t" completion bash > "$COMP_DIR/$t.tmp" 2>/dev/null; then
      mv "$COMP_DIR/$t.tmp" "$COMP_DIR/$t"
    else rm -f "$COMP_DIR/$t.tmp"; fi
  done
  if command -v yq >/dev/null; then yq shell-completion bash > "$COMP_DIR/yq" 2>/dev/null || log "WARNING: no completion for yq"; fi
  # aws ships a completer binary instead of a script
  if command -v aws_completer >/dev/null; then echo 'complete -C aws_completer aws' > "$COMP_DIR/aws"; fi
  # azure-cli ships an eager /etc/bash_completion.d file; expose it to the lazy loader as well
  if [[ -f /etc/bash_completion.d/azure-cli ]]; then ln -sf /etc/bash_completion.d/azure-cli "$COMP_DIR/az"; fi
  # Alias k=kubectl for every login shell (ssh, sudo -i, tmux windows); its completion is lazy-loaded as "k"
  echo 'alias k=kubectl' > /etc/profile.d/bastion-aliases.sh
  if [[ -f "$COMP_DIR/kubectl" ]]; then
    printf '. %s/kubectl\ncomplete -o default -F __start_kubectl k\n' "$COMP_DIR" > "$COMP_DIR/k"
  fi
  log "completions in $COMP_DIR: $(cd "$COMP_DIR" && ls asdf kubectl k helm yq clusterctl clusterawsadm aws az docker git 2>/dev/null | tr '\n' ' ')"
}

cmd_sync() {
  [[ -x /usr/local/bin/asdf ]] || { echo "asdf not installed (bastion-tools install)" >&2; exit 1; }
  export PATH="$ASDF_DATA_DIR/shims:/usr/local/bin:$PATH"
  if [[ ! -f "$TOOLS_FILE" ]]; then
    [[ -f "$DEFAULTS" ]] || { echo "no $TOOLS_FILE and no $DEFAULTS" >&2; exit 1; }
    install -m 664 -g deployers "$DEFAULTS" "$TOOLS_FILE"
    log "seeded $TOOLS_FILE from $DEFAULTS"
  fi
  local dir=${SYNC_DIR:-/deployments} t rc=0
  cd "$dir"
  for t in $(awk '!/^#/ && NF {print $1}' "$TOOLS_FILE" .tool-versions 2>/dev/null | sort -u); do
    asdf plugin list 2>/dev/null | grep -qx "$t" || asdf plugin add "$t" || { log "WARNING: plugin add $t failed"; rc=1; }
  done
  # A line may list several versions (first = active default, rest preinstalled); one failure must not block the others
  local vs v
  while read -r t vs; do
    [[ -n "${t:-}" && "$t" != \#* ]] || continue
    for v in $vs; do
      asdf install "$t" "$v" || { log "WARNING: asdf install $t $v failed"; rc=1; }
    done
  done < <(cat "$TOOLS_FILE" .tool-versions 2>/dev/null | awk '!/^#/ && NF' | sort -u)
  asdf reshim || { log "WARNING: asdf reshim failed"; rc=1; }
  chgrp -R deployers "$ASDF_DATA_DIR" 2>/dev/null || true
  cmd_completions
  log "sync done in $dir (rc=$rc)"
  return $rc
}

cmd_status() {
  export PATH="$ASDF_DATA_DIR/shims:/usr/local/bin:$PATH"
  echo "asdf:        $(asdf version 2>/dev/null || echo not installed)"
  echo "data dir:    $ASDF_DATA_DIR"
  echo "tools file:  $TOOLS_FILE"
  (cd /deployments && asdf current 2>/dev/null) || true
}

cmd=${1:-}; shift || true
case "$cmd" in
  install) need_root; cmd_install ;;
  install-az) need_root; cmd_install_az ;;
  sync) need_root; cmd_sync ;;
  completions) need_root; cmd_completions ;;
  status) cmd_status ;;
  -h|--help|"") usage ;;
  *) echo "unknown command: $cmd" >&2; usage; exit 1 ;;
esac
