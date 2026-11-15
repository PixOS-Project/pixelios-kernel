#!/usr/bin/env python3
"""Host test of tools/gate-xnu-build (TP-039, AP-147) with a fake xnu-local-build (no compiler, seconds, everything in a tempdir).
The fake prints the real tool's RESULT/sha256/error formats (tools/xnu-local-build:619-651) and records the stack it was given."""
import os, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "gate-xnu-build")
FAKE = r"""#!/bin/bash
stack=; label=; fresh=0
while [ $# -gt 0 ]; do case $1 in --stack) stack=$2; shift 2 ;; --label) label=$2; shift 2 ;; --fresh) fresh=1; shift ;; *) shift ;; esac; done
ls "$stack" >"$FAKE_REC"; cat "$stack"/*.patch >>"$FAKE_REC"; echo "label=$label" >>"$FAKE_REC"; echo "fresh=$fresh" >>"$FAKE_REC.calls"
if [ "$FAKE_MODE" = guard ] || { [ "$FAKE_MODE" = guard-fresh ] && [ $fresh = 0 ]; }; then
	echo "XNU BUILD GUARD: FAIL 1 problem(s)"; echo "RESULT: FAIL stale-object guard: xnu-build-guard: 1 linked object(s) not proven current (F3 STALE x.o)"
	echo "  F3 STALE $LOCALBUILD/work/$label/build/x.o: not recompiled, but 1 of its sources changed"; exit 1
fi
[ "$FAKE_MODE" != guard-fresh ] || FAKE_MODE=pass
[ -z "${FAKE_SLEEP:-}" ] || sleep "$FAKE_SLEEP"
k=$LOCALBUILD/work/$label/fakeroot/System/Library/Kernels/kernel.development.sweet
case $FAKE_MODE in
pass|stale) mkdir -p "$(dirname "$k")"; echo kernel-bytes >"$k"; s=$(sha256sum "$k" | cut -d' ' -f1)
	[ "$FAKE_MODE" = stale ] && s=0000000000000000000000000000000000000000000000000000000000000000
	echo "RESULT: PASS kernel.development.sweet built locally in 17s${FAKE_EXTRA:-}"; echo "kernel: $LOCALBUILD/out/$label/kernel.development.sweet"; echo "sha256: $s"; exit 0 ;;
fail) echo "[3s] make exit 2 after 17s" >&2
	echo "RESULT: FAIL build failed (3 error lines, make exit 2, 17s)"
	echo "$LOCALBUILD/work/$label/xnu/bsd/dev/sweet_wlan.c:409:9: error: call to undeclared function 'pmap_find_phys'; ISO C99 and later do not support implicit function declarations"
	echo "make[7]: *** [sweet_wlan.o] Error 1"; echo "build log: x"; exit 1 ;;
error) echo "RESULT: ERROR cannot clone pristine xnu-8796.141.3 (network?)"; exit 2 ;;
nopass) echo "no result line"; exit 0 ;;
esac
"""


