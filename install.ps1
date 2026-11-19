# game-study-tool (gst) - install for Windows PowerShell 5.1+. Idempotent: safe to re-run.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1                    # venv + gst on PATH + doctor
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Tools             # + gst tools install core
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Plugin            # + Claude Code plugin gst@gst-local
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -EnableLongPaths   # + LongPathsEnabled=1 (admin, UAC)
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -DryRun            # print every step, change nothing
#                                                                             # (-WhatIf does the same)
# Steps: Python >= 3.12 -> venv (reused when it exists) -> pip install -e .[full] -> gst.exe into a PATH folder
#        (~/.local/bin, where the plugin hooks look) -> [gs_home] -> [tools] -> LongPaths check/[enable]
#        -> [plugin] -> gst doctor.
# Never done without its switch: tools downloads, the registry (LongPaths, Defender), the user PATH, Claude Code
# settings. The legacy gs plugin is never disabled here: -Plugin only prints the commands (docs/MIGRATION.md).
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Python = '',              # a Python >= 3.12 to create the venv with (default: found automatically)
    [string]$Venv = '',                # default: <repo>\.venv
    [string]$Extras = 'full',          # pip extras: full = index + unity + formats
    [string]$BinDir = '',              # where gst.exe goes (default: ~/.local/bin)
    [switch]$AddToPath,                # add BinDir to the user PATH when it is missing (otherwise only a warning)
    [string]$GsHome = '',              # set gs_home (study root) in the gst config
    [switch]$Tools,                    # gst tools install <Profiles>
    [string[]]$Profiles = @('core'),
    [switch]$Plugin,                   # register the Claude Code marketplace gst-local and the plugin gst@gst-local
    [switch]$EnableLongPaths,          # LongPathsEnabled = 1 (needs admin: asks through UAC)
    [switch]$System,                   # -EnableLongPaths + Defender exclusion of the study root (scripts/system-setup.ps1)
    [switch]$NoDoctor,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dry = [bool]($DryRun -or $WhatIfPreference)
if (-not $Venv) { $Venv = Join-Path $Root '.venv' }
if (-not $BinDir) { $BinDir = Join-Path $env:USERPROFILE '.local\bin' }
$StateDir = Join-Path $env:LOCALAPPDATA 'game-study-tool'
$Log = Join-Path $StateDir 'install.log'
$MinPy = [version]'3.12'

