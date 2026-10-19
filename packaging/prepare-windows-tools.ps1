param([string]$FFmpegBuild = 'build/windows-ffmpeg')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$package = (Get-Content 'packaging/windows-tools.json' -Raw | ConvertFrom-Json).ffmpeg
$build = Get-Content (Join-Path $FFmpegBuild 'build.json') -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force release, tools | Out-Null
if ($build.version -ne $package.version -or $build.source_sha256 -ne $package.sha256) {
    throw 'The FFmpeg build does not match packaging/windows-tools.json.'
}
$source = Join-Path $FFmpegBuild "licenses/$($package.archive)"
if ((Get-FileHash $source -Algorithm SHA256).Hash.ToLower() -ne $package.sha256) {
    throw 'The bundled FFmpeg source archive does not match its pinned SHA-256.'
}
if (-not (Test-Path (Join-Path $FFmpegBuild 'licenses/COPYING.LGPLv2.1'))) { throw 'The FFmpeg LGPL licence text is missing.' }
foreach ($name in @('ffmpeg', 'ffprobe')) {
    $binary = Join-Path $FFmpegBuild "bin/$name.exe"
    if ((Get-FileHash $binary -Algorithm SHA256).Hash.ToLower() -ne $build.binaries."$name.exe") {
        throw "$name.exe does not match the hash recorded by its build."
    }
    Copy-Item $binary "tools/$name.exe" -Force
}
$versionOutput = & tools/ffmpeg.exe -hide_banner -version
if ($LASTEXITCODE -ne 0 -or $versionOutput[0] -notlike "ffmpeg version $($package.version)*") {
    throw 'The FFmpeg executable did not report the expected version.'
}
$configuration = ($versionOutput | Where-Object { $_ -like 'configuration:*' }) -join ' '
if ($configuration -match '--enable-(gpl|nonfree|version3)') { throw "FFmpeg was configured with a GPL or nonfree option: $configuration" }
$licence = (& tools/ffmpeg.exe -hide_banner -L) -join ' '
if ($licence -notmatch 'GNU Lesser General Public License') { throw 'FFmpeg does not report the LGPL.' }
Write-Host $versionOutput[0]
$compiler = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $compiler) { $compiler = "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe" }
if (-not (Test-Path $compiler)) { throw 'Install Inno Setup 6 to produce the installer.' }
$compilerVersion = (Get-Item $compiler).VersionInfo.ProductVersion
Write-Host "Using Inno Setup $compilerVersion at $compiler"
if ($env:GITHUB_PATH) {
    (Resolve-Path tools).Path | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
    (Split-Path $compiler -Parent) | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
}
@{ version = $package.version; source_archive = $package.archive; source_sha256 = $package.sha256;
   source_url = $package.url; license = $package.license; configure = $build.configure;
   build_compiler = $build.compiler; binaries = $build.binaries; version_output = $versionOutput[0];
   configuration = $configuration; installer_compiler = $compiler; installer_compiler_version = $compilerVersion } |
    ConvertTo-Json | Set-Content -Encoding utf8 'release/windows-tools-verification.json'
