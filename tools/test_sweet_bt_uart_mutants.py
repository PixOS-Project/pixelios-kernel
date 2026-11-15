#!/usr/bin/env python3
"""Mutation check for the BT-S4a backend host test: each mutant of xnu/patches/*-sweet-bt-transport.patch must make
`tools/test_sweet_bt_c.py --uart` FAIL (one mutant per rule). Prints `RESULT: PASS bt-s4a-mutants N/N killed` or lists survivors (exit 1)."""
import pathlib, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
PATCH = sorted((HERE.parent / "xnu" / "patches").glob("*-sweet-bt-transport.patch"))[0]
MUTANTS = [
    ("A10 delay before REG_ON high shortened", "sbu.hw.msleep(30);", "sbu.hw.msleep(3);"),
    ("A10 DEV_WAKE raised before REG_ON",
     'sbu_pin(SBU_G16_DAT, SBU_REGON_PIN, true, "BT_REG_ON high")) != 0 || (e = sbu_pin(SBU_G14_DAT, SBU_DEVWAKE_PIN, true, "DEV_WAKE high")',
     'sbu_pin(SBU_G14_DAT, SBU_DEVWAKE_PIN, true, "DEV_WAKE high")) != 0 || (e = sbu_pin(SBU_G16_DAT, SBU_REGON_PIN, true, "BT_REG_ON high")'),
    ("A27 REG_ON dropped before DEV_WAKE", 'int e = sbu_pin(SBU_G14_DAT, SBU_DEVWAKE_PIN, false, "DEV_WAKE low");',
     'int e = sbu_pin(SBU_G16_DAT, SBU_REGON_PIN, false, "BT_REG_ON low");'),
    ("A8 USI mode value", "(v & ~7u) | 1u", "(v & ~7u) | 2u"),
    ("U4 UBRDIV off by one", "uint32_t quot = div / 16 - 1;", "uint32_t quot = div / 16;"),
    ("U4 round-up rule inverted", "if (drd > dru) {", "if (drd < dru) {"),
    ("store list open", "return pa == SBU_USI_MODE ||", "return true || pa == SBU_USI_MODE ||"),
    ("unsourced clock parent accepted", "if (sv != 0 || sm != 0) {", "if (0) {"),
    # BT-S4c (T-833a, research BT-S4c s.4): both-layouts case table, raw words, no CMU store
    ("S4c raw CMU words not logged", 'printf("BT UART: cmu_apm mux_func raw %#x funcsrc raw %#x div raw %#x\\n", rf, rs, rd);', ""),
    ("S4c mainline-only func decode (bit 4)", "if (fv == 0 && fm == 0) {", "if (fm == 0) {"),
    ("S4c vendor-only func decode (bits [1:0])", "if (fv == 0 && fm == 0) {", "if (fv == 0) {"),
    ("S4c conflict accepted as funcsrc", "if (fv != 1 || fm != 1) {", "if (fv != 1) {"),
    ("S4c conflict GAP dropped", 'SBU_GAP(SG_CLK, "uart16 baud clock parent conflict (mux_func raw %#x: vendor select %u, mainline bit4 %u); no baud set", rf, fv, fm);', ""),
    ("S4c bit tests swapped (func bit 3, funcsrc bit 4)", "fm = (rf >> 4) & 1, sv = rs & 3, sm = (rs >> 3) & 1;", "fm = (rf >> 3) & 1, sv = rs & 3, sm = (rs >> 4) & 1;"),
    ("S4c vendor field width 1", "fv = rf & 3,", "fv = rf & 1,"),
    ("S4c funcsrc mainline bit ignored", "if (sv != 0 || sm != 0) {", "if (sv != 0) {"),
    ("S4c oscclk rate wrong", "#define SBU_OSCCLK      24576000u", "#define SBU_OSCCLK      26000000u"),
    ("S4c CMU mux store added", "\tprintf(\"BT UART: cmu_apm mux_func raw", "\t(void)sbu_wr(SBU_CMU_MUXF, rf | 0x11u, \"force funcsrc\");\n+\tprintf(\"BT UART: cmu_apm mux_func raw"),
    ("S4c raw CMU mux store past sbu_wr", "\t*osc = false;\n", "\tsbu.hw.wr(SBU_CMU_MUXF, rf | 0x11u);\n+\t*osc = false;\n"),
    ("S4c oscclk divider ignored", "*hz = SBU_OSCCLK / d;", "*hz = SBU_OSCCLK;"),
    ("S4c refused baud GAP dropped", 'SBU_GAP(SG_OTHER, "uart16 baud %u refused (error %d%s)", baud, e, e == EINVAL && hz / 16 < baud ? ": above the baud clock limit rate/16" : "");', ""),
    ("S4c oscclk non-115200 allowed", "if (osc && baud != 115200) {", "if (0) {"),
    ("S4c vendor idx 3 accepted as oscclk", "if (fv == 0 && fm == 0) {", "if ((fv == 0 || fv == 3) && fm == 0) {"),
    ("S4c clock pre-check before power-on dropped", "if (nu == 0) {\n+\t\t\tprintf", "if (0) {\n+\t\t\tprintf"),
    # BT-S4e (T-5c3c): the chip's answer to HCI Reset settles the baud clock where the two mux layouts disagree
    ("S4e conflict test ignores BUSY/upper bits of mux_func", "return rf == 0x1 &&", "return (rf & 0xFF) == 0x1 &&"),
    ("S4e conflict test accepts any vendor select", "return rf == 0x1 &&", "return (rf & 3) != 0 && (rf & ~3u) == 0 &&"),
    ("S4e conflict test ignores funcsrc", "&& rs == 0x0 && rd == 0x1;", "&& rd == 0x1;"),
    ("S4e conflict test ignores funcsrc upper bits", "&& rs == 0x0 && rd == 0x1;", "&& (rs & 3) == 0 && rd == 0x1;"),
    ("S4e conflict test ignores DIVRATIO", "&& rs == 0x0 && rd == 0x1;", "&& rs == 0x0;"),
    ("S4e vendor candidate is oscclk", "c[0] = (struct sbu_cand){ SBU_PLL_DIV2 / d, false, \"vendor\" };", "c[0] = (struct sbu_cand){ SBU_OSCCLK / d, true, \"vendor\" };"),
    ("S4e mainline candidate is pll_alv_div2", "c[1] = (struct sbu_cand){ SBU_OSCCLK / d, true, \"mainline\" };", "c[1] = (struct sbu_cand){ SBU_PLL_DIV2 / d, false, \"mainline\" };"),
    ("S4e mainline candidate not marked oscclk", "c[1] = (struct sbu_cand){ SBU_OSCCLK / d, true,", "c[1] = (struct sbu_cand){ SBU_OSCCLK / d, false,"),
    ("S4e answer opcode unchecked", " && buf[i + 4] == 0x03 && buf[i + 5] == 0x0c) {", ") {"),
    ("S4e answer event code unchecked", "buf[i] == 0x04 && buf[i + 1] == 0x0e && buf[i + 2] == 0x04 &&", "buf[i + 1] == 0x0e && buf[i + 2] == 0x04 &&"),
    ("S4e no power-off after a silent candidate", "(void)sbu_power_off();\n+\tsbu.clk_hz = 0;", "sbu.clk_hz = 0;"),
    ("S4e settled clock not used by set_baud", "if (sbu.clk_hz != 0) {          /* BT-S4e", "if (0) {          /* BT-S4e"),
    ("S4e settled flag ignored (second power-on)", "if (!settled && (sbu_power_on() != 0", "if ((sbu_power_on() != 0"),
    ("S4e answering layout line dropped", 'printf("BT UART: baud probe answered layout=%s uart16 clock %u Hz\\n", use[i].layout, use[i].hz);', ""),
    ("S4e settled-by-answer GAP dropped", 'SBU_GAP(SG_CLK, "uart16 baud clock %u Hz settled by the chip\'s answer', 'if (0) SBU_GAP(SG_CLK, "uart16 baud clock %u Hz settled by the chip\'s answer'),
    ("S4e probe line per candidate dropped", 'printf("BT UART: baud probe layout=%s uart16 clock %u Hz UBRDIV %u UFRACVAL %u: %s (rx %zu bytes, uart error flags %#x)\\n",', '(void)printf("%s%s%u%u%u%s%zu%u\\n",'),
    ("S4e failed probe not reported", 'printf("BT GAP: uart16 baud probe: no candidate clock got an answer to HCI Reset (error %d); backend not started, controller powered off\\n", e);', ""),
    ("S4e hard start error keeps probing", "for (int i = 0; i < nu && e == ETIMEDOUT; i++) {", "for (int i = 0; i < nu && e != 0; i++) {"),
    ("S4e bad Reset status silent", 'SBU_GAP(SG_OTHER, "baud probe: HCI Reset Command Complete status', 'if (0) SBU_GAP(SG_OTHER, "baud probe: HCI Reset Command Complete status'),
    ("S4e late answer bytes not drained", "while (nl < 1024 &&", "while (0 && nl < 1024 &&"),
    ("S4e probe writes the CMU mux past sbu_wr", "\tsbu.clk_hz = c->hz;\n+\tsbu.clk_osc = c->osc;", "\tsbu.hw.wr(SBU_CMU_MUXF, 0x11u);\n+\tsbu.clk_hz = c->hz;\n+\tsbu.clk_osc = c->osc;"),
    ("S4e probe sends no Reset", "e = sbu_tx(sbu_hci_reset, sizeof(sbu_hci_reset));", "e = 0;"),
    ("S4e probe Reset opcode wrong", "{ 0x01, 0x03, 0x0c, 0x00 };", "{ 0x01, 0x03, 0x0c, 0x01 };"),
    ("S4e probe listens 0 ms", "#define SBU_PROBE_MS    300", "#define SBU_PROBE_MS    0"),
    ("S4e probe listen never sleeps", "if (t < SBU_PROBE_MS) {\n+\t\t\tsbu.hw.msleep(10);\n+\t\t}", ""),
    ("S4e probe window off by one byte (match)", "for (size_t i = 0; i + 7 <= *n; i++) {", "for (size_t i = 0; i + 8 <= *n; i++) {"),
    ("S4e probe window off by one byte (buffer)", "while (*n < SBU_PROBE_BUF &&", "while (*n + 1 < SBU_PROBE_BUF &&"),
    ("S4e probe error flags not read", "*err |= sbu_rd(SBU_UART + U_UERSTAT);", "*err |= 0;"),
    ("S4e probe single round only", "for (int round = 0; round < 2 && !ok && e == 0; round++) {", "for (int round = 0; round < 1 && !ok && e == 0; round++) {"),
    ("S4e settle after two Resets is 20 ms", "(sent == 2 ? SBU_PROBE_MS : 20u)", "20u"),
    ("S4c CMU on the store list", "return pa == SBU_USI_MODE ||", "return pa == SBU_CMU_MUXF || pa == SBU_USI_MODE ||"),
    ("S4c 4M on oscclk silently downgraded", "if (e != 0) {\n+\t\tSBU_GAP(SG_OTHER, \"uart16 baud %u refused", "if (e == EINVAL && hz / 16 < baud) { baud = 115200; e = sbu_baud_regs(hz, baud, &q, &f); }\n+\tif (e != 0) {\n+\t\tSBU_GAP(SG_OTHER, \"uart16 baud %u refused"),
    ("stuck TX FIFO reported as success", "i, len);\n+\t\t\treturn ETIMEDOUT;", "i, len);\n+\t\t\treturn 0;"),
    ("RX without client dropped silently", 'SBT_GAP(G_RX, "UART RX %zu bytes with no connected client; dropped", len);', ""),
    ("RX errors swallowed", 'SBU_GAP(SG_RXERR, "uart16 rx error flags %#x (overrun/frame/break); bytes may be lost", err);', ""),
    ("GPIO CON written without read-modify-write", "(c & ~(0xFu << (4 * pin))) | (1u << (4 * pin))", "(1u << (4 * pin))"),
    ("GPIO DAT (sbu_pin) written without read-modify-write", "return sbu_wr(dat, (sbu_rd(dat) & ~(1u << pin))", "return sbu_wr(dat, (0)"),
    ("GPIO CON mask widened to 8 bits", "0xFu << (4 * pin))) | (1u", "0xFFu << (4 * pin))) | (1u"),
    ("change-mask audit off", "if (mask != 0xFFFFFFFFu &&", "if (0 &&"),
    ("USI mode keeps no upper bits", "(v & ~7u) | 1u", "1u"),
    ("sweetbt=0 enables the backend", "|| want != 1) {", "|| 0) {"),
    ("non-gs101 board not refused", "if (!sweet_usb_board_is_gs101()) {", "if (0) {"),
    ("clock gate check skipped", "if (sbu_clk_gates() != 0) {", "if (0) {"),
    ("rstnsync gate not checked", "!(g[3] & SBU_GATE_EN)", "0"),
    ("failed start does not power off", "(void)sbu_power_off();\n+\t\treturn;", "return;"),
    ("ops published before the open", "if (!settled && (sbu_power_on() != 0 || sbu_uart_open(115200) != 0)) {", "sweet_bt_set_ops(&sbu_ops);\n+\tif (!settled && (sbu_power_on() != 0 || sbu_uart_open(115200) != 0)) {"),
    ("USI read-back check off", "if ((v & 7u) != 1u) {", "if (0) {"),
    ("full RX FIFO read as empty", "RXMASK) != 0 || (sbu_rd(SBU_UART + U_UTRSTAT) & SBU_UTRSTAT_RXDR) != 0)) {\n+\t\tbuf[n++]", "RXMASK) != 0)) {\n+\t\tbuf[n++]"),
    ("second control may connect", "__atomic_exchange_n(&sbt.uart_busy, 1, __ATOMIC_ACQ_REL)", "0"),
    ("TIOCSETA accepts non-8N1", "if ((t->c_cflag & (CSIZE | PARENB | CSTOPB)) != CS8) {", "if (0) {"),
    ("TIOCSETA speed unchecked", "if (t->c_ospeed == 0 || t->c_ospeed > SBU_MAX_BAUD) {", "if (0) {"),
    ("UBRDIV width unchecked", "if (quot > 0xFFFFu) {", "if (0) {"),
    ("tx while unpowered allowed", "if (!sbu.powered || !sbu.uart_ok) {\n+\t\tSBU_GAP(SG_OTHER, \"H4 tx", "if (0) {\n+\t\tSBU_GAP(SG_OTHER, \"H4 tx"),
    # BT-S4h (T-e3c9 H1): gpa3-0..3 pad function
    ("S4h pad function value wrong", "#define SBU_GA3_FUNC    0x2222u", "#define SBU_GA3_FUNC    0x1111u"),
    ("S4h pad mux never stored", "if ((c & 0xFFFFu) != SBU_GA3_FUNC) {", "if (0) {"),
    ("S4h pad store clobbers other pins", "(c & ~0xFFFFu) | SBU_GA3_FUNC", "SBU_GA3_FUNC"),
    ("S4h gpa3 audit mask widened", "case SBU_GA3_CON:   return 0xFFFFu;", "case SBU_GA3_CON:   return 0xFFFFFFFFu;"),
    ("S4h pads muxed unconditionally", "if ((c & 0xFFFFu) != SBU_GA3_FUNC) {", "if (1) {"),
    ("S4h pad PUD written", 'printf("BT UART: pads before gpa3', 'sbu.hw.wr(SBU_GA3_PUD, 5u); printf("BT UART: pads before gpa3'),
    ("S4h pad witness dropped", 'printf("BT UART: witness %s regon', 'printf("%s regon'),
]


