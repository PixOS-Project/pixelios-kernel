"""R30 (tools/check_rules/r30_license.py): a clean synthetic tree passes, one planted violation per failure mode fails, the real tree is clean.
The temp trees are plain directories (no git), so the rule walks the directory."""
import pathlib, shutil, sys, tempfile, unittest
TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from check_rules import r30_license as r30

MAP = ("LICENSES/*\tMIT\ttexts\n"
       "xnu/patches/*\tAPSL-2.0\tapple-xnu\n"
       "qrlog/qrcodegen.*\tMIT\tnayuki qrcodegen\n"
       "shim/vendor/*\tMIT\tAOSP external/avb\n"
       "shim/sweet/*\tMIT\tios-sweet@067c9b3\n"
       "shim/*\tMIT\tpixos\n"
       "device/*\tMIT\tpixos\n"
       "tools/*\tMIT\tpixos\n")
LICS = ("MIT.txt", "APSL-2.0.txt", "MIT-qrcodegen.txt", "MIT-ios-sweet.txt", "ISC-bwfm.txt", "Spleen-BSD-2-Clause.txt",
        "MIT-AOSP-avbtool.txt", "MIT-BearSSL.txt")


def tree(files, mp=MAP, lics=LICS):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "tools").mkdir()
    (d / "tools" / "license-map.txt").write_text(mp)
    (d / "LICENSES").mkdir()
    for n in lics:
        (d / "LICENSES" / n).write_text("text\n")
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return d


def msgs(d):
    try:
        return [f.msg.split(":")[0] for f in r30.check(d)]
    finally:
        shutil.rmtree(d)


