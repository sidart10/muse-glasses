# Security policy

This project connects smart glasses, through the internet, to a Mac that is paired with your Muse account. Please treat security problems seriously and report them privately.

## Reporting a vulnerability

Use GitHub's **[private vulnerability reporting](https://github.com/sidart10/muse-glasses/security/advisories/new)**. Please don't open a public issue.

Include what's affected, how to reproduce it, and the impact you think it has. You'll get a reply within a week. This is a personal project maintained in spare time, so there is no bounty.

## Scope

In scope:
- Authentication of the glasses API (the bearer token) and anything that bypasses it
- Ways for Muse, the glasses, or a web page to run commands or read files on the Mac
- Token or chat content leaking into logs, URLs, caches or other services
- The installer and LaunchAgents it writes

Out of scope:
- Meta's Muse Gadget SDK, Muse itself, the Meta AI app or the glasses' software. Report those to Meta.
- Problems that require someone who already has your Mac account, your Keychain, or your install QR code

## Design notes

- Muse is offered **no commands** on the Mac (`ALLOWED_COMMANDS = {}` in `muse_bridge/service.py`, enforced by a test).
- The glasses token is random, kept in the macOS Keychain, carried only in the install QR code's URL fragment, and compared in constant time. The access log records paths only.
- `python -m muse_bridge off` rotates the token and refuses every request.
