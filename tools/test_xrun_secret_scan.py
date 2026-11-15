#!/usr/bin/env python3
"""Host test for tools/xrun-secret-scan + xrun_report.py SECRET step (TP-051). No device, no lock, a random fake key per run.

Self-check: the captured output of every CLI/report call must not contain the fake key, its hex, base64 or PMK, and no file under the temp dir
except the planted ones (and the input env file) may contain them (no cache files).
"""
import base64, hashlib, json, os, pathlib, random, shutil, string, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import xrun_report  # noqa: E402
import xrun_secret_scan as xss  # noqa: E402
from test_xrun_report import make_run  # noqa: E402

CLI = str(HERE / "xrun-secret-scan")
SEPS = (b" ", b":", b"-")


def sep_hex(h, s):
    return s.join(h[i:i + 2] for i in range(0, len(h), 2))


class Base(unittest.TestCase):
    def setUp(self):
        rnd = random.SystemRandom()
        self.psk = "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(rnd.randint(12, 30)))
        self.ssid = "net" + "".join(rnd.choice(string.ascii_lowercase) for _ in range(7))
        self.pb, self.sb = self.psk.encode(), self.ssid.encode()
        self.pmk = hashlib.pbkdf2_hmac("sha1", self.pb, self.sb, 4096, 32)
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="xss-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = self.tmp / "fake.env"
        self.env.write_text("# comment\nOTHER=1\nexport PIXOS_WIFI_SSID='%s'\nPIXOS_WIFI_PSK=\"%s\"\nPIXOS_WIFI_SECURITY=wpa2\n" % (self.ssid, self.psk))
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.outputs = []       # every captured stdout/stderr (bytes) for the self-check
        self.planted = set()

    def secrets(self):
        p, m = self.pb, self.pmk
        s = [p, p.hex().encode(), p.hex().upper().encode(), base64.b64encode(p), m, m.hex().encode(), m.hex().upper().encode(), self.sb.hex().encode()]
        # the SSID itself is not a secret here (TP-051): not in the list
        return s[:-1]

    def plant(self, name, data):
        p = self.run_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self.planted.add(p)
        return p

    def cli(self, *args, run_dir=None, env=True):
        argv = [sys.executable, CLI, str(run_dir or self.run_dir)] + (["--env", str(self.env)] if env is True else [])
        r = subprocess.run(argv + list(args), capture_output=True, timeout=120)
        self.outputs += [r.stdout, r.stderr]
        return r.returncode, r.stdout.decode(), r.stderr.decode()

    def tearDown(self):
        for o in self.outputs:
            for s in self.secrets():
                self.assertNotIn(s, o, "a secret form appeared in captured output")


