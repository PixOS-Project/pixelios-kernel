#!/usr/bin/env python3
"""Host tests of the WLANCRED credential channel (T-2952, SPEC-WIFI.md TRANSPORT block s.3): tools/mkwlancred.py, mkpayload --wlancred,
build-xnu-image.sh --wlancred, the xrun wiring (extracted lines run in bash) and the secret-scan cases with a planted FAKE PSK.
No device, no lock; every secret is random per run and is made up here (nothing from the real .env is read).
Self-check (tearDown): no captured stdout/stderr may contain the fake PSK, its hex or base64.
  python3 tools/test_mkwlancred.py   -> last line RESULT: PASS|FAIL test_wlancred tests=N failures=M
"""
import base64, os, pathlib, random, re, shutil, stat, string, subprocess, sys, tempfile, unittest, zlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import mkwlancred  # noqa: E402
import mkpayload  # noqa: E402
import xrun_secret_scan as xss  # noqa: E402
from tools import adt as adtmod  # noqa: E402

PY = sys.executable


class Base(unittest.TestCase):
    def setUp(self):
        rnd = random.SystemRandom()
        self.fake = "fake-psk-" + "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(14))
        self.ssid = "fake-ssid-" + "".join(rnd.choice(string.ascii_lowercase) for _ in range(5))
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="wlc-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.xdg = self.tmp / "xdg"
        self.xdg.mkdir(mode=0o700)
        self.env = self.tmp / "fake.env"
        self.write_env()
        self.outputs = []

    def write_env(self, drop=(), **over):
        d = {"PIXOS_WIFI_SSID": self.ssid, "PIXOS_WIFI_PSK": self.fake, "PIXOS_WIFI_SECURITY": "wpa2-psk", "PIXOS_WIFI_IP": "192.0.2.77", "PIXOS_WIFI_GW": "192.0.2.1"}
        d.update(over)
        for k in drop:
            d.pop(k, None)
        self.env.write_text("".join("%s=%s\n" % kv for kv in d.items()))

    def forms(self):
        p = self.fake.encode()
        return [p, p.hex().encode(), base64.b64encode(p)]

    def run_tool(self, argv, env_extra=None, **kw):
        env = dict(os.environ, XDG_RUNTIME_DIR=str(self.xdg))
        env.update(env_extra or {})
        r = subprocess.run(argv, capture_output=True, timeout=120, env=env, **kw)
        self.outputs += [r.stdout, r.stderr]
        return r

    def mk(self, **kw):
        return self.run_tool([PY, str(HERE / "mkwlancred.py"), "--env", str(self.env)], **kw)

    def blob_path(self, r):
        m = re.search(rb"^wlancred: (.+)$", r.stdout, re.M)
        self.assertIsNotNone(m, r.stdout)
        return pathlib.Path(m.group(1).decode())

    def tearDown(self):
        for o in self.outputs:
            for f in self.forms():
                self.assertNotIn(f, o, "the fake PSK appeared in captured output")


