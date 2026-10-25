#!/usr/bin/env python3
"""Every tool with a `--self-test` mode must run in the gate: a step of tools/check-all.sh that calls `tools/NAME ... --self-test`, or a
tools/test_*.py (run by the gate's `unit` step) that names the tool and `--self-test`, or the tool's self-test runs a tools/test_*.py file itself (CR-01, new-tools review nt:A6). Replay: K 0113cbb^ (agent-guard
unwired) must FAIL, K 0113cbb (test_agent_guard.py runs it) must PASS; fixtures in tools/testdata/selftests-wired (taken once with git show)."""
import pathlib, re, unittest

TOOLS = pathlib.Path(__file__).resolve().parent
FIX = TOOLS / "testdata" / "selftests-wired"


def selftest_tools(tools):
    """tools: {name: text} of tools/* -> names whose text mentions --self-test (test_* files and check-all.sh itself excluded)."""
    return sorted(n for n, t in tools.items() if not n.startswith("test_") and n != "check-all.sh" and "--self-test" in t)


def unwired(tools, check_all, tests, modes=("step", "test", "delegate")):
    """-> names with a --self-test mode that no gate step and no test file runs. tests: {filename: text} of tools/test_*."""
    steps = [l for l in check_all.splitlines() if "--self-test" in l and not l.lstrip().startswith("#")]
    out = []
    for n in selftest_tools(tools):
        by_step = any(re.search(r"tools/" + re.escape(n) + r"(?![\w.-])", l) for l in steps)
        by_delegate = any(f in tools[n] for f in tests)      # the self-test just runs a test_*.py file, which the gate's `unit` step discovers (phone, runs, run-preflight)
        by_test = any(re.search(r"(?:tools/|[\"'])" + re.escape(n) + r"(?![\w-])", t) and "--self-test" in t for t in tests.values())
        if not ((by_step and "step" in modes) or (by_test and "test" in modes) or (by_delegate and "delegate" in modes)):
            out.append(n)
    return out


def read_tree(d=TOOLS):
    tools = {}
    for p in sorted(pathlib.Path(d).iterdir()):
        if p.is_file() and not p.name.endswith((".pyc", ".rules", ".txt", ".known")):
            try:
                tools[p.name] = p.read_text()
            except UnicodeDecodeError:
                continue
    tests = {n: t for n, t in tools.items() if n.startswith("test_") and n != pathlib.Path(__file__).name}   # not this file: it names every tool
    return tools, tools["check-all.sh"], tests


class Replay(unittest.TestCase):
    AG = {"agent-guard": "# agent-guard --self-test\nif a == '--self-test': pass\n"}      # shape of 0113cbb^ tools/agent-guard:10,926

    def test_0113cbb_parent_fails(self):
        check_all = (FIX / "fail" / "check-all.sh.txt").read_text()
        self.assertEqual(unwired(self.AG, check_all, {}), ["agent-guard"])

    def test_0113cbb_passes(self):
        check_all = (FIX / "pass" / "check-all.sh.txt").read_text()
        tests = {"test_agent_guard.py": (FIX / "pass" / "test_agent_guard.py.txt").read_text()}
        self.assertEqual(unwired(self.AG, check_all, tests), [])

    def test_step_wires_a_tool(self):
        step = "\tstep B hang-recover  tools/hang-recover --self-test\n"
        self.assertEqual(unwired({"hang-recover": "--self-test"}, step, {}), [])
        self.assertEqual(unwired({"hang-recover": "--self-test"}, "# " + step, {}), ["hang-recover"])
        self.assertEqual(unwired({"phone": "--self-test"}, step, {}), ["phone"])          # tools/phone must not match tools/phone-state
        self.assertEqual(unwired({"phone": "--self-test"}, "tools/phone-state --self-test", {}), ["phone"])

    def test_a_test_that_names_the_tool_without_running_it_does_not_count(self):
        self.assertEqual(unwired({"agent-guard": "--self-test"}, "", {"test_x.py": "agent-guard"}), ["agent-guard"])
        self.assertEqual(unwired({"agent-guard": "--self-test"}, "", {"test_x.py": "run('tools/agent-guard', '--self-test')"}), [])
        self.assertEqual(unwired({"runs": "--self-test"}, "", {"test_x.py": "this runs --self-test"}), ["runs"])   # prose is not a call

    def test_delegating_self_test(self):
        self.assertEqual(unwired({"runs": "--self-test\nos.path.join(HERE, 'test_runs.py')"}, "", {"test_runs.py": ""}), [])
        self.assertEqual(unwired({"runs": "--self-test\ntest_runs.py"}, "", {}), ["runs"])        # the named file must exist


class Mutants(unittest.TestCase):
    """CR-02: switch one way of being wired off; a case that relies on it must then report the tool unwired."""
    def test_each_mode_is_needed(self):
        cases = {"step": ({"hang-recover": "--self-test"}, "tools/hang-recover --self-test", {}),
                 "test": ({"agent-guard": "--self-test"}, "", {"test_x.py": "run('tools/agent-guard', '--self-test')"}),
                 "delegate": ({"runs": "--self-test test_runs.py"}, "", {"test_runs.py": ""})}
        for mode, (tools, ca, tests) in cases.items():
            self.assertEqual(unwired(tools, ca, tests), [], mode)
            self.assertEqual(unwired(tools, ca, tests, modes=tuple(m for m in cases if m != mode)), list(tools), mode)


class Tree(unittest.TestCase):
    def test_every_self_test_tool_is_in_the_gate(self):
        tools, check_all, tests = read_tree()
        self.assertTrue(selftest_tools(tools), "no --self-test tools found: the scan is broken")
        self.assertEqual(unwired(tools, check_all, tests), [], "tools with --self-test that no gate step or test_*.py runs")


if __name__ == "__main__":
    unittest.main()
