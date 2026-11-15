"""Object audit of the --el2-trap EL2 code (spec S/docs/specs/SPEC-EL2-PROXY-PROBE.md section 3.8 and B4).

Run by `shim/build.sh --el2-trap` through `tools/test-bluejay-boot.py --out DIR --el2-objects`; it audits the built objects el2_asm.o (el2.S), el2.o, el2h.o, el2cfg.o, start.o against
start_nodef.o (start.S without the define), shim.o (the test reads) and shim.elf (symbol alignment), then runs a mutation self-test: every rule is fed a mutated copy of the REAL
disassembly and must reject it. Rules fail closed: an instruction the register model does not know is an error, never a pass. The audit reads `objdump -dr` / `nm -S` text only.

Rules (3.8): R1 msr targets in {vbar_el2, hcr_el2, vtcr_el2, vttbr_el2, elr_el2, spsr_el2, spsel} (no sp_el2), one `tlbi vmalls12e1` per path, no smc/dc/at/ic/hvc;
R2 every `msr hcr_el2` takes a register loaded by mov/movk immediately before, value in {RW, RW|VM}; R3 el2_ld32 range scan (start to .size); R4 entry 4 (x16/x17 only, branches followed);
R5 x19-x29 outside the el2_frame_restore range; R6 nm vector offsets and alignment; R7 start.o vs start_nodef.o differ only at sites (a)-(d); R8 the C objects touch no system register;
R9 the GICD / T1 test reads in shim.o are single 32-bit loads and their number matches the predicted traps.
EL2_RESIDENT objects (build.sh --el2-trap --boot-xnu, SPEC 3.4 (b) design item 1; detected by the `el2_resident_build` marker in el2h.o) add: R12 no object but start.o and el2_asm.o writes hcr_el2 / vtcr_el2 /
vttbr_el2 / vbar_el2 (or any s3_4_* EL2 encoding), and inside el2_asm.o only el2_enable and the VM-clear path of el2_sync_lower do (stage 2 and VBAR_EL2 stay live across loader_boot); R13 el2_hcr_vm is
exactly `mrs x0, hcr_el2; and x0, x0, #1; ret` (the one new EL2 register READ); R9 resident: shim.o has the 2 pre-existing GICD loads and no T1 read.
"""
import pathlib
import re
import subprocess

ALLOWED_MSR = {"vbar_el2", "hcr_el2", "vtcr_el2", "vttbr_el2", "elr_el2", "spsr_el2", "spsel"}
HCR_VALUES = {0x80000000, 0x80000001}	# RW, RW|VM [SPEC 3.3.6]
PARK_LABELS = {"el2_park_guard", "el2_park_ec", "el2_park_elr"}	# the only targets entry 4 may branch to outside its slot
BANNED_ANYWHERE = {"smc", "hvc", "at", "ic", "svc"}	# `dc` only as the two `dc ivac` pool loops of el2_enable (R1, review el2-probe-job2-r1 F3)
DC_IVAC_FN, DC_IVAC_N = "el2_enable", 2
TLBI_PATHS = {"el2_sync_lower": 1, "el2_enable": 1}	# exactly one per path, both present (review r1 F1)
HCR_PINNED = {"el2_enable": 0x80000001, "el2_sync_lower": 0x80000000}	# exactly these two writes (review r1 F1)
# R10 (review r1 F1): the ordered barrier / system-register skeleton of each EL2 function (msr target, tlbi, dsb/dmb option, isb, dc op, eret); anything else fails
SEQ = {
    "el2_enable": ["dc ivac", "dsb sy", "dmb sy", "dc ivac", "dsb sy", "dsb ish", "msr hcr_el2", "msr vtcr_el2", "msr vttbr_el2", "isb", "tlbi vmalls12e1", "dsb nsh", "isb"],
    "el2_sync_lower": ["msr hcr_el2", "isb", "tlbi vmalls12e1", "dsb nsh", "isb"],
    "el2_frame_restore": ["msr elr_el2", "msr spsr_el2", "eret"],
}
# the action dispatch after `bl el2_handle` (EL2_A_RETURN 0 / PARK 1 / CLEAR_VM 2; anything else parks: review r1 F5)
DISPATCH = ["bl el2_handle", "cbz w0 el2_frame_restore", "cmp w0, #0x1", "b.eq el2_park_trap", "cmp w0, #0x2", "b.ne el2_park_trap", "mov x1, #0x80000000", "msr hcr_el2, x1"]
EL2_FRAME = 0x110	# x0-x30 at 8*n, ELR/SPSR at 0x100 (el2_abi.h EL2_FRAME_ELR)
# R7b (spec 3.8, review r1 F6): sha256 of the normalized start.o listing of the commit before B1 (e00af0c); start_nodef.o must still equal it.
# A trunk change to start.S breaks this on purpose: re-pin from `git show e00af0c-successor:shim/start.S` only after reading that diff.
START_PRE_B1_SHA = "ec0a4a03c0a546241e589dea6be26d4aad6b1edafe127280a069b86a86e0a96a"	# e00af0c shim/out/start.o (driver 17, 2026-10-28)
PRED_GICD_LOADS_RESIDENT = 2	# EL2_RESIDENT compiles the T1/T2 test reads out (shim.c): only the two pre-existing reads (GICD_TYPER, GICD_CTLR) remain; no trap page exists
PRED_GICD_LOADS = 3		# shim.c:640, :641 and the test read of GICD_TYPER (SPEC 3.3.9: traps=4 = these three + the T1 read)


class AuditError(Exception):
    pass


class Insn:
    __slots__ = ("addr", "mnem", "ops", "fn", "reloc", "target")

    def __init__(self, addr, mnem, ops, fn):
        self.addr, self.mnem, self.ops, self.fn, self.reloc, self.target = addr, mnem, ops, fn, None, None

    def text(self):
        return f"{self.mnem}\t{self.ops}"


_INSN = re.compile(r"^\s*([0-9a-f]+):\s+[0-9a-f]{8}\s+(\S+)\s*(.*)$")
_RELOC = re.compile(r"^\s+([0-9a-f]+):\s+(R_AARCH64_\w+)\s+(\S+)")
_FN = re.compile(r"^[0-9a-f]+ <([^>]+)>:")
_REG = re.compile(r"\b([xw])(\d+)\b|\b(sp)\b|\b([xw])zr\b")


def parse_dis(text):
    """objdump -dr text -> list of Insn (relocation symbol attached as .reloc, branch/adr target symbol as .target)."""
    out, fn, last = [], "", None
    for line in text.splitlines():
        m = _FN.match(line)
        if m:
            fn = m.group(1)
            continue
        m = _INSN.match(line)
        if m:
            ops = m.group(3).split("//")[0].rstrip()
            last = Insn(int(m.group(1), 16), m.group(2), ops, fn)
            tm = re.search(r"<([A-Za-z_.$][\w.$]*)(?:\+0x[0-9a-f]+)?>", ops)
            if tm:
                last.target = tm.group(1)
            out.append(last)
            continue
        m = _RELOC.match(line)
        if m and last is not None and int(m.group(1), 16) == last.addr:
            last.reloc = m.group(3)
            if last.reloc and not last.reloc.startswith("."):
                last.target = last.reloc.split("+")[0]
    return out


def tokens(s):
    """register names in operand order: 'x5'/'w5' -> 'x5', 'sp', 'xzr'/'wzr' -> 'xzr'."""
    out = []
    for m in _REG.finditer(s):
        out.append("sp" if m.group(3) else "xzr" if m.group(4) else "x" + m.group(2))
    return out


def regs_of(s):
    return set(tokens(s))


