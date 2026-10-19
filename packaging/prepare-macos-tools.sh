#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(uname -m)" != arm64 ]; then
  echo 'The current processing engine requires Apple silicon.' >&2
  exit 1
fi
mkdir -p build/macos-sources tools/macos
curl --fail --location --silent --show-error https://ffmpeg.org/releases/ffmpeg-7.1.3.tar.xz -o build/macos-sources/ffmpeg-7.1.3.tar.xz
curl --fail --location --silent --show-error https://github.com/denoland/deno/releases/download/v2.9.5/deno-aarch64-apple-darwin.zip -o build/macos-sources/deno-aarch64-apple-darwin.zip
curl --fail --location --silent --show-error https://raw.githubusercontent.com/denoland/deno/v2.9.5/LICENSE.md -o build/macos-sources/Deno-LICENSE.md
curl --fail --location --silent --show-error https://github.com/astral-sh/python-build-standalone/releases/download/20261009/cpython-3.11.17%2B20261009-aarch64-apple-darwin-install_only_stripped.tar.gz -o build/macos-sources/python-3.11.17.tar.gz
shasum -a 256 -c packaging/macos-tools.sha256
tar -xf build/macos-sources/ffmpeg-7.1.3.tar.xz -C build/macos-sources
root="$PWD"
(
  cd build/macos-sources/ffmpeg-7.1.3
  ./configure --prefix="$root/build/macos-ffmpeg" --enable-shared --disable-static --disable-doc --disable-debug --disable-autodetect --extra-cflags=-mmacosx-version-min=13.0 --extra-ldflags=-mmacosx-version-min=13.0
  make -j8
  make install
)
unzip -o build/macos-sources/deno-aarch64-apple-darwin.zip -d build/macos-sources/deno
python="${PAULA_BUILD_PYTHON:-.venv/bin/python}"
uv_binary="$($python -c 'import uv; print(uv.find_uv_bin())')"
"$python" packaging/prepare-macos-tools.py --ffmpeg-prefix build/macos-ffmpeg --uv "$uv_binary" --deno build/macos-sources/deno/deno
