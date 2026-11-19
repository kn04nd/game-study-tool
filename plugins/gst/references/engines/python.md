# Python games — study guide

Covers the `gst recon` engine key `pyinstaller` and, by the same route, other packaged Python games (cx_Freeze,
py2exe, Nuitka, PyArmor-protected builds, Panda3D, pygame). Ren'Py has its own guide: `renpy.md`.

## Identify

- PyInstaller: a CArchive appended to the exe, ending with the cookie `MEI\x0c\x0b\x0a\x0b\x0e` (recon checks the
  last 4 KB); 6.x onedir builds add `_internal/` with `python3XY.dll` (recon evidence).
  `gst fmt hex "<Game>.exe" --offset -88` usually shows the whole cookie: magic, package size, TOC offset and size,
  Python version (e.g. 311) and the DLL name.
- `pyinstxtractor-ng --info "<install>\<Game>.exe"` prints the PyInstaller and Python versions, file counts and
  `Encrypted: True|False`, and writes nothing. Run it before any extraction.
- Other packagers: `library.zip` (`lib/library.zip` for cx_Freeze; py2exe) next to `python3XY.dll`; Nuitka: `.pyd`
  modules and no `.pyc` (native code); PyArmor: `pyarmor_runtime*` or `pytransform`; Panda3D: `libpanda.dll` and
  `*.mf` multifiles (magic `pmf\0\n\r`).
- Bytecode version: the `.pyc` magic (first 2 bytes) or the `python3XY.dll` name.

## Code

Run from `decomp/py/` as the working directory (pyinstxtractor-ng writes `<exe>_extracted/` into the cwd):

    pyinstxtractor-ng "<install>\<Game>.exe"     # only after --info said Encrypted: False
    pycdc <module>.pyc > <module>.py             # one file at a time; pycdas <module>.pyc disassembles

- The extraction names the entry script (`Possible entry point: <name>.pyc`); modules sit in
  `PYZ-00.pyz_extracted/`, next to the bundled DLLs and data.
- Decompilers by bytecode version: pycdc (all versions, partial from 3.9 on), PyLingual's local CLI (3.6–3.15; see
  its `--help`), decompyle3/uncompyle6 (≤ 3.8; they themselves run only on Python 3.7/3.8, so use a uv-managed 3.8
  env). When all fail, the pycdas disassembly is still readable context.
- Read first: the entry script (main loop, scene or state manager), then the game's own packages. The stdlib and
  third-party packages (pygame, numpy…) are public source: read them upstream.
- `gst index` has no Python parser: `gst q grep "^\s*(class|def) " --glob "*.py"`. After one `gst index` (it creates
  the index even with nothing to parse), `gst q show decomp/py/<path>.py:<line>`.
- cx_Freeze/py2exe: `library.zip` is a plain zip of `.pyc`: `gst tools install 7zip`, then
  `7z x "<install>\lib\library.zip" -odecomp/py/lib`. Nuitka: compiled C, so `gst decompile native <module>`
  within the verdict; module and function names survive as strings.

## Data and assets

- Bundled data comes out with the extraction (onedir builds keep it on disk): images, sounds, fonts, levels as
  JSON, CSV, pickle or custom binaries. Read loose files in place.
- Panda3D multifiles: `multify` (installed by `pip install panda3d`; see its `--help`), output into the workspace.
- Pickled data and saves: `pickletools` (stdlib) disassembles them without executing anything; `pickle.load` runs
  code.
- Unknown formats: `gst fmt id`, `gst fmt scan "<folder>"`, then the study-formats skill; the loader is usually one
  search away (`gst q grep "open\(|load\(" --glob "*.py"`).
- `gst assets list` and `gst assets extract` don't cover Python games. Audio: `gst assets audio --list "**/*.ogg"`.

## Runtime

Verdict `offline-ok` only.

- CPython objects are boxed and immutable: a changed int or float is a new object, so `gst trace scan`/`next` lose
  it, and `gst trace watch` names `python3XY.dll` (the interpreter), never game code.
- What works: `gst trace snapshot`/`gst trace diff` around an action (saves, configs, logs), then the source.
- Anything that injects into the interpreter (consoles, hooks) runs only in the `gst sandbox` copy, offline.

## Graphics

- It depends on the library. Not capturable: pygame/SDL software surfaces or SDL's default D3D9 renderer, Panda3D's
  OpenGL (compatibility) or D3D9 back ends. Capturable from the sandbox (`gst gfx capture`): OpenGL 3.3 core
  libraries such as moderngl or arcade, D3D11 back ends.
- Check which libraries the extraction contains; the draw code is in the decompiled source.

## Gotchas

- `gst decompile` says "no decompile backend for pyinstaller" → no gst backend yet → the route above, output in
  `decomp/py/`.
- `<Game>.exe_extracted/` lands in an unexpected folder → pyinstxtractor-ng writes into the current directory →
  run it from `decomp/py/`.
- "Missing cookie" or not a PyInstaller archive → another packager (cx_Freeze, Nuitka) or a modified bootloader →
  `gst fmt hex "<Game>.exe" --offset -88` and the files around the exe tell which.
- pycdc stops on unsupported opcodes → newer bytecode than it handles → PyLingual locally; keep the pycdas output.
- `pip install pylingual` gives an empty package → that PyPI name is an unrelated placeholder → clone
  syssec-utd/pylingual and `uv tool install` it. Never the pylingual.io website: it uploads the code.
- Large `.pyd` files → C extensions or Cython → native code: `gst decompile native <name>.pyd`; Cython keeps the
  Python names in strings.
- `gst map` finds no systems → Python isn't indexed → map the systems by hand from the entry script and grep.

## Protections

- PyInstaller `--key` (encrypted PYZ; PyInstaller ≤ 5.x, removed in 6.0): `--info` says `Encrypted: True`. That key
  sits inside the executable, so it does not count as a published key: the toolkit diagnoses it and stops.
  pyinstxtractor-ng would decrypt silently with that key, so `gst recon` flags the build (`pyimod00_crypto_key` in the
  exe) and the guard then refuses every pyinstxtractor run on it except `--info`. Record it in STUDY.md.
- Any other key: the toolkit accepts only keys already published by the community, with their source recorded. It
  never extracts a key from the executable or from memory, and never brute-forces.
- PyArmor (obfuscated bytecode + native runtime) is a protector: diagnose only. Nuitka is a compiler, not a
  protection: the native route within the verdict.
- Alternatives: files on disk (onedir data, configs, saves, logs), the public source of the libraries, official docs.
- Full rules: `plugins/gst/guardrails.md`.
