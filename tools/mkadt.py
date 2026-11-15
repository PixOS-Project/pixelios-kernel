#!/usr/bin/env python3
"""Build the minimal Apple Device Tree that the SWEET xnu needs to boot.

Only what the early boot path actually reads (see NOTES.md); every MMIO
address comes from the sweet live FDT. Usage:  mkadt.py OUT.adt [--dump] [--qemu] [--highmem]
"""
import argparse, hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import adt, boards  # noqa: E402

BOARDS = boards.BOARDS

# Kernel-managed RAM: above every Qualcomm XPU carve-out (see NOTES.md)
PHYS_BASE = 0xb0000000
MEM_SIZE = 0x4d000000
# --highmem (phone only): the whole upper DDR bank. The live FDT memory node is
# <0x80000000 2G>, <0x1_00000000 0xfcc00000>; every fixed reserved-memory
# carve-out is in the low bank and the dynamic pools are limited below 4G.
HIGH_PHYS_BASE = 0x100000000
HIGH_MEM_SIZE = 0xfcc00000
# /arm-io children are relative to this; it must not be 0 (serial_init bails)
SOC_BASE = 0x01000000

GICD, GICD_SIZE = 0x17a00000, 0x10000
GICR, GICR_SIZE = 0x17a60000, 0x100000
FB, FB_SIZE = 0x9c000000, 0x1700000
SWEET_ECID = 0x5357454554000001  # 'SWEET' + 1, synthetic
FB_W, FB_H, FB_STRIDE = 1080, 2400, 1080 * 4
# Internal UFS host (ufshc@1d84000 "ufs_mem"); QEMU virt has none
UFS, UFS_SIZE = 0x01d84000, 0x3000
TIMEBASE = 19200000
VTIMER_INTID = 19
# QEMU virt (-M virt,gic-version=3 -cpu cortex-a76): GICD/GICR and the generic timer's CNTFRQ
QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080a0000, 62500000
QEMU_VTIMER_INTID = 27

BOOT_MANIFEST_HASH = hashlib.sha384(b'sweet boot manifest').digest()

# The iOS userland is the iPhone X (iPhone10,3 / D22AP) IPSW. MobileGestalt
# derives product type, device class, home-button-less UI, artwork etc. from
# the root identity and /product of the real D22AP device tree. That tree is
# an Apple artifact (kept out of git): extract it from the IPSW with
#   tools/remotezip.py URL DeviceTree.d22ap.im4p DIR
#   ipsw img4 im4p extract DIR/Firmware/all_flash/DeviceTree.d22ap.im4p -o d22ap.adt
D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../research/ipsw-x/d22ap.adt')


def d22ap_product(path=D22AP_ADT):
    """/product properties of the real D22AP tree, without the syscfg
    placeholders iBoot would fill in (flag bit set) and the node bookkeeping."""
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def fake_mac(kind):
    """Locally administered unicast MAC (02:xx...) derived from the synthetic ECID; kind 0/1/2 = wifi0/bluetooth0/ethernet0.
    Placeholder until the radio's own address is read from its firmware (docs/research/26 rows 2 and 7)."""
    e = struct.pack('<Q', SWEET_ECID)
    return bytes([0x02, e[0], e[1], e[2], e[3], (e[4] + kind) & 0xff])


def bt_gap_lines(bt_node=True):
    """`BT GAP:` lines (rule 8; tools/xrun_report.py counts `<AREA> GAP: ` at line start) for every BT ADT value mkadt cannot source.
    Keys follow docs/research/109-bt-conformance-matrix.md section 4 (M1-M5, M9); one line each, key first."""
    m9 = "BT GAP: bd-address mac-address-bluetooth0 is a synthetic mac (not the controller's) [mkadt]"
    if not bt_node:
        return ["BT GAP: node-bluetooth no IOService named bluetooth or marconi-bt published (mkadt --no-bt-node); bluetoothd exits 0", m9]
    mac = ':'.join('%02x' % b for b in fake_mac(1))
    return [
        "BT GAP: node-marconi-bt no IOService named marconi-bt published; bluetoothd falls back to the node named bluetooth [mkadt]",
        f"BT GAP: local-mac-address synthetic ({mac}) not the controller's real BD address (BT-S3 D-3 A) [mkadt]",
        "BT GAP: transport-encoding 3 (H4BC) is bluetoothd's own default, not measured on hardware; H4 (1) not tried [mkadt]",
        "BT GAP: transport-speed not published; default 2400000 bps will be requested [mkadt]",
        m9,
    ]


