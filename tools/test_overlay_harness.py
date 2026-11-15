#!/usr/bin/env python3
"""
test_overlay_harness.py: Verifiable Test Oracle for Dev Overlay Packaging (Ladder 3)
===================================================================================
Tests packaging of org.pixelios.devd daemon and its launchd plist into a SWEETDEV
v2 compressed overlay archive suitable for fastboot boot on Google Pixel 6a.
"""

import os
import shutil
import subprocess
import sys
import tempfile

# The script's own directory (tools/ of whichever checkout or worktree it lives in), never a cwd-relative path.
HERE = os.path.dirname(os.path.abspath(__file__))
KERNEL_TOOLS = HERE
sys.path.insert(0, KERNEL_TOOLS)

try:
    import mkdevoverlay
except ImportError:  # lint-silent: expected mkdevoverlay absent: Test 0 prints RESULT: ERROR
    mkdevoverlay = None

TMP_DIR = os.path.join(tempfile.mkdtemp(prefix="devd-overlay-"), "test_overlay")
TOTAL = 4
PLIST_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>org.pixelios.devd</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/devd</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/private/var/log/devd.log</string>
    <key>StandardErrorPath</key>
    <string>/private/var/log/devd.log</string>
</dict>
</plist>
"""


def run():
    score = 0
    print("--- Running Dev Overlay Packaging Verifiable Test Suite (Ladder 3) ---")

    if mkdevoverlay is None:
        print("[-] Test 0: mkdevoverlay module could not be imported")
        print("RESULT: ERROR mkdevoverlay not importable from " + KERNEL_TOOLS)
        return -1

    if os.path.exists(TMP_DIR):
        shutil.rmtree(TMP_DIR)
    staging_dir = os.path.join(TMP_DIR, "staging")
    os.makedirs(os.path.join(staging_dir, "usr/local/bin"), exist_ok=True)
    os.makedirs(os.path.join(staging_dir, "System/Library/LaunchDaemons"), exist_ok=True)

    # 1. Compilation of devd for overlay
    devd_bin = os.path.join(staging_dir, "usr/local/bin/devd")
    c = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-o", devd_bin, os.path.join(HERE, "devd.c")], capture_output=True)
    if c.returncode == 0 and os.path.exists(devd_bin):
        os.chmod(devd_bin, 0o755)
        print("[+] Test 1 (Compile devd for overlay): PASSED")
        score += 1
    else:
        print(f"[-] Test 1 (Compile devd for overlay): FAILED ({c.stderr})")

    # 2. Plist staging & allowlist validation
    plist_path = os.path.join(staging_dir, "System/Library/LaunchDaemons/org.pixelios.devd.plist")
    with open(plist_path, "w") as f:
        f.write(PLIST_CONTENT)
    os.chmod(plist_path, 0o644)

    bin_check = mkdevoverlay.path_check("/usr/local/bin/devd")
    plist_check = mkdevoverlay.path_check("/System/Library/LaunchDaemons/org.pixelios.devd.plist")
    if bin_check is None and plist_check is None:
        print("[+] Test 2 (Kernel allowlist checks for devd + plist): PASSED")
        score += 1
    else:
        print(f"[-] Test 2 (Kernel allowlist checks): FAILED (bin: {bin_check}, plist: {plist_check})")

    # 3. SWEETDEV v2 archive packaging
    archive_out = os.path.join(TMP_DIR, "devd_overlay.bin")
    c = subprocess.run([
        sys.executable,
        os.path.join(KERNEL_TOOLS, "mkdevoverlay.py"),
        staging_dir,
        archive_out,
        "--compress"
    ], capture_output=True, text=True)

    if c.returncode == 0 and os.path.exists(archive_out):
        print("[+] Test 3 (Pack SWEETDEV v2 archive): PASSED")
        score += 1
    else:
        print(f"[-] Test 3 (Pack SWEETDEV v2 archive): FAILED ({c.stderr})")

    # 4. Kernel Parse & Size Validation (<64 KB)
    if os.path.exists(archive_out):
        with open(archive_out, "rb") as f:
            data = f.read()
        try:
            parsed = mkdevoverlay.parse(data)
            paths = [entry[0] for entry in parsed]
            size_bytes = len(data)
            if "/usr/local/bin/devd" in paths and "/System/Library/LaunchDaemons/org.pixelios.devd.plist" in paths:
                if size_bytes < 64 * 1024:
                    print(f"[+] Test 4 (Kernel parse & lean size - {size_bytes} B): PASSED")
                    score += 1
                else:
                    print(f"[-] Test 4 (Size too large: {size_bytes} B >= 64KB)")
            else:
                print(f"[-] Test 4 (Missing expected paths in parsed archive: {paths})")
        except Exception as e:
            print(f"[-] Test 4 (Kernel parse failed: {e})")

    failed = TOTAL - score
    print(f"\nRESULT: {'PASS' if failed == 0 else 'FAIL'} {score} passed, {failed} failed")
    return score


# T-c24f: `unittest discover` (tools/gate-unit-shards) runs this script through lib.script_case: exit 0 + last `RESULT: PASS`
import test_devd_lib as lib  # noqa: E402 (script_case wrapper)
ScriptRun = lib.script_case(__file__, 300)


if __name__ == "__main__":
    s = run()
    sys.exit(2 if s < 0 else (0 if s == TOTAL else 1))
