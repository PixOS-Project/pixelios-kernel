#!/usr/bin/env python3
"""Host tests for xnu/patches/0341-sweet-proccpu-top.patch (SWEET CPUTOP: top processes + kernel threads by CPU time per period; READS ONLY). No phone, no kernel build.

    python3 tools/test_cputop.py  -> `RESULT: PASS cputop cases=N patch_sha=<sha256>` / `RESULT: FAIL ...`

Proves: the patch's new file bsd/dev/sweet_cputop.c extracts with `git apply`; it builds -Wall -Wextra -Werror in the host model (tools/cputop_host_test.c: a fake world of
processes and kernel threads behind proc_iterate / task_info / sweet_cputop_task); the cases: first sample = baseline line only; ranking by the CPU time delta over
processes and kernel threads together, exact header and row text, at most 8 rows, user + system summed; idle threads and pid 0 never ranked (idle_ms); entries without
CPU time are not rows; new / gone / late / reused-pid accounting; task_info failure, table caps and a failed thread walk are counted on every header and named once on a
SWEET CPUTOP GAP line; a changed walk order is not new/gone; the line cap. Mutants of the source fail at least one case. Text checks: boot-arg sweetcputop parsed by its
literal name and listed in tools/bootargs-known.txt; the patch adds no MMIO or register access; the start call, the files row and the thread names are in the patch; the
header fields documented in the file are the ones printed.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(ROOT, "xnu", "patches", "0341-sweet-proccpu-top.patch")
MODEL = os.path.join(ROOT, "tools", "cputop_host_test.c")
SRC_REL = "bsd/dev/sweet_cputop.c"
CASES = []

# (name, old, new): `old` exactly once in sweet_cputop.c as patched; the mutated file must fail a case (or not build)
MUTANTS = (
    ("rank-order-reversed", "while (at > 0 && top[at - 1].d < d) {", "while (at > 0 && top[at - 1].d > d) {"),
    ("idle-thread-ranked", "\t\tif (c->kind == 'i') {\n\t\t\tidle += d;\n\t\t\tcontinue;\n\t\t}\n", ""),
    ("pid0-ranked", "\tif (pid == 0) {\n\t\treturn PROC_RETURNED;      /* kernel_task: sampled per thread by ct_kernel */\n\t}\n", ""),
    ("late-not-dropped", "\t\t\tif (d > dt_us * CT_NCPU) {\n\t\t\t\tlate++;\n\t\t\t\td = 0;\n\t\t\t}\n", ""),
    ("system-time-dropped", "e->us = ct_abs_us(ti.total_user + ti.total_system);", "e->us = ct_abs_us(ti.total_user);"),
    ("reused-pid-underflow", "if (q != NULL && c->us >= q->us) {", "if (q != NULL) {"),
    ("proc-cap-off", "\tif (t->np >= CT_MAXP) {\n\t\tt->trunc++;\n\t\treturn PROC_RETURNED;\n\t}\n", "\tif (t->np >= CT_MAXP) {\n\t\treturn PROC_RETURNED;\n\t}\n"),
    ("fail-silent", "\tif (cur->fail && !fail_said) {", "\tif (0 && cur->fail && !fail_said) {"),
    ("kerr-silent", "\tif (cur->kerr && !kerr_said) {", "\tif (0 && cur->kerr && !kerr_said) {"),
    ("gone-not-counted", "\t\tif (!prev->e[j].seen) {\n\t\t\tgone++;\n\t\t}\n", ""),
    ("cap-ignored", "\tif (ct_lines >= CT_CAP) {\n\t\treturn;\n\t}\n\tprintf(\"SWEET CPUTOP: t_ms", "\tprintf(\"SWEET CPUTOP: t_ms"),
    ("zero-rows-printed", "\t\tif (d == 0) {\n\t\t\tcontinue;\n\t\t}\n", ""),
    ("kind-not-compared", "if (q->key == c->key && q->kind == c->kind) {", "if (q->key == c->key) {"),
)


def run(*a, **k):
    return subprocess.run(a, capture_output=True, text=True, **k)


def rd(path):
    with open(path) as f:
        return f.read()


def wr(path, text):
    with open(path, "w") as f:
        f.write(text)


def added(text, path=None):
    """Added lines of the patch (optionally of one file), without the leading +."""
    out, cur = [], None
    for ln in text.split("\n"):
        if ln.startswith("+++ "):
            cur = ln[6:] if ln.startswith("+++ b/") else None
        elif ln.startswith("+") and (path is None or cur == path):
            out.append(ln[1:])
    return "\n".join(out) + "\n"


def code_of(text):
    """C text without comments and string literals."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cputop-")
        tree = os.path.join(cls.tmp, "tree")
        os.makedirs(tree)
        run("git", "-C", tree, "init", "-q")
        cls.apply = run("git", "-C", tree, "apply", "--include=" + SRC_REL, PATCH)
        cls.src = os.path.join(tree, SRC_REL)
        cls.patch = rd(PATCH)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build(self, src=None, out="cputop", extra=()):
        exe = os.path.join(self.tmp, out)
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", "-DSWEET_CPUTOP_HOST", '-DCPUTOP_SRC="%s"' % (src or self.src), *extra, MODEL, "-o", exe)
        return r, exe

    def results(self, src=None, tag=""):
        """-> ({case: 'ok' | 'FAIL ..'}, text) or (None, compiler stderr). The cap case runs in the small-cap build."""
        r, exe = self.build(src, "cputop" + tag)
        if r.returncode != 0:
            return None, r.stderr
        r2, exe2 = self.build(src, "cputop-cap" + tag, ("-DCT_CAP=10", "-DCPUTOP_SMALL_CAP"))
        if r2.returncode != 0:
            return None, r2.stderr
        res, text = {}, ""
        for name in run(exe, "--list").stdout.split():
            try:
                o = run(exe2 if name == "cap" else exe, name, timeout=20)
            except subprocess.TimeoutExpired:
                res[name] = "FAIL timeout"
                continue
            text += o.stdout
            m = re.search(r"^CASE %s (ok|FAIL.*)$" % re.escape(name), o.stdout, re.M)
            res[name] = m.group(1) if m else "FAIL no CASE line (rc %d)" % o.returncode
        return res, text