def bt_dt_node(ph):
    """The /bluetooth root child (BT-S3 e; docs/research/BT-S3-transport-decision.md D-1 = option a, BT-S3-blueTool-rows.md s.5 D-3 = A). bluetoothd/BlueTool search the IODeviceTree plane
    for an IOService named `marconi-bt`, then `bluetooth` (matrix M1/M2; bluetoothd 0x1000269ac, BlueTool 0x10003e33c) and read `local-mac-address` (M3, 6 bytes,
    non-zero, else "No bluetooth on this device." and exit 0), `transport-encoding` (M4, u32) and `transport-speed` (M5, u32, read from `bluetooth` only).
    A root child becomes an IOPlatformDevice nub under SweetPlatformExpert with this name (BT-S3-o12-o13.md O12, [src], not yet seen on hardware).
    Never add `kernel-only` or `AAPL,ignore` here (they hide the properties / the nub). The address is the synthetic one of /chosen mac-address-bluetooth0 (M9).
    `transport-encoding` 3 = H4BC, bluetoothd's own default when the key is absent (BT-S1-ios-side.md:35); decision O10: H4/H4BC both use termios mode 0.
    `transport-speed` is deliberately absent: no cited source for a value (bluetoothd then asks for 2400000 bps); logged by bt_gap_lines() (rule 7/8)."""
    return node('bluetooth', [ph(), ('local-mac-address', fake_mac(1)), ('transport-encoding', u32(3))])


AUDIO_PROPS = ('wireless-splitter', 'supports-always-listening')


def audio_mode(env=None):
    """PIXOS_ADT_AUDIO: unset or 1 = empty /product/audio node, no hardware properties (default since bt/defaults: VirtualAudio needs the node, mediaserverd crashed without it);
    0 = no node (the old default tree); d22ap = node with the two D22AP properties copied."""
    v = (os.environ if env is None else env).get('PIXOS_ADT_AUDIO', '')
    if v == '0':
        return None
    if v in ('', '1'):
        return 'empty'
    if v == 'd22ap':
        return 'd22ap'
    raise SystemExit(f"mkadt: GAP: PIXOS_ADT_AUDIO={v!r} is not 0, 1 or d22ap")


def audio_gap_lines(mode):
    """`ADT GAP:` lines (rule 7) for /product/audio, one per D22AP property: dropped ('empty') or copied ('d22ap') without matching hardware."""
    if not mode:
        return []
    if mode == 'empty':
        return [f"ADT GAP: /product/audio {p} omitted (D22AP has it, no matching hardware on bluejay); whether VirtualAudio needs it is unknown [mkadt]" for p in AUDIO_PROPS]
    return [f"ADT GAP: /product/audio {p} copied from D22AP, no matching hardware on bluejay" for p in AUDIO_PROPS]


def audio_node(ph, mode, path=D22AP_ADT):
    """/product/audio, read by VirtualAudio as IODeviceTree:/product/audio (string 0x52486c, 20+ readers, e.g. 0x13bca0..0x13caa0 and 0x377af4..0x3786d0;
    docs/research/ios-audio-route-no-codec.md s.3.3). Without the node mediaserverd crashed in a loop with VirtualAudio loaded (2026-10-17; crash site s.0 row 4, FAR 0x390).
    The D22AP node (research/ipsw-x/d22ap.adt) has exactly two properties, both u32 1: `wireless-splitter` and `supports-always-listening`.
    Neither describes anything a Pixel 6a has (no iPhone X wireless-audio splitter, no always-listening AOP audio path), so 'empty' publishes the node with no
    properties (the honest "no built-in codec" answer; absent flag = off is a guess, the reader was not decoded: docs s.6 U1) and 'd22ap' copies them as a fallback
    if the node alone does not stop the crash. Both modes print ADT GAP lines (audio_gap_lines)."""
    props = [ph()]
    if mode == 'd22ap':
        root, _ = adt.parse(open(path, 'rb').read())
        prod = [c for c in root.children if dict((k, v) for k, f, v in c.props).get('name') == b'product\0'][0]
        aud = [c for c in prod.children if dict((k, v) for k, f, v in c.props).get('name') == b'audio\0'][0]
        # COPIED from D22AP, no matching hardware on bluejay (ADT GAP printed at build time)
        props += [(k, v) for k, f, v in aud.props if k in AUDIO_PROPS]
    return node('audio', props)


