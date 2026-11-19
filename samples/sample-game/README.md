# Sample game

A tiny game written for the toolkit: three enemies chase the player in an arena and take health from them every two
seconds. It exists to practice and test every layer of a study on a target of our own, with no protection at all:

| Layer | What the game offers |
| --- | --- |
| Native code | `SampleGame.exe` in C++ (MSVC) with `SampleGame.pdb`; classes with RTTI and inheritance (`sg::Entity` → `sg::Player`, `sg::Enemy`) |
| .NET code | `tools/SampleGame.SaveTool.dll`: a reader of the save format in C# |
| Formats | the `SGSV` save (little-endian binary, CRC-32 at the end) |
| Runtime | the player's HP (`sg::Entity::hp`) changes on every attack; files read and written: `settings.ini`, the save |
| Graphics | Direct3D 11 in two passes (scene into an HDR target, post-processing on the swap chain), shaders compiled at startup |

## Build

Needs Visual Studio (Build Tools) with C++ x64 and the .NET SDK:

```powershell
.\build.ps1        # produces build\SampleGame.exe (+ .pdb), build\settings.ini and build\tools\
```

`build\` stays out of git.

## Run

```powershell
build\SampleGame.exe                    # window: H heals, F5 saves, F9 loads, R restarts, Esc quits
build\SampleGame.exe --frames 300       # window that closes by itself (captures, traces)
build\SampleGame.exe --headless --ticks 600 --save run.sgs --timestamp 1700000000
build\tools\SampleGame.SaveTool.exe verify run.sgs
```

The window's default save is at `%LOCALAPPDATA%\SampleGame\save.sgs`.

## Study it with the toolkit

```powershell
gst new "<path>\samples\sample-game\build" --slug sample-game
cd D:\game-study\sample-game
gst decompile native        # Ghidra with the PDB: classes, field layouts, calls
gst decompile dotnet        # the SaveTool in C#
```

The end-to-end tests use their own workspace (`_selftest-sample`).
