# Builds the sample game into build/: native/ with MSVC (SampleGame.exe + SampleGame.pdb) and
# managed/SaveTool with the .NET SDK (build/tools/SampleGame.SaveTool.dll).
# Needs Visual Studio (Build Tools) with the C++ x64 tools and the .NET SDK. Usage: .\build.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$build = Join-Path $root "build"
$obj = Join-Path $build "obj"
New-Item -ItemType Directory -Force $build, $obj | Out-Null

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "Visual Studio (Build Tools) with the C++ workload is required" }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "no Visual Studio installation with the MSVC x64 tools" }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
$env:PATH = "$(Split-Path $vswhere);$env:PATH"  # vcvars64.bat calls vswhere.exe by name

$sources = (Get-ChildItem (Join-Path $root "native") -Filter *.cpp | ForEach-Object { '"' + $_.FullName + '"' }) -join " "
$cl = "cl /nologo /std:c++17 /EHsc /O2 /Zi /GR /W4 /permissive- /DUNICODE /D_UNICODE " +
      "/Fo`"$obj\\`" /Fd`"$obj\vc.pdb`" /Fe`"$build\SampleGame.exe`" $sources " +
      "/link /DEBUG:FULL /INCREMENTAL:NO /OPT:REF /OPT:ICF /PDB:`"$build\SampleGame.pdb`" /SUBSYSTEM:CONSOLE " +
      "d3d11.lib dxgi.lib d3dcompiler.lib user32.lib"
$bat = Join-Path $obj "build-native.bat"
Set-Content -Path $bat -Encoding Ascii -Value "@echo off`r`ncall `"$vcvars`" >nul 2>&1 || exit /b 1`r`n$cl 2>&1`r`n"
# Windows PowerShell turns any stderr line of a native tool into an error: judge the tools by exit code
$ErrorActionPreference = "Continue"
& cmd.exe /d /c $bat
if ($LASTEXITCODE -ne 0) { throw "MSVC build failed" }
Copy-Item (Join-Path $root "settings.ini") $build -Force

dotnet build (Join-Path $root "managed\SaveTool\SaveTool.csproj") -c Release -o (Join-Path $build "tools") --nologo -v quiet 2>&1
if ($LASTEXITCODE -ne 0) { throw "dotnet build failed" }
Write-Host "built: $build\SampleGame.exe (+ SampleGame.pdb), $build\tools\SampleGame.SaveTool.dll"