class FormsTest(Base):
    def one(self, data, form, name="f.log"):
        self.plant(name, data)
        rc, out, _ = self.cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("LEAK: %s:1 key=PIXOS_WIFI_PSK form=%s\n" % (name, form), out)
        self.assertEqual(out.strip().splitlines()[-1], "RESULT: FAIL leaks=%d files=1" % out.count("LEAK:"), out)
        self.assertEqual(out.count("LEAK:"), 1, out)

    def test_raw(self):
        self.one(b"junk before " + self.pb + b" after\n", "raw")

    def test_hex_lower_upper(self):
        self.one(b"x " + self.pb.hex().encode() + b"\n", "hex", "lo.log")

    def test_hex_upper(self):
        self.one(self.pb.hex().upper().encode(), "hex")

    def test_hex_separators(self):
        for i, s in enumerate(SEPS):
            for j, up in enumerate((False, True)):
                with self.subTest(sep=s, upper=up):
                    d = self.tmp / ("run%d%d" % (i, j))
                    d.mkdir()
                    h = sep_hex(self.pb.hex(), s.decode())
                    (d / "x.log").write_bytes(b"dump: " + (h.upper() if up else h).encode() + b"\n")
                    rc, out, _ = self.cli(run_dir=d)
                    self.assertEqual(rc, 1, out)
                    self.assertIn("LEAK: x.log:1 key=PIXOS_WIFI_PSK form=hex", out)

    def test_b64_all_alignments(self):
        for pad in (0, 1, 2):
            for tail in (0, 1, 2):
                with self.subTest(pad=pad, tail=tail):
                    d = self.tmp / ("b%d%d" % (pad, tail))
                    d.mkdir()
                    data = base64.b64encode(os.urandom(pad) + self.pb + os.urandom(tail)) + b"\n"
                    (d / "x.log").write_bytes(data)
                    rc, out, _ = self.cli(run_dir=d)
                    self.assertEqual(rc, 1, out)
                    self.assertIn("LEAK: x.log:1 key=PIXOS_WIFI_PSK form=b64", out)

    def test_pmk_raw_and_hex(self):
        self.plant("a.bin", b"\0\1" + self.pmk + b"\2")
        self.plant("b.log", b"pmk=" + self.pmk.hex().encode() + b"\n")
        self.plant("c.log", b"pmk " + sep_hex(self.pmk.hex().upper(), ":").encode() + b"\n")
        rc, out, _ = self.cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("LEAK: a.bin:1 key=PIXOS_WIFI_PSK form=pmk-raw", out)
        self.assertIn("LEAK: b.log:1 key=PIXOS_WIFI_PSK form=pmk-hex", out)
        self.assertIn("LEAK: c.log:1 key=PIXOS_WIFI_PSK form=pmk-hex", out)
        self.assertEqual(out.strip().splitlines()[-1], "RESULT: FAIL leaks=3 files=3")

    def test_line_number_and_nested_file(self):
        self.plant("sub/deep/x.log", b"a\nb\nc " + self.pb + b"\nd\n" + self.pb + b"\n")
        rc, out, _ = self.cli()
        self.assertEqual(rc, 1)
        self.assertIn("LEAK: sub/deep/x.log:3 key=PIXOS_WIFI_PSK form=raw\n", out)
        self.assertEqual(out.count("LEAK:"), 1)

    def test_clean_dir_passes(self):
        (self.run_dir / "a.log").write_text("nothing here\n" * 100)
        (self.run_dir / "b.bin").write_bytes(os.urandom(5000))
        rc, out, _ = self.cli()
        self.assertEqual((rc, out.strip().splitlines()[-1]), (0, "RESULT: PASS no secret in 2 files (skipped=0)"), out)

    def test_ssid_is_not_a_secret(self):
        self.plant("a.log", b"ssid=" + self.sb + b"\n")
        rc, out, _ = self.cli()
        self.assertEqual(rc, 0, out)

    def test_chunk_boundary(self):
        """The value is split across two read chunks (and across more than one for a tiny chunk): still found, right line."""
        for chunk in (7, 16, 64):
            for off in range(0, 70, 5):
                with self.subTest(chunk=chunk, off=off):
                    d = self.tmp / ("c%d_%d" % (chunk, off))
                    d.mkdir()
                    (d / "x.log").write_bytes(b"\n" * off + b"." * (chunk - off % chunk or chunk) + self.pb + b"\nZ\n")
                    r = xss.scan(str(d), str(self.env), ["PIXOS_WIFI_PSK"], chunk=chunk)
                    self.assertEqual(r["status"], "FAIL")
                    self.assertEqual(r["leaks"], [("x.log", off + 1, "PIXOS_WIFI_PSK", "raw")])

    def test_chunk_boundary_cli_size(self):
        """Real chunk size: the value straddles the 4 MiB read boundary."""
        pad = xss.CHUNK - len(self.pb) // 2
        self.plant("big.bin", b"a" * pad + self.pb + b"b" * 10)
        rc, out, _ = self.cli()
        self.assertEqual(rc, 1)
        self.assertIn("LEAK: big.bin:1 key=PIXOS_WIFI_PSK form=raw", out)