class Modes(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(msgs(tree({"shim/a.c": "// SPDX-License-Identifier: MIT\n// Copyright (c) 2026 PixOS-Project\n",
                                    "xnu/patches/0100-x.patch": "+ * Copyright (c) Apple Inc.\n"})), [])

    def test_unmapped_file(self):
        self.assertEqual(msgs(tree({"newdir/a.c": "int x;\n"})), ["R30 unmapped"])

    def test_map_licence_text_missing(self):
        self.assertEqual(msgs(tree({"shim/a.c": ""}, lics=("MIT.txt",))), ["R30 map-text"])

    def test_map_id_not_allowed(self):
        self.assertEqual(msgs(tree({"shim/a.c": ""}, mp=MAP + "docs/*\tGPL-2.0-only\tx\n", lics=LICS + ("GPL-2.0-only.txt",))), ["R30 map-id"])

    def test_map_syntax(self):
        self.assertEqual(msgs(tree({"shim/a.c": ""}, mp=MAP + "docs/*\tMIT\n")), ["R30 map-syntax"])

    def test_spdx_disagrees(self):
        self.assertEqual(msgs(tree({"shim/a.c": "/* SPDX-License-Identifier: GPL-2.0-only */\n"})), ["R30 spdx"])
        self.assertEqual(msgs(tree({"shim/a.c": "/* SPDX-License-Identifier: ISC */\n"})), ["R30 spdx"])

    def test_holder_disagrees(self):
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) 2016 Patrick Wildt\n"})), ["R30 holder"])
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) Apple Inc.\n"})), ["R30 holder"])
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) 2026 dipa-studio\n"})), ["R30 holder"])
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) Project Nayuki\n"})), ["R30 holder"])
        self.assertEqual(msgs(tree({"qrlog/qrcodegen.c": " * Copyright (c) Project Nayuki\n"})), [])

    def test_holder_cambus_and_aosp(self):
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) 2018, Frederic Cambus\n"})), ["R30 holder"])
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (C) 2008 The Android Open Source Project\n"})), ["R30 holder"])
        self.assertEqual(msgs(tree({"shim/vendor/a.py": "# Copyright (C) 2008 The Android Open Source Project\n"})), [])
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) 2016 Thomas Pornin\n"}, mp=MAP.replace("shim/*\tMIT", "shim/a.c\tMIT\tpornin\nshim/*\tMIT"))), [])

    def test_holder_unknown(self):
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) 2020 Some Corp\n"})), ["R30 holder-unknown"])
        self.assertEqual(msgs(tree({"shim/a.c": "// (c) 2020 Some Corp\n"})), ["R30 holder-unknown"])
        self.assertEqual(msgs(tree({"shim/a.c": "// \u00a9 Some Corp\n"})), ["R30 holder-unknown"])
        self.assertEqual(msgs(tree({"shim/a.c": "// (c) poll is bounded\n"})), [])  # list marker, not a notice
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) 1988 NeXT, Inc.\n"})), ["R30 holder"])

    def test_isc_boilerplate_is_not_a_holder(self):
        self.assertEqual(msgs(tree({"shim/a.c": " * copyright notice and this permission notice appear in all copies.\n"})), [])
        self.assertEqual(msgs(tree({"shim/a.c": " * Copyright 2020 Some Corp\n"})), ["R30 holder-unknown"])

    def test_notice_file_per_holder(self):
        lics = tuple(x for x in LICS if x != "MIT-ios-sweet.txt")
        self.assertEqual(msgs(tree({"shim/sweet/a.c": " * Copyright (c) 2026 dipa-studio\n"}, lics=lics)), ["R30 notice"])
        self.assertEqual(msgs(tree({"shim/sweet/a.c": " * Copyright (c) 2026 dipa-studio\n"})), [])

    def test_empty_licence_text(self):
        d = tree({"shim/a.c": ""})
        (d / "LICENSES" / "MIT.txt").write_text(" \n")
        self.assertEqual(msgs(d), ["R30 license-empty"])

    def test_catchall_map_row(self):
        self.assertEqual(msgs(tree({"shim/a.c": ""}, mp=MAP + "*\tMIT\tall\n")), ["R30 map-catchall"])

    def test_binary(self):
        d = tree({"shim/a.c": ""})
        (d / "shim" / "blob.dat").write_bytes(b"ab\0cd")
        self.assertEqual(msgs(d), ["R30 binary"])

    def test_banned_markers(self):
        for m in ("GNU General Public License", "Broadcom-WL-IPTag", "MODULE_LICENSE(\"GPL\");", "EXPORT_SYMBOL(x);", "PolyForm Noncommercial"):
            self.assertEqual(msgs(tree({"shim/a.c": f"// {m}\n"})), ["R30 banned"], m)
        self.assertEqual(msgs(tree({"docs/research/a.md": "GNU General Public License\n"}, mp=MAP + "docs/*\tMIT\tpixos\n")), [])

    def test_no_scan_is_narrow(self):
        self.assertEqual(msgs(tree({"docs/x.md": "EXPORT_SYMBOL\n"}, mp=MAP + "docs/*\tMIT\tpixos\n")), ["R30 banned"])

    def test_device_leaks(self):
        for body in ("[ 0.0] bootargs androidboot.serialno=ABC123XYZ foo\n", "IMEI: 356938035643809\n", "wlan0 mac 3c:28:6d:11:22:33\n"):
            self.assertEqual(msgs(tree({"device/google/bluejay/hardware/x.txt": body})), ["R30 device-leak"], body)
        for body in ("androidboot.serialno=<redacted>\n", "local mac 02:11:22:33:44:55, none 00:00:00:00:00:00\n", "strip the IMEI\n"):
            self.assertEqual(msgs(tree({"device/google/bluejay/hardware/x.txt": body})), [], body)
        self.assertEqual(msgs(tree({"shim/a.c": "androidboot.serialno=ABC123\n"})), [])  # only device/** is checked

    def test_forbidden_material(self):
        for rel in ("shim/fw.bin", "tools/boot.img", "tools/.env", "tools/x.ipsw", "tools/k.pem", "ipsw/readme.txt",
                    "tools/ipsw/readme.txt", "tools/a/Payload/x", "tools/dyld_shared_cache_arm64e", "tools/kernelcache.release.x", "tools/prod.env"):
            self.assertIn("R30 forbidden", msgs(tree({rel: "x"})), rel)


class Mutants(unittest.TestCase):
    """Disable one check: its planted violation must stop failing, so the test above would catch the mutant."""
    def test_each_check_is_needed(self):
        old = (r30.HOLDERS, r30.FORBIDDEN_NAMES, r30.SPDX)
        try:
            r30.HOLDERS = [(rx, None, None, None) for rx, *_ in old[0]]  # no id/origin/notice requirement
            self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) Apple Inc.\n"})), [])
            self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) 2018, Frederic Cambus\n"})), [])
            self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (C) The Android Open Source Project\n"})), [])
            r30.HOLDERS = old[0]
            r30.FORBIDDEN_NAMES = []
            self.assertEqual(msgs(tree({"shim/fw.bin": "x"})), [])
            r30.FORBIDDEN_NAMES = old[1]
            r30.SPDX = __import__("re").compile(r"NEVER")
            self.assertEqual(msgs(tree({"shim/a.c": "// SPDX-License-Identifier: GPL-2.0-only\n"})), [])
        finally:
            r30.HOLDERS, r30.FORBIDDEN_NAMES, r30.SPDX = old


class RealTree(unittest.TestCase):
    def test_repo_is_clean(self):
        self.assertEqual([(f.path, f.msg) for f in r30.check(TOOLS.parent)], [])


if __name__ == "__main__":
    unittest.main()
