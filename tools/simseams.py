"""Simulation test seams, Python side (docs/plans/PLAN-SIM.md SIM-R2 / SIM-R5). stdlib only; imported by tools/phone, tools/phone-lock, tools/xrun_report.py.

Every seam: env override only, default behaviour unchanged, and a loud line on stderr when an override is active (rule 8):
    SIM SEAM ACTIVE: NAME=value          (once per process and name)
PIXOS_SLEEP_SCALE  decimal 0..1000 (default 1, at most 30 characters, ASCII digits only): multiplier for the POLL waits of phone and phone-lock
                   (poll(S) sleeps S*scale, never less than MIN_POLL: no busy spin at 0). Unset or empty = real time, `time.sleep(S)` exactly.
                   Anything else is SIM SEAM ERROR, exit 2 (a typo must not silently mean real time); phone and phone-lock check it at start.
                   The phone lock's own lease/grace timing is never scaled (review xrun-seams-r1 F3).
XRUN_FBOOT / XRUN_TO_FASTBOOT / XRUN_USB_PORT / XRUN_RUN_PREFLIGHT / XRUN_BUILD (tools/xrun): replace the real safety wrappers, so they run only in a
                   sandbox: PHONE_LOCK_DIR is set, is not the real lock and lies under $TMPDIR (`python3 -I tools/simseams.py require NAME...`, exit 2
                   "SIM SEAM REFUSED: NAME needs a sandbox"; review xrun-seams-r1 F1, AP-299).
XRUN_SYSFS_USB     USB sysfs root instead of /sys/bus/usb/devices (sysfs_usb()).
The shell twin is sim_sleep / sim_warn in tools/xrun; tools/test_sim_seams.sh checks both agree.
"""
import os
import re
import sys
import tempfile
import time

_warned = set()
DEFAULT_LOCK = os.path.expanduser("~/pixelios-artifacts/phone.lock")   # tools/phone-lock uses this one
MAX_SCALE = 1000.0
MAX_SCALE_CHARS = 30
MIN_POLL = 0.005   # floor of a scaled poll: scale 0 must not busy-spin (review xrun-seams-r1 F3)
_DEFAULT_SYSFS = "/sys/bus/usb/devices"


def warn(name):
    """Print the SIM SEAM ACTIVE line for env var NAME if it is set (once per process)."""
    v = os.environ.get(name)
    if v and name not in _warned:
        _warned.add(name)
        print("SIM SEAM ACTIVE: %s=%s" % (name, v), file=sys.stderr, flush=True)
    return v


def scale():
    v = warn("PIXOS_SLEEP_SCALE")
    if not v:
        return None
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?", v) or len(v) > MAX_SCALE_CHARS or float(v) > MAX_SCALE:   # [0-9]: ASCII digits only
        print("SIM SEAM ERROR: PIXOS_SLEEP_SCALE=%s is not a decimal number in 0..%d (at most %d characters)" % (v, MAX_SCALE, MAX_SCALE_CHARS), file=sys.stderr, flush=True)
        raise SystemExit(2)
    return float(v)


def poll(seconds, limit=None):
    """Sleep for one poll interval: real time when the seam is off; else seconds*scale, floored at MIN_POLL and (if given) capped at limit."""
    k = scale()
    if k is None:
        time.sleep(seconds)
    elif seconds > 0:
        time.sleep(min(max(k * seconds, MIN_POLL), max(limit, MIN_POLL)) if limit is not None else max(k * seconds, MIN_POLL))


def in_sandbox():
    """True when PHONE_LOCK_DIR is a test lock: set, not the real lock, under $TMPDIR (same predicate as tools/phone-lock adb_server)."""
    lock = os.environ.get("PHONE_LOCK_DIR")
    if not lock:
        return False
    real = os.path.realpath(lock)
    return real != os.path.realpath(DEFAULT_LOCK) and real.startswith(os.path.realpath(tempfile.gettempdir()).rstrip(os.sep) + os.sep)


def require_sandbox(names):
    """Exit 2 with one SIM SEAM REFUSED line per set variable in NAMES unless in_sandbox()."""
    bad = [n for n in names if os.environ.get(n)]
    if bad and not in_sandbox():
        for n in bad:
            print("SIM SEAM REFUSED: %s needs a sandbox (PHONE_LOCK_DIR under $TMPDIR and not the real lock; a real run holds the real lock, AP-299)" % n, file=sys.stderr, flush=True)
        raise SystemExit(2)


def sysfs_usb():
    return warn("XRUN_SYSFS_USB") or _DEFAULT_SYSFS


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "require":
        require_sandbox(sys.argv[2:])
    else:
        sys.exit("usage: simseams.py require NAME...")
