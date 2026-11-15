#!/usr/bin/env python3
"""tools/repo-health: its self-test must pass."""
import os, subprocess, sys, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


class T(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "repo-health"), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_phone_tools_match_agent_guard(self):
        import re
        guard = re.search(r"^PHONE_TOOLS = \{(.*?)\}", open(os.path.join(TOOLS, "agent-guard")).read(), re.M).group(1)
        rh = re.search(r"^PHONE_TOOL_PATHS = .*?\((.*?)\)\}", open(os.path.join(TOOLS, "repo-health")).read(), re.M).group(1)
        self.assertEqual(sorted(re.findall(r'"([^"]+)"', guard)), sorted(re.findall(r'"([^"]+)"', rh)))


if __name__ == "__main__":
    unittest.main()