_NOWRITE = {"str", "stp", "strb", "strh", "cmp", "tst", "cmn", "msr", "b", "cbz", "cbnz", "tbz", "tbnz", "tlbi", "dsb", "isb", "dmb", "eret", "ret", "nop", "br", "blr", "dc"}
_DST1 = {"mov", "movk", "movz", "movn", "mrs", "adr", "adrp", "ldr", "ldrb", "ldrh", "ldrsw", "add", "sub", "and", "orr", "eor", "bic", "lsr", "lsl", "asr", "csel", "cset", "ubfx", "sxtw", "mvn", "neg"}


def rw(i):
    """(reads, writes) as register sets; raises AuditError on a mnemonic the model does not know (fail closed)."""
    m, ops = i.mnem, i.ops
    if m.startswith("b.") or m in _NOWRITE:
        return regs_of(ops), (set() if m != "blr" else {"x30"})
    if m == "bl":
        return set(), {"x30"}
    mem = re.findall(r"\[([^\]]*)\]", ops)
    mem_regs = set().union(*[regs_of(x) for x in mem]) if mem else set()
    writeback = bool(re.search(r"\]!|\],\s*#", ops))
    head = ops.split("[")[0] if mem else ops
    head_regs = tokens(head)
    if m == "ldp":
        w = set(head_regs[:2])
        return mem_regs, w | ({"sp"} if writeback and "sp" in mem_regs else set())
    if m in _DST1:
        if not head_regs:
            raise AuditError(f"{i.fn}+{i.addr:#x}: no destination register in '{i.text()}'")
        reads = set(head_regs[1:]) | mem_regs | (set(head_regs[:1]) if m == "movk" else set())
        return reads, {head_regs[0]} | ({"sp"} if writeback and "sp" in mem_regs else set())
    raise AuditError(f"{i.fn}+{i.addr:#x}: unknown mnemonic '{i.mnem}' (the register model fails closed)")


def _num(s):
    return int(s, 0)


def _imm(i):
    m = re.search(r"#(-?0x[0-9a-f]+|-?\d+)", i.ops)
    return _num(m.group(1)) if m else None


def nm_syms(text):
    """nm -S text -> {name: (addr, size|None)}"""
    out = {}
    for line in text.splitlines():
        t = line.split()
        if len(t) == 4:
            out[t[3]] = (int(t[0], 16), int(t[1], 16))
        elif len(t) == 3 and t[1] != "U":
            out[t[2]] = (int(t[0], 16), None)
    return out


def sym_range(syms, name):
    if name not in syms:
        raise AuditError(f"symbol {name} missing")
    a, n = syms[name]
    if not n:
        raise AuditError(f"{name}: no .size (nm -S shows none): the range rule cannot scan it (r5 F5)")
    return a, a + n


# ---- R1 / R8 ---------------------------------------------------------------------------------------------------------------------------------------------------------------

def check_msr(insns, *, c_object=False):
    n_tlbi = {}
    for i in insns:
        if i.mnem in BANNED_ANYWHERE:
            raise AuditError(f"{i.fn}+{i.addr:#x}: banned instruction '{i.text()}'")
        if c_object and i.mnem in ("msr", "mrs", "tlbi", "isb", "dsb", "eret", "dc"):
            raise AuditError(f"{i.fn}+{i.addr:#x}: a C EL2 object must not touch system registers: '{i.text()}'")
        if i.mnem == "msr":
            tgt = i.ops.split(",")[0].strip()
            if tgt not in ALLOWED_MSR:
                raise AuditError(f"{i.fn}+{i.addr:#x}: msr target '{tgt}' is not in the allowed set {sorted(ALLOWED_MSR)}")
        if i.mnem == "dc" and (i.fn != DC_IVAC_FN or i.ops.split(",")[0].strip() != "ivac"):
            raise AuditError(f"{i.fn}+{i.addr:#x}: '{i.text()}': only `dc ivac` in {DC_IVAC_FN} is allowed")
        if i.mnem == "tlbi":
            if i.ops.split(",")[0].strip() != "vmalls12e1":
                raise AuditError(f"{i.fn}+{i.addr:#x}: tlbi '{i.ops}' is not vmalls12e1")
            n_tlbi[i.fn] = n_tlbi.get(i.fn, 0) + 1
    if c_object:
        return n_tlbi
    if n_tlbi != TLBI_PATHS:
        raise AuditError(f"tlbi vmalls12e1 per path {n_tlbi}, expected exactly {TLBI_PATHS}")
    n_dc = sum(1 for i in insns if i.mnem == "dc")
    if n_dc != DC_IVAC_N:
        raise AuditError(f"{n_dc} dc ivac in {DC_IVAC_FN}, expected exactly {DC_IVAC_N} (the two pool invalidate loops)")
    return n_tlbi


# ---- R2 --------------------------------------------------------------------------------------------------------------------------------------------------------------------

def check_hcr(insns):
    n, seen = 0, set()
    for k, i in enumerate(insns):
        if i.mnem != "msr" or i.ops.split(",")[0].strip() != "hcr_el2":
            continue
        n += 1
        reg = i.ops.split(",")[1].strip()
        run = []
        j = k - 1
        while j >= 0 and insns[j].mnem in ("mov", "movk", "movz") and insns[j].ops.split(",")[0].strip() == reg and insns[j].fn == i.fn:
            run.append(insns[j])
            j -= 1
        if not run:
            raise AuditError(f"{i.fn}+{i.addr:#x}: msr hcr_el2 takes {reg}, which is not loaded by mov/movk immediately before (no bic/orr of an mrs allowed)")
        v = 0
        for p in reversed(run):
            im = _imm(p)
            if im is None:
                raise AuditError(f"{p.fn}+{p.addr:#x}: non-immediate producer '{p.text()}'")
            sh = re.search(r"lsl #(\d+)", p.ops)
            if p.mnem == "movk":
                s = int(sh.group(1)) if sh else 0
                v = (v & ~(0xFFFF << s)) | ((im & 0xFFFF) << s)
            else:
                v = im << (int(sh.group(1)) if sh else 0)
        if v not in HCR_VALUES:
            raise AuditError(f"{i.fn}+{i.addr:#x}: HCR_EL2 literal {v:#x} not in {sorted(hex(x) for x in HCR_VALUES)}")
        if HCR_PINNED.get(i.fn) != v or i.fn in seen:
            raise AuditError(f"{i.fn}+{i.addr:#x}: HCR_EL2 write {v:#x} in {i.fn}: expected exactly one write per function {[(f, hex(x)) for f, x in HCR_PINNED.items()]}")
        seen.add(i.fn)
    if seen != set(HCR_PINNED):
        raise AuditError(f"HCR_EL2 writes in {sorted(seen)}, expected exactly {sorted(HCR_PINNED)} (the audit would pass a missing VM-clear or enable write)")
    return n


# ---- R3 --------------------------------------------------------------------------------------------------------------------------------------------------------------------

def check_ld32(insns, syms):
    lo, hi = sym_range(syms, "el2_ld32")
    try_a = syms["el2_ld32_try"][0]
    rng = [i for i in insns if lo <= i.addr < hi]
    if not rng:
        raise AuditError("el2_ld32 range holds no instruction")
    written = set()
    for i in rng:
        if i.mnem in ("bl", "blr", "br"):
            raise AuditError(f"el2_ld32+{i.addr - lo:#x}: call '{i.text()}'")
        r, w = rw(i)
        both = r | w
        if "sp" in both:
            raise AuditError(f"el2_ld32+{i.addr - lo:#x}: sp used in '{i.text()}'")
        if {"x16", "x17"} & both:
            raise AuditError(f"el2_ld32+{i.addr - lo:#x}: x16/x17 used in '{i.text()}' (entry 4 clobbers them)")
        bad = w - {"x0", "x1", "x2", "x3"}
        if bad:
            raise AuditError(f"el2_ld32+{i.addr - lo:#x}: writes {sorted(bad)} (only x0-x3 allowed) in '{i.text()}'")
        if i.addr >= try_a:
            reads = set(r) - {"xzr"}
            if i.mnem == "ret":
                reads = set()			# `ret` returns through x30 (the caller's link): allowed, it is not a use of a value the range computed
            extra = reads - written - {"x0"}
            if extra:
                raise AuditError(f"el2_ld32+{i.addr - lo:#x}: reads {sorted(extra)} after el2_ld32_try that nothing in the range wrote (only x0 may be live across the load)")
        written |= w
    return len(rng)


