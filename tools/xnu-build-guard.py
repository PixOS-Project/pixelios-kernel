#!/usr/bin/env python3
# Stale-object guard for tools/xnu-local-build: proves that every object linked into the kernel was compiled from the sources
# that are in the work tree NOW. One report ending RESULT: PASS|FAIL|ERROR.
#   tools/xnu-build-guard.py --cfg CFGDIR --tree W --stamp FILE --manifest FILE --kernel K --installed K2 [--root DIR ...]
#   --cfg DIR        the kernel config object dir (e.g. W/build/xnu.obj/DEVELOPMENT_ARM64_SWEET)
#   --tree DIR       the work tree (W); a dependency under another $LOCALBUILD/work/<label> is a foreign tree
#   --stamp FILE     a file touched right before `make install`: an object newer than it was compiled by this run
#   --manifest FILE  JSON {object: {dependency: sha256}} written by the last PASS of this tree (read, then rewritten on PASS)
#   --kernel FILE    the linked kernel in the object dir (kernel.development.sweet.unstripped)
#   --installed FILE the installed kernel (fakeroot); must equal CFGDIR/kernel.development.sweet byte for byte
#   --root DIR       a directory dependencies may live in (repeatable: the tree, the toolchain/SDK/dependency dirs)
#   --record-only    after a FAILED make: record the objects this run compiled (their dependency hashes now) into the manifest, keep the other entries, no verdict;
#                    the next build can then verify them (else a failed build, a fix and an incremental build fail F3 on every object the failed run compiled)
#   --self-test      fixture checks (clean, verified rerun, stale header, foreign tree, kernel not relinked); run by tools/test_xnu_build_guard.py
# What it checks (each failure is listed, up to 12 lines, and the run FAILs):
#   F1 every linked object (all CFGDIR/*/*/*.filelist entries + the CFGDIR/*.o link inputs) has its compiler .d file;
#   F2 every dependency in it exists and lies under a --root, and none under another work tree (wlan-wui-f, 2026-11-08: a work tree
#      copied from label T-857c kept SOURCE_DIR and every .d pointing at T-857c/xnu, so make compiled that tree's old sources);
#   F3 an object this run did NOT compile is accepted only when the manifest of the last PASS lists it with exactly the current
#      content hash of every dependency (content, not mtime: a header changed under an old mtime, a moved tree, a missed make
#      dependency all show up as a hash mismatch or a missing entry);
#   F4 the linked kernel is newer than every linked object, and the installed kernel equals the linked one.
# This is per object, not a marker: one fresh object next to stale ones fails on the stale ones.
# Exit: 0 PASS, 1 FAIL, 2 ERROR.
import sys
if sys.argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + "".join(l[2:] for l in open(__file__).readlines()[1:25] if l.startswith("# ")).strip()); raise SystemExit(0)
import argparse, glob, hashlib, json, os


def _deps_of(dfile, odir, real):
    # first rule of a clang -MD file: "target: dep dep \" ... ; the -MP phony rules after it are skipped
    out, txt = [], open(dfile, errors="replace").read().replace("\\\n", " ")
    first = txt.split("\n", 1)[0]
    if ":" not in first: return None
    for t in first.split(":", 1)[1].split():
        out.append(real(t if t.startswith("/") else os.path.join(odir, t)))
    return out


def _record_only(a, objs, old, since, tree, workroot, roots, deps_of, h):
    """--record-only: record the objects this (failed) make compiled; no verdict."""
    rec = dict(old)
    n = 0
    for o in objs:
        try: st = os.stat(o)
        except OSError as e:
            print("xnu-build-guard GAP: --record-only: %s not readable (%s), not recorded" % (o, e), file=sys.stderr); continue
        d = o[:-1] + "d" if o.endswith("o") else None
        deps = deps_of(d, os.path.dirname(o)) if d and os.path.isfile(d) else None
        if not deps or st.st_mtime_ns <= since: continue
        cur = {p: h(p) for p in deps}
        if any(v is None for v in cur.values()) or any((p.startswith(workroot) and not p.startswith(tree + "/")) or not any(p.startswith(r) for r in roots) for p in cur):
            rec.pop(o, None); continue
        rec[o] = cur; n += 1
    tmp = a.manifest + ".tmp"
    with open(tmp, "w") as f: json.dump(rec, f)
    os.replace(tmp, a.manifest)
    print(f"XNU BUILD GUARD: RECORD {n} object(s) compiled by this (failed) make recorded, {len(rec)} in the manifest, no verdict")
    print(f"RESULT: PASS xnu-build-guard record-only recorded={n}")
    return 0