function Write-Log([string]$Message) {
    if ($Dry) { return }
    if (-not (Test-Path -LiteralPath $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
    Add-Content -Path $Log -Value ("{0} {1}" -f (Get-Date -Format s), $Message)
}
function Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
    Write-Log $Message
}
function Fail([string]$Message) {
    Write-Log "ERROR $Message"
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}
# Run a change, or only describe it in a dry run. Native output goes straight to the console.
# Probes of native tools run with ErrorActionPreference Continue: in PS 5.1 a redirected stderr line would
# otherwise stop the script.
function Invoke-Probe([scriptblock]$Probe) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return & $Probe } catch { return $null } finally { $ErrorActionPreference = $old }
}
function Invoke-Change([string]$What, [scriptblock]$Action) {
    if ($Dry) { Write-Host "    [dry-run] would $What" -ForegroundColor Yellow; return }
    Write-Log "do: $What"
    & $Action
}
function Get-PyVersion([string]$Exe) {
    # pyenv shims and the Microsoft Store stub start other programs: skip them
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return $null }
    if ($Exe -match '\\pyenv-win\\shims\\' -or $Exe -match '\\Microsoft\\WindowsApps\\') { return $null }
    $out = Invoke-Probe { & $Exe --version 2>&1 | Out-String }
    if ($out -match 'Python (\d+\.\d+)') { return [version]$Matches[1] }
    return $null
}
function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $found = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') -Filter uv.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match 'astral-sh\.uv' } | Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}
function Find-Python {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($Python) { $candidates.Add($Python) }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($line in (Invoke-Probe { & $py.Source -0p 2>$null })) {
            if ($line -match '(?<path>[A-Za-z]:\\.+python\.exe)\s*$') { $candidates.Add($Matches['path'].Trim()) }
        }
    }
    $uv = Find-Uv
    if ($uv) {
        foreach ($v in @('3.13', '3.12')) {
            $p = (Invoke-Probe { & $uv python find $v 2>$null } | Select-Object -First 1)
            if ($p) { $candidates.Add($p.Trim()) }
        }
    }
    foreach ($c in (Get-Command python -All -ErrorAction SilentlyContinue)) { $candidates.Add($c.Source) }
    foreach ($c in @($candidates)) {
        # a venv's python (another project's, on PATH): use the interpreter it was made from
        $cfg = Join-Path (Split-Path -Parent (Split-Path -Parent $c)) 'pyvenv.cfg'
        if (Test-Path -LiteralPath $cfg) {
            $homeLine = Get-Content -LiteralPath $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
            if ($homeLine) {
                $base = Join-Path (($homeLine -split '=', 2)[1].Trim()) 'python.exe'
                if (Test-Path -LiteralPath $base) { $candidates[$candidates.IndexOf($c)] = $base }
            }
        }
    }
    foreach ($c in $candidates) {
        $v = Get-PyVersion $c
        if ($v -and $v -ge $MinPy) { return @{ Path = $c; Version = $v } }
    }
    return $null
}
function Test-Admin {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Get-LongPaths {
    try {
        return [int](Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -ErrorAction Stop).LongPathsEnabled
    } catch { return $null }
}
function Test-OnPath([string]$Folder) {
    $want = [IO.Path]::GetFullPath($Folder).TrimEnd('\')
    $all = @($env:Path -split ';') + @(([Environment]::GetEnvironmentVariable('Path', 'User')) -split ';')
    foreach ($p in $all) {
        if (-not $p) { continue }
        try { if ([IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($p)).TrimEnd('\') -ieq $want) { return $true } } catch { }
    }
    return $false
}

if ($Dry) { Write-Host 'DRY RUN: nothing is installed, created or changed.' -ForegroundColor Yellow }
Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue  # a stray VIRTUAL_ENV confuses pip/uv

# --- 1. Python and the venv -------------------------------------------------------------------------
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$venvVersion = Get-PyVersion $VenvPy
if ($venvVersion) {
    if ($venvVersion -lt $MinPy) {
        Fail "the venv $Venv has Python $venvVersion; gst needs >= $MinPy. Pass -Venv <new folder> (the old one is left alone)."
    }
    Step "venv: $Venv (Python $venvVersion, reused)"
} else {
    $found = Find-Python
    if (-not $found) {
        $uv = Find-Uv
        if (-not $uv) {
            Fail "no Python >= $MinPy found. Install one (winget install Python.Python.3.13, or uv python install 3.13) or pass -Python <python.exe>."
        }
        Step "no Python >= $MinPy found: installing CPython 3.13 with uv ($uv)"
        Invoke-Change "run: uv python install 3.13" { & $uv python install 3.13; if ($LASTEXITCODE -ne 0) { Fail 'uv python install 3.13 failed' } }
        $found = @{ Path = '(uv-managed 3.13)'; Version = [version]'3.13' }
        if (-not $Dry) { $found = Find-Python; if (-not $found) { Fail 'Python 3.13 not found after uv python install' } }
    }
    Step ("Python {0}: {1}" -f $found.Version, $found.Path)
    Step "creating the venv $Venv"
    Invoke-Change "run: `"$($found.Path)`" -m venv `"$Venv`"" {
        & $found.Path -m venv $Venv
        if ($LASTEXITCODE -ne 0) { Fail "python -m venv $Venv failed" }
    }
}

# --- 2. gst into the venv (editable: changes to src/gst apply immediately) -----------------------------
$spec = "$Root[$Extras]"
Step "installing gst: pip install -e `"$spec`""
$hasPip = $false
if (Test-Path -LiteralPath $VenvPy) {
    $hasPip = [bool](Invoke-Probe { & $VenvPy -m pip --version 2>$null; if ($LASTEXITCODE -ne 0) { $null } })
}
if ($hasPip -or ($Dry -and -not (Test-Path -LiteralPath $VenvPy))) {
    Invoke-Change "run: `"$VenvPy`" -m pip install -e `"$spec`"" {
        & $VenvPy -m pip install --disable-pip-version-check -e $spec
        if ($LASTEXITCODE -ne 0) { Fail 'pip install -e failed (see above)' }
    }
} else {
    $uv = Find-Uv
    if ($uv) {  # venvs made by uv have no pip
        Invoke-Change "run: uv pip install --python `"$VenvPy`" -e `"$spec`"" {
            & $uv pip install --python $VenvPy -e $spec
            if ($LASTEXITCODE -ne 0) { Fail 'uv pip install -e failed (see above)' }
        }
    } else {
        Invoke-Change "run: `"$VenvPy`" -m ensurepip, then pip install -e `"$spec`"" {
            & $VenvPy -m ensurepip --upgrade
            & $VenvPy -m pip install --disable-pip-version-check -e $spec
            if ($LASTEXITCODE -ne 0) { Fail 'pip install -e failed (see above)' }
        }
    }
}
$VenvGst = Join-Path $Venv 'Scripts\gst.exe'

# --- 3. gst on PATH: the venv's launcher copied to BinDir (it points at the venv's python) -------------
$Gst = Join-Path $BinDir 'gst.exe'
Step "gst on PATH: $Gst -> $VenvGst"
Invoke-Change "copy $VenvGst to $Gst" {
    if (-not (Test-Path -LiteralPath $VenvGst)) { Fail "$VenvGst missing after the install" }
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    try { Copy-Item -LiteralPath $VenvGst -Destination $Gst -Force }
    catch { Fail "could not replace $Gst (is a gst command running?): $($_.Exception.Message)" }
}
if (-not (Test-OnPath $BinDir)) {
    if ($AddToPath) {
        Invoke-Change "append $BinDir to the user PATH" {
            $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
            [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $BinDir).TrimStart(';')), 'User')
        }
    } else {
        Write-Warning "$BinDir is not on PATH: run gst as `"$Gst`", or re-run with -AddToPath (user PATH), then restart VS Code/shells."
    }
}
if ($Dry -and -not (Test-Path -LiteralPath $Gst)) { $Gst = 'gst' }

# --- 4. optional configuration and tools --------------------------------------------------------------
if ($GsHome) {
    Step "study root (gs_home): $GsHome"
    Invoke-Change "run: gst config set gs_home `"$GsHome`"" {
        & $Gst config set gs_home $GsHome
        if ($LASTEXITCODE -ne 0) { Fail 'gs_home must be outside any git repository' }
    }
}
if ($Tools) {
    Step ("tools: gst tools install {0} (some installers ask for UAC)" -f ($Profiles -join ' '))
    Invoke-Change ("run: gst tools install {0}" -f ($Profiles -join ' ')) {
        & $Gst tools install @Profiles
        if ($LASTEXITCODE -ne 0) { Write-Warning 'some tools failed; see above (gst tools install <id> retries one)' }
    }
} else {
    Write-Host '    tools: skipped (-Tools runs gst tools install core; gst doctor lists what is missing)'
}

