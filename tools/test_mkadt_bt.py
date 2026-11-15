"""Host test of the BT node mkadt emits (ticket T-80e3, BT-S3 e). Run: python3 tools/test_mkadt_bt.py -> last line `RESULT: PASS bt-adt-node`.
Sources: docs/research/109-bt-conformance-matrix.md rows M1-M5, M9; BT-S3-transport-decision.md (D-1: option a, H4/H4BC, mode 0);
BT-S3-blueTool-rows.md section 5 (D-3: synthetic locally administered address on node `bluetooth`); BT-S3-o12-o13.md (root child = nub with that name)."""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools import adt, boards, mkadt  # noqa: E402

GAP_RE = re.compile(r"^BT GAP: (\S+) ")  # tools/xrun_report.py counts `<AREA> GAP: ` at line start (matrix section 0)


def tree(board, **kw):
    return adt.parse(adt.serialize(mkadt.build(seed=b"\0" * 64, board=board, **kw)))[0]


def props(n):
    return dict((k, v) for k, _f, v in n.props)


def bt_nodes(root):
    return [props(c) for c in root.children if props(c).get("name") in (b"bluetooth\0", b"marconi-bt\0")]


class BtNode(unittest.TestCase):
    def test_node_and_properties_in_dumped_adt(self):
        for board in ("sweet", "bluejay"):
            with self.subTest(board=board):
                nodes = bt_nodes(tree(board))
                # one node, named `bluetooth`: bluetoothd tries `marconi-bt` first and falls back to it (M1/M2); `transport-speed` is read from it only (M2)
                self.assertEqual([n["name"] for n in nodes], [b"bluetooth\0"])
                p = nodes[0]
                self.assertEqual(p["transport-encoding"], mkadt.u32(3))  # M4: H4BC = bluetoothd's own default, mode-0 termios (decision O10)
                mac = p["local-mac-address"]
                self.assertEqual(len(mac), 6)  # M3
                self.assertNotEqual(mac, bytes(6))  # all zero => errx "No bluetooth on this device."
                self.assertEqual(mac[0] & 3, 2)  # D-3: locally administered unicast, synthetic
                for forbidden in ("kernel-only", "AAPL,ignore"):  # O12: either would hide the properties / the nub
                    self.assertNotIn(forbidden, p)

    def test_address_is_the_synthetic_one_of_mobilegestalt_and_not_a_wifi_mac(self):
        root = tree("bluejay")
        chosen = next(props(c) for c in root.children if props(c)["name"] == b"chosen\0")
        self.assertEqual(bt_nodes(root)[0]["local-mac-address"], chosen["mac-address-bluetooth0"])  # M9: one address everywhere
        self.assertNotEqual(chosen["mac-address-bluetooth0"], chosen["mac-address-wifi0"])  # D-3

    def test_transport_speed_not_invented(self):
        # No cited source for a speed (gs101 UART driver absent; Android 4 Mbaud is a firmware-download speed): rule 7 -> not published, GAP line instead (M5)
        self.assertNotIn("transport-speed", bt_nodes(tree("bluejay"))[0])

    def test_node_can_be_left_out(self):
        self.assertEqual(bt_nodes(tree("bluejay", bt_node=False)), [])

    def test_gap_lines(self):
        lines = mkadt.bt_gap_lines()
        keys = [m.group(1) for m in map(GAP_RE.match, lines)]
        self.assertNotIn(None, keys, lines)  # every line starts with `BT GAP: <key> `
        # M1 (marconi-bt not published), M3 (synthetic address), M4 (encoding is a default, not measured), M5 (speed), M9 (mac-address-bluetooth0 synthetic)
        self.assertEqual(keys, ["node-marconi-bt", "local-mac-address", "transport-encoding", "transport-speed", "bd-address"])
        self.assertTrue(all("\n" not in l for l in lines))
        self.assertIn("synthetic", lines[1])
        self.assertIn("02:", lines[1])
        self.assertIn("2400000", lines[3])
        self.assertEqual(mkadt.bt_gap_lines(bt_node=False), [
            "BT GAP: node-bluetooth no IOService named bluetooth or marconi-bt published (mkadt --no-bt-node); bluetoothd exits 0",
            "BT GAP: bd-address mac-address-bluetooth0 is a synthetic mac (not the controller's) [mkadt]"])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    ok = r.wasSuccessful()
    print("RESULT: %s bt-adt-node" % ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)
