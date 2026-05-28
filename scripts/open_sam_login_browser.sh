#!/usr/bin/env bash
# Launches a visible Chromium window for SAM.gov login via a Python helper.
# Usage: bash scripts/open_sam_login_browser.sh [profile_dir]
# The window is sized/positioned to fit inside the noVNC viewport (1280x900).

export DISPLAY="${DISPLAY:-:99}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PROFILE_DIR="${1:-${ROOT_DIR}/.browser/sam-profile}"
LOG_FILE="/tmp/sam_login_browser.log"

printf '[%s] Opening SAM.gov login browser. DISPLAY=%s PROFILE=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$DISPLAY" "$PROFILE_DIR" >> "$LOG_FILE"

exec python "$SCRIPT_DIR/open_sam_login_browser.py" "$PROFILE_DIR" >> "$LOG_FILE" 2>&1
