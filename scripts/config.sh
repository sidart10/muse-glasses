# shellcheck shell=bash disable=SC2034  # sourced by install.sh and deploy.sh, which use these
# Shared settings for install.sh and deploy.sh. Per-machine values live in
# ~/Library/Application Support/muse-glasses/config.env (written by install.sh).
HOME_DIR="$HOME/Library/Application Support/muse-glasses"
RT="$HOME_DIR/runtime"
VOICE_DIR="$HOME_DIR/voice"
CONFIG="$HOME_DIR/config.env"
SDK_REPO="https://github.com/facebookincubator/muse-gadget-sdk.git"
SDK_COMMIT="f60347aeb851a1fe9438cfc388721776aa24f275"  # the SDK version this port is tested against
# shellcheck source=/dev/null  # per-machine file written by install.sh
[ -f "$CONFIG" ] && . "$CONFIG"
BRIDGE_LABEL="${BRIDGE_LABEL:-local.muse-glasses.bridge}"
VOICE_LABEL="${VOICE_LABEL:-local.muse-glasses.voice}"
