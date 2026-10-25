"""R30 (tools/check_rules/r30_license.py): a clean synthetic tree passes, one planted violation per failure mode fails, the real tree is clean.
The temp trees are plain directories (no git), so the rule walks the directory."""
import pathlib, shutil, sys, tempfile, unittest
TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from check_rules import r30_license as r30

MAP = ("LICENSES/*\tMIT\ttexts\n"
       "xnu/patches/*\tAPSL-2.0\tapple-xnu\n"
       "qrlog/qrcodegen.*\tMIT\tnayuki qrcodegen\n"
       "shim/*\tMIT\tpixos\n"
       "tools/*\tMIT\tpixos\n")
LICS = ("MIT.txt", "APSL-2.0.txt")


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

    def test_forbidden_material(self):
        for rel in ("shim/fw.bin", "tools/boot.img", "tools/.env", "tools/x.ipsw", "tools/k.pem", "ipsw/readme.txt"):
            self.assertIn("R30 forbidden", msgs(tree({rel: "x"})), rel)


class Mutants(unittest.TestCase):
    """Disable one check: its planted violation must stop failing, so the test above would catch the mutant."""
    def test_each_check_is_needed(self):
        old = (r30.HOLDERS, r30.FORBIDDEN_NAMES, r30.SPDX)
        try:
            r30.HOLDERS = []
            self.assertEqual(msgs(tree({"shim/a.c": " * Copyright (c) Apple Inc.\n"})), [])
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
