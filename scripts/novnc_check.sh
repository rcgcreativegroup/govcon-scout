#!/usr/bin/env bash
set -u

DISPLAY_VALUE="${DISPLAY:-}"
NOVNC_HOST="${NOVNC_HOST:-127.0.0.1}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
VNC_PORT="${VNC_PORT:-5900}"
XVFB_PID_FILE="${NOVNC_XVFB_PID_FILE:-/tmp/novnc_xvfb.pid}"
FLUXBOX_PID_FILE="${NOVNC_FLUXBOX_PID_FILE:-/tmp/novnc_fluxbox.pid}"
X11VNC_PID_FILE="${NOVNC_X11VNC_PID_FILE:-/tmp/novnc_x11vnc.pid}"
WEBSOCKIFY_PID_FILE="${NOVNC_WEBSOCKIFY_PID_FILE:-/tmp/novnc_websockify.pid}"

fail=0

ok() {
  printf 'OK: %s\n' "$*"
}

bad() {
  printf 'FAIL: %s\n' "$*"
  fail=1
}

warn() {
  printf 'WARN: %s\n' "$*"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

port_open() {
  local host="$1"
  local port="$2"

  if command_exists nc; then
    nc -z "$host" "$port" >/dev/null 2>&1
    return $?
  fi

  timeout 2 bash -c "</dev/tcp/${host}/${port}" >/dev/null 2>&1
}

http_ok() {
  local url="$1"

  if command_exists curl; then
    curl -fsS --max-time 3 "$url" >/dev/null 2>&1
    return $?
  fi

  if command_exists wget; then
    wget -q --timeout=3 --spider "$url" >/dev/null 2>&1
    return $?
  fi

  return 2
}

check_process() {
  local pattern="$1"
  local label="$2"
  local pid_file="${3:-}"

  if [ -n "$pid_file" ] && [ -s "$pid_file" ]; then
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" >/dev/null 2>&1; then
      ok "${label} process is running"
      return
    fi
  fi

  if ps -eo pid=,args= \
    | grep -E "$pattern" \
    | grep -Ev "grep -E|novnc_reset.sh|novnc_check.sh" \
    >/dev/null 2>&1; then
    ok "${label} process is running"
  else
    bad "${label} process is not running"
  fi
}

if [ -z "$DISPLAY_VALUE" ]; then
  bad "DISPLAY is not set"
else
  ok "DISPLAY=${DISPLAY_VALUE}"

  display_number="${DISPLAY_VALUE#:}"
  display_number="${display_number%%.*}"
  if [ -S "/tmp/.X11-unix/X${display_number}" ]; then
    ok "X socket exists for ${DISPLAY_VALUE}"
  else
    bad "X socket /tmp/.X11-unix/X${display_number} does not exist"
  fi
fi

check_process "Xvfb ${DISPLAY_VALUE:-:99}" "Xvfb" "$XVFB_PID_FILE"
check_process "x11vnc .*${DISPLAY_VALUE:-:99}" "x11vnc" "$X11VNC_PID_FILE"
check_process "websockify .*${NOVNC_PORT}" "websockify/noVNC" "$WEBSOCKIFY_PID_FILE"

if port_open "$NOVNC_HOST" "$VNC_PORT"; then
  ok "VNC port ${VNC_PORT} is reachable on ${NOVNC_HOST}"
else
  bad "VNC port ${VNC_PORT} is not reachable on ${NOVNC_HOST}"
fi

if port_open "$NOVNC_HOST" "$NOVNC_PORT"; then
  ok "noVNC port ${NOVNC_PORT} is reachable on ${NOVNC_HOST}"
else
  bad "noVNC port ${NOVNC_PORT} is not reachable on ${NOVNC_HOST}"
fi

if http_ok "http://${NOVNC_HOST}:${NOVNC_PORT}/vnc.html"; then
  ok "noVNC web client responds at /vnc.html"
else
  status=$?
  if [ "$status" -eq 2 ]; then
    warn "curl/wget unavailable; skipped HTTP check for noVNC web client"
  else
    bad "noVNC web client did not respond at /vnc.html"
  fi
fi

if [ "$fail" -ne 0 ]; then
  printf '\nRun scripts/novnc_reset.sh, then open forwarded port %s and choose vnc.html.\n' "$NOVNC_PORT"
  exit 1
fi

printf '\nnoVNC live-mode prerequisites look ready.\n'
