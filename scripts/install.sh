#!/bin/bash
# One-time setup on a Mac. Safe to re-run.
#   scripts/install.sh --host glasses.example.com [--voice]
# Needs: macOS, uv, Node 20+, git. --voice also needs ffmpeg (brew install ffmpeg).
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
. "$SRC/scripts/config.sh"

HOST="${MUSE_GLASSES_HOST:-}"; WITH_VOICE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --voice) WITH_VOICE=1; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ -n "$HOST" ] || { echo "Pass --host <the hostname your tunnel serves, e.g. glasses.example.com>" >&2; exit 2; }
for tool in uv npm git; do command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }; done

echo "1/5 Meta's Muse Gadget SDK at $SDK_COMMIT"
if [ ! -d "$SRC/vendor/muse-gadget-sdk/.git" ]; then
  git clone -q "$SDK_REPO" "$SRC/vendor/muse-gadget-sdk"
fi
git -C "$SRC/vendor/muse-gadget-sdk" fetch -q origin "$SDK_COMMIT" 2>/dev/null || true
git -C "$SRC/vendor/muse-gadget-sdk" checkout -q "$SDK_COMMIT"

echo "2/5 Python environment and web app dependencies"
[ -x "$SRC/.venv/bin/python" ] || uv venv -q --python 3.12 "$SRC/.venv"
uv pip install -q --python "$SRC/.venv/bin/python" -e "$SRC[test,qr]"
(cd "$SRC/webapp" && npm ci --silent)

echo "3/5 Settings in $CONFIG"
mkdir -p "$HOME_DIR"; chmod 700 "$HOME_DIR"
{
  echo "MUSE_GLASSES_HOST=$HOST"
  echo "BRIDGE_LABEL=$BRIDGE_LABEL"
  echo "VOICE_LABEL=$VOICE_LABEL"
} > "$CONFIG"

echo "4/5 Build, test, copy to $RT"
"$SRC/deploy.sh" >/dev/null

agent() {  # label, log name, python, env PYTHONPATH, args...
  local label="$1" log="$2" py="$3" pypath="$4"; shift 4
  local plist="$HOME/Library/LaunchAgents/$label.plist" args=""
  for a in "$@"; do args="$args<string>$a</string>"; done
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$py</string>$args</array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>PYTHONPATH</key><string>$pypath</string>
  </dict>
  <key>WorkingDirectory</key><string>$RT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/$log.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/$log.log</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
}

echo "5/5 Background services"
agent "$BRIDGE_LABEL" muse-bridge "$RT/.venv/bin/python" "$RT:$RT/vendor/muse-gadget-sdk/linux/src" -m muse_bridge serve

if [ "$WITH_VOICE" = 1 ]; then
  command -v ffmpeg >/dev/null || { echo "missing: ffmpeg (brew install ffmpeg)" >&2; exit 1; }
  echo "Voice: Kokoro (local, free; ~350 MB of model files)"
  mkdir -p "$VOICE_DIR"
  [ -x "$VOICE_DIR/.venv/bin/python" ] || uv venv -q --python 3.12 "$VOICE_DIR/.venv"
  uv pip install -q --python "$VOICE_DIR/.venv/bin/python" "kokoro-onnx==0.6.1" "soundfile" "aiohttp==3.14.5"
  base="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
  for f in kokoro-v1.0.onnx voices-v1.0.bin; do
    [ -f "$VOICE_DIR/$f" ] || curl -fL --progress-bar -o "$VOICE_DIR/$f" "$base/$f"
  done
  agent "$VOICE_LABEL" muse-glasses-voice "$VOICE_DIR/.venv/bin/python" "$RT" -m muse_bridge.voice
fi

cat <<NEXT

Installed. Next:
  1. Pair this Mac with your Muse (see README, "Pair"):  .venv/bin/python -m musegadget_mac pair
  2. Point a tunnel at http://127.0.0.1:8790 for https://$HOST (README, "Tunnel")
  3. Make the glasses install code:  MUSE_GLASSES_HOST=$HOST .venv/bin/python -m muse_bridge link --qr ~/Desktop/glasses-install.png
NEXT
