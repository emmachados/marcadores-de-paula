#!/bin/bash
# Cross-compiles LGPL ffmpeg.exe and ffprobe.exe for Windows x64 from the official source archive.
set -euo pipefail
cd "$(dirname "$0")/.."
root="$PWD"
manifest=packaging/windows-tools.json
read -r version archive url sha256 < <(python3 -c "
import json; f = json.load(open('$manifest'))['ffmpeg']
print(f['version'], f['archive'], f['url'], f['sha256'])")
out="$root/build/windows-ffmpeg"
sources="$root/build/windows-ffmpeg-sources"
rm -rf "$out" "$sources/ffmpeg-$version"
mkdir -p "$sources" "$out/bin" "$out/licenses"
[ -f "$sources/$archive" ] || curl --fail --location --silent --show-error "$url" -o "$sources/$archive"
digest=$( (sha256sum "$sources/$archive" 2>/dev/null || shasum -a 256 "$sources/$archive") | cut -d' ' -f1)
[ "$digest" = "$sha256" ] || { echo "FFmpeg archive SHA-256 mismatch: $digest" >&2; exit 1; }
tar -xf "$sources/$archive" -C "$sources"
configure=(--arch=x86_64 --target-os=mingw32 --cross-prefix=x86_64-w64-mingw32-
  --pkg-config=false --enable-static --disable-shared --disable-doc --disable-debug --disable-autodetect
  --disable-ffplay --extra-ldflags=-static)
jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
(
  cd "$sources/ffmpeg-$version"
  ./configure "${configure[@]}"
  make -j"$jobs"
  cp ffmpeg.exe ffprobe.exe "$out/bin/"
  cp COPYING.LGPLv2.1 "$out/licenses/"
)
x86_64-w64-mingw32-strip "$out/bin/ffmpeg.exe" "$out/bin/ffprobe.exe"
cp "$sources/$archive" "$out/licenses/"
cp packaging/build-ffmpeg-windows.sh "$manifest" "$out/licenses/"
python3 - "$out" "$version" "$archive" "$sha256" "${configure[*]}" "$(x86_64-w64-mingw32-gcc --version | head -n 1)" <<'PY'
import hashlib, json, sys
from pathlib import Path
out, version, archive, sha256, configure, compiler = sys.argv[1:]
digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
json.dump({'version': version, 'source_archive': archive, 'source_sha256': sha256,
           'configure': configure, 'compiler': compiler,
           'binaries': {n: digest(f'{out}/bin/{n}') for n in ('ffmpeg.exe', 'ffprobe.exe')}},
          open(f'{out}/build.json', 'w'), indent=2)
PY
cp "$out/build.json" "$out/licenses/"
cat "$out/build.json"
