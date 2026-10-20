# Contributing

Thanks for your interest! This is a small personal project, so issues and focused pull requests are very welcome. Please open an issue first for anything large.

## Getting set up

You don't need glasses or a Muse account to work on most of the code:

```bash
git clone https://github.com/sidart10/muse-glasses.git && cd muse-glasses
. scripts/config.sh && git clone -q "$SDK_REPO" vendor/muse-gadget-sdk && git -C vendor/muse-gadget-sdk checkout -q "$SDK_COMMIT"
uv venv --python 3.12 .venv && uv pip install -e ".[test]" -e vendor/muse-gadget-sdk/linux
(cd webapp && npm ci)

.venv/bin/python -m pytest -q                                                   # bridge tests
MUSE_BRIDGE_API_TOKEN=practice .venv/bin/python -m muse_bridge serve --fake     # practice mode on :8790
cd webapp && npm run dev                                                        # web app; /api goes to :8790
```

Open the web app at `http://localhost:5173/beru/#k=practice`. Meta's [Ray-Ban Display Simulator](https://wearables.developer.meta.com/) Chrome extension shows it at the glasses' 600×600 size. The glasses are driven by the arrow keys and Enter, so test with the keyboard only.

## Ground rules

- **Never commit secrets or private data:** tokens, install links (`#k=…`), QR codes, logs with chat content.
- **Never commit Meta artwork:** mascot clips, fonts, icons extracted from the Muse app. They're gitignored; keep it that way.
- **Muse gets no commands.** `ALLOWED_COMMANDS` stays empty. A change that lets Muse run anything on the Mac won't be merged.
- Keep the [Muse Gadget SDK terms](https://gadgets.muse.ai/sdk-terms) in mind: nothing that implies Meta made or endorses this project.

## Pull requests

- Keep them focused, and add or update tests for behavior changes.
- CI must pass: bridge tests, web app build, shell script checks.
- Update the README when setup or behavior changes.
- By contributing, you agree your contribution is licensed under the [Apache License 2.0](LICENSE).
