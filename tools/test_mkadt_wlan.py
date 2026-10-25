import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools import adt, boards, mkadt  # noqa: E402


def _kids(n):
    return {dict((k, v) for k, _f, v in c.props)['name']: c for c in n.children}


class WlanAdt(unittest.TestCase):
    def tree(self, **kw):
        return adt.parse(adt.serialize(mkadt.build(seed=b"\0" * 64, board="sweet", **kw)))[0]

    def test_mac_addresses_locally_administered_and_distinct(self):
        props = dict((k, v) for k, _f, v in _kids(self.tree())[b"chosen\0"].props)
        macs = [props["mac-address-" + n] for n in ("wifi0", "bluetooth0", "ethernet0")]
        self.assertEqual(len(set(macs)), 3)
        for m in macs:
            self.assertEqual(len(m), 6)
            self.assertEqual(m[0] & 3, 2)  # locally administered, unicast

    def test_wlan_node_only_when_asked(self):
        self.assertNotIn(b"wlan\0", _kids(_kids(self.tree())[b"arm-io\0"]))
        self.assertIn(b"wlan\0", _kids(_kids(self.tree(wlan_node=True))[b"arm-io\0"]))


def wlan_node(board, **kw):
    root = adt.parse(adt.serialize(mkadt.build(seed=b"\0" * 64, board=board, **kw)))[0]
    for c in root.children:
        props = dict((k, v) for k, _f, v in c.props)
        if props["name"] == b"sweet-wlan\0":
            return props
    return None


class SweetWlanNode(unittest.TestCase):
    def test_bluejay_node_matches_soc_json(self):
        p = wlan_node("bluejay")
        w = boards.BOARDS["bluejay"]["wlan"]
        self.assertEqual(p["compatible"], b"google,gs101-bcm4389-pcie\0")
        self.assertEqual(p["reg"], mkadt.u64(int(w["mem"], 0), 0x10000))
        self.assertEqual(p["cfg"], mkadt.u64(int(w["cfg"], 0), 0x1000))
        self.assertEqual(p["atu"], mkadt.u64(int(w["atu"], 0), 0x1000))
        self.assertEqual(p["elbi"], mkadt.u64(int(w["elbi"], 0), 0x2000))
        self.assertEqual(p["pmu-iso"], mkadt.u64(int(w["pmu_pcie1_iso"], 0), 4))

    def test_pinned_values_of_the_xnu_driver(self):
        # bsd/dev/sweet_wlan.c refuses any other value (CR-19/CR-20): the observed bluejay addresses, and the SW_* pins of patch 0120 equal them
        w = boards.BOARDS["bluejay"]["wlan"]
        self.assertEqual([int(w[k], 0) for k in ("mem", "cfg", "atu", "elbi", "pmu_pcie1_iso")], [0x60000000, 0x60FFE000, 0x14B00000, 0x14520000, 0x17463EC4])
        patch = open(os.path.join(ROOT, "xnu", "patches", "0120-sweet-wlan-bwfm-core.patch")).read()
        pins = {k: int(v, 16) for k, v in re.findall(r"^\+#define SW_(MEM|CFG|ATU|ELBI|ISO) +(0x[0-9A-Fa-f]+)ull", patch, re.M)}
        self.assertEqual(pins, {"MEM": 0x60000000, "CFG": 0x60FFE000, "ATU": 0x14B00000, "ELBI": 0x14520000, "ISO": 0x17463EC4})

    def test_sweet_and_qemu_trees_have_no_wlan_node(self):
        self.assertIsNone(wlan_node("sweet"))
        self.assertIsNone(wlan_node("bluejay", qemu=True))


if __name__ == "__main__":
    unittest.main()
