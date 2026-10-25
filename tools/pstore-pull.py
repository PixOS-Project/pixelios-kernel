#!/usr/bin/env python3
"""Pull the PixelOS probe shim log out of Android pstore after a warm reset.

The shim (shim/pstore.c) writes its whole RAM log into the Linux ramoops
console zone right before PSCI SYSTEM_RESET. Android then exposes the
previous boot's console as /sys/fs/pstore/console-ramoops-0. The log is
found by the marker line "PIXELIOS PSTORE v1" (keep in sync with
pstore_marker in shim/pstore.c). When the log outgrows the zone the
shim keeps the marker as the FIXED FIRST LINE followed by the newest
tail (size == capacity, the retained tail may start mid-line), so the
build is always identifiable.

Subcommands:
  extract <file>        Extract the shim log from a console-ramoops file
                        (raw text, or a binary persistent_ram_buffer blob).
  from-bugreport <zip>  Find FS/sys/fs/pstore/console-ramoops* inside an
                        Android bugreport zip and extract the log.
  run <image>           Automation: fastboot boot <image>, wait for adb,
                        cat /sys/fs/pstore/console-ramoops-0 (fall back to
                        `adb bugreport`), save the log under logs/, then
                        `adb reboot bootloader` and wait for fastboot.

stdlib only; no third-party packages. `run` refuses any device whose
`fastboot getvar product` is not "bluejay" (docs/SAFETY.md).
"""
import argparse
import datetime
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

MARKER = b"PIXELIOS PSTORE v1"
PERSISTENT_RAM_SIG = 0x43474244  # 'DBGC' little-endian in memory
BUFFER_HEADER = struct.Struct("<III")  # sig, start, size
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE = "Pull the PixelOS probe shim log out of Android pstore after a warm reset."

FASTBOOT_TIMEOUT_S = 60
FASTBOOT_BOOT_TIMEOUT_S = 180
ADB_POLL_S = 3
ADB_WAIT_TIMEOUT_S = 240
ADB_CMD_TIMEOUT_S = 120
BUGREPORT_TIMEOUT_S = 600
REBOOT_BOOTLOADER_TIMEOUT_S = 120


def decode_console_ramoops(raw: bytes) -> bytes:
    """Unwrap a raw persistent_ram_buffer if present.

    /sys/fs/pstore/console-ramoops-0 normally holds the already-unwrapped
    old log (plain text), but tolerate a raw dump of the zone too: a
    valid header (sig DBGC, size within the buffer, start <= size) is
    decoded in the kernel's old-log order, data[start..size) followed by
    data[0..start) (fs/pstore/ram_core.c persistent_ram_save_old).
    """
    if len(raw) >= BUFFER_HEADER.size:
        sig, start, size = BUFFER_HEADER.unpack_from(raw, 0)
        data = raw[BUFFER_HEADER.size:]
        if sig == PERSISTENT_RAM_SIG and 0 < size <= len(data) and start <= size:
            return data[:size][start:] + data[:size][:start]
    return raw


def extract_log(raw: bytes) -> bytes:
    """Return our log: from the marker line to the end of the decoded
    buffer, tolerating other console text before (and, after a later
    reboot overwrote things, around) it.

    In a tail-keep (truncated) zone the marker is the fixed first line
    of the retained data; find() picks it up at offset 0 there, and the
    bytes after it are the newest tail (its first line may be partial).
    """
    data = decode_console_ramoops(raw)
    idx = data.find(MARKER)
    if idx < 0:
        return b""
    return data[idx:]


def find_bugreport_pstore(zf: zipfile.ZipFile) -> str | None:
    """Locate the console-ramoops entry inside a bugreport zip."""
    names = zf.namelist()
    candidates = [n for n in names if re.search(r"(^|/)sys/fs/pstore/console-ramoops", n)]
    # Prefer console-ramoops-0, then the unnumbered console-ramoops.
    candidates.sort(key=lambda n: (0 if n.endswith("console-ramoops-0") else 1, len(n)))
    return candidates[0] if candidates else None


def parse_product(getvar_output: str) -> str | None:
    m = re.search(r"product:\s*(\S+)", getvar_output)
    return m.group(1) if m else None


