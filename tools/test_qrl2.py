import importlib.util, os, pathlib, re, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import qrl2

try:
    import cv2, numpy  # noqa: F401
    HAVE_CV = True
except ImportError:
    sys.path.insert(0, os.path.expanduser("~/pixelios-artifacts/pylib"))
    try:
        import cv2, numpy  # noqa: F401,F811
        HAVE_CV = True
    except ImportError:   # lint-silent: expected opencv is optional: the tests that need it are skipUnless(HAVE_CV) and say so
        HAVE_CV = False
HAVE_GCC = shutil.which("gcc") is not None


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@unittest.skipUnless(HAVE_GCC, "needs gcc")
class Stream(unittest.TestCase):
    def test_round_trips_with_faults(self):
        qrl2.selftest(images=False, verbose=False)

    @unittest.skipUnless(HAVE_CV, "needs opencv + numpy")
    def test_rendered_two_tile_screens(self):
        qrl2.selftest(images=True, verbose=False)

    def test_c_encoder_under_asan_and_ubsan(self):
        d = tempfile.mkdtemp(prefix="qrl2-asan-")
        exe = os.path.join(d, "qsh")
        src = [str(ROOT / "qrlog" / f) for f in ("qrstream_host.c", "qrstream.c", "qrz.c", "qrlog.c", "qrcodegen.c")]
        subprocess.check_call(["gcc", "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-Wall", "-Wextra", "-Werror",
                               "-Wno-unused-parameter", "-I", str(ROOT / "qrlog"), "-o", exe] + src)
        log = b"".join((HERE / "testdata" / n).read_bytes() for n in ("qr-a.txt", "qr-b.txt", "qr-c.txt"))
        log += bytes(range(256)) * 20 + b"x" * 5000 + b"\n" + b"y" * 3000        # binary, a very long line, no final newline
        for args in (["--ver", "10"], ["--ver", "25", "--tiles", "3", "--klog", "2", "--unit", "1024"],
                     ["--ver", "6", "--ecc", "3", "--klog", "9", "--unit", "8192", "--grow", "333", "--flush-every", "7"]):
            r = subprocess.run([exe, "--frames", "400"] + args, input=log, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr.decode()[-500:])
            rx = qrl2.Receiver()
            for ln in r.stdout.decode().split("\n"):
                if ln and ln != "-":
                    rx.feed(ln)
            self.assertTrue(rx.streams)

    def test_binary_and_long_lines_survive(self):
        import qrsim
        log = b"line one\r\n" + b"A" * 5000 + b"\n\x00\x01\x02 control\n" + b"last line without newline"
        enc = qrsim.Encoder(log, ver=8)
        rx = qrl2.Receiver()
        for _ in range(300):
            f = enc.frame()
            if f:
                rx.feed(f)
        data, _ = rx.text(1)
        self.assertIn(b"line one\n", data)
        self.assertEqual(max(len(x) for x in data.split(b"\n")), 1024)     # a long line is split, never cut
        self.assertIn(b".. control", data.replace(b"\x01", b".").replace(b"\x02", b"."))
        self.assertNotIn(b"\r", data)


class Codec(unittest.TestCase):
    def test_base45_rfc9285_vectors(self):
        self.assertEqual(qrl2.b45decode("BB8"), b"AB")
        self.assertEqual(qrl2.b45decode("%69 VD92EX0"), b"Hello!!")
        self.assertEqual(qrl2.b45decode("QED8WEX0"), b"ietf!")
        for bad in ("a", "BB", "BBBB", "GGW", ":::"):
            with self.assertRaises(ValueError):
                qrl2.b45decode(bad)

    def test_tables_agree(self):
        import qrz_tables
        h = (ROOT / "qrlog" / "qrz_tables.h").read_text()
        dict_c = bytes(int(x) for x in re.search(r"qrz_dict\[\d+\] = \{(.*?)\};", h, re.S).group(1).replace("\n", "").replace("\t", "").split(",") if x.strip())
        self.assertEqual(dict_c, qrz_tables.DICT)
        lit = [int(x) for x in re.search(r"qrz_litlen_len\[286\] = \{(.*?)\};", h, re.S).group(1).replace("\n", "").replace("\t", "").split(",") if x.strip()]
        dist = [int(x) for x in re.search(r"qrz_dist_len\[30\] = \{(.*?)\};", h, re.S).group(1).replace("\n", "").replace("\t", "").split(",") if x.strip()]
        self.assertEqual((lit, dist), (qrz_tables.LITLEN_LEN, qrz_tables.DIST_LEN))
        for lens in (lit, dist):
            self.assertAlmostEqual(sum(2.0 ** -l for l in lens), 1.0)       # complete prefix code: zlib refuses anything else
        self.assertEqual(int(re.search(r"QRZ_DICT_LEN (\d+)u", h).group(1)), len(dict_c))

    def test_repair_masks(self):
        # C and Python agree on the subset a repair frame covers (the round-trip selftest decodes real repair frames); never empty
        self.assertTrue(all(qrl2.rep_mask(g, s, 16) for g in range(20) for s in range(20)))
        ones = sum(bin(qrl2.rep_mask(3, s, 32)).count("1") for s in range(200)) / 200
        self.assertTrue(12 < ones < 20, ones)       # dense: about half of the generation

    def test_frame_crc_rejects_a_flipped_character(self):
        self.assertIsNone(qrl2.parse_frame("0" * 30))
        self.assertIsNone(qrl2.parse_frame("not a frame"))


@unittest.skipUnless(shutil.which("clang") and shutil.which("git"), "needs clang and git")
class KernelSources(unittest.TestCase):
    """The committed patches 0081 + 0086 + 0094 reproduce the sources the generator builds from qrlog/, and they compile syntactically."""
    PATCHES = ("0081-sweet-qr-log", "0086-sweet-restart-poll", "0094-sweet-qr-ql2")

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="qrl2-kern-")
        for p in self.PATCHES:
            subprocess.run(["git", "apply", "--include=bsd/dev/sweet_qr*", "--whitespace=nowarn", str(ROOT / "xnu" / "patches" / (p + ".patch"))],
                           cwd=self.d, check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_patch_matches_the_generator(self):
        gen = load("make_qr2_patch", HERE / "make-qr2-patch.py")
        for rel, text in gen.new_files().items():
            self.assertEqual((pathlib.Path(self.d) / rel).read_text(), text, rel + ": run tools/make-qr2-patch.py again")
        thread = (ROOT / "xnu" / "qr" / "sweet_qr2_thread.c.in").read_text()
        self.assertTrue((pathlib.Path(self.d) / "bsd/dev/sweet_qr.c").read_text().endswith(thread), "sweet_qr.c thread part is stale: run tools/make-qr2-patch.py")

    def test_syntax_against_stub_headers(self):
        for f in ("sweet_qr.c", "sweet_qrstream.c", "sweet_qrz.c", "sweet_qrcodegen.c"):
            r = subprocess.run(["clang", "-fsyntax-only", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                                "-I", str(ROOT / "qrlog" / "stubs" / "xnu"), "-I", os.path.join(self.d, "bsd/dev"), os.path.join(self.d, "bsd/dev", f)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f + ":\n" + r.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