class MkWlancredTest(Base):
    def test_iv_format_mode_crc(self):
        r = self.mk()
        self.assertEqual(r.returncode, 0, r.stdout)
        p = self.blob_path(r)
        self.assertTrue(str(p).startswith(str(self.xdg) + "/wlancred-"), "blob must live under XDG_RUNTIME_DIR")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        blob = p.read_bytes()
        self.assertEqual(len(blob), 128)
        self.assertEqual(blob[:8], b"WLANCRB1")
        self.assertEqual(int.from_bytes(blob[124:], "little"), zlib.crc32(blob[:124]) & 0xffffffff)
        got = mkwlancred.parse(blob)
        self.assertEqual(got, {"ssid": self.ssid.encode(), "psk": self.fake.encode(), "ip": "192.0.2.77", "gw": "192.0.2.1"})
        self.assertIn(self.fake.encode(), blob)          # the PSK is in the FILE only (tearDown: not in stdout/stderr)
        self.assertEqual(blob[116:120], bytes([192, 0, 2, 77]))
        self.assertEqual(blob[120:124], bytes([192, 0, 2, 1]))
        self.assertIn(b"RESULT: PASS mkwlancred bytes=128", r.stdout)

    def test_v_missing_key_exits_2_naming_the_key(self):
        for key in ("PIXOS_WIFI_IP", "PIXOS_WIFI_GW", "PIXOS_WIFI_SSID", "PIXOS_WIFI_PSK", "PIXOS_WIFI_SECURITY"):
            self.write_env(drop=(key,))
            r = self.mk()
            self.assertEqual(r.returncode, 2, key)
            self.assertIn(b"RESULT: ERROR", r.stdout)
            self.assertIn(key.encode(), r.stdout)
            self.assertNotIn(b"192.0.2", r.stdout, "an address value was printed")
            self.assertFalse(list(self.xdg.iterdir()), "no output dir after an error")

    def test_empty_key_is_missing(self):
        self.write_env(PIXOS_WIFI_GW="")
        r = self.mk()
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"PIXOS_WIFI_GW", r.stdout)

    def test_refusals(self):
        cases = [("PIXOS_WIFI_SECURITY", {"PIXOS_WIFI_SECURITY": "wpa3"}), ("PIXOS_WIFI_IP", {"PIXOS_WIFI_IP": "not-an-ip"}),
                 ("PIXOS_WIFI_GW", {"PIXOS_WIFI_GW": "192.0.2.300"}), ("PIXOS_WIFI_PSK", {"PIXOS_WIFI_PSK": "short"}),
                 ("PIXOS_WIFI_PSK", {"PIXOS_WIFI_PSK": "a" * 64}), ("PIXOS_WIFI_PSK", {"PIXOS_WIFI_PSK": "café-passphrase"}),
                 ("PIXOS_WIFI_SSID", {"PIXOS_WIFI_SSID": "s" * 33})]
        for key, over in cases:
            self.write_env(**over)
            r = self.mk()
            self.assertEqual(r.returncode, 2, over)
            self.assertIn(key.encode(), r.stdout)
            for v in over.values():
                self.assertNotIn(v.encode(), r.stdout, "a value was printed")

    def test_no_tmpfs_no_fallback(self):
        r = self.run_tool([PY, str(HERE / "mkwlancred.py"), "--env", str(self.env)], env_extra={"XDG_RUNTIME_DIR": str(self.tmp / "nope")})
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"XDG_RUNTIME_DIR", r.stdout)

    def test_self_test_flag(self):
        r = self.run_tool([PY, str(HERE / "mkwlancred.py"), "--self-test"])
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_parse_rejects_damage(self):
        blob = mkwlancred.build_blob({"PIXOS_WIFI_SSID": self.ssid, "PIXOS_WIFI_PSK": self.fake, "PIXOS_WIFI_SECURITY": "wpa2-psk",
                                      "PIXOS_WIFI_IP": "192.0.2.77", "PIXOS_WIFI_GW": "192.0.2.1"})
        for name, bad in (("short", blob[:127]), ("magic", b"X" + blob[1:]), ("crc", blob[:30] + bytes([blob[30] ^ 1]) + blob[31:])):
            with self.assertRaises(ValueError, msg=name) as cm:
                mkwlancred.parse(bad)
            self.assertNotIn(self.fake, str(cm.exception))
        # security != 0 and a dirty padding byte, each with a VALID crc, still refused
        for off, val in ((8, 1), (20 + len(self.ssid), 1)):
            b = bytearray(blob[:124]); b[off] = val
            with self.assertRaises(ValueError):
                mkwlancred.parse(bytes(b) + (zlib.crc32(bytes(b)) & 0xffffffff).to_bytes(4, "little"))


def synthetic_kernel(path):
    """Smallest Mach-O mkpayload accepts: one __TEXT segment, LC_UNIXTHREAD, the SWEET_BOOT_LINE_LENGTH tag, no chained fixups."""
    import struct
    vm = 0xfffffff007004000
    seg = struct.pack("<II16s4Q4I", 0x19, 72, b"__TEXT", vm, 0x4000, 0, 0x1000, 7, 5, 0, 0)
    thr = struct.pack("<IIII", 0x5, 16 + 272, 6, 68) + bytes(8 * 32) + struct.pack("<Q", vm + 0x100) + bytes(8)
    hdr = struct.pack("<8I", 0xfeedfacf, 0x0100000c, 0, 2, 2, len(seg) + len(thr), 0, 0)
    buf = bytearray(0x1000)
    buf[:len(hdr) + len(seg) + len(thr)] = hdr + seg + thr
    buf[0x200:0x200 + len(mkpayload.KERNEL_TAG)] = mkpayload.KERNEL_TAG
    pathlib.Path(path).write_bytes(bytes(buf))


