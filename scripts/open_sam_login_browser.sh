#!/usr/bin/env bash
# Launches a visible Playwright Chromium window in noVNC for SAM.gov login.
# Usage: bash scripts/open_sam_login_browser.sh [profile_dir]
# The script stays alive until the operator closes the browser window.

export DISPLAY="${DISPLAY:-:99}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PROFILE_DIR="${1:-${ROOT_DIR}/.browser/sam-profile}"
LOG_FILE="/tmp/sam_login_browser.log"

mkdir -p "$PROFILE_DIR"
printf '[%s] Opening SAM.gov login browser. DISPLAY=%s PROFILE=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$DISPLAY" "$PROFILE_DIR" >> "$LOG_FILE"

exec playwright open --user-data-dir "$PROFILE_DIR" https://sam.gov >> "$LOG_FILE" 2>&1
