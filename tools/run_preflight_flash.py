#!/usr/bin/env python3
"""run_preflight_flash: module of tools/run-preflight: the `FLASH: last=<UTC> reason=<TEXT> today=<N>` line printed by --userdata-check (R54, rule 8),
moved out so run-preflight does not grow (R29). Reads flash-reasons.tsv (tools/fflash-userdata.sh writes UTC<TAB>sha256<TAB>reason per flash).

  tools/run_preflight_flash.py --self-test    inline checks (also run by tools/test_run_preflight_flash.py)
Last line: RESULT: PASS|FAIL run_preflight_flash ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import datetime, os, sys, tempfile


def flash_reasons_line(path=None):
    """-> the FLASH line; `FLASH: none recorded` when the file is missing or has no row; an unreadable file prints a FLASH GAP line on stderr."""
    path = path or os.environ.get("FLASH_REASONS") or os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "flash-reasons.tsv")
    try:
        rows = [l.rstrip("\n").split("\t") for l in open(path, errors="replace") if l.strip()]
    except FileNotFoundError:   # lint-silent: expected - no flash was ever recorded; printed as `FLASH: none recorded`
        rows = []
    except OSError as e:
        print("FLASH GAP: cannot read %s (%s): flashes_today unknown" % (path, e), file=sys.stderr)
        rows = []
    if not rows:
        return "FLASH: none recorded"
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return "FLASH: last=%s reason=%s today=%d" % (rows[-1][0], rows[-1][2] if len(rows[-1]) > 2 else "?", sum(1 for r in rows if r[0][:10] == today))


def userdata(check):
    """Run check() -> (status, detail) and print the FLASH line before the caller prints its RESULT line (tools/xrun reads the last line)."""
    st, d = check()
    print(flash_reasons_line())
    return st, d


def self_test():
    fails = []
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "r.tsv")
        if flash_reasons_line(p) != "FLASH: none recorded":
            fails.append("missing file")
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        with open(p, "w") as f:
            f.write("2020-01-01T00:00:00Z\t%s\told\n%sT01:02:03Z\t%s\tfirst\n%sT04:05:06Z\t%s\troot image rebuilt\n" % ("a" * 64, today, "b" * 64, today, "c" * 64))
        if flash_reasons_line(p) != "FLASH: last=%sT04:05:06Z reason=root image rebuilt today=2" % today:
            fails.append("rows: " + flash_reasons_line(p))
        if flash_reasons_line(t) != "FLASH: none recorded":   # a directory: OSError path (GAP on stderr)
            fails.append("unreadable")
    for f in fails:
        print("FAIL run_preflight_flash %s" % f)
    print("RESULT: %s run_preflight_flash self-test" % ("FAIL" if fails else "PASS"))
    return 1 if fails else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL run_preflight_flash is a module of tools/run-preflight; use --self-test")   # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
