# Migrating from `gs` to `gst`

`gst` reads `gs` workspaces as they are. The migration only adds things: nothing is moved, renamed or deleted, and
the old `gs` still opens the workspaces afterwards.

## 1. Migrate the workspaces

First the rehearsal. It only reads and writes nothing:

```powershell
gst migrate --all --dry-run --report D:\gst-migrate-rehearsal.md
```

For each workspace in `D:\game-study`, the rehearsal shows:

- what would be imported;
- the problems found (install not found, slug different from the folder, long paths);
- the status: `would-migrate`, `up-to-date`, `stray` or `error`.

`.zip` files and folders without `game.json` show as ignored.

Then the real migration:

```powershell
gst migrate --all --report D:\gst-migrate.md
gst workspace            # v2m = migrated
gst status <slug>
gst coverage <slug>
```

What the migration writes in each workspace:

- the `jobs/` and `analysis/` folders;
- `jobs/jobs.db`, with one `done` job per old output (`decomp/cs`, `assets/audio`, `recon`...), with extractor
  `legacy.<folder>` and tool `legacy`;
- `jobs/COVERAGE.md`;
- in `game.json`: `"gst_workspace": 2` and `"gst_migrated"` (the `gs` keys stay as they are);
- one line in `STUDY.md`.

Running it again writes nothing, unless `gs` produced new outputs. In that case only those go in.

`registry.json` is not touched.

An empty sibling `<slug>@<buildid>` shows as `stray` and is left alone (old versions of `gst study --dry-run` created
such siblings). If you do not need it, delete the folder and its entry in `registry.json` by hand.

## 2. Switch the Claude Code plugin

Real names, checked with `claude plugin list`:

| | plugin | marketplace |
| --- | --- | --- |
| old (`gs`) | `game-study@game-study-local` | `game-study-local` (the legacy gs checkout) |
| new (`gst`) | `gst@gst-local` | `gst-local` (this repository) |

First register the new one:

```powershell
.\install.ps1 -Plugin
```

`-Plugin` does the same as these commands:

```powershell
claude plugin marketplace add <path of your game-study-tool clone>
claude plugin install gst@gst-local --scope user
```

Then disable the old one and enable the new one:

```powershell
claude plugin disable game-study@game-study-local --scope user
claude plugin enable gst@gst-local --scope user
```

`install.ps1` only prints these two commands and never disables the old plugin by itself. After the switch, restart
VS Code or the `claude` session.

## 3. Living side by side

While both plugins are enabled:

- the `game-study:*` skills (from `gs`) and the `gst:*` skills show side by side, and the hooks of both run;
- `gst doctor` warns about it;
- each CLI has its own configuration: `%LOCALAPPDATA%\game-study` for `gs` and `%LOCALAPPDATA%\game-study-tool` for
  `gst`. While `gst` has no config of its own, it inherits the `gs_home` of `gs`;
- the tools `gs` installed (Ghidra, ilspycmd...) are reused.

To avoid mixing them, ask for new studies through `gst` (`/gst:game-study`, or "analyze this game" with only `gst`
enabled).

## 4. Rolling back

To go back to the old plugin:

```powershell
claude plugin disable gst@gst-local --scope user
claude plugin enable game-study@game-study-local --scope user
```

Migrated workspaces need nothing: `gs` ignores `jobs/`, `analysis/` and the new `game.json` keys.

To undo the migration of a workspace, delete:

- the `jobs/` and `analysis/` folders, if `gst` wrote nothing in them besides the migration;
- the `gst_workspace` and `gst_migrated` keys of `game.json`.

Before deleting, check that `gst jobs list` shows only `legacy.*` jobs.

Removing the old plugin for good (optional, after a while):

```powershell
claude plugin uninstall game-study@game-study-local --scope user
claude plugin marketplace remove game-study-local
```