# --- 5. LongPathsEnabled: checked always, changed only with -EnableLongPaths / -System -----------------
$lp = Get-LongPaths
$lpText = if ($lp -eq 1) { '1 (enabled)' } elseif ($null -eq $lp) { 'unset (= disabled)' } else { "$lp (disabled)" }
Step "LongPathsEnabled = $lpText"
if ($EnableLongPaths -or $System) {
    $script = Join-Path $Root 'scripts\system-setup.ps1'
    $args2 = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$script`"")
    if ($System) {
        $home2 = $GsHome
        if (-not $home2 -and (Test-Path -LiteralPath $VenvPy)) {
            $home2 = Invoke-Probe { & $VenvPy -X utf8 -c 'from gst import config; print(config.gs_home())' 2>$null | Select-Object -Last 1 }
        }
        if (-not $home2) { Fail '-System needs the study root: pass -GsHome <folder>' }
        $args2 += @('-GsHome', "`"$home2`"")
    } else {
        $args2 += '-LongPathsOnly'
    }
    if ($lp -eq 1 -and -not $System) {
        Write-Host '    already enabled: nothing to do'
    } elseif (Test-Admin) {
        Invoke-Change "run (already elevated): powershell $($args2 -join ' ')" {
            & powershell.exe @args2
            if ($LASTEXITCODE -ne 0) { Write-Warning "system-setup.ps1 exited with $LASTEXITCODE" }
        }
    } else {
        Write-Host '    this needs administrator rights: Windows will ask (UAC)'
        Invoke-Change "start elevated (UAC prompt): powershell $($args2 -join ' ')" {
            $proc = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList $args2
            if ($proc.ExitCode -ne 0) { Write-Warning "system-setup.ps1 exited with $($proc.ExitCode)" }
        }
    }
} elseif ($lp -ne 1) {
    Write-Host '    recommended: re-run with -EnableLongPaths (asks for admin through UAC) so third-party tools handle deep'
    Write-Host '    extracted trees; gst itself already copes (\\?\ paths). Nothing was changed.'
}