def run(argv):

    ap = argparse.ArgumentParser(add_help=False)
    for o in ("--cfg", "--tree", "--stamp", "--manifest", "--kernel", "--installed"):
        ap.add_argument(o, required=True)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--min-objects", type=int, default=100)
    ap.add_argument("--record-only", action="store_true")   # fewer linked objects = the filelists are missing (the self-test fixture lowers it)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        print("RESULT: ERROR xnu-build-guard: bad arguments (see -h)"); return 2

    _rc = {}
    def real(p):
        r = _rc.get(p)
        if r is None: r = _rc[p] = os.path.realpath(p)
        return r
    cfg, tree = real(a.cfg), real(a.tree)
    roots = [real(r).rstrip("/") + "/" for r in a.root + [a.tree]]
    workroot = os.path.dirname(tree).rstrip("/") + "/"   # $LOCALBUILD/work/
    if not os.path.isdir(cfg): print(f"RESULT: ERROR xnu-build-guard: no config dir {cfg}"); return 2
    try: since = os.stat(a.stamp).st_mtime_ns
    except OSError: print(f"RESULT: ERROR xnu-build-guard: no stamp file {a.stamp}"); return 2

    objs = []
    for fl in sorted(glob.glob(os.path.join(cfg, "*", "*", "*.filelist"))):
        with open(fl) as f: objs += [l.strip() for l in f if l.strip()]
    objs += sorted(glob.glob(os.path.join(cfg, "*.o")))
    objs = sorted(set(real(o) for o in objs))
    if len(objs) < a.min_objects: print(f"RESULT: ERROR xnu-build-guard: only {len(objs)} linked objects found under {cfg} (filelists missing?)"); return 2

    def deps_of(dfile, odir): return _deps_of(dfile, odir, real)

    hcache = {}
    def h(p):
        if p not in hcache:
            try:
                with open(p, "rb") as f: hcache[p] = hashlib.sha256(f.read()).hexdigest()
            except OSError: hcache[p] = None   # lint-silent: expected a dependency that is gone/unreadable hashes as None, so the object counts as changed and is rebuilt
        return hcache[p]

    try:
        with open(a.manifest) as f: old = json.load(f)
    except (OSError, ValueError): old = {}   # lint-silent: expected no/garbled previous manifest on the first build: every object is then treated as new

    if a.record_only:
        return _record_only(a, objs, old, since, tree, workroot, roots, deps_of, h)
    fails, new, rebuilt, kept, newest = [], {}, 0, 0, 0
    for o in objs:
        try: st = os.stat(o)
        except OSError: fails.append(f"F1 linked object missing: {o}"); continue
        newest = max(newest, st.st_mtime_ns)
        d = o[:-1] + "d" if o.endswith("o") else None
        deps = deps_of(d, os.path.dirname(o)) if d and os.path.isfile(d) else None
        if not deps: fails.append(f"F1 no dependency file for {o}"); continue
        cur, bad = {}, False
        for p in deps:
            if p.startswith(workroot) and not p.startswith(tree + "/"):
                fails.append(f"F2 {os.path.basename(o)} depends on another work tree: {p}"); bad = True; break
            if not any(p.startswith(r) for r in roots):
                fails.append(f"F2 {os.path.basename(o)} depends on a file outside the build roots: {p}"); bad = True; break
            hv = h(p)
            if hv is None: fails.append(f"F2 {os.path.basename(o)} depends on a missing file: {p}"); bad = True; break
            cur[p] = hv
        if bad: continue
        if st.st_mtime_ns > since: rebuilt += 1
        else:
            prev = old.get(o)
            if prev is None: fails.append(f"F3 {o} was not compiled by this run and no earlier verified build lists it (cannot prove it is current)"); continue
            diff = sorted(p for p in set(cur) | set(prev) if cur.get(p) != prev.get(p))
            if diff: fails.append(f"F3 STALE {o}: not recompiled, but {len(diff)} of its sources changed since it was verified, e.g. {diff[0]}"); continue
            kept += 1
        new[o] = cur

    try:
        kst = os.stat(a.kernel).st_mtime_ns
        if kst < newest: fails.append(f"F4 the linked kernel {a.kernel} is older than a linked object (not relinked)")
    except OSError: fails.append(f"F4 no linked kernel {a.kernel}")
    lk = os.path.join(cfg, "kernel.development.sweet")
    if os.path.isfile(lk) and h(real(lk)) != h(real(a.installed)):
        fails.append(f"F4 the installed kernel {a.installed} differs from the built one {lk}")

    summary = f"objects={len(objs)} compiled_this_run={rebuilt} verified_unchanged={kept} dependencies_hashed={len(hcache)}"
    if fails:
        print(f"XNU BUILD GUARD: FAIL {len(fails)} problem(s), {summary}")
        for l in fails[:12]: print("  " + l)
        if len(fails) > 12: print(f"  ... {len(fails) - 12} more")
        print(f"RESULT: FAIL xnu-build-guard: {len(fails)} linked object(s) not proven current ({fails[0][:160]})"); return 1
    tmp = a.manifest + ".tmp"
    with open(tmp, "w") as f: json.dump(new, f)
    os.replace(tmp, a.manifest)
    print(f"XNU BUILD GUARD: PASS {summary}")
    print(f"RESULT: PASS xnu-build-guard {summary}")
    return 0