def wlan_node_default(board, qemu):
    """The iOS-root boot (tools/xrun runs mkadt.py with no flag) carries the wlan node on a board that is not sweet (its tree stays unchanged) and not on a qemu tree."""
    return board != 'sweet' and not qemu


def product_overrides(props, config):
    """/product edits of the Pixel boards: product-description (About > Model Name, MG ArtworkTraits) and, from soc.json wifi_chipset, wifi-chipset (MobileGestalt WifiChipset,
    research 110 s.7 row WifiChipset 0x19c56d18c; SPEC-WIFI Part F W2). The D22AP tree says 4357 (iPhone X); a board without the soc.json key keeps it (nothing invented)."""
    out = [(k, v) for k, v in props if k != 'product-description'] + [('product-description', cstr('Pixel 6a'))]
    if config.get('wifi_chipset'):
        out = [(k, v) for k, v in out if k != 'wifi-chipset'] + [('wifi-chipset', cstr(str(config['wifi_chipset'])))]
    return out


def add_wdt_node(root, board='sweet', qemu=False):
    """bsd/dev/sweet_wdt.c (patch 0278 = port of 0193, docs/specs/SPEC-WDT.md SQ2): gs101 cluster-0 watchdog block (reg, 0x100 bytes; oracle R01) and the
    three PMU offsets it reads (R08: reset mask 0x1244 bit 2, counter enable 0x1220 bit 8; RST_STAT 0x3b44). Read-only except WTCON/WTDAT/WTCNT. The PMU
    base is the sweet-reset node's reg. Only boards whose soc.json names wdt_cl0_base get the node; sweet keeps its tree unchanged. Added after build()
    (lint-size: build() may not change): the phandle is the tree's largest + 1, the node goes right after /sweet-reset."""
    config = BOARDS[board]
    if qemu or 'wdt_cl0_base' not in config or 'pmu_base' not in config:
        return None
    def phandles(n):
        out = [struct.unpack('<I', v[:4])[0] for k, _, v in n.props if k == 'AAPL,phandle']
        for c in n.children:
            out += phandles(c)
        return out
    wdt = node('sweet-wdt', [
        ('AAPL,phandle', u32(max(phandles(root) or [0]) + 1)),
        ('compatible', cstr('google,gs101-wdt-cl0')),
        ('reg', u64(config['wdt_cl0_base'], config['wdt_cl0_size'])),
        ('pmu-rst-mask-off', u32(config['pmu_wdt_mask_off'])),
        ('pmu-cnt-en-off', u32(config['pmu_wdt_cnt_off'])),
        ('pmu-rst-stat-off', u32(config['pmu_rst_stat_off'])),
    ])
    names = [c.name for c in root.children]
    root.children.insert(names.index('sweet-reset') + 1 if 'sweet-reset' in names else len(names), wdt)
    return wdt


