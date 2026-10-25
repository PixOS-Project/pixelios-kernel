#!/bin/sh
# Fetch AOSP boot tools, pinned to commits and verified by sha256 (fail closed).
# Pins resolved 2026-10-25 from refs/heads/main via the googlesource JSON log:
#   platform/system/tools/mkbootimg d2bb0af5ba6d3198a3e99529c97eda1be0b5a093 (2025-03-02)
#   platform/external/avb           761178607206f4cb2af79ed9eec52d8cbd814adb (2025-03-13)
# Licences (file headers): mkbootimg.py, generate_gki_certificate.py = Apache-2.0;
# avbtool.py = MIT-style permission notice (Copyright 2016 The Android Open Source Project).
set -eu
root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
dest="$root/tools/third_party"
mkbootimg_rev=d2bb0af5ba6d3198a3e99529c97eda1be0b5a093
avb_rev=761178607206f4cb2af79ed9eec52d8cbd814adb
mkdir -p "$dest/gki"

# fetch REPO REV PATH OUT SHA256
fetch() {
	curl -L --fail --retry 2 -s "https://android.googlesource.com/$1/+/$2/$3?format=TEXT" | base64 -d > "$4"
	got=$(sha256sum "$4" | cut -d' ' -f1)
	if [ "$got" != "$5" ]; then
		echo "sha256 mismatch for $3: got $got want $5" >&2
		rm -f "$4"
		echo "RESULT: FAIL"
		exit 1
	fi
}

fetch platform/system/tools/mkbootimg "$mkbootimg_rev" mkbootimg.py "$dest/mkbootimg.py" \
	37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c
fetch platform/system/tools/mkbootimg "$mkbootimg_rev" gki/generate_gki_certificate.py "$dest/gki/generate_gki_certificate.py" \
	1bb1feec68a13da18d581aa2c631798f86f6bc10b55d587b2dd31446a0f8a203
touch "$dest/gki/__init__.py"
fetch platform/external/avb "$avb_rev" avbtool.py "$dest/avbtool.py" \
	e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b
echo "RESULT: OK"