class MkpayloadTest(Base):
    def setUp(self):
        super().setUp()
        self.kernel, self.adt = self.tmp / "kernel", self.tmp / "A.adt"
        synthetic_kernel(self.kernel)
        subprocess.run([PY, "-I", str(HERE / "mkadt.py"), str(self.adt), "--board", "bluejay"], check=True, capture_output=True, timeout=120)
        r = self.mk()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.cred = self.blob_path(r)

    def prop(self, blob_bytes, name):
        root, _ = adtmod.parse(blob_bytes)
        chosen = next(c for c in root.children if c.name == "chosen")
        return next(c for c in chosen.children if c.name == "memory-map").prop(name)

    def test_iii_property_in_the_adt_and_nothing_else_reported(self):
        out = self.tmp / "payload.bin"
        r = self.run_tool([PY, "-I", str(HERE / "mkpayload.py"), str(self.kernel), str(self.adt), str(out), "--board", "bluejay", "--wlancred", str(self.cred)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(b"wlancred: present", r.stdout)
        data = out.read_bytes()
        (adt_off, adt_size), = [__import__("struct").unpack_from("<2Q", data, 0x20)]
        prop = self.prop(data[adt_off:adt_off + adt_size], "WLANCRED")
        self.assertIsNotNone(prop, "no WLANCRED property")
        pa, size = __import__("struct").unpack("<2Q", prop)
        self.assertEqual(size, 128)
        self.assertEqual(pa % 0x4000, 0, "page-rounded placement as WLANFW")
        self.assertNotIn(hex(pa).encode(), r.stdout, "the placement must not be reported")
        self.assertIn(self.cred.read_bytes(), data)
        info = mkpayload.build(str(self.kernel), str(self.adt), "debug=0xc", None, "bluejay", None, str(self.cred))[1]
        self.assertEqual(info["wlancred"], "present")
        self.assertIsNone(mkpayload.build(str(self.kernel), str(self.adt), "debug=0xc", None, "bluejay")[1]["wlancred"])

    def test_invalid_blob_refused_without_value(self):
        bad = self.tmp / "bad"
        bad.write_bytes(self.cred.read_bytes()[:100])
        r = self.run_tool([PY, "-I", str(HERE / "mkpayload.py"), str(self.kernel), str(self.adt), str(self.tmp / "o"), "--board", "bluejay", "--wlancred", str(bad)])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b"--wlancred", r.stderr)

    def test_build_xnu_image_needs_wlan_hold_and_hides_credentials(self):
        sh = str(HERE / "build-xnu-image.sh")
        r = self.run_tool(["bash", sh, "--wlancred", str(self.cred)])
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"--wlancred needs --wlan-hold", r.stderr)
        r = self.run_tool(["bash", sh, "--wlan-hold", "--wlancred", str(self.cred), "--args", "sweetwlan=26"], env_extra={"XNU_IMAGE_ARGS_ONLY": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"boot-args:", r.stdout)
        self.assertNotIn(self.ssid.encode(), r.stdout + r.stderr)
        self.assertNotIn(str(self.cred).encode(), r.stdout, "not even the blob path goes into the boot-args line")


class XrunWiringTest(Base):
    """The lines of tools/xrun themselves, extracted and run in bash (a full xrun needs the phone lock and a build)."""
    src = (HERE / "xrun").read_text()

    def bash(self, script):
        r = self.run_tool(["bash", "-c", script], cwd=self.tmp)
        return r.returncode, r.stdout.decode()

    def level_snippet(self):
        m = re.search(r"^(wlcarg=\(\); wl=0; read -ra wat.*?)$\n(if \[ \"\$intent\" = wlan \].*?)$", self.src, re.M)
        self.assertIsNotNone(m, "the sweetwlan level lines are gone from tools/xrun")
        return m.group(1), m.group(2)

    def test_credentials_only_for_wlan_intent_and_level_24_up(self):
        lv, cond = self.level_snippet()
        for intent, args, want in (("wlan", "sweetwlan=26", 1), ("wlan", "sweetwlan=24", 1), ("wlan", "sweetwlan=23", 0), ("wlan", "sweetwlan=2 sweetwlan=25", 1),
                                   ("wlan", "sweetwlan=25 sweetwlan=2", 0), ("wlan", "", 0), ("gpu", "sweetwlan=26", 0), ("", "sweetwlan=26", 0), ("wlan", "sweetwlan=x", 0)):
            rc, out = self.bash('set -u; intent="%s"; args="%s"; %s\n%s' % (intent, args, lv, cond + " echo YES; fi"))
            self.assertEqual("YES" in out, bool(want), (intent, args, out))

    def test_exit_trap_removes_blob_dir_and_every_image(self):
        m = re.search(r"^cleanup\(\) \{ stop_capture; ovstd_record; plock_drop\n.*?\}$", self.src, re.M | re.S)      # ovstd_record: overlay ledger, 928c488d
        self.assertIsNotNone(m, "the cleanup() of tools/xrun changed")
        cc = re.search(r"^cred_clean\(\) \{.*\}$", self.src, re.M)      # L-12: cleanup() and die() share it
        self.assertIsNotNone(cc, "cred_clean() of tools/xrun is missing")
        self.assertRegex(self.src, r"(?m)^die\(\) \{ cred_clean; \$R error ", "die() must remove the credential images before the report scans the run dir (L-12)")
        d = self.tmp / "work"
        (d / "shim/out").mkdir(parents=True)
        (d / "run").mkdir()
        blobdir = self.xdg / "wlancred-abc"
        blobdir.mkdir()
        (blobdir / "blob").write_bytes(b"x")
        files = [d / "shim/out/shim-boot-avb.img", d / "shim/out/shim-boot-avb.img.meta.json", d / "run/shim-boot-avb.img"]
        for f in files:
            f.write_bytes(b"x")
        keep = d / "shim/out/other.bin"
        keep.write_bytes(b"x")
        script = 'stop_capture(){ :; }; ovstd_record(){ :; }; plock_drop(){ :; }; %s\novstd=""; wlcdir="%s"; wlcimg=1; D="%s"; cd "%s"; cleanup' % (cc.group(0) + "\n" + m.group(0), blobdir, d / "run", d)
        self.assertEqual(self.bash(script)[0], 0)
        self.assertFalse(blobdir.exists())
        self.assertFalse(any(f.exists() for f in files))
        self.assertTrue(keep.exists())
        # no blob: nothing but the plock runs (a normal run keeps its shim/out image)
        files[0].write_bytes(b"x")
        self.assertEqual(self.bash(script.replace("wlcimg=1", "wlcimg=0").replace('wlcdir="%s"' % blobdir, 'wlcdir=""'))[0], 0)
        self.assertTrue(files[0].exists())
        # a wlcdir that is not a wlancred-* dir is never removed
        other = self.xdg / "precious"
        other.mkdir()
        self.assertEqual(self.bash(script.replace(str(blobdir), str(other)))[0], 0)
        self.assertTrue(other.exists())


class SecretScanCasesTest(Base):
    """(i) (ii) the planted fake PSK in the run dir; the leftover-image step of the report."""

    def setUp(self):
        super().setUp()
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()

    def scan(self):
        r = self.run_tool([PY, str(HERE / "xrun-secret-scan"), str(self.run_dir), "--env", str(self.env)])
        return r.returncode, r.stdout.decode()

    def test_i_one_file_leaks_1(self):
        (self.run_dir / "xrun.log").write_text("boot ok\nkey=%s\n" % self.fake)
        rc, out = self.scan()
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL leaks=1 files=1", out)

    def test_ii_second_file_leaks_2(self):
        (self.run_dir / "xrun.log").write_text("key=%s\n" % self.fake)
        (self.run_dir / "build.out").write_text("x %s x\n" % self.fake)
        rc, out = self.scan()
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL leaks=2 files=2", out)

    def test_clean_run_dir_passes(self):
        (self.run_dir / "xrun.log").write_text("boot ok\n")
        (self.run_dir / "image.meta.json").write_text('{"boot_args": "sweetwlan=26"}\n')
        rc, out = self.scan()
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS no secret in 2 files", out)

    def test_iii_leftover_image_is_a_gap_only_when_it_holds_the_psk(self):
        so = self.tmp / "shim-out"
        so.mkdir()
        old = os.environ.get("XRUN_SHIM_OUT")
        os.environ["XRUN_SHIM_OUT"] = str(so)
        self.addCleanup(lambda: os.environ.pop("XRUN_SHIM_OUT", None) if old is None else os.environ.__setitem__("XRUN_SHIM_OUT", old))
        old_env = os.environ.get("PIXOS_ENV_FILE")
        os.environ["PIXOS_ENV_FILE"] = str(self.env)
        self.addCleanup(lambda: os.environ.pop("PIXOS_ENV_FILE", None) if old_env is None else os.environ.__setitem__("PIXOS_ENV_FILE", old_env))
        (so / "shim-boot-avb.img").write_bytes(b"\0" * 4096 + b"a clean image" + b"\0" * 4096)
        (so / "shim-boot-avb.img.meta.json").write_text("{}")
        self.assertEqual(xss.report_step(str(self.run_dir))["left"], [], "a clean image is not a gap")
        (so / "shim-boot-avb.img").write_bytes(b"\0" * 4096 + mkwlancred.build_blob({
            "PIXOS_WIFI_SSID": self.ssid, "PIXOS_WIFI_PSK": self.fake, "PIXOS_WIFI_SECURITY": "wpa2-psk", "PIXOS_WIFI_IP": "192.0.2.77", "PIXOS_WIFI_GW": "192.0.2.1"}))
        self.assertEqual(xss.report_step(str(self.run_dir))["left"], [str(so / "shim-boot-avb.img")])

    def test_iii_report_prints_the_secret_gap_line(self):
        import xrun_report
        from test_xrun_report import make_run
        so = self.tmp / "shim-out"
        so.mkdir()
        img = so / "shim-boot-avb.img"
        img.write_bytes(b"\0" * 64 + self.fake.encode())
        old = {k: os.environ.get(k) for k in ("XRUN_SHIM_OUT", "PIXOS_ENV_FILE")}
        os.environ.update(XRUN_SHIM_OUT=str(so), PIXOS_ENV_FILE=str(self.env))
        try:
            d = make_run(self.tmp / "rep", [(5, "SWEET: boot ok\n")], run={"expect": []})
            r = xrun_report.build(str(d))
            text = xrun_report.render(r)
            img.unlink()
            clean = xrun_report.render(xrun_report.build(str(make_run(self.tmp / "rep2", [(5, "SWEET: boot ok\n")], run={"expect": []}))))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertIn("SECRET GAP: image with credentials left at %s\n" % img, text)
        self.assertIn("gaps=1", text.splitlines()[0])
        self.assertNotIn(self.fake, text)
        self.assertNotIn("SECRET GAP", clean)


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    n, bad = res.testsRun, len(res.failures) + len(res.errors)
    print("RESULT: %s test_wlancred tests=%d failures=%d" % ("FAIL" if bad else "PASS", n, bad))
    sys.exit(1 if bad else 0)


class Net2PathLine(unittest.TestCase):
    """2026-11-10 run wd6-hotspot-a: xrun reads every `wlancred: ` line as the blob path; --net 2 must print exactly one."""
    def test_one_path_line(self):
        import os, subprocess, sys, tempfile
        d = tempfile.mkdtemp()
        env_file = os.path.join(d, ".env")
        open(env_file, "w").write("PIXOS_WIFI2_SSID=net-two\nPIXOS_WIFI2_PSK=passphrase-two\nPIXOS_WIFI2_SECURITY=wpa2-psk\n")
        r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mkwlancred.py"), "--env", env_file, "--net", "2"],
                           env=dict(os.environ, XDG_RUNTIME_DIR=d), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        paths = [l for l in r.stdout.splitlines() if l.startswith("wlancred: ")]
        self.assertEqual(len(paths), 1, r.stdout)
        self.assertTrue(os.path.isfile(paths[0].split(": ", 1)[1]))
        self.assertIn("wlancred-mode: dhcp", r.stdout)