def add_chg_node(root, board='sweet', qemu=False):
    """bsd/dev/sweet_chg.c (xnu patch 0283, charger phase 1, docs/research/charger-max77759-gs101.md section 8 steps 1-2): the READ windows of the MAX77759 I2C bus
    bring-up (HSI2C12/USI12, sysreg_peric1, CMU_PERIC1, CMU_TOP, pad bank gpp23) and the register offsets read in each. soc.json "chg_probe" is the only table (sources
    cited there): `<window>-reg` = u64 (base, size), `w-<word>` = u32 offset inside its window. No write window exists: the driver has no store. Only boards whose
    soc.json has chg_probe get the node; every other board keeps its tree. Added after build() like add_wdt_node: the phandle is the tree's largest + 1, the node goes
    right after /sweet-wdt (else /sweet-reset)."""
    config = BOARDS[board]
    cp = config.get('chg_probe')
    if qemu or not cp:
        return None
    n = lambda v: int(v, 0) if isinstance(v, str) else v
    props = [('compatible', cstr('google,gs101-max77759-chg-probe'))]
    sizes = {}
    for name, (base, size) in cp['windows'].items():
        sizes[name] = n(size)
        props.append((name + '-reg', u64(n(base), n(size))))
    for word, (win, off) in cp['words'].items():
        if win not in sizes or n(off) % 4 or n(off) + 4 > sizes[win]:
            sys.exit('mkadt: GAP: soc.json chg_probe word %s: window %s unknown or offset %s outside it' % (word, win, off))
        props.append(('w-' + word, u32(n(off))))
    ci = config.get('chg_i2c')      # xnu patch 0284 (phase 2): the transfer registers of the window above; `i-<word>` = u32 offset
    if ci:
        for word, off in ci['words'].items():
            if ci['window'] not in sizes or n(off) % 4 or n(off) + 4 > sizes[ci['window']]:
                sys.exit('mkadt: GAP: soc.json chg_i2c word %s: window %s unknown or offset %s outside it' % (word, ci['window'], off))
            props.append(('i-' + word, u32(n(off))))
    def phandles(nd):
        out = [struct.unpack('<I', v[:4])[0] for k, _, v in nd.props if k == 'AAPL,phandle']
        for c in nd.children:
            out += phandles(c)
        return out
    chg = node('sweet-chg', [('AAPL,phandle', u32(max(phandles(root) or [0]) + 1))] + props)
    names = [c.name for c in root.children]
    at = names.index('sweet-wdt') + 1 if 'sweet-wdt' in names else (names.index('sweet-reset') + 1 if 'sweet-reset' in names else len(names))
    root.children.insert(at, chg)
    return chg


def chosen_node(ph, phys_base, mem_size, seed):
    """/chosen with its memory-map and asmb children (moved out of build() unchanged, bt/defaults: build() must not grow, lint-size)."""
    return node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('sweet-shim')),
        ('system-firmware-version', cstr('sweet-shim')),
        # iBoot's SHA-384 of the boot manifest; /sbin/mount -P 1 exits unless
        # it exists (bind mounts /private/preboot/<hex>/usr/standalone/firmware,
        # tolerating a missing directory). Fixed stand-in value.
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        # libimg4 (cryptexd) aborts "failed to get crypto algorithm" without
        # it; real A11+ device trees say sha2-384.
        ('crypto-hash-method', cstr('sha2-384')),
        # MobileGestalt's screen class: display-scale (u32, default 0) comes
        # from /chosen, the pixel size from IOMobileFramebuffer. Scale 0 makes
        # SpringBoard's FBSDisplayMode "infxinf@0x" invalid. 1080x2400 @3x =
        # 360x800 points (like iPhone X class devices).
        ('display-scale', u32(3)),
        # XNU derives IOPlatformUUID (gethostuuid) from these on arm; without
        # them CFPreferences cannot use kCFPreferencesCurrentHost (no ByHost
        # files), so e.g. DataMigration never records LastSystemVersion and
        # migrates on every boot. Synthetic ECID (not the phone's); chip-id
        # of the A11 that the iPhone X userland expects.
        ('unique-chip-id', struct.pack('<Q', SWEET_ECID)),
        ('chip-id', u32(0x8015)),
        # Security status as on a dev-fused, undemoted board (tuple 0,0,1,1):
        # MobileGestalt reads these from /chosen, and mobileactivationd's
        # DeviceType then takes its native "Hactivation" branch (state
        # Activated without an activation record; no SEP, no baseband here).
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
        # MobileGestalt WifiAddress/BluetoothAddress read these; without them each call waits 5 s for an
        # IONetworkController (docs/research/26 row 2, per ios-flame notes: installd took 5 s per bundle).
        ('mac-address-wifi0', fake_mac(0)),
        ('mac-address-bluetooth0', fake_mac(1)),
        ('mac-address-ethernet0', fake_mac(2)),
    ], [node('memory-map', [ph()]),
        # Local boot policy (kern_csr.c): SIP relaxed like `csrutil disable`
        # (lp-sip0: untrusted kexts, unrestricted FS, task_for_pid, kernel
        # debugger, dtrace, nvram), lp-sip1: unauthenticated (unsealed) root.
        # The iOS shared cache is in no trust cache, so dyld can only map it
        # with CSR_ALLOW_UNRESTRICTED_FS (vm_unix.c shared region checks).
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])


