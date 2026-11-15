"""A designed-normal answer prints NO `GAP:` line; only an unexpected error does (rule 8, AP-326 F1 of review lint-silent-r1).

One test per file whose `except OSError: return None` was turned into a GAP print by the lint-silent rewrite. Each test provokes the normal case
(a process that exited, a missing file, a cache miss, a race with another waiter) and asserts empty stderr, then provokes one unexpected error and asserts the
GAP line is still there (so the silence cannot be bought by deleting the print)."""
import contextlib
import errno
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))


def load(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(TOOLS / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def stderr():
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        yield buf


class NormalRunsAreSilent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = pathlib.Path(self.tmp.name)

    def test_cpu_timeout(self):
        import cpu_timeout as ct
        with stderr() as e:
            r = ct.run_cpu_limited(["true"], 5, poll=0.05)   # killpg after a normal exit raises ProcessLookupError
            self.assertEqual(ct._stat(2 ** 22 + 12345), None)   # a vanished pid
            self.assertEqual(r.returncode if hasattr(r, "returncode") else r, 0)
        self.assertEqual(e.getvalue(), "")
        with stderr() as e, mock.patch.object(ct.os, "killpg", side_effect=PermissionError(errno.EPERM, "denied")):
            p = subprocess.Popen(["true"])
            p.wait()
            ct._finish(p, ["true"], 5, 0)
        self.assertIn("cpu_timeout GAP:", e.getvalue())

    def test_audit_memo(self):
        import audit_memo as am
        script = self.d / "script.py"
        script.write_text("v1")
        os.environ["PIXELIOS_AUDIT_MEMO_DIR"] = str(self.d / "cache")
        os.environ.pop("PIXELIOS_AUDIT_NO_MEMO", None)
        self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_MEMO_DIR", None)
        with stderr() as e:
            self.assertIsNone(am._exists("stat", str(self.d / "missing")))
            self.assertIsNone(am._load(self.d / "no-entry.json", "k"))
            self.assertIsNone(am._deps({str(self.d / "gone-dep"): "h"}))
            with contextlib.redirect_stdout(io.StringIO()):
                am.run(lambda: 0, b"", script=script, name="cold")   # a cold cache: miss, run, store
        self.assertEqual(e.getvalue(), "")
        bad = self.d / "bad.json"
        bad.write_text("{not json")
        with stderr() as e:
            self.assertIsNone(am._load(bad, "k"))
        self.assertIn("audit_memo GAP:", e.getvalue())

    def test_wlan_mutant_cache(self):
        import wlan_mutant_cache as wmc
        os.environ["GATE_MUT_CACHE"] = str(self.d)
        self.addCleanup(os.environ.pop, "GATE_MUT_CACHE", None)
        with stderr() as e:
            self.assertIsNone(wmc.get("ab" + "0" * 62))   # a miss
        self.assertEqual(e.getvalue(), "")
        (self.d / "cd").mkdir()
        (self.d / "cd" / ("cd" + "0" * 62)).write_text("{not json")
        with stderr() as e:
            self.assertIsNone(wmc.get("cd" + "0" * 62))
        self.assertIn("wlan_mutant_cache GAP:", e.getvalue())

    def _usb_reader(self, name):
        m = load(name.replace("-", "_"), name + ".py")
        (self.d / "1-1:1.0").mkdir()   # an interface directory has no idVendor
        with stderr() as e:
            self.assertIsNone(m._read_stripped(str(self.d / "1-1:1.0" / "idVendor")))
            self.assertIsNone(m.scan_once(str(self.d)))   # no matching device: None, and no noise per sysfs entry
        self.assertEqual(e.getvalue(), "")
        with stderr() as e:
            self.assertIsNone(m._read_stripped(str(self.d / "1-1:1.0")))   # a directory: IsADirectoryError is not "missing"
        self.assertIn(name + " GAP:", e.getvalue())
        return m

    def test_usb_log(self):
        m = self._usb_reader("usb-log")
        r, w = os.pipe()
        os.close(w)
        os.close(r)
        for errno_ in (errno.ENODEV, errno.EIO):   # the documented end of a capture
            with stderr() as e, mock.patch.object(m.select, "select", return_value=([1], [], [])), \
                    mock.patch.object(m.os, "read", side_effect=OSError(errno_, "gone")):
                m.drain(0, mock.Mock())
            self.assertEqual(e.getvalue(), "", errno_)
        with stderr() as e, mock.patch.object(m.select, "select", return_value=([1], [], [])), \
                mock.patch.object(m.os, "read", side_effect=OSError(errno.EBADF, "bad fd")):
            m.drain(0, mock.Mock())
        self.assertIn("usb-log GAP:", e.getvalue())

    def test_udump_recv(self):
        self._usb_reader("udump-recv")

    def test_phone_lock_queue(self):
        os.environ["PHONE_LOCK_DIR"] = str(self.d / "phone.lock")
        self.addCleanup(os.environ.pop, "PHONE_LOCK_DIR", None)
        pl = load("phone_lock_t", "phone-lock")
        q = pathlib.Path(pl.QUEUE)
        q.mkdir(parents=True)
        dead = subprocess.Popen(["true"])
        dead.wait()
        (q / ("1-%d-owner" % dead.pid)).write_text("")   # a dead waiter's ticket, over-age
        real_unlink = pl.os.unlink

        def raced(p):   # the other waiter removed the same ticket first
            real_unlink(p)
            raise FileNotFoundError(errno.ENOENT, "gone", p)
        with stderr() as e, mock.patch.object(pl.os, "unlink", side_effect=raced):
            self.assertEqual(pl.queue(), [])
        self.assertEqual(e.getvalue(), "")
        (q / "x-y").write_text("")   # a malformed name stays in the dir: one line, not one per 0.2 s poll
        with stderr() as e:
            pl.queue()
            pl.queue()
            pl.queue()
        self.assertEqual(e.getvalue().count("phone-lock GAP:"), 1, e.getvalue())
        (q / ("1-%d-owner" % dead.pid)).write_text("")
        with stderr() as e, mock.patch.object(pl.os, "unlink", side_effect=PermissionError(errno.EACCES, "denied")):
            pl.queue()
        self.assertIn("phone-lock GAP: OSError", e.getvalue())

    def test_run_preflight(self):
        rp = load("run_preflight_t", "run-preflight")
        os.environ["FLASH_STATE"] = str(self.d / "never-flashed.txt")
        self.addCleanup(os.environ.pop, "FLASH_STATE", None)
        with stderr() as e:
            self.assertIsNone(rp.flash_state_last())
            self.assertIsNone(rp.read_names(str(self.d / "missing.txt")))
        self.assertEqual(e.getvalue(), "")
        os.environ["FLASH_STATE"] = str(self.d)   # a directory: unexpected
        with stderr() as e:
            self.assertIsNone(rp.flash_state_last())
        self.assertIn("run-preflight GAP:", e.getvalue())

    def test_usbroot_host(self):
        uh = load("usbroot_host_t", "usbroot-host.py")
        with stderr() as e:
            self.assertIsNone(uh.iface_index("nonexistent0"))   # ENODEV: the interface is not up yet
            self.assertIsNone(uh.interface_mac("../../../../nonexistent-dir/x"))
            self.assertIsNone(uh.interface_mac("bonding_masters"))   # a file in /sys/class/net: NotADirectoryError (or absent)
        self.assertEqual(e.getvalue(), "")
        with stderr() as e, mock.patch.object(uh.socket, "if_nametoindex", side_effect=OSError(errno.EPERM, "denied")):
            self.assertIsNone(uh.iface_index("x"))
        self.assertIn("usbroot-host GAP:", e.getvalue())

    def test_check_readme(self):
        cr = load("check_readme_t", "check-readme.py")
        with stderr() as e:
            cr.generated(str(self.d))   # no upstream-files.txt in a private clone
        self.assertEqual(e.getvalue(), "")
        (self.d / "upstream-files.txt").mkdir()   # a directory: unexpected
        with stderr() as e:
            cr.generated(str(self.d))
        self.assertIn("check-readme GAP:", e.getvalue())

    def test_xrun_report(self):
        import xrun_report as xr
        sysfs = self.d / "usb"
        (sysfs / "1-1").mkdir(parents=True)
        (sysfs / "1-1" / "idVendor").write_text("18d1\n")
        os.environ["XRUN_SYSFS_USB"] = str(sysfs)
        os.environ["XRUN_SANDBOX"] = "1"
        self.addCleanup(os.environ.pop, "XRUN_SYSFS_USB", None)
        self.addCleanup(os.environ.pop, "XRUN_SANDBOX", None)
        with stderr() as e, mock.patch.object(xr.simseams, "sysfs_usb", return_value=str(sysfs)), \
                mock.patch.object(xr.pathlib.Path, "read_text", side_effect=FileNotFoundError(errno.ENOENT, "unplugged")):
            self.assertFalse(xr.usb_present())   # the device went away between glob and read
            self.assertEqual(xr.usb_ids(), "")
        self.assertEqual(e.getvalue(), "")
        with stderr() as e, mock.patch.object(xr.simseams, "sysfs_usb", return_value=str(sysfs)), \
                mock.patch.object(xr.pathlib.Path, "read_text", side_effect=PermissionError(errno.EACCES, "denied")):
            self.assertFalse(xr.usb_present())
        self.assertIn("xrun_report GAP:", e.getvalue())

    def test_agent_guard_dead_gate_pid(self):
        """E5: a RUNNING gate.result whose pid is dead is a stale marker (the normal case): no GAP, and the gate is not blocked"""
        repo = self.d / "repo"
        (repo / "shim" / "out").mkdir(parents=True)
        (repo / "tools").mkdir()
        (repo / "tools" / "check-all.sh").write_text("#!/bin/sh\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        dead = subprocess.Popen(["true"])
        dead.wait()
        (repo / "shim" / "out" / "gate.result").write_text("RUNNING %d\n" % dead.pid)
        hook = '{"tool_name": "Bash", "tool_input": {"command": "tools/check-all.sh"}, "cwd": "%s", "session_id": "t", "hook_event_name": "PreToolUse"}' % repo
        env = dict(os.environ, AGENT_GUARD_STATE=str(self.d / "st"), AGENT_GUARD_OUT=str(self.d / "out"), CLAUDE_PROJECT_DIR=str(self.d / "proj"))
        p = subprocess.run([sys.executable, "-I", str(TOOLS / "agent-guard"), "--pre"], input=hook, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.stderr, "", p.stderr)
        self.assertNotIn("a gate is RUNNING", p.stdout)


if __name__ == "__main__":
    unittest.main()