def self_test():
    import contextlib, io, tempfile, time
    t = tempfile.mkdtemp(prefix="xbg."); work = os.path.join(t, "work"); W = os.path.join(work, "lab"); O = os.path.join(work, "other")
    cfg = os.path.join(W, "build", "xnu.obj", "CFG"); comp = os.path.join(cfg, "bsd", "DEV"); src = os.path.join(W, "xnu")
    for d in (comp, src, os.path.join(O, "xnu"), os.path.join(W, "fakeroot")): os.makedirs(d, exist_ok=True)
    def wr(p, s):
        with open(p, "w") as f: f.write(s)
    wr(os.path.join(src, "hdr.h"), "int v = 1;\n"); wr(os.path.join(O, "xnu", "hdr.h"), "int v = 0;\n")
    objs = []
    for i in range(12):
        c = os.path.join(src, "f%d.c" % i); wr(c, "#include \"hdr.h\"\n"); o = os.path.join(comp, "f%d.o" % i); objs.append(o)
    stamp, man = os.path.join(W, ".make-stamp"), os.path.join(W, ".guard-manifest.json")
    kern, inst = os.path.join(cfg, "k.unstripped"), os.path.join(W, "fakeroot", "kernel")
    def build(hdr_root=src):   # "make": stamp, then objects + .d, then the kernel
        wr(stamp, ""); time.sleep(0.02)
        for i, o in enumerate(objs):
            wr(o, "obj"); wr(o[:-1] + "d", "%s: %s \\\n  %s\n\n%s:\n" % (os.path.basename(o), os.path.join(src, "f%d.c" % i), os.path.join(hdr_root, "hdr.h"), os.path.join(hdr_root, "hdr.h")))
        wr(os.path.join(comp, "bsd.filelist"), "".join(o + "\n" for o in objs)); time.sleep(0.02); wr(kern, "K"); wr(inst, "K"); wr(os.path.join(cfg, "kernel.development.sweet"), "K")
    def guard():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run(["--cfg", cfg, "--tree", W, "--stamp", stamp, "--manifest", man, "--kernel", kern, "--installed", inst, "--root", W, "--min-objects", "10"])
        return rc, buf.getvalue()
    checks = []
    def chk(name, cond, outp): checks.append((name, cond)); print(("ok   " if cond else "FAIL ") + name + ("" if cond else "\n" + outp))
    build(); rc, o = guard(); chk("clean build: PASS, all compiled this run", rc == 0 and "compiled_this_run=12" in o, o)
    time.sleep(0.02); wr(stamp, ""); rc, o = guard(); chk("no-op rerun: PASS, all verified unchanged by hash", rc == 0 and "verified_unchanged=12" in o, o)
    wr(os.path.join(src, "hdr.h"), "int v = 2;\n"); time.sleep(0.02); wr(stamp, ""); rc, o = guard()
    chk("header changed, objects not recompiled (the wui-f symptom): FAIL F3 STALE", rc == 1 and "F3 STALE" in o, o)
    build(hdr_root=os.path.join(O, "xnu")); rc, o = guard(); chk("dependency in another work tree (copied tree): FAIL F2", rc == 1 and "another work tree" in o, o)
    os.remove(man); build(); time.sleep(0.02); wr(stamp, ""); rc, o = guard(); chk("no manifest + nothing compiled: FAIL F3 cannot prove", rc == 1 and "cannot prove" in o, o)
    build(); os.utime(kern, ns=(1, 1)); rc, o = guard(); chk("kernel not relinked after the objects: FAIL F4", rc == 1 and "F4" in o, o)
    build(); wr(inst, "OLD"); rc, o = guard(); chk("installed kernel differs from the built one: FAIL F4", rc == 1 and "installed kernel" in o, o)
    os.path.exists(man) and os.remove(man); build()           # a FAILED make compiled every object: record-only, then a no-op rebuild must verify them
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rr = run(["--cfg", cfg, "--tree", W, "--stamp", stamp, "--manifest", man, "--kernel", kern, "--installed", inst, "--root", W, "--min-objects", "10", "--record-only"])
    time.sleep(0.02); wr(stamp, ""); rc, o = guard(); chk("failed make + --record-only, then a no-op build: PASS verified", rr == 0 and rc == 0 and "verified_unchanged=12" in o, o + buf.getvalue())
    import shutil; shutil.rmtree(t, ignore_errors=True)
    bad = [n for n, c in checks if not c]
    print("RESULT: %s xnu-build-guard self-test cases=%d failed=%d" % ("FAIL" if bad else "PASS", len(checks), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(self_test() if sys.argv[1:2] == ["--self-test"] else run(sys.argv[1:]))
