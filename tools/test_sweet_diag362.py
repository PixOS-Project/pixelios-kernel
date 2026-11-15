"""Patch 0362 (lowclk diagnostics: exit log for many-times-exec'd names, UFS read/write split, VM page counters): host test. Extracts the pure table logic
(sel_exec / sel_exit) of the new bsd/dev/sweet_exitlog.c from xnu/patches/0362-sweet-diag-exit-ufs-vm.patch, compiles it as C and checks the thresholds (more than 10
execs, first 5 exits then every 100th, table full), then text checks of every hook in the patch (exec site, exit site before proc_prepareexit, conf/files, the old
CPUSTAT KiB field kept + the 4 new fields, the UFS direction split, the 6 VM counters every second tick) with a mutant per check. `RESULT: PASS 0362-diag cases=N mutants=M`.
Not a hardware or kernel-build test."""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0362-sweet-diag-exit-ufs-vm.patch")

HARNESS = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stddef.h>
#define MAXCOMLEN 16
%s
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %%d: %%s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
	static struct sel_tab t;
	uint32_t ex, xi;
	/* exec counting: the 11th exec of a name is the first "more than 10" */
	memset(&t, 0, sizeof t);
	for (int i = 1; i <= 10; i++) CHECK(sel_exec(&t, "lskdd") == SEL_NO);
	CHECK(sel_exec(&t, "lskdd") == SEL_TRACK_NEW);
	CHECK(sel_exec(&t, "lskdd") == SEL_NO && sel_exec(&t, "lskdd") == SEL_NO);
	/* exits of lskdd (13 execs): first 5 logged, 6..99 not, 100 and 200 logged, 101 not */
	for (uint32_t i = 1; i <= 250; i++) {
		bool want = i <= 5 || i == 100 || i == 200;
		CHECK(sel_exit(&t, "lskdd", &ex, &xi) == want);
		CHECK(ex == 13 && xi == i);
	}
	/* a name exec'd 10 times (not more) never logs, a name never exec'd never logs and is not added */
	for (int i = 0; i < 10; i++) sel_exec(&t, "ten");
	for (int i = 0; i < 20; i++) CHECK(!sel_exit(&t, "ten", &ex, &xi));
	CHECK(!sel_exit(&t, "ghost", &ex, &xi) && t.n == 2);
	/* names are matched whole: "lskdd2" is not "lskdd" */
	for (int i = 0; i < 12; i++) sel_exec(&t, "lskdd2");
	CHECK(sel_exit(&t, "lskdd2", &ex, &xi) && ex == 12 && xi == 1);
	/* table full: the 49th name is SEL_FULL and counted, known names still work */
	memset(&t, 0, sizeof t);
	for (int i = 0; i < SEL_NAMES; i++) { char n[16]; snprintf(n, sizeof n, "p%%d", i); CHECK(sel_exec(&t, n) == SEL_NO); }
	CHECK(sel_exec(&t, "extra") == SEL_FULL && t.dropped == 1 && t.n == SEL_NAMES);
	CHECK(sel_exec(&t, "p0") == SEL_NO && t.dropped == 1);
	/* a 31-char name (p_name is 2*MAXCOMLEN+1) is truncated safely and matched again */
	memset(&t, 0, sizeof t);
	{ const char *ln = "0123456789abcdef0123456789abcde"; for (int i = 0; i < 11; i++) sel_exec(&t, ln); CHECK(sel_exit(&t, ln, &ex, &xi) && ex == 11); }
	/* signed exit code: W_EXITCODE(ret, sig) = (ret << 8) | sig; a negative main() return must stay negative (observed xstat 0xff595c00) */
	CHECK(sel_code((int)0xff595c00u) == -42660);
	CHECK(sel_code(0) == 0 && sel_code(92 << 8) == 92 && sel_code(1 << 8) == 1 && sel_code(255 << 8) == 255);
	CHECK(sel_code(-1 << 8) == -1 && sel_code((-1 << 8) | 9) == -1 && sel_code(9) == 0);
	puts("ok");
	return 0;
}
'''


def new_file_body(text, path):
    """added lines of one new file in the patch"""
    out, on = [], False
    for l in text.splitlines():
        if l.startswith("+++ b/"):
            on = l[6:] == path
            continue
        if l.startswith("--- "):
            continue
        if on and l.startswith("+"):
            out.append(l[1:])
    return "\n".join(out)


def body(text):
    out, on = [], False
    for l in text.splitlines():
        if l.startswith("--- a/") or l.startswith("--- /dev/null"):
            on = True
        if on and l[:1] in ("+", " ") and not l.startswith("+++"):
            out.append(l[1:])
    return "\n".join(out)


def extract(src):
    m = re.search(r"#define SEL_NAMES.*?\nsel_exit\(.*?\n}\n", src, re.S)
    if not m:
        raise SystemExit("RESULT: FAIL 0362-diag: sel_exec/sel_exit not found in the patch")
    return m.group(0)


def run(func):
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "t.c")
        exe = os.path.join(d, "t")
        with open(c, "w") as f:
            f.write(HARNESS % func)
        r = subprocess.run(["cc", "-Wall", "-Werror", "-D_DEFAULT_SOURCE", "-o", exe, c], capture_output=True, text=True)
        if r.returncode:
            return 2, r.stderr
        r = subprocess.run([exe], capture_output=True, text=True)
        return r.returncode, r.stderr + r.stdout


TEXT = [
    ("exec site calls the counter", r"sweet_fb_swap_count\);\s*\{[^\n]*patch 0362[^\n]*\s*extern void sweet_exitlog_exec\(const char \*\);\s*sweet_exitlog_exec\(p->p_name\);"),
    ("exit site before proc_prepareexit", r"sweet_exitlog_exit\(p, rv, jetsam_flags\);\s*\}\s*#endif\s*proc_prepareexit\(p, rv, perf_notify\);"),
    ("exit site after the exit reason is stored", r"proc_transend\(p, 1\);\s*proc_unlock\(p\);\s*#if defined\(ARM64_BOARD_CONFIG_SWEET\)\s*\{[^\n]*patch 0362"),
    ("source in conf/files", r"bsd/dev/sweet_exitlog\.c\s+standard"),
    ("threshold more than 10", r"#define SEL_MIN_EXECS\s+10\b"),
    ("first 5 then every 100th", r"#define SEL_FIRST\s+5\b.*#define SEL_EVERY\s+100\b"),
    ("row carries status, signal, jetsam reason, killer", r"xstat 0x%x code %d signal %d reason ns %u code 0x%llx jetsam_flags 0x%x by %s\[%d\]"),
    ("row prints the signed code", r"sel_code\(rv\), rv & 0x7f"),
    ("table-full GAP", r"SWEET GAP: exit log table full"),
    ("old KiB field kept, new fields appended", r"KiB %llu busy %llu ms lockwait %llu ms maxcmd %llu us rios %llu wios %llu rKiB %llu wKiB %llu"),
    ("total KiB still the sum", r"sweet_ufs_stat\[2\] \+= count;\s*sweet_ufs_stat\[rd \? 6 : 7\] \+= count;"),
    ("ios split", r"sweet_ufs_stat\[rd \? 8 : 9\]\+\+;"),
    ("stat array has 10 slots (definition)", r"(?m)^uint64_t sweet_ufs_stat\[10\];"),
    ("stat array has 10 slots (extern)", r"extern uint64_t sweet_ufs_stat\[10\];"),
    ("vm row reads six counters", r"counter_load\(&vm_statistics_pageins\).*counter_load\(&vm_statistics_pageouts\).*counter_load\(&vm_statistics_compressions\).*counter_load\(&vm_statistics_decompressions\).*counter_load\(&vm_statistics_swapins\).*counter_load\(&vm_statistics_swapouts\)"),
    ("vm row every second tick", r"if \(\(tick & 1\) == 0\) \{[^}]*cs_vm_row\(now_ms\);"),
    ("vm row text", r"CPUSTAT vm up %llu ms window %llu ms \| pageins %llu pageouts %llu compressions %llu decompressions %llu swapins %llu swapouts %llu"),
]


def main():
    text = open(PATCH).read()
    func = extract(new_file_body(text, "bsd/dev/sweet_exitlog.c"))
    rc, out = run(func)
    if rc:
        print(out)
        print("RESULT: FAIL 0362-diag: harness")
        return 1
    cases = 1
    src = body(text)
    for name, rx in TEXT:
        if not re.search(rx, src, re.S):
            print("RESULT: FAIL 0362-diag: patch text check failed: %s" % name)
            return 1
        cases += 1
    mutants = [
        ("threshold 10 -> 11", func.replace("#define SEL_MIN_EXECS   10", "#define SEL_MIN_EXECS   11")),
        ("threshold 10 -> 9", func.replace("#define SEL_MIN_EXECS   10", "#define SEL_MIN_EXECS   9")),
        ("first 5 -> 4", func.replace("#define SEL_FIRST       5", "#define SEL_FIRST       4")),
        ("every 100 -> 50", func.replace("#define SEL_EVERY       100", "#define SEL_EVERY       50")),
        ("prefix match", func.replace("strncmp(t->e[i].name, name, sizeof(t->e[i].name)) == 0) {\n\t\t\tstruct", "strncmp(t->e[i].name, name, 3) == 0) {\n\t\t\tstruct")),
        ("table never full", func.replace("if (t->n >= SEL_NAMES) {", "if (0) {")),
        ("exit of unknown name added", func.replace("\treturn false;\n}\n", "\tt->n++;\n\treturn true;\n}\n")),
        ("exits not counted", func.replace("e->exits++;", "")),
        ("execs not counted", func.replace("e->execs++;", "")),
        ("code masked to 8 bits (the old bug)", func.replace("return rv >> 8;", "return (rv >> 8) & 0xff;")),
        ("code not shifted", func.replace("return rv >> 8;", "return rv;")),
        ("code unsigned shift", func.replace("return rv >> 8;", "return (int)((unsigned)rv >> 8);")),
    ]
    dead = 0
    for name, m in mutants:
        if m == func:
            print("RESULT: FAIL 0362-diag: mutant %s did not change the source" % name)
            return 1
        rc, _ = run(m)
        if rc == 0:
            print("RESULT: FAIL 0362-diag: mutant %s survived" % name)
            return 1
        dead += 1
    for name, rx in TEXT:
        m = re.sub(rx, "", src, count=1, flags=re.S)
        if m == src or re.search(rx, m, re.S):
            print("RESULT: FAIL 0362-diag: text mutant %s did not remove the hook" % name)
            return 1
        dead += 1
    print("RESULT: PASS 0362-diag cases=%d mutants=%d" % (cases, dead))
    return 0


import unittest  # the gate runs tests through unittest discover: wrap main() so the file counts as one test (it printed RESULT and exited before)


class RunMain(unittest.TestCase):
    def test_main_passes(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
