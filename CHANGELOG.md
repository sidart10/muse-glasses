# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [semantic versioning](https://semver.org/).

## [0.1.0] - 2026-10-19

First public release.

### Added
- macOS Muse gadget: a CoreBluetooth port of the Muse Gadget SDK's pairing server.
- Bridge: relays messages between a Ray-Ban Display web app and your Muse (main chat), with live progress, approvals and late replies.
- Glasses web app: one-pinch dictation, chat thread, Muse mascot states (optional, from your own Muse app), approvals screen.
- Local Kokoro voice, spoken sentence by sentence, with the glasses' built-in voice as fallback.
- Instant open from a copy stored on the glasses, with automatic update when the Mac has a newer version.
- A fresh chat on every open; a request still running is picked back up.
- Clear message when the Mac restarts mid-request; deploys wait until the bridge is idle.
- Installer (`scripts/install.sh`), install QR generator (`muse_bridge link --qr`), kill switch (`muse_bridge off`).

### Security
- Muse is offered no commands on the Mac.
- Bearer-token API with path-only access logs; the token lives in the Keychain.

[0.1.0]: https://github.com/sidart10/muse-glasses/releases/tag/v0.1.0
