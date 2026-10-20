"""`python -m muse_bridge <command>`

serve [--fake] [--port N]   run the bridge (live Muse unless --fake)
say "text"                  send one message through a running bridge and print Muse's answer
off                         kill switch: refuse all API calls and rotate the glasses token
on                          accept API calls again (glasses need the new install link)
link [--host H] [--qr PNG]  print the glasses install URL (it carries the API token), or write it as a QR
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8790
CONFIG_ENV = Path.home() / "Library" / "Application Support" / "muse-glasses" / "config.env"


def _configured_host() -> str:
    """Your tunnel hostname: MUSE_GLASSES_HOST, else the value scripts/install.sh saved."""
    if os.environ.get("MUSE_GLASSES_HOST"):
        return os.environ["MUSE_GLASSES_HOST"]
    try:
        for line in CONFIG_ENV.read_text().splitlines():
            if line.startswith("MUSE_GLASSES_HOST="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


DEFAULT_HOST = _configured_host()
WEBAPP_DIST = Path(__file__).resolve().parent.parent / "webapp" / "dist"


def _serve(args) -> int:
    from aiohttp import web

    from muse_bridge import settings
    from muse_bridge.backends import FakeBackend
    from muse_bridge.server import Bridge, make_app

    if args.fake:
        backend = FakeBackend()
    else:
        import musegadget_mac  # noqa: F401  (macOS state dir for the gadget pairing)

        from muse_bridge.backends import LiveBackend
        backend = LiveBackend(settings.sdk_token())
    settings.api_token()  # create it now, not on the first request
    app = make_app(Bridge(backend), WEBAPP_DIST)
    logging.getLogger(__name__).info("serving on 127.0.0.1:%d (%s)", args.port, "fake" if args.fake else "live")
    from muse_bridge.server import PathOnlyAccessLogger
    web.run_app(app, host="127.0.0.1", port=args.port, print=None, access_log_class=PathOnlyAccessLogger)
    return 0


def _say(args) -> int:
    from muse_bridge import settings

    base = f"http://127.0.0.1:{args.port}"
    headers = {"Authorization": f"Bearer {settings.api_token()}", "Content-Type": "application/json"}

    def call(path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.load(resp)

    request_id = call("/api/say", {"text": args.text})["request_id"]
    seq, shown = 0, ""
    while True:
        for event in call(f"/api/feed?after={seq}")["events"]:
            seq = event["seq"]
            if event["request_id"] != request_id:
                continue
            if event["type"] == "status":
                print(f"… {event['text']}", file=sys.stderr)
            elif event["type"] == "reply" and event.get("done"):
                shown = event["text"]
                print(shown)
            elif event["type"] == "error":
                print(f"error: {event['text']}", file=sys.stderr)
                return 1
            elif event["type"] == "done":
                return 0


def _switch(off: bool) -> int:
    from muse_bridge import settings

    settings.set_disabled(off)
    if off:
        settings.rotate_api_token()
        print("Bridge is OFF. The old glasses link no longer works.")
    else:
        print("Bridge is ON. Make a new install link with: python -m muse_bridge link")
    return 0


def _link(args) -> int:
    import urllib.parse

    from muse_bridge import settings
    from muse_bridge.server import APP_PATH

    if not args.host:
        print("Set MUSE_GLASSES_HOST (your tunnel hostname) or pass --host.", file=sys.stderr)
        return 2
    url = f"https://{args.host}{APP_PATH}#k={settings.api_token()}"
    if not args.qr:
        print(url)
        return 0
    try:
        import qrcode
    except ImportError:
        print("Install the QR extra first: uv pip install 'qrcode[pil]'", file=sys.stderr)
        return 2
    # Scanning this with the phone opens the Meta AI app and installs the web app on the glasses.
    deep_link = ("fb-viewapp://web_app_deep_link?appName=" + urllib.parse.quote(args.name)
                 + "&appUrl=" + urllib.parse.quote(url, safe=""))
    out = Path(args.qr).expanduser()
    qrcode.make(deep_link, box_size=10, border=4).save(out)
    out.chmod(0o600)  # it carries the token
    print(f"wrote {out} (keep it private: it carries the glasses token)")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="muse_bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--fake", action="store_true")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    say = sub.add_parser("say")
    say.add_argument("text")
    say.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub.add_parser("off")
    sub.add_parser("on")
    link = sub.add_parser("link")
    link.add_argument("--host", default=DEFAULT_HOST)
    link.add_argument("--qr", help="write the install QR code to this PNG instead of printing the URL")
    link.add_argument("--name", default="Beru", help="app name shown on the glasses")
    args = parser.parse_args()
    if args.command == "serve":
        return _serve(args)
    if args.command == "say":
        return _say(args)
    if args.command in ("off", "on"):
        return _switch(args.command == "off")
    return _link(args)


if __name__ == "__main__":
    sys.exit(main())