# ---- R4 --------------------------------------------------------------------------------------------------------------------------------------------------------------------

ENTRY4_MNEM = {"adr", "ldr", "str", "mrs", "msr", "cmp", "ubfx", "mov", "b", "cbz", "cbnz", "eret", "nop"}


def check_entry4(insns, syms):
    vec = syms["el2_vectors"][0]
    lo, hi = vec + 0x200, vec + 0x280
    slot = [i for i in insns if lo <= i.addr < hi]
    while slot and slot[-1].mnem == "nop":		# .balign padding after the exit
        slot.pop()
    if not slot:
        raise AuditError("entry 4 slot is empty")
    for i in slot:
        if i.mnem in ("bl", "blr", "br", "ret"):
            raise AuditError(f"entry 4+{i.addr - lo:#x}: '{i.text()}' (no bl/blr/br/ret in the slot)")
        if i.mnem not in ENTRY4_MNEM and not i.mnem.startswith("b."):
            raise AuditError(f"entry 4+{i.addr - lo:#x}: mnemonic '{i.mnem}' is not in the slot's allowed set")
        if i.mnem in ("mrs", "msr"):
            sysreg = i.ops.split(",")[1 if i.mnem == "mrs" else 0].strip()
            if sysreg not in ("esr_el2", "elr_el2"):
                raise AuditError(f"entry 4+{i.addr - lo:#x}: system register {sysreg} (only esr_el2/elr_el2)")
            gp = i.ops.split(",")[0 if i.mnem == "mrs" else 1].strip()
            if gp not in ("x16", "x17"):
                raise AuditError(f"entry 4+{i.addr - lo:#x}: '{i.text()}' uses {gp} (x16/x17 only)")
            continue
        r, w = rw(i)
        bad = (r | w) - {"x16", "x17", "xzr"}
        if bad:
            raise AuditError(f"entry 4+{i.addr - lo:#x}: '{i.text()}' touches {sorted(bad)}; only x16/x17 (and xzr/wzr) may be used")
        if i.mnem == "b" or i.mnem.startswith("b.") or i.mnem in ("cbz", "cbnz"):
            if i.target in PARK_LABELS:
                continue
            m = re.search(r"\b([0-9a-f]+)\s*<", i.ops)
            tgt = int(m.group(1), 16) if m else None
            if i.reloc is None and tgt is not None and lo <= tgt < hi:
                continue
            raise AuditError(f"entry 4+{i.addr - lo:#x}: branch '{i.text()}' target {i.target or hex(tgt or 0)} is neither a park label {sorted(PARK_LABELS)} nor inside the slot")
    last = slot[-1]
    if last.mnem != "eret" and last.mnem != "b":
        raise AuditError(f"entry 4 ends with '{last.text()}': it falls out of the slot (exits are eret or a park branch)")
    return len(slot)


# ---- R5 --------------------------------------------------------------------------------------------------------------------------------------------------------------------

_CALLEE = {f"x{n}" for n in range(19, 30)}


def check_callee_saved(insns, syms):
    start = syms["el2_frame_restore"][0]
    end = None
    for i in insns:
        if i.addr >= start and i.mnem == "eret":
            end = i.addr
            break
    if end is None:
        raise AuditError("no eret at or after el2_frame_restore: the range cannot be bounded")
    for i in insns:
        r, w = rw(i)
        hit = w & _CALLEE
        if not hit:
            continue
        inside = start <= i.addr <= end
        if not inside:
            raise AuditError(f"{i.fn}+{i.addr:#x}: '{i.text()}' writes {sorted(hit)} outside el2_frame_restore..eret (callee-saved: el2.S must not write them)")
        if i.mnem not in ("ldp", "ldr") or not re.search(r"\[sp(,\s*#\d+)?\]", i.ops):
            raise AuditError(f"{i.fn}+{i.addr:#x}: '{i.text()}' writes {sorted(hit)} inside the frame-restore range other than by ldp/ldr from [sp, #imm]")
    return end - start


# ---- R6 --------------------------------------------------------------------------------------------------------------------------------------------------------------------

def check_vectors(o_syms, o_hdr, elf_syms=None):
    vec = o_syms["el2_vectors"][0]
    m = re.search(r"\.text\s+[0-9a-f]+\s+[0-9a-f]+\s+[0-9a-f]+\s+[0-9a-f]+\s+2\*\*(\d+)", o_hdr)
    if not m or int(m.group(1)) < 11:
        raise AuditError(f"el2_asm.o .text alignment {m.group(0) if m else '?'}: el2_vectors needs 2**11")
    if vec % 0x800:
        raise AuditError(f"el2_vectors at {vec:#x} is not 2 KiB aligned")
    for name, off in (("el2_vent4", 0x200), ("el2_vent8", 0x400)):
        if o_syms.get(name, (None,))[0] != vec + off:
            raise AuditError(f"{name} is not at el2_vectors+{off:#x} (slot layout shifted)")
    if elf_syms is not None:
        if elf_syms["el2_vectors"][0] % 0x800:
            raise AuditError(f"linked el2_vectors {elf_syms['el2_vectors'][0]:#x} is not 2 KiB aligned")
        st = elf_syms["_el2_stack_top"][0]
        if st % 16 or st > elf_syms["_bss_end"][0]:
            raise AuditError(f"_el2_stack_top {st:#x} not 16-aligned or beyond _bss_end")


# ---- R7 --------------------------------------------------------------------------------------------------------------------------------------------------------------------

def _norm(i):
    """branch/adr targets without addresses (the insertions shift them); the relocation symbol stays."""
    ops = re.sub(r"\b[0-9a-f]+\s*<[^>]*>", "<T>", i.ops)
    return f"{i.mnem} {ops}" + (f" @{i.reloc}" if i.reloc else "")


# the exact edit script start.S applies under -DEL2_TRAP (SPEC 3.3.1): (a) three instructions before the first `mov sp, x1`, (b)+(c) `bl el2_census` and the two-vbar sequence in place of the
# one-adr sequence, (d) `bl el2_enable` after the first `msr icc_sre_el2`. Targets are compared by relocation symbol, addresses are dropped.
SITE_A = ["cmp x21, #0x2", "adr x2, <T> @_el2_stack_top", "csel x1, x2, x1, eq"]
SITE_C_OLD = ["adr x1, <T> @vectors", "msr vbar_el2, x1", "msr vbar_el1, x1"]
SITE_BC_NEW = ["bl <T> @el2_census", "adr x1, <T> @el2_vectors", "msr vbar_el2, x1", "adr x1, <T> @vectors", "msr vbar_el1, x1"]
SITE_D = ["bl <T> @el2_enable"]


def expected_el2_start(a):
    exp = list(a)
    k = next((x for x, t in enumerate(exp) if t == "mov sp, x1"), None)
    if k is None:
        raise AuditError("start_nodef.o has no `mov sp, x1`")
    exp[k:k] = SITE_A
    k = next((x for x in range(len(exp) - 2) if exp[x:x + 3] == SITE_C_OLD), None)
    if k is None:
        raise AuditError("start_nodef.o lacks the pre-edit vbar sequence of site (c)")
    exp[k:k + 3] = SITE_BC_NEW
    k = next((x + 1 for x, t in enumerate(exp) if t.startswith("msr icc_sre_el2")), None)
    if k is None:
        raise AuditError("start_nodef.o has no `msr icc_sre_el2`")
    exp[k:k] = SITE_D
    return exp


