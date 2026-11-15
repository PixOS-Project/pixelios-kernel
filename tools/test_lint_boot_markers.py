"""tools/lint-boot-markers (gate step boot-markers, AP-229): the real patch stack passes, and a patch whose added print trips a failing run marker fails (negative tests).
Run: python3 -I tools/test_lint_boot_markers.py"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

LINT = pathlib.Path(__file__).resolve().parent / "lint-boot-markers"


def run(patch_text=None, real=False, files=None, extra=()):
    with tempfile.TemporaryDirectory() as d:
        if patch_text is not None:
            pathlib.Path(d, "0999-x.patch").write_text(patch_text)
        for n, t in (files or {}).items():
            pathlib.Path(d, n).write_text(t)
        args = [] if real else ["--patches", d]
        p = subprocess.run([sys.executable, "-I", str(LINT), *args, *extra], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr


def raw(*lines):
    """A hunk from literal diff lines (' ' context, '+' added, '-' removed)."""
    return "--- a/x.c\n+++ b/x.c\n@@ -1,9 +1,9 @@\n" + "".join(ln + "\n" for ln in lines)


def patch(body):
    return "--- a/x.c\n+++ b/x.c\n@@ -1 +1,3 @@\n" + "".join("+\t%s\n" % ln for ln in body.split("\n"))


class BootMarkers(unittest.TestCase):
    def test_real_stack_passes(self):
        rc, out = run(real=True)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"RESULT: PASS boot-markers patches=\d+ statements=[1-9]")

    def test_panic_word_in_a_print_fails(self):
        rc, out = run(patch('printf("SWEET UFS: no panic region, log lost\\n");'))
        self.assertEqual(rc, 1, out)
        self.assertIn("BOOT PRINT HITS MARKER 0999-x.patch:4 marker 'panic/hang/watchdog'", out)
        self.assertIn("RESULT: FAIL boot-markers", out)

    def test_other_words_and_functions(self):
        for stmt, lab in (('IOLog("probe hang at step %d\\n", i);', "panic/hang/watchdog"), ('kprintf("Assertion failed: x\\n");', "panic/hang/watchdog"),
                          ('printf("hfs_mountroot failed %d\\n", e);', "root mount failed")):
            rc, out = run(patch(stmt))
            self.assertEqual(rc, 1, stmt + out)
            self.assertIn("marker '%s'" % lab, out)

    def test_multi_line_concatenated_string_is_seen(self):
        rc, out = run(patch('printf("SWEET X: all good, but "\n    "the watchdog timeout fired\\n");'))
        self.assertEqual(rc, 1, out)

    def test_gap_line_is_exempt_like_xrun_report(self):
        rc, out = run(patch('printf("SWEET GAP: no panic region; panics spin\\n");'))
        self.assertEqual(rc, 0, out)

    def test_gap_line_with_a_real_panic_event_still_fails(self):
        rc, out = run(patch('printf("SWEET GAP: panic(cpu 0 caller x)\\n");'))
        self.assertEqual(rc, 1, out)

    def test_comments_and_non_print_code_are_ignored(self):
        rc, out = run(patch('printf("SWEET OK: step %d\\n", i);   /* gputest hung after the query */\nint panic_count = 0;   /* panic */\npanic("real panic");'))
        self.assertEqual(rc, 0, out)

    def test_context_and_removed_lines_are_ignored(self):
        rc, out = run("--- a/x.c\n+++ b/x.c\n@@ -1,2 +1,1 @@\n-\tprintf(\"panic old\\n\");\n \tprintf(\"panic ctx\\n\");\n+\tprintf(\"SWEET OK\\n\");\n")
        self.assertEqual(rc, 0, out)

    def test_no_patches_is_a_fail_not_a_pass(self):
        rc, out = run(None)
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL boot-markers patches=0", out)

    def test_two_marker_lines_of_one_print_are_one_hit(self):
        rc, out = run(patch('printf("panic one\\nkernel panic two\\n");'))
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.count("BOOT PRINT HITS MARKER"), 1, out)
        self.assertIn("hits=1 ", out)


class Wrappers(unittest.TestCase):
    """r42-gate-r1 F5.1: the real stack prints through macros; they are derived from the added #defines, not guessed."""
    def test_real_stack_wrappers_are_derived(self):
        rc, out = run(real=True, extra=["--wrappers"])
        self.assertEqual(rc, 0, out)
        names = set(out.split())
        for n in ("SM_UC_LOG", "SM_UC_ERR", "UR_TRACE", "SWEET_HFSTRACE", "HIDLogError", "HIDLog", "HIDLogFault", "HIDServiceLogError"):   # 0113:13-14, 0157:394, 0171:116, 0058:100-108
            self.assertIn(n, names)
        self.assertNotIn("HIDLogInfo", names)   # 0058:103 `do { } while (0)`: prints nothing
        rc, out = run(real=True)
        self.assertRegex(out, r"wrappers=\d\d")

    def test_each_named_wrapper_flags_a_marker_word(self):
        defs = ("#define SM_UC_LOG(...) do { if (n < 64) { printf(__VA_ARGS__); } } while (0)", "#define SM_UC_ERR(...) printf(__VA_ARGS__)",
                '#define UR_TRACE(fmt, ...) printf("SWEET USBROOT: [%llu ms] " fmt "\\n", ms(), ##__VA_ARGS__)',
                '#define HIDLogError(fmt, ...)   IOLog("HID error: " fmt "\\n", ##__VA_ARGS__)',
                "#define SWEET_HFSTRACE(fmt, ...) do { \\\n\tkprintf(fmt, ##__VA_ARGS__); \\\n} while (0)")
        calls = ('SM_UC_LOG("SWEET UC: probe hang at step %d\\n", i);', 'SM_UC_ERR("watchdog timeout\\n");', 'UR_TRACE("hfs_mountroot failed %d", e);',
                 'HIDLogError("Kernel trap in hid");', 'SWEET_HFSTRACE("Assertion failed: x\\n");')
        for d, c in zip(defs, calls):
            with self.subTest(c=c):
                rc, out = run(files={"0001-def.patch": patch(d), "0002-use.patch": patch(c)})
                self.assertEqual(rc, 1, out)
                self.assertIn("BOOT PRINT HITS MARKER 0002-use.patch:", out)

    def test_wrapper_of_a_wrapper_and_unknown_or_silent_macros(self):
        d = patch('#define LOGA(fmt, ...) IOLog("A: " fmt, ##__VA_ARGS__)\n#define LOGB(fmt, ...) LOGA("B " fmt, ##__VA_ARGS__)\n#define LOGC(fmt, ...) do { } while (0)')
        rc, out = run(files={"0001-def.patch": d, "0002-use.patch": patch('LOGB("watchdog timeout");')})
        self.assertEqual(rc, 1, out)                                     # nested: LOGB -> LOGA -> IOLog
        rc, out = run(files={"0001-def.patch": d, "0002-use.patch": patch('LOGC("watchdog timeout");')})
        self.assertEqual(rc, 0, out)                                     # prints nothing
        rc, out = run(patch('UNKNOWN_LOG("watchdog timeout");'))
        self.assertEqual(rc, 0, out)                                     # not a known print: documented limit (wrapper outside the patches)
        rc, out = run(patch('#define NOTE(x) foo("printf(")\nNOTE("watchdog timeout");'))
        self.assertEqual(rc, 0, out)                                     # "printf(" inside a string is no call

    def test_redefining_a_wrapper_as_a_no_op_with_a_string_is_not_a_print(self):
        """The #define line's own name is a definition, not a call (a later patch may turn a wrapper off)."""
        d = patch('#define LOGA(fmt, ...) IOLog("A: " fmt, ##__VA_ARGS__)')
        rc, out = run(files={"0001-def.patch": d, "0002-off.patch": patch('#define LOGA(fmt, ...) do { (void)"panic region"; } while (0)')})
        self.assertEqual(rc, 0, out)

    def test_a_macro_definition_without_semicolon_does_not_swallow_the_next_lines(self):
        rc, out = run(patch('#define T(fmt, ...) printf("SWEET: " fmt, ##__VA_ARGS__)\nconst char *why = "panic region";'))
        self.assertEqual(rc, 0, out)
        rc, out = run(patch('#define T(fmt, ...) printf("SWEET: panic " fmt, ##__VA_ARGS__)'))
        self.assertEqual(rc, 1, out)                                     # but a marker in the macro body itself is flagged

    def test_every_basic_print_function_is_covered(self):
        for stmt in ('dprintf("panic: x\\n");', 'DBGLOG("panic: x\\n");', 'os_log_error(OS_LOG_DEFAULT, "panic: x");', 'os_log("panic: x");', 'IOLog("panic: x");', 'kprintf("panic: x");', 'printf("panic: x");'):
            with self.subTest(stmt=stmt):
                rc, out = run(patch(stmt))
                self.assertEqual(rc, 1, out)

    def test_two_prints_on_one_line_and_semicolon_in_a_string(self):
        rc, out = run(patch('if (a) printf("ok;\\n"); else printf("kernel panic: x\\n");'))
        self.assertEqual(rc, 1, out)
        rc, out = run(patch('printf("a;b panic\\n");'))
        self.assertEqual(rc, 1, out)