class Flow(Base):
    def test_apply_and_cases(self):
        self.assertEqual(self.apply.returncode, 0, self.apply.stderr)
        self.assertTrue(os.path.isfile(self.src))
        res, text = self.results()
        self.assertIsNotNone(res, text)
        self.assertEqual(len(res), 10, text[-2000:])
        self.assertEqual({k: v for k, v in res.items() if v != "ok"}, {}, text[-4000:])
        CASES.extend("flow:" + k for k in res)

    def test_mutants_fail(self):
        base = rd(self.src)
        for i, (name, old, new) in enumerate(MUTANTS):
            with self.subTest(mutant=name):
                self.assertEqual(base.count(old), 1, "mutant %s: needle is not exactly once in %s" % (name, SRC_REL))
                p = os.path.join(self.tmp, "mut%d.c" % i)
                wr(p, base.replace(old, new))
                res, text = self.results(p, "-m%d" % i)
                if res is None:
                    self.fail("mutant %s does not build (a mutant must be caught by a case, not by the compiler): %s" % (name, text[-600:]))
                self.assertTrue([k for k, v in res.items() if v != "ok"], "mutant %s passes every case" % name)
                CASES.append("mutant:" + name)


class Text(Base):
    def test_bootarg(self):
        src = rd(self.src)
        self.assertIn('PE_parse_boot_argn("sweetcputop", &per, sizeof(per))', src)
        self.assertIn('PE_parse_boot_argn("sweetperf", &perf, sizeof(perf))', src)
        self.assertRegex(rd(os.path.join(ROOT, "tools", "bootargs-known.txt")), r"(?m)^sweetcputop\tpatch:0341$")
        CASES.append("text:bootarg")

    def test_reads_only(self):
        """No MMIO mapping, no volatile store, no register write helper, in any line the patch adds."""
        code = code_of(added(self.patch))
        for bad in ("ml_io_map", "volatile", "IOMemoryDescriptor", "wr(", "writel", "usb_wr", "i2c_", "smc_call", "ml_set_interrupts_enabled"):
            self.assertNotIn(bad, code, "the patch adds `%s`" % bad)
        CASES.append("text:reads-only")

    def test_wiring(self):
        p = self.patch
        self.assertIn("+bsd/dev/sweet_cputop.c\t\t\tstandard\n", p)
        self.assertIn("+\tsweet_cputop_start();", added(p, "bsd/dev/sweet_ufs.c").replace("\n", "\n+"))
        self.assertIn("sweet_cputop_task(task_t task, uint64_t *out, char *names, int namesz, int max, int *total)\n{", added(p, "osfmk/kern/exception.c"))
        names = re.findall(r'thread_set_thread_name\([^,]+, "(\w+)"\)', added(p))
        self.assertEqual(sorted(names), ["sweet_cputop", "sweet_fb_vsync", "sweet_mali", "sweet_touch", "sweet_usb", "sweet_usb_log"])
        for n in names:
            self.assertLess(len(n), 28, "thread name %s does not fit CT_NAME" % n)
        # the model's kernel-walk signature is the one the kernel file declares
        self.assertIn("sweet_cputop_task(task_t task, uint64_t *out, char *names, int namesz, int max, int *total)", rd(MODEL))
        CASES.append("text:wiring")

    def test_header_doc_matches_format(self):
        src = rd(self.src)
        doc = re.search(r"SWEET CPUTOP: t_ms T dt_ms D ([^\n]+)\n", src).group(1)
        fmt = re.search(r'printf\("SWEET CPUTOP: t_ms %llu dt_ms %llu ([^"]+)\\n"', src).group(1)
        self.assertEqual(re.findall(r"[a-z_]+", doc), re.findall(r"[a-z_]+", re.sub(r"%\w+", "", fmt)))
        CASES.append("text:header-doc")

    def test_proccpu_pid_change_lines(self):
        a = added(self.patch, "bsd/dev/sweet_mali.c")
        for who in ("backboardd", "SpringBoard"):
            self.assertIn('printf("SWEET PROCCPU: %s not found\\n");' % who, a)
            self.assertIn('printf("SWEET PROCCPU: %s pid %%d (was %%d)\\n"' % who, a)
        # the per-second data line is untouched (tools/frame-split reads it)
        self.assertNotIn("bb_us %llu sb_pid", "\n".join(l for l in self.patch.split("\n") if l.startswith("-") or l.startswith("+")))
        CASES.append("text:proccpu")


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=1)
    ok = prog.result.wasSuccessful()
    with open(PATCH, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    print("RESULT: %s cputop cases=%d patch_sha=%s" % ("PASS" if ok else "FAIL", len(CASES), sha))
    sys.exit(0 if ok else 1)
