import os, struct, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
import pack_wlanfw as p
import mkpayload
from tools import adt

BLOBS = [(b'FW  ', bytes(range(251)) * 3), (b'NVRM', b'a=1\0b=2\0\0'), (b'CLM ', b'\x01' * 17)]


class Container(unittest.TestCase):
    def test_roundtrip_and_alignment(self):
        c = p.pack(BLOBS)
        self.assertEqual(len(c) % 8, 0)
        self.assertEqual(p.parse(c), dict(BLOBS))
        for i in range(3):
            self.assertEqual(struct.unpack_from('<I', c, 16 + 16 * i + 8)[0] % 8, 0)

    def test_bit_flip_in_any_blob_is_refused(self):
        c = bytearray(p.pack(BLOBS))
        for i in range(3):
            off, = struct.unpack_from('<I', c, 16 + 16 * i + 8)
            d = bytearray(c); d[off] ^= 1
            with self.assertRaises(ValueError):
                p.parse(bytes(d))

    def test_structure_errors(self):
        c = p.pack(BLOBS)
        for bad in (c[:-9], b'WLANFWB2' + c[8:], c[:8] + struct.pack('<II', 2, 3) + c[16:], c[:8] + struct.pack('<II', 1, 0) + c[16:],
                    c[:8] + struct.pack('<II', 1, 9) + c[16:]):
            with self.assertRaises(ValueError):
                p.parse(bad)
        d = bytearray(c); struct.pack_into('<I', d, 16 + 8, 12)	# unaligned offset
        with self.assertRaises(ValueError):
            p.parse(bytes(d))
        d = bytearray(c); struct.pack_into('<I', d, 16 + 4, len(c))	# length past the end
        with self.assertRaises(ValueError):
            p.parse(bytes(d))

    def test_pack_refuses(self):
        for bad in ([(b'XXXX', b'1')], [(b'FW  ', b'')], [(b'FW  ', b'1'), (b'FW  ', b'2')]):
            with self.assertRaises(ValueError):
                p.pack(bad)


def tiny_adt():
    def node(name, children=()):
        n = adt.Node(); n.props.append(('name', 0, name.encode() + b'\0')); n.children.extend(children); return n
    return adt.serialize(node('device-tree', [node('chosen', [node('memory-map')])]))


class AdtProperty(unittest.TestCase):
    def test_named_property_added_once(self):
        blob = mkpayload.add_ramdisk_to_adt(tiny_adt(), 0x880000000, 0x1b92f0, 'WLANFW')
        root, _ = adt.parse(blob)
        mm = root.children[0].children[0]
        self.assertEqual(mm.prop('WLANFW'), struct.pack('<2Q', 0x880000000, 0x1b92f0))
        self.assertIsNone(mm.prop('RAMDisk'))
        with self.assertRaises(AssertionError):
            mkpayload.add_ramdisk_to_adt(blob, 1, 2, 'WLANFW')


if __name__ == '__main__':
    unittest.main()
