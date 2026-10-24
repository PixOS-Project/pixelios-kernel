#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
dest="$root/tools/third_party"
mkdir -p "$dest"
mkdir -p "$dest/gki"
curl -L --fail --retry 2 'https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/heads/main/mkbootimg.py?format=TEXT' | base64 -d > "$dest/mkbootimg.py"
curl -L --fail --retry 2 'https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/heads/main/gki/generate_gki_certificate.py?format=TEXT' | base64 -d > "$dest/gki/generate_gki_certificate.py"
touch "$dest/gki/__init__.py"
curl -L --fail --retry 2 'https://android.googlesource.com/platform/external/avb/+/refs/heads/main/avbtool.py?format=TEXT' | base64 -d > "$dest/avbtool.py"
