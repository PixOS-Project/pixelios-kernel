#!/usr/bin/env python3
"""Pack an xnu kernel Mach-O + ADT into the payload the sweet shim boots.

  mkpayload.py KERNEL ADT OUT [--cmdline "..."] [--ramdisk IMAGE] [--wlanfw CONTAINER] [--wlancred BLOB]

The kernel is linked as a PIE with LC_DYLD_CHAINED_FIXUPS. Nobody else
(no kmutil, no iBoot) resolves those for us, and xnu only rebases static
kernelcaches itself, so the chains are walked here and every pointer is
rewritten to its unslid target. The shim then only copies flat segments.

Payload layout (little endian, all offsets from payload start):
  0x000  char  magic[8] = "SWEETXNU"
  0x008  u32   version = 3, nsegs   (3: cmdline[2048]; 2 had cmdline[1024], segs at 0x448; 1 had cmdline[608], segs at 0x2a8; shim/loader.c refuses any other)
  0x010  u64   virt_base      kernel VA that maps to phys_base
  0x018  u64   entry_va       LC_UNIXTHREAD pc
  0x020  u64   adt_off, adt_size
  0x030  u64   kernel_end_va  end of the highest segment (page aligned)
  0x038  u64   phys_base      kernel-managed RAM (same values as the ADT's dram-base/size)
  0x040  u64   mem_size
  0x048  char  cmdline[2048]  BOOT_LINE_LENGTH of xnu patch 0320 (0176: 1024), NUL-terminated (at most 2047 characters)
  0x848  seg[nsegs] { u64 vmaddr, vmsize, file_off(in payload), file_size }

--ramdisk appends IMAGE as one more segment right after the kernel and moves
kernel_end past it, so the shim needs no changes: the image lands below the
ADT and boot_args, inside topOfKernelData (never handed to the VM), and the
ADT gets /chosen/memory-map "RAMDisk" = <phys, size> for IOFindBSDRoot to
turn into md0 (boot with rd=md0). --wlanfw does the same for the BCM4389 container but names the
property WLANFW (page-rounded placement, <phys, exact size>), which nothing turns into a disk; the container is
validated (magic, CRCs) before it is packed. --wlancred (tools/mkwlancred.py, 128 bytes) is placed the same way as property WLANCRED
<phys, 128>; it is validated (magic, crc) and only `wlancred: present` is reported, never a phys address or any value.
"""
import argparse, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import adt, boards  # noqa: E402
from tools.pack_wlanfw import parse as wlanfw_parse  # noqa: E402
from tools.mkwlancred import parse as wlancred_parse  # noqa: E402

LC_SEGMENT_64, LC_UNIXTHREAD, LC_DYLD_CHAINED_FIXUPS = 0x19, 0x5, 0x80000034
PAGE = 0x4000
BOOT_LINE_LENGTH = 2048	# = shim/payload.h and xnu patch 0320 (on 0176) pexpert/pexpert/arm64/boot.h (tools/test_bootline_layout.py)
PAYLOAD_VERSION = 3	# = shim/payload.h PAYLOAD_VERSION
CMDLINE = 0x48
SEG_TABLE = CMDLINE + BOOT_LINE_LENGTH	# 0x848
KERNEL_TAG = b'SWEET_BOOT_LINE_LENGTH=%d\0' % BOOT_LINE_LENGTH	# xnu patch 0176 tag (follows the macro: 2048 with 0320), osfmk/arm64/arm_vm_init.c (review bootline-1024-r1 F1)


def parse_macho(buf):
    magic, cputype, cpusub, ftype, ncmds, sizeofcmds, flags, _ = struct.unpack_from('<8I', buf, 0)
    assert magic == 0xfeedfacf, 'not a 64-bit Mach-O'
    segs, entry, fixups = [], None, None
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', buf, off)
        if cmd == LC_SEGMENT_64:
            name = buf[off + 8:off + 24].split(b'\0')[0].decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<4Q', buf, off + 24)
            segs.append(dict(name=name, vmaddr=vmaddr, vmsize=vmsize, fileoff=fileoff, filesize=filesize))
        elif cmd == LC_UNIXTHREAD:
            flavor, count = struct.unpack_from('<II', buf, off + 8)
            # arm_thread_state64: x0..x28, fp, lr, sp, pc, cpsr
            entry, = struct.unpack_from('<Q', buf, off + 16 + 32 * 8)
        elif cmd == LC_DYLD_CHAINED_FIXUPS:
            fixups = struct.unpack_from('<II', buf, off + 8)	# dataoff, datasize
        off += size
    return dict(cputype=cputype, cpusub=cpusub & 0xff, segs=segs, entry=entry, fixups=fixups)


