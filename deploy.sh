#!/bin/bash
# Build the web app, run the tests, copy everything to a runtime dir outside
# ~/Desktop (launchd services can't read Desktop without a privacy prompt),
# refresh its venv, then restart the bridge and voice. Any failure stops the deploy.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
. "$SRC/scripts/config.sh"

(cd "$SRC/webapp" && npm run -s build)
(cd "$SRC" && .venv/bin/python -m pytest -q)

# Restarting the bridge drops the request Muse is working on (requests live in memory), so wait
# until it's idle: up to 10 minutes. DEPLOY_NOW=1 skips the wait.
TOKEN="$(security find-generic-password -s muse-bridge-api-token -w 2>/dev/null || true)"
if [ -n "$TOKEN" ] && [ "${DEPLOY_NOW:-0}" != 1 ]; then
  for i in $(seq 1 120); do
    printf 'Authorization: Bearer %s\n' "$TOKEN" \
      | curl -s -m 3 -H @- http://127.0.0.1:8790/api/health | grep -q '"busy": *true' || break
    [ "$i" = 1 ] && echo "Beru is working on a request; waiting for it to finish before restarting (DEPLOY_NOW=1 skips)"
    sleep 5
  done
fi
unset TOKEN

mkdir -p "$RT"
rsync -a --delete --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude webapp/node_modules --exclude webapp/src "$SRC/" "$RT/"
[ -x "$RT/.venv/bin/python" ] || uv venv -q --python 3.12 "$RT/.venv"
uv pip install -q --python "$RT/.venv/bin/python" "aiohttp==3.14.5" "bless==0.3.0" "bleak==3.0.2" \
  "cryptography>=3.3.2" "websockets>=13"

for label in "$VOICE_LABEL" "$BRIDGE_LABEL"; do
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$(id -u)/$label"
    echo "restarted $label"
  fi
done
echo "deployed to $RT"