class RobustTest(Base):
    def test_symlink_not_followed_and_counted(self):
        outside = self.tmp / "outside.txt"
        outside.write_bytes(self.pb)
        os.symlink(outside, self.run_dir / "link.txt")
        os.symlink(self.tmp, self.run_dir / "linkdir")
        (self.run_dir / "a.log").write_text("x")
        rc, out, _ = self.cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("SKIP:"), 2, out)
        self.assertEqual(out.strip().splitlines()[-1], "RESULT: PASS no secret in 1 files (skipped=2)")

    def test_oversize_skip_is_loud(self):
        (self.run_dir / "big.bin").write_bytes(self.pb + b"0" * 100)
        (self.run_dir / "a.log").write_text("x")
        r = xss.scan(str(self.run_dir), str(self.env), ["PIXOS_WIFI_PSK"], max_file=50)
        self.assertEqual((r["status"], r["skipped"], r["files"]), ("PASS", 1, 1))
        self.assertIn("SKIP: big.bin", xss.render(r))
        self.assertIn("(skipped=1)", xss.render(r))

    def test_env_errors_exit_2_never_pass(self):
        def env(text):
            p = self.tmp / "e.env"
            p.write_text(text)
            return p
        (self.run_dir / "a.log").write_text("clean")
        cases = {
            "missing file": lambda: self.cli("--env", str(self.tmp / "nope.env"), env=False),
            "missing key": lambda: self.cli("--env", str(env("PIXOS_WIFI_SSID=abc\n")), env=False),
            "empty psk": lambda: self.cli("--env", str(env("PIXOS_WIFI_SSID=abc\nPIXOS_WIFI_PSK=\n")), env=False),
            "short psk": lambda: self.cli("--env", str(env("PIXOS_WIFI_SSID=abc\nPIXOS_WIFI_PSK=1234567\n")), env=False),
            "no ssid": lambda: self.cli("--env", str(env("PIXOS_WIFI_PSK=abcdefgh1234\n")), env=False),
            "bad utf8": lambda: self.cli("--env", str(self.write_bytes_env(b"PIXOS_WIFI_PSK=\xff\xfe\xfd\xfc\xfb\xfa\xf9\xf8\xf7\n")), env=False),
        }
        for name, fn in cases.items():
            with self.subTest(name):
                rc, out, err = fn()
                self.assertEqual(rc, 2, (out, err))
                self.assertEqual(out.strip().splitlines()[-1][:14], "RESULT: ERROR ", out)
                self.assertNotIn("Traceback", err)
        rc, out, _ = self.cli(run_dir=self.tmp / "no-such-dir")
        self.assertEqual(rc, 2, out)

    def write_bytes_env(self, b):
        p = self.tmp / "b.env"
        p.write_bytes(b)
        return p

    def test_64_hex_psk_is_the_pmk(self):
        pmk = os.urandom(32)
        e = self.tmp / "h.env"
        e.write_text("PIXOS_WIFI_PSK=%s\n" % pmk.hex())
        (self.run_dir / "a.bin").write_bytes(b"..." + pmk)
        r = xss.scan(str(self.run_dir), str(e), ["PIXOS_WIFI_PSK"])
        self.assertEqual(r["status"], "FAIL")
        self.assertIn(("a.bin", 1, "PIXOS_WIFI_PSK", "pmk-raw"), r["leaks"])

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_unreadable_file_is_an_error_without_message(self):
        p = self.plant("locked.log", b"x")
        os.chmod(p, 0)
        self.addCleanup(os.chmod, p, 0o600)
        rc, out, _ = self.cli()
        self.assertEqual(rc, 2, out)
        self.assertIn("UNREADABLE: locked.log PermissionError", out)

    def test_scan_exception_text_has_no_value(self):
        orig = xss.scan_file
        xss.scan_file = lambda *a, **k: (_ for _ in ()).throw(ValueError(self.psk))
        try:
            (self.run_dir / "a.log").write_text("x")
            r = xss.scan(str(self.run_dir), str(self.env), ["PIXOS_WIFI_PSK"])
        finally:
            xss.scan_file = orig
        self.assertEqual(r["status"], "ERROR")
        self.assertNotIn(self.psk, xss.render(r))
        self.assertIn("ValueError", xss.render(r))

    def test_outer_exception_text_has_no_value(self):
        orig = xss.build_needles
        xss.build_needles = lambda *a, **k: (_ for _ in ()).throw(ValueError(self.psk))
        try:
            r = xss.scan(str(self.run_dir), str(self.env), ["PIXOS_WIFI_PSK"])
        finally:
            xss.build_needles = orig
        self.assertEqual(r["status"], "ERROR")
        self.assertNotIn(self.psk, xss.render(r))
        self.assertIn("ValueError", xss.render(r))

    def test_other_key(self):
        e = self.tmp / "o.env"
        e.write_text("TOKEN=abc123XYZ\n")
        (self.run_dir / "a.log").write_text("here abc123XYZ")
        rc, out, _ = self.cli("--keys", "TOKEN", "--env", str(e), env=False)
        self.assertEqual(rc, 1)
        self.assertIn("LEAK: a.log:1 key=TOKEN form=raw", out)