def git(cwd, *a):
    subprocess.run(["git", "-C", cwd, "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True)


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        d = self.d = self.t.name
        self.repo = os.path.join(d, "wt")
        os.makedirs(self.repo + "/xnu/patches")
        with open(self.repo + "/xnu/patches/0001-a.patch", "w") as f:
            f.write("committed-0001\n")
        git(self.repo, "init", "-q", "-b", "trunk")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "base")
        git(self.repo, "checkout", "-qb", "topic")
        with open(self.repo + "/xnu/patches/0002-b.patch", "w") as f:
            f.write("committed-0002\n")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "new patch")
        self.fake = os.path.join(d, "fake-xlb")
        with open(self.fake, "w") as f:
            f.write(FAKE)
        os.chmod(self.fake, 0o755)
        self.rec = os.path.join(d, "rec")
        self.lb = os.path.join(d, "lb")
        self.env = dict(os.environ, GATE_XNU_LOCAL_BUILD=self.fake, LOCALBUILD=self.lb, FAKE_REC=self.rec, FAKE_MODE="pass", TMPDIR=d)

    def tearDown(self):
        self.t.cleanup()

    def run_tool(self, *a, **env):
        p = subprocess.run([TOOL, "--repo", self.repo, *a], capture_output=True, text=True, env=dict(self.env, **env), timeout=60)
        return p.returncode, p.stdout + p.stderr

    def last(self, out):
        return out.rstrip().splitlines()[-1]

    def test_pass_reports_work_tree_sha_and_builds_head_stack(self):
        with open(self.repo + "/xnu/patches/0003-uncommitted.patch", "w") as f:
            f.write("dirty\n")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertRegex(self.last(out), r"^RESULT: PASS xnu-build sha=[0-9a-f]{64} label=gate-wt stack=[0-9a-f]{12} \d+s where=unknown$")   # a build tool that names no where= is shown as unknown, never as offload-host
        with open(self.rec) as f:
            rec = f.read()
        self.assertIn("0001-a.patch\n0002-b.patch\ncommitted-0001\ncommitted-0002\n", rec)   # HEAD's stack, not the working tree
        self.assertNotIn("dirty", rec)
        self.assertIn("WARNING uncommitted/untracked changes in xnu/patches are NOT built", out)
        self.assertIn("WARNING tools/xnu-local-build replaced by GATE_XNU_LOCAL_BUILD", out)   # rule 8: the override is loud
        self.assertIn("kernel: %s/work/gate-wt/fakeroot/" % self.lb, out)   # never the shared $LOCALBUILD/out copy
        self.assertEqual([x for x in os.listdir(self.d) if x.startswith("gate-xnu-stack")], [])   # temp stack removed

    def test_where_fallback_and_dirty_ride_on_the_result_line(self):   # review xnu-remote-build-r1 F7/F4: the summary line the driver reads says where the kernel was built
        for extra, want in ((" where=offload-host head=abc dirty=0 stack=def", r" \d+s where=offload-host$"),
                            (" where=lab-host-fallback fallback=lab-host head=abc dirty=0 stack=def", r" \d+s where=lab-host-fallback fallback=lab-host$"),
                            (" where=offload-host head=abc dirty=3 stack=def DIRTY", r" \d+s where=offload-host dirty=3$")):
            rc, out = self.run_tool(FAKE_EXTRA=extra)
            self.assertEqual(rc, 0, out)
            self.assertRegex(self.last(out), r"^RESULT: PASS xnu-build sha=[0-9a-f]{64} label=gate-wt stack=[0-9a-f]{12}" + want)
        rc, out = self.run_tool(XNUB_IN_SLOT="1")   # inside the offload-host container the build prints no where=
        self.assertRegex(self.last(out), r" where=offload-slot$")

    def test_undeclared_function_fails_with_first_error(self):   # the AP-147 case (patch 0167)
        rc, out = self.run_tool(FAKE_MODE="fail")
        self.assertEqual(rc, 1, out)
        self.assertTrue(self.last(out).startswith("RESULT: FAIL xnu-build errors=3 first=bsd/dev/sweet_wlan.c:409 label=gate-wt"), out)
        self.assertIn("call to undeclared function 'pmap_find_phys'", out)
        self.assertNotIn("kernel:", out)

    def test_stale_sha_is_error(self):
        rc, out = self.run_tool(FAKE_MODE="stale")
        self.assertEqual(rc, 2, out)
        self.assertIn("differs from the reported", self.last(out))

    def test_tool_error_and_missing_pass_line(self):
        rc, out = self.run_tool(FAKE_MODE="error")
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR xnu-build: xnu-local-build exit 2", self.last(out))
        rc, out = self.run_tool(FAKE_MODE="nopass")
        self.assertEqual(rc, 2, out)
        self.assertIn("exit 0 without a PASS line", self.last(out))

    def test_skip_only_when_stack_equals_trunk(self):
        git(self.repo, "checkout", "-q", "trunk")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS xnu-build SKIPPED", self.last(out))
        self.assertFalse(os.path.exists(self.rec))
        rc, out = self.run_tool("--always")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS xnu-build sha=", self.last(out))

    def test_label_busy_refused(self):
        p = subprocess.Popen([TOOL, "--repo", self.repo, "--label", "x"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env=dict(self.env, FAKE_SLEEP="3"))
        try:
            for _ in range(100):   # wait (bounded) until the first run holds the lock: the fake has written its record
                if os.path.exists(self.rec):
                    break
                subprocess.run(["sleep", "0.05"])
            rc, out = self.run_tool("--label", "x")
        finally:
            p.communicate(timeout=30)
        self.assertEqual(rc, 2, out)
        self.assertIn("label x is busy", self.last(out))

    def test_bad_args(self):
        rc, out = self.run_tool("--jobs", "0")
        self.assertEqual(rc, 2, out)
        rc, out = self.run_tool("--nope")
        self.assertEqual(rc, 2, out)
        self.assertTrue(self.last(out).startswith("RESULT: ERROR"), out)



class XnuLocalBuildLock(unittest.TestCase):   # review dev-speed-gaps-r1 F1: one XNU build per host, whatever the label
    def test_second_build_refused_while_lock_held(self):
        import fcntl, tempfile
        here = os.path.dirname(os.path.abspath(__file__))
        with tempfile.TemporaryDirectory() as lb:
            with open(os.path.join(lb, ".build-lock"), "a") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                r = subprocess.run([os.path.join(here, "xnu-local-build"), "--label", "other", "--timeout", "2"], capture_output=True, text=True,
                # HEAVY_JOB_HELD: this tests the LOCALBUILD lock, not heavy-job; without it the test takes the real host lock under $HOME (AP-210)
                                   env=dict(os.environ, HEAVY_JOB_HELD="test", LOCALBUILD=lb, ARTIFACTS=os.path.join(lb, "art")), stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("RESULT: ERROR another xnu-local-build held", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(lb, "art")), "a refused build must write nothing under ARTIFACTS (AP-210)")

    def test_label_must_be_one_path_component(self):   # review localbuild-per-label-r1 F2: refused before any work
        import tempfile
        here = os.path.dirname(os.path.abspath(__file__))
        for bad in ("../x", "a/b", "-x", ".hidden", "a b", "_x"):
            with tempfile.TemporaryDirectory() as lb:
                r = subprocess.run([os.path.join(here, "xnu-local-build"), "--label", bad, "--timeout", "2"], capture_output=True, text=True,
                                   env=dict(os.environ, HEAVY_JOB_HELD="test", LOCALBUILD=lb, ARTIFACTS=lb), stdin=subprocess.DEVNULL, timeout=60)
                self.assertEqual(r.returncode, 2, bad + r.stdout + r.stderr)
                self.assertIn("RESULT: ERROR --label must be one path component", r.stdout)
                self.assertEqual(os.listdir(lb), [], bad)   # nothing created

class Guard(unittest.TestCase):   # the stale-object guard retry (2026-11-11); T's fixture without re-running T's own tests
    setUp, tearDown, run_tool = T.setUp, T.tearDown, T.run_tool

    def test_stale_guard_rebuilds_once_fresh_and_passes(self):   # 2026-11-11: a shared label went stale after a stack switch
        rc, out = self.run_tool("--always", FAKE_MODE="guard-fresh")
        self.assertEqual(rc, 0, out)
        self.assertIn("STALE-OBJECT GUARD failed on work tree", out)
        self.assertIn("RESULT: PASS xnu-build", out)
        self.assertEqual(open(self.rec + ".calls").read().split(), ["fresh=0", "fresh=1"])

    def test_stale_guard_after_fresh_still_fails(self):   # the guard is never bypassed: a fresh build that is still stale FAILs
        rc, out = self.run_tool("--always", FAKE_MODE="guard")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL xnu-build", out)
        self.assertEqual(open(self.rec + ".calls").read().split(), ["fresh=0", "fresh=1"])

    def test_plain_compile_error_is_not_retried(self):
        rc, out = self.run_tool("--always", FAKE_MODE="fail")
        self.assertEqual(rc, 1, out)
        self.assertEqual(open(self.rec + ".calls").read().split(), ["fresh=0"])


class PerRunLog(unittest.TestCase):   # 2026-11-11: the shared label's single log was overwritten by the next gate before the failure was read
    setUp, tearDown, run_tool = T.setUp, T.tearDown, T.run_tool

    def test_a_failed_runs_log_survives_the_next_run(self):
        import glob, time
        rc1, out1 = self.run_tool("--always", "--label", "shared", FAKE_MODE="fail")
        time.sleep(1.1)   # the per-run name carries a UTC second
        rc2, out2 = self.run_tool("--always", "--label", "shared", FAKE_MODE="pass")
        self.assertEqual((rc1, rc2), (1, 0), out1 + out2)
        logs = sorted(glob.glob(os.path.join(self.lb, "work", ".shared.gate-xnu-build.*.log")))
        self.assertEqual(len(logs), 2, logs)
        self.assertTrue(any("pmap_find_phys" in open(l).read() for l in logs), "the failed run's log is gone")
        link = os.path.join(self.lb, "work", ".shared.gate-xnu-build.log")
        self.assertTrue(os.path.islink(link))
        self.assertIn("RESULT: PASS", open(link).read())   # the stable name = the newest run


if __name__ == "__main__":
    unittest.main()
