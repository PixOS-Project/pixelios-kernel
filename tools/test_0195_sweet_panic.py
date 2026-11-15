#!/usr/bin/env python3
"""Host test for xnu patch 0195 sweet-panic-test (T-118f, PR-2 rerun; no phone). Reads the patch text; compiles one extracted function.

    tools/test_0195_sweet_panic.py [--kernel KERNEL] [--patch FILE]

Checks (added side of the patch):
(a) sweet_panic_decide() (extracted between the SWEET_PANIC_DECIDE_BEGIN/END markers, compiled with cc, run over a table): 0 -> off, 1..600 -> armed,
    601 and 0xffffffff -> out of range (the boot-arg parse; PE_parse_boot_argn itself is XNU's and needs the kernel);
(b) rule 8: every refusal path prints a `SWEET GAP:` line (out of range, no USB log thread, kernel_thread_start failed, gadget never configured) and a set
    sweetpanic always prints `SWEET GAP: sweetpanic=%u set (test only)`;
(c) the panic thread waits for ud.configured, sleeps the S seconds, prints `SWEET FAULT TEST: firing now` BEFORE panic(), and panic() is called once;
(d) the start is called after the ud_log_thread start (log_started false when it did not start) and the patch only adds lines;
(e) no register write: no volatile pointer store, no `writel`/`usb_wr`/`mmio` in the added lines;
(f) mutants of the patch text, each of which must FAIL a check.
With --kernel KERNEL: `strings` of the built kernel must contain `SWEET GAP: sweetpanic=%u set (test only)` and `SWEET FAULT TEST: firing now`.
Last line: `RESULT: PASS sweetpanic-build ...` or `RESULT: FAIL sweetpanic-build <cause>`. Exit 0 PASS, 1 FAIL, 2 ERROR. Not run on hardware.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import pathlib, re, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
PATCH = HERE.parent / "xnu" / "patches" / "0195-sweet-panic-test.patch"


def added(text):
    return "\n".join(l[1:] for l in text.split("\n") if l.startswith("+") and not l.startswith("+++"))


def new_side(text):
    return "\n".join(l[1:] for l in text.split("\n") if l[:1] in (" ", "+") and not l.startswith("+++"))


def removed(text):
    return [l for l in text.split("\n") if l.startswith("-") and not l.startswith("---")]


def decide_table(text):
    """Compile the extracted sweet_panic_decide and evaluate it; returns {value: result} or raises."""
    m = re.search(r"SWEET_PANIC_DECIDE_BEGIN[^\n]*\n(.*?)/\* SWEET_PANIC_DECIDE_END", added(text), re.S)
    if not m:
        raise ValueError("no SWEET_PANIC_DECIDE markers")
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise RuntimeError("no C compiler")
    vals = [0, 1, 2, 20, 599, 600, 601, 1000, 0xffffffff]
    src = ("#include <stdint.h>\n#include <stdio.h>\n#define SWEET_PANIC_MAX 600\n" + m.group(1) +
           "int main(void){uint32_t v[]={" + ",".join("%du" % x for x in vals) + "};for(unsigned i=0;i<%d;i++)printf(\"%%u %%d\\n\",v[i],sweet_panic_decide(v[i]));return 0;}\n" % len(vals))
    with tempfile.TemporaryDirectory() as d:
        c, exe = pathlib.Path(d, "t.c"), pathlib.Path(d, "t")
        c.write_text(src)
        r = subprocess.run([cc, "-Wall", "-Werror", "-o", str(exe), str(c)], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("compile failed: " + r.stderr[:300])
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
    return {int(a): int(b) for a, b in (l.split() for l in out.splitlines())}


def check(text):
    bad, a = [], added(text)
    try:
        t = decide_table(text)
        want = {0: 0, 1: 1, 2: 1, 20: 1, 599: 1, 600: 1, 601: -1, 1000: -1, 0xffffffff: -1}
        if t != want:
            bad.append("decide table %s != %s" % (t, want))
    except (ValueError, RuntimeError) as e:
        bad.append("decide: %s" % e)
    for need in ("SWEET GAP: sweetpanic=%u outside 1..%d, ignored", "SWEET GAP: sweetpanic=%u refused: the USB log thread did not start",
                 "SWEET GAP: sweetpanic=%u set (test only)", "SWEET GAP: sweetpanic=%u: kernel_thread_start failed",
                 "SWEET GAP: sweetpanic=%u: the USB gadget was not configured"):
        if need not in a:
            bad.append("rule 8: missing %r" % need)
    th = re.search(r"static void\nsweet_panic_thread\(.*?\n}\n", a, re.S)
    if not th:
        bad.append("no sweet_panic_thread")
    else:
        b = th.group(0)
        if not re.search(r"for \(i = 0; i < SWEET_PANIC_CFG_WAIT_S \* 10 && !ud\.configured; i\+\+\) \{\n\t\tud_sleep_ms\(100\);", b):
            bad.append("no bounded wait loop on !ud.configured")
        i_cfg, i_slp, i_now, i_pan = b.find("ud.configured"), b.find("i < secs"), b.find("firing now"), b.find("panic(\"")
        if not (0 <= i_cfg < i_slp < i_now < i_pan):
            bad.append("thread order wrong (configured wait, S s sleep, marker printf, panic): %s" % [i_cfg, i_slp, i_now, i_pan])
        if b.count("panic(") != 1:
            bad.append("panic() called %d times" % b.count("panic("))
    if not re.search(r"kernel_thread_start\(ud_log_thread, NULL, &thread\) == KERN_SUCCESS\) \{\n\t\tthread_deallocate\(thread\);\n"
                     r"\t\tlog_started = true;\n\t}\n\tsweet_panic_test_start\(log_started\);", new_side(text)):
        bad.append("start not after the ud_log_thread start with log_started")
    if "kernel_thread_start(sweet_panic_thread" not in a:
        bad.append("no kernel thread start")
    if removed(text):
        bad.append("patch removes lines (it must only add): %s" % removed(text))
    if re.search(r"\*\(volatile|writel|usb_wr\(|mmio|writeq", a):
        bad.append("hardware write in added lines")
    return bad


MUTANTS = {
    "limit raised": lambda t: t.replace("v > SWEET_PANIC_MAX", "v > SWEET_PANIC_MAX + 1", 1),
    "zero armed": lambda t: t.replace("if (v == 0) {", "if (v == 99999) {", 1),
    "out of range silent": lambda t: t.replace("SWEET GAP: sweetpanic=%u outside", "sweetpanic=%u outside", 1),
    "set line dropped": lambda t: t.replace("SWEET GAP: sweetpanic=%u set (test only)", "sweetpanic=%u set", 1),
    "no configured wait": lambda t: t.replace("&& !ud.configured", "&& 0", 1),
    "marker after panic": lambda t: t.replace("firing now (test fault after", "firing later (test fault after", 1),
    "unlogged refusal": lambda t: t.replace("SWEET GAP: sweetpanic=%u refused", "sweetpanic=%u refused", 1),
    "hardware write": lambda t: t.replace("static int\n+sweet_panic_decide", "static int\n+*(volatile uint32_t *)0 = 0; sweet_panic_decide", 1),
}


def main(argv):
    kernel, patch = None, PATCH
    it = iter(argv)
    for x in it:
        if x == "--kernel":
            kernel = next(it)
        elif x == "--patch":
            patch = pathlib.Path(next(it))
        else:
            print("RESULT: ERROR bad argument " + x)
            return 2
    text = patch.read_text(encoding="latin-1")
    bad = check(text)
    for name, mut in MUTANTS.items():
        t2 = mut(text)
        if t2 == text:
            bad.append("mutant %r did not change the patch (test bug)" % name)
        elif not check(t2):
            bad.append("mutant %r NOT caught" % name)
    if kernel:
        s = subprocess.run(["strings", "-a", kernel], capture_output=True, text=True).stdout
        for need in ("SWEET GAP: sweetpanic=%u set (test only)", "SWEET FAULT TEST: firing now"):
            if need not in s:
                bad.append("kernel strings lack %r" % need)
    if bad:
        print("RESULT: FAIL sweetpanic-build " + "; ".join(bad))
        return 1
    print("RESULT: PASS sweetpanic-build (decide table + rule-8 lines + thread order + %d mutants caught%s)" %
          (len(MUTANTS), ", kernel strings ok" if kernel else ""))
    return 0


class Patch0195(unittest.TestCase):
    """The gate runs `python3 -m unittest discover -s tools -p FILE`; a script without a TestCase runs 0 tests (rc 5) and fails the unit step."""

    def test_patch_and_mutants(self):
        rc = main([])
        sys.stdout.flush()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
