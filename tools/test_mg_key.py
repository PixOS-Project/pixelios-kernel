#!/usr/bin/env python3
"""tools/mg-key on a SYNTHETIC image: hand-encoded AArch64 shaped like a perfect-hash name lookup (different char positions, bases,
bound and stride than any Apple binary), so a hard-coded constant or a broken symbolic executor fails. No Apple binary is used."""
import importlib.machinery, importlib.util, os, struct, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "mg-key")
try:
    import capstone
except ImportError:   # lint-silent: expected capstone is optional: the tests that need it are skipUnless(capstone) and say so
    capstone = None

_l = importlib.machinery.SourceFileLoader("mgkey", TOOL)
mg = importlib.util.module_from_spec(importlib.util.spec_from_loader("mgkey", _l))
_l.exec_module(mg)

# ---- encoders (checked against capstone in test_encoders) ----
def adrp(d, pc, target): return 0x90000000 | (((target >> 12) - (pc >> 12)) & 3) << 29 | ((((target >> 12) - (pc >> 12)) >> 2) & 0x7ffff) << 5 | d
def add_i(d, n, imm): return 0x91000000 | imm << 10 | n << 5 | d
def add_r(d, n, m): return 0x8b000000 | m << 16 | n << 5 | d
def add_rl1(d, n, m): return 0x8b000000 | m << 16 | 1 << 10 | n << 5 | d
def ldrb(t, n, imm): return 0x39400000 | imm << 10 | n << 5 | t
def ldrh(t, n, imm): return 0x79400000 | (imm // 2) << 10 | n << 5 | t
def ldrh_rl1(t, n, m): return 0x78607800 | m << 16 | n << 5 | t
def cmp_i(n, imm): return 0x7100001f | imm << 10 | n << 5
def mov_i(d, imm): return 0x52800000 | imm << 5 | d
def madd(d, n, m, a): return 0x9b000000 | m << 16 | a << 10 | n << 5 | d
CMP22, RET = 0x7100583f, 0xd65f03c0

TEXT, DATA = 0x10000, 0x20000
H, TAB, STRIDE, BOUND = DATA + 0x100, DATA + 0x2000, 0x20, 40
POS = (7, 5, 3)  # chars used by the synthetic hash; Apple's differ
ADDS = (8, 2, 0)


def lookup_code(pc):
    w = [CMP22,
         ldrb(9, 0, POS[0]), adrp(10, pc + 8, H), add_i(10, 10, H & 0xfff), add_rl1(9, 10, 9), ldrh(9, 9, ADDS[0]),
         ldrb(11, 0, POS[1]), add_rl1(11, 10, 11), ldrh(11, 11, ADDS[1]), add_r(9, 9, 11),
         ldrb(8, 0, POS[2]), ldrh_rl1(10, 10, 8), add_r(9, 9, 10), add_i(9, 9, 3),
         cmp_i(9, BOUND), mov_i(10, STRIDE), adrp(11, pc + 4 * 16, TAB), add_i(11, 11, TAB & 0xfff), madd(19, 9, 10, 11), RET]
    return struct.pack("<%dI" % len(w), *w)


def build():
    """-> Image with one table entry per fixture key; returns (img, {name: (key, entry)})"""
    text = bytearray(0x1000)
    text[0:4 * 20] = lookup_code(TEXT)
    # compute fn for ModelNumber-like key: adrp/add of a C string "IODeviceTree:/" and "model-number", mov w2,#0, tail b out
    fn = TEXT + 0x200
    strs = {"IODeviceTree:/": 0x400, "model-number": 0x420}
    for s, o in strs.items():
        text[o:o + len(s) + 1] = s.encode() + b"\0"
    code = [adrp(0, fn, TEXT + 0x400), add_i(0, 0, 0x400), adrp(1, fn + 8, TEXT + 0x420), add_i(1, 1, 0x420), mov_i(2, 0),
            0x14000000 | ((-0x100 >> 2) & 0x3ffffff)]  # b backwards (tail call)
    text[0x200:0x200 + 4 * len(code)] = struct.pack("<%dI" % len(code), *code)
    data = bytearray(0x4000)
    for off in ADDS:  # u16 tables, small values so idx stays under BOUND
        for c in range(256):
            v = (c * (off + 1)) % 7
            struct.pack_into("<H", data, (H - DATA) + off + 2 * c, v)
    keys = {}
    for i, name in enumerate(["ModelNumber", "SerialNumber"]):
        k = mg.obfuscate(name)
        s = k.encode()
        idx = 3 + sum(struct.unpack_from("<H", data, (H - DATA) + off + 2 * s[pos])[0] for off, pos in zip(ADDS, POS))
        assert idx <= BOUND
        ent = TAB + idx * STRIDE
        kp = TEXT + 0x600 + 32 * i
        text[0x600 + 32 * i:0x600 + 32 * i + 23] = s + b"\0"
        struct.pack_into("<Q", data, ent - DATA, kp)
        struct.pack_into("<Q", data, ent - DATA + 8, fn)
        struct.pack_into("<Q", data, ent - DATA + 0x18, 0)
        struct.pack_into("<H", data, ent - DATA + 0x9a, 0xffff)
        keys[name] = (k, ent)
    return mg.Image([("__TEXT", TEXT, 0x1000, bytes(text)), ("__DATA_CONST", DATA, 0x4000, bytes(data))]), keys


def macho(img):
    """Minimal 64-bit Mach-O with the image's segments, to test the file loader + CLI."""
    body, cmds, off = b"", b"", 32 + 72 * len(img.segs)
    for nm, va, vs, buf in img.segs:
        cmds += struct.pack("<II16sQQQQiiII", 0x19, 72, nm.encode(), va, vs, off + len(body), len(buf), 7, 7, 0, 0)
        body += buf
    return struct.pack("<IIIIIIII", 0xfeedfacf, 0x0100000c, 0, 6, len(img.segs), len(cmds), 0, 0) + cmds + body


@unittest.skipUnless(capstone, "python3 capstone missing")
class T(unittest.TestCase):
    def test_obfuscation_vectors(self):  # strings from scratch/reports/about-identity.md (read from the iOS 16.7 dylib)
        self.assertEqual(mg.obfuscate("SerialNumber"), "VasUgeSzVyHdB27g2XpN0g")
        self.assertEqual(mg.obfuscate("ModelNumber"), "D0cJ8r7U5zve6uA6QbOiLA")
        self.assertEqual(mg.obfuscate("VasUgeSzVyHdB27g2XpN0g"), "VasUgeSzVyHdB27g2XpN0g")

    def test_encoders(self):
        md = mg.capstone()
        for w, txt in [(adrp(10, 0x10008, 0x20100), "adrp"), (add_rl1(9, 10, 9), "add x9, x10, x9, lsl #1"), (ldrh(9, 9, 8), "ldrh w9, [x9, #8]"),
                       (ldrh_rl1(10, 10, 8), "ldrh w10, [x10, x8, lsl #1]"), (cmp_i(9, 40), "cmp w9, #0x28"), (madd(19, 9, 10, 11), "madd x19, x9, x10, x11"),
                       (ldrb(9, 0, 7), "ldrb w9, [x0, #7]"), (mov_i(10, 0x20), "mov w10, #0x20")]:
            i = next(md.disasm(struct.pack("<I", w), 0x10008))
            self.assertTrue((i.mnemonic + " " + i.op_str).startswith(txt), (txt, i.mnemonic, i.op_str))

    def test_hash_parameters_are_read_from_code(self):
        img, _ = build()
        p = mg.find_lookup(img, mg.capstone())
        self.assertEqual((p["bound"], p["stride"], p["table"], p["const"]), (BOUND, STRIDE, TAB, 3))
        self.assertEqual(p["terms"], sorted((pos, H + off) for pos, off in zip(POS, ADDS)))
        self.assertEqual(p["func"], TEXT + 4 * 0)

    def test_resolve_and_source(self):
        img, keys = build()
        md = mg.capstone()
        p = mg.find_lookup(img, md)
        r = mg.resolve(img, md, p, "ModelNumber")
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["key"], r["entry"], r["fn"], r["precache"]), (keys["ModelNumber"][0], keys["ModelNumber"][1], TEXT + 0x200, 0xffff))
        self.assertEqual(r["strs"], ["IODeviceTree:/", "model-number"])
        self.assertEqual(r["calls"][0][1], 0)  # tail call recorded with w2 == 0
        self.assertFalse(mg.resolve(img, md, p, "NoSuchQuestion")["ok"])

    def test_cli(self):
        img, _ = build()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lib.dylib")
            with open(path, "wb") as f:
                f.write(macho(img))
            run = lambda *a: subprocess.run([sys.executable, TOOL, *a, "--out", d], capture_output=True, text=True)
            r = run("--dylib", path, "SerialNumber", "ModelNumber")
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("RESULT: PASS keys=2", r.stdout)
            r = run("--dylib", path, "SerialNumber", "Nope")
            self.assertEqual((r.returncode, "RESULT: FAIL keys=2 notfound=1" in r.stdout), (1, True))
            r = run("--dsc", d + "/missing", "SerialNumber")
            self.assertEqual(r.returncode, 2)
            self.assertIn("RESULT: ERROR", r.stdout)


if __name__ == "__main__":
    unittest.main()