def check_start(nodef, el2):
    """start.o (-DEL2_TRAP) is start_nodef.o plus exactly the edits at sites (a)-(d): any other inserted, removed or changed instruction, and any new msr, fails."""
    a, b = [_norm(i) for i in nodef if i.mnem != "nop"], [_norm(i) for i in el2 if i.mnem != "nop"]	# .balign padding differs with the inserted instructions
    exp = expected_el2_start(a)
    if exp != b:
        import difflib
        bad = [f"{t}{x}" for t, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, exp, b, autojunk=False).get_opcodes() if t != "equal" for x in (b[j1:j2] or ["<removed>"])][:4]
        raise AuditError(f"start.o differs from start_nodef.o beyond sites (a)-(d): {bad}")
    return len(b) - len(a)


# ---- R9 --------------------------------------------------------------------------------------------------------------------------------------------------------------------

def check_test_reads(insns, resident=False):
    """shim.o: loads from the GICD page (a constant 0x10400000..0x10403fff materialised by mov/movk) are exactly PRED_GICD_LOADS, each a plain 32-bit `ldr w`; the T1 read
    (`ldr x, el2_t1_pa` then a load through it) is one plain `ldr w`. A writeback or pair form would give ISV = 0 (class 1: VM cleared, traps short) [SPEC 3.3.9; r3 finding 9]."""
    const, gicd, t1reg, t1 = {}, [], set(), []	# const: 'x0' -> value of a register loaded by mov/movk (linear scan, labels ignored: crude on purpose, the count below is the check)

    def reg0(i):
        h = i.ops.split(",")[0].strip()
        return "x" + h[1:] if h[:1] in "xw" and h[1:].isdigit() else h

    for i in insns:
        mem = re.search(r"\[(x\d+)(?:,\s*#(-?\d+|0x[0-9a-f]+))?\](!?)", i.ops)
        if mem:
            base = mem.group(1)
            if base in const and 0x10400000 <= const[base] < 0x10404000:
                gicd.append(i)
                if i.mnem != "ldr" or not i.ops.startswith("w") or mem.group(3) or re.search(r"\],", i.ops):
                    raise AuditError(f"{i.fn}+{i.addr:#x}: GICD access '{i.text()}' is not a plain 32-bit `ldr w` (ISV would be 0)")
            if base in t1reg and i.mnem.startswith("ld"):
                t1.append(i)
                t1reg = set()
                if i.mnem != "ldr" or not i.ops.startswith("w") or mem.group(3):
                    raise AuditError(f"{i.fn}+{i.addr:#x}: the T1 test read '{i.text()}' is not a plain 32-bit `ldr w`")
        if i.mnem in ("mov", "movz", "movk"):
            d, im = reg0(i), _imm(i)
            sh = re.search(r"lsl #(\d+)", i.ops)
            sft = int(sh.group(1)) if sh else 0
            if i.mnem == "movk" and im is not None:
                const[d] = (const.get(d, 0) & ~(0xFFFF << sft)) | ((im & 0xFFFF) << sft)
            elif im is not None:
                const[d] = im << sft
            else:
                const.pop(d, None)
        elif not re.match(r"(st|b|cb|tb|cmp|tst|ccmp|msr|ret|nop|dsb|isb|dmb)", i.mnem):
            const.pop(reg0(i), None)		# the first operand of any other instruction may be rewritten
        if i.mnem == "ldr" and i.reloc == "el2_t1_pa":
            t1reg = {reg0(i)}
    if resident:
        if any(i.reloc == "el2_t1_pa" for i in insns):
            raise AuditError("shim.o references el2_t1_pa in an EL2_RESIDENT build: the T1 test read must be compiled out")
        if len(gicd) != PRED_GICD_LOADS_RESIDENT:
            raise AuditError(f"{len(gicd)} loads from the GICD page in shim.o, an EL2_RESIDENT build predicts {PRED_GICD_LOADS_RESIDENT} (no test reads; no trap page, traps=0)")
        return len(gicd), 0
    if len(gicd) != PRED_GICD_LOADS:
        raise AuditError(f"{len(gicd)} loads from the GICD page in shim.o, the run-plan predicts {PRED_GICD_LOADS} (+ the T1 read = traps=4); re-list the accesses (AP-159)")
    if len(t1) != 1:
        raise AuditError(f"{len(t1)} test reads through el2_t1_pa in shim.o, expected exactly 1")
    return len(gicd), len(t1)


# ---- R12 / R13 (EL2_RESIDENT, SPEC 3.4 (b) design item 1 (iii)) -----------------------------------------------------------------------------------------------------------

STAGE2_REGS = {"hcr_el2", "vtcr_el2", "vttbr_el2", "vbar_el2"}
RESIDENT_WRITERS = {"el2_enable": {"hcr_el2", "vtcr_el2", "vttbr_el2"}, "el2_sync_lower": {"hcr_el2"}}	# el2_asm.o only: enable and the VM-clear path


def check_resident_writes(objs):
    """objs: {name: [Insn]} of every object but start.o / start_nodef.o (they write these registers at boot, before the jump path, and have their own site audit). A write of an EL2 stage-2 /
    vector register (by name, or by a raw s3_4_* encoding) is allowed only in el2_asm.o inside RESIDENT_WRITERS; anywhere else it could change or drop stage 2 on the path to XNU."""
    n = 0
    for name, insns in objs.items():
        for i in insns:
            if i.mnem != "msr":
                continue
            tgt = i.ops.split(",")[0].strip()
            if tgt not in STAGE2_REGS and not tgt.startswith("s3_4_"):
                continue
            if name == "el2_asm" and tgt in RESIDENT_WRITERS.get(i.fn, set()):
                n += 1
                continue
            raise AuditError(f"{name}.o {i.fn}+{i.addr:#x}: '{i.text()}' writes an EL2 stage-2/vector register outside el2_enable and the VM-clear path (EL2_RESIDENT keeps stage 2 live across loader_boot)")
    return n


def check_hcr_vm(insns, syms):
    lo, hi = sym_range(syms, "el2_hcr_vm")
    rng = [i for i in insns if lo <= i.addr < hi]
    got = [(i.mnem, re.sub(r"\s+", " ", i.ops)) for i in rng]
    if got != [("mrs", "x0, hcr_el2"), ("and", "x0, x0, #0x1"), ("ret", "")]:
        raise AuditError(f"el2_hcr_vm is {got}, expected exactly mrs x0, hcr_el2; and x0, x0, #0x1; ret (read only)")
    return len(rng)


# ---- drivers ---------------------------------------------------------------------------------------------------------------------------------------------------------------

def _tool(name):
    import os
    base = os.environ.get("CROSS_PREFIX")
    if base:
        return base + name
    home = pathlib.Path.home() / ".local/opt/arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-elf/bin"
    for cand in ("aarch64-linux-gnu-", "aarch64-none-elf-"):
        from shutil import which
        if which(cand + name):
            return cand + name
    return str(home / ("aarch64-none-elf-" + name))


