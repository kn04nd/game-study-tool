"""Decompilation backends. Each writes under ``<workspace>/decomp/<backend dir>/`` with a MANIFEST.json
(source file, sha256, tool version, exact command) and never touches the game install."""

BACKENDS = ("dotnet",)  # every backend the CLI offers: gst.cmd_decompile.BACKENDS