def main():
    text, bad, tmp = PATCH.read_text(), [], pathlib.Path(tempfile.mkdtemp(prefix="bt-mut-"))
    for i, (name, old, new) in enumerate(MUTANTS):
        if text.count(old) < 1:
            print("MUTANT %d %s: pattern not found" % (i, name))
            bad.append(name)
            continue
        p = tmp / ("m%d-sweet-bt-transport.patch" % i)
        p.write_text(text.replace(old, new, 1))
        r = subprocess.run([sys.executable, str(HERE / "test_sweet_bt_c.py"), "--uart", "--patch", str(p)], capture_output=True, text=True, timeout=300)
        killed = r.returncode != 0
        print("MUTANT %d %-45s %s" % (i, name, "killed" if killed else "SURVIVED"))
        if not killed:
            bad.append(name)
    if bad:
        print("RESULT: FAIL bt-s4a-mutants survivors: " + "; ".join(bad))
        return 1
    print("RESULT: PASS bt-s4a-mutants %d/%d killed" % (len(MUTANTS), len(MUTANTS)))
    return 0


class TestBtUartMutants(unittest.TestCase):
    """Unit-step wrapper (the gate runs unittest on every test_*.py). The full mutant run compiles the host test 29 times (minutes) and stays the script run
    `python3 tools/test_sweet_bt_uart_mutants.py` (heavy-job); here every pattern must exist in the patch and the first mutant of each rule family must be killed."""

    def test_every_pattern_is_in_the_patch(self):
        text = PATCH.read_text()
        missing = [n for n, old, _new in MUTANTS if text.count(old) < 1]
        self.assertEqual(missing, [], "pattern not found: %s" % missing)

    def test_one_mutant_is_killed(self):
        text = PATCH.read_text()
        name, old, new = MUTANTS[0]
        with tempfile.TemporaryDirectory(prefix="bt-mut-") as t:
            p = pathlib.Path(t) / "m0-sweet-bt-transport.patch"
            p.write_text(text.replace(old, new, 1))
            r = subprocess.run([sys.executable, str(HERE / "test_sweet_bt_c.py"), "--uart", "--patch", str(p)], capture_output=True, text=True, timeout=300)
        self.assertNotEqual(r.returncode, 0, "mutant %r survived" % name)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "-v":
        unittest.main()
    sys.exit(main())
