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


def build(seed=None, qemu=False, highmem=False, board='sweet', no_ufs=False, wlan_node=False):
    """qemu=True: same tree, but with QEMU virt's GIC addresses and CNTFRQ (the QEMU virt smoke runs were retired, T2)."""
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

    chosen = node('chosen', [
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
        # docs/research/26 row 1). Off by default: no IO80211 stand-in exists yet, so wifid would only fail and burn CPU.
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
        product_props = [(k, v) for k, v in product_props if k != 'product-description'] + [('product-description', cstr('Pixel 6a'))]
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

    # Identity of the iPhone X whose userland runs here: compatible[0] is
    # hw.model/hw.target (D22AP), model is hw.machine/hw.product
    # (iPhone10,3). 'xiaomi,sweet' stays for SweetPlatformExpert matching.
    return node('device-tree', [
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
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + ([] if (qemu or no_ufs) else [ufs]) + ([usb] if usb else []) + ([reset] if reset else []) + ([keys] if keys else []) + ([touch] if touch else []))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--dump', action='store_true')
    parser.add_argument('--qemu', action='store_true')
    parser.add_argument('--highmem', action='store_true')
    parser.add_argument('--no-ufs', action='store_true', help='omit the /sweet-ufs node (no root device yet: the UFS driver must not probe)')
    parser.add_argument('--board', choices=BOARDS, default='sweet')
    args = parser.parse_args()
    root = build(qemu=args.qemu, highmem=args.highmem, board=args.board, no_ufs=args.no_ufs)
    blob = adt.serialize(root)
    open(args.output, 'wb').write(blob)
    if args.dump:
        adt.dump(adt.parse(blob)[0])
    print(f'{args.output}: {len(blob)} bytes', file=sys.stderr)
