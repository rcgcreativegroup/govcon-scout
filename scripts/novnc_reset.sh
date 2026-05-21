#!/usr/bin/env bash

DISPLAY_VALUE="${DISPLAY:-:99}"
SCREEN_GEOMETRY="${NOVNC_SCREEN_GEOMETRY:-1280x900x24}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_WEB="${NOVNC_WEB:-/usr/share/novnc/}"
DISPLAY_NUMBER="${DISPLAY_VALUE#:}"
DISPLAY_NUMBER="${DISPLAY_NUMBER%%.*}"
XVFB_PID_FILE="${NOVNC_XVFB_PID_FILE:-/tmp/novnc_xvfb.pid}"
FLUXBOX_PID_FILE="${NOVNC_FLUXBOX_PID_FILE:-/tmp/novnc_fluxbox.pid}"
X11VNC_PID_FILE="${NOVNC_X11VNC_PID_FILE:-/tmp/novnc_x11vnc.pid}"
WEBSOCKIFY_PID_FILE="${NOVNC_WEBSOCKIFY_PID_FILE:-/tmp/novnc_websockify.pid}"

ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
CHECK_SCRIPT="${ROOT_DIR}/scripts/novnc_check.sh"

log() {
  printf '%s\n' "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "Missing required command: $1"
    return 1
  fi
}

stop_matching() {
  local pattern="$1"
  if process_running "$pattern"; then
    pkill -f "$pattern" || true
  fi
}

process_running() {
  local pattern="$1"
  ps -eo pid=,args= \
    | grep -E "$pattern" \
    | grep -Ev "grep -E|novnc_reset.sh|novnc_check.sh" \
    >/dev/null 2>&1
}

wait_for_match() {
  local pattern="$1"
  local label="$2"
  local attempts="${3:-20}"

  for _ in $(seq 1 "$attempts"); do
    if process_running "$pattern"; then
      return 0
    fi
    sleep 0.25
  done

  log "Timed out waiting for ${label}."
  return 1
}

wait_for_pid() {
  local pid="$1"
  local label="$2"
  local attempts="${3:-20}"

  for _ in $(seq 1 "$attempts"); do
    if kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done

  log "Timed out waiting for ${label}."
  return 1
}

remember_pid() {
  local pid="$1"
  local pid_file="$2"
  printf '%s\n' "$pid" >"$pid_file"
  disown "$pid" 2>/dev/null || true
}

main() {
  local missing=0
  require_command Xvfb || missing=1
  require_command x11vnc || missing=1
  require_command websockify || missing=1

  if [ "$missing" -ne 0 ]; then
    log ""
    log "Install the missing noVNC/X11 tools, then rerun this script."
    return 1
  fi

  log "Stopping old noVNC/X11 processes..."
  stop_matching "Xvfb ${DISPLAY_VALUE}"
  stop_matching "x11vnc .*${DISPLAY_VALUE}"
  stop_matching "websockify .*${NOVNC_PORT} .*${VNC_PORT}"
  stop_matching "fluxbox"
  rm -f "$XVFB_PID_FILE" "$FLUXBOX_PID_FILE" "$X11VNC_PID_FILE" "$WEBSOCKIFY_PID_FILE"
  sleep 1

  if ! process_running "Xvfb ${DISPLAY_VALUE}"; then
    rm -f "/tmp/.X${DISPLAY_NUMBER}-lock"
    rm -f "/tmp/.X11-unix/X${DISPLAY_NUMBER}"
  fi

  log "Starting Xvfb on ${DISPLAY_VALUE}..."
  nohup Xvfb "$DISPLAY_VALUE" -screen 0 "$SCREEN_GEOMETRY" >/tmp/xvfb.log 2>&1 &
  xvfb_pid=$!
  remember_pid "$xvfb_pid" "$XVFB_PID_FILE"
  wait_for_pid "$xvfb_pid" "Xvfb" || return 1

  export DISPLAY="$DISPLAY_VALUE"

  if command -v fluxbox >/dev/null 2>&1; then
    log "Starting fluxbox..."
    nohup fluxbox >/tmp/fluxbox.log 2>&1 &
    fluxbox_pid=$!
    remember_pid "$fluxbox_pid" "$FLUXBOX_PID_FILE"
    wait_for_pid "$fluxbox_pid" "fluxbox" 12 || true
  else
    log "fluxbox not found; continuing without a window manager."
  fi

  log "Starting x11vnc on localhost:${VNC_PORT}..."
  nohup x11vnc -display "$DISPLAY_VALUE" -nopw -listen localhost -rfbport "$VNC_PORT" -xkb -forever >/tmp/x11vnc.log 2>&1 &
  x11vnc_pid=$!
  remember_pid "$x11vnc_pid" "$X11VNC_PID_FILE"
  wait_for_pid "$x11vnc_pid" "x11vnc" || return 1

  log "Starting noVNC websockify on port ${NOVNC_PORT}..."
  nohup websockify --web="$NOVNC_WEB" "$NOVNC_PORT" "localhost:${VNC_PORT}" >/tmp/novnc.log 2>&1 &
  websockify_pid=$!
  remember_pid "$websockify_pid" "$WEBSOCKIFY_PID_FILE"
  wait_for_pid "$websockify_pid" "websockify" || return 1

  log ""
  log "DISPLAY=${DISPLAY}"
  log "If you ran this script normally, export DISPLAY before live processing:"
  log "  export DISPLAY=${DISPLAY}"
  log "noVNC URL: forwarded port ${NOVNC_PORT}, then open vnc.html and connect."
  log ""

  if [ -x "$CHECK_SCRIPT" ]; then
    "$CHECK_SCRIPT"
  else
    bash "$CHECK_SCRIPT"
  fi
}

main "$@"
