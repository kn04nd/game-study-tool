#!/usr/bin/env bash
# gst hook launcher, hook JSON on stdin. hooks.json sources it as
# `GS_HOOK=<name> . run.sh` (one shell process instead of two: ~50 ms less per tool call on
# Windows); `bash run.sh <name>` works too. POSIX sh, so it also runs where hooks use dash.
# Fast path: hook.cfg (written by `gst setup hooks|plugin` and by any `gst hook` run) holds the base
# Python and the folder containing the gst package; -S -E skips site-packages and PYTHON* variables, -P keeps
# the current folder (the open project) off sys.path so a json.py there is never imported,
# about half the start-up of the gst.exe trampoline. Fallback: gst.exe. Never blocks a session:
# exits 0 without output when gst can't be found.
# State lives in %LOCALAPPDATA%/game-study-tool (the legacy gs keeps %LOCALAPPDATA%/game-study).
gs_hook="${GS_HOOK:-${1:-}}"
[ -z "$gs_hook" ] && exit 0
if [ -n "${LOCALAPPDATA:-}" ]; then gs_cfg="$LOCALAPPDATA/game-study-tool/hook.cfg"; else gs_cfg="$HOME/.local/state/game-study-tool/hook.cfg"; fi
if [ -f "$gs_cfg" ]; then
  { IFS= read -r gs_py; IFS= read -r gs_src; } < "$gs_cfg"
  if [ -n "$gs_py" ] && [ -f "$gs_py" ] && [ -d "$gs_src/gst" ]; then
    exec "$gs_py" -S -E -P -X utf8 -c 'import sys; sys.path.insert(0, sys.argv[1]); from gst.hooks import main; sys.exit(main(sys.argv[2]))' "$gs_src" "$gs_hook"
  fi
fi
for gs_exe in "${GST_EXE:-}" "$HOME/.local/bin/gst.exe" "$HOME/.local/bin/gst"; do
  if [ -n "$gs_exe" ] && [ -f "$gs_exe" ]; then
    exec "$gs_exe" hook "$gs_hook"
  fi
done
if command -v gst >/dev/null 2>&1; then
  exec gst hook "$gs_hook"
fi
exit 0
