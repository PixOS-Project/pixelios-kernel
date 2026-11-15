"""Host test of the (default-on)  /product/audio node (PIXOS_ADT_AUDIO). Run: python3 tools/test_mkadt_audio.py -> last line `RESULT: PASS mkadt-audio`.
Source: docs/research/ios-audio-route-no-codec.md s.3.3 / s.4.1; D22AP node = research/ipsw-x/d22ap.adt."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools import adt, mkadt  # noqa: E402


def tree(**kw):
    return adt.parse(adt.serialize(mkadt.build(seed=b"\0" * 64, board="bluejay", **kw)))[0]


def props(n):
    return dict((k, v) for k, _f, v in n.props)


def audio(root):
    prod = [c for c in root.children if props(c)["name"] == b"product\0"][0]
    return [c for c in prod.children if props(c)["name"] == b"audio\0"]


class Audio(unittest.TestCase):
    def test_default_on_and_zero_is_old_default(self):
        self.assertEqual(mkadt.audio_mode({}), "empty")
        self.assertEqual(mkadt.audio_mode({"PIXOS_ADT_AUDIO": ""}), "empty")
        self.assertEqual(mkadt.audio_mode({"PIXOS_ADT_AUDIO": "0"}), None)  # PIXOS_ADT_AUDIO=0 = the old default tree (no node, no GAP lines)
        self.assertEqual(audio(tree()), [])
        self.assertEqual(adt.serialize(mkadt.build(seed=b"\0" * 64, board="bluejay")), adt.serialize(mkadt.build(seed=b"\0" * 64, board="bluejay", audio=None)))
        self.assertEqual(mkadt.audio_gap_lines(None), [])

    def test_flag_adds_exactly_the_node(self):
        base = adt.serialize(mkadt.build(seed=b"\0" * 64, board="bluejay"))
        for mode in ("empty", "d22ap"):
            with self.subTest(mode=mode):
                n = audio(tree(audio=mode))
                self.assertEqual(len(n), 1)
                self.assertEqual(n[0].children, [])
                self.assertGreater(len(adt.serialize(mkadt.build(seed=b"\0" * 64, board="bluejay", audio=mode))), len(base))
                # every other node keeps its properties (the audio phandle is allocated last)
                a, b = tree(), tree(audio=mode)
                strip = lambda r: [[(k, v) for k, f, v in c.props] for c in r.children]
                self.assertEqual(strip(a), strip(b))

    def test_empty_has_no_hardware_props_d22ap_copies_two(self):
        self.assertEqual(sorted(k for k in props(audio(tree(audio="empty"))[0]) if k not in ("name", "AAPL,phandle")), [])
        if os.path.exists(mkadt.D22AP_ADT):
            p = props(audio(tree(audio="d22ap"))[0])
            self.assertEqual({k: p[k] for k in mkadt.AUDIO_PROPS}, {k: mkadt.u32(1) for k in mkadt.AUDIO_PROPS})

    def test_env_and_gap_lines(self):
        self.assertEqual(mkadt.audio_mode({"PIXOS_ADT_AUDIO": "1"}), "empty")
        self.assertEqual(mkadt.audio_mode({"PIXOS_ADT_AUDIO": "d22ap"}), "d22ap")
        with self.assertRaises(SystemExit):
            mkadt.audio_mode({"PIXOS_ADT_AUDIO": "yes"})
        lines = mkadt.audio_gap_lines("d22ap")
        self.assertEqual(len(lines), 2)
        for p, l in zip(mkadt.AUDIO_PROPS, lines):
            self.assertTrue(l.startswith(f"ADT GAP: /product/audio {p} copied from D22AP, no matching hardware on bluejay"))
        self.assertTrue(all(l.startswith("ADT GAP: /product/audio ") for l in mkadt.audio_gap_lines("empty")))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    ok = r.wasSuccessful()
    print("RESULT: %s mkadt-audio" % ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)
