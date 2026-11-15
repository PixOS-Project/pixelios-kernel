#!/usr/bin/env python3
"""Host test for tools/lint-shellcheck-reason (RULES R16, T-eff6): a bad file fails, a good file passes, plus the edge cases."""
import os
import subprocess
import sys
import tempfile
import unittest

T = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.join(T, "lint-shellcheck-reason")


class Lint(unittest.TestCase):
    def run_on(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.sh")
            open(p, "w").write(text)
            r = subprocess.run([sys.executable, "-I", LINT, p], capture_output=True, text=True)
        return r.returncode, r.stdout

    def test_bad_file_fails_and_names_the_line(self):
        rc, out = self.run_on("#!/bin/bash\n# shellcheck disable=SC2086\necho $x\n")
        self.assertEqual(rc, 1)
        self.assertIn("SHELLCHECK DISABLE WITHOUT REASON", out)
        self.assertIn("x.sh:2:", out)
        self.assertIn("RESULT: FAIL shellcheck-reason files=1 disables=1 bare=1", out)

    def test_good_file_passes(self):
        rc, out = self.run_on("#!/bin/bash\n# shellcheck disable=SC2086  # fl is a word list on purpose\necho $x\n"
                              "\t# shellcheck disable=SC2034,SC2154 -- set by the sourced lib\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS shellcheck-reason files=1 disables=2 bare=0", out)

    def test_one_word_or_empty_reason_fails(self):
        for tail in (" #", " # ok", " # ", " --", "  "):
            rc, out = self.run_on("# shellcheck disable=SC2086%s\n" % tail)
            self.assertEqual(rc, 1, repr(tail))

    def test_directive_in_a_string_or_other_directive_is_not_read(self):
        rc, out = self.run_on("printf '# shellcheck disable=SC2086\\n' > f\n# shellcheck source=lib.sh\n# shellcheck shell=bash\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("disables=0", out)

    def test_repo_tree_has_no_bare_disable(self):
        r = subprocess.run([sys.executable, "-I", LINT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_unreadable_file_is_an_error_not_a_pass(self):
        r = subprocess.run([sys.executable, "-I", LINT, "/nonexistent/x.sh"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("RESULT: ERROR", r.stdout)


if __name__ == "__main__":
    unittest.main()
