#!/usr/bin/env bash
# Resolve a headless Chromium binary for Playwright on self-hosted Linux runners.
set -euo pipefail

GITHUB_ENV_FILE="${GITHUB_ENV:-/dev/null}"

find_system_chrome() {
  local candidate found
  for candidate in \
    /usr/lib/chromium/chromium \
    /usr/lib/chromium-browser/chromium-browser \
    /usr/bin/google-chrome-stable \
    /usr/bin/google-chrome \
    /opt/google/chrome/google-chrome; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  found="$(command -v google-chrome-stable || command -v google-chrome || true)"
  if [[ -n "$found" && "$found" != /snap/* && -x "$found" ]]; then
    echo "$found"
    return 0
  fi
  return 1
}

export_chrome() {
  echo "Using Chromium: $1"
  echo "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=$1" >> "${GITHUB_ENV_FILE}"
  echo "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1" >> "${GITHUB_ENV_FILE}"
}

export_playwright_cache() {
  echo "Using Playwright browser cache: $1"
  echo "PLAYWRIGHT_BROWSERS_PATH=$1" >> "${GITHUB_ENV_FILE}"
  echo "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1" >> "${GITHUB_ENV_FILE}"
}

install_chrome_via_apt() {
  # Fallback only — the provisioner installs google-chrome-stable, so find_system_chrome
  # normally short-circuits before here. 1 runner/VM means no apt lock is needed.
  sudo apt-get update
  sudo apt-get install -y chromium chromium-browser google-chrome-stable 2>/dev/null || true
  sudo apt-get install -y chromium 2>/dev/null || true
  sudo apt-get install -y google-chrome-stable 2>/dev/null || true
}

install_playwright_bundle() {
  echo "No system Chrome found — downloading Playwright Chromium bundle..."
  export PLAYWRIGHT_BROWSERS_PATH="${RUNNER_TEMP:-/tmp}/pw-browsers"
  export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1
  mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
  if command -v npx >/dev/null 2>&1 && [[ -f frontend/package-lock.json ]]; then
    (cd frontend && npx playwright install chromium)
  elif python -m playwright --version >/dev/null 2>&1; then
    python -m playwright install chromium
  else
    echo "ERROR: no Chromium binary and playwright CLI unavailable" >&2
    exit 1
  fi
  export_playwright_cache "$PLAYWRIGHT_BROWSERS_PATH"
}

# Prefer existing system Chrome (runner-setup installs google-chrome-stable).
if CHROME="$(find_system_chrome)"; then
  export_chrome "$CHROME"
  exit 0
fi

# Reuse a prior Playwright download on this runner before touching apt.
for cache_dir in \
  "${PLAYWRIGHT_BROWSERS_PATH:-}" \
  "${RUNNER_TEMP:-/tmp}/pw-browsers" \
  "${HOME:-/tmp}/.cache/ms-playwright"; do
  if [[ -n "$cache_dir" && -d "$cache_dir" ]] && compgen -G "${cache_dir}/chromium-*" >/dev/null; then
    export_playwright_cache "$cache_dir"
    exit 0
  fi
done

echo "System Chrome not found — trying apt..."
install_chrome_via_apt

if CHROME="$(find_system_chrome)"; then
  export_chrome "$CHROME"
  exit 0
fi

install_playwright_bundle
