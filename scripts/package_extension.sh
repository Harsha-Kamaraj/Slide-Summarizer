#!/usr/bin/env bash
# Build the zip to share with friends: dist/slide-summarizer-<version>.zip
# Run from the repo root after setting DEFAULT_BACKEND_URL in extension/config.js.
set -euo pipefail
cd "$(dirname "$0")/.."
version=$(python3 -c "import json; print(json.load(open('extension/manifest.json'))['version'])")
backend=$(grep -o 'DEFAULT_BACKEND_URL = "[^"]*"' extension/config.js | cut -d'"' -f2)
if [[ "$backend" == *localhost* ]]; then
  echo "DEFAULT_BACKEND_URL still points at localhost; friends' copies wouldn't reach the server." >&2
  exit 1
fi
mkdir -p dist
out="dist/slide-summarizer-$version.zip"
rm -f "$out"
# The zip contains a top-level "slide-summarizer" folder: the one friends select
# in "Load unpacked".
staging=$(mktemp -d)
cp -R extension "$staging/slide-summarizer"
(cd "$staging" && zip -qr - slide-summarizer -x '*.DS_Store') > "$out"
rm -rf "$staging"
echo "Built $out (backend: $backend)"
