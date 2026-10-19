param([string]$ModelManifest, [string]$ModelArchive)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$edition = 'stock'
if ($ModelManifest) {
    $ModelManifest = (Resolve-Path $ModelManifest).Path
    $model = Get-Content $ModelManifest -Raw | ConvertFrom-Json
    $edition = $model.edition
    if (-not $ModelArchive) { $ModelArchive = "release/$($model.archive)" }
} elseif ($ModelArchive) { throw 'A model archive requires -ModelManifest.' }
function Run-Python {
    & python @args
    if ($LASTEXITCODE -ne 0) { throw "Python step failed: $args" }
}
function Run-GuiCheck {
    param([string]$Executable, [string]$Arguments, [int]$TimeoutSeconds = 240)
    $process = Start-Process $Executable -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        throw "Native application check timed out after $TimeoutSeconds seconds: $Arguments"
    }
    $process.WaitForExit()
    return $process
}

Run-Python -m pip install -r requirements-desktop.txt
New-Item -ItemType Directory -Force tools, release | Out-Null
foreach ($tool in @('ffmpeg', 'ffprobe')) {
    if (-not (Test-Path "tools/$tool.exe")) { throw "Run packaging/prepare-windows-tools.ps1 first: tools/$tool.exe is missing." }
}
$env:PATH = (Resolve-Path tools).Path + [IO.Path]::PathSeparator + $env:PATH
foreach ($tool in @('deno', 'uv')) {
    Copy-Item (Get-Command $tool -ErrorAction Stop).Source "tools/$tool.exe" -Force
}

$env:QT_QPA_PLATFORM = 'offscreen'
Run-Python -m pytest -q
Run-Python -m PyInstaller --noconfirm --clean --onedir --console --name TranscriptWorker `
    --collect-all yt_dlp --collect-all yt_dlp_ejs `
    --exclude-module torch --exclude-module whisperx --exclude-module PySide6 `
    worker_entry.py
Run-Python -m PyInstaller --noconfirm --clean --onedir --windowed --name MarcadoresDePaula `
    --collect-submodules keyring.backends `
    --add-data 'requirements-engine.txt;.' --add-data 'requirements-engine-windows-cpu.txt;.' `
    --add-data 'requirements-engine-windows-cuda.txt;.' --add-data 'transcript_desk;transcript_desk' `
    --add-data 'tools;tools' --exclude-module torch --exclude-module whisperx `
    run_app.py
Copy-Item 'dist/TranscriptWorker' 'dist/MarcadoresDePaula/_internal/worker' -Recurse -Force
if ($ModelManifest) {
    Run-Python scripts/verify_release_model.py $ModelArchive $ModelManifest
    Expand-Archive -Path $ModelArchive -DestinationPath dist/MarcadoresDePaula/_internal/models -Force
} else {
    Set-Content 'dist/MarcadoresDePaula/_internal/stock-default' 'large-v3-turbo'
}
New-Item -ItemType Directory -Force 'dist/MarcadoresDePaula/licenses' | Out-Null
Copy-Item 'build/windows-ffmpeg/licenses' 'dist/MarcadoresDePaula/licenses/FFmpeg' -Recurse -Force
Run-Python packaging/collect_licenses.py dist/MarcadoresDePaula/licenses
Copy-Item 'licenses/*' 'dist/MarcadoresDePaula/licenses' -Force
Copy-Item LICENSE 'dist/MarcadoresDePaula/LICENSE.txt'
$denoVersion = ((& deno --version)[0] -split ' ')[1]
& curl.exe --fail --location --silent --show-error --output 'dist/MarcadoresDePaula/licenses/Deno-LICENSE.md' "https://raw.githubusercontent.com/denoland/deno/v$denoVersion/LICENSE.md"
if ($LASTEXITCODE -ne 0) { throw "The licence for Deno $denoVersion could not be downloaded." }
foreach ($name in @('Deno-LICENSE.md', 'Python/LICENSE.txt', 'uv', 'LGPL-3.0.txt')) {
    if (-not (Test-Path "dist/MarcadoresDePaula/licenses/$name")) { throw "The package is missing licenses/$name." }
}
Copy-Item README.md 'dist/MarcadoresDePaula/README.md'
Copy-Item THIRD_PARTY.md 'dist/MarcadoresDePaula/THIRD_PARTY.md'
Copy-Item docs 'dist/MarcadoresDePaula/docs' -Recurse -Force

