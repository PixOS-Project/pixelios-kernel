#!/usr/bin/env python3
"""Host test for the devsh USB-net fast fail (2026-11-09, gputest-fcap6): no interface with the phone's gadget MAC, or one that is
down -> `devsh ping` against 192.168.7.2 fails in well under a second with the link message; a present+up link and other hosts are
not judged. Fake /sys/class/net trees through DEVSH_SYS_CLASS_NET; no network traffic to the phone."""
import importlib.machinery, importlib.util, os, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DEVSH = os.path.join(HERE, "devsh")


def load():
    l = importlib.machinery.SourceFileLoader("devsh_t", DEVSH)
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader("devsh_t", l))
    l.exec_module(m)
    return m


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.m = load()

    def tearDown(self):
        self.d.cleanup()

    def iface(self, name, mac, flags):
        p = os.path.join(self.d.name, name)
        os.makedirs(p)
        open(os.path.join(p, "address"), "w").write(mac + "\n")
        open(os.path.join(p, "flags"), "w").write(flags + "\n")

    def test_states(self):
        self.iface("eth0", "aa:bb:cc:dd:ee:ff", "0x1003")
        self.assertEqual(self.m.usbnet_state(self.d.name), "absent")
        self.iface("enx02005e77ee01", "02:00:5e:77:ee:01", "0x1002")
        self.assertEqual(self.m.usbnet_state(self.d.name), "down")
        open(os.path.join(self.d.name, "enx02005e77ee01", "flags"), "w").write("0x1003\n")
        self.assertEqual(self.m.usbnet_state(self.d.name), "up")
        self.assertIsNone(self.m.usbnet_state(os.path.join(self.d.name, "missing")))

    def test_ping_fails_fast_without_link(self):
        self.iface("eth0", "aa:bb:cc:dd:ee:ff", "0x1003")
        env = dict(os.environ, DEVSH_SYS_CLASS_NET=self.d.name, PIXOS_DEVD_TIMEOUT="180")
        t0 = time.time()
        r = subprocess.run([sys.executable, DEVSH, "ping"], env=env, capture_output=True, text=True, timeout=60)
        self.assertLess(time.time() - t0, 10)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no USB-net link to the phone: the interface with MAC 02:00:5e:77:ee:01 is absent", r.stdout + r.stderr)

    def test_other_host_not_judged(self):
        with self.assertRaises(Exception) as c:   # loopback port with nothing listening: a refused connect, not the link verdict
            os.environ["DEVSH_SYS_CLASS_NET"] = self.d.name
            try:
                self.m.get_socket("127.0.0.1", 1, timeout=2)
            finally:
                os.environ.pop("DEVSH_SYS_CLASS_NET", None)
        self.assertNotIn("USB-net", str(c.exception))


if __name__ == "__main__":
    unittest.main()
