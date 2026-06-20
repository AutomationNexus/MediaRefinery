#!/usr/bin/env bash
# Fail CI when operator config env reads appear outside allowlists (Task 008).
set -euo pipefail

fail=0

check() {
  local label="$1"
  local pattern="$2"
  local path="$3"
  if rg -n "$pattern" "$path" 2>/dev/null; then
    echo "FAIL: $label"
    fail=1
  else
    echo "OK: $label"
  fi
}

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

check "MR operator MR_ runtime" \
  'os\.environ\.get\("MR_(IMMICH|DATA|BASE|TRUSTED|SESSION|AUTO|DEMO|WEB)' \
  "$ROOT/src/mediarefinery/"
check "MR MEDIAREFINERY_CONFIG in service" \
  'MEDIAREFINERY_CONFIG' \
  "$ROOT/src/mediarefinery/service/"

check "MR_MASTER_KEY in service runtime" \
  'MR_MASTER_KEY' \
  "$ROOT/src/mediarefinery/service/"

exit "$fail"