def run_cmd(cmd, timeout, what):
    """Run an external command with a hard timeout; never raises."""
    try:
        return subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False
        )
    except FileNotFoundError:
        print(f"error: {what}: command not found: {cmd[0]}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print(f"error: {what}: timed out after {timeout}s: {' '.join(cmd)}", file=sys.stderr)
        return None


def adb_state(devices_output: str) -> str | None:
    """Map `adb devices` output to 'device' / 'unauthorized' / None."""
    state = None
    for line in devices_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("device", "unauthorized", "recovery", "sideload"):
            if parts[1] == "device":
                return "device"
            state = parts[1]
    return state


def cmd_extract(args) -> int:
    raw = args.file.read_bytes()
    log = extract_log(raw)
    if not log:
        print(
            "error: marker 'PIXELIOS PSTORE v1' not found in this file\n"
            "(either ABL scrubbed the zone, a later reboot overwrote it with\n"
            "the Android console, or this is not a shim pstore dump)",
            file=sys.stderr,
        )
        sys.stdout.buffer.write(decode_console_ramoops(raw))
        return 1
    sys.stdout.buffer.write(log)
    sys.stdout.buffer.flush()
    return 0


def cmd_from_bugreport(args) -> int:
    with zipfile.ZipFile(args.zip) as zf:
        entry = find_bugreport_pstore(zf)
        if entry is None:
            print("error: no sys/fs/pstore/console-ramoops* entry in this zip", file=sys.stderr)
            return 1
        raw = zf.read(entry)
    log = extract_log(raw)
    if not log:
        print(f"error: marker not found in {entry}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(log)
    sys.stdout.buffer.flush()
    return 0


def cmd_run(args) -> int:
    image: pathlib.Path = args.image
    if not image.is_file():
        print(f"error: image not found: {image}", file=sys.stderr)
        return 2

    lk = subprocess.run([str(pathlib.Path(__file__).resolve().parent / "phone-lock"), "check"], capture_output=True, text=True)
    if lk.returncode != 0:
        print("REFUSING: the phone lock is not held by you: run under tools/phone-lock run --owner NAME -- ...", file=sys.stderr)
        return 2

    r = run_cmd(["fastboot", "getvar", "product"], FASTBOOT_TIMEOUT_S, "fastboot getvar product")
    if r is None:
        return 2
    product = parse_product((r.stdout + r.stderr).decode("utf-8", "replace"))
    if product != "bluejay":
        print(
            f"REFUSING: fastboot getvar product reported {product!r}, expected 'bluejay'.\n"
            "docs/SAFETY.md: never run adb/fastboot against another device.",
            file=sys.stderr,
        )
        return 2
    print(f"product ok: {product}")

    r = run_cmd(["fastboot", "boot", str(image)], FASTBOOT_BOOT_TIMEOUT_S, "fastboot boot")
    if r is None or b"OKAY" not in (r.stdout + r.stderr):
        out = (r.stdout + r.stderr).decode("utf-8", "replace") if r else ""
        print(f"error: fastboot boot failed:\n{out}", file=sys.stderr)
        return 1
    print("fastboot boot OKAY; waiting for Android to come up (shim resets itself)...")

    deadline = time.monotonic() + ADB_WAIT_TIMEOUT_S
    state = None
    warned_unauthorized = False
    while time.monotonic() < deadline:
        r = run_cmd(["adb", "devices"], ADB_CMD_TIMEOUT_S, "adb devices")
        if r is not None:
            state = adb_state((r.stdout + r.stderr).decode("utf-8", "replace"))
            if state == "device":
                break
            if state == "unauthorized" and not warned_unauthorized:
                warned_unauthorized = True
                print("phone shows 'unauthorized': accept the USB-debugging prompt on the screen")
            elif state is None:
                print("no adb device yet; still waiting...")
        time.sleep(ADB_POLL_S)
    if state != "device":
        print(
            f"error: no adb device in 'device' state after {ADB_WAIT_TIMEOUT_S}s "
            f"(last state: {state or 'none'})",
            file=sys.stderr,
        )
        return 1
    print("adb device online")

    raw = None
    for path in ("/sys/fs/pstore/console-ramoops-0", "/sys/fs/pstore/console-ramoops"):
        r = run_cmd(["adb", "shell", "cat", path], ADB_CMD_TIMEOUT_S, f"adb shell cat {path}")
        if r is None:
            return 1
        text = r.stdout.decode("utf-8", "replace")
        if r.returncode == 0 and text.strip():
            raw = r.stdout
            print(f"read {path} ({len(raw)} bytes)")
            break
        why = "permission denied" if "Permission denied" in text else "empty or missing"
        print(f"{path}: {why}")
    if raw is None:
        print("falling back to `adb bugreport` (this takes a minute)...")
        with tempfile.TemporaryDirectory() as td:
            zip_path = pathlib.Path(td) / "bugreport.zip"
            r = run_cmd(["adb", "bugreport", str(zip_path)], BUGREPORT_TIMEOUT_S, "adb bugreport")
            if r is None:
                return 1
            if not zip_path.is_file():
                print(f"error: bugreport zip not created: {r.stderr.decode('utf-8', 'replace')}", file=sys.stderr)
                return 1
            with zipfile.ZipFile(zip_path) as zf:
                entry = find_bugreport_pstore(zf)
                if entry is None:
                    print("error: no pstore console entry in the bugreport zip", file=sys.stderr)
                    return 1
                raw = zf.read(entry)
                print(f"read {entry} ({len(raw)} bytes) from bugreport")

    log = extract_log(raw)
    if not log:
        decoded = decode_console_ramoops(raw)
        print(
            "error: pstore was read, but the PIXELIOS PSTORE marker is not in it.\n"
            "Likely causes: ABL scrubbed the ramoops region on the shim->Android\n"
            "transition (the unknown this run tests), or a later reboot replaced it\n"
            "with the Android console log. First 400 bytes follow:\n"
            + decoded[:400].decode("utf-8", "replace"),
            file=sys.stderr,
        )
        return 1

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "logs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"probe-{stamp}.txt"
    out.write_bytes(log)
    print(f"log saved: {out} ({len(log)} bytes)")

    r = run_cmd(["adb", "reboot", "bootloader"], ADB_CMD_TIMEOUT_S, "adb reboot bootloader")
    if r is None:
        return 1
    deadline = time.monotonic() + REBOOT_BOOTLOADER_TIMEOUT_S
    while time.monotonic() < deadline:
        r = run_cmd(["fastboot", "devices"], FASTBOOT_TIMEOUT_S, "fastboot devices")
        if r is not None and r.stdout.strip():
            print("phone is back in fastboot")
            return 0
        time.sleep(ADB_POLL_S)
    print("warning: log saved, but the phone did not reappear in fastboot; "
          "use Power + Volume Down if needed", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=USAGE)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract", help="extract the shim log from a console-ramoops file")
    p.add_argument("file", type=pathlib.Path)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("from-bugreport", help="extract from an Android bugreport zip")
    p.add_argument("zip", type=pathlib.Path)
    p.set_defaults(func=cmd_from_bugreport)

    p = sub.add_parser("run", help="boot the shim, wait for Android, pull the log, return to fastboot")
    p.add_argument("image", type=pathlib.Path)
    p.set_defaults(func=cmd_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
