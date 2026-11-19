# Blizzard CASC/TACT — study guide

Covers the `gst recon` engine key `blizzard-casc`: Diablo IV (Steam layout) first; Battle.net installs (WoW, Diablo II: Resurrected, Overwatch, StarCraft II) use the same storage family.

## Identify

- Steam Diablo IV: `Diablo IV.exe`, `Data/.build.config`, `Data/NNN/0xNNNN-meta.dat` + `-payload.dat` (+ `.md5s`),
  `BuildRevision.txt`, `FenrisDebug*.txt` (Fenris is the engine codename), `vivoxsdk.dll`, `BlizzardBrowser/`.
- Battle.net layout: `.build.info` at the root, `Data/data/data.NNN` + `*.idx`, `Data/config/`.
- `gst recon` matches `Data/.build.config`, `.build.info` or `Data/NNN/0x…-meta.dat` (gst rules `BlizzardCASC`); no
  engine details beyond the evidence.
- Version: `BuildRevision.txt`, `.build.config` / `.build.info` (text: read in place) and the Steam build id. Data
  changes every season: tag every fact with the build.

## Code

- Native C++ client, always online (`no-runtime`): nothing is decompiled, and the server-side rules aren't on disk.
- The rules the client knows ship as data: SNO records (one file per actor, power, item, affix, quest, string
  list...) plus game-balance tables. References between them: `DT_SNO` / `DT_SNONAME` point at another file,
  `DT_GBID` at a row of a game-balance file (d4data README).
- Read first: the community d4data repository (DiabloTools/d4data: `definitions/`, parsed `json/`, `parse.js`), the
  public documentation of the record layouts; then check it against your own files from the same build.
- Background on the storage: wowdev.wiki's CASC and TACT pages, zezula.net's CASC pages.

## Data and assets

- Not in the `gst tools` catalog yet: rustydemon-cli 0.4.4 (HoldMyBeer-gg/rustydemon; AGPL-3.0 + Commons Clause,
  personal use allowed) and the GUIs CascView 2.4 / CASCExplorer. Get them with the user's OK; log versions and
  commands in STUDY.md.
- rustydemon reads the Steam D4 layout and Battle.net CASC natively. `--archive` is the install root:

```text
rustydemon-cli --archive "<install>" --path "base/meta/<Group>" --output "<ws>\assets\d4" --dry-run > dumps\d4-<Group>.txt
rustydemon-cli --archive "<install>" --path "base/meta/<Group>" --output "<ws>\assets\d4" -j 4
```

- `--path` takes a folder, a file or a glob. Keep the tree (no `--flat`): paths are evidence.
- SNO files are binary: decode them with the d4data definitions, or write a reader (`gst fmt new-reader`, proved by
  `gst fmt roundtrip`); `gst fmt id` / `hex` / `strings` on groups nobody documented.
- Textures and audio use Blizzard formats: `gst fmt id` first; audio candidates go to
  `gst assets audio "<absolute file>" --list` (vgmstream names the codec when it knows it).

## Runtime

- `no-runtime`: always online with server authority and no offline mode, so no process work of any kind.
- Static route: local CASC data, community definitions, the text files in the install (`BuildRevision.txt`,
  `FenrisDebug*.txt`).
- Client data shows definitions, not server outcomes: drop rates and roll results stay open questions unless a
  definition states them.

## Graphics

- Direct3D 12 only on PC (no D3D11 fallback). No capture at this verdict.
- Static: the texture and material SNO groups through rustydemon and `gst fmt`.

## Gotchas

- `gst assets list` says it handles Unity, Godot and Unreal only → no gst backend for CASC → rustydemon + `gst fmt`.
- rustydemon opens no storage or matches nothing → `--archive` must be the install root (README), not `Data/` →
  fix the path, then `--dry-run` again.
- `file is encrypted: missing TACT key <id>` → that content is encrypted and the key isn't public → record it as
  encrypted and move on (Protections).
- Path-too-long errors → deep `base/...` trees → a short output root, or `install.ps1 -EnableLongPaths` (LongPathsEnabled,
  admin).
- A GUI offers "online storage" or CDN browsing → that reads Blizzard's servers → never; local install only.
- d4data JSON disagrees with your files → different patch → compare builds before comparing values.
- Thousands of dry-run lines in the conversation → redirect them to `dumps/` and search with `rg`.

## Protections

- Always online (gst title list) and Warden (AreWeAntiCheatYet) → `no-runtime`.
- TACT keys: only community-published ones with the source URL recorded in game.json (rustydemon `--tact-keys <file>`;
  the gst guard checks it); never a key from the exe or memory, never brute force.
- `recon/protections.md` records them. Limits and reasons: [`plugins/gst/guardrails.md`](../../guardrails.md).