# --- 6. Claude Code plugin (only with -Plugin) ----------------------------------------------------------
if ($Plugin) {
    Step 'Claude Code: marketplace gst-local + plugin gst@gst-local'
    Invoke-Change "run: gst setup plugin --root `"$Root`" (claude plugin marketplace add + install gst@gst-local --scope user)" {
        & $Gst setup plugin --root $Root
        if ($LASTEXITCODE -ne 0) { Write-Warning 'gst setup plugin failed; see above' }
    }
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    $legacy = $null
    if ($claude) {
        $legacy = Invoke-Probe { & $claude.Source plugin list 2>$null | Select-String -Pattern 'game-study@[\w.-]+' | Select-Object -First 1 }
    }
    $legacyId = 'game-study@game-study-local'
    if ($legacy) { $legacyId = $legacy.Matches[0].Value }
    Write-Host ''
    Write-Host "The legacy gs plugin ($legacyId) is left as it is: both coexist (skills game-study:* and gst:*)." -ForegroundColor Green
    Write-Host 'When gst covers your studies (gst migrate --all --dry-run, then gst migrate --all), switch with:' -ForegroundColor Green
    Write-Host "  claude plugin disable $legacyId --scope user"
    Write-Host '  claude plugin enable gst@gst-local --scope user'
    Write-Host "Rollback: claude plugin disable gst@gst-local --scope user; claude plugin enable $legacyId --scope user"
    Write-Host 'Details: docs/MIGRATION.md'
} else {
    Write-Host '    Claude Code plugin: skipped (-Plugin registers gst@gst-local; docs/MIGRATION.md)'
}

# --- 7. health check -------------------------------------------------------------------------------------
if (-not $NoDoctor) {
    Step 'health check: gst doctor'
    Invoke-Change 'run: gst doctor' { & $Gst doctor }
}
Write-Host ''
if ($Dry) {
    Write-Host 'Dry run finished: nothing was changed.' -ForegroundColor Yellow
} else {
    Write-Host 'Done. Restart VS Code/shells if gst is not found, then:' -ForegroundColor Green
    Write-Host '  gst study "<install folder or game name>" --dry-run' -ForegroundColor Green
}
exit 0