def apply_chained_fixups(buf, m):
    """Rewrite every chained rebase in buf (the whole file) to its plain target VA."""
    if not m['fixups']:
        return 0
    data_off, _ = m['fixups']
    _, starts_off, _, _, imports_count, _, _ = struct.unpack_from('<7I', buf, data_off)
    assert imports_count == 0, 'kernel has binds; only rebases are supported'
    image_base = m['segs'][0]['vmaddr']		# __TEXT, holds the mach header
    seg_count, = struct.unpack_from('<I', buf, data_off + starts_off)
    nfix = 0
    for i in range(seg_count):
        seg_info, = struct.unpack_from('<I', buf, data_off + starts_off + 4 + 4 * i)
        if not seg_info:
            continue
        base = data_off + starts_off + seg_info
        size, page_size, fmt, seg_offset, max_valid, page_count = struct.unpack_from('<IHHQIH', buf, base)
        starts = struct.unpack_from('<%dH' % page_count, buf, base + 22)
        seg = m['segs'][i]
        for p, start in enumerate(starts):
            if start == 0xffff:
                continue
            assert not start & 0x8000, 'DYLD_CHAINED_PTR_START_MULTI not supported'
            loc = seg['fileoff'] + p * page_size + start
            while True:
                raw, = struct.unpack_from('<Q', buf, loc)
                target, nxt, stride = decode(raw, fmt, image_base)
                struct.pack_into('<Q', buf, loc, target)
                nfix += 1
                if not nxt:
                    break
                loc += nxt * stride
    return nfix


def decode(raw, fmt, image_base):
    """Return (unslid target VA, next, stride) for one chained pointer."""
    if fmt in (2, 6):		# DYLD_CHAINED_PTR_64 / _64_OFFSET
        assert not raw >> 63, 'bind in kernel'
        target = raw & ((1 << 36) - 1)
        high8 = (raw >> 36) & 0xff
        nxt = (raw >> 51) & 0xfff
        if fmt == 6:
            target += image_base
        return target | (high8 << 56), nxt, 4
    if fmt == 8:		# DYLD_CHAINED_PTR_64_KERNEL_CACHE
        target = raw & ((1 << 30) - 1)
        nxt = (raw >> 51) & 0xfff
        return image_base + target, nxt, 4
    if fmt in (1, 7, 9, 12):	# arm64e variants (stock VMAPPLE kernel only; PAC is not re-signed)
        auth = raw >> 63
        nxt = (raw >> 51) & 0x7ff
        stride = 4 if fmt in (7, 12) else 8
        if auth:
            target = raw & 0xffffffff
        else:
            target = raw & ((1 << 43) - 1)
            target |= ((raw >> 43) & 0xff) << 56
        if fmt in (7, 9, 12) or auth:
            target += image_base
        return target, nxt, stride
    raise ValueError(f'unsupported chained pointer format {fmt}')


def add_ramdisk_to_adt(adt_blob, phys, size, name='RAMDisk'):
    """Return the ADT with /chosen/memory-map NAME = <phys, size> (two uintptr_t)."""
    root, _ = adt.parse(adt_blob)
    chosen = next(c for c in root.children if c.name == 'chosen')
    mmap = next(c for c in chosen.children if c.name == 'memory-map')
    assert mmap.prop(name) is None, f'ADT already has a {name}'
    mmap.props.append((name, 0, struct.pack('<2Q', phys, size)))
    return adt.serialize(root)


def adt_dram(adt_blob):
    """Kernel-managed RAM <base, size> from the ADT's /chosen dram-base/dram-size."""
    root, _ = adt.parse(adt_blob)
    chosen = next(c for c in root.children if c.name == 'chosen')
    return (struct.unpack('<Q', chosen.prop('dram-base'))[0],
            struct.unpack('<Q', chosen.prop('dram-size'))[0])


