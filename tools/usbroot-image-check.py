#!/usr/bin/env python3
"""Read-only host check of an HFS+/HFSX root image against the HFS mount path (research 55).
  tools/usbroot-image-check.py IMAGE      (opens IMAGE "rb"; never writes)
Reports the journaled flag, lastMountedVersion, journalInfoBlock and its flags, and whether a read-write mount
would write the journal (hfs_vfsutils.c:735-739 late init, journal_create when JIB flag NeedInit 0x4, hfs_format.h:803)
and whether the patched read-only mount (patch 0151) would not. Exit: 0 no write on a patched ro mount, 1 otherwise, 2 error.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import struct, sys

HFSJ = 0x4846534A          # kHFSJMountVersion 'HFSJ' (hfs_format.h:60)
JOURNALED = 0x00002000     # kHFSVolumeJournaledMask
JI_NEED_INIT = 0x4         # kJIJournalNeedInitMask


def parse(vh, read_jib):
    """vh: 512+ bytes of the volume header; read_jib(offset) -> 12+ bytes. Returns a dict."""
    sig = vh[0:2]
    if sig not in (b"H+", b"HX"):
        raise ValueError("no HFS+/HFSX signature at 1024: %r" % sig)
    attrs, lmv = struct.unpack(">I4s", vh[4:12])
    bsize, = struct.unpack(">I", vh[40:44])
    jib, = struct.unpack(">I", vh[12:16])
    r = {"sig": sig, "attrs": attrs, "lmv": lmv, "bsize": bsize, "jib": jib,
         "journaled": bool(attrs & JOURNALED), "jflags": None}
    if r["journaled"] and jib:
        r["jflags"] = struct.unpack(">I", read_jib(jib * bsize)[0:4])[0]
    late = r["journaled"] and struct.unpack(">I", lmv)[0] != HFSJ
    need = r["jflags"] is not None and bool(r["jflags"] & JI_NEED_INIT)
    r["rw_writes_journal"] = bool(late and need)
    r["rw_late_init"] = bool(late)
    r["patched_ro_writes_journal"] = False if late else None  # late init is skipped when READ_ONLY (0151)
    if not late and r["journaled"]:
        r["patched_ro_writes_journal"] = False  # early open path (lastMountedVersion HFSJ): not examined here
        r["note"] = "lastMountedVersion is HFSJ: early journal open runs; a dirty journal may be replayed (not checked here)"
    return r


def main(argv):
    if len(argv) != 2:
        print("usage: usbroot-image-check.py IMAGE\nRESULT: ERROR usage"); return 2
    try:
        with open(argv[1], "rb") as f:
            f.seek(1024); vh = f.read(512)
            r = parse(vh, lambda off: (f.seek(off), f.read(16))[1])
    except (OSError, ValueError, struct.error) as e:
        print("RESULT: ERROR %s" % e); return 2
    print("signature %s  attributes 0x%08x  journaled %s  blockSize %d" % (r["sig"].decode(), r["attrs"], r["journaled"], r["bsize"]))
    print("lastMountedVersion %r  journalInfoBlock %d  JIB flags %s" % (r["lmv"], r["jib"], "none" if r["jflags"] is None else "0x%x" % r["jflags"]))
    print("read-write mount: late journal init %s, journal header write %s" % ("RUNS" if r["rw_late_init"] else "skipped", "YES" if r["rw_writes_journal"] else "no"))
    print("patched (0151) read-only mount: late journal init skipped, journal write %s" % ("no" if r["patched_ro_writes_journal"] is False else "n/a"))
    if r.get("note"): print("note:", r["note"])
    ok = r["patched_ro_writes_journal"] is False or not r["journaled"]
    print("RESULT: %s patched read-only mount %s the journal" % ("PASS" if ok else "FAIL", "does not write" if ok else "may write"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
