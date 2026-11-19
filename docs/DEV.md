# Developing gst

## Environment

A local venv in the repo, Python 3.13 through uv (do not use the system/pyenv Python):

```bash
uv venv .venv --python 3.13
uv pip install --python .venv/Scripts/python.exe -e ".[full]" pytest
```

The package is installed in editable mode: changes in `src/gst/` apply immediately.
Sanity checks:

```bash
.venv/Scripts/gst --help
.venv/Scripts/gst config show      # gs_home inherited from %LOCALAPPDATA%/game-study/config.toml
.venv/Scripts/gst status peak      # reads D:\game-study\peak (a gs v1 workspace), read-only
.venv/Scripts/gst tools list       # gst's tools, then the ones the gs already installed
```

## Tests

```bash
.venv/Scripts/python -m pytest              # unit tests (default: -m 'not tools and not games')
.venv/Scripts/python -m pytest -m tools     # real tools on the sample game (samples/)
.venv/Scripts/python -m pytest -m games     # installed games; outputs only in GS_HOME
.venv/Scripts/python scripts/check_no_derived.py   # git tracks no derived material
```

Pre-commit hook (optional, once per clone): `git config core.hooksPath .githooks`.

## Living next to the installed gs

The legacy `gs` (an editable uv tool pointing at the legacy `game-study-toolkit/cli` checkout) keeps working in
parallel. gst has its own binary (`gst`), package (`gst`), plugin (`gst@gst-local`, skills `gst:*`) and state folder
(`%LOCALAPPDATA%/game-study-tool`):

- config: `%LOCALAPPDATA%/game-study-tool/config.toml`; while it does not exist, gst reads the gs one
  (`%LOCALAPPDATA%/game-study/config.toml`, `gs_home = D:/game-study`). `gst config set` creates its own.
- variables: `GST_HOME`, `GST_TOOLS`, `GST_ONLINE`, `GST_GAME`, `GST_EXE`; the `GS_*` names work as a fallback
  (except `GS_EXE`, which points at the old gs.exe).
- tools: `tools.bin_path` looks in the gst root and then in `%LOCALAPPDATA%/game-study/tools` (with its
  `tools.lock.json`). New installs go to the gst root.
- v1 workspaces (`"gs_workspace": 1`) are read by `src/gst/compat/legacy.py`; `gst migrate` imports them without
  moving anything (`--dry-run` only reads). Step by step: [`MIGRATION.md`](MIGRATION.md).
- the `gst@gst-local` plugin is registered by `install.ps1 -Plugin` (or `gst setup plugin`) and lives next to the gs
  `game-study@game-study-local` until the user switches (commands in `MIGRATION.md`). To try it without registering:
  `claude --plugin-dir plugins/gst`.
- installer tests: `install.ps1 -DryRun`, or a throwaway venv
  (`-Venv D:/gst-e2e/<folder>/venv -BinDir D:/gst-e2e/<folder>/bin`); never `-AddToPath`/`-EnableLongPaths`/`-Plugin`
  in a test.

## Where to work

Module contracts: [`INTERFACES.md`](INTERFACES.md). Code layout and rules: [`../CLAUDE.md`](../CLAUDE.md). Open items:
[`KNOWN-LIMITATIONS.md`](KNOWN-LIMITATIONS.md).