class ReportTest(Base):
    def report(self, d, envfile):
        old = os.environ.get("PIXOS_ENV_FILE")
        os.environ["PIXOS_ENV_FILE"] = str(envfile)
        try:
            r = xrun_report.build(str(d))
            txt = xrun_report.render(r)
        finally:
            if old is None:
                del os.environ["PIXOS_ENV_FILE"]
            else:
                os.environ["PIXOS_ENV_FILE"] = old
        self.outputs += [txt.encode(), json.dumps(r).encode()]
        return r, txt

    def mk(self, name):
        return make_run(self.tmp / name, [(5, "SWEET: boot ok\n")], run={"expect": []})

    def test_leak_turns_result_into_fail(self):
        d = self.mk("leaky")
        (d / "udp.log").write_bytes(b"pmk iovar " + self.pmk.hex().encode() + b"\n")
        r, txt = self.report(d, self.env)
        self.assertEqual((r["result"], r["reason"]), ("FAIL", "secret-leak"))
        self.assertEqual(r["exit"], 1)
        self.assertIn("RESULT: FAIL secret-leak gaps=1", txt)
        self.assertIn("SECRET GAP: leaks=1\n", txt)
        self.assertIn("SECRET SCAN: RESULT: FAIL leaks=1\n", txt)
        self.assertEqual(r["secret"]["leaks"], 1)

    def test_clean_run_prints_the_pass_step(self):
        d = self.mk("clean")
        r, txt = self.report(d, self.env)
        self.assertEqual(r["secret"], {"status": "PASS", "leaks": 0, "files": r["secret"]["files"], "why": "skipped=0"})
        self.assertIn("SECRET SCAN: RESULT: PASS no secret in %d files (skipped=0)\n" % r["secret"]["files"], txt)     # T-632c: the step is in the summary
        self.assertNotIn("SECRET GAP", txt)
        self.assertIn("RESULT: PASS ", txt.splitlines()[0])
        self.assertNotEqual(r["reason"], "secret-leak")

    def test_no_psk_configured_prints_a_skip_line(self):
        d = self.mk("nopsk")
        (d / "udp.log").write_bytes(self.pb)
        for text in ("OTHER=1\n", "PIXOS_WIFI_PSK=\n"):
            e = self.tmp / "np.env"
            e.write_text(text)
            r, txt = self.report(d, e)
            self.assertEqual(r["secret"]["status"], "SKIP")
            self.assertIn("SECRET SCAN: skipped, no PIXOS_WIFI_PSK", txt)       # rule 8: a skipped step is visible, not a gap
            self.assertNotIn("SECRET GAP", txt)
            self.assertNotEqual(r["reason"], "secret-leak")
        r, txt = self.report(d, self.tmp / "absent.env")      # PIXOS_ENV_FILE naming a missing file: no env, skipped
        self.assertEqual(r["secret"]["status"], "SKIP")

    def test_scan_error_is_a_gap_and_hides_nothing(self):
        d = self.mk("err")
        e = self.tmp / "noss.env"
        e.write_text("PIXOS_WIFI_PSK=%s\n" % self.psk)      # PSK but no SSID: the PMK cannot be derived
        r, txt = self.report(d, e)
        self.assertEqual(r["secret"]["status"], "ERROR")
        self.assertIn("SECRET GAP: scan error\n", txt)
        self.assertIn("EXPECT:", txt)
        self.assertIn("MARKERS:", txt)
        self.assertNotEqual(r["reason"], "secret-leak")
        e.write_bytes(b"PIXOS_WIFI_PSK=\xff\xfe\n")           # unreadable (not utf-8) env
        r, txt = self.report(d, e)
        self.assertIn("SECRET GAP: scan error\n", txt)


class NoCacheTest(Base):
    def test_no_file_but_planted_contains_a_secret(self):
        self.plant("a.log", self.pb + b"\n")
        self.plant("b.log", self.pmk.hex().encode())
        self.cli()
        self.cli("--keys", "NOPE")
        d = self.mk_report()
        for p in self.tmp.rglob("*"):
            if p.is_file() and p not in self.planted and p != self.env:
                data = p.read_bytes()
                for s in self.secrets():
                    self.assertNotIn(s, data, "secret found in unplanted file %s" % p.name)

    def mk_report(self):
        d = make_run(self.tmp / "rep", [(5, "SWEET: boot ok\n")])
        old = os.environ.get("PIXOS_ENV_FILE")
        os.environ["PIXOS_ENV_FILE"] = str(self.env)
        try:
            xrun_report.build(str(d))
        finally:
            if old is None:
                del os.environ["PIXOS_ENV_FILE"]
            else:
                os.environ["PIXOS_ENV_FILE"] = old
        return d


if __name__ == "__main__":
    ok = unittest.main(exit=False, verbosity=1).result
    n = ok.testsRun
    bad = len(ok.failures) + len(ok.errors)
    print("RESULT: %s xrun-secret-scan tests=%d failures=%d" % ("FAIL" if bad else "PASS", n, bad))
    sys.exit(1 if bad else 0)