def check_cmdline(cmdline):
    """The boot-args line is a BOOT_LINE_LENGTH (2048) byte field incl. the NUL; refuse longer ones instead of cutting them silently."""
    cl = cmdline.encode()
    if len(cl) > BOOT_LINE_LENGTH - 1:
        sys.exit(f'mkpayload: cmdline is {len(cl)} bytes, the limit is {BOOT_LINE_LENGTH - 1}')
    return cl


# QEMU-lab markers (super repo tools/qemu-lab/qadt.py + kernel lab patches 0299/0301-0303 on branch lab/qemu-ios, never on trunk): a phone payload must never
# carry them (user 2026-11-11). Only the lab's own payload builder (qpay.py) passes lab=True.
LAB_NODE_NAMES = ('sweet-vblk',)
LAB_COMPATIBLE = (b'pixos,qemu-virtio-mmio',)
LAB_PROPS = ('psci-method',)
LAB_BOOTARGS = ('rd=vblkroot', 'sweetvinput')


def lab_markers(adt_blob, cmdline):
    """Every QEMU-lab marker in this ADT + boot-args line, as 'where: what' strings (empty = a phone-clean image)."""
    found = []
    def walk(n, path):
        here = path + '/' + n.name
        if n.name in LAB_NODE_NAMES:
            found.append(f'ADT node {here}')
        comp = n.prop('compatible') or b''
        for c in LAB_COMPATIBLE:
            if c in comp.split(b'\0'):
                found.append(f'ADT {here} compatible {c.decode()}')
        for k, _, _ in n.props:
            if k in LAB_PROPS:
                found.append(f'ADT {here} property {k}')
        for c in n.children:
            walk(c, here)
    root, _ = adt.parse(adt_blob)
    walk(root, '')
    for w in cmdline.split():
        for b in LAB_BOOTARGS:
            if w.startswith(b):
                found.append(f'boot-arg {w}')
    return found


