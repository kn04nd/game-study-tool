---
name: study-graphics
description: Study how a game draws a frame - capture one frame with RenderDoc from the sandbox copy (offline) and summarize it - render passes, render targets with formats and depth, the shaders of each pass with their constant buffers, textures and disassembly (DXBC, DXIL, SPIR-V), the final image. D3D11/12, Vulkan, OpenGL. Only for games whose verdict is offline-ok; RenderDoc injects, so it runs only from the sandbox.
when_to_use: The user wants to understand the rendering. Examples - "how does the game render", "what passes does a frame have", "show me the shaders", "capture a frame", "how is the lighting done", "como o jogo renderiza", "quais passes o frame tem", "mostra os shaders", "captura um frame", "como é feita a iluminação".
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Study a frame

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal: the frame's structure - passes, formats and the key shaders - explained with a capture as evidence.
Reply in pt-BR; notes in pt-BR.

## 0. Before the capture

- `gst status`: the verdict must be `offline-ok` (protected and online games are refused).
- RenderDoc: `gst tools install renderdoc`. It injects into the game, so it runs only from the copy:
  `gst sandbox create` (and again after a game update: `gst sandbox remove`, then create).
- Vulkan games also need RenderDoc's Vulkan layer registered, a change in the Windows registry: ask the
  user before running `renderdoccmd vulkanlayer --register --user` (`gst tools path renderdoc`).

## 1. Capture

```
gst gfx capture                        # game from sandbox/ under RenderDoc; one frame 5 s after the device starts
gst gfx capture --after 40             # long loading screens: capture later
gst gfx capture --manual               # the user presses F12 or PrtScr at the right moment; ends when the game exits
gst gfx capture --args "-windowed"     # arguments for the game
gst gfx list                           # captures in traces/gfx
gst gfx export <file.rdc>              # summarize again (or a capture made in RenderDoc's UI)
```

The automatic mode stops the game by its pid after the capture. In `--manual` mode, ask the user to reach
the scene first (gameplay, not a menu).

## 2. Read the summary

`traces/gfx/<capture>/frame.md`: passes in order (events, draws, targets with size and format, depth, shaders),
then each shader (entry, constant buffers with their variables, textures), then the render targets.
`thumbs/` holds small PNGs of each pass's targets and the final image: Read a PNG to see it.
`shaders/<stage>_<id>.txt` holds the disassembly; `frame.json` everything else.

## 3. Interpret

- A reading guide, not a fact about this game until `frame.md` shows it: many D3D11/12 games go depth
  prepass (depth only), shadow maps (square depth targets), G-buffer (several color targets: albedo,
  normals, material), lighting (full screen, reads the G-buffer), forward and transparent, post-processing
  (bloom, tonemap: full screen into smaller targets), UI last on the swap chain.
- Formats tell intent: `R16G16B16A16_FLOAT` = HDR, `R10G10B10A2` = normals, `D24S8`/`D32` = depth.
- Constant buffer variables name the parameters (view-projection, light direction, time, exposure); the
  disassembly shows the math. Tie them back to the code with `gst q strings` / `gst q refs` on those names.

## 4. Record

`notes/rendering.md`: a pass table (inputs -> outputs, formats), what each key shader computes, and the
evidence (capture file, event ids, image). Verified facts go to FACTS.md.

## Done when

`notes/rendering.md` lists the passes with their formats and explains the key shaders, backed by a capture
in `traces/gfx/`.

## Common problems

| Symptom | Fix |
|---|---|
| `runtime tools are not allowed` | verdict no-runtime or static-only: study shaders and materials on disk instead |
| `runs only from the sandbox copy` | `gst sandbox create` |
| `no graphics API registered` | the game did not start from sandbox/ (launcher, store relaunch): try `--manual`, or study statically |
| the capture shows a menu or loading screen | `--after` with more seconds, or `--manual` |
| `cannot be replayed on this machine` | the frame uses features this GPU/driver lacks: keep the capture, summarize what is listed |
