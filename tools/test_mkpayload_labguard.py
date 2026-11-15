"""mkpayload refuses QEMU-lab markers (kernel lab patches 0299/0301-0303, super tools/qemu-lab/qadt.py) in a phone payload (user 2026-11-11)."""
import os, struct, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import adt, mkadt, mkpayload  # noqa: E402

PHONE_CMD = 'debug=0x8 serial=3 cpus=8 keepsyms=1 rd=ufsroot0 sweetdev=1'


def phone_root(board):
    return mkadt.build(board=board, wlan_node=board == 'bluejay', bt_node=True)


def vblk_node():
    n = adt.Node()
    n.props = [('name', 0, b'sweet-vblk\0'), ('compatible', 0, b'pixos,qemu-virtio-mmio\0'), ('reg', 0, struct.pack('<QQ', 0x0a000000, 0x4000))]
    return n


class LabGuard(unittest.TestCase):
    def test_phone_images_are_clean(self):
        for board in ('bluejay', 'sweet'):
            self.assertEqual(mkpayload.lab_markers(adt.serialize(phone_root(board)), PHONE_CMD), [], board)

    def test_vblk_node_caught(self):
        root = phone_root('bluejay'); root.children.append(vblk_node())
        marks = mkpayload.lab_markers(adt.serialize(root), PHONE_CMD)
        self.assertTrue(any('ADT node' in m and 'sweet-vblk' in m for m in marks), marks)
        self.assertTrue(any('pixos,qemu-virtio-mmio' in m for m in marks), marks)

    def test_renamed_vblk_node_caught_by_compatible(self):
        root = phone_root('bluejay'); n = vblk_node(); n.props[0] = ('name', 0, b'disk\0'); root.children.append(n)
        self.assertTrue(mkpayload.lab_markers(adt.serialize(root), PHONE_CMD))

    def test_psci_method_caught(self):
        root = phone_root('bluejay')
        cpus = next(c for c in root.children if c.name == 'cpus')
        cpus.children[0].props.append(('psci-method', 0, b'hvc\0'))
        marks = mkpayload.lab_markers(adt.serialize(root), PHONE_CMD)
        self.assertTrue(any('psci-method' in m for m in marks), marks)

    def test_lab_bootargs_caught(self):
        blob = adt.serialize(phone_root('bluejay'))
        for arg in ('rd=vblkroot0', 'sweetvinput=1'):
            self.assertTrue(mkpayload.lab_markers(blob, PHONE_CMD + ' ' + arg), arg)

    def test_build_has_lab_flag_default_off(self):
        import inspect
        p = inspect.signature(mkpayload.build).parameters['lab']
        self.assertIs(p.default, False)


if __name__ == '__main__':
    unittest.main()