def _run(*cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise AuditError(f"{cmd[0]} failed: {p.stderr.strip()}")
    return p.stdout


# ---- R10 / R11 (review el2-probe-job2-r1 F1, F2) ---------------------------------------------------------------------------------------------------------------------------------

def _skel(i):
    if i.mnem == "msr":
        return f"msr {i.ops.split(',')[0].strip()}"
    if i.mnem in ("tlbi", "dsb", "dmb", "dc"):
        return f"{i.mnem} {i.ops.split(',')[0].strip()}"
    if i.mnem in ("isb", "eret"):
        return i.mnem
    return None


def check_seq(insns):
    """R10: per function, the ordered barrier / system-register skeleton equals SEQ (a removed or moved tlbi/dsb/isb/msr/dc fails), and the action dispatch after bl el2_handle equals DISPATCH."""
    for fn, exp in SEQ.items():
        got = [k for k in (_skel(i) for i in insns if i.fn == fn) if k]
        if got != exp:
            raise AuditError(f"{fn}: barrier/sysreg sequence {got} != {exp}")
    sl = [i for i in insns if i.fn == "el2_sync_lower"]
    k = next((x for x, i in enumerate(sl) if i.mnem == "bl"), None)
    if k is None:
        raise AuditError("el2_sync_lower: no bl el2_handle")
    got = []
    for i in sl[k:k + len(DISPATCH)]:
        got.append(f"{i.mnem} {i.target}" if i.mnem in ("bl", "b.eq", "b.ne") else f"cbz {i.ops.split(',')[0]} {i.target}" if i.mnem == "cbz" else f"{i.mnem} {i.ops}")
    if got != DISPATCH:
        raise AuditError(f"el2_sync_lower: action dispatch {got} != {DISPATCH}")
    return len(sl)


def _sp_slots(i):
    """(reg, sp offset) pairs of an stp/str/ldp/ldr [sp, #imm] (no writeback)."""
    m = re.match(r"(\w+), (?:(\w+), )?\[sp(?:, #(\d+))?\]$", i.ops)
    if not m:
        return None
    off = int(m.group(3) or 0)
    regs = [r for r in (m.group(1), m.group(2)) if r]
    return [(r, off + 8 * n) for n, r in enumerate(regs)]


def check_frame(insns):
    """R11: entry 8 saves x0-x30 at 8*n of a 0x110 frame before its first mrs, and el2_frame_restore loads exactly those slots back, then releases the frame before eret."""
    want = {(f"x{n}", 8 * n) for n in range(31)}
    sl = [i for i in insns if i.fn == "el2_sync_lower"]
    if not sl or sl[0].text() != f"sub\tsp, sp, #{EL2_FRAME:#x}":
        raise AuditError(f"el2_sync_lower does not start with sub sp, sp, #{EL2_FRAME:#x}")
    saved = set()
    for i in sl[1:]:
        if i.mnem == "mrs":
            break
        sp = _sp_slots(i) if i.mnem in ("stp", "str") else None
        if sp is None:
            raise AuditError(f"el2_sync_lower+{i.addr:#x}: '{i.text()}' before the first mrs is not a frame store")
        saved |= set(sp)
    if saved != want:
        raise AuditError(f"entry 8 saves {sorted(want - saved)} missing / {sorted(saved - want)} extra")
    fr = [i for i in insns if i.fn == "el2_frame_restore"]
    loaded = set()
    for i in fr:
        if i.mnem in ("ldp", "ldr") and (sp := _sp_slots(i)):
            loaded |= {x for x in sp if x[1] < 0x100}
    if loaded != want:
        raise AuditError(f"el2_frame_restore loads {sorted(want - loaded)} missing / {sorted(loaded - want)} extra")
    if len(fr) < 2 or fr[-1].mnem != "eret" or fr[-2].text() != f"add\tsp, sp, #{EL2_FRAME:#x}":
        raise AuditError(f"el2_frame_restore does not end with add sp, sp, #{EL2_FRAME:#x}; eret")
    return len(saved)


def listing_sha(insns):
    import hashlib
    return hashlib.sha256("\n".join(_norm(i) for i in insns if i.mnem != "nop").encode()).hexdigest()


def check_start_pinned(nodef):
    """R7b: start_nodef.o equals the pre-B1 start.o (START_PRE_B1_SHA), so an unguarded start.S edit fails (spec 3.8)."""
    got = listing_sha(nodef)
    if got != START_PRE_B1_SHA:
        raise AuditError(f"start_nodef.o listing sha {got[:16]} != pre-B1 pin {str(START_PRE_B1_SHA)[:16]}: start.S changed outside #ifdef EL2_TRAP")
    return got[:16]



def load(out):
    out = pathlib.Path(out)
    o = {}
    for n in ("el2_asm", "el2", "el2h", "el2cfg", "start", "start_nodef", "shim"):
        p = out / f"{n}.o"
        if not p.is_file():
            raise AuditError(f"{p} missing (build with shim/build.sh --el2-trap)")
        o[n] = {"dis": _run(_tool("objdump"), "-dr", str(p)), "nm": _run(_tool("nm"), "-S", str(p)), "hdr": _run(_tool("objdump"), "-h", str(p))}
    o["resident"] = "el2_resident_build" in o["el2h"]["nm"]
    o["others"] = {}
    if o["resident"]:	# R12 scans every other object of the build directory
        for p in sorted(out.glob("*.o")):
            if p.stem in ("start", "start_nodef") or "aarch64" not in _run(_tool("objdump"), "-f", str(p)):	# x86 host-test objects share the directory; every AArch64 object is audited
                continue
            o["others"][p.stem] = parse_dis(_run(_tool("objdump"), "-dr", str(p)))
    elf = _run(_tool("nm"), "-S", str(out / "shim.elf"))
    return o, nm_syms(elf)


def run_rules(o, elf_syms):
    asm = parse_dis(o["el2_asm"]["dis"])
    syms = nm_syms(o["el2_asm"]["nm"])
    res = {}
    res["tlbi"] = check_msr(asm)
    for n in ("el2", "el2h", "el2cfg"):
        check_msr(parse_dis(o[n]["dis"]), c_object=True)
    res["hcr"] = check_hcr(asm)
    res["ld32"] = check_ld32(asm, syms)
    res["entry4"] = check_entry4(asm, syms)
    res["frame"] = check_callee_saved(asm, syms)
    res["seq"] = check_seq(asm)
    res["slots"] = check_frame(asm)
    res["pin"] = check_start_pinned(parse_dis(o["start_nodef"]["dis"]))
    check_vectors(syms, o["el2_asm"]["hdr"], elf_syms)
    res["start"] = check_start(parse_dis(o["start_nodef"]["dis"]), parse_dis(o["start"]["dis"]))
    res["reads"] = check_test_reads(parse_dis(o["shim"]["dis"]), o["resident"])
    res["hcrvm"] = check_hcr_vm(asm, syms)
    if o["resident"]:
        res["resident"] = check_resident_writes(o["others"])
        res["loader"] = check_loader_src(LOADER_C.read_text())
        res["build"] = check_build_sh(BUILD_SH.read_text(), run=True)
    return res


# ---- loader.c source needles (review el2-b2-report-r1 F3, F5): the host test covers payload_bootflags / payload_ranges_overlap, not the call sites ------------------------------
LOADER_C = pathlib.Path(__file__).resolve().parent.parent / "shim" / "loader.c"
_NEEDLE_BF = "payload_bootflags(1, el2_s2_on)"
_NEEDLE_OV = "payload_ranges_overlap((uint64_t)(uintptr_t)_head, (uint64_t)(uintptr_t)_end, p->phys_base, p->phys_base + p->mem_size)"


def check_loader_src(text):
    """The bit-62 argument is el2_s2_on (never a constant) and the EL2 image/XNU RAM overlap check exists, both inside #ifdef EL2_RESIDENT and the overlap before the segment copy."""
    blocks = re.findall(r"#ifdef EL2_RESIDENT\n(.*?)\n#endif", text, re.S)
    for needle in (_NEEDLE_BF, _NEEDLE_OV):
        if text.count(needle) != 1 or sum(b.count(needle) for b in blocks) != 1:
            raise AuditError(f"loader.c: '{needle[:48]}...' must appear exactly once, inside #ifdef EL2_RESIDENT (review r1 F3/F5)")
    ov = text.index(_NEEDLE_OV)
    if not ('return "EL2 image inside XNU RAM"' in text[ov:ov + 600] and ov < text.index("copy64(dst")):
        raise AuditError("loader.c: the EL2 image overlap check must refuse the boot and sit before the segment copy (review r1 F3)")
    return 2


# ---- build.sh flag rules (review el2-b2-report-r1 F6, spec 3.4 (b) design items 1 and 7) ---------------------------------------------------------------------------------------
BUILD_SH = pathlib.Path(__file__).resolve().parent.parent / "shim" / "build.sh"
_GS101_REFUSAL = '[ "$gs101" = 1 ] || { echo "--el2-trap needs a gs101 board" >&2; exit 2; }'
_SET_RES = 'CFLAGS="$CFLAGS -DEL2_RESIDENT"'
_PAIR_REFUSAL = "(B2) takes no --gpu-*/--wlan-*/--touch-probe/--udump/--qrlog flags"
# (arguments, message that must be on stderr with exit 2)
BUILD_REFUSALS = [
    (["--board", "sweet", "--el2-trap"], "--el2-trap needs a gs101 board"),
    (["--board", "bluejay", "--el2-trap", "--boot-xnu", "--gpu-handover"], _PAIR_REFUSAL),
    (["--board", "bluejay", "--el2-trap", "--boot-xnu", "--gpu-probe"], _PAIR_REFUSAL),
    (["--board", "bluejay", "--el2-trap", "--boot-xnu", "--wlan-hold"], _PAIR_REFUSAL),
    (["--board", "bluejay", "--el2-trap", "--boot-xnu", "--touch-probe"], _PAIR_REFUSAL),
    (["--board", "bluejay", "--el2-trap", "--boot-xnu", "--udump", "4"], _PAIR_REFUSAL),
    (["--board", "bluejay", "--el2-trap", "--boot-xnu", "--qrlog"], _PAIR_REFUSAL),
]


def check_build_sh(text, run=False):
    """Source needles (always) and, with run=True, the refusals executed against the real build.sh (each exits 2 before compiling anything)."""
    if text.count(_GS101_REFUSAL) != 1 or text.count(_PAIR_REFUSAL) != 1:
        raise AuditError("build.sh: the --el2-trap gs101 refusal and the B2 companion-flag refusal must each appear exactly once")
    blk = text[text.index('if [ "$el2_trap" = 1 ]; then'):]
    blk = blk[:blk.index("\nfi\n")]
    if not (_GS101_REFUSAL in blk and _PAIR_REFUSAL in blk and _SET_RES in blk and blk.index(_PAIR_REFUSAL) < blk.index(_SET_RES)):
        raise AuditError("build.sh: both refusals must sit in the el2_trap block, the companion refusal before -DEL2_RESIDENT is set")
    if run:
        for args, msg in BUILD_REFUSALS:
            p = subprocess.run(["bash", str(BUILD_SH), *args, "--out", "/tmp/el2-audit-refusal"], capture_output=True, text=True, cwd=BUILD_SH.parent)
            if p.returncode != 2 or msg not in p.stderr:
                raise AuditError(f"build.sh {' '.join(args)}: want exit 2 with '{msg}', got rc {p.returncode}: {p.stderr[-120:]}")
    return len(BUILD_REFUSALS)


# ---- mutation self-test: each rule must reject a mutated copy of the REAL disassembly -----------------------------------------------------------------------------------------

def _mut(text, pat, rep, count=1, flags=0):
    new, n = re.subn(pat, rep, text, count=count, flags=flags)
    if n == 0:
        raise RuntimeError(f"self-test mutant pattern did not match (the mutant would be vacuous): {pat!r}")	# not an AuditError: a vacuous mutant must not count as "rejected"
    return new


def _insert_after(text, pat, line, addr=None):
    """insert an instruction line after the first line matching pat; it takes the address of that line (the rules scan by address range)."""
    lines = text.splitlines()
    for k, ln in enumerate(lines):
        if re.search(pat, ln):
            a = re.match(r"\s*([0-9a-f]+):", ln).group(1)
            lines.insert(k + 1, f"  {a}:\t00000000 \t{line}")
            return "\n".join(lines) + "\n"
    raise RuntimeError(f"self-test: no line matches {pat!r} (a vacuous mutant must not count as rejected)")


def selftest(o, elf_syms):
    asm_t, syms = o["el2_asm"]["dis"], nm_syms(o["el2_asm"]["nm"])
    cases = []

    def case(name, fn, *args):
        try:
            fn(*args)
        except AuditError as e:
            cases.append((name, str(e)))
            return
        raise AuditError(f"self-test: mutant '{name}' was NOT rejected")

    # R1 msr set
    case("msr sp_el2 in el2_enable", lambda: check_msr(parse_dis(_insert_after(asm_t, r"\tmsr\tvttbr_el2, x2", "msr\tsp_el2, x1"))))
    case("msr sctlr_el2 in the clear path", lambda: check_msr(parse_dis(_insert_after(asm_t, r"\tmsr\thcr_el2, x1", "msr\tsctlr_el2, x1"))))
    case("a second tlbi in el2_enable", lambda: check_msr(parse_dis(_insert_after(asm_t, r"\ttlbi\tvmalls12e1", "tlbi\tvmalls12e1"))))
    case("smc in el2.S", lambda: check_msr(parse_dis(_insert_after(asm_t, r"\tmsr\tvttbr_el2, x2", "smc\t#0x0"))))
    case("mrs in a C EL2 object", lambda: check_msr(parse_dis(o["el2h"]["dis"].replace("\tbl\t", "\tmrs\tx0, esr_el2\n  0:\t00000000 \tbl\t", 1)), c_object=True))
    # R2 HCR literals only
    case("HCR literal RW|VM|bit16", lambda: check_hcr(parse_dis(_mut(asm_t, r"movk\tx0, #0x8000, lsl #16", "movk\tx0, #0x8001, lsl #16"))))
    case("HCR from mrs (read-modify-write)", lambda: check_hcr(parse_dis(_mut(_mut(asm_t, r"movk\tx0, #0x8000, lsl #16", "orr\tx0, x0, #0x1"), r"\tmov\tx0, #0x1\b", "mrs\tx0, hcr_el2"))))
    case("HCR produced by orr", lambda: check_hcr(parse_dis(_insert_after(asm_t, r"movk\tx0, #0x8000, lsl #16", "orr\tx0, x0, #0x1"))))
    case("HCR clear path via mrs/bic", lambda: check_hcr(parse_dis(_mut(asm_t, r"mov\tx1, #0x80000000[^\n]*", "mrs\tx1, hcr_el2"))))
    # R3 el2_ld32
    case("el2_ld32 keeps a value in x16", lambda: check_ld32(parse_dis(_insert_after(asm_t, r"\tadr\tx2, ", "mov\tx16, x2")), syms))
    case("el2_ld32 uses x17 after the load", lambda: check_ld32(parse_dis(_insert_after(asm_t, r"\tstr\twzr, \[x3\]", "str\twzr, [x17]")), syms))
    case("el2_ld32 writes x4", lambda: check_ld32(parse_dis(_mut(asm_t, r"(ldr\tw1, \[x3\])", "ldr\tw4, [x3]")), syms))
    case("el2_ld32 reads x4 after the load", lambda: check_ld32(parse_dis(_mut(asm_t, r"cbz\tw1,", "cbz\tw4,")), syms))
    case("el2_ld32 calls bl", lambda: check_ld32(parse_dis(_insert_after(asm_t, r"\tdsb\tsy", "bl\t0 <el2_handle>")), syms))
    case("el2_ld32 uses sp", lambda: check_ld32(parse_dis(_insert_after(asm_t, r"\tdsb\tsy", "str\tx0, [sp]")), syms))
    case("el2_ld32 without .size", lambda: check_ld32(parse_dis(asm_t), {k: (v[0], None) if k == "el2_ld32" else v for k, v in syms.items()}))
    # R4 entry 4
    case("entry 4 uses adr x1, el2_guard", lambda: check_entry4(parse_dis(_mut(asm_t, r"(\n\s*200:\t[0-9a-f]{8} \t)adr\tx16", r"\1adr\tx1")), syms))
    case("entry 4 branches through a trampoline", lambda: check_entry4(parse_dis(_mut(asm_t, r"b\.ne\t784 <el2_park_ec>[^\n]*\n(\s*)21c: R_AARCH64_CONDBR19\tel2_park_ec", r"b.ne\t800 <el2_tramp>\n\121c: R_AARCH64_CONDBR19\tel2_tramp")), syms))
    case("entry 4 uses sp", lambda: check_entry4(parse_dis(_insert_after(asm_t, r"\s210:\t", "str\tx16, [sp]")), syms))
    case("entry 4 calls bl", lambda: check_entry4(parse_dis(_insert_after(asm_t, r"\s210:\t", "bl\t0 <el2_handle>")), syms))
    case("entry 4 falls out of its slot", lambda: check_entry4(parse_dis(_mut(asm_t, r"\n\s*24[0-9a-f]:\t[0-9a-f]{8} \teret", "\n  24c:\t00000000 \tnop")), syms))
    case("entry 4 reads x0", lambda: check_entry4(parse_dis(_insert_after(asm_t, r"\s210:\t", "mov\tx16, x0")), syms))
    # R5 x19-x29
    case("el2_census writes x19", lambda: check_callee_saved(parse_dis(_insert_after(asm_t, r"mrs\tx1, sctlr_el2", "mov\tx19, x1")), syms))
    case("el2_enable writes x20", lambda: check_callee_saved(parse_dis(_insert_after(asm_t, r"\tmsr\tvttbr_el2, x2", "mov\tx20, x2")), syms))
    case("ldp x19,x20 before el2_frame_restore", lambda: check_callee_saved(parse_dis(_insert_after(asm_t, r"\tisb", "ldp\tx19, x20, [sp, #144]")), syms))
    case("x29 written by a mov inside the restore range", lambda: check_callee_saved(parse_dis(_insert_after(asm_t, r"ldr\tx30, \[sp, #240\]", "mov\tx29, x30")), syms))
    case("no eret after el2_frame_restore", lambda: check_callee_saved(parse_dis(_mut(asm_t, r"\teret\n\n[0-9a-f]+ <el2_ld32>", "\tnop\n\n0000000000000874 <el2_ld32>")), syms))
    # R6 vector offsets
    case("el2_vent8 not at +0x400", lambda: check_vectors({**syms, "el2_vent8": (syms["el2_vectors"][0] + 0x480, None)}, o["el2_asm"]["hdr"]))
    case("el2_vent4 not at +0x200", lambda: check_vectors({**syms, "el2_vent4": (syms["el2_vectors"][0] + 0x280, None)}, o["el2_asm"]["hdr"]))
    case("el2_vectors misaligned in the linked image", lambda: check_vectors(syms, o["el2_asm"]["hdr"], {**elf_syms, "el2_vectors": (0xd880, None)}))
    case("text alignment below 2**11", lambda: check_vectors(syms, o["el2_asm"]["hdr"].replace("2**11", "2**2")))
    # R7 start.o
    st, sn = o["start"]["dis"], o["start_nodef"]["dis"]
    case("start.o: an extra msr", lambda: check_start(parse_dis(sn), parse_dis(_insert_after(st, r"msr\tcptr_el2, xzr", "msr\tsctlr_el2, x2"))))
    case("start.o: site (a) loads another symbol", lambda: check_start(parse_dis(sn), parse_dis(_mut(st, r"R_AARCH64_ADR_PREL_LO21\t_el2_stack_top", "R_AARCH64_ADR_PREL_LO21\t_stack_top"))))
    case("start.o: bl el2_enable moved before icc_sre", lambda: check_start(parse_dis(sn), parse_dis(_mut(_mut(st, r"bl\t0 <el2_enable>", "nop"), r"(mrs\tx2, icc_sre_el2)", r"bl\t0 <el2_enable>\n  0:\t00000000 \t\1"))))
    case("start.o: hcr write changed", lambda: check_start(parse_dis(sn), parse_dis(_mut(st, r"mov\tx2, #0x80000000", "mov\tx2, #0x80000001"))))
    case("start.o: site (b) missing", lambda: check_start(parse_dis(sn), parse_dis(_mut(st, r"bl\t0 <el2_census>\n[^\n]*\n", ""))))
    case("start.o: nodef build contains an el2 call", lambda: check_start(parse_dis(_insert_after(sn, r"msr\tcptr_el2, xzr", "bl\t0 <el2_census>")), parse_dis(st)))
    # R9 test reads
    sh = o["shim"]["dis"]
    case("GICD read compiled as ldp", lambda: check_test_reads(parse_dis(_mut(sh, r"(mov\tx0, #0x10400000[^\n]*\n[^\n]*\t)ldr\tw1, \[x0\]", r"\1ldp\tw1, w2, [x0]"))))
    case("GICD read with writeback", lambda: check_test_reads(parse_dis(_mut(sh, r"(mov\tx0, #0x10400000[^\n]*\n[^\n]*\t)ldr\tw1, \[x0\]", r"\1ldr\tw1, [x0], #4"))))
    case("GICD read as ldrb", lambda: check_test_reads(parse_dis(_mut(sh, r"(mov\tx0, #0x10400000[^\n]*\n[^\n]*\t)ldr\tw1, \[x0\]", r"\1ldrb\tw1, [x0]"))))
    res_mode = o["resident"]
    case("an extra GICD read", lambda: check_test_reads(parse_dis(_insert_after(sh, r"mov\tx0, #0x10400000", "ldr\tw3, [x0]")), res_mode))
    if not res_mode:
        case("the T1 read missing", lambda: check_test_reads(parse_dis(_mut(sh, r"R_AARCH64_LDST64_ABS_LO12_NC\tel2_t1_pa", "R_AARCH64_LDST64_ABS_LO12_NC\tel2_s2_on"))))
    else:	# EL2_RESIDENT (R9 resident, R12): the T1 test read must stay compiled out; no object but el2_asm.o writes the stage-2 / vector registers
        def t1_back():
            t = _insert_after(sh, r"mov\tx0, #0x10400000", "ldr\tx9, 0 <el2_t1_pa>")
            a = re.search(r"mov\tx0, #0x10400000", t)
            addr = re.findall(r"^\s*([0-9a-f]+):[^\n]*\n\s*[0-9a-f]+:\t00000000 \tldr\tx9", t, re.M)[0]
            t = t.replace(f"{addr}:\t00000000 \tldr\tx9, 0 <el2_t1_pa>\n", f"{addr}:\t00000000 \tldr\tx9, 0 <el2_t1_pa>\n  {addr}:\tR_AARCH64_LDST64_ABS_LO12_NC\tel2_t1_pa\n", 1)
            assert a
            return check_test_reads(parse_dis(t), True)
        case("the T1 test read compiled back in (resident)", t1_back)
        case("the GICD test reads compiled back in (resident, 3 loads)", lambda: check_test_reads(parse_dis(_insert_after(sh, r"mov\tx0, #0x10400000", "ldr\tw3, [x0]")), True))
        oth = o["others"]

        def with_extra(name, line, pat=None):
            ob = dict(oth)
            base = o["el2_asm"]["dis"] if name == "el2_asm" else None
            if base is not None:
                ob[name] = parse_dis(_insert_after(base, pat, line))
            else:
                ob[name] = list(oth[name]) + parse_dis(f"00000000 <fake>:\n  0:\t00000000 \t{line}\n")
            return check_resident_writes(ob)
        case("loader.o writes hcr_el2 (resident)", lambda: with_extra("loader", "msr\thcr_el2, x0"))
        case("loader.o clears VM through vttbr_el2 (resident)", lambda: with_extra("loader", "msr\tvttbr_el2, xzr"))
        case("shim.o writes vbar_el2 (resident)", lambda: with_extra("shim", "msr\tvbar_el2, x1"))
        case("payload.o writes a raw EL2 register encoding (resident)", lambda: with_extra("payload", "msr\ts3_4_c1_c1_0, x0"))
        case("el2h.o writes vtcr_el2 (resident)", lambda: with_extra("el2h", "msr\tvtcr_el2, x0"))
        case("vbar_el2 written in el2.S (resident)", lambda: with_extra("el2_asm", "msr\tvbar_el2, x1", r"\tmrs\tx1, spsr_el2"))
        case("vttbr_el2 written in the VM-clear path (resident)", lambda: with_extra("el2_asm", "msr\tvttbr_el2, x1", r"\tmsr\thcr_el2, x1"))
        case("hcr_el2 written in el2_ld32 (resident)", lambda: with_extra("el2_asm", "msr\thcr_el2, x1", r"\tstr\twzr, \[x2\]"))
    # R13 el2_hcr_vm is a read-only leaf
    case("el2_hcr_vm gains a write (R13)", lambda: check_hcr_vm(parse_dis(_insert_after(asm_t, r"\tmrs\tx0, hcr_el2", "msr\thcr_el2, x0")), syms))
    case("el2_hcr_vm returns more than VM (R13)", lambda: check_hcr_vm(parse_dis(_mut(asm_t, r"(<el2_hcr_vm>:\n[^\n]*\tmrs\tx0, hcr_el2\n[^\n]*\t)and\tx0, x0, #0x1", r"\1orr\tx0, x0, #0x1")), syms))
    # R1/R2/R10/R11/R7b (review el2-probe-job2-r1 F1, F2, F3, F6): the escapes the r1 reviewer found, now rejected
    case("tlbi removed from el2_enable", lambda: check_msr(parse_dis(_mut(asm_t, r"(<el2_enable>:(?:\n[^\n]*)*?)\ttlbi\tvmalls12e1", r"\1\tnop"))))
    case("tlbi removed from the VM-clear path", lambda: check_msr(parse_dis(_mut(asm_t, r"(<el2_sync_lower>:(?:\n[^\n]*)*?)\ttlbi\tvmalls12e1", r"\1\tnop"))))
    case("VM-clear HCR write removed", lambda: check_hcr(parse_dis(_mut(asm_t, r"\tmsr\thcr_el2, x1", "\tnop"))))
    case("enable HCR write removed", lambda: check_hcr(parse_dis(_mut(asm_t, r"\tmsr\thcr_el2, x0", "\tnop"))))
    case("dsb ish removed", lambda: check_seq(parse_dis(_mut(asm_t, r"\tdsb\tish", "\tnop"))))
    case("dsb nsh removed", lambda: check_seq(parse_dis(_mut(asm_t, r"\tdsb\tnsh", "\tnop"))))
    case("msr vttbr removed", lambda: check_seq(parse_dis(_mut(asm_t, r"\tmsr\tvttbr_el2, x2", "\tnop"))))
    case("isb after the clear removed", lambda: check_seq(parse_dis(_mut(asm_t, r"(\tmsr\thcr_el2, x1\n[^\n]*\t)isb", r"\1nop"))))
    case("park dispatch removed", lambda: check_seq(parse_dis(_mut(asm_t, r"\tb\.eq\t[^\n]*<el2_park_trap>", "\tnop"))))
    case("unknown action falls into the VM clear (F5)", lambda: check_seq(parse_dis(_mut(asm_t, r"\tb\.ne\t[^\n]*<el2_park_trap>", "\tnop"))))
    case("first pool invalidate removed", lambda: check_seq(parse_dis(_mut(asm_t, r"\tdc\tivac, x0", "\tnop"))))
    case("dc civac in el2_enable", lambda: check_msr(parse_dis(_mut(asm_t, r"\tdc\tivac, x0", "\tdc\tcivac, x0"))))
    case("dc ivac outside el2_enable", lambda: check_msr(parse_dis(_insert_after(asm_t, r"\tmrs\tx1, spsr_el2", "dc\tivac, x1"))))
    case("dc in a C EL2 object", lambda: check_msr(parse_dis(o["el2h"]["dis"].replace("\tbl\t", "\tdc\tivac, x0\n  0:\t00000000 \tbl\t", 1)), c_object=True))
    case("entry 8 drops stp x18, x19", lambda: check_frame(parse_dis(_mut(asm_t, r"\tstp\tx18, x19, \[sp, #144\]", "\tnop"))))
    case("restore drops ldp x28, x29", lambda: check_frame(parse_dis(_mut(asm_t, r"\tldp\tx28, x29, \[sp, #224\]", "\tnop"))))
    case("frame not released before eret", lambda: check_frame(parse_dis(_mut(asm_t, r"\tadd\tsp, sp, #0x110", "\tnop"))))
    case("unguarded start.S edit (nodef differs from pre-B1)", lambda: check_start_pinned(parse_dis(_insert_after(sn, r"msr\tcptr_el2, xzr", "isb"))))
    if o["resident"]:
        lt = LOADER_C.read_text()
        case("loader.c: bit 62 argument is a constant 1", lambda: check_loader_src(lt.replace(_NEEDLE_BF, "payload_bootflags(1, 1)")))
        case("loader.c: bit 62 call outside EL2_RESIDENT", lambda: check_loader_src(lt.replace("#ifdef EL2_RESIDENT\n\tuint64_t bootflags", "#if 1\n\tuint64_t bootflags")))
        bt = BUILD_SH.read_text()
        case("build.sh: gs101 refusal deleted", lambda: check_build_sh(bt.replace(_GS101_REFUSAL, ":")))
        case("build.sh: B2 companion-flag refusal deleted", lambda: check_build_sh(bt.replace(_PAIR_REFUSAL, "x")))
        case("build.sh: -DEL2_RESIDENT set before the companion refusal", lambda: check_build_sh(bt.replace('\t\tCFLAGS="$CFLAGS -DEL2_RESIDENT"\n', "", 1).replace("\tif [ \"$boot_xnu\" = 1 ]; then\n\t\t# review el2-b2", "\tCFLAGS=\"$CFLAGS -DEL2_RESIDENT\"\n\tif [ \"$boot_xnu\" = 1 ]; then\n\t\t# review el2-b2", 1)))
        case("loader.c: overlap check removed", lambda: check_loader_src(lt.replace(_NEEDLE_OV, "0")))
        case("loader.c: overlap check no longer refuses", lambda: check_loader_src(lt.replace('return "EL2 image inside XNU RAM"', 'break')))
    return cases


def audit(out):
    o, elf_syms = load(out)
    res = run_rules(o, elf_syms)
    cases = selftest(o, elf_syms)
    for name, why in cases:
        print(f"self-test caught: {name} -> {why[:150]}")
    print(f"PASS el2 object self-test: {len(cases)}/{len(cases)} mutants rejected")
    if o["resident"]:
        print(f"PASS el2 resident audit (EL2_RESIDENT): {res['resident']} stage-2 register writes, all in el2_asm.o el2_enable / the VM-clear path; no stage-2/vector write in any other object but start.o; "
              f"el2_hcr_vm read-only ({res['hcrvm']} insns); shim.o has {res['reads'][0]} GICD loads and no T1 read; loader.c: bit-62 argument el2_s2_on + EL2-image/XNU-RAM overlap refusal; build.sh: gs101 + {res['build']} B2 flag refusals executed")
    print(f"PASS el2 object audit: msr set + one tlbi per path ({res['tlbi']}), HCR literals ({res['hcr']} writes), el2_ld32 range ({res['ld32']} insns), entry 4 ({res['entry4']} insns), "
          f"x19-x29 bounded by el2_frame_restore ({res['frame']:#x} bytes), vector offsets, start.o vs start_nodef.o (+{res['start']} insns at sites a-d), "
          f"test reads ({res['reads'][0]} GICD + {res['reads'][1]} T1 single ldr w" + ("" if o["resident"] else f" = traps {res['reads'][0] + res['reads'][1]}") + "), "
          f"barrier/sysreg sequences + dispatch (R10), frame x0-x30 ({res['slots']} slots, R11), start_nodef pinned to pre-B1 {res['pin']} (R7b)")