def build(seed=None, qemu=False, highmem=False, board='sweet', no_ufs=False, wlan_node=False, bt_node=True, audio=None):
    """audio: None (no node; the CLI default is 'empty' via audio_mode()) | 'empty' | 'd22ap', see audio_node(). qemu=True: same tree, but with QEMU virt's GIC addresses and CNTFRQ (the QEMU virt smoke runs were retired, T2)."""
    config = BOARDS[board]
    if board == 'sweet':
        phys_base, mem_size = (HIGH_PHYS_BASE, HIGH_MEM_SIZE) if highmem else (PHYS_BASE, MEM_SIZE)
        gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (GICD, GICR, TIMEBASE)
    else:
        phys_base, mem_size = config['xnu_ram']
        gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (config['gicd_base'], config['gicr_base'], config['timer_freq'])
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = chosen_node(ph, phys_base, mem_size, seed)

    # SWEET SMP: all eight cores in physical-core order (0-5 Cortex-A55,
    # 6-7 Cortex-A76), one homogeneous cluster. The kernel defaults to one
    # and uses cpus=N to select an initial prefix (N <= 8).
    # QEMU retains its original single-CPU tree: its MPIDRs differ from sweet.
    cpu_nodes = []
    for core in range(1 if qemu else len(config['cpu_mpidrs'])):
        # 'qcom,kryo470' is the IOKit personality key SweetCPU matches (KernelConfigTables), not a
        # hardware description: every board uses it so the kernel needs no per-board CPU match.
        compatible = 'qcom,kryo470'
        mpidr = core << 8 if board == 'sweet' else config['cpu_mpidrs'][core]
        # One homogeneous cluster, as sweet's working 8-core ADT (MAX_CPU_CLUSTERS is 1 in the
        # SWEET machine config; a real 4+2+2 topology would panic xnu).
        cluster = 0
        cluster_core = core
        cpu_nodes.append(node(f'cpu{core}', [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr(compatible) + cstr('ARM,v8')),
            ('reg', u32(mpidr)),  # actual MPIDR Aff1:Aff0, NOT logical ID
            ('cpu-id', u32(core)),   # descriptive: XNU assigns IDs by DT order
            ('cluster-id', u32(cluster)),
            ('cluster-type', u32(cluster)),
            ('die-cluster-id', u32(cluster)),
            ('cluster-core-id', u32(cluster_core)),
            ('l2-cache-id', u32(core)),  # distinct per-core L2 nodes in live FDT
            ('l3-cache-id', u32(0)),     # shared L3
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
            ('clock-frequency', u32(2304000000 if core >= 6 else 1804800000)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(1866000000)),
            ('peripheral-frequency', u32(timebase)),
            ('fixed-frequency', u32(timebase)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(QEMU_VTIMER_INTID if qemu else config['vtimer_intid'])),
        ('reg', u64(gicd - SOC_BASE, config['gicd_size'], gicr - SOC_BASE, config['gicr_size'])),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [gic,
        # No hardware: watchdogd checks IODeviceTree:/arm-io/wdt for no-pmu and
        # otherwise crashes trying to set the PMU boot stage (customer builds
        # ignore the -wdt_no_pmu boot-arg).
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        # Main display: IOMobileFramebuffer.framework counts /arm-io children
        # with this device_type and reads the canvas size from the node
        # (the presented iPhone X size, see SweetFramebuffer.cpp);
        # the kernel's AppleCLCD matches the compatible string.
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('sweet,display')),
            ('canvas-width', u32(1125)),
            ('canvas-height', u32(2436)),
        ])] + ([
        # Makes launchd start wifid (com.apple.wifid is limited to the 'wlan' device tree node, per ios-flame notes,
        # docs/research/26 row 1). build() is off by default; wlan_node_default() turns it on for the iOS-root boot (patch 0200 gives wifid its interface).
        node('wlan', [ph(), ('device_type', cstr('wlan')), ('compatible', cstr('sweet,wlan'))])] if wlan_node else []))

    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(config['fb_base'], config['fb_size'])),
        ('width', u32(config['fb_width'])),
        ('height', u32(config['fb_height'])),
        ('stride', u32(config['fb_stride'])),
    ])

    # bsd/dev/sweet_ufs.c: the UFS host ABL leaves running (absolute physical reg).
    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc' if board == 'sweet' else 'google,gs101-ufs')),
        ('reg', u64(config['ufs_base'], config['ufs_size'])),
    ])

    # pmap_compute_max_asids requires the node even when no overrides are set.
    # An absent pmap-max-asids property selects XNU's own MAX_ASIDS default.
    # No effaceable storage: keybagd then reads /private/var/keybags/systembag.kb
    # as a plain plist instead of decrypting it with the effaceable key.
    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    # MobileGestalt HomeButtonType = IODeviceTree:/buttons home-button-type
    # (D22AP: 2, no Home button; absent = 0 = Home-button device).
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    product_props = d22ap_product()
    # About > Model Name reads /product product-description (MG ArtworkTraits, docs about-identity report)
    if product_props:
        product_props = product_overrides(product_props, config)
    product = [node('product', [ph()] + product_props)] if product_props else []

    # bsd/dev/sweet_usb.c (patch 0082): the gs101 DWC3 and the DRAM range ABL keeps its event
    # buffer in. Only boards that name a USB core (soc.json usb_base) get it; sweet keeps its
    # hard-coded Qualcomm addresses and an unchanged tree.
    usb = node('sweet-usb', [
        ph(),
        ('compatible', cstr('google,gs101-dwusb3')),
        ('reg', u64(config['usb_base'], config['usb_size'])),
        ('dma-pool-range', u64(*config['usb_dma_range'])),
    ]) if 'usb_base' in config and not qemu else None

    # bsd/dev/sweet_restart.c (patch 0083): reset through the EL3 PMU SMC (docs/research/10). Only boards that
    # name a PMU (soc.json pmu_base) get the node; sweet keeps its tree unchanged.
    reset = node('sweet-reset', [
        ph(),
        ('compatible', cstr('google,gs101-pmu-reset')),
        ('reg', u64(config['pmu_base'], 0x10000)),
        ('mode-offset', u32(config['pmu_mode_off'])),
        ('swreset-offset', u32(config['pmu_swreset_off'])),
    ]) if 'pmu_base' in config and not qemu else None

    # bsd/dev/sweet_keys_gs101.c (patch 0087): the three buttons as GPIO DAT words. 'keys' = (sweet_hid_key index,
    # physical address of the bank's DAT register, pin) per key; board.json key_pins names bank, bank offset, pin,
    # soc.json gpio_alive_dat_off the DAT offset. Only boards that define key_pins get the node.
    keys = node('sweet-keys', [
        ph(),
        ('compatible', cstr('google,gs101-gpio-keys')),
        ('keys', u32(*[v for k, base, off, pin in config['key_pins']
                       for v in (k, base + off + config['gpio_alive_dat_off'], pin)])),
        ('active-low', u32(config['key_active_low'])),
    ]) if 'key_pins' in config and not qemu else None
    # bsd/dev/sweet_touch_gs101.c (patch 0088): the FTM5 touchscreen. Only boards whose board.json names a touch IC
    # (touch_chip_id) get the node; sweet keeps an unchanged tree. Pins are (bank register base, pin) u64 pairs; the
    # bank bases and the USI/SPI engine come from soc.json (sources cited there).
    def touch_node():
        usi = config['touch_usi']
        pin = lambda key: u64(config[config[key][0] + '_bank'], config[key][1])
        return node('sweet-touch', [
            ph(),
            ('compatible', cstr('google,gs101-ftm5-spi')),
            ('reg', u64(config['usi%d_spi_base' % usi], config['usi%d_spi_size' % usi])),
            ('usi-regs-offset', u32(config['usi_regs_offset'])),
            ('usi-mode-reg', u64(config['usi%d_mode_reg' % usi])),
            ('usi-mode-spi', u32(config['usi_mode_spi'])),
            ('usi-mode-mask', u32(config['usi_mode_mask'])),
            ('spi-pin-func', u32(config['touch_spi_pin_func'])),
            ('max-hz', u32(config['touch_max_hz'])),
            ('chip-id', u32(*config['touch_chip_id'])),
            ('max-coords', u32(*config['touch_max_coords'])),
            ('screen', u32(config['fb_width'], config['fb_height'])),
            ('pin-clk', pin('touch_pin_clk')),
            ('pin-mosi', pin('touch_pin_mosi')),
            ('pin-miso', pin('touch_pin_miso')),
            ('pin-cs', pin('touch_pin_cs')),
            ('pin-irq', pin('touch_pin_irq')),
            ('pin-reset', pin('touch_pin_reset')),
            ('pin-mux', pin('touch_pin_mux')),
        ] + cmu_props())
    # CMU_PERIC1 clock chain of USI11 (docs/research/19, soc.json cmu_peric1): offsets and bit numbers, read by the
    # driver's clock dump and its sweettouchclk sequence.
    def cmu_props():
        c = config.get('cmu_peric1')
        if not c:
            return []
        n = lambda v: int(v, 0) if isinstance(v, str) else v
        o, b = c['offsets'], c['bits']
        names = [('cmu-option', 'option'), ('cmu-mux-usi', 'mux_usi'), ('cmu-mux-bus', 'mux_bus'),
                 ('cmu-mux-bus-auto', 'mux_bus_auto'), ('cmu-mux-usi-auto', 'mux_usi_auto'), ('cmu-div', 'div'),
                 ('cmu-gate-ipclk', 'gate_ipclk'), ('cmu-gate-pclk', 'gate_pclk'), ('cmu-gate-rstn', 'gate_rstn'),
                 ('cmu-qch', 'qch')]
        bits = [('cmu-mux-sel-bit', 'mux_sel'), ('cmu-mux-busy-bit', 'mux_busy'), ('cmu-gate-manual-bit', 'gate_manual'),
                ('cmu-gate-val-bit', 'gate_val'), ('cmu-gate-auto-bit', 'gate_auto'),
                ('cmu-qch-enable-bit', 'qch_enable'), ('cmu-qch-req-bit', 'qch_req')]
        t = c.get('top')    # CMU_TOP PERIC1_IP chain (docs/research/19 section 1, read from source)
        top = [('cmutop-reg', u64(n(t['base']), n(t['size']))), ('cmutop-mux', u32(n(t['mux']))),
               ('cmutop-div', u32(n(t['div']))), ('cmutop-gate', u32(n(t['gate']))),
               ('cmutop-gate-bit', u32(n(t['gate_bit'])))] if t else []
        return ([('cmu-reg', u64(n(c['base']), n(c['size'])))] + [(p, u32(n(o[k]))) for p, k in names] +
                [(p, u32(n(b[k]))) for p, k in bits] + top)
    touch = touch_node() if 'touch_chip_id' in config and not qemu else None

    # bsd/dev/sweet_wlan.c (patches 0124, 0129): the BCM4389 behind the PCIe1 link the shim left up (--wlan-hold). All addresses come from soc.json
    # wlan (sources cited there): reg = BAR0 ATU window (mem, 64 KiB), cfg = endpoint config space, atu = DBI ATU block, elbi = PCIe1 ELBI (link state), pmu-iso = PHY isolation word.
    # Only boards whose soc.json has a wlan block with mem/cfg get the node; the driver additionally refuses addresses other than the pinned ones.
    w = config.get('wlan')
    wlan = node('sweet-wlan', [
        ph(),
        ('compatible', cstr('google,gs101-bcm4389-pcie')),
        ('reg', u64(int(w['mem'], 0), 0x10000)),
        ('cfg', u64(int(w['cfg'], 0), 0x1000)),
        ('atu', u64(int(w['atu'], 0), 0x1000)),
        ('elbi', u64(int(w['elbi'], 0), 0x2000)),
        ('pmu-iso', u64(int(w['pmu_pcie1_iso'], 0), 4)),
    ] + (
        # xnu patch 0129 (W-D3 step 2, docs/research/43): tcm = BAR2 (the chip's 4 MiB backplane window, CPU range), atu-r2-off = outbound iATU region 2
        # inside the atu block. The driver pins both, like the five above, and refuses any other value.
        [('tcm', u64(int(w['tcm'], 0), int(w['tcm_bytes'], 0))), ('atu-r2-off', u32(int(w['atu_r2_off'], 0)))]
        if all(k in w for k in ('tcm', 'tcm_bytes', 'atu_r2_off')) else []) + (
        # xnu patch 0174 (Wi-Fi G2, docs/research/107 #3): RC DBI for the Command store, pinned by the driver.
        [('dbi', u64(int(w['dbi'], 0), 0x1000))] if 'dbi' in w else [])
    ) if w and all(k in w for k in ('mem', 'cfg', 'atu', 'elbi', 'pmu_pcie1_iso')) and not qemu else None

    # Identity of the iPhone X whose userland runs here: compatible[0] is
    # hw.model/hw.target (D22AP), model is hw.machine/hw.product
    # (iPhone10,3). 'xiaomi,sweet' stays for SweetPlatformExpert matching.
    dt = node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('xiaomi,sweet') + cstr('qcom,sm7150') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        # About > Serial Number / Model Number: IOPlatformExpert serial-number -> IOPlatformSerialNumber,
        # MG ModelNumber = root model-number (region-info stays absent so the row is exactly this text). Fake serial, never a real one.
        ('serial-number', cstr('PixOS-Project/pixelios')),
        ('model-number', cstr('bluejay')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + ([] if (qemu or no_ufs) else [ufs]) + ([usb] if usb else []) + ([reset] if reset else []) + ([keys] if keys else []) + ([touch] if touch else []) + ([wlan] if wlan else []) + ([bt_dt_node(ph)] if bt_node else []))
    # /product/audio (default on, PIXOS_ADT_AUDIO=0 = off): built after the whole tree so no other phandle moves; PIXOS_ADT_AUDIO=0 is byte-identical to the old default tree.
    if audio and product:
        product[0].children.append(audio_node(ph, audio))

    return dt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--dump', action='store_true')
    parser.add_argument('--qemu', action='store_true')
    parser.add_argument('--highmem', action='store_true')
    parser.add_argument('--no-ufs', action='store_true', help='omit the /sweet-ufs node (no root device yet: the UFS driver must not probe)')
    parser.add_argument('--board', choices=BOARDS, default='sweet')
    parser.add_argument('--no-wlan-node', action='store_true', help='omit the wlan node (wifid then is not started by launchd); default: present on every board but sweet and on non-qemu trees')
    parser.add_argument('--no-bt-node', action='store_true', help='omit the /bluetooth node (bluetoothd then exits 0: "No bluetooth on this device.")')
    parser.add_argument('--cpu-order', help='comma list of physical core numbers in /cpus DT order (a permutation starting with 0 = boot CPU); '
                       'only the child order changes, every property keeps its value. --intent gpu passes 0,6,7,4,5,1,2,3 (sprint-gpu nav19+: with cpus=5 the kernel picks '
                       'the boot A55 + the two A76 + the two X1 first)')
    args = parser.parse_args()
    root = build(qemu=args.qemu, highmem=args.highmem, board=args.board, no_ufs=args.no_ufs,
                 wlan_node=wlan_node_default(args.board, args.qemu) and not args.no_wlan_node, bt_node=not args.no_bt_node, audio=audio_mode())
    add_wdt_node(root, args.board, args.qemu)
    add_chg_node(root, args.board, args.qemu)
    for line in bt_gap_lines(not args.no_bt_node) + audio_gap_lines(audio_mode()):
        print(line)
    if args.cpu_order:
        order = [int(x) for x in args.cpu_order.split(',')]
        cpus_node = [c for c in root.children if c.name == 'cpus'][0]
        byname = {c.name: c for c in cpus_node.children}
        if sorted(order) != list(range(len(byname))) or order[0] != 0:
            sys.exit(f'mkadt: GAP: --cpu-order {args.cpu_order} is not a permutation of 0..{len(byname) - 1} starting with 0')
        cpus_node.children = [byname['cpu%d' % i] for i in order]
    blob = adt.serialize(root)
    open(args.output, 'wb').write(blob)
    if args.dump:
        adt.dump(adt.parse(blob)[0])
    print(f'{args.output}: {len(blob)} bytes', file=sys.stderr)