def build(kernel_path, adt_path, cmdline, ramdisk_path=None, board='sweet', wlanfw_path=None, wlancred_path=None, lab=False):
    buf = bytearray(open(kernel_path, 'rb').read())
    if KERNEL_TAG not in buf:
        sys.exit(f'mkpayload: {kernel_path} lacks {KERNEL_TAG[:-1].decode()!r} (needs xnu patches 0176 + 0320): with another boot-line '
                 f'length it would read the {BOOT_LINE_LENGTH}-byte boot-args line of this shim as bootFlags/memSizeActual; rebuild the kernel')
    m = parse_macho(buf)
    nfix = apply_chained_fixups(buf, m)
    segs = [s for s in m['segs'] if s['vmsize']]
    text = m['segs'][0]
    assert text['name'] == '__TEXT' and text['fileoff'] == 0
    # virt_base: 32MB (16K-granule L2 block) below the mach header, so start.s's
    # block mapping that starts at the header's block begins exactly at phys_base.
    virt_base = text['vmaddr'] & ~(0x2000000 - 1)
    kernel_end = max(s['vmaddr'] + s['vmsize'] for s in segs)
    kernel_end = (kernel_end + PAGE - 1) & ~(PAGE - 1)

    adt_blob = open(adt_path, 'rb').read()
    if not lab:
        marks = lab_markers(adt_blob, cmdline)
        if marks:
            raise SystemExit('mkpayload: REFUSED: QEMU-lab markers in a phone payload (the lab patches must never boot on a phone; '
                             'only tools/qemu-lab/qpay.py builds lab payloads): ' + '; '.join(marks))
    # The ADT decides where the kernel's RAM is (mkadt.py --highmem).
    PHYS_BASE, MEM_SIZE = adt_dram(adt_blob)
    if not any(PHYS_BASE >= base and PHYS_BASE + MEM_SIZE <= base + size
               for base, size in boards.BOARDS[board]['dram_banks']):
        raise SystemExit(f'ADT RAM range does not fit board {board}')
    pieces = [(s['vmaddr'], s['vmsize'], buf[s['fileoff']:s['fileoff'] + s['filesize']]) for s in segs]
    ramdisk = None
    if ramdisk_path:
        image = open(ramdisk_path, 'rb').read()
        rd_size = (len(image) + PAGE - 1) & ~(PAGE - 1)
        rd_va = kernel_end
        kernel_end = rd_va + rd_size
        rd_pa = rd_va - virt_base + PHYS_BASE
        pieces.append((rd_va, rd_size, image))
        adt_blob = add_ramdisk_to_adt(adt_blob, rd_pa, rd_size)
        ramdisk = (hex(rd_pa), hex(rd_size))
    wlanfw = None
    if wlanfw_path:
        # BCM4389 firmware container (tools/pack_wlanfw.py): same placement as the RAM disk, own property so IOFindBSDRoot ignores it
        image = open(wlanfw_path, 'rb').read()
        wlanfw_parse(image)
        fw_size = (len(image) + PAGE - 1) & ~(PAGE - 1)
        fw_va = kernel_end
        kernel_end = fw_va + fw_size
        fw_pa = fw_va - virt_base + PHYS_BASE
        pieces.append((fw_va, fw_size, image))
        adt_blob = add_ramdisk_to_adt(adt_blob, fw_pa, len(image), 'WLANFW')
        wlanfw = (hex(fw_pa), hex(len(image)))
    wlancred = None
    if wlancred_path:
        # RAM-only credential blob (tools/mkwlancred.py, SPEC-WIFI TRANSPORT s.3): same placement as WLANFW, own property; no address or value is reported
        with open(wlancred_path, 'rb') as f:
            image = f.read()
        try:
            wlancred_parse(image)
        except ValueError as e:     # the text names the rule, never a value
            sys.exit(f'mkpayload: --wlancred: {e}')
        cr_size = (len(image) + PAGE - 1) & ~(PAGE - 1)
        cr_va = kernel_end
        kernel_end = cr_va + cr_size
        cr_pa = cr_va - virt_base + PHYS_BASE
        pieces.append((cr_va, cr_size, image))
        adt_blob = add_ramdisk_to_adt(adt_blob, cr_pa, len(image), 'WLANCRED')
        wlancred = 'present'

    hdr_size = SEG_TABLE + 32 * len(pieces)
    out = bytearray(hdr_size)
    seg_table = []
    for vmaddr, vmsize, data in pieces:
        off = len(out)
        out += data
        out += b'\0' * (-len(out) & 0xf)
        seg_table.append((vmaddr, vmsize, off, len(data)))
    adt_off = len(out)
    out += adt_blob
    out += b'\0' * (-len(out) & 0xf)

    struct.pack_into('<8sII', out, 0, b'SWEETXNU', PAYLOAD_VERSION, len(pieces))
    struct.pack_into('<7Q', out, 0x10, virt_base, m['entry'], adt_off, len(adt_blob), kernel_end,
                     PHYS_BASE, MEM_SIZE)
    cl = check_cmdline(cmdline)
    out[CMDLINE:CMDLINE + len(cl)] = cl
    for i, t in enumerate(seg_table):
        struct.pack_into('<4Q', out, SEG_TABLE + 32 * i, *t)
    info = dict(cputype=hex(m['cputype']), cpusub=m['cpusub'], fixups=nfix, virt_base=hex(virt_base),
                entry=hex(m['entry']), kernel_end=hex(kernel_end), ramdisk=ramdisk, wlanfw=wlanfw, wlancred=wlancred, size=len(out),
                segs=[(s['name'], hex(s['vmaddr']), hex(s['vmsize'])) for s in segs])
    return bytes(out), info


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('kernel'); ap.add_argument('adt'); ap.add_argument('out')
    ap.add_argument('--cmdline', default='debug=0xc serial=3 cpus=1 keepsyms=1')
    ap.add_argument('--ramdisk', help='root image for md0 (boot with rd=md0)')
    ap.add_argument('--wlanfw', help='BCM4389 firmware container (tools/pack_wlanfw.py) -> /chosen/memory-map WLANFW')
    ap.add_argument('--wlancred', help='credential blob (tools/mkwlancred.py) -> /chosen/memory-map WLANCRED')
    ap.add_argument('--board', choices=('sweet', 'bluejay'), default='sweet')
    a = ap.parse_args()
    blob, info = build(a.kernel, a.adt, a.cmdline, a.ramdisk, a.board, a.wlanfw, a.wlancred)
    open(a.out, 'wb').write(blob)
    for k, v in info.items():
        if k != 'segs':
            print(f'{k}: {v}')
    for s in info['segs']:
        print('  ', *s)
