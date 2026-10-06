#!/usr/bin/env bash
# vm-autostop - power off a bastion VM after a configured local time unless a cloud-provisioner bootstrap is running
set -euo pipefail

BIN=/usr/local/sbin/vm-autostop
CONF=/etc/vm-autostop.conf
STATE=/var/lib/vm-autostop
UNIT=vm-autostop
GRACE_MIN=5

usage() {
  cat <<EOF
Usage: vm-autostop <command>   (run as root)

  install [HH:MM] [M] Install binary + systemd timer, enabled at HH:MM (default 18:00, Europe/Madrid);
                      M = poweroff (AWS/GCP, default) | azure-deallocate (needs VM managed identity with deallocate rights)
  uninstall           Remove timer, service, config and binary
  enable [HH:MM]      Turn auto-stop on (optionally change the time)
  disable             Turn auto-stop off until enabled again
  skip-today          Do not stop tonight; back to normal tomorrow
  cancel              Cancel an already announced stop
  status              Config, next timer run, skip flag, pending shutdown, activity
  stop-if-idle [MIN]  Stop now (after MIN minutes, default 1) only if no bootstrap is running
  check               Called by the timer every 15 min: stop if after HH:MM, before LAST_TRY and idle
EOF
}

log() { logger -t vm-autostop -- "$*"; echo "$*"; }
need_root() { [[ $EUID -eq 0 ]] || { echo "run as root (sudo vm-autostop $*)" >&2; exit 1; }; }
valid_time() { [[ "$1" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "invalid time '$1', expected HH:MM" >&2; exit 1; }; }

load_conf() {
  ENABLED=1 TIME=18:00 TZONE=Europe/Madrid LAST_TRY=23:30 STOP_METHOD=poweroff
  # shellcheck disable=SC1090
  if [[ -f "$CONF" ]]; then . "$CONF"; fi
}
save_conf() {
  printf 'ENABLED=%s\nTIME=%s\nTZONE=%s\nLAST_TRY=%s\nSTOP_METHOD=%s\n' "$ENABLED" "$TIME" "$TZONE" "$LAST_TRY" "$STOP_METHOD" > "$CONF"
}
hhmm() { echo $((10#${1/:/})); }
today() { TZ="$TZONE" date +%F; }
shutdown_pending() { systemctl is-active --quiet $UNIT-stop.timer; }

# Prints the reasons the VM is busy; empty output means idle
# Only the cloud-provisioner binary itself (first cmdline token), never a tail/tee/ssh that merely mentions it
activity() {
  local ct procs
  ct=$(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | grep -E -- '-control-plane |cloud-provisioner-upgrade' | cut -d' ' -f1 || true)
  procs=$(pgrep -af '^[^ ]*cloud-provisioner[^ /]*( |$)' || true)
  [[ -z "$ct" ]] || echo "bootstrap/upgrade container(s): $(echo $ct)"
  [[ -z "$procs" ]] || echo "process(es): $(echo "$procs" | cut -c1-120 | tr '\n' ';')"
}

# Schedules do-stop in MIN minutes as a transient timer; do-stop re-checks activity before stopping
poweroff_in() {
  local min=$1 reason=$2
  if shutdown_pending; then log "stop already scheduled, nothing to do"; return; fi
  log "stopping in ${min} min ($reason) - cancel with: sudo vm-autostop cancel | skip tonight: sudo vm-autostop skip-today"
  wall "vm-autostop: this VM stops in ${min} min ($reason). Cancel: sudo vm-autostop cancel | skip tonight: sudo vm-autostop skip-today" 2>/dev/null || true
  systemd-run --quiet --collect --unit=$UNIT-stop --on-active="${min}min" --timer-property=AccuracySec=1s "$BIN" do-stop
}

# Azure: a guest poweroff leaves the VM "Stopped (allocated)" and still billed, so deallocate through ARM with the VM managed identity
azure_deallocate() {
  local imds=http://169.254.169.254/metadata token rid code
  token=$(curl -fsS -H Metadata:true "$imds/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F" | jq -r .access_token) || return 1
  rid=$(curl -fsS -H Metadata:true "$imds/instance/compute/resourceId?api-version=2021-02-01&format=text") || return 1
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $token" -H 'Content-Length: 0' \
    "https://management.azure.com${rid}/deallocate?api-version=2023-09-01") || return 1
  [[ "$code" == 202 || "$code" == 200 ]] || { log "deallocate HTTP $code for $rid"; return 1; }
}

cmd_install() {
  local t=${1:-18:00} m=${2:-poweroff}; valid_time "$t"
  [[ "$m" == poweroff || "$m" == azure-deallocate ]] || { echo "invalid stop method '$m' (poweroff|azure-deallocate)" >&2; exit 1; }
  # Refuse stdin runs (bash -s): $0 would be the shell itself, not this script
  [[ -f "$0" ]] && grep -q '^# vm-autostop' "$0" || { echo "run install from the script file, not via stdin" >&2; exit 1; }
  [[ "$(readlink -f "$0")" == "$BIN" ]] || install -m 755 "$0" "$BIN"
  mkdir -p "$STATE"
  cat > /etc/systemd/system/$UNIT.service <<EOF
[Unit]
Description=Stop this bastion VM after hours when idle (vm-autostop)

[Service]
Type=oneshot
ExecStart=$BIN check
EOF
  cat > /etc/systemd/system/$UNIT.timer <<EOF
[Unit]
Description=Run vm-autostop check every 15 minutes

[Timer]
OnCalendar=*-*-* *:00/15:00
Persistent=false

[Install]
WantedBy=timers.target
EOF
  load_conf; TIME=$t; ENABLED=1; STOP_METHOD=$m; save_conf
  systemctl daemon-reload
  systemctl enable --now $UNIT.timer >/dev/null 2>&1
  log "installed: auto-stop at $TIME $TZONE ($STOP_METHOD)"
}

cmd_uninstall() {
  systemctl disable --now $UNIT.timer >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/$UNIT.service /etc/systemd/system/$UNIT.timer "$CONF"
  rm -rf "$STATE"
  systemctl daemon-reload
  log "uninstalled"
  [[ "$(readlink -f "$0")" == "$BIN" ]] && rm -f "$BIN" || true
}

cmd_enable() {
  load_conf
  if [[ -n "${1:-}" ]]; then valid_time "$1"; TIME=$1; fi
  ENABLED=1; save_conf
  systemctl enable --now $UNIT.timer >/dev/null 2>&1
  log "enabled: auto-stop at $TIME $TZONE"
}

cmd_disable() {
  load_conf; ENABLED=0; save_conf
  systemctl disable --now $UNIT.timer >/dev/null 2>&1
  log "disabled (re-enable with: sudo vm-autostop enable [HH:MM])"
}

cmd_skip_today() {
  load_conf; mkdir -p "$STATE"
  rm -f "$STATE"/skip-*; touch "$STATE/skip-$(today)"
  if shutdown_pending; then systemctl stop $UNIT-stop.timer; fi
  log "skip-today: no auto-stop on $(today)"
}

cmd_cancel() {
  if shutdown_pending; then systemctl stop $UNIT-stop.timer; log "pending stop cancelled (the timer will retry in 15 min unless skip-today/disable)"
  else echo "no stop pending"; fi
}

cmd_do_stop() {
  load_conf
  local a; a=$(activity)
  if [[ -n "$a" ]]; then log "do-stop aborted at the last check - busy: $a"; exit 0; fi
  if [[ "$STOP_METHOD" == azure-deallocate ]]; then
    log "deallocating (azure)"; azure_deallocate || { log "deallocate failed, VM left running (next check in 15 min)"; exit 1; }
  else
    log "powering off"; systemctl poweroff
  fi
}

cmd_status() {
  load_conf
  echo "enabled:     $ENABLED"
  echo "stop after:  $TIME $TZONE (last try $LAST_TRY), method $STOP_METHOD"
  echo "now:         $(TZ="$TZONE" date '+%F %H:%M %Z')"
  echo "skip today:  $([[ -f "$STATE/skip-$(today)" ]] && echo yes || echo no)"
  echo "timer:       $(systemctl is-active $UNIT.timer 2>/dev/null || true), next $(systemctl show $UNIT.timer -p NextElapseUSecRealtime --value 2>/dev/null)"
  echo "shutdown:    $(shutdown_pending && echo pending || echo none)"
  local a; a=$(activity)
  echo "activity:    ${a:-idle}"
}

cmd_stop_if_idle() {
  local min=${1:-1} a
  a=$(activity)
  if [[ -n "$a" ]]; then log "stop-if-idle refused - busy: $a"; exit 2; fi
  poweroff_in "$min" "stop-if-idle requested"
}

cmd_check() {
  load_conf
  [[ "$ENABLED" == 1 ]] || exit 0
  [[ ! -f "$STATE/skip-$(today)" ]] || exit 0
  local now; now=$(hhmm "$(TZ="$TZONE" date +%H:%M)")
  (( now >= $(hhmm "$TIME") )) || exit 0
  if (( now > $(hhmm "$LAST_TRY") )); then exit 0; fi
  local a; a=$(activity)
  if [[ -n "$a" ]]; then log "after $TIME but busy, retry in 15 min - $a"; exit 0; fi
  poweroff_in "$GRACE_MIN" "after-hours auto-stop ($TIME $TZONE)"
}

cmd=${1:-}; shift || true
case "$cmd" in
  install) need_root "$cmd"; cmd_install "$@" ;;
  uninstall) need_root "$cmd"; cmd_uninstall ;;
  enable) need_root "$cmd"; cmd_enable "$@" ;;
  disable) need_root "$cmd"; cmd_disable ;;
  skip-today) need_root "$cmd"; cmd_skip_today ;;
  cancel) need_root "$cmd"; cmd_cancel ;;
  status) cmd_status ;;
  stop-if-idle) need_root "$cmd"; cmd_stop_if_idle "$@" ;;
  check) need_root "$cmd"; cmd_check ;;
  do-stop) need_root "$cmd"; cmd_do_stop ;;
  -h|--help|"") usage ;;
  *) echo "unknown command: $cmd" >&2; usage; exit 1 ;;
esac