class Continuation(unittest.TestCase):
    """r1 F5.2: an added continuation of a print opened on a context line."""
    def test_added_continuation_of_a_context_print_is_seen(self):
        rc, out = run(raw(' \tprintf("SWEET X: state %s\\n",', '+\t\t"the watchdog timeout fired");'))
        self.assertEqual(rc, 1, out)
        self.assertIn("BOOT PRINT HITS MARKER 0999-x.patch:4", out)

    def test_added_harmless_continuation_passes_and_removed_lines_are_skipped(self):
        rc, out = run(raw(' \tprintf("SWEET X: state %s\\n",', '-\t\t"panic old");', '+\t\tname);'))
        self.assertEqual(rc, 0, out)

    def test_a_removed_line_inside_the_statement_does_not_end_it(self):
        rc, out = run(raw(' \tprintf("SWEET X: state %s\\n",', '-\t\t"old");', '+\t\t"the watchdog timeout fired");'))
        self.assertEqual(rc, 1, out)

    def test_an_open_context_print_at_a_file_boundary_is_not_ours(self):
        """The next file's header lines (`+++ b/y`) must not be fed as added code of the open statement."""
        rc, out = run("--- a/x.c\n+++ b/x.c\n@@ -1,2 +1,3 @@\n \tprintf(\"kernel panic old\\n\",\n" "diff --git a/y.c b/y.c\nindex 1..2 100644\n--- a/y.c\n+++ b/y.c\n@@ -1 +1,2 @@\n+\tint z;\n")
        self.assertEqual(rc, 0, out)

    def test_an_open_context_print_does_not_leak_across_hunks_or_files(self):
        rc, out = run(raw(' \tprintf("SWEET X: state %s\\n",', "@@ -20,2 +20,3 @@", '+\tconst char *w = "panic region";'))
        self.assertEqual(rc, 0, out)

    def test_pure_context_print_is_not_ours(self):
        rc, out = run(raw(' \tprintf("kernel panic old\\n",', ' \t\tx);', '+\tint y;'))
        self.assertEqual(rc, 0, out)