Run-Python scripts/smoke_packaged.py dist/MarcadoresDePaula
# Use Windows' native platform and font backend for the packaged interface.
Remove-Item Env:QT_QPA_PLATFORM
$smokeArgs = '--smoke-test --expect-stock --smoke-report release/default-model-verification.json'
if ($ModelManifest) { $smokeArgs = "--smoke-test --expect-model-pack `"$ModelManifest`" --smoke-report release/default-model-verification.json" }
$process = Run-GuiCheck 'dist/MarcadoresDePaula/MarcadoresDePaula.exe' $smokeArgs
if ($process.ExitCode -ne 0) { throw 'Packaged interface failed to start.' }
$env:TRANSCRIPT_DESK_DATA = Join-Path $env:LOCALAPPDATA 'Paula setup verification'
$process = Run-GuiCheck 'dist/MarcadoresDePaula/MarcadoresDePaula.exe' '--setup-smoke-test --smoke-report release/engine-setup-verification.json' 1500
if (Test-Path "$env:TRANSCRIPT_DESK_DATA/setup-logs") {
    New-Item -ItemType Directory -Force release/setup-logs | Out-Null
    Copy-Item "$env:TRANSCRIPT_DESK_DATA/setup-logs/*.log" release/setup-logs -Force
}
if ($process.ExitCode -ne 0) {
    if (Test-Path release/engine-setup-verification.json) { Get-Content release/engine-setup-verification.json }
    throw 'Engine installation through the packaged Setup dialog failed.'
}
$env:TRANSCRIPT_DESK_DATA = Join-Path $env:LOCALAPPDATA 'Paula recovery verification'
$process = Run-GuiCheck 'dist/MarcadoresDePaula/MarcadoresDePaula.exe' '--setup-smoke-test --simulate-python-link-error --smoke-report release/engine-recovery-verification.json' 1500
if (Test-Path "$env:TRANSCRIPT_DESK_DATA/setup-logs") {
    New-Item -ItemType Directory -Force release/setup-logs | Out-Null
    Copy-Item "$env:TRANSCRIPT_DESK_DATA/setup-logs/*.log" release/setup-logs -Force
}
if ($process.ExitCode -ne 0) {
    if (Test-Path release/engine-recovery-verification.json) { Get-Content release/engine-recovery-verification.json }
    throw 'Recovery from error 448 failed the Windows check.'
}
Remove-Item Env:TRANSCRIPT_DESK_DATA

$compiler = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $compiler) { $compiler = "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe" }
if (-not (Test-Path $compiler)) { throw 'Install Inno Setup 6 to produce the installer.' }
$version = (& python -c "from transcript_desk import __version__; print(__version__)").Trim()
& $compiler "/DEdition=$edition" "/DAppVersion=$version" packaging/installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
$installer = "release/MarcadoresDePaula-Setup-$version-$edition.exe"
$installedFolder = Join-Path $env:LOCALAPPDATA 'Paula installed verification'
$installerArgs = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR="' + $installedFolder + '"'
$process = Run-GuiCheck $installer $installerArgs
if ($process.ExitCode -ne 0) { throw 'The Windows installer failed.' }
$installedArgs = $smokeArgs.Replace('release/default-model-verification.json', 'release/installed-default-verification.json')
$process = Run-GuiCheck (Join-Path $installedFolder 'MarcadoresDePaula.exe') $installedArgs
if ($process.ExitCode -ne 0) { throw 'The installed application failed its default-model and playback checks.' }
$ffmpegSource = (Get-Content 'packaging/windows-tools.json' -Raw | ConvertFrom-Json).ffmpeg.archive
foreach ($name in @($ffmpegSource, 'COPYING.LGPLv2.1', 'build-ffmpeg-windows.sh', 'build.json')) {
    if (-not (Test-Path (Join-Path $installedFolder "licenses/FFmpeg/$name"))) { throw "The installed application is missing licenses/FFmpeg/$name." }
}
Get-ChildItem (Join-Path $installedFolder 'licenses/FFmpeg') | ForEach-Object { Write-Host "Installed licence file: $($_.Name) $($_.Length)" }
$process = Run-GuiCheck (Join-Path $installedFolder 'MarcadoresDePaula.exe') '--setup-save-smoke-test --smoke-report release/setup-save-verification.json'
if ($process.ExitCode -ne 0) {
    if (Test-Path release/setup-save-verification.json) { Get-Content release/setup-save-verification.json }
    throw "Saving Setup settings exited with code $($process.ExitCode)."
}
$process = Run-GuiCheck (Join-Path $installedFolder 'MarcadoresDePaula.exe') '--review-smoke-test --smoke-report release/review-verification.json'
if ($process.ExitCode -ne 0) {
    if (Test-Path release/review-verification.json) { Get-Content release/review-verification.json }
    throw "The installed review check exited with code $($process.ExitCode)."
}
foreach ($check in @('workspace', 'editor', 'speaker', 'processing-fault', 'preservation')) {
    $report = "release/$check-verification.json"
    $process = Run-GuiCheck (Join-Path $installedFolder 'MarcadoresDePaula.exe') "--$check-smoke-test --smoke-report $report"
    if ($process.ExitCode -ne 0) {
        if (Test-Path $report) { Get-Content $report }
        throw "The installed $check check exited with code $($process.ExitCode)."
    }
    if (-not (Test-Path $report)) { throw "The installed $check check did not write its report." }
    $result = Get-Content $report -Raw | ConvertFrom-Json
    if ($result.ok -ne $true -or $result.PSObject.Properties.Name -contains 'error') {
        Get-Content $report
        throw "The installed $check check did not pass or reported an error: $report"
    }
}
$smokePython = (Get-Command python -ErrorAction Stop).Source
foreach ($mode in @('button', 'close')) {
    $pauseState = Join-Path $env:LOCALAPPDATA "Paula pause verification $mode"
    foreach ($phase in @('pause', 'resume')) {
        $pauseReport = "release/$mode-$phase-verification.json"
        $pauseArgs = "--pause-smoke-test --smoke-phase $phase --pause-mode $mode --smoke-state `"$pauseState`" --smoke-python `"$smokePython`" --smoke-report $pauseReport"
        $process = Run-GuiCheck (Join-Path $installedFolder 'MarcadoresDePaula.exe') $pauseArgs
        if ($process.ExitCode -ne 0) {
            if (Test-Path $pauseReport) { Get-Content $pauseReport }
            throw "The installed $mode/$phase check exited with code $($process.ExitCode)."
        }
    }
}
$digest = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLower()
"$digest  $(Split-Path $installer -Leaf)" | Set-Content -Encoding ascii "$installer.sha256"