class PerLogLine(unittest.TestCase):
    """r1 F5.3: the GAP exemption and the marker test apply per log line, like xrun_report.py:510-513."""
    def test_marker_on_a_second_line_after_a_gap_line_is_flagged(self):
        rc, out = run(patch('printf("SWEET GAP: ok\\nSWEET: kernel panic here\\n");'))
        self.assertEqual(rc, 1, out)

    def test_gap_on_the_second_line_is_exempt(self):
        rc, out = run(patch('printf("boot ok\\nSWEET GAP: x panic region missing\\n");'))
        self.assertEqual(rc, 0, out)

    def test_a_real_panic_event_after_a_gap_marker_on_the_same_line_still_fails(self):
        rc, out = run(patch('printf("boot ok\\nSWEET GAP: panic(cpu 0 caller x)\\n");'))
        self.assertEqual(rc, 1, out)

    def test_unknown_area_format_is_not_exempt(self):
        """`%s GAP:` cannot be resolved at lint time (the area may be anything): it stays flagged until reworded (conservative, no escape)."""
        rc, out = run(patch('printf("%s GAP: no panic region\\n", area);'))
        self.assertEqual(rc, 1, out)

    def test_words_are_not_glued_across_a_newline_or_tab(self):
        for stmt in ('printf("pan\\nic\\n");', 'printf("pa\\tnic");', 'printf("pa\\rnic");', 'printf("hu\\nng\\n");', 'printf("root mount\\nfailed hfs_mountroot\\n");'):
            with self.subTest(stmt=stmt):
                rc, out = run(patch(stmt))
                self.assertEqual(rc, 0, out)

    def test_marker_split_over_adjacent_string_literals_is_still_one_line(self):
        rc, out = run(patch('printf("SWEET: kernel pa" "nic here\\n");'))
        self.assertEqual(rc, 1, out)


if __name__ == "__main__":
    unittest.main()
