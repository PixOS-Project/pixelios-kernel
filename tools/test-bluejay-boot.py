#!/usr/bin/env python3
"""Boot-image and read-only-safety checks for the bluejay probe shim.

check_ufs_safety() is the host-side enforcement of docs/SAFETY.md's
"no UFS write" rule. The primary control is compile-time: scsi() in
shim/ufs.c takes enum scsi_dir, which has no write value, and derives the
UPIU flags and UTRD DD bits from it alone; enum upiu_type gates the
transaction types to NOP OUT (v2 liveness probe) and COMMAND. The scan
below is the second layer: it rejects write-type SCSI opcodes and
query-write opcodes however they are spelled (hex/decimal, assignment,
initializer) in the CDB opcode slot, rejects any flags-byte literal
outside {0x00, 0x40}, rejects any transaction-type literal outside
{0x00 NOP OUT, 0x01 COMMAND}, and rejects the QUERY REQUEST UPIU type
byte anywhere. ufs_safety_selftest() feeds known-bad sources (the five
review-2 bypasses plus NOP-era pointer-form bypasses) and asserts each
is caught, so the scan cannot silently rot again.

check_usb_safety() enforces the stage-1 USB probe's (shim/usbprobe.c
AND usbprobe.h) "STRICTLY read-only" rule: no store to a register
address however spelled - write32/try_write32-style helper identifiers,
kernel writel spellings, *(volatile ...)= dereference stores, stores
through declared volatile pointers/arrays (x[i]=, *x=, x->f=), and
str-to-MMIO inline asm - plus a blanket ban on asm/__asm,
memcpy/memmove/memset and __builtin_mem*/__atomic*/__sync* tokens, which
can all hide an MMIO store from the spelling checks above. Read-only
volatile pointer declarations are not stores (review-1 H1 false
positive): declaration statements are blanked before the store patterns
run. usb_safety_selftest() feeds known-bad sources and asserts each is
caught, same style as the UFS checker.

check_usb_object() is the primary gate (review-1 H1): an OBJECT audit of
the built shim/out/usbprobe.o via aarch64-none-elf-objdump -dr/-h and
nm -u. Undefined symbols must stay within the probe's sanctioned
external surface, .data/.bss must be empty, smc/hvc/msr/dc/ic/at/tlbi
and the LSE atomic families (stadd/cas/swp/...) are banned, and every
store's base register must be provably stack-derived (sp; add/sub/mov
chains off sp or a spill slot holding such a register; or the x0 dst
parameter inside static put_* helpers). Anything the audit cannot
classify is an error - it fails closed. usb_object_selftest() feeds
synthetic bad objects (non-stack store, smc, LSE, stack pivots,
unclassifiable instructions, ...) and asserts each is rejected on
every run.
"""
import argparse
import os
import pathlib
import re
import shutil
import struct
import subprocess
import tempfile

SCSI_WRITE_OPCODES = {
    0x0A: "WRITE(6)",
    0x2A: "WRITE(10)",
    0xAA: "WRITE(12)",
    0x8A: "WRITE(16)",
    0x42: "UNMAP",
    0x04: "FORMAT UNIT",
    0x41: "WRITE SAME(10)",
    0x93: "WRITE SAME(16)",
}

UFS_QUERY_WRITE_OPCODES = {
    0x02: "Set Flag",
    0x04: "Clear Flag",
    0x06: "Toggle Flag",
    0x07: "Write Attribute",
    0x08: "Write Descriptor",
}

# Bytes that must never appear in a CDB opcode slot (CDB[0], the first
# element of a CDB brace initializer, or the ucd.cmd[16+i] UPIU slot).
FORBIDDEN_OPCODE_BYTES = {
    **SCSI_WRITE_OPCODES,
    **UFS_QUERY_WRITE_OPCODES,
    0x16: "QUERY REQUEST UPIU",
}

# The only UPIU data-direction flag bytes a read-only probe may contain:
# 0x00 (no data) and 0x40 (R, data-in). 0x20 (W) is a write.
UPIU_READ_FLAGS = {0x00, 0x40}

# The only UPIU transaction types the probe may issue: NOP OUT (path
# liveness test, no SCSI layer) and COMMAND (carries the read-only CDBs).
# QUERY REQUEST and DATA OUT are the UFS write paths and are not allowed.
UPIU_ALLOWED_TYPES = {0x00: "NOP OUT", 0x01: "COMMAND"}

# Matches the command-UPIU header byte writes whether the code spells the
# command descriptor as ucd.cmd[...] or through a pointer, u->cmd[...].
_CMD_SLOT = r"(?:ucd\s*\.\s*cmd|\w+\s*->\s*cmd)"

_INT_RE = re.compile(r"(?<![\w.])(0[xX][0-9a-fA-F]+|\d+)[uUlL]*")


def _parse_int(token: str) -> int:
    try:
        return int(token, 0)
    except ValueError:
        return int(token, 8)  # C octal like 042


def _bare_literal(rhs: str):
    """Return the value of rhs if it is nothing but an integer literal
    (optionally parenthesized/cast), else None. Used so the opcode scans
    flag constant assignments like `w[0] = 0x2a` without tripping on
    register arithmetic like `(1u << 28) | ((x ? 2u : 0u) << 25)`."""
    rhs = re.sub(r"\b(?:u?int(?:8|16|32|64)?_t|char|signed|unsigned|const|volatile)\b", " ", rhs)
    rhs = re.sub(r"[()\s]+", "", rhs)
    m = re.fullmatch(r"(0[xX][0-9a-fA-F]+|\d+)[uUlL]*", rhs)
    return _parse_int(m.group(1)) if m else None


def _int_literals(text: str):
    for m in _INT_RE.finditer(text):
        yield _parse_int(m.group(1)), m.group(1)


def _strip_c_comments_and_strings(src: str) -> str:
    """Blank out // and /* */ comments and string/char literals so only
    executable tokens remain for the opcode scan."""
    out = []
    i, n = 0, len(src)
    while i < n:
        two = src[i : i + 2]
        if two == "/*":
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
        elif two == "//":
            end = src.find("\n", i)
            i = n if end < 0 else end
            out.append(" ")
        elif src[i] in ('"', "'"):
            quote = src[i]
            j = i + 1
            while j < n and src[j] != quote:
                j += 2 if src[j] == "\\" else 1
            i = j + 1 if j < n else n
            out.append(quote + quote)
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def check_ufs_safety_text(src: str, name: str = "ufs.c") -> None:
    text = _strip_c_comments_and_strings(src)

    # The compile-time control must be present: scsi() plumbing without the
    # read-only direction enum means someone rewrote the interface, and the
    # UPIU type enum gates the transaction types to NOP OUT and COMMAND.
    assert "enum scsi_dir" in text and "SCSI_DIR_READ" in text, (
        f"{name}: read-only direction enum missing from the scsi() plumbing"
    )
    assert "enum upiu_type" in text and "UPIU_NOP_OUT" in text and "UPIU_COMMAND" in text, (
        f"{name}: upiu type enum missing; NOP OUT and COMMAND are the only allowed types"
    )

    # (a) First element of any array brace initializer is a CDB opcode slot
    # when the array is a CDB; catch hex and decimal spellings there.
    for m in re.finditer(r"\w+\s*\[\s*[0-9]*\s*\]\s*=\s*\{", text):
        end = text.find("}", m.end())
        if end < 0:
            continue
        first_element = text[m.end() : end].split(",")[0]
        for value, tok in _int_literals(first_element):
            if value in FORBIDDEN_OPCODE_BYTES:
                raise AssertionError(
                    f"{name}: forbidden opcode {tok} "
                    f"({FORBIDDEN_OPCODE_BYTES[value]}) as first CDB element in initializer"
                )

    # (b) Any index-0 assignment: x[0] = <bare opcode constant>, however
    # the array was built (assignment instead of an initializer).
    for m in re.finditer(r"\w+\s*\[\s*0\s*\]\s*=(?!=)\s*([^;]*)", text):
        value = _bare_literal(m.group(1))
        if value in FORBIDDEN_OPCODE_BYTES:
            raise AssertionError(
                f"{name}: forbidden opcode {value:#04x} "
                f"({FORBIDDEN_OPCODE_BYTES[value]}) in a CDB[0] assignment"
            )

    # (c) The CDB area of the command UPIU itself: ucd.cmd[16(+i)] = <bare
    # opcode constant>, in object or pointer spelling.
    for m in re.finditer(_CMD_SLOT + r"\s*\[\s*16(?:\s*\+\s*\w+)?\s*\]\s*=(?!=)\s*([^;]*)", text):
        value = _bare_literal(m.group(1))
        if value in FORBIDDEN_OPCODE_BYTES:
            raise AssertionError(
                f"{name}: forbidden opcode {value:#04x} "
                f"({FORBIDDEN_OPCODE_BYTES[value]}) written into the CDB area of the command UPIU"
            )

    # (d) UPIU data-direction flags byte: every literal assigned to the
    # command UPIU flags byte (object or pointer spelling) must be a
    # read/none flag. Catches 0x20 in direct, ternary, and OR-assignment
    # forms, in hex or decimal.
    for m in re.finditer(_CMD_SLOT + r"\s*\[\s*1\s*\]\s*([^;]*)", text):
        for value, tok in _int_literals(m.group(1)):
            if value not in UPIU_READ_FLAGS:
                raise AssertionError(
                    f"{name}: command UPIU flags literal {tok} is neither 0x00 nor 0x40; "
                    "the UPIU write flag is 0x20 and no other direction may be requested"
                )

    # (f) UPIU transaction type byte: every literal in the type slot
    # (object or pointer spelling) must be NOP OUT or COMMAND. The NOP OUT
    # type was allow-listed for the v2 liveness probe; QUERY REQUEST and
    # DATA OUT stay out.
    for m in re.finditer(_CMD_SLOT + r"\s*\[\s*0\s*\]\s*([^;]*)", text):
        for value, tok in _int_literals(m.group(1)):
            if value not in UPIU_ALLOWED_TYPES:
                raise AssertionError(
                    f"{name}: command UPIU transaction-type literal {tok} is not NOP OUT "
                    "(0x00) or COMMAND (0x01)"
                )

    # (e) No QUERY REQUEST UPIU may be built at all (any hex spelling).
    assert not re.search(r"0[xX]16\b", text), (
        f"{name}: QUERY REQUEST UPIU (0x16) found; this probe must not issue UFS queries"
    )


def check_ufs_safety(ufs_c_path: pathlib.Path) -> None:
    assert ufs_c_path.is_file(), f"ufs.c not found: {ufs_c_path}"
    check_ufs_safety_text(ufs_c_path.read_text(encoding="utf-8"), str(ufs_c_path))

def ufs_safety_selftest() -> int:
    """Feed known-bad sources to the checker and assert each is caught;
    a clean control source must pass. Returns the count caught."""
    benign = (
        "enum scsi_dir { SCSI_DIR_NONE = 0, SCSI_DIR_READ = 1 };\n"
        "enum upiu_type { UPIU_NOP_OUT = 0x00, UPIU_COMMAND = 0x01 };\n"
        "uint8_t cdb[10] = { 0x28, 0, 0, 0, 0, 0, 0, 0, 1, 0 };\n"
        "cdb[0] = 0x25;\n"
        "ucd.cmd[0] = 0x01;\n"
        "ucd.cmd[1] = dir == SCSI_DIR_READ ? 0x40 : 0x00;\n"
        "ucd.cmd[16 + i] = cdb[i];\n"
        "u->cmd[0] = type;\n"
        "u->cmd[1] = dir == SCSI_DIR_READ ? 0x40 : 0x00;\n"
        "u->cmd[16 + i] = cdb[i];\n"
    )
    cases = [
        ("UPIU write flag 0x20 assigned to ucd.cmd[1]", "ucd.cmd[1] = 0x20;\n"),
        ("UPIU write flag 0x20 hidden in a ternary", "ucd.cmd[1] = len ? 0x20 : 0x00;\n"),
        ("UPIU write flag 0x20 via pointer spelling", "u->cmd[1] = 0x20;\n"),
        ("decimal WRITE(10) opcode 42 in a CDB initializer", "uint8_t w[6] = { 42, 0, 0, 0, 0, 0 };\n"),
        ("hex WRITE(10) opcode built by CDB[0] assignment", "uint8_t w[6]; w[0] = 0x2a;\n"),
        ("hex WRITE(10) opcode in a CDB initializer", "uint8_t w[6] = { 0x2A, 0, 0, 0, 0, 0 };\n"),
        ("UPIU transaction type 0x0F (not NOP OUT/COMMAND)", "u->cmd[0] = 0x0f;\n"),
        ("QUERY REQUEST UPIU 0x16 as transaction type", "ucd->cmd[0] = 0x16;\n"),
    ]
    check_ufs_safety_text(benign, "selftest control")  # must not raise
    caught = 0
    for label, bad in cases:
        try:
            check_ufs_safety_text(benign + bad, f"selftest: {label}")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: {label} -> {exc}")
        else:
            raise AssertionError(f"ufs safety self-test FAILED to catch: {label}")
    print(f"PASS ufs safety self-test: {caught}/{len(cases)} known-bad sources rejected")
    return caught


# Matches a direct store through a cast dereference: the lvalue (and any
# index) sits between the cast's closing paren and the assignment.
_VOLATILE_DEREF_STORE = re.compile(
    r"\*\s*\(\s*volatile\b[^)]*\)\s*(?:\[[^\]]*\]\s*)?[^;=]*=(?!=)"
)

# Matches any write32-family or kernel MMIO-write identifier.
_MMIO_WRITE_ID = re.compile(
    r"\b\w*write32\w*\b|\b\w*writel\w*\b|\biowrite\w*\b|\boutsw?\b|\bout[lwd]\b"
)

# Matches an inline-asm statement containing an AArch64 store mnemonic
# (checked on comment-stripped text with string literals intact, since
# the mnemonic lives inside the asm string).
_ASM_STORE = re.compile(
    r"\b(?:__asm__|asm)\b[^;]*?\b(?:str[bh]?|stp|stlr\w*|sttr\w*|stur\w*|stxp?)\b"
)


def _strip_c_comments_keep_strings(src: str) -> str:
    """Blank out // and /* */ comments but keep string literals (the asm
    store-mnemonic scan needs the literal contents)."""
    out = []
    i, n = 0, len(src)
    while i < n:
        two = src[i : i + 2]
        if two == "/*":
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
        elif two == "//":
            end = src.find("\n", i)
            i = n if end < 0 else end
            out.append(" ")
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _volatile_pointer_vars(text: str):
    """Names declared as pointers to volatile ('volatile T *name;',
    'volatile T *const name;', including function PARAMETERS - ')' also
    terminates the declarator) or volatile arrays ('volatile T name[N];')
    - the lvalue spellings a store to a register window could hide
    behind."""
    names = set()
    for m in re.finditer(r"\bvolatile\b[^;()=]*?\*\s*(?:const\s+)?(\w+)\s*(?:=|;|,|\))", text):
        names.add(m.group(1))
    for m in re.finditer(r"\bvolatile\b[^;()=]*?\b(\w+)\s*\[[^\]]*\]\s*[;=]", text):
        names.add(m.group(1))
    return names

# Tokens that must not appear at all in the probe sources: inline asm
# (any spelling), the mem* family (a store behind a call), and compiler
# atomics (an RMW store). The object audit below catches their compiled
# forms; this ban catches them at the source level first (review-1 H1).
_USB_BANNED_SOURCE_TOKENS = re.compile(
    r"\b(?:asm|__asm|__asm__)\b"
    r"|\b(?:memcpy|memmove|memset)\b"
    r"|\b__builtin_mem\w*"
    r"|\b__atomic\w*"
    r"|\b__sync\w*"
)


def check_usb_safety_text(src: str, name: str = "usbprobe.c") -> None:
    """The stage-1 USB probe must contain no store to a register address.
    Reject write32/try_write32 identifiers, kernel writel spellings,
    *(volatile ...)= stores, stores through declared volatile pointers
    or arrays, and str-to-MMIO inline asm."""
    code = _strip_c_comments_and_strings(src)
    code_keep = _strip_c_comments_keep_strings(src)

    m = _MMIO_WRITE_ID.search(code)
    if m:
        raise AssertionError(
            f"{name}: MMIO store identifier '{m.group(0)}' in a read-only USB probe"
        )

    m = _USB_BANNED_SOURCE_TOKENS.search(code_keep)
    if m:
        raise AssertionError(
            f"{name}: banned token '{m.group(0)}' in a read-only USB probe "
            "(asm / mem* / atomics can hide an MMIO store)"
        )

    if _VOLATILE_DEREF_STORE.search(code):
        raise AssertionError(
            f"{name}: '*(volatile ...)= ' store in a read-only USB probe"
        )

    # A declaration cannot contain a store, but its declarator looks like
    # one ('*r = <init>', 'regs[N] = {...}') - matching it rejected
    # read-only declarations like 'volatile uint32_t *r = ...; x = *r;'
    # (review-1 H1 false positive). Blank declaration statements, then
    # scan only the remaining (executable) text for stores.
    body = code
    for var in sorted(_volatile_pointer_vars(code)):
        body = re.sub(
            rf"\bvolatile\b[^;{{}}=]*\*+\s*(?:const\s+)?{re.escape(var)}\b[^;{{}}]*;?",
            " ",
            body,
        )
        body = re.sub(
            rf"\bvolatile\b[^;{{}}=]*\b{re.escape(var)}\s*\[[^\];{{}}]*\]\s*(?:=[^;{{}}]*)?;",
            " ",
            body,
        )
    for var in sorted(_volatile_pointer_vars(code)):
        for pat in (
            rf"\b{re.escape(var)}\s*\[[^\]]*\]\s*=(?!=)",
            rf"\*\s*{re.escape(var)}\s*=(?!=)",
            rf"\b{re.escape(var)}\s*->\s*\w+\s*=(?!=)",
        ):
            m = re.search(pat, body)
            if m:
                raise AssertionError(
                    f"{name}: store through volatile pointer '{var}' "
                    f"('{m.group(0).strip()}') in a read-only USB probe"
                )

    m = _ASM_STORE.search(code_keep)
    if m:
        raise AssertionError(
            f"{name}: inline-asm store instruction in a read-only USB probe"
        )


def check_usb_safety(usb_c_path: pathlib.Path) -> None:
    assert usb_c_path.is_file(), f"usbprobe.c not found: {usb_c_path}"
    check_usb_safety_text(usb_c_path.read_text(encoding="utf-8"), str(usb_c_path))


def usb_safety_selftest() -> int:
    """Feed known-bad store spellings to the USB checker and assert each
    is caught; the real probe-shaped control must pass."""
    benign = (
        "#include \"usbprobe.h\"\n"
        "struct usb_reg { int ok; uint32_t v; };\n"
        "static struct usb_reg usb_read(uintptr_t a)\n"
        "{ struct usb_reg r; r.ok = try_read32(a, &r.v); if (!r.ok) r.v = 0; return r; }\n"
        "static unsigned put_reg(char *dst, unsigned cap, unsigned pos,\n"
        "                        const char *key, const struct usb_reg *r)\n"
        "{ pos = put_txt(dst, cap, pos, key); pos = put_txt(dst, cap, pos, \"=\");\n"
        "  if (!r->ok) return put_txt(dst, cap, pos, \"unreadable\");\n"
        "  return put_hex(dst, cap, pos, r->v, 8); }\n"
        "char line[96]; unsigned p = 0;\n"
        "line[p] = 0;\n"
    )
    cases = [
        ("try_write32 helper", "try_write32(USBPROBE_DWC3_BASE + USBPROBE_DCTL_OFF, 0);\n"),
        ("write32 embedded in a longer identifier", "usb_write32(USBPROBE_DWC3_BASE, 0);\n"),
        ("kernel writel spelling", "writel(0, base + USBPROBE_GCTL_OFF);\n"),
        ("direct *(volatile uint32_t *)= store", "*(volatile uint32_t *)(base + off) = 1;\n"),
        ("store through a declared volatile pointer", "volatile uint32_t *r = (void *)base; r[4] = 1;\n"),
        ("member store through a volatile struct pointer", "volatile struct dwc3 *d = (void *)base; d->dctl = 1;\n"),
        ("str-to-MMIO inline asm", "__asm__ volatile(\"str %w0, [%1]\" : : \"r\"(v), \"r\"(a));\n"),
        ("volatile register-file array store", "volatile uint32_t regs[8]; regs[0] = 1;\n"),
        ("volatile pointer parameter store", "static void f(volatile uint32_t *r) { r[1] = 0; }\n"),
        ("volatile T *const pointer store", "volatile uint32_t *const p = (void *)base; *p = 1;\n"),
        ("inline __asm__ smc", "__asm__ volatile(\"smc #0\");\n"),
        ("plain asm keyword", "asm(\"str %w0, [%1]\" : : \"r\"(v), \"r\"(a));\n"),
        ("memcpy to a register window", "memcpy((void *)(base + off), &v, 4);\n"),
        ("__builtin_memset", "__builtin_memset((void *)base, 0, 4);\n"),
        ("__atomic_store_n", "__atomic_store_n((uint32_t *)base, 0, 0);\n"),
        ("__sync compare-and-swap", "__sync_val_compare_and_swap((int *)base, 0, 1);\n"),
    ]
    check_usb_safety_text(benign, "selftest control")  # must not raise
    # review-1 H1 false positive: a READ-ONLY volatile declaration must
    # not be rejected (the old scan matched the declarator '*r =').
    check_usb_safety_text(
        benign + "volatile uint32_t *ro = (void *)0x11110000;\nunsigned x = *ro;\n",
        "selftest control: read-only volatile declaration")  # must not raise
    caught = 0
    for label, bad in cases:
        try:
            check_usb_safety_text(benign + bad, f"selftest: {label}")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: {label} -> {exc}")
        else:
            raise AssertionError(f"usb safety self-test FAILED to catch: {label}")
    print(f"PASS usb safety self-test: {caught}/{len(cases)} known-bad store spellings rejected")
    return caught

# ---- stage-1 USB probe: OBJECT-level read-only audit (review-1 H1) ----
#
# The source scan above is a spelling check; review-1 showed 21 of 22
# tried bypass spellings got through it. This audit checks the BUILT
# object (shim/out/usbprobe.o), so anything that compiles to a store is
# seen as a store, whatever the C spelling was:
#
#   (a) nm -u: undefined symbols must be a subset of the sanctioned
#       external surface below (extending it is a reviewable decision);
#   (b) objdump -h: .data and .bss must be empty (no writable static
#       state the probe could corrupt or be corrupted through);
#   (c) no smc/hvc/msr/dc/ic/at/tlbi (system-register and cache writes,
#       dc zva writes cache lines) and no LSE atomics (stadd/stset/cas/
#   swp/... all modify memory);
#   (d) every store instruction's base register must be provably the
#       stack: sp itself; a register whose definition on EVERY path from
#       the function entry is add/sub xN, sp, #imm|reg, a plain
#       mov xN, xM off such a register, an add/sub xN, xM, #|reg whose
#       base xM is such a register (loop-index addressing into a stack
#       buffer), a load from a spill slot that last received such a
#       register, or - only inside a STATIC put_* helper - a register of
#       the x0 dst-parameter chain. The proof is a per-basic-block
#       dataflow with intersection at joins (see _usb_audit_function).
#
# Fail closed: a store whose base cannot be proven, an unparseable
# operand, or ANY instruction outside the understood mnemonic families
# is an error, never a pass.

_USB_ALLOWED_UNDEF = {
    "try_read32",          # fault-guarded MMIO read (shim.c)
    "con_str",             # console/ramlog line out (console.c)
    "fdt_collect_memmap",  # reserved-memory walk (fdt.c)
    "pstore_zone_addr",    # validated ramoops zone base (pstore.c, L2)
    "pstore_zone_size",    # validated ramoops zone size (pstore.c, L2)
    "_head", "_end",       # linked image bounds (shim.ld)
}

_USB_BANNED_INSN = re.compile(
    r"^(?:smc|hvc|msr|dc|ic|at|tlbi|"
    r"(?:st|ld)(?:add|clr|eor|set|smax|smin|umax|umin)[al]?|"
    r"swp[al]?|cas[a-z]*)$"
)

# Mnemonics that write their first operand (a plain value, never a
# stack-derived pointer): the destination register loses any class.
# Includes the bitfield-insert aliases (ubfiz/sbfiz/bfi/bfxil) of the
# ubfm/sbfm family - same first-operand-only semantics.
_USB_WRITES_FIRST = {
    "and", "ands", "bic", "bics", "eor", "eon", "orr", "orn", "mvn",
    "lsl", "lsr", "asr", "ror", "ubfx", "sbfx", "ubfm", "sbfm",
    "ubfiz", "sbfiz", "bfi", "bfxil", "bfm",
    "sxtw", "sxtb", "sxth", "uxtw", "uxtb", "uxth", "mul", "mneg",
    "madd", "msub", "umull", "umaddl", "umulh", "smull", "sdiv",
    "udiv", "neg", "rev", "rev16", "rev32", "rbit", "clz", "cls",
    "extr", "csel", "csinc", "csinv", "csneg", "cset", "csetm", "crc32b", "crc32x",
    "cinc", "cinv", "cneg",
}
_USB_FLAGS_ONLY = {"cmp", "cmn", "tst", "ccmp"}
_USB_NO_WRITE = {"nop", "dsb", "dmb", "isb", "yield", "br", "blr", "ret"}

_FUNC_LABEL = re.compile(r"^([0-9a-f]+) <([^>]+)>:$")
_INSN_LINE = re.compile(r"^\s*([0-9a-f]+):\s+([0-9a-f]{4,16})\s+(.+?)\s*$")
_RELOC_LINE = re.compile(r"^\s*[0-9a-f]+:\s+R_\w+")
_MEM_BASE = re.compile(r"\[\s*(sp|x\d+|w\d+|xzr|wzr)")
_SP_SLOT_MEM = re.compile(
    r"^\s*(?P<rs>[^\[\]]+?)\s*,\s*\[\s*sp\s*,\s*#(?P<off>-?(?:0[xX][0-9a-fA-F]+|\d+))\s*\]\s*$"
)
_SP_SLOT_LD = re.compile(r"\[\s*sp\s*,\s*#(-?(?:0[xX][0-9a-fA-F]+|\d+))\s*\]")


def _usb_reg(tok: str):
    """Canonical register name for tok ('w5' -> 'x5', 'sp', 'xzr'), or
    None when tok is not a plain register."""
    tok = tok.strip()
    if tok == "sp":
        return "sp"
    if tok in ("xzr", "wzr"):
        return "xzr"
    m = re.fullmatch(r"([xw])([12]?\d|30)", tok)
    return "x" + m.group(2) if m else None


def check_usb_object_text(disasm: str, local_text: set[str], undefined: set[str],
                          sections: str, name: str = "usbprobe.o",
                          *, allowed_undef: set[str] | None = None,
                          trusted_args: frozenset[str] = frozenset(),
                          allow_adrp: bool = False,
                          require_clean_data: bool = True) -> int:
    """Audit one probe-shaped object from objdump/nm text. Raises
    AssertionError on any violation; returns the number of stores
    audited (reachable blocks). Keyword arguments generalize the audit
    for the other shim objects (see check_usbproto_object_text); the
    defaults are the original usbprobe.o policy."""
    allowed = _USB_ALLOWED_UNDEF if allowed_undef is None else allowed_undef
    extra = undefined - allowed
    if extra:
        raise AssertionError(
            f"{name}: undefined symbols beyond the sanctioned surface "
            f"({', '.join(sorted(allowed))}): {', '.join(sorted(extra))}"
        )
    if require_clean_data:
        for sec in (".data", ".bss"):
            m = re.search(rf"^\s*\d+\s+{re.escape(sec)}\s+([0-9a-f]+)\s", sections, re.M)
            if m and int(m.group(1), 16) != 0:
                raise AssertionError(
                    f"{name}: {sec} is {int(m.group(1), 16)} bytes; the probe "
                    "must carry no writable static state"
                )

    # Split the disassembly into functions of (addr, mnemonic, ops, text).
    funcs: list[tuple[str, list[tuple[int, str, str, str]]]] = []
    cur_name: str | None = None
    cur_insns: list[tuple[int, str, str, str]] = []
    for raw in disasm.splitlines():
        m = _FUNC_LABEL.match(raw.strip())
        if m:
            if cur_name is not None:
                funcs.append((cur_name, cur_insns))
            cur_name, cur_insns = m.group(2), []
            continue
        if not raw.strip() or ": R_" in raw or _RELOC_LINE.match(raw):
            continue
        m = _INSN_LINE.match(raw)
        if not m:
            continue  # 'file format' / 'Disassembly of section' headers
        addr_s, _hex, rest = m.groups()
        rest = rest.split("//")[0].strip()
        parts = rest.split(None, 1)
        cur_insns.append((int(addr_s, 16), parts[0],
                          parts[1] if len(parts) > 1 else "", rest))
    if cur_name is not None:
        funcs.append((cur_name, cur_insns))

    func_entries = {insns[0][0] for _fname, insns in funcs if insns}
    total = 0
    for fname, insns in funcs:
        total += _usb_audit_function(fname, insns, local_text, name,
                                     func_entries, trusted_args, allow_adrp)
    return total


# Registers a call may clobber (AAPCS caller-saved, plus the link
# register). Callee-saved x19-x28, x29 and sp survive 'bl'/'blr'.
_USB_CALLER_SAVED = {f"x{i}" for i in range(19)} | {"x30"}


def _usb_branch_target(ops: str, name: str, fname: str, addr: int) -> int:
    for tok in ops.replace(",", " ").split():
        if re.fullmatch(r"[0-9a-f]+", tok):
            return int(tok, 16)
    raise AssertionError(
        f"{name}: {fname}+{addr:x}: cannot parse branch target '{ops.strip()}'"
    )


def _usb_audit_function(fname: str, insns: list[tuple[int, str, str, str]],
                        local_text: set[str], name: str,
                        func_entries: set[int],
                        trusted: frozenset[str] = frozenset(),
                        allow_adrp: bool = False) -> int:
    """Prove every store in one function stack-based, or raise.

    Runs a per-basic-block dataflow with INTERSECTION at joins: a
    register counts as sp-derived at a store only when EVERY path from
    the function entry proves it so. GCC interleaves epilogues with
    hot blocks (this object has 'ret'-terminated epilogue blocks
    sitting between the stores of the PHY label loop), so a linear
    "most recent definition" scan would reject good code here - and on
    other shapes it could wrongly accept bad code."""
    if not insns:
        return 0
    is_put = fname.startswith("put_") and fname in local_text
    index = {a: i for i, (a, _mn, _ops, _rest) in enumerate(insns)}

    def fail(addr: int, msg: str):
        raise AssertionError(f"{name}: {fname}+{addr:x}: {msg}")

    # ---- basic blocks: entry, branch targets, and any instruction
    # after a branch/ret/br are leaders. An unconditional 'b' to another
    # function's ENTRY is a tail call: a terminator like 'ret' (this
    # object tail-calls put_dec from put_bit); anything else that leaves
    # the function is an error - fail closed.
    leaders = {0}
    for i, (a, mn, ops, rest) in enumerate(insns):
        if mn == "b" or mn.startswith("b.") or mn in ("cbz", "cbnz", "tbz", "tbnz"):
            tgt = _usb_branch_target(ops, name, fname, a)
            if tgt not in index:
                if mn == "b" and tgt in func_entries:
                    leaders.add(i + 1)
                    continue
                fail(a, f"branch '{rest}' leaves the function (fail closed)")
            leaders.add(index[tgt])
            leaders.add(i + 1)
        elif mn in ("ret", "br"):
            leaders.add(i + 1)
    blocks = []
    start = 0
    for i in range(1, len(insns)):
        if i in leaders:
            blocks.append((start, i))
            start = i
    blocks.append((start, len(insns)))
    block_of = {}
    for bi, (s, e) in enumerate(blocks):
        for i in range(s, e):
            block_of[i] = bi

    def block_succs(bi: int) -> list[int]:
        _s, e = blocks[bi]
        a, mn, ops, _rest = insns[e - 1]
        if mn in ("ret", "br"):
            return []
        if mn == "b" or mn.startswith("b.") or mn in ("cbz", "cbnz", "tbz", "tbnz"):
            tgt = _usb_branch_target(ops, name, fname, a)
            if tgt not in index:
                if mn == "b" and tgt in func_entries:
                    return []  # tail call: control does not come back
                fail(a, f"branch leaves the function (fail closed)")
            out = [block_of[index[tgt]]]
            if mn != "b" and e < len(insns):
                out.append(block_of[e])
            return out
        return [block_of[e]] if e < len(insns) else []

    nblocks = len(blocks)
    succs = [block_succs(b) for b in range(nblocks)]
    preds: list[list[int]] = [[] for _ in range(nblocks)]
    for b, ss in enumerate(succs):
        for t in ss:
            preds[t].append(b)

    init = (frozenset(), frozenset({"x0"}) if is_put else frozenset(trusted),
            frozenset(), {})

    def meet(states):
        it = iter(states)
        first = next(it)
        sp, x0, img, slots = (set(first[0]), set(first[1]), set(first[2]),
                              dict(first[3]))
        for st in it:
            sp &= st[0]
            x0 &= st[1]
            img &= st[2]
            slots = {k: v for k, v in slots.items() if st[3].get(k) == v}
        return (frozenset(sp), frozenset(x0), frozenset(img), slots)

    def transfer(bi: int, state, check: bool):
        """Apply one block's instruction semantics; with check=True,
        every store in the block must be provably stack-based."""
        sp, x0, img, slots = (set(state[0]), set(state[1]), set(state[2]),
                              dict(state[3]))
        n_stores = 0

        def kill(r):
            sp.discard(r)
            x0.discard(r)
            img.discard(r)

        def cls(r):
            if r is None:
                return None
            if r == "sp" or r in sp:
                return "sp"
            if (allow_adrp and r in trusted) or r in x0:
                return "x0"
            if r in img:
                return "img"
            return None

        s, e = blocks[bi]
        for i in range(s, e):
            a, mn, ops, rest = insns[i]
            if _USB_BANNED_INSN.match(mn):
                fail(a, f"banned instruction '{rest}' "
                        "(system write / cache maintenance / atomic RMW)")
            if mn.startswith("st"):  # every store family (str/stp/stur/st1/...)
                n_stores += 1
                bm = _MEM_BASE.search(ops)
                base = _usb_reg(bm.group(1)) if bm else None
                if base is None:
                    fail(a, f"cannot parse store '{rest}'")
                if check and trusted and not allow_adrp:
                    m_off = re.search(r"\[\s*(x\d+)\s*,\s*(x\d+)", ops)
                    if m_off:
                        r1 = _usb_reg(m_off.group(1))
                        r2 = _usb_reg(m_off.group(2))
                        if not (r1 == "sp" or cls(r1) == "sp"):
                            if not ((r1 == "sp" or cls(r1)) and (r2 == "sp" or cls(r2))):
                                fail(a, f"store '{rest}' register-offset [{r1}, {r2}] not provably safe")
                if check and not (base == "sp" or cls(base)):
                    fail(a, f"store '{rest}' base {base} is not provably "
                            "stack-based")
                if base == "sp":  # plain spill stores feed the slot map
                    sm = _SP_SLOT_MEM.match(ops)
                    if sm:
                        srcs = [t.strip() for t in sm.group("rs").split(",")]
                        off = int(sm.group("off"), 0)
                        width = 4 if re.fullmatch(r"w(?:[12]?\d|30)|wzr",
                                                  srcs[0]) else 8
                        for j, srctok in enumerate(srcs):
                            slots[off + j * width] = cls(_usb_reg(srctok))
            elif (mn == "ldr" or mn.startswith(("ldr", "ldur", "ldtr"))
                    or mn in ("ldar", "ldaxr", "ldxr", "ldxp", "ldaxp", "ldp")):
                n_dest = 2 if mn in ("ldp", "ldxp", "ldaxp") else 1
                toks = [t.strip() for t in ops.split(",")[:n_dest]]
                dests = [_usb_reg(t) for t in toks]
                if any(d is None for d in dests):
                    fail(a, f"cannot parse load '{rest}'")
                sm = _SP_SLOT_LD.search(ops)
                for j, (tok, d) in enumerate(zip(toks, dests)):
                    c = None
                    if not tok.startswith("w") and sm:
                        c = slots.get(int(sm.group(1), 0) + j * 8)
                    if c == "sp":
                        sp.add(d)
                    elif c == "x0":
                        x0.add(d)
                    elif c == "img":
                        img.add(d)
                    else:
                        kill(d)
            elif mn in ("add", "sub", "adds", "subs"):
                toks = [t.strip() for t in ops.split(",")]
                dest = _usb_reg(toks[0])
                if dest is None:
                    fail(a, f"cannot parse '{rest}'")
                if toks[0].startswith("w"):
                    kill(dest)  # 32-bit result: zero-extended, not a pointer
                elif dest != "sp":  # sp stays sp (frame adjustment)
                    src1 = _usb_reg(toks[1]) if len(toks) > 1 else None
                    c = cls(src1)
                    if c == "sp":
                        sp.add(dest)
                    elif c == "x0":
                        is_imm = len(toks) > 2 and toks[2].startswith("#")
                        if is_imm or allow_adrp or is_put:
                            x0.add(dest)
                        else:
                            kill(dest)
                    elif c == "img":
                        img.add(dest)
                    else:
                        kill(dest)
            elif mn == "mov":
                toks = [t.strip() for t in ops.split(",")]
                dest = _usb_reg(toks[0])
                if dest is None:
                    fail(a, f"cannot parse '{rest}'")
                src = _usb_reg(toks[1]) if len(toks) > 1 else None
                if dest == "sp":
                    if cls(src) != "sp":
                        fail(a, f"stack pivot '{rest}'")
                else:
                    c = cls(src) if not toks[0].startswith("w") else None
                    if c == "sp":
                        sp.add(dest)
                    elif c == "x0":
                        x0.add(dest)
                    elif c == "img":
                        img.add(dest)
                    else:
                        kill(dest)
            elif mn in ("movz", "movn", "movk", "adr", "adrp"):
                d = _usb_reg(ops.split(",")[0])
                if d is None:
                    fail(a, f"cannot parse '{rest}'")
                if allow_adrp and mn in ("adrp", "adr"):
                    img.add(d)  # PC-relative, resolved inside the linked image
                else:
                    kill(d)
            elif mn in ("bl", "blr"):
                for r in _USB_CALLER_SAVED:
                    kill(r)
            elif (mn == "b" or mn.startswith("b.")
                    or mn in ("cbz", "cbnz", "tbz", "tbnz")
                    or mn in _USB_NO_WRITE or mn in _USB_FLAGS_ONLY):
                pass
            elif mn in _USB_WRITES_FIRST:
                d = _usb_reg(ops.split(",")[0])
                if d is None:
                    fail(a, f"cannot parse '{rest}'")
                kill(d)
            else:
                fail(a, f"cannot classify instruction '{rest}' (fail closed)")
        return (frozenset(sp), frozenset(x0), frozenset(img), slots), n_stores

    # ---- fixpoint: out-states only shrink, so this terminates ----
    outs: list[tuple[frozenset[str], frozenset[str], frozenset[str], dict[int, str | None]] | None] = [None] * nblocks
    changed = True
    while changed:
        changed = False
        for b in range(nblocks):
            states = ([init] if b == 0 else []) + \
                     [outs[p] for p in preds[b] if outs[p] is not None]
            if not states:
                continue  # not (yet) reachable
            new_out, _n = transfer(b, meet(states), check=False)
            if new_out != outs[b]:
                outs[b] = new_out
                changed = True

    # ---- verification pass over the final in-states ----
    total = 0
    for b in range(nblocks):
        if outs[b] is None:
            for i in range(*blocks[b]):
                if insns[i][1].startswith("st"):
                    fail(insns[i][0], f"store '{insns[i][3]}' in an "
                            "unreachable block (fail closed)")
            continue
        states = ([init] if b == 0 else []) + \
                 [outs[p] for p in preds[b] if outs[p] is not None]
        _out, n = transfer(b, meet(states), check=True)
        total += n
    return total

def _cross_tool(kind: str) -> str:
    """Resolve objdump/nm the way shim/build.sh does: CROSS_* env, PATH,
    then the arm-gnu toolchain in $HOME/.local/opt."""
    env = os.environ.get(f"CROSS_{kind.upper()}")
    if env:
        return env
    home = pathlib.Path.home() / ".local/opt/arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-elf/bin"
    for prefix in ("aarch64-none-elf-", "aarch64-linux-gnu-"):
        name = prefix + kind
        if shutil.which(name):
            return name
        if (home / name).is_file():
            return str(home / name)
    raise AssertionError(
        f"cannot find {kind} for the usbprobe object audit (set CROSS_{kind.upper()})"
    )


def check_usb_object(obj_path: pathlib.Path) -> int:
    """Run the object audit on a built usbprobe.o; returns the number of
    stores audited."""
    assert obj_path.is_file(), f"{obj_path} not found (run shim/build.sh first)"
    objdump, nm = _cross_tool("objdump"), _cross_tool("nm")

    def run(*cmd):
        p = subprocess.run(cmd, capture_output=True, text=True)
        assert p.returncode == 0, f"{cmd[0]} failed: {p.stderr.strip()}"
        return p.stdout

    undefined = set()
    for line in run(nm, "-u", str(obj_path)).splitlines():
        toks = line.split()
        if len(toks) == 2 and toks[0] == "U":
            undefined.add(toks[1])
    local_text = set()
    for line in run(nm, str(obj_path)).splitlines():
        toks = line.split()
        if len(toks) == 3 and toks[1] == "t":
            local_text.add(toks[2])
    return check_usb_object_text(
        run(objdump, "-dr", str(obj_path)),
        local_text,
        undefined,
        run(objdump, "-h", str(obj_path)),
        str(obj_path),
    )


# Synthetic object fixtures for the self-test: encodings are ignored by
# the audit, only the mnemonic/operand text matters.
_SYN_H = """out.o:     file format elf64-littleaarch64

Sections:
Idx Name          Size      VMA               LMA               File off  Algn
  0 .text         000000f0  0000000000000000  0000000000000000  00000040  2**5
  1 .data         00000000  0000000000000000  0000000000000000  00000130  2**0
  2 .bss          00000000  0000000000000000  0000000000000000  00000130  2**0
"""
_SYN_NM = (
    "0000000000000000 t usbprobe_report\n"
    "0000000000000040 t put_hex\n"
)
_SYN_NM_GLOBAL_PUT = (
    "0000000000000000 t usbprobe_report\n"
    "0000000000000040 T put_hex\n"   # not static: x0 dst rule must NOT apply
)
_SYN_NM_U = (
    "                 U try_read32\n"
    "                 U con_str\n"
    "                 U fdt_collect_memmap\n"
    "                 U _head\n"
    "                 U _end\n"
)
_SYN_CONTROL = """0000000000000000 <usbprobe_report>:
   0:	d10043ff 	sub	sp, sp, #0x10
   4:	910003fd 	mov	x29, sp
   8:	910083f3 	add	x19, sp, #0x20
   c:	b9000660 	str	w0, [x19, 4]
  10:	f90003f3 	str	x19, [sp, #8]
  14:	f94003e2 	ldr	x2, [sp, #8]
  18:	b9000042 	str	w2, [x2]
  1c:	aa0603f3 	mov	x3, x19
  20:	b9000063 	str	w3, [x3, 8]
  24:	7100041f 	cmp	w0, #0x1
  28:	54000061 	b.ne	2c <usbprobe_report+0x2c>
  2c:	d503201f 	nop
0000000000000040 <put_hex>:
  40:	aa0003e5 	mov	x5, x0
  44:	39000185 	strb	w1, [x5]
  48:	39000001 	strb	w2, [x0]
  4c:	d65f03c0 	ret
"""
_SYN_TAIL = "  2c:\td503201f \tnop\n"  # splice point for injected bad code

# CFG fixtures: a join where x19 is sp-derived on one path and reloaded
# from a foreign pointer on the other, and a store in a dead block.
_SYN_JOIN_BAD = """0000000000000000 <usbprobe_report>:
   0:	d10043ff 	sub	sp, sp, #0x10
   4:	910083f3 	add	x19, sp, #0x20
   8:	34000010 	cbz	w0, 14 <usbprobe_report+0x14>
   c:	f9400027 	ldr	x19, [x1]
  10:	14000002 	b	18 <usbprobe_report+0x18>
  14:	d503201f 	nop
  18:	b9000660 	str	w0, [x19]
"""
_SYN_JOIN_OK = _SYN_JOIN_BAD.replace("ldr\tx19, [x1]", "add\tx19, sp, #0x8")
_SYN_UNREACHABLE = """0000000000000000 <usbprobe_report>:
   0:	d65f03c0 	ret
   4:	b9000020 	str	w0, [x1]
"""


def usb_object_selftest() -> int:
    """Feed the object audit synthetic bad objects and assert each is
    rejected; the good control must pass (and its stores must actually
    be counted, so a parser that sees nothing cannot pass silently)."""

    def parse_nm(nm_text):
        local = {t[2] for t in (l.split() for l in nm_text.splitlines())
                 if len(t) == 3 and t[1] == "t"}
        undef = {t[1] for t in (l.split() for l in _SYN_NM_U.splitlines())
                 if len(t) == 2 and t[0] == "U"}
        return local, undef

    def run_case(disasm, nm=_SYN_NM, nm_u=_SYN_NM_U, h=_SYN_H):
        local, _ = parse_nm(nm)
        undef = {t[1] for t in (l.split() for l in nm_u.splitlines())
                 if len(t) == 2 and t[0] == "U"}
        return check_usb_object_text(disasm, local, undef, h, "selftest object")

    n = run_case(_SYN_CONTROL)
    assert n == 6, f"selftest control: expected 6 audited stores, saw {n}"
    n = run_case(_SYN_JOIN_OK)
    assert n == 1, f"selftest join control: expected 1 audited store, saw {n}"

    def bad(tail):
        assert _SYN_TAIL in _SYN_CONTROL
        return _SYN_CONTROL.replace(_SYN_TAIL, tail)

    cases = [
        ("store to a non-stack base",
         bad("  2c:\tb9000020 \tstr\tw0, [x1]\n")),
        ("smc", bad("  2c:\td4000003 \tsmc\t#0\n")),
        ("hvc", bad("  2c:\td4000002 \thvc\t#0\n")),
        ("msr system register", bad("  2c:\td5184100 \tmsr\tsctlr_el1, x0\n")),
        ("dc cache maintenance", bad("  2c:\td50b7e20 \tdc\tzva, x19\n")),
        ("LSE atomic stadd", bad("  2c:\tb8200260 \tstadd\tw0, [x19]\n")),
        ("LSE atomic swp", bad("  2c:\tb8201260 \tswp\tw0, w1, [x19]\n")),
        ("LSE atomic cas", bad("  2c:\t88a00260 \tcas\tw0, w1, [x19]\n")),
        ("x0 store outside a put_ helper",
         bad("  2c:\tb9000000 \tstr\tw9, [x0]\n")),
        ("store through a rodata pointer",
         bad("  2c:\t90000009 \tadrp\tx9, 0 <usbprobe_report>\n"
             "  30:\t91002129 \tadd\tx9, x9, #0x8\n"
             "  34:\tb9000129 \tstr\tw0, [x9]\n")),
        ("store through a spilled unknown pointer",
         bad("  2c:\tf90007e1 \tstr\tx1, [sp, #16]\n"
             "  30:\tf94007e9 \tldr\tx9, [sp, #16]\n"
             "  34:\tb9000129 \tstr\tw0, [x9]\n")),
        ("movk breaks an sp-derived chain",
         bad("  2c:\t91002129 \tadd\tx9, sp, #0x8\n"
             "  30:\td2acce29 \tmovk\tx9, #0x667, lsl #16\n"
             "  34:\tb9000129 \tstr\tw0, [x9]\n")),
        ("stack pivot via mov sp",
         bad("  2c:\taa0103f0 \tmov\tsp, x1\n")),
        ("store through a register killed on one incoming path", _SYN_JOIN_BAD),
        ("store in an unreachable block", _SYN_UNREACHABLE),
        ("unclassifiable instruction (.inst)",
         bad("  2c:\te7ffdefe \t.inst\t0xe7ffdefe ; undefined\n")),
    ]
    caught = 0
    for label, disasm in cases:
        try:
            run_case(disasm)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: object {label} -> {exc}")
        else:
            raise AssertionError(f"usb object self-test FAILED to catch: {label}")
    for label, kwargs in (
        ("undefined symbol beyond the surface",
         {"nm_u": _SYN_NM_U + "                 U memcpy\n"}),
        ("non-empty .data", {"h": _SYN_H.replace("  1 .data         00000000",
                                                  "  1 .data         00000010")}),
        ("non-empty .bss", {"h": _SYN_H.replace("  2 .bss          00000000",
                                                 "  2 .bss          00000020")}),
        ("put_ helper that is not static", {"nm": _SYN_NM_GLOBAL_PUT}),
    ):
        try:
            run_case(_SYN_CONTROL, **kwargs)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: object {label} -> {exc}")
        else:
            raise AssertionError(f"usb object self-test FAILED to catch: {label}")
    print(f"PASS usb object self-test: {caught}/{len(cases) + 4} bad objects rejected")
    return caught


# ---- usbproto.o: pure-protocol object audit (stage-2 gate honesty) ----
#
# usbproto.c must stay 100% free of MMIO: it has NO undefined symbols at
# all (it cannot call try_read32/try_write32 or anything else), and every
# store must be provably to the stack, to a caller-provided pointer, or
# through a PC-relative adrp/adr-derived pointer into the object's own
# image (in_byte, the one-byte static backing one-byte IN stages; adrp
# resolves inside the linked image and can never reach a device window).
# "Caller-provided pointer" means the AAPCS argument registers x0-x7,
# seeded at every function entry and invalidated by calls - the task's
# 'x0-x3' family widened because the 5-parameter get_descriptor helper
# receives its out-length pointer in x4.

_USBPROTO_TRUSTED_ARGS = frozenset(f"x{i}" for i in range(8))


def check_usbproto_object_text(disasm: str, local_text: set[str],
                               undefined: set[str], sections: str,
                               name: str = "usbproto.o") -> int:
    return check_usb_object_text(
        disasm, local_text, undefined, sections, name,
        allowed_undef=set(), trusted_args=_USBPROTO_TRUSTED_ARGS,
        allow_adrp=True, require_clean_data=False,
    )


def check_usbproto_object(obj_path: pathlib.Path) -> int:
    """Run the no-MMIO audit on a built usbproto.o."""
    assert obj_path.is_file(), f"{obj_path} not found (run shim/build.sh first)"
    objdump, nm = _cross_tool("objdump"), _cross_tool("nm")

    def run(*cmd):
        p = subprocess.run(cmd, capture_output=True, text=True)
        assert p.returncode == 0, f"{cmd[0]} failed: {p.stderr.strip()}"
        return p.stdout

    undefined = set()
    for line in run(nm, "-u", str(obj_path)).splitlines():
        toks = line.split()
        if len(toks) == 2 and toks[0] == "U":
            undefined.add(toks[1])
    local_text = set()
    for line in run(nm, str(obj_path)).splitlines():
        toks = line.split()
        if len(toks) == 3 and toks[1] == "t":
            local_text.add(toks[2])
    return check_usbproto_object_text(
        run(objdump, "-dr", str(obj_path)),
        local_text,
        undefined,
        run(objdump, "-h", str(obj_path)),
        str(obj_path),
    )


def usbproto_object_selftest() -> int:
    """Synthetic objects for the usbproto no-MMIO audit; the control
    carries one store of every sanctioned class (stack, arg register,
    adrp-derived image pointer)."""

    def run_case(disasm, nm_u=""):
        local = {t[2] for t in (l.split() for l in _UP_NM.splitlines())
                 if len(t) == 3 and t[1] == "t"}
        undef = {t[1] for t in (l.split() for l in nm_u.splitlines())
                 if len(t) == 2 and t[0] == "U"}
        return check_usbproto_object_text(disasm, local, undef, _SYN_H,
                                          "usbproto selftest")

    n = run_case(_UP_CONTROL)
    assert n == 4, f"usbproto selftest control: expected 4 audited stores, saw {n}"

    cases = [
        ("undefined symbol (any)", run_case,
         _UP_CONTROL, "                 U memcpy\n"),
        ("store through a movz/movk absolute pointer", run_case,
         _UP_CONTROL.replace(
             "   c:\t90000009 \tadrp\tx9, 0 <in_byte>\n"
             "  14:\t91000d29 \tadd\tx9, x9, #0x3\n",
             "   c:\td2a22229 \tmovz\tx9, #0x1111, lsl #16\n"
             "  14:\t72c20009 \tmovk\tx9, #0x0\n"), ""),
        ("smc", run_case,
         _UP_CONTROL.replace("  24:\td65f03c0 \tret\n",
                             "  24:\td4000003 \tsmc\t#0\n"), ""),
        ("store through an unclassified register", run_case,
         _UP_CONTROL.replace("strb\tw9, [x9]", "strb\tw9, [x20]"), ""),
    ]
    caught = 0
    for label, fn, *args in cases:
        try:
            fn(*args)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: usbproto {label} -> {exc}")
        else:
            raise AssertionError(f"usbproto object self-test FAILED to catch: {label}")
    print(f"PASS usbproto object self-test: {caught}/{len(cases)} bad objects rejected")
    return caught


_UP_NM = (
    "0000000000000000 t usbproto_setup\n"
    "0000000000000040 t usbproto_init\n"
)
_UP_CONTROL = """0000000000000000 <usbproto_setup>:
   0:	d10043ff 	sub	sp, sp, #0x10
   4:	39000001 	strb	w1, [x0]
   8:	b9000062 	str	w2, [x3]
   c:	90000009 	adrp	x9, 0 <in_byte>
  14:	91000d29 	add	x9, x9, #0x3
  18:	39000129 	strb	w9, [x9]
  1c:	52800029 	mov	w9, #0x1
  20:	b9000be9 	str	w9, [sp, #8]
  24:	d65f03c0 	ret
"""


# ---- usbgadget.o: WRITER audit (stage-2 gate honesty) ----
#
# usbgadget.c writes registers by design, so the read-only proof does
# not apply. Instead: (a) undefined symbols stay inside the sanctioned
# set; (b) EVERY reference to try_write32 (any relocation kind) must be
# a R_AARCH64_CALL26 bl from inside the single symbol w32 - the source
# keeps one noinline write helper, so the address of try_write32 never
# escapes it; (c) no smc/hvc/msr/dc/ic/at/tlbi and no LSE atomics (a
# writer must not sneak a system-register write or an RMW past the
# w32 whitelist).

_USBG_ALLOWED_UNDEF = {
    "try_read32",       # fault-guarded MMIO read (shim.c)
    "try_write32",      # ONLY from w32, enforced above
    "con_str",          # console/ramlog line out (console.c)
    "usbproto_init",    # pure protocol state reset (usbproto.c)
    "usbproto_setup",   # pure ep0 decision (usbproto.c)
}


_TOUCH_ALLOWED_UNDEF = {
    "try_read32",       # fault-guarded MMIO read (shim.c)
    "try_write32",      # ONLY from w32, enforced by the same writer audit
    "con_str",          # console/ramlog line out (console.c)
    "counter_freq",     # timer frequency (console.c)
    "wait_ticks",       # bounded delay (console.c)
    "touch_ticks",      # CNTPCT (shim.c)
    # --touch-probe=railsr|rails prints the ACPM client's results (acpm.o has its own writer audit below; these never write here)
    "acpm_cfg_init", "acpm_describe", "acpm_chan_id", "acpm_qword", "acpm_pmic_read", "acpm_pmic_update", "acpm_unsure", "acpm_why",
    # --touch-probe=dvfsr|dvfs: the ACPM DVFS client (CPU cluster rates, docs/research/25); same rule, acpm.o's writes are audited there
    "acpm_dvfs_ndom", "acpm_dvfs_dom", "acpm_dvfs_get", "acpm_dvfs_set",
}


_ACPM_ALLOWED_UNDEF = {
    "try_read32",       # fault-guarded MMIO / SRAM read (shim.c)
    "try_write32",      # ONLY from w32, enforced by the same writer audit
    "touch_ticks",      # CNTPCT (shim.c)
    "counter_freq",     # timer frequency (console.c)
}


def check_usbgadget_object_text(disasm: str, undefined: set[str],
                                name: str = "usbgadget.o",
                                allowed_undef: set[str] = _USBG_ALLOWED_UNDEF) -> None:
    extra = undefined - allowed_undef
    if extra:
        raise AssertionError(
            f"{name}: undefined symbols beyond the sanctioned surface "
            f"({', '.join(sorted(allowed_undef))}): "
            f"{', '.join(sorted(extra))}"
        )
    cur = None
    for raw in disasm.splitlines():
        line = raw.strip()
        m = _FUNC_LABEL.match(line)
        if m:
            cur = m.group(2)
            continue
        if ": R_" in line:
            m = re.match(r"^([0-9a-f]+):\s+(R_\w+)\s+(\S+)", line)
            if m and m.group(3) == "try_write32":
                if m.group(2) != "R_AARCH64_CALL26" or cur != "w32":
                    raise AssertionError(
                        f"{name}: reference to try_write32 at {m.group(1)} "
                        f"({m.group(2)}) inside '{cur}'; the only sanctioned "
                        "use is a bl from the w32 helper"
                    )
            continue
        m = _INSN_LINE.match(raw)
        if not m:
            continue
        rest = m.group(3).split("//")[0].strip()
        mn = rest.split(None, 1)[0]
        if _USB_BANNED_INSN.match(mn):
            raise AssertionError(
                f"{name}: banned instruction '{rest}' inside '{cur}' "
                "(system write / cache maintenance / atomic RMW)"
            )


def check_usbgadget_object(obj_path: pathlib.Path,
                           allowed_undef: set[str] = _USBG_ALLOWED_UNDEF) -> None:
    """Run the WRITER audit on a built usbgadget.o (or touchprobe.o with its own undef set)."""
    assert obj_path.is_file(), f"{obj_path} not found (run shim/build.sh first)"
    objdump, nm = _cross_tool("objdump"), _cross_tool("nm")

    def run(*cmd):
        p = subprocess.run(cmd, capture_output=True, text=True)
        assert p.returncode == 0, f"{cmd[0]} failed: {p.stderr.strip()}"
        return p.stdout

    undefined = set()
    for line in run(nm, "-u", str(obj_path)).splitlines():
        toks = line.split()
        if len(toks) == 2 and toks[0] == "U":
            undefined.add(toks[1])
    check_usbgadget_object_text(run(objdump, "-dr", str(obj_path)),
                                undefined, str(obj_path), allowed_undef)


def usbgadget_object_selftest() -> int:
    """Synthetic objects for the usbgadget WRITER audit; the control is
    a w32 with one bl try_write32 plus another function that never
    references it."""

    def run_case(disasm, nm_u=_UG_NM_U):
        undef = {t[1] for t in (l.split() for l in nm_u.splitlines())
                 if len(t) == 2 and t[0] == "U"}
        check_usbgadget_object_text(disasm, undef, "usbgadget selftest")

    run_case(_UG_CONTROL)  # must pass

    cases = [
        ("bl try_write32 outside w32",
         _UG_CONTROL.replace(
             "  14:\t94000000 \tbl\t0 <con_str>\n"
             "\t\t\t14: R_AARCH64_CALL26\tcon_str\n",
             "  14:\t94000000 \tbl\t0 <try_write32>\n"
             "\t\t\t14: R_AARCH64_CALL26\ttry_write32\n")),
        ("address of try_write32 taken (adrp)",
         _UG_CONTROL.replace(
             "  14:\t94000000 \tbl\t0 <con_str>\n"
             "\t\t\t14: R_AARCH64_CALL26\tcon_str\n",
             "  14:\t90000009 \tadrp\tx9, 0 <try_write32>\n"
             "\t\t\t14: R_AARCH64_ADR_PREL_PG_HI21\ttry_write32\n")),
        ("msr system register",
         _UG_CONTROL + "  18:\td5184100 \tmsr\tsctlr_el1, x0\n"),
        ("LSE atomic stadd",
         _UG_CONTROL + "  18:\tb8200260 \tstadd\tw0, [x19]\n"),
    ]
    caught = 0
    for label, disasm in cases:
        try:
            run_case(disasm)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: usbgadget writer {label} -> {exc}")
        else:
            raise AssertionError(f"usbgadget writer self-test FAILED to catch: {label}")
    for label, nm_u in (
        ("undefined symbol wait_ticks (time comes via the hooks struct)",
         _UG_NM_U + "                 U wait_ticks\n"),
        ("undefined symbol memcpy",
         _UG_NM_U + "                 U memcpy\n"),
    ):
        try:
            run_case(_UG_CONTROL, nm_u)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: usbgadget writer {label} -> {exc}")
        else:
            raise AssertionError(f"usbgadget writer self-test FAILED to catch: {label}")
    print(f"PASS usbgadget writer self-test: {caught}/{len(cases) + 2} bad objects rejected")
    return caught


_UG_NM_U = "                 U try_read32\n                 U try_write32\n                 U con_str\n"
_UG_CONTROL = """0000000000000000 <w32>:
   0:	d10043ff 	sub	sp, sp, #0x10
   4:	94000000 	bl	0 <try_write32>
\t\t\t4: R_AARCH64_CALL26\ttry_write32
   8:	d65f03c0 	ret
0000000000000010 <usbgadget_poll>:
  14:	94000000 	bl	0 <con_str>
\t\t\t14: R_AARCH64_CALL26\tcon_str
  18:	d65f03c0 	ret
"""



# ---- usbgadget.c source audit (review H3): cannot be bypassed by asm / raw pointers ----
# The object audit proves where try_write32 is *named*; it cannot see stores
# done by inline asm, a hard-coded register pointer, or a second write path.
# These source rules close those holes: no asm; try_write32 / the DWC3 base
# only inside the two single-purpose helpers w32 / r32; no integer-literal
# pointer casts anywhere (raw MMIO); w32 must call its allowlist function.

def _strip_c_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def _c_function_span(code: str, name: str) -> tuple[int, int]:
    m = re.search(r"\b(?:static\s+)?(?:const\s+)?(?:int|void|uint32_t|unsigned|char|uint64_t|uintptr_t)(?:\s+|\s*\*+\s*)" + name + r"\s*\([^)]*\)\s*\{", code)
    assert m, f"usbgadget source audit: function {name} not found"
    depth, i = 1, m.end()
    while depth and i < len(code):
        depth += {"{": 1, "}": -1}.get(code[i], 0)
        i += 1
    assert depth == 0, f"usbgadget source audit: unbalanced braces in {name}"
    return m.start(), i


def check_usbgadget_source_text(src: str, name: str = "usbgadget.c",
                                base_macro: str | None = "USBG_DWC3_BASE",
                                strict: bool = False,
                                guard_funcs: tuple[str, ...] = ("w32_allowed",)) -> None:
    """strict (touchprobe.c): no volatile outside r32 and no explicit pointer casts at all, so no second writer can
    be built from a pointer; guard_funcs must really depend on the address and not be a constant."""
    code = _strip_c_comments(src)
    if re.search(r"\b(__asm__|__asm|asm)\b", code):
        raise AssertionError(f"{name}: inline asm is banned in the hardware writer")
    spans = [_c_function_span(code, f) for f in ("w32", "r32")]

    def inside(pos: int) -> bool:
        return any(a <= pos < b for a, b in spans)

    w32_a, w32_b = spans[0]
    for m in re.finditer(r"\btry_write32\b", code):
        if not (w32_a <= m.start() < w32_b):
            raise AssertionError(f"{name}: try_write32 used outside w32")
    for m in (re.finditer(r"\b" + base_macro + r"\b", code) if base_macro else ()):
        line_start = code.rfind("\n", 0, m.start()) + 1
        if code[line_start:m.start()].lstrip().startswith("#define"):
            continue
        if not inside(m.start()):
            raise AssertionError(f"{name}: DWC3 base used outside w32/r32")
    if re.search(r"\(\s*(?:volatile\s+)?[A-Za-z_][\w\s]*\*\s*\)\s*(?:0x[0-9a-fA-F]+|\d+)\b", code):
        raise AssertionError(f"{name}: integer-literal pointer cast (raw MMIO) is banned")
    body = code[w32_a:w32_b]
    guard = re.search(r"if\s*\(\s*!\s*w32_allowed\s*\([^;{}]*\)\s*\)\s*return\b", body)
    if not guard:
        raise AssertionError(f"{name}: w32 lacks the 'if (!w32_allowed(...)) return' guard")
    if body.find("try_write32") < guard.start():
        raise AssertionError(f"{name}: w32 writes before its allowlist guard")
    for g in guard_funcs:
        ga, gb = _c_function_span(code, g)
        gbody = code[ga:gb]
        params = re.search(r"\(([^)]*)\)", gbody).group(1)
        first = re.findall(r"[A-Za-z_]\w*", params.split(",")[0])[-1]
        inner = gbody[gbody.index("{") + 1:-1]
        if not re.search(r"\b" + first + r"\b", inner) or re.fullmatch(r"\s*return\s+[01]\s*;\s*", inner):
            raise AssertionError(f"{name}: {g} does not depend on its address parameter '{first}' (constant allowlist)")
    if strict:
        r32_a, r32_b = spans[1]
        for m in re.finditer(r"\bvolatile\b", code):
            if not (r32_a <= m.start() < r32_b):
                raise AssertionError(f"{name}: 'volatile' outside r32 (a second MMIO writer?)")
        if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code):
            raise AssertionError(f"{name}: explicit pointer cast is banned in the hardware writer")


def usbgadget_source_selftest() -> int:
    good = open(pathlib.Path(__file__).resolve().parents[1] / "shim" / "usbgadget.c").read()
    check_usbgadget_source_text(good)  # control must pass
    cases = [
        ("inline asm store", good.replace("static int r32", 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\nstatic int r32', 1)),
        ("second write path", good.replace("static int r32", "static void evil(void) { try_write32(0x11110000UL + 0xC110u, 0); }\nstatic int r32", 1)),
        ("base used in a helper", good.replace("static int r32", "static uint32_t evil(void) { return USBG_DWC3_BASE + 8; }\nstatic int r32", 1)),
        ("literal pointer cast", good.replace("static int r32", "static void evil(void) { *(volatile uint32_t *)0x11111234 = 0; }\nstatic int r32", 1)),
        ("w32 skips allowlist", good.replace("if (!w32_allowed(off))", "if (0)", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"usbgadget source selftest case did not mutate: {label}"
        try:
            check_usbgadget_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: usbgadget source {label} -> {exc}")
        else:
            raise AssertionError(f"usbgadget source self-test FAILED to catch: {label}")
    print(f"PASS usbgadget source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


_TOUCH_KW = dict(base_macro=None, strict=True, guard_funcs=("w32_allowed", "wmask"))


def touchprobe_source_selftest() -> int:
    """Strict source audit of shim/touchprobe.c: no asm, no volatile / pointer casts, one write path (w32) that checks
    w32_allowed before writing, and an allowlist that really depends on the address."""
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "touchprobe.c").read_text()
    check_usbgadget_source_text(good, "touchprobe.c", **_TOUCH_KW)  # control must pass
    ins = "static int r32"
    cases = [
        ("inline asm store", good.replace(ins, 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\n' + ins, 1)),
        ("second write path", good.replace(ins, "static void evil(void) { try_write32(0x10d40000UL, 0); }\n" + ins, 1)),
        ("second writer via a non-literal pointer",
         good.replace(ins, "static void evil(volatile uint32_t *p) { *p = 0; }\n" + ins, 1)),
        ("second writer via a pointer cast of a variable",
         good.replace(ins, "static void evil(uintptr_t pa) { *(uint32_t *)pa = 0; }\n" + ins, 1)),
        ("literal pointer cast", good.replace(ins, "static void evil(void) { *(volatile uint32_t *)0x10d40000 = 0; }\n" + ins, 1)),
        ("w32 skips allowlist", good.replace("if (!w32_allowed(pa, old, v, opts))", "if (0)", 1)),
        ("w32 calls the allowlist and ignores it",
         good.replace("if (!w32_allowed(pa, old, v, opts))\n\t\treturn -2;", "(void)w32_allowed(pa, old, v, opts);", 1)),
        ("w32 writes before the guard",
         good.replace("\tif (!w32_allowed(pa, old, v, opts))\n\t\treturn -2;\n\treturn try_write32(pa, v) ? 0 : -1;",
                      "\t(void)try_write32(pa, v);\n\tif (!w32_allowed(pa, old, v, opts))\n\t\treturn -2;\n\treturn 0;", 1)),
        ("w32_allowed always returns 1",
         re.sub(r"(static int w32_allowed\(uintptr_t pa, uint32_t old, uint32_t v, unsigned opts\)\n\{).*?\n\}\n",
                r"\1\n\treturn 1;\n}\n", good, count=1, flags=re.S)),
        ("wmask ignores the address",
         re.sub(r"(static uint32_t wmask\(uintptr_t pa, unsigned opts\)\n\{).*?\n\}\n",
                r"\1\n\treturn 0xffffffffu;\n}\n", good, count=1, flags=re.S)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"touchprobe source selftest case did not mutate: {label}"
        try:
            check_usbgadget_source_text(bad, "selftest", **_TOUCH_KW)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: touchprobe source {label} -> {exc}")
        else:
            raise AssertionError(f"touchprobe source self-test FAILED to catch: {label}")
    print(f"PASS touchprobe source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


_ACPM_KW = dict(base_macro=None, strict=True, guard_funcs=("w32_allowed",))


def acpm_source_selftest() -> int:
    """Strict source audit of shim/acpm.c (same rules as touchprobe.c): one write path (w32) that checks w32_allowed first,
    an allowlist that depends on the address, no asm / volatile / pointer casts."""
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "acpm.c").read_text()
    check_usbgadget_source_text(good, "acpm.c", **_ACPM_KW)  # control must pass
    ins = "static int r32"
    cases = [
        ("inline asm store", good.replace(ins, 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\n' + ins, 1)),
        ("second write path", good.replace(ins, "static void evil(void) { try_write32(0x17610040UL, 4); }\n" + ins, 1)),
        ("second writer via a pointer cast of a variable",
         good.replace(ins, "static void evil(uintptr_t pa) { *(uint32_t *)pa = 0; }\n" + ins, 1)),
        ("volatile outside r32", good.replace(ins, "static void evil(volatile uint32_t *p) { *p = 0; }\n" + ins, 1)),
        ("w32 skips allowlist", good.replace("if (!w32_allowed(pa, v, g))", "if (0)", 1)),
        ("w32 writes before the guard",
         good.replace("\tif (!w32_allowed(pa, v, g))\n\t\treturn -2;\n\treturn try_write32(pa, v) ? 0 : -1;",
                      "\t(void)try_write32(pa, v);\n\tif (!w32_allowed(pa, v, g))\n\t\treturn -2;\n\treturn 0;", 1)),
        ("w32_allowed always returns 1",
         re.sub(r"(static int w32_allowed\(uintptr_t pa, uint32_t v, const struct wguard \*g\)\n\{).*?\n\}\n",
                r"\1\n\treturn 1;\n}\n", good, count=1, flags=re.S)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"acpm source selftest case did not mutate: {label}"
        try:
            check_usbgadget_source_text(bad, "selftest", **_ACPM_KW)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: acpm source {label} -> {exc}")
        else:
            raise AssertionError(f"acpm source self-test FAILED to catch: {label}")
    print(f"PASS acpm source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


# ---- gpupower.c (--gpu-power): the opt-in Mali power cycle. Generic writer audit (strict, like touchprobe.c) plus checks that
# pin WHAT may be written: the register offsets, the exact allowlist, precheck-before-first-write, bounded loops.
_GPUPOWER_KW = dict(base_macro=None, strict=True, guard_funcs=("w32_allowed",))
_GPUPOWER_ALLOWED_UNDEF = {
    "try_read32",       # fault-guarded MMIO read (shim.c)
    "try_write32",      # ONLY from w32, enforced by the same writer audit
    "con_str",          # console/ramlog line out (console.c)
    "touch_ticks",      # CNTPCT (shim.c): the 1 s bound of each wait
    "counter_freq",     # timer frequency (console.c)
}
# every G_* register define of gpupower.c (GPU_CONTROL offsets, kbase regmap_jm_enums.h; the four writes are the *_ON / *_OFF ones)
_GPUPOWER_REGS = {
    "G_ID": "0x000u", "G_IRQ_MASK": "0x028u", "G_L2_CONFIG": "0x048u", "G_COHERENCY": "0x304u",
    "G_JM_CFG": "0xf00u", "G_SH_CFG": "0xf04u", "G_TL_CFG": "0xf08u", "G_L2MMU_CFG": "0xf0cu",
    "G_STACK_PRES": "0xe00u", "G_STACK_ON": "0xe20u", "G_STACK_OFF": "0xe30u", "G_STACK_RDY": "0xe10u", "G_STACK_TR": "0xe40u", "G_RAWSTAT": "0x020u", "G_STATUS": "0x034u", "G_FAULT": "0x03cu",
    "G_SHADER_PRES": "0x100u", "G_TILER_PRES": "0x110u", "G_L2_PRES": "0x120u",
    "G_SHADER_RDY": "0x140u", "G_TILER_RDY": "0x150u", "G_L2_RDY": "0x160u",
    "G_SHADER_ON": "0x180u", "G_TILER_ON": "0x190u", "G_SHADER_OFF": "0x1c0u", "G_L2_OFF": "0x1e0u",
    "G_SHADER_TR": "0x200u", "G_TILER_TR": "0x210u", "G_L2_TR": "0x220u",
}
_GPUPOWER_ALLOW = ("if(pa==G_SHADER_ON||pa==G_SHADER_OFF)returnv==GPU_SHADER_PRESENT;"
                   "if(pa==G_TILER_ON)returnv==GPU_TILER_PRESENT;"
                   "if(pa==G_L2_OFF)returnv==GPU_L2_PRESENT;return0;")
# --gpu-power=stack (-DGPU_POWER_STACK): exactly two more entries, each only with the stack-present mask
_GPUPOWER_ALLOW_STACK = _GPUPOWER_ALLOW.replace("return0;", "if(pa==G_STACK_ON||pa==G_STACK_OFF)returnv==GPU_STACK_PRESENT;return0;")


def _gpupower_variant(src: str, stack: bool, job: bool = False) -> str:
    """The source as the compiler sees it with / without -DGPU_POWER_STACK and -DGPU_JOB (only those two #ifdefs are interpreted, no nesting)."""
    want = {"GPU_POWER_STACK": stack, "GPU_JOB": job}
    out, cur, skip = [], None, False
    for line in src.split("\n"):
        t = line.strip()
        m = re.fullmatch(r"#ifdef (GPU_POWER_STACK|GPU_JOB)", t)
        if m:
            if cur:
                raise AssertionError("gpupower.c: nested GPU_POWER_STACK / GPU_JOB block")
            cur, skip = m.group(1), not want[m.group(1)]
            continue
        if cur and t == "#endif":
            cur, skip = None, False
            continue
        if cur and re.match(r"#\s*(?:else|elif)\b", t):
            raise AssertionError("gpupower.c: #else inside a GPU_POWER_STACK / GPU_JOB block is not supported by the audit")
        if not skip:
            out.append(line)
    if cur:
        raise AssertionError(f"gpupower.c: unterminated {cur} block")
    return "\n".join(out)


def check_gpupower_source_text(src: str, name: str = "gpupower.c") -> None:
    """Both builds are audited against their own exact allowlist: the plain one must not even mention the stack registers."""
    _check_gpupower_variant(_gpupower_variant(src, False), name + " [plain]", False)
    _check_gpupower_variant(_gpupower_variant(src, True), name + " [stack]", True)
    # --gpu-job: the plain view plus exactly one hook, after the ON-phase dump and before the first power-off, only when the shaders came up
    jv = _gpupower_variant(src, False, True)
    _check_gpupower_variant(jv, name + " [job]", False)
    jn = re.sub(r"\s+", "", _strip_c_comments(jv))
    if jn.count("gpu_job_run(") != 1 or "if(up[0]=='o')(void)gpu_job_run();" not in jn:
        raise AssertionError(f"{name} [job]: exactly one hook, `if (up[0] == 'o') (void)gpu_job_run();`")
    if not 0 <= jn.find('dump_all("");') < jn.find("gpu_job_run(") < jn.find("power_off(G_SHADER_OFF"):
        raise AssertionError(f"{name} [job]: the job hook must sit after dump_all(\"\") and before the OFF sequence")
    if "gpu_job_run" in re.sub(r"\s+", "", _strip_c_comments(_gpupower_variant(src, False, False))) or "gpu_job_run" in re.sub(r"\s+", "", _strip_c_comments(_gpupower_variant(src, True, False))):
        raise AssertionError(f"{name}: the job hook must not exist in the plain / stack builds")


def _check_gpupower_variant(src: str, name: str, stack: bool) -> None:
    check_usbgadget_source_text(src, name, **_GPUPOWER_KW)
    code = _strip_c_comments(src)
    regs = dict(re.findall(r"(?m)^#define\s+(G_\w+)\s+\(GPU_MALI \+ (0x[0-9a-f]+u)\)\s*$", code))
    if regs != _GPUPOWER_REGS:
        raise AssertionError(f"{name}: register defines differ from the audited set: {sorted(set(regs.items()) ^ set(_GPUPOWER_REGS.items()))}")
    rest = re.sub(r"(?m)^#define\s+G_\w+.*$", "", code).replace("defined(GPU_MALI)", "")
    for ok in ("GPU_MALI + 0x104u + 0x10u * i", "GPU_MALI + 0x140u + 0x10u * i + 4u * h", "GPU_MALI + 0x200u + 0x10u * i + 4u * h"):
        rest = rest.replace(ok, "")	# precheck's read-only PRESENT-HI / READY / PWRTRANS loops
    if "GPU_MALI" in rest:
        raise AssertionError(f"{name}: GPU_MALI used outside the audited G_* defines")
    a, b = _c_function_span(code, "w32_allowed")
    body = re.sub(r"\s+", "", code[a:b][code[a:b].index("{") + 1:-1])
    if body != (_GPUPOWER_ALLOW_STACK if stack else _GPUPOWER_ALLOW):
        raise AssertionError(f"{name}: w32_allowed is not the audited allowlist (plain: four LO words, stack variant: six; each only with its present mask): {body}")
    # no write before the preconditions were checked: precheck() has no writer, and the first w32( in gpu_power_cycle follows the refusal
    pa_, pb_ = _c_function_span(code, "precheck")
    if re.search(r"\bw32\s*\(", code[pa_:pb_]):
        raise AssertionError(f"{name}: precheck must be read-only")
    ca, cb = _c_function_span(code, "gpu_power_cycle")
    cyc = code[ca:cb]
    refuse = re.search(r"if\s*\(\s*why\s*\)\s*\{[^{}]*return\s+1\s*;\s*\}", cyc)
    first_w = re.search(r"\bw32\s*\(", cyc)
    if "why = precheck()" not in cyc or not refuse or not first_w or first_w.start() < refuse.end():
        raise AssertionError(f"{name}: gpu_power_cycle must call precheck() and return on a refusal before its first w32()")
    # bounded waits only: the single while is put_txt's string copy; no for(;;), do/while or goto
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    pa_, pb_ = _c_function_span(code, "put_txt")
    for m in re.finditer(r"\bwhile\b", code):
        if not (pa_ <= m.start() < pb_):
            raise AssertionError(f"{name}: 'while' outside put_txt (waits must be the GP_POLLS-bounded wait_for)")
    wa, wb = _c_function_span(code, "wait_for")
    if not re.search(r"for\s*\(\s*unsigned\s+i\s*=\s*0\s*;\s*i\s*<\s*GP_POLLS\s*;\s*i\+\+\s*\)", code[wa:wb]):
        raise AssertionError(f"{name}: wait_for is not bounded by GP_POLLS")
    m = re.search(r"#define\s+GP_POLLS\s+(\d+)u\b", code)
    if not m:
        raise AssertionError(f"{name}: GP_POLLS is not a plain constant")
    if int(m.group(1)) > 200000:
        raise AssertionError(f"{name}: GP_POLLS {m.group(1)} exceeds the audited cap of 200000")
    # both waits: polls AND time bound, and nothing may touch the loop variable
    hdr = r"for\s*\(\s*unsigned\s+i\s*=\s*0\s*;\s*i\s*<\s*GP_POLLS\s*;\s*i\+\+\s*\)"
    for fn in ("wait_for", "wait_idle"):
        wa, wb = _c_function_span(code, fn)
        body = code[wa:wb]
        if len(re.findall(hdr, body)) != 1 or "touch_ticks() - t0 > lim" not in body or "lim = counter_freq() * GP_WAIT_S" not in body:
            raise AssertionError(f"{name}: {fn} must be one GP_POLLS-bounded loop with the CNTPCT time bound")
        if re.search(r"\bi\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+\s*i\b|\bi\s*--|--\s*i\b|\bi\s*\+\+", re.sub(hdr, "", body)):
            raise AssertionError(f"{name}: {fn} modifies its loop variable")
    if not re.search(r"#define\s+GP_WAIT_S\s+[1-9]u\b", code):
        raise AssertionError(f"{name}: GP_WAIT_S must be a small plain constant")
    # no recursion, direct or through another function
    names = re.findall(r"(?m)^(?:__attribute__\(\(noinline\)\)\s+)?(?:static\s+)?(?:const\s+)?(?:int|void|uint32_t|unsigned|char)(?:\s+|\s*\*+\s*)(\w+)\s*\([^)]*\)\s*\{", code)
    calls = {}
    for fn in names:
        fa, fb = _c_function_span(code, fn)
        calls[fn] = {g for g in names if re.search(r"\b" + g + r"\s*\(", code[fa:fb][code[fa:fb].index("{"):])}
    def reaches(a, b, seen=()):
        return any(c == b or (c not in seen and reaches(c, b, seen + (c,))) for c in calls.get(a, ()))
    for fn in names:
        if reaches(fn, fn):
            raise AssertionError(f"{name}: {fn} is (mutually) recursive")
    # the only writes: the two ON calls in gpu_power_cycle and w32(reg, r) in power_off; power_off is called exactly for shaders then L2
    wcalls = [re.sub(r"\s+", "", m.group(0)) for m in re.finditer(r"(?<![\w])w32\s*\([^;]*?\)(?=\))|(?<![\w])w32\s*\([^()]*\)", code)]
    wcalls = [c for c in wcalls if not c.startswith("w32(uintptr_tpa")]
    want_w = ["w32(G_TILER_ON,GPU_TILER_PRESENT)", "w32(G_SHADER_ON,GPU_SHADER_PRESENT)", "w32(reg,r)"]
    if stack:
        want_w.append("w32(G_STACK_ON,GPU_STACK_PRESENT)")
    elif re.search(r"\bG_STACK_(?:ON|OFF|PRES)\b|\bGPU_STACK_PRESENT\b", re.sub(r"(?m)^#define\s+G_\w+.*$", "", code)):
        raise AssertionError(f"{name}: the plain build mentions the core-stack registers/mask (they are --gpu-power=stack only)")
    if sorted(wcalls) != sorted(want_w):
        raise AssertionError(f"{name}: unexpected w32() call sites: {wcalls}")
    for fn, want in (("gpu_power_cycle", 3 if stack else 2), ("power_off", 1)):
        fa, fb = _c_function_span(code, fn)
        if len(re.findall(r"\bw32\s*\(", code[fa:fb])) != want:
            raise AssertionError(f"{name}: w32 call sites are only allowed in gpu_power_cycle (ON) and power_off")
    cyc_norm = re.sub(r"\s+", "", cyc)
    offs = ["power_off(G_SHADER_OFF,G_SHADER_RDY,G_SHADER_TR,GPU_SHADER_PRESENT)", "power_off(G_L2_OFF,G_L2_RDY,G_L2_TR,GPU_L2_PRESENT)"]
    if stack:
        offs.insert(1, "power_off(G_STACK_OFF,G_STACK_RDY,G_STACK_TR,GPU_STACK_PRESENT)")
        if not 0 <= cyc_norm.find(offs[0]) < cyc_norm.find(offs[1]) < cyc_norm.find(offs[2]):
            raise AssertionError(f"{name}: OFF order must be shaders, stacks, L2")
        if not 0 <= cyc_norm.find("w32(G_TILER_ON") < cyc_norm.find("w32(G_STACK_ON") < cyc_norm.find("w32(G_SHADER_ON"):
            raise AssertionError(f"{name}: ON order must be tiler/L2, stacks, shaders")
        if "elseif((e=w32(G_STACK_ON" not in cyc_norm or "elseif((e=w32(G_SHADER_ON" not in cyc_norm:
            raise AssertionError(f"{name}: STACK_PWRON / SHADER_PWRON must stay in the else-if chain behind the tiler/L2 wait")
        if not 0 <= cyc_norm.find("wait_for(G_STACK_RDY,GPU_STACK_PRESENT,G_STACK_TR)") < cyc_norm.find("w32(G_SHADER_ON"):
            raise AssertionError(f"{name}: the stacks must be ready before SHADER_PWRON")
    for want in offs:
        if cyc_norm.count(want) != 1:
            raise AssertionError(f"{name}: gpu_power_cycle must call {want} exactly once (the OFF sequence)")
    if not 0 <= cyc_norm.find("wait_idle(G_TILER_TR)") < cyc_norm.find("power_off(G_L2_OFF"):
        raise AssertionError(f"{name}: gpu_power_cycle must drain TILER_PWRTRANS (wait_idle(G_TILER_TR)) before power_off(G_L2_OFF, ...)")
    if cyc_norm.find("power_off(G_SHADER_OFF") > cyc_norm.find("power_off(G_L2_OFF"):
        raise AssertionError(f"{name}: shaders must be powered off before the L2")
    pa_, pb_ = _c_function_span(code, "precheck")
    pre = code[pa_:pb_]
    if len(re.findall(r"return\s+0\s*;", pre)) != 1 or not re.search(r"return\s+0\s*;\s*\}\s*$", pre):
        raise AssertionError(f"{name}: precheck must return 0 (all clear) only as its last statement")
    for need in ("gpu-status-nonzero", "gpu-fault", "rawstat-unexpected", "present-hi-nonzero", "cores-not-off", "no-timer", "stack-busy") + (("stack-present-mismatch", "stack-not-off") if stack else ()):
        if need not in pre:
            raise AssertionError(f"{name}: precheck lost the '{need}' refusal")


def check_gpupower_variant_text(disasm: str, stack: bool, name: str) -> None:
    """Object-level pins that tell the two builds apart: the number of w32 / power_off call sites (the plain object has the 4
    audited writes: 2 ON + power_off x2; the stack object adds exactly the STACK_PWRON site and the STACK_PWROFF power_off call)
    and the STACK_PWRON offset 0xE20 as a mov immediate (the plain object must not carry it)."""
    n_w32 = len(re.findall(r"\bbl\s+[0-9a-f]+ <w32>", disasm))
    n_off = len(re.findall(r"\bbl\s+[0-9a-f]+ <power_off>", disasm))
    n_e20 = len(re.findall(r"\bmovz?\s+[xw]\d+, #0xe20\b", disasm))
    # the STACK_PWROFF (0xE30) immediate test is vacuous (the stack object computes it as 0x1C500E40 - 0x10); the call-site counts are the real pin
    n_e30 = len(re.findall(r"\bmovz?\s+[xw]\d+, #0xe30\b", disasm))
    want = (4, 3, 1) if stack else (3, 2, 0)
    if (n_w32, n_off, n_e20) != want or n_e30:
        raise AssertionError(f"{name}: {'stack' if stack else 'plain'} object has {n_w32} w32 / {n_off} power_off call sites and "
                             f"{n_e20}+{n_e30} STACK_PWRON/PWROFF immediates, expected {want[0]} / {want[1]} and {want[2]}+0")


def gpupower_object_variant_selftest(obj_path: pathlib.Path, stack: bool) -> int:
    disasm = _touch_obj_info(obj_path)[0]
    check_gpupower_variant_text(disasm, stack, "gpupower variant selftest")  # control
    other = "bl\t0 <w32>\n"
    cases = [("an extra w32 call site", disasm + "0000000000ffff00 <evil>:\n  ffff00:\t94000000 \t" + other),
             ("an extra power_off call", disasm + "0000000000ffff00 <evil>:\n  ffff00:\t94000000 \tbl\t0 <power_off>\n"),
             ("a STACK_PWRON immediate" if not stack else "a second STACK_PWRON immediate", disasm + "  ffff04:\td281c400 \tmov\tx0, #0xe20\n"),
             ("a STACK_PWROFF immediate", disasm + "  ffff08:\td281c600 \tmov\tx0, #0xe30\n")]
    if stack:
        cases.append(("a missing STACK_PWRON site", disasm.replace("#0xe20", "#0xe21")))
    caught = 0
    for label, bad in cases:
        try:
            check_gpupower_variant_text(bad, stack, "gpupower variant selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: gpupower {'stack' if stack else 'plain'} object {label} -> {exc}")
        else:
            raise AssertionError(f"gpupower variant object self-test FAILED to catch: {label}")
    print(f"PASS gpupower {'stack' if stack else 'plain'} object variant self-test: {caught}/{len(cases)} bad objects rejected")
    return caught


def gpupower_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "gpupower.c").read_text()
    check_gpupower_source_text(good)  # control must pass
    ins = "static int r32"
    cases = [
        ("inline asm store", good.replace(ins, 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\n' + ins, 1)),
        ("second write path", good.replace(ins, "static void evil(void) { try_write32(0x1c5001a0UL, 1); }\n" + ins, 1)),
        ("second writer via a pointer cast of a variable",
         good.replace(ins, "static void evil(uintptr_t pa) { *(uint32_t *)pa = 0; }\n" + ins, 1)),
        ("volatile outside r32", good.replace(ins, "static void evil(volatile uint32_t *p) { *p = 0; }\n" + ins, 1)),
        ("w32 skips allowlist", good.replace("if (!w32_allowed(pa, v))", "if (0)", 1)),
        ("w32 writes before the guard",
         good.replace("\tif (!w32_allowed(pa, v))\n\t\treturn -2;\n\treturn try_write32(pa, v) ? 0 : -1;",
                      "\t(void)try_write32(pa, v);\n\tif (!w32_allowed(pa, v))\n\t\treturn -2;\n\treturn 0;", 1)),
        ("w32_allowed always returns 1",
         re.sub(r"(static int w32_allowed\(uintptr_t pa, uint32_t v\)\n\{).*?\n\}\n", r"\1\n\treturn 1;\n}\n", good, count=1, flags=re.S)),
        ("extra register in the allowlist (L2_READY)", good.replace("if (pa == G_TILER_ON)", "if (pa == G_TILER_ON || pa == G_L2_RDY)", 1)),
        ("wrong value (L2_PWROFF accepts anything)", good.replace("return v == GPU_L2_PRESENT;", "return v == 0xffffffffu;", 1)),
        ("value ignored (TILER_PWRON)", good.replace("return v == GPU_TILER_PRESENT;", "return 1;", 1)),
        ("TILER_PWROFF added (0x1d0)", good.replace("G_L2_OFF\t(GPU_MALI + 0x1e0u)", "G_L2_OFF\t(GPU_MALI + 0x1d0u)", 1)),
        ("HI word of SHADER_PWRON (0x184)", good.replace("G_SHADER_ON\t(GPU_MALI + 0x180u)", "G_SHADER_ON\t(GPU_MALI + 0x184u)", 1)),
        ("write before the preconditions",
         good.replace("int gpu_power_cycle(void)\n{\n", "int gpu_power_cycle(void)\n{\n\t(void)w32(G_TILER_ON, GPU_TILER_PRESENT);\n", 1)),
        ("unbounded wait (while)", good.replace("for (unsigned i = 0; i < GP_POLLS; i++)", "while (1)", 1)),
        ("unbounded wait (for(;;))", good.replace("for (unsigned i = 0; i < GP_POLLS; i++)", "for (;;)", 1)),
        ("GP_POLLS beyond the cap", good.replace("#define GP_POLLS\t100000u", "#define GP_POLLS\t4000000000u", 1)),
        ("wait_for resets its loop variable",
         good.replace("\t\tif (settled(rdy, want, trans))\n\t\t\treturn 1;", "\t\tif (settled(rdy, want, trans))\n\t\t\treturn 1;\n\t\ti = 0;", 1)),
        ("wait_for recurses", good.replace("\t\tif (settled(rdy, want, trans))\n\t\t\treturn 1;", "\t\tif (settled(rdy, want, trans))\n\t\t\treturn wait_for(rdy, want, trans);", 1)),
        ("wait_idle loses its time bound", good.replace("\t\tif (touch_ticks() - t0 > lim)\n\t\t\treturn 0;\n\t}\n\treturn 0;\n}\n\n/* Read-only.", "\t}\n\treturn 0;\n}\n\n/* Read-only.", 1)),
        ("a write from dump()", good.replace("\tint ok = r32(pa, &v);\n\tunsigned p = put_txt(line, sizeof(line), 0, pre);", "\tint ok = r32(pa, &v);\n\tunsigned p = put_txt(line, sizeof(line), 0, pre);\n\t(void)w32(G_TILER_ON, GPU_TILER_PRESENT);", 1)),
        ("OFF path writes the PWRON register", good.replace("power_off(G_SHADER_OFF, G_SHADER_RDY", "power_off(G_SHADER_ON, G_SHADER_RDY", 1)),
        ("OFF sequence skipped (shaders)", good.replace("d = power_off(G_SHADER_OFF, G_SHADER_RDY, G_SHADER_TR, GPU_SHADER_PRESENT);", "d = 0;", 1)),
        ("stack allowlist leaks into the plain build",
         good.replace("#ifdef GPU_POWER_STACK\n\tif (pa == G_STACK_ON || pa == G_STACK_OFF)\n\t\treturn v == GPU_STACK_PRESENT;\n#endif\n", "\tif (pa == G_STACK_ON || pa == G_STACK_OFF)\n\t\treturn v == GPU_STACK_PRESENT;\n", 1)),
        ("STACK_PWRON loses its else (runs after a failed tiler/L2 wait)", good.replace("\telse if ((e = w32(G_STACK_ON", "\tif ((e = w32(G_STACK_ON", 1)),
        ("stack allowlist accepts any value", good.replace("return v == GPU_STACK_PRESENT;", "return 1;", 1)),
        ("STACK_PWROFF moved (0xe34)", good.replace("G_STACK_OFF\t(GPU_MALI + 0xe30u)", "G_STACK_OFF\t(GPU_MALI + 0xe34u)", 1)),
        ("STACK_PWRON written in the plain build",
         good.replace("#ifdef GPU_POWER_STACK\n\telse if ((e = w32(G_STACK_ON", "\telse if ((e = w32(G_STACK_ON", 1).replace("\t\tup = \"timeout:stack\";\n#endif\n", "\t\tup = \"timeout:stack\";\n", 1)),
        ("stacks powered off after the L2",
         good.replace("#ifdef GPU_POWER_STACK\n\tif (!d) {\t/* corestack path", "#ifdef GPU_POWER_STACK\n\tif (d == 99) {\t/* corestack path", 1).replace("d = power_off(G_L2_OFF, G_L2_RDY, G_L2_TR, GPU_L2_PRESENT);", "d = power_off(G_L2_OFF, G_L2_RDY, G_L2_TR, GPU_L2_PRESENT);\n\t\t\td = power_off(G_STACK_OFF, G_STACK_RDY, G_STACK_TR, GPU_STACK_PRESENT);", 1)),
        ("stacks switched on after the shaders",
         good.replace("	else if ((e = w32(G_STACK_ON, GPU_STACK_PRESENT)))", "	else if ((e = w32(G_STACK_ON, GPU_STACK_PRESENT)) && 0)", 1).replace("	else if (!wait_for(G_STACK_RDY, GPU_STACK_PRESENT, G_STACK_TR))\n\t\tup = \"timeout:stack\";\n#endif\n", "#endif\n", 1)),
        ("stack variant skips the STACK_PRESENT check", good.replace('return "stack-present-mismatch";', "(void)w;", 1)),
        ("L2 off without draining the tiler", good.replace("if (wait_idle(G_TILER_TR)) {", "if (1) {", 1)),
        ("precheck lost the STACK_PWRTRANS refusal", good.replace('return "stack-busy";', "(void)v;", 1)),
        ("precheck short-circuits", good.replace("\tif (!counter_freq())\n\t\treturn \"no-timer\";", "\treturn 0;\n\tif (!counter_freq())\n\t\treturn \"no-timer\";", 1)),
        ("precheck lost the RAWSTAT refusal", good.replace("return \"rawstat-unexpected\"", "return 0", 1)),
        ("power_off writes a value other than READY", good.replace("e = w32(reg, r);", "e = w32(reg, present);", 1)),
        ("raw register outside the audited defines",
         good.replace(ins, "static uintptr_t evil(void) { return GPU_MALI + 0x1a0u; }\n" + ins, 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"gpupower source selftest case did not mutate: {label}"
        try:
            check_gpupower_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: gpupower source {label} -> {exc}")
        else:
            raise AssertionError(f"gpupower source self-test FAILED to catch: {label}")
    print(f"PASS gpupower source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


# ---- gpuclk.c (--gpu-clk): no MMIO of its own; asks the audited ACPM DVFS client (acpm.c) for the GPU init rate, G3DL2 then G3D ----
_GPUCLK_ALLOWED_UNDEF = {
    "con_str", "counter_freq", "wait_ticks",                                  # console
    "acpm_cfg_init", "acpm_why", "acpm_describe", "acpm_dvfs_get",           # the audited ACPM client (reads; its writes are audited in acpm.o)
    "acpm_dvfs_reassert",                                                     # GPU_CLK images only: G3D / G3DL2, ceiling rate only (acpm.c)
    "gpu_probe_cmu",                                                          # gpuprobe.c: read-only CMU_G3D dump
}


def check_gpuclk_source_text(src: str, name: str = "gpuclk.c") -> None:
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    if re.search(r"\b(__asm__|__asm|asm)\b", code):
        raise AssertionError(f"{name}: inline asm is banned")
    for bad in ("try_write32", "try_read32", "volatile", "acpm_dvfs_set", "acpm_pmic_update", "acpm_pmic_read", "w32"):
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned here (no MMIO of its own; only acpm_dvfs_get / acpm_dvfs_reassert reach the ACPM client)")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code):
        raise AssertionError(f"{name}: explicit pointer cast is banned")
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    spans = [_c_function_span(code, f) for f in ("put_txt", "put_dec")]
    for m in re.finditer(r"\bwhile\b", code):
        if not any(a <= m.start() < b for a, b in spans):
            raise AssertionError(f"{name}: 'while' outside the string helpers (retry loops are not allowed: no write is retried)")
    if len(re.findall(r"\bacpm_dvfs_reassert\s*\(", code)) != 1 or "e=acpm_dvfs_reassert(&c,dom,GPU_CLK_KHZ,&t);" not in norm:
        raise AssertionError(f"{name}: exactly one request site is allowed: acpm_dvfs_reassert(&c, dom, GPU_CLK_KHZ, &t)")
    if "#defineIDX_G3DL2GPU_FVMAP_IDX_G3DL2" not in norm:
        raise AssertionError(f"{name}: IDX_G3DL2 must be the soc.json G3DL2 index")
    if "#defineIDX_G3DGPU_FVMAP_IDX_G3D" not in norm:
        raise AssertionError(f"{name}: IDX_G3D must be the soc.json G3D index")
    ga, gb = _c_function_span(code, "gpu_clk")
    body = re.sub(r"\s+", "", code[ga:gb])
    if body.count("dom=k?IDX_G3D:IDX_G3DL2;") != 3:
        raise AssertionError(f"{name}: the three loops (GET, request, read-back) must visit k=0 -> G3DL2 first, then G3D (kbase gpu_dvfs_set_initial_level order)")
    req = body.find("acpm_dvfs_reassert(")
    pre = body[:req]
    for need in ("if(!c.dvfs_ch)", "if(!c.layout_verified)", "if(!c.elem_unit_verified)", "if(!counter_freq())", "e=acpm_describe(&c,&h);if(e){",
                 "if(h.rr!=h.rf||h.sr!=h.sf)", "e=get_one(&c,dom,&cur[k],&t);"):
        if need not in pre:
            raise AssertionError(f"{name}: '{need}' must come before the first request")
    # every refusal before the request returns without a request; the first GET loop returns on error
    if pre.count("return1;") < 7:
        raise AssertionError(f"{name}: the pre-request refusals must all return")
    if not re.search(r"e=get_one\(&c,dom,&cur\[k\],&t\);if\(e\)\{[^}]*return1;\}", pre):
        raise AssertionError(f"{name}: a failing first GET must stop before any request")
    if not re.search(r"for\(k=0;k<2&&!bad;k\+\+\)\{dom=k\?IDX_G3D:IDX_G3DL2;t\.n=0;e=acpm_dvfs_reassert\(&c,dom,GPU_CLK_KHZ,&t\);", body):
        raise AssertionError(f"{name}: the request loop must stop at the first failure (no retry, no second domain)")
    if "gpu_probe_cmu();" not in body or body.find("gpu_probe_cmu();") < body.find("wait_ticks("):
        raise AssertionError(f"{name}: the CMU words are printed again after the request and the settle wait")


def gpuclk_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "gpuclk.c").read_text()
    check_gpuclk_source_text(good)  # control must pass
    cases = [
        ("inline asm", good.replace("static const char *dname", 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\nstatic const char *dname', 1)),
        ("a raw MMIO writer", good.replace("static const char *dname", "static void evil(void) { try_write32(0x1c400140UL, 0x80000010u); }\nstatic const char *dname", 1)),
        ("a pointer cast", good.replace("static const char *dname", "static void evil(uintptr_t pa) { *(uint32_t *)pa = 0; }\nstatic const char *dname", 1)),
        ("the CPU request function", good.replace("e = acpm_dvfs_reassert(&c, dom, GPU_CLK_KHZ, &t);", "e = acpm_dvfs_set(&c, dom, GPU_CLK_KHZ, &t);", 1)),
        ("another rate", good.replace("e = acpm_dvfs_reassert(&c, dom, GPU_CLK_KHZ, &t);", "e = acpm_dvfs_reassert(&c, dom, 848000, &t);", 1)),
        ("G3D before G3DL2 in the request loop",
         good.replace("for (k = 0; k < 2 && !bad; k++) {\n\t\tdom = k ? IDX_G3D : IDX_G3DL2;", "for (k = 0; k < 2 && !bad; k++) {\n\t\tdom = k ? IDX_G3DL2 : IDX_G3D;", 1)),
        ("a retry loop", good.replace("\t\tif (e)\n\t\t\tbad = 1;", "\t\twhile (e)\n\t\t\te = acpm_dvfs_reassert(&c, dom, GPU_CLK_KHZ, &t);", 1)),
        ("the second domain is asked after a failure", good.replace("k < 2 && !bad; k++", "k < 2; k++", 1)),
        ("no layout gate", good.replace("if (!c.layout_verified) {", "if (0) {", 1)),
        ("no elem-unit gate", good.replace("if (!c.elem_unit_verified) {", "if (0) {", 1)),
        ("no describe refusal", good.replace("\tif (e) {\n\t\tsay_txt(\"refused: \", acpm_why(h.why), \"\");", "\tif (0) {\n\t\tsay_txt(\"refused: \", acpm_why(h.why), \"\");", 1)),
        ("a failing first GET does not stop", good.replace("\t\tif (e) {\n\t\t\tsay_txt(\"result: \", \"REFUSED-or-FAILED at the first GET, nothing written\", \"\");\n\t\t\treturn 1;\n\t\t}", "\t\t(void)e;", 1)),
        ("queues not checked", good.replace("if (h.rr != h.rf || h.sr != h.sf) {", "if (0) {", 1)),
        ("the other index for G3D", good.replace("#define IDX_G3D\t\tGPU_FVMAP_IDX_G3D", "#define IDX_G3D\t\t2", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"gpuclk source selftest case did not mutate: {label}"
        try:
            check_gpuclk_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: gpuclk source {label} -> {exc}")
        else:
            raise AssertionError(f"gpuclk source self-test FAILED to catch: {label}")
    print(f"PASS gpuclk source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


# ---- gpurails.c (--gpu-rails): READ-ONLY S2MPG11 register dump through the audited ACPM PMIC read path; no MMIO of its own ----
_GPURAILS_ALLOWED_UNDEF = {"con_str", "counter_freq", "acpm_cfg_init", "acpm_why", "acpm_describe", "acpm_s11_read"}


def check_gpurails_source_text(src: str, name: str = "gpurails.c") -> None:
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    if re.search(r"\b(__asm__|__asm|asm)\b", code):
        raise AssertionError(f"{name}: inline asm is banned")
    for bad in ("try_write32", "try_read32", "volatile", "acpm_pmic_update", "acpm_pmic_read", "acpm_dvfs_set", "acpm_dvfs_get",
                "acpm_dvfs_reassert", "w32"):
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned here (read-only: only acpm_s11_read reaches the ACPM client)")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code):
        raise AssertionError(f"{name}: explicit pointer cast is banned")
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    spans = [_c_function_span(code, f) for f in ("put_txt", "put_dec")]
    for m in re.finditer(r"\bwhile\b", code):
        if not any(a <= m.start() < b for a, b in spans):
            raise AssertionError(f"{name}: 'while' outside the string helpers (no retry loops)")
    if len(re.findall(r"\bacpm_s11_read\s*\(", code)) != 1 or "e=acpm_s11_read(&c,regs[k],&v[k],&t);" not in norm:
        raise AssertionError(f"{name}: exactly one read site is allowed: acpm_s11_read(&c, regs[k], &v[k], &t) with regs = ACPM_S11_REGS")
    if "staticconstuint32_tregs[ACPM_S11_NREG]={ACPM_S11_REGS};" not in norm or "for(k=0;k<ACPM_S11_NREG;k++){" not in norm:
        raise AssertionError(f"{name}: the register list must be soc.json's ACPM_S11_REGS, read in one bounded loop")
    ga, gb = _c_function_span(code, "gpu_rails")
    body = re.sub(r"\s+", "", code[ga:gb])
    req = body.find("acpm_s11_read(")
    pre = body[:req]
    for need in ("if(!c.layout_verified){", "if(!c.elem_unit_verified){", "if(!counter_freq()){", "e=acpm_describe(&c,&h);if(e){",
                 "if(h.rr!=h.rf||h.sr!=h.sf){"):
        if need not in pre:
            raise AssertionError(f"{name}: '{need}' must come before the first read")
    if pre.count("return1;") < 5:
        raise AssertionError(f"{name}: every refusal before the first read must return")
    m = re.search(r"e=acpm_s11_read\(&c,regs\[k\],&v\[k\],&t\);.*?if\(e\)\{.*?break;\}", body)
    if not m:
        raise AssertionError(f"{name}: the first failed read must stop the loop (break), no retry")


def gpurails_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "gpurails.c").read_text()
    check_gpurails_source_text(good)  # control must pass
    cases = [
        ("inline asm", good.replace("static const char *rname", 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\nstatic const char *rname', 1)),
        ("a raw MMIO writer", good.replace("static const char *rname", "static void evil(void) { try_write32(0x17610040UL, 1); }\nstatic const char *rname", 1)),
        ("a pointer cast", good.replace("static const char *rname", "static void evil(uintptr_t pa) { *(uint32_t *)pa = 0; }\nstatic const char *rname", 1)),
        ("a PMIC update", good.replace("e = acpm_s11_read(&c, regs[k], &v[k], &t);", "e = acpm_pmic_update(&c, 0x5C, 0x80, 0x80, &t);", 1)),
        ("a literal register", good.replace("e = acpm_s11_read(&c, regs[k], &v[k], &t);", "e = acpm_s11_read(&c, 0x5C, &v[k], &t);", 1)),
        ("a retry loop", good.replace("\t\tif (e) {\n\t\t\tp = put_txt(line, sizeof(line), p, \" FAILED err=\");", "\t\twhile (e)\n\t\t\te = acpm_s11_read(&c, regs[k], &v[k], &t);\n\t\tif (e) {\n\t\t\tp = put_txt(line, sizeof(line), p, \" FAILED err=\");", 1)),
        ("the loop goes on after a failure", good.replace("\t\t\tbreak;\n\t\t}\n\t\tp = put_txt(line, sizeof(line), p, \" v=0x\");", "\t\t}\n\t\tp = put_txt(line, sizeof(line), p, \" v=0x\");", 1)),
        ("no layout gate", good.replace("if (!c.layout_verified) {", "if (0) {", 1)),
        ("no elem-unit gate", good.replace("if (!c.elem_unit_verified) {", "if (0) {", 1)),
        ("no describe refusal", good.replace("\tif (e) {\n\t\tsay(\"refused: \", acpm_why(h.why));", "\tif (0) {\n\t\tsay(\"refused: \", acpm_why(h.why));", 1)),
        ("queues not checked", good.replace("if (h.rr != h.rf || h.sr != h.sf) {", "if (0) {", 1)),
        ("a second read site", good.replace("\tif (got == ACPM_S11_NREG) {", "\t(void)acpm_s11_read(&c, regs[0], &v[0], &t);\n\tif (got == ACPM_S11_NREG) {", 1)),
        ("another register list", good.replace("static const uint32_t regs[ACPM_S11_NREG] = { ACPM_S11_REGS };", "static const uint32_t regs[ACPM_S11_NREG] = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 };", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"gpurails source selftest case did not mutate: {label}"
        try:
            check_gpurails_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: gpurails source {label} -> {exc}")
        else:
            raise AssertionError(f"gpurails source self-test FAILED to catch: {label}")
    print(f"PASS gpurails source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


# ---- --gpu-rail-on: the ONLY S2MPG11 write of the shim (BUCK2S_CTRL mode field), acpm.c acpm_s11_rail_set + gpurailon.c orchestration + the shim.c sequence ----
_GPURAILON_ALLOWED_UNDEF = {"con_str", "counter_freq", "wait_ticks", "acpm_cfg_init", "acpm_why", "acpm_describe", "acpm_s11_read", "acpm_s11_rail_set"}

_RAILSET_UPDATE = "xfer(c,XM_PMIC,s11_w0(ACPM_S11_ON_REG),FUNC_UPDATE|(val<<8)|((uint32_t)ACPM_S11_ON_MASK<<16),0,0,t,&t->b);"


def check_acpm_s11_writer_source(src: str, name: str = "acpm.c") -> None:
    """acpm_s11_rail_set may express exactly one write: UPDATE of S2MPG11 ACPM_S11_ON_REG under ACPM_S11_ON_MASK with VAL_ON or VAL_OFF, guarded by the pre-read."""
    code = _strip_c_comments(src)
    fa, fb = _c_function_span(code, "acpm_s11_rail_set")
    body = re.sub(r"\s+", "", code[fa:fb])
    if body.count("FUNC_UPDATE") != 1 or body.count(_RAILSET_UPDATE) != 1:
        raise AssertionError(f"{name}: acpm_s11_rail_set must contain exactly one update: {_RAILSET_UPDATE}")
    if "val=on?ACPM_S11_ON_VAL_ON:ACPM_S11_ON_VAL_OFF;" not in body:
        raise AssertionError(f"{name}: the written value must be VAL_ON or VAL_OFF only")
    if len(re.findall(r"\bFUNC_UPDATE\b", code)) != 3:	# the #define, acpm_pmic_update (S2MPG10 rails) and acpm_s11_rail_set
        raise AssertionError(f"{name}: FUNC_UPDATE may appear only in its #define, acpm_pmic_update (S2MPG10 rails) and acpm_s11_rail_set")
    for need in ("e=xfer(c,XM_PMIC,s11_w0(ACPM_S11_ON_REG),FUNC_READ,0,0,t,&t->a);if(e)returne;",
                 "if((cur&ACPM_S11_ON_MASK)!=0)returnrefuse(&t->b,ACPM_E_ON,ACPM_W_ON);",
                 "e=xfer(c,XM_PMIC,s11_w0(ACPM_S11_ON_OUT1),FUNC_READ,0,0,t,&xo);",
                 "if(xo.value<ACPM_S11_ON_VSEL_MIN||xo.value>ACPM_S11_ON_VSEL_MAX)returnrefuse(&t->b,ACPM_E_RAIL,ACPM_W_VSELOUT);",
                 "elseif((cur&ACPM_S11_ON_MASK)==0){returnrefuse(&t->b,ACPM_E_ON,ACPM_W_ON);}",
                 "t->n=3;return"+_RAILSET_UPDATE):
        if need not in body:
            raise AssertionError(f"{name}: acpm_s11_rail_set lost its guard '{need}'")
    if body.find(_RAILSET_UPDATE) < body.find("xo.value>ACPM_S11_ON_VSEL_MAX") or body.find(_RAILSET_UPDATE) < body.find("(cur&ACPM_S11_ON_MASK)!=0"):
        raise AssertionError(f"{name}: the update must come after every guard")
    if re.search(r"\b(for|while|goto|do)\b", code[fa:fb]):
        raise AssertionError(f"{name}: acpm_s11_rail_set must not loop (one request per write, no retry)")
    if body.count("xfer(") != 3:
        raise AssertionError(f"{name}: acpm_s11_rail_set has exactly three xfer calls (pre-read, OUT1 read, update)")


def check_gpurailon_source_text(src: str, name: str = "gpurailon.c") -> None:
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    if re.search(r"\b(__asm__|__asm|asm)\b", code):
        raise AssertionError(f"{name}: inline asm is banned")
    for bad in ("try_write32", "try_read32", "volatile", "acpm_pmic_update", "acpm_pmic_read", "acpm_dvfs_set", "acpm_dvfs_get", "acpm_dvfs_reassert", "w32"):
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned here (the only writer is acpm_s11_rail_set)")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code):
        raise AssertionError(f"{name}: explicit pointer cast is banned")
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    spans = [_c_function_span(code, f) for f in ("put_txt", "put_dec")]
    for m in re.finditer(r"\bwhile\b", code):
        if not any(a <= m.start() < b for a, b in spans):
            raise AssertionError(f"{name}: 'while' outside the string helpers (no retry loops)")
    if len(re.findall(r"\bacpm_s11_rail_set\s*\(", code)) != 2:
        raise AssertionError(f"{name}: exactly two rail-set sites (enable, restore)")
    ea, eb = _c_function_span(code, "gpu_rail_enable")
    ra, rb = _c_function_span(code, "gpu_rail_restore")
    en, rs = re.sub(r"\s+", "", code[ea:eb]), re.sub(r"\s+", "", code[ra:rb])
    if en.count("acpm_s11_rail_set(&c,1,&t,&out1)") != 1 or en.count("acpm_s11_rail_set(") != 1:
        raise AssertionError(f"{name}: gpu_rail_enable has exactly one acpm_s11_rail_set(&c, 1, &t, &out1)")
    if rs.count("acpm_s11_rail_set(&c,0,&t,0)") != 1 or rs.count("acpm_s11_rail_set(") != 1:
        raise AssertionError(f"{name}: gpu_rail_restore has exactly one acpm_s11_rail_set(&c, 0, &t, 0)")
    if not en.find("if(preflight(&c))returnRAIL_REFUSED;") >= 0 or en.find("if(preflight(&c))") > en.find("acpm_s11_rail_set("):
        raise AssertionError(f"{name}: gpu_rail_enable must run preflight() and refuse before its write")
    pa_, pb_ = _c_function_span(code, "preflight")
    pf = re.sub(r"\s+", "", code[pa_:pb_])
    for need in ("if(!c->layout_verified){", "if(!c->elem_unit_verified){", "if(!counter_freq()){", "if(acpm_describe(c,&h)){", "if(h.rr!=h.rf||h.sr!=h.sf){"):
        if need not in pf:
            raise AssertionError(f"{name}: preflight lost '{need}'")
    if pf.count("return1;") < 5:
        raise AssertionError(f"{name}: every preflight refusal must return")
    if "state=((ctrl&ACPM_S11_ON_MASK)==ACPM_S11_ON_VAL_ON&&out1b==out1)?RAIL_ON_OK:RAIL_MISMATCH;" not in en:
        raise AssertionError(f"{name}: RAIL_ON_OK needs mode == on AND OUT1 unchanged")
    if "if(t.n==3&&t.b.published){" not in en or "returnRAIL_UNVERIFIED;" not in en or "returnstate;" not in en:
        raise AssertionError(f"{name}: gpu_rail_enable result handling changed")
    if "if(state==RAIL_REFUSED){" not in rs:
        raise AssertionError(f"{name}: restore must run for every state except REFUSED")
    if code.count("!!! RESTORE FAILED") != 2 or rs.count('summary(state,"FAILED",power);') != 1 or 'summary(state,ok?"ok":"FAILED",power);' not in rs:	# preflight failed, and the read-back says it is not off
        raise AssertionError(f"{name}: a failed restore must be reported loudly")


def check_shim_rail_sequence(src: str, name: str = "shim.c") -> None:
    """The GPU section of shim.c: anything but a verified rail-on (or a failed gpu_clk) skips the power cycle, and the rail is restored AFTER it, always."""
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    a = norm.find("rail_state=gpu_rail_enable();")
    if a < 0 or "if(rail_state!=RAIL_ON_OK)skip_power=1;" not in norm[a:a + 80]:
        raise AssertionError(f"{name}: anything but RAIL_ON_OK after gpu_rail_enable must set skip_power")
    if "if(gpu_clk())skip_power=1;" not in norm:
        raise AssertionError(f"{name}: a failed gpu_clk must set skip_power (review M2)")
    c = norm.find("if(skip_power)con_str(\"GPU\",\"POWERskipped")
    p = norm.find("gpu_power_cycle();")
    r = norm.find("gpu_rail_restore(rail_state,")
    sh = norm.find("gpu_probe_shaders();")
    if not (0 < a < c < p < sh < r) or "else" not in norm[c:p]:
        raise AssertionError(f"{name}: order must be gpu_rail_enable, (skip | gpu_power_cycle), gpu_probe_shaders, gpu_rail_restore")
    # --gpu-handover (docs/research/30, review gpu-handover-review-1 M1/L1/L2): inside the BOOT_XNU block, after 'boot XNU' and before loader_boot, ONE extra
    # gpu_rail_enable whose only restore is for UNVERIFIED/MISMATCH; and after loader_boot returns a restore guarded by RAIL_ON_OK. Nothing else.
    if code.count("gpu_rail_enable()") != 2 or code.count("gpu_rail_restore(") != 3:
        raise AssertionError(f"{name}: exactly two gpu_rail_enable calls (probe flow + handover) and three gpu_rail_restore (probe flow, handover unverified, loader_boot failed)")
    bx = code.find('con_str("SECTION", "boot XNU")')
    lb = code.find("loader_boot(dtb)", bx)
    h = code.find("#if defined(GPU_HANDOVER) && defined(GPU_RAIL_ON)", bx)
    he = code.find("#endif", h)
    hb = re.sub(r"\s+", "", code[h:he]) if h >= 0 and he >= 0 else ""
    if not (0 <= bx < h < he < lb):
        raise AssertionError(f"{name}: the handover rail-on must sit in the BOOT_XNU block after 'boot XNU' and before loader_boot")
    if hb.count("gpu_rail_enable()") != 1 or hb.count("gpu_rail_restore(") != 1 or "if(gpu_ho==RAIL_UNVERIFIED||gpu_ho==RAIL_MISMATCH)gpu_rail_restore(gpu_ho,RAIL_POWER_NONE);" not in hb:
        raise AssertionError(f"{name}: the handover block must restore only for RAIL_UNVERIFIED/RAIL_MISMATCH")
    if any(w in hb for w in ("for(", "while(", "goto", "#if0", "(*")):
        raise AssertionError(f"{name}: no loops, goto or indirect calls in the handover block")
    a2 = code.find("#if defined(GPU_HANDOVER) && defined(GPU_RAIL_ON)", lb)
    a2e = code.find("#endif", a2)
    tb = re.sub(r"\s+", "", code[a2:a2e]) if a2 >= 0 and a2e >= 0 else ""
    if a2 - lb > 400 or tb != "#ifdefined(GPU_HANDOVER)&&defined(GPU_RAIL_ON)if(gpu_ho==RAIL_ON_OK)gpu_rail_restore(gpu_ho,RAIL_POWER_NONE);":
        raise AssertionError(f"{name}: after loader_boot returns, the verified handover rail must be restored (RAIL_ON_OK)")


def gpurailon_selftest() -> int:
    root = pathlib.Path(__file__).resolve().parents[1] / "shim"
    acpm_src, on_src, shim_src = (root / "acpm.c").read_text(), (root / "gpurailon.c").read_text(), (root / "shim.c").read_text()
    check_acpm_s11_writer_source(acpm_src)
    check_gpurailon_source_text(on_src)
    check_shim_rail_sequence(shim_src)
    cases = [
        ("acpm: another mask", check_acpm_s11_writer_source, acpm_src, "((uint32_t)ACPM_S11_ON_MASK << 16)", "(0xffu << 16)"),
        ("acpm: another register in the update", check_acpm_s11_writer_source, acpm_src, "return xfer(c, XM_PMIC, s11_w0(ACPM_S11_ON_REG), FUNC_UPDATE", "return xfer(c, XM_PMIC, s11_w0(ACPM_S11_ON_OUT1), FUNC_UPDATE"),
        ("acpm: an arbitrary value", check_acpm_s11_writer_source, acpm_src, "val = on ? ACPM_S11_ON_VAL_ON : ACPM_S11_ON_VAL_OFF", "val = on ? 0xffu : ACPM_S11_ON_VAL_OFF"),
        ("acpm: no 'must be off' guard", check_acpm_s11_writer_source, acpm_src, "if ((cur & ACPM_S11_ON_MASK) != 0)\t\t\t/* not exactly", "if (0)\t\t\t/* not exactly"),
        ("acpm: no voltage window", check_acpm_s11_writer_source, acpm_src, "xo.value < ACPM_S11_ON_VSEL_MIN || xo.value > ACPM_S11_ON_VSEL_MAX", "0"),
        ("acpm: a retry loop", check_acpm_s11_writer_source, acpm_src, "\treturn xfer(c, XM_PMIC, s11_w0(ACPM_S11_ON_REG), FUNC_UPDATE", "\twhile (0) { }\n\treturn xfer(c, XM_PMIC, s11_w0(ACPM_S11_ON_REG), FUNC_UPDATE"),
        ("acpm: a second FUNC_UPDATE elsewhere", check_acpm_s11_writer_source, acpm_src, "int acpm_s11_rail_set(", "static uint32_t evil_w1(void) { return FUNC_UPDATE | 0x1000u; }\nint acpm_s11_rail_set("),
        ("shim: handover block after loader_boot", check_shim_rail_sequence, shim_src, "con_str(\"SECTION\", \"GPU handover (rail on, left on)\");", "con_str(\"SECTION\", \"GPU handover (rail on, left on)\");\n\tfor (;;) { }"),
        ("shim: handover restore for every result", check_shim_rail_sequence, shim_src, "if (gpu_ho == RAIL_UNVERIFIED || gpu_ho == RAIL_MISMATCH)", "if (1)"),
        ("shim: no restore after loader_boot failed", check_shim_rail_sequence, shim_src, "if (gpu_ho == RAIL_ON_OK)\t/* loader_boot returned", "if (0)\t/* loader_boot returned"),
        ("shim: handover wrapped in #if 0", check_shim_rail_sequence, shim_src, "int gpu_ho;\n", "int gpu_ho;\n#if 0\n#endif\n"),
        ("on: inline asm", check_gpurailon_source_text, on_src, "static void emit(", 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\nstatic void emit('),
        ("on: a raw writer", check_gpurailon_source_text, on_src, "static void emit(", "static void evil(void) { try_write32(0x17610040UL, 1); }\nstatic void emit("),
        ("on: no preflight before the enable", check_gpurailon_source_text, on_src, "if (preflight(&c))\n\t\treturn RAIL_REFUSED;", "if (0)\n\t\treturn RAIL_REFUSED;"),
        ("on: preflight without the queue check", check_gpurailon_source_text, on_src, "if (h.rr != h.rf || h.sr != h.sf) {", "if (0) {"),
        ("on: OUT1 not compared", check_gpurailon_source_text, on_src, " && out1b == out1", ""),
        ("on: a retry of the enable", check_gpurailon_source_text, on_src, "\tif (e) {\n\t\tif (t.n == 3 && t.b.published) {", "\twhile (e)\n\t\te = acpm_s11_rail_set(&c, 1, &t, &out1);\n\tif (e) {\n\t\tif (t.n == 3 && t.b.published) {"),
        ("on: a failed OUT1 read counted as a published update", check_gpurailon_source_text, on_src, "t.n == 3 && t.b.published", "t.b.published"),
        ("acpm: the update no longer marks t->n = 3", check_acpm_s11_writer_source, acpm_src, "\tt->n = 3;", "\tt->n = 2;"),
        ("shim: no shader log before the restore", check_shim_rail_sequence, shim_src, "\tgpu_probe_shaders();\n\tgpu_rail_restore", "\tgpu_rail_restore"),
        ("on: the restore also switches on", check_gpurailon_source_text, on_src, "acpm_s11_rail_set(&c, 0, &t, 0)", "acpm_s11_rail_set(&c, 1, &t, 0)"),
        ("on: no restore after an unverified enable", check_gpurailon_source_text, on_src, "if (state == RAIL_REFUSED) {", "if (state != RAIL_ON_OK) {"),
        ("on: a silent restore failure", check_gpurailon_source_text, on_src, "!!! RESTORE FAILED: ", "restore problem: "),
        ("shim: power cycle after a failed enable", check_shim_rail_sequence, shim_src, "if (rail_state != RAIL_ON_OK)\n\t\tskip_power = 1;", "(void)0;"),
        ("shim: restore before the power cycle", check_shim_rail_sequence, shim_src, "\telse\n\t\tgpu_power_cycle();", "\telse {\n\t\tgpu_rail_restore(rail_state, 0);\n\t\tgpu_power_cycle();\n\t}"),
        ("shim: a failed gpu_clk does not skip", check_shim_rail_sequence, shim_src, "if (gpu_clk())\n\t\tskip_power = 1;", "gpu_clk();"),
    ]
    caught = 0
    for label, fn, good, old, newtxt in cases:
        assert old in good, f"gpurailon selftest case does not apply: {label}"
        bad = good.replace(old, newtxt, 1)
        try:
            fn(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: gpu-rail-on {label} -> {exc}")
        else:
            raise AssertionError(f"gpu-rail-on self-test FAILED to catch: {label}")
    print(f"PASS gpu-rail-on source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


def check_acpm_rail_set_object_text(disasm: str, name: str, required: bool) -> None:
    """The acpm.o built with GPU_RAIL_ON: acpm_s11_rail_set has exactly three xfer calls and the update word is built only from MASK 0xC0 and VAL 0x40 / 0;
    a non-rail-on object must not carry the function at all."""
    m = re.search(r"(?ms)^[0-9a-f]+ <acpm_s11_rail_set>:\n(.*?)(?=^[0-9a-f]+ <|\Z)", disasm)
    if not m:
        if required:
            raise AssertionError(f"{name}: acpm_s11_rail_set missing from the GPU_RAIL_ON object")
        return
    blk = m.group(1)
    if len(re.findall(r"\bbl\s+[0-9a-f]+ <xfer", blk)) != 3:
        raise AssertionError(f"{name}: acpm_s11_rail_set must call xfer exactly three times")
    w2 = re.findall(r"\bmov\s+w2, #(0x[0-9a-f]+)", blk)
    if sorted(w2) != ["0x1115", "0x1115", "0x1116"]:
        raise AssertionError(f"{name}: request word 0 constants {w2}: expected reg 0x15 (pre-read, update) and 0x16 (OUT1 read) on S2MPG11 (channel 1, type 1)")
    # update word = 2 | 0xC0 << 16 | (on ? 1 : 0) << 14 (VAL_ON 0x40 << 8): cset on the flag, one orr with lsl #14, one movk of the mask
    if len(re.findall(r"\bmovk\s+w\d+, #0xc0, lsl #16", blk)) != 1 or len(re.findall(r"\borr\s+w\d+, w\d+, w\d+, lsl #14\b", blk)) != 1 or len(re.findall(r"\bcset\s+w\d+, ne\b", blk)) != 1:
        raise AssertionError(f"{name}: the update word must be built from MASK 0xC0 << 16 and (on ? 1 : 0) << 14 (VAL_ON 0x40 or 0) only")
    if len(re.findall(r"\bmovk\b", blk)) != 1 or re.search(r"#0x(?:c0c0|c000|ff)\b", blk):
        raise AssertionError(f"{name}: unexpected constants in acpm_s11_rail_set")


def gpurailon_object_selftest(obj_path: pathlib.Path) -> int:
    disasm = _touch_obj_info(obj_path)[0]
    check_acpm_rail_set_object_text(disasm, "acpm_railon selftest", True)
    cases = [("a fourth xfer call", disasm.replace("bl\t7e0 <xfer>", "bl\t7e0 <xfer>\n  ffff0:\t97fffd00 \tbl\t7e0 <xfer>", 1) if False else None)]
    cases = [
        ("another mask", re.sub(r"(movk\s+w\d+, )#0xc0(, lsl #16)", r"\1#0xff\2", disasm, count=1)),
        ("another register", re.sub(r"(mov\s+w2, )#0x1116", r"\1#0x1117", disasm, count=1)),
        ("an extra xfer call", re.sub(r"(<acpm_s11_rail_set>:\n)", r"\1  ffff0:\t97fffd00 \tbl\t7e0 <xfer>\n", disasm, count=1)),
        ("function absent", disasm.replace("<acpm_s11_rail_set>:", "<acpm_other>:", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != disasm, f"gpurailon object selftest did not mutate: {label}"
        try:
            check_acpm_rail_set_object_text(bad, "acpm_railon selftest", True)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: acpm_railon object {label} -> {exc}")
        else:
            raise AssertionError(f"acpm_railon object self-test FAILED to catch: {label}")
    print(f"PASS acpm_railon object self-test: {caught}/{len(cases)} bad objects rejected")
    return caught


# ---- wlanrails.c (--wlan-rails): READ-ONLY S2MPG10 PCIe-PHY LDOs (audited ACPM client) + CMU_HSI2 / PMU words behind the UFS gate; docs/research/29 section 4 ----
_WLANRAILS_ALLOWED_UNDEF = {"con_str", "counter_freq", "try_read32", "acpm_cfg_init", "acpm_why", "acpm_describe", "acpm_s10_read"}
_WLANRAILS_MMIO = {"WLAN_CMU_HSI2 + WLAN_CMU_OFF_PCIE_MUX", "WLAN_CMU_HSI2 + WLAN_CMU_OFF_OSC", "WLAN_CMU_HSI2 + WLAN_CMU_OFF_QCH_GPIO", "WLAN_CMU_HSI2 + WLAN_CMU_OFF_QCH_PCIE_A",
                   "WLAN_CMU_HSI2 + WLAN_CMU_OFF_QCH_PCIE_B", "WLAN_CMU_HSI2 + WLAN_CMU_OFF_QCH_UFS", "WLAN_CMU_HSI2 + WLAN_CMU_OFF_QCH_SYSREG", "WLAN_PMU_TXCO"}
_WLANRAILS_TOKENS = {"WLAN_CMU_HSI2", "WLAN_PMU_TXCO", "WLAN_S10_L16M_CTRL", "WLAN_S10_L17M_CTRL", "WLAN_S10_L18M_CTRL", "WLAN_S10_L19M_CTRL"} | \
    {"WLAN_CMU_OFF_" + n for n in ("PCIE_MUX", "OSC", "QCH_GPIO", "QCH_PCIE_A", "QCH_PCIE_B", "QCH_UFS", "QCH_SYSREG")}


def check_wlanrails_source_text(src: str, name: str = "wlanrails.c") -> None:
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    if re.search(r"\b(__asm__|__asm|asm)\b", code):
        raise AssertionError(f"{name}: inline asm is banned")
    for bad in ("try_write32", "volatile", "acpm_pmic_update", "acpm_pmic_read", "acpm_s11_read", "acpm_s11_rail_set", "acpm_dvfs_set", "acpm_dvfs_get", "acpm_dvfs_reassert", "w32", "wait_ticks"):
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned here (read-only: MMIO via try_read32 in rd()/the UFS gate, rails via acpm_s10_read)")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code):
        raise AssertionError(f"{name}: explicit pointer cast is banned")
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    spans = [_c_function_span(code, f) for f in ("put_txt", "put_dec")]
    for m in re.finditer(r"\bwhile\b", code):
        if not any(a <= m.start() < b for a, b in spans):
            raise AssertionError(f"{name}: 'while' outside the string helpers (no retry loops)")
    toks = set(re.findall(r"\bWLAN_\w+\b", code))
    if toks - _WLANRAILS_TOKENS:
        raise AssertionError(f"{name}: WLAN_ values outside the audited set: {sorted(toks - _WLANRAILS_TOKENS)}")
    # MMIO: try_read32 only in rd() (every call site of rd names one of the eight cited words) and the one UFS HCI VER read of the gate
    ra, rb = _c_function_span(code, "rd")
    if len(re.findall(r"\btry_read32\s*\(", code)) != 2 or code[ra:rb].count("try_read32(") != 1:
        raise AssertionError(f"{name}: try_read32 is allowed exactly in rd() and in the UFS gate")
    if "ver_ok=try_read32(UFS_BASE+UFS_REG_VER,&ver);" not in norm or "#defineUFS_REG_VER0x08u" not in norm:
        raise AssertionError(f"{name}: the CMU gate must read the UFS HCI VER word (UFS_BASE + 0x08)")
    sites = re.findall(r"\brd\(\"(?:CMU|PMU)\",\"\w+\",([^,]+),&\w+\)", norm)
    if len(sites) != 8 or set(sites) != {re.sub(r"\s+", "", a) for a in _WLANRAILS_MMIO}:
        raise AssertionError(f"{name}: rd() call sites differ from the eight audited words: {sorted(sites)}")
    ga, gb = _c_function_span(code, "wlan_rails")
    body = re.sub(r"\s+", "", code[ga:gb])
    first_cmu = body.find('rd("CMU"')
    if body.find("gate=ver_ok&&ver!=0;") < 0 or body.find("if(gate){") < 0 or not 0 < body.find("gate=ver_ok&&ver!=0;") < body.find("if(gate){") < first_cmu:
        raise AssertionError(f"{name}: every CMU_HSI2 read must sit inside `if (gate)` with gate = UFS VER readable and nonzero")
    if body.count('rd("CMU"') != 7 or body.rfind('rd("CMU"') > body.find("txco_ok=rd(") or body.find("}", first_cmu) < first_cmu:
        raise AssertionError(f"{name}: the seven CMU reads must precede the PMU read")
    # ACPM: one read site over the cited register list, after every ACPM gate; the first failure stops the loop
    if len(re.findall(r"\bacpm_s10_read\s*\(", code)) != 1 or "e=acpm_s10_read(&c,regs[k],&v[k],&t);" not in norm:
        raise AssertionError(f"{name}: exactly one read site is allowed: acpm_s10_read(&c, regs[k], &v[k], &t)")
    if "staticconstuint32_tregs[4]={WLAN_S10_L16M_CTRL,WLAN_S10_L17M_CTRL,WLAN_S10_L18M_CTRL,WLAN_S10_L19M_CTRL};" not in norm or "for(k=0;k<4;k++){" not in norm:
        raise AssertionError(f"{name}: the register list must be the four soc.json LDO control registers, read in one bounded loop")
    req = body.find("acpm_s10_read(")
    pre = body[:req]
    for need in ("if(!c.layout_verified){", "elseif(!c.elem_unit_verified){", "elseif(!counter_freq()){", "elseif(acpm_describe(&c,&h)){", "elseif(h.rr!=h.rf||h.sr!=h.sf){"):
        if need not in pre:
            raise AssertionError(f"{name}: '{need}' must come before the first rail read")
    if not re.search(r"e=acpm_s10_read\(&c,regs\[k\],&v\[k\],&t\);.*?if\(e\)\{.*?break;\}", body):
        raise AssertionError(f"{name}: the first failed rail read must stop the loop (break), no retry")


def wlanrails_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "wlanrails.c").read_text()
    check_wlanrails_source_text(good)  # control must pass
    cases = [
        ("inline asm", good.replace("static void emit(", 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\nstatic void emit(', 1)),
        ("a raw writer", good.replace("static void emit(", "static void evil(void) { try_write32(0x17463EC4UL, 1); }\nstatic void emit(", 1)),
        ("a PMIC update", good.replace("e = acpm_s10_read(&c, regs[k], &v[k], &t);", "e = acpm_pmic_update(&c, 0x5C, 0x80, 0x80, &t);", 1)),
        ("a literal register", good.replace("e = acpm_s10_read(&c, regs[k], &v[k], &t);", "e = acpm_s10_read(&c, 0x5C, &v[k], &t);", 1)),
        ("another register list", good.replace("WLAN_S10_L19M_CTRL };", "0x57 };", 1)),
        ("a second read site", good.replace("\t\tif (got == 4) {", "\t\t(void)acpm_s10_read(&c, regs[0], &v[0], &t);\n\t\tif (got == 4) {", 1)),
        ("a retry loop", good.replace("\t\t\tif (e) {\n\t\t\t\tp = put_txt(line, sizeof(line), p, \" FAILED err=\");", "\t\t\twhile (e)\n\t\t\t\te = acpm_s10_read(&c, regs[k], &v[k], &t);\n\t\t\tif (e) {\n\t\t\t\tp = put_txt(line, sizeof(line), p, \" FAILED err=\");", 1)),
        ("no queue check", good.replace("} else if (h.rr != h.rf || h.sr != h.sf) {", "} else if (0) {", 1)),
        ("no describe", good.replace("} else if (acpm_describe(&c, &h)) {", "} else if (0) {", 1)),
        ("failure does not stop", good.replace("\t\t\t\tbreak;\n", "", 1)),
        ("another MMIO word", good.replace("rd(\"PMU\", \"TXCO\", WLAN_PMU_TXCO, &txco)", "rd(\"PMU\", \"TXCO\", WLAN_PMU_PCIE1_ISO, &txco)", 1)),
        ("a CMU word outside the list", good.replace("WLAN_CMU_HSI2 + WLAN_CMU_OFF_OSC, &osc", "WLAN_CMU_HSI2 + 0x1000, &osc", 1)),
        ("CMU read without the UFS gate", good.replace("\tif (gate) {", "\tif (1) {", 1)),
        ("gate always open", good.replace("gate = ver_ok && ver != 0;", "gate = 1;", 1)),
        ("a second raw read", good.replace("static void emit(", "static void evil(uint32_t *v) { (void)try_read32(WLAN_CMU_HSI2 + 0x1000, v); }\nstatic void emit(", 1)),
        ("volatile", good.replace("static void emit(", "static volatile int evil_v;\nstatic void emit(", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"wlanrails source selftest case did not mutate: {label}"
        try:
            check_wlanrails_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlanrails source {label} -> {exc}")
        else:
            raise AssertionError(f"wlanrails source self-test FAILED to catch: {label}")
    print(f"PASS wlanrails source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


# ---- wlanen.c (--wlan-en): the ONLY writes are the WLAN_EN field of GPP14.CON and the WLAN_EN bit of GPP14.DAT (docs/research/29 W2b) ----
_WLANEN_KW = dict(base_macro=None, strict=True, guard_funcs=("w32_allowed", "wmask"))
_WLANEN_ALLOWED_UNDEF = {"try_read32", "try_write32", "con_str", "counter_freq", "wait_ticks"}
_WLANEN_DEFS = {"EN_CON": "(WLAN_GPP14)", "EN_DAT": "(WLAN_GPP14+4u)", "PERST_CON": "(WLAN_GPH2)", "PERST_DAT": "(WLAN_GPH2+4u)", "EN_SHIFT": "(4u*(unsigned)WLAN_GPP14_EN_PIN)",
                "EN_CON_MASK": "(0xfu<<EN_SHIFT)", "EN_DAT_MASK": "(1u<<(unsigned)WLAN_GPP14_EN_PIN)", "PERST_SHIFT": "(4u*(unsigned)WLAN_GPH2_PERST_PIN)",
                "PERST_DAT_MASK": "(1u<<(unsigned)WLAN_GPH2_PERST_PIN)", "FUNC_INPUT": "0u", "FUNC_OUTPUT": "1u", "WAIT_MS": "200u"}
_WLANEN_WMASK = "if(pa==EN_CON)returnEN_CON_MASK;if(pa==EN_DAT)returnEN_DAT_MASK;return0u;"
_WLANEN_ALLOW = "uint32_tm=wmask(pa);if(m==0u||((old^v)&~m)!=0u)return0;if(pa==EN_CON&&((v&m)>>EN_SHIFT)>FUNC_OUTPUT)return0;return1;"


def check_wlanen_source_text(src: str, name: str = "wlanen.c") -> None:
    check_usbgadget_source_text(src, name, **_WLANEN_KW)
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    defs = dict(re.findall(r"(?m)^#define\s+(\w+)\s+(.+?)\s*$", re.sub(r"\\\n", " ", code)))
    for k, want in _WLANEN_DEFS.items():
        if re.sub(r"\s+", "", defs.get(k, "")) != want:
            raise AssertionError(f"{name}: #define {k} is not the audited {want}: {defs.get(k)!r}")
    for fn, want in (("wmask", _WLANEN_WMASK), ("w32_allowed", _WLANEN_ALLOW)):
        a, b = _c_function_span(code, fn)
        body = re.sub(r"\s+", "", code[a:b])
        body = body[body.index("{") + 1:-1]
        if body != want:
            raise AssertionError(f"{name}: {fn} is not the audited allowlist: {body}")
    wa, wb = _c_function_span(code, "w32")
    wbody = re.sub(r"\s+", "", code[wa:wb])
    if "if(wmask(pa)==0u)return-2;" not in wbody or "v=(old&~wmask(pa))|(bits&wmask(pa));" not in wbody or wbody.count("try_write32(") != 1:
        raise AssertionError(f"{name}: w32 must refuse an unmasked address and write only (old & ~mask) | (bits & mask)")
    # exactly four call sites of the writer, in the audited order and arguments
    if len(re.findall(r"\bw32\s*\(", code)) != 5 or len(re.findall(r"\bw32_allowed\s*\(", code)) != 2:
        raise AssertionError(f"{name}: w32 has exactly four call sites (enable DAT, enable CON, restore DAT, restore CON)")
    ea, eb = _c_function_span(code, "wlan_en_run")
    en = re.sub(r"\s+", "", code[ea:eb])
    ra, rb = _c_function_span(code, "restore")
    rs = re.sub(r"\s+", "", code[ra:rb])
    if en.count("w32(") != 2 or "e1=w32(EN_DAT,EN_DAT_MASK);" not in en or "if(e1==0)e2=w32(EN_CON,FUNC_OUTPUT<<EN_SHIFT);" not in en:
        raise AssertionError(f"{name}: enable = w32(EN_DAT, EN_DAT_MASK) then, only if it worked, w32(EN_CON, FUNC_OUTPUT << EN_SHIFT)")
    if rs.count("w32(") != 2 or "inte1=w32(EN_DAT,dat0),e2,ok;" not in rs or "e2=w32(EN_CON,con0);" not in rs or rs.find("w32(EN_DAT,dat0)") > rs.find("w32(EN_CON,con0)"):
        raise AssertionError(f"{name}: restore = w32(EN_DAT, saved) THEN w32(EN_CON, saved), nothing else")
    # nothing is written before the preconditions held: every refusal returns before the first w32 of wlan_en_run
    first_w = en.find("w32(")
    pre = en[:first_w]
    for need in ("if(!counter_freq()){", "if(!r32(EN_CON,&con0)||!r32(EN_DAT,&dat0)||!r32(PERST_CON,&pcon)||!r32(PERST_DAT,&pdat)){", "why=precondition(con0,dat0,pcon,pdat);", "if(why){"):
        if need not in pre:
            raise AssertionError(f"{name}: '{need}' must come before the first write")
    if pre.count("return1;") < 3:
        raise AssertionError(f"{name}: every refusal before the first write must return")
    pa_, pb_ = _c_function_span(code, "precondition")
    pc = re.sub(r"\s+", "", code[pa_:pb_])
    for need in ("if(((con&EN_CON_MASK)>>EN_SHIFT)!=FUNC_INPUT)", "if(dat&EN_DAT_MASK)", "if(((pcon>>PERST_SHIFT)&0xfu)>FUNC_OUTPUT)", "if(pdat&PERST_DAT_MASK)"):
        if need not in pc:
            raise AssertionError(f"{name}: precondition lost '{need}'")
    # the restore always runs after the enable, unconditionally, and a failure is loud
    if en.count("restored=restore(con0,dat0);") != 1 or en.find("restored=restore(con0,dat0);") < en.find("e1=w32(") or re.search(r"\)\s*restored=restore", en):
        raise AssertionError(f"{name}: `restored = restore(con0, dat0);` must follow the enable unconditionally")
    if code.count("!!! RESTORE FAILED") != 2 or "if(!restored){" not in en or 'summary(enable_ok?"ok":"fault",sample,restored?"ok":"FAILED");' not in en:
        raise AssertionError(f"{name}: a failed restore must be reported loudly (two !!! lines and restore=FAILED)")
    if "wait_ticks(counter_freq()/1000u*WAIT_MS);" not in en or en.count("wait_ticks(") != 1 or "if(enable_ok){" not in en[:en.find("wait_ticks(")]:
        raise AssertionError(f"{name}: one 200 ms wait on the counter, only after a verified enable")
    # bounded: no loops except the two string helpers
    if re.search(r"\bgoto\b|\bdo\s*\{", code):
        raise AssertionError(f"{name}: goto / do-while is banned")
    for m in re.finditer(r"\b(while|for)\b", code):
        if not any(a <= m.start() < b for a, b in [_c_function_span(code, f) for f in ("put_txt", "put_hex")]):
            raise AssertionError(f"{name}: loop outside the string helpers (no retry loops)")


def wlanen_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "wlanen.c").read_text()
    check_wlanen_source_text(good)  # control must pass
    ins = "static int r32"
    cases = [
        ("inline asm store", good.replace(ins, 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\n' + ins, 1)),
        ("second write path", good.replace(ins, "static void evil(void) { try_write32(0x14440004UL, 0); }\n" + ins, 1)),
        ("w32 skips the allowlist", good.replace("if (!w32_allowed(pa, old, v))", "if (0)", 1)),
        ("wmask widened", good.replace("return EN_CON_MASK;", "return 0xffffffffu;", 1)),
        ("a third register", good.replace("\treturn 0u;\n}\n\n/* v may differ", "\tif (pa == PERST_DAT)\n\t\treturn PERST_DAT_MASK;\n\treturn 0u;\n}\n\n/* v may differ", 1)),
        ("allowlist ignores the field value", good.replace(" > FUNC_OUTPUT)\n\t\treturn 0;", " > 0xfu)\n\t\treturn 0;", 1)),
        ("CON before DAT", good.replace("e1 = w32(EN_DAT, EN_DAT_MASK);\n\tif (e1 == 0)\n\t\te2 = w32(EN_CON, FUNC_OUTPUT << EN_SHIFT);", "e1 = w32(EN_CON, FUNC_OUTPUT << EN_SHIFT);\n\tif (e1 == 0)\n\t\te2 = w32(EN_DAT, EN_DAT_MASK);", 1)),
        ("another field value", good.replace("w32(EN_CON, FUNC_OUTPUT << EN_SHIFT)", "w32(EN_CON, 0xfu << EN_SHIFT)", 1)),
        ("restore only after a good enable", good.replace("restored = restore(con0, dat0);", "restored = enable_ok ? restore(con0, dat0) : 1;", 1)),
        ("restore CON before DAT", good.replace("int e1 = w32(EN_DAT, dat0), e2, ok;", "int e1 = 0, e2, ok;", 1).replace("e2 = w32(EN_CON, con0);", "e2 = w32(EN_CON, con0);\n\te1 = w32(EN_DAT, dat0);", 1)),
        ("no precondition refusal", good.replace("\tif (why) {", "\tif (0) {", 1)),
        ("silent restore failure", good.replace("!!! RESTORE FAILED: ", "restore problem: ")),
        ("restore result ignored", good.replace('restored ? "ok" : "FAILED"', '"ok"', 1)),
        ("a retry loop", good.replace("\tenable_ok = e1 == 0 && e2 == 0;", "\twhile (e1 != 0)\n\t\te1 = w32(EN_DAT, EN_DAT_MASK);\n\tenable_ok = e1 == 0 && e2 == 0;", 1)),
        ("longer wait", good.replace("#define WAIT_MS\t\t200u", "#define WAIT_MS\t\t2000u", 1)),
        ("wait without a verified enable", good.replace("\tif (enable_ok) {\n\t\twait_ticks", "\tif (1) {\n\t\twait_ticks", 1)),
        ("another pin", good.replace("#define EN_SHIFT\t(4u * (unsigned)WLAN_GPP14_EN_PIN)", "#define EN_SHIFT\t(4u * (unsigned)WLAN_GPH2_PERST_PIN)", 1)),
        ("a pad-function precondition dropped", good.replace("if (((con & EN_CON_MASK) >> EN_SHIFT) != FUNC_INPUT)", "if (0)", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"wlanen source selftest case did not mutate: {label}"
        try:
            check_wlanen_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlanen source {label} -> {exc}")
        else:
            raise AssertionError(f"wlanen source self-test FAILED to catch: {label}")
    print(f"PASS wlanen source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


_WLANEN_DEST_FREE = {"str", "stp", "strb", "strh", "cmp", "cmn", "tst", "cbz", "cbnz", "b", "ret", "nop", "tbz", "tbnz", "ccmp"}


def check_wlanen_w32_object_text(disasm: str, name: str = "wlanen.o") -> None:
    """The COMPILED w32 must still carry its own allowlist (review B1: GCC specialised a noinline w32 for its constant callers and dropped the address compare), and every
    `bl w32` must load x0 from `mov x0, #0x1c0|#0x1c4` + `movk x0, #0x1084, lsl #16` (= GPP14.CON / GPP14.DAT) with nothing else writing x0 in between."""
    blocks = re.split(r"(?m)^(?=[0-9a-f]+ <[^>]+>:$)", disasm)
    w = [b for b in blocks if re.match(r"[0-9a-f]+ <w32>:", b)]
    if len(w) != 1:
        raise AssertionError(f"{name}: expected exactly one w32 function, found {len(w)}")
    wb = w[0]
    need = [(r"\bmovk\s+x\d+, #0x1084, lsl #16", "the GPP14 base high half"), (r"\bmov\s+x\d+, #0x1c0\b", "the CON address"), (r"\badd\s+x\d+, x\d+, #0x4\b", "the DAT address (CON + 4)"),
            (r"\bmov\s+w0, #0xfffffffe\b", "the -2 refusal return"), (r"\band\s+w\d+, w\d+, #0xf00\b", "the CON field mask"), (r"\band\s+w\d+, w\d+, #0x4\b", "the DAT bit mask"),
            (r"\bbl\s+[0-9a-f]+ <try_read32>|R_AARCH64_CALL26\s+try_read32", "the fault-guarded read"), (r"R_AARCH64_CALL26\s+try_write32", "the one write")]
    for rx, what in need:
        if not re.search(rx, wb):
            raise AssertionError(f"{name}: the compiled w32 lost {what} (specialised away? it must keep its own allowlist compare)")
    if len(re.findall(r"\bcmp\s+x0, x\d+\b", wb)) != 2:
        raise AssertionError(f"{name}: the compiled w32 must compare its address argument against exactly the two registers")
    insn = re.compile(r"^\s*[0-9a-f]+:\t[0-9a-f]+ \t(\S+)\s*(.*?)(?:\s*//.*)?$")
    sites = 0
    for b in blocks:
        if re.match(r"[0-9a-f]+ <w32>:", b):
            continue
        lines = [l for l in b.splitlines() if insn.match(l)]
        for i, l in enumerate(lines):
            m = insn.match(l)
            if not (m.group(1) == "bl" and m.group(2).endswith("<w32>")):
                continue
            sites += 1
            writers = []
            for j in range(i - 1, max(i - 40, -1), -1):
                mj = insn.match(lines[j])
                mn, ops = mj.group(1), mj.group(2)
                if mn == "bl" or (mn not in _WLANEN_DEST_FREE and re.match(r"[xw]0\b", ops)):
                    writers.append((mn, ops))
                    if len(writers) == 2:
                        break
            if len(writers) < 2 or not (writers[0][0] == "movk" and writers[0][1] == "x0, #0x1084, lsl #16"):
                raise AssertionError(f"{name}: a call of w32 in {b.splitlines()[0]} does not load x0 with the GPP14 base high half last")
            if not (writers[1][0] == "mov" and re.fullmatch(r"x0, #0x1c[04]", writers[1][1])):
                raise AssertionError(f"{name}: a call of w32 in {b.splitlines()[0]} does not pass 0x108401C0 / 0x108401C4 ({writers[1]})")
    if sites != 4:
        raise AssertionError(f"{name}: w32 must have exactly four call sites, found {sites}")


def _mut_site(disasm: str, old: str, new: str) -> str:
    """Replace the last `old` before the first call of w32 outside w32 itself (mutation helper)."""
    i = disasm.index("bl\t100 <w32>")
    j = disasm.rfind(old, 0, i)
    assert j >= 0, old
    return disasm[:j] + new + disasm[j + len(old):]


def wlanen_w32_object_selftest(obj_path: pathlib.Path) -> int:
    disasm = _touch_obj_info(obj_path)[0]
    check_wlanen_w32_object_text(disasm, "wlanen selftest")  # control must pass
    wblk = re.search(r"(?ms)^[0-9a-f]+ <w32>:\n.*?(?=^[0-9a-f]+ <|\Z)", disasm).group(0)
    specialised = ("0000000000000100 <w32>:\n 100:\ta9bd7bfd \tstp\tx29, x30, [sp, #-48]!\n 104:\t94000000 \tbl\t0 <try_read32>\n"
                   "\t\t\t104: R_AARCH64_CALL26\ttry_read32\n 108:\t94000000 \tbl\t0 <try_write32>\n\t\t\t108: R_AARCH64_CALL26\ttry_write32\n 10c:\td65f03c0 \tret\n")
    cases = [
        ("w32 specialised: no address compare", disasm.replace(wblk, specialised, 1)),
        ("w32 without the refusal return", disasm.replace("mov\tw0, #0xfffffffe", "mov\tw0, #0x0", 1)),
        ("w32 compares another address", re.sub(r"(mov\s+x\d+, )#0x1c0\b", r"\1#0x1c8", disasm, count=1)),
        ("w32 mask widened", re.sub(r"(and\s+w\d+, w\d+, )#0xf00\b", r"\1#0xf0f", disasm, count=1)),
        ("a call site passes another address", _mut_site(disasm, "#0x1c4", "#0x1c8")),
        ("a call site passes a computed address", _mut_site(disasm, "movk\tx0, #0x1084, lsl #16", "add\tx0, x0, x19")),
        ("an extra call site", disasm + "0000000000fff000 <evil>:\n fff000:\td2800009 \tmov\tx9, #0x0\n fff004:\t2a0903e0 \tmov\tw0, w9\n fff008:\t97fffe78 \tbl\t100 <w32>\n"),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != disasm, f"wlanen w32 object selftest case did not mutate: {label}"
        try:
            check_wlanen_w32_object_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlanen w32 object {label} -> {exc}")
        else:
            raise AssertionError(f"wlanen w32 object self-test FAILED to catch: {label}")
    print(f"PASS wlanen w32 object self-test: {caught}/{len(cases)} bad objects rejected")
    return caught


# ---- wlansmc.c (--wlan-smc-read): exactly ONE `smc` instruction, SMC_CMD_PRIV_REG with option READ, two addresses (docs/research/29 W2a) ----
_WLANSMC_ALLOWED_UNDEF = {"con_str", "try_read32"}


_ISO_BLOCK_RX = re.compile(r"#ifdef WLAN_ISO_RUN\n(?:(?!#ifdef WLAN_ISO_RUN).)*?#endif\t/\* WLAN_ISO_RUN \*/\n", re.S)
_WLANSMC_RMW_SET = ("registeruint64_tx0__asm__(\"x0\")=SMC_FID_PRIV_REG;registeruint64_tx1__asm__(\"x1\")=ISO_REG;registeruint64_tx2__asm__(\"x2\")=PRIV_REG_OPTION_RMW;"
                    "registeruint64_tx3__asm__(\"x3\")=ISO_MASK;registeruint64_tx4__asm__(\"x4\")=%sUL;registeruint64_tx5__asm__(\"x5\")=0;registeruint64_tx6__asm__(\"x6\")=0;"
                    "registeruint64_tx7__asm__(\"x7\")=0;")


def check_wlansmc_source_text(src: str, name: str = "wlansmc.c") -> None:
    """Two views of the same file: without the WLAN_ISO_RUN blocks (the W2a image: ONE smc, READ only) and the WLAN_ISO_RUN blocks themselves (W2c: exactly two more, RMW set / clear)."""
    iso_parts = _ISO_BLOCK_RX.findall(src)
    plain = _ISO_BLOCK_RX.sub("", src)
    _check_wlansmc_plain(plain, name)
    _check_wlansmc_iso("\n".join(iso_parts), re.sub(r"#ifdef WLAN_ISO_RUN\n|#endif\t/\* WLAN_ISO_RUN \*/\n", "", src), name)


def _check_wlansmc_plain(src: str, name: str) -> None:
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    for bad in ("try_write32", "w32", "hvc", "wait_ticks", "counter_freq", "smc_priv_rmw_set", "smc_priv_rmw_clear", "wlan_host_smc5"):
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned in the W2a (READ only) part")
    if len(re.findall(r"\b__asm__\s+volatile\b", code)) != 1 or len(re.findall(r"\b__asm__\s*\(\s*\"x[0-3]\"\s*\)", code)) != 4 or len(re.findall(r"\b__asm__\b", code)) != 5 or \
       code.count("smc #0") != 1 or len(re.findall(r"\bsmc\s*#", code)) != 1:
        raise AssertionError(f"{name}: exactly one inline asm, exactly one `smc #0`")
    if re.search(r"\bvolatile\b", re.sub(r'__asm__ volatile\("smc #0"', "", code)):
        raise AssertionError(f"{name}: 'volatile' only as the asm qualifier of the one smc")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", code):
        raise AssertionError(f"{name}: explicit pointer cast is banned")
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;|\bwhile\s*\((?!\*s)", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    for m in re.finditer(r"\bwhile\b", code):
        a, b = _c_function_span(code, "put_txt")
        if not a <= m.start() < b:
            raise AssertionError(f"{name}: 'while' outside put_txt (no retry loops)")
    if "#defineSMC_FID_PRIV_REG0x82000504UL" not in norm or "#definePRIV_REG_OPTION_READ0UL" not in norm:
        raise AssertionError(f"{name}: the function id must be 0x82000504 and the option constant READ = 0 (exynos-smc.h:45, exynos-el3_mon.h:21)")
    a, b = _c_function_span(code, "smc_priv_read")
    body = re.sub(r"\s+", "", code[a:b])
    guard = "if(pa!=WLAN_PMU_PCIE1_ISO&&pa!=WLAN_PMU_PCIE0_ISO)return-1;"
    for need in (guard, "registeruint64_tx0__asm__(\"x0\")=SMC_FID_PRIV_REG;", "registeruint64_tx1__asm__(\"x1\")=pa;", "registeruint64_tx2__asm__(\"x2\")=PRIV_REG_OPTION_READ;",
                 "registeruint64_tx3__asm__(\"x3\")=0;", "wlan_host_smc(SMC_FID_PRIV_REG,pa,PRIV_REG_OPTION_READ,0,r);"):
        if need not in body:
            raise AssertionError(f"{name}: smc_priv_read lost '{need}'")
    if body.find(guard) > body.find("__asm__volatile"):
        raise AssertionError(f"{name}: the address guard must come before the smc")
    if "__attribute__((noipa))staticintsmc_priv_read(uint64_tpa,structsmc_res*r)" not in norm:
        raise AssertionError(f"{name}: smc_priv_read must be noipa with the signature (uint64_t pa, struct smc_res *r): no parameter can carry the function id or the option")
    sites = re.findall(r"\bsmc_priv_read\s*\(([^;]*)\)", code)
    if sorted(re.sub(r"\s+", "", x) for x in sites if "uint64_t" not in x) != ["pa,&r"]:
        raise AssertionError(f"{name}: smc_priv_read has exactly one call site in the W2a part, with the caller's pa: {sites}")
    if len(re.findall(r"\bone\s*\(", code)) != 3 or "r1=one(WLAN_PMU_PCIE1_ISO,&m1,&amb1);" not in norm or "r0=one(WLAN_PMU_PCIE0_ISO,&m0,&amb0);" not in norm:
        raise AssertionError(f"{name}: exactly two addresses are read: one(WLAN_PMU_PCIE1_ISO, ..) and one(WLAN_PMU_PCIE0_ISO, ..)")


def _check_wlansmc_iso(iso_src: str, full_src: str, name: str) -> None:
    code = _strip_c_comments(iso_src)
    norm = re.sub(r"\s+", "", code)
    fcode = _strip_c_comments(full_src)
    if not code.strip():
        raise AssertionError(f"{name}: the WLAN_ISO_RUN block is missing")
    if re.search(r"\b(try_write32|w32|hvc)\b", fcode):
        raise AssertionError(f"{name}: try_write32 / w32 / hvc are banned")
    if len(re.findall(r"\b__asm__\s+volatile\b", code)) != 2 or code.count("smc #0") != 2 or len(re.findall(r"\b__asm__\s*\(\s*\"x[0-7]\"\s*\)", code)) != 16:
        raise AssertionError(f"{name}: the W2c block has exactly two smc instructions (rmw set, rmw clear) and nothing else in asm")
    if re.search(r"\(\s*(?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*+\s*\)", fcode):
        raise AssertionError(f"{name}: explicit pointer cast is banned")
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", fcode):
        raise AssertionError(f"{name}: unbounded loop construct")
    for need in ("#definePRIV_REG_OPTION_RMW2UL", "#defineISO_REGWLAN_PMU_PCIE1_ISO", "#defineISO_MASK1UL", "#defineISO_POLLS10u", "#defineISO_POLL_DIV10000u", "#defineISO_FINAL_DIV100u"):
        if need not in norm:
            raise AssertionError(f"{name}: lost '{need}' (RMW option 2, register 0x17463EC4, mask 1, 10 polls of 100 us)")
    for fn, val in (("smc_priv_rmw_set", "1"), ("smc_priv_rmw_clear", "0")):
        a, b = _c_function_span(code, fn)
        body = re.sub(r"\s+", "", code[a:b])
        if (_WLANSMC_RMW_SET % val) not in body or f"wlan_host_smc5(SMC_FID_PRIV_REG,ISO_REG,PRIV_REG_OPTION_RMW,ISO_MASK,{val}UL,r);" not in body:
            raise AssertionError(f"{name}: {fn} must load exactly fid / ISO_REG / RMW / ISO_MASK / {val}UL")
        if f"__attribute__((noipa))staticvoid{fn}(structsmc_res*r)" not in norm:
            raise AssertionError(f"{name}: {fn} must be noipa with the only parameter `struct smc_res *r`")
    for fn, n in (("smc_priv_rmw_set", 1), ("smc_priv_rmw_clear", 1)):
        if len(re.findall(r"\b" + fn + r"\s*\(", fcode)) != 2:	# the definition and exactly one call
            raise AssertionError(f"{name}: {fn} has exactly one call site")
    ra, rb = _c_function_span(code, "wlan_iso_run")
    run = re.sub(r"\s+", "", code[ra:rb])
    s_at, c_at = run.find("smc_priv_rmw_set(&rs);"), run.find("smc_priv_rmw_clear(&rc);")
    if s_at < 0 or c_at < s_at:
        raise AssertionError(f"{name}: wlan_iso_run = (preconditions) set, then (conditionally) clear")
    pre = run[:s_at]
    for need in ("if(!counter_freq()){", "if(!try_read32(ISO_REG,&before)||!try_read32(WLAN_PMU_PCIE0_ISO,&iso0)||!try_read32(WLAN_GPH2+4u,&perst)||!try_read32(WLAN_GPP14+4u,&en)){",
                 "if(before&1u){", "if(perst&(1u<<(unsigned)WLAN_GPH2_PERST_PIN)){", "if(en&(1u<<(unsigned)WLAN_GPP14_EN_PIN)){", "if(smc_priv_read(ISO_REG,&rd)||(int32_t)(uint32_t)rd.a0<0){", "if((uint32_t)rd.a0!=before){"):
        if need not in pre:
            raise AssertionError(f"{name}: precondition '{need}' must come before the RMW set")
    if pre.count("return2;") < 7:
        raise AssertionError(f"{name}: every precondition refusal must return before the RMW set")
    if run.count("smc_priv_rmw_set(") != 1 or run.count("smc_priv_rmw_clear(") != 1:
        raise AssertionError(f"{name}: exactly one set and one clear in wlan_iso_run (never a second set)")
    if 'rel="mismatch";smc_priv_rmw_clear(&rc);' not in run:
        raise AssertionError(f"{name}: the ONE clear follows every sent set unconditionally (review 2 F1): nothing but the classification may sit between it and the set")
    if "wait_ticks(counter_freq()/ISO_FINAL_DIV);fin_ok=try_read32(ISO_REG,&fin);" not in run or 'rest=(rok2&&!(rb&1u)&&fin_ok&&!(fin&1u))?"ok":"FAILED";' not in run:
        raise AssertionError(f"{name}: the restore verdict needs the poll read-back AND a delayed final read, both bit 0 == 0")
    if "rel_ok=rok&&reached&&ret==0&&((after^before)&~1u)==0u&&iso0b==iso0;" not in run:
        raise AssertionError(f"{name}: the release is `ok` only with the bit set, a zero return, every other bit unchanged and ISO0 unchanged")
    if "reached=poll_bit(1u,&after,&rok);" not in run or "(void)poll_bit(0u,&rb,&rok2);" not in run:
        raise AssertionError(f"{name}: the read-backs go through the bounded poll_bit")
    pa_, pb_ = _c_function_span(code, "poll_bit")
    pbody = re.sub(r"\s+", "", code[pa_:pb_])
    if "for(unsignedi=0;i<ISO_POLLS;i++){" not in pbody or re.search(r"(?<![A-Za-z_0-9])i(?:[-+*/%&|^]|<<|>>)?=(?!=)", pbody.replace("for(unsignedi=0;i<ISO_POLLS;i++){", "")):
        raise AssertionError(f"{name}: poll_bit is one ISO_POLLS-bounded loop that does not touch its counter")
    fa_, fb_ = _c_function_span(fcode, "poll_bit")
    for m in re.finditer(r"\b(while|for)\b", fcode):
        if not any(a <= m.start() < b for a, b in [(fa_, fb_), _c_function_span(fcode, "put_hex"), _c_function_span(fcode, "put_txt")]):
            raise AssertionError(f"{name}: loop outside poll_bit / put_hex / put_txt")
    if code.count("!!! RESTORE FAILED") != 1 or "if(rest[0]=='F'){" not in run:
        raise AssertionError(f"{name}: a failed restore must be reported loudly")


def wlansmc_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "wlansmc.c").read_text()
    check_wlansmc_source_text(good)  # control must pass
    cases = [
        ("another function id", good.replace("#define SMC_FID_PRIV_REG\t0x82000504UL", "#define SMC_FID_PRIV_REG\t0x82000505UL", 1)),
        ("option WRITE", good.replace("#define PRIV_REG_OPTION_READ\t0UL", "#define PRIV_REG_OPTION_READ\t1UL", 1)),
        ("option RMW for the read", good.replace("#define PRIV_REG_OPTION_READ\t0UL", "#define PRIV_REG_OPTION_READ\t2UL", 1)),
        ("x2 carries a variable", good.replace('register uint64_t x2 __asm__("x2") = PRIV_REG_OPTION_READ;', 'register uint64_t x2 __asm__("x2") = r->a2;', 1)),
        ("x3 carries a value", good.replace('register uint64_t x3 __asm__("x3") = 0;', 'register uint64_t x3 __asm__("x3") = pa;', 1)),
        ("no address guard", good.replace("if (pa != WLAN_PMU_PCIE1_ISO && pa != WLAN_PMU_PCIE0_ISO)", "if (0)", 1)),
        ("a third address", good.replace("pa != WLAN_PMU_PCIE0_ISO)", "pa != WLAN_PMU_PCIE0_ISO && pa != WLAN_PMU_PCIE0_ISO + 8)", 1)),
        ("a second smc in the W2a part", good.replace("static unsigned put_txt", '__attribute__((noipa)) static void evil(void) { __asm__ volatile("smc #0" ::: "memory"); }\n__attribute__((noipa)) static unsigned put_txt', 1)),
        ("hvc", good.replace('"smc #0"', '"hvc #0"', 1)),
        ("a raw writer", good.replace("static void emit(", "static void evil(void) { try_write32(0x17463EC4UL, 1); }\nstatic void emit(", 1)),
        ("a third address read", good.replace("\tr0 = one(WLAN_PMU_PCIE0_ISO, &m0, &amb0);", "\tr0 = one(WLAN_PMU_PCIE0_ISO, &m0, &amb0);\n\t(void)one(WLAN_PMU_PCIE1_ISO + 4, &m0, &amb0);", 1)),
        ("a retry loop", good.replace("\trc = smc_priv_read(pa, &r);", "\trc = smc_priv_read(pa, &r);\n\twhile (rc)\n\t\trc = smc_priv_read(pa, &r);", 1)),
        # W2c part
        ("rmw: option WRITE", good.replace("#define PRIV_REG_OPTION_RMW\t2UL", "#define PRIV_REG_OPTION_RMW\t1UL", 1)),
        ("rmw: mask widened", good.replace("#define ISO_MASK\t\t1UL", "#define ISO_MASK\t\t3UL", 1)),
        ("rmw: another register", good.replace("#define ISO_REG\t\t\tWLAN_PMU_PCIE1_ISO", "#define ISO_REG\t\t\tWLAN_PMU_PCIE0_ISO", 1)),
        ("rmw set writes 3", good.replace('register uint64_t x4 __asm__("x4") = 1UL;', 'register uint64_t x4 __asm__("x4") = 3UL;', 1)),
        ("rmw clear writes 1", good.replace('register uint64_t x4 __asm__("x4") = 0UL;', 'register uint64_t x4 __asm__("x4") = 1UL;', 1)),
        ("rmw set takes a value parameter", good.replace("static void smc_priv_rmw_set(struct smc_res *r)", "static void smc_priv_rmw_set(struct smc_res *r, uint64_t v)", 1)),
        ("a third smc", good.replace("static int poll_bit(", '__attribute__((noipa)) static void evil2(void) { __asm__ volatile("smc #0" ::: "memory"); }\nstatic int poll_bit(', 1)),
        ("a second set", good.replace("\tsmc_priv_rmw_set(&rs);\n", "\tsmc_priv_rmw_set(&rs);\n\tsmc_priv_rmw_set(&rs);\n", 1)),
        ("no precondition: bit already set", good.replace("\tif (before & 1u) {", "\tif (0) {", 1)),
        ("no precondition: PERST", good.replace("\tif (perst & (1u << (unsigned)WLAN_GPH2_PERST_PIN)) {", "\tif (0) {", 1)),
        ("clear only when the bit reads set (F1)", good.replace("\tsmc_priv_rmw_clear(&rc);\n\tiso_ret(", "\tif (after & 1u)\n\t\tsmc_priv_rmw_clear(&rc);\n\tiso_ret(", 1)),
        ("no delayed final read", good.replace("\trest = (rok2 && !(rb & 1u) && fin_ok && !(fin & 1u)) ? \"ok\" : \"FAILED\";", "\trest = (rok2 && !(rb & 1u)) ? \"ok\" : \"FAILED\";", 1)),
        ("no SMC READ == MMIO precondition (F3)", good.replace("\tif ((uint32_t)rd.a0 != before) {", "\tif (0) {", 1)),
        ("rmw: x5 not zeroed (F7)", good.replace('register uint64_t x5 __asm__("x5") = 0;', 'register uint64_t x5 __asm__("x5") = pa_junk;', 1)),
        ("option as a parameter (F4)", good.replace("static int smc_priv_read(uint64_t pa, struct smc_res *r)", "static int smc_priv_read(uint64_t pa, struct smc_res *r, uint64_t op)", 1)),
        ("no precondition: SMC READ", good.replace("\tif (smc_priv_read(ISO_REG, &rd) || (int32_t)(uint32_t)rd.a0 < 0) {", "\tif (0) {", 1)),
        ("other bits not compared", good.replace(" && ((after ^ before) & ~1u) == 0u && iso0b == iso0;", ";", 1)),
        ("silent restore failure", good.replace("!!! RESTORE FAILED", "restore problem")),
        ("unbounded poll", good.replace("for (unsigned i = 0; i < ISO_POLLS; i++) {", "for (unsigned i = 0; ; i++) {", 1)),
        ("longer poll", good.replace("#define ISO_POLLS\t\t10u", "#define ISO_POLLS\t\t1000u", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"wlansmc source selftest case did not mutate: {label}"
        try:
            check_wlansmc_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlansmc source {label} -> {exc}")
        else:
            raise AssertionError(f"wlansmc source self-test FAILED to catch: {label}")
    print(f"PASS wlansmc source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


_INSN_RX = re.compile(r"^\s*[0-9a-f]+:\t[0-9a-f]+ \t(\S+)\s*(.*?)(?:\s*//.*)?$")


def _smc_writers(ins, k, reg):
    out = []
    for mn, ops in reversed(ins[:k]):
        if mn in _WLANEN_DEST_FREE or mn.startswith("b.") or mn in ("cbz", "cbnz"):
            continue
        if re.match(reg + r"\b", ops) or re.match("w" + reg[1:] + r"\b", ops):
            out.append((mn, ops))
    return out


def _reg_const(ins, k, reg, depth=0):
    """The constant held by `reg` just before instruction k, following plain register copies; None if it is not a constant."""
    w = _smc_writers(ins, k, reg)[:1]
    if not w or depth > 4:
        return None
    mn, ops = w[0]
    m = re.fullmatch(reg + r", #(0x[0-9a-f]+|\d+)", ops)
    if mn == "mov" and m:
        return int(m.group(1), 0)
    if mn == "mov" and ops == reg + ", xzr":
        return 0
    m = re.fullmatch(reg + r", (x\d+)", ops) if mn == "mov" else None
    if m:
        cp = max(i for i, x in enumerate(ins[:k]) if x == w[0])
        return _reg_const(ins, cp, m.group(1), depth + 1)
    return None


def check_wlansmc_object_text(disasm: str, undefined: set[str], local_text: set[str], sections: str, name: str = "wlansmc.o", iso: bool = False) -> int:
    """The compiled object. W2a image (iso=False): exactly one `smc`, in smc_priv_read, with x0 = 0x82000504 (mov #0x504 + movk #0x8200 lsl 16), x2 = 0 (READ), x3 = 0 as the LAST writes before it, an address
    compare against exactly 0x17463EC0 / 0x17463EC4 with a refusal path that skips it. W2c image (iso=True): additionally exactly two more smc, one in smc_priv_rmw_set and one in smc_priv_rmw_clear, whose x0..x4 are
    the constants 0x82000504 / 0x17463EC4 / 2 (RMW) / 1 (mask) / 1 resp. 0 (value). No other banned instruction; stores stack/argument based; undef subset. Returns the store count."""
    blocks = re.split(r"(?m)^(?=[0-9a-f]+ <[^>]+>:$)", disasm)
    smc_in = [re.match(r"[0-9a-f]+ <(\w+)>:", b.splitlines()[0]).group(1) for b in blocks if b.strip() and re.match(r"[0-9a-f]+ <(\w+)>:", b.splitlines()[0])
              for l in b.splitlines() if _INSN_RX.match(l) and _INSN_RX.match(l).group(1) == "smc"]
    want = ["smc_priv_read"] + (["smc_priv_rmw_clear", "smc_priv_rmw_set"] if iso else [])
    if sorted(smc_in) != sorted(want):
        raise AssertionError(f"{name}: smc instructions expected exactly in {sorted(want)}; found {sorted(smc_in)}")

    def block_of(fn):
        return next(b for b in blocks if b.strip() and re.match(r"[0-9a-f]+ <" + fn + r">:", b.splitlines()[0]))

    def need(ins, k, reg, expect, what):
        w = _smc_writers(ins, k, reg)
        if len(w) < len(expect) or [x for x in w[:len(expect)]] not in (expect if isinstance(expect[0], list) else [expect]):
            raise AssertionError(f"{name}: {what}: {reg} at the smc must be built by {expect}; found {w[:len(expect)]}")

    for fn in want:
        ins = [(_INSN_RX.match(l).group(1), _INSN_RX.match(l).group(2)) for l in block_of(fn).splitlines() if _INSN_RX.match(l)]
        k = next(i for i, x in enumerate(ins) if x[0] == "smc")
        if ins[k] != ("smc", "#0x0"):
            raise AssertionError(f"{name}: the smc in {fn} must be `smc #0`: {ins[k]}")
        # x0 = 0x82000504 in every function
        need(ins, k, "x0", [("movk", "x0, #0x8200, lsl #16"), ("mov", "x0, #0x504")], fn)
        zero = [[("mov", "x%d, #0x0" % n)] for n in (2, 3)]
        if fn == "smc_priv_read":
            need(ins, k, "x2", [("mov", "x2, #0x0")], fn) if _smc_writers(ins, k, "x2")[:1] == [("mov", "x2, #0x0")] else need(ins, k, "x2", [("mov", "x2, xzr")], fn)
            need(ins, k, "x3", [("mov", "x3, #0x0")], fn) if _smc_writers(ins, k, "x3")[:1] == [("mov", "x3, #0x0")] else need(ins, k, "x3", [("mov", "x3, xzr")], fn)
            text = "\n".join(f"{m} {o}" for m, o in ins[:k])
            if not re.search(r"movk\s+x\d+, #0x1746, lsl #16", text) or not re.search(r"mov\s+x\d+, #0x3ec0\b", text):
                raise AssertionError(f"{name}: the address guard must compare against 0x17463EC0")
            masks = re.findall(r"and\s+x\d+, x\d+, #(0x[0-9a-f]+)", text)
            if masks:
                if masks != ["0xfffffffffffffffb"]:
                    raise AssertionError(f"{name}: the address guard mask must be ~4 (accepts exactly 0x17463EC0 and 0x17463EC4): {masks}")
            elif not re.search(r"mov\s+x\d+, #0x3ec4\b", text) and not re.search(r"add\s+x\d+, x\d+, #0x4\b", text):
                raise AssertionError(f"{name}: the guard must also accept 0x17463EC4")
            if not re.search(r"\bcmp\s+x\d+, x\d+", text) or not any(m.startswith("b.") or m in ("cbz", "cbnz") for m, _ in ins[:k]):
                raise AssertionError(f"{name}: no compare / conditional branch before the smc")
            if not any(m == "mov" and o == "w0, #0xffffffff" for m, o in ins[k:]):
                raise AssertionError(f"{name}: no refusal return (-1) in smc_priv_read")
        else:
            need(ins, k, "x1", [("movk", "x1, #0x1746, lsl #16"), ("mov", "x1, #0x3ec4")], fn)
            need(ins, k, "x2", [("mov", "x2, #0x2")], fn)
            need(ins, k, "x3", [("mov", "x3, #0x1")], fn)
            want4 = "1" if fn == "smc_priv_rmw_set" else "0"
            w4 = _smc_writers(ins, k, "x4")[:1]
            if w4 and w4[0][0] == "mov" and re.fullmatch(r"x4, x(\d+)", w4[0][1]):	# the compiler may copy the constant from x3 / x2 (= 1 / 2): follow the copy
                src = "x" + re.fullmatch(r"x4, x(\d+)", w4[0][1]).group(1)
                cp = max(i for i, x in enumerate(ins[:k]) if x == w4[0])
                ws = _smc_writers(ins, cp, src)[:1]
                w4 = [("mov", "x4, " + ws[0][1].split(", ")[1])] if ws and ws[0][0] == "mov" and ws[0][1].startswith(src + ", #") else w4
            okv = [("mov", "x4, #0x1")] if want4 == "1" else [("mov", "x4, #0x0"), ("mov", "x4, xzr")]
            if not w4 or w4[0] not in okv:
                raise AssertionError(f"{name}: x4 at the smc in {fn} must be the constant {want4}: {w4}")
            for n in (5, 6, 7):	# Linux passes a5..a7 = 0 (review F7)
                if _reg_const(ins, k, "x%d" % n) != 0:
                    raise AssertionError(f"{name}: x{n} at the smc in {fn} must be the constant 0 (Linux: arm_smccc_smc(.., 0, 0, 0))")
            if any(m in ("cmp", "cbz", "cbnz") or m.startswith("b.") for m, _ in ins[:k]):
                raise AssertionError(f"{name}: {fn} must be straight-line constants (no compare / branch before the smc)")
    # everything else: the generic read-only/stack store audit on a copy with the smc instructions turned into nops (the generic audit bans smc)
    plain = re.sub(r"(?m)^(\s*[0-9a-f]+:\t[0-9a-f]+ \t)smc\t#0x0$", r"\1nop", disasm)
    return check_usb_object_text(plain, local_text, undefined, sections, name, allowed_undef=_WLANSMC_ALLOWED_UNDEF | ({"wait_ticks", "counter_freq"} if iso else set()),
                                 trusted_args=frozenset(f"x{i}" for i in range(8)), allow_adrp=False, require_clean_data=True)


def check_wlansmc_object(obj_path: pathlib.Path, iso: bool = False) -> int:
    assert obj_path.is_file(), f"{obj_path} not found (run shim/build.sh first)"
    disasm, undefined, local_text, sections = _touch_obj_info(obj_path)
    return check_wlansmc_object_text(disasm, undefined, local_text, sections, str(obj_path), iso)


def wlansmc_object_selftest(obj_path: pathlib.Path, iso: bool = False) -> int:
    disasm, undefined, local_text, sections = _touch_obj_info(obj_path)
    check_wlansmc_object_text(disasm, undefined, local_text, sections, "wlansmc selftest", iso)  # control
    cases = [
        ("another function id", re.sub(r"(mov\s+x0, )#0x504", r"\1#0x505", disasm, count=1), undefined),
        ("function id high half", re.sub(r"(movk\s+x0, )#0x8200", r"\1#0x8201", disasm, count=1), undefined),
        ("option WRITE", re.sub(r"(smc_priv_read>:(?:.*\n)*?\s+[0-9a-f]+:\t[0-9a-f]+ \tmov\tx2, )#0x0", r"\1#0x1", disasm, count=1), undefined),
        ("x3 nonzero", re.sub(r"(smc_priv_read>:(?:.*\n)*?\s+[0-9a-f]+:\t[0-9a-f]+ \tmov\tx3, )#0x0", r"\1#0x1", disasm, count=1), undefined),
        ("guard mask widened", re.sub(r"#0xfffffffffffffffb", "#0xfffffffffffffff8", disasm, count=1), undefined),
        ("guard removed", re.sub(r"(?m)^(\s*[0-9a-f]+:\t[0-9a-f]+ \t)b\.ne\t[^\n]*$", r"\1nop", disasm, count=1), undefined),
        ("a second smc", disasm + "0000000000fff000 <evil>:\n fff000:\td4000003 \tsmc\t#0x0\n fff004:\td65f03c0 \tret\n", undefined),
        ("hvc", disasm.replace("smc\t#0x0", "hvc\t#0x0", 1), undefined),
        ("undefined symbol try_write32", disasm, undefined | {"try_write32"}),
    ]
    if iso:
        def fnre(fn, ins, new):
            return re.sub(r"(" + fn + r">:(?:.*\n)*?\s+[0-9a-f]+:\t[0-9a-f]+ \t)" + ins, r"\1" + new, disasm, count=1)
        cases += [
            ("rmw: option WRITE", fnre("smc_priv_rmw_set", r"mov\tx2, #0x2", "mov\tx2, #0x1"), undefined),
            ("rmw: mask widened", fnre("smc_priv_rmw_set", r"mov\tx3, #0x1", "mov\tx3, #0x3"), undefined),
            ("rmw: another register", fnre("smc_priv_rmw_set", r"mov\tx1, #0x3ec4", "mov\tx1, #0x3ec8"), undefined),
            ("rmw set: value 2 (copied from x2)", fnre("smc_priv_rmw_set", r"mov\tx4, x3", "mov\tx4, x2"), undefined),
            ("rmw clear: value 1", fnre("smc_priv_rmw_clear", r"mov\tx4, #0x0", "mov\tx4, #0x1"), undefined),
            ("rmw specialised/parametrised: register from an argument", fnre("smc_priv_rmw_set", r"mov\tx1, #0x3ec4", "mov\tx1, x19"), undefined),
            ("rmw: value from an argument", fnre("smc_priv_rmw_clear", r"mov\tx4, #0x0", "mov\tx4, x20"), undefined),
            ("rmw: a guard compare added (helper turned into a conditional one)", fnre("smc_priv_rmw_set", r"mov\tx3, #0x1", "cmp\tx0, x3"), undefined),
            ("rmw: x5 not zeroed", fnre("smc_priv_rmw_set", r"mov\tx5, #0x0", "mov\tx5, #0x1"), undefined),
            ("rmw: x7 left as garbage", fnre("smc_priv_rmw_clear", r"mov\tx7, #0x0", "mov\tx7, x19"), undefined),
            ("rmw clear function absent", disasm.replace("<smc_priv_rmw_clear>:", "<smc_other>:", 1), undefined),
        ]
    caught = 0
    for label, bad, und in cases:
        assert bad != disasm or und != undefined, f"wlansmc object selftest case did not mutate: {label}"
        try:
            check_wlansmc_object_text(bad, und, local_text, sections, "wlansmc selftest", iso)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlansmc{'_iso' if iso else ''} object {label} -> {exc}")
        else:
            raise AssertionError(f"wlansmc object self-test FAILED to catch: {label}")
    print(f"PASS wlansmc{'_iso' if iso else ''} object self-test: {caught}/{len(cases)} bad objects rejected")
    return caught


def check_shim_wlan_sequence(src: str, name: str = "shim.c") -> None:
    """shim.c calls wlan_rails(), wlan_smc_read() and wlan_en_run() exactly once each, inside their own flag, in the WLAN section (after wlan_probe_l1, before the level-2 gate), in that order."""
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    pos = []
    for fn, flag in (("wlan_rails", "WLAN_RAILS"), ("wlan_smc_read", "WLAN_SMC_READ"), ("wlan_iso_run", "WLAN_ISO_RUN"), ("wlan_en_run", "WLAN_EN_RUN")):
        if len(re.findall(r"\b" + fn + r"\s*\(", code)) != 1 or f"#ifdef{flag}" not in norm or f"(void){fn}();" not in norm:
            raise AssertionError(f"{name}: {fn}() must be called exactly once, as `(void){fn}();` inside #ifdef {flag}")
        i = norm.find(f"(void){fn}();")
        if not norm.rfind(f"#ifdef{flag}", 0, i) > norm.find("wlan_probe_l1();") or not i < norm.find("wlan_probe_l2_open()"):
            raise AssertionError(f"{name}: {fn}() must sit in its own #ifdef {flag} block after wlan_probe_l1() and before the level-2 gate")
        pos.append(i)
    if not re.search(r"#if defined\(WLAN_ISO_RUN\) && defined\(WLAN_EN_RUN\)\n#error", src):
        raise AssertionError(f"{name}: --wlan-iso and --wlan-en must be impossible to combine (#error in shim.c)")
    m = re.search(r"#ifdef WLAN_ISO_RUN\n(?:(?!#endif).)*?l2 skipped(?:(?!#endif).)*?#else\n(.*?)#endif\t/\* !WLAN_ISO_RUN \*/", src, re.S)
    if not m or "wlan_probe_l2_open()" not in m.group(1) or "wlan_probe_l2_group(4);" not in m.group(1) or len(re.findall(r"wlan_probe_l2_(?:open|group)\(", code)) != 3:
        raise AssertionError(f"{name}: in a WLAN_ISO_RUN image every level-2 read (wlan_probe_l2_open / _group, SUMMARY included) must sit in the #else of `#ifdef WLAN_ISO_RUN` (review 2 F2)")
    if pos != sorted(pos):
        raise AssertionError(f"{name}: order must be wlan_rails (read-only), wlan_smc_read (EL3 reads), wlan_iso_run (EL3 RMW), wlan_en_run (GPIO writer)")


def wlan_shim_selftest() -> int:
    shim_src = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "shim.c").read_text()
    check_shim_wlan_sequence(shim_src)
    cases = [
        ("a second wlan_en_run call", shim_src.replace("\t(void)wlan_en_run();\n", "\t(void)wlan_en_run();\n\t(void)wlan_en_run();\n", 1)),
        ("wlan_en_run outside its flag", shim_src.replace("#ifdef WLAN_EN_RUN\n", "#if 1\n", 1)),
        ("writer before the rails", shim_src.replace("(void)wlan_rails();", "(void)0;", 1).replace("(void)wlan_en_run();", "(void)wlan_en_run();\n\t(void)wlan_rails();", 1)),
        ("a second wlan_smc_read call", shim_src.replace("\t(void)wlan_smc_read();\n", "\t(void)wlan_smc_read();\n\t(void)wlan_smc_read();\n", 1)),
        ("wlan_smc_read outside its flag", shim_src.replace("#ifdef WLAN_SMC_READ\n", "#if 1\n", 1)),
        ("level 2 not skipped in an iso image", shim_src.replace("#ifdef WLAN_ISO_RUN\n\t/* review 2 F2", "#if 0\n\t/* review 2 F2", 1)),
        ("iso and en combinable", shim_src.replace("#if defined(WLAN_ISO_RUN) && defined(WLAN_EN_RUN)\n#error", "#if 0\n#error", 1)),
        ("level-2 SUMMARY outside the skip", shim_src.replace("\twlan_probe_l2_group(4);\t\t\t/* SUMMARY, also when gated */\n#endif\t/* !WLAN_ISO_RUN */", "#endif\t/* !WLAN_ISO_RUN */\n\twlan_probe_l2_group(4);", 1)),
        ("a second wlan_iso_run call", shim_src.replace("\t(void)wlan_iso_run();\n", "\t(void)wlan_iso_run();\n\t(void)wlan_iso_run();\n", 1)),
        ("wlan_iso_run outside its flag", shim_src.replace("#ifdef WLAN_ISO_RUN\n", "#if 1\n", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != shim_src, f"wlan shim selftest case did not mutate: {label}"
        try:
            check_shim_wlan_sequence(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlan shim {label} -> {exc}")
        else:
            raise AssertionError(f"wlan shim self-test FAILED to catch: {label}")
    print(f"PASS wlan shim sequence self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


# ---- gpujob.c (--gpu-job): the G2 stage-2 writer. Strict writer audit (w32 -> w32_allowed first, no asm / volatile / pointer casts) plus pins on WHAT may be written ----
_GPUJOB_KW = dict(base_macro=None, strict=True, guard_funcs=("w32_allowed",))
_GPUJOB_ALLOWED_UNDEF = {"try_read32", "try_write32", "con_str", "counter_freq", "touch_ticks", "gpu_job_scratch", "gpu_job_cache_flush", "gpu_s2mpu_dump"}
_GPUJOB_REGS = {   # every A_* define of gpujob.c: GPU_CONTROL / JOB_CONTROL / MMU offsets from GPU_MALI (regmap_jm_enums.h; docs/research/28 section 7)
    "A_JOB_RAW": "0x1000u", "A_JOB_CLEAR": "0x1004u", "A_JS_STATUS": "0x1824u", "A_JS_CMD": "0x1820u", "A_JS_HEAD_LO": "0x1800u",
    "A_JS_HEAD_NEXT_LO": "0x1840u", "A_JS_HEAD_NEXT_HI": "0x1844u", "A_JS_AFF_NEXT_LO": "0x1850u", "A_JS_AFF_NEXT_HI": "0x1854u",
    "A_JS_CFG_NEXT": "0x1858u", "A_JS_CMD_NEXT": "0x1860u", "A_MMU_RAW": "0x2000u", "A_MMU_CLEAR": "0x2004u",
    "A_AS_TRANSTAB_LO": "0x2400u", "A_AS_TRANSTAB_HI": "0x2404u", "A_AS_MEMATTR_LO": "0x2408u", "A_AS_MEMATTR_HI": "0x240cu",
    "A_JS_PRESENT": "0x001cu", "A_JS0_FEAT": "0x00c0u", "A_JS1_FEAT": "0x00c4u", "A_JS2_FEAT": "0x00c8u",
    "A_AS_COMMAND": "0x2418u", "A_AS_FAULTSTATUS": "0x241cu", "A_AS_FAULTADDR_LO": "0x2420u", "A_AS_STATUS": "0x2428u",
    "A_AS_TRANSCFG_LO": "0x2430u", "A_AS_TRANSCFG_HI": "0x2434u", "A_GPU_FAULT": "0x003cu", "A_SHADER_READY": "0x0140u",
}
_GPUJOB_ALLOW = ("if(pa>=j->base&&pa<j->base+NPAGES*PG)return!(pa&3u);"
                 "if(pa==A_AS_TRANSCFG_LO)returnv==GPU_JOB_TRANSCFG_ON||v==GPU_JOB_TRANSCFG_OFF;"
                 "if(pa==A_AS_TRANSCFG_HI)returnv==0;"
                 "if(pa==A_AS_MEMATTR_LO)returnv==GPU_JOB_MEMATTR_LO;"
                 "if(pa==A_AS_MEMATTR_HI)returnv==GPU_JOB_MEMATTR_HI;"
                 "if(pa==A_AS_TRANSTAB_LO)returnv==j->l0_lo||v==0;"
                 "if(pa==A_AS_TRANSTAB_HI)returnv==j->l0_hi||v==0;"
                 "if(pa==A_AS_COMMAND)returnv==AS_CMD_UPDATE;"
                 "if(pa==A_JS_HEAD_NEXT_LO)returnv==GPU_JOB_JOB_VA;"
                 "if(pa==A_JS_HEAD_NEXT_HI||pa==A_JS_AFF_NEXT_HI)returnv==0;"
                 "if(pa==A_JS_AFF_NEXT_LO)returnv==GPU_SHADER_PRESENT;"
                 "if(pa==A_JS_CFG_NEXT)returnv==GPU_JOB_JS_CONFIG;"
                 "if(pa==A_JS_CMD_NEXT)returnv==JS_CMD_START;"
                 "if(pa==A_JS_CMD)returnv==JS_CMD_HARDSTOP_1;"
                 "if(pa==A_JOB_CLEAR||pa==A_MMU_CLEAR)returnv==IRQ_SLOT0_BITS;return0;")
# the page-table builder is pinned TEXTUALLY: every next-level PA is derived from j->base inside entry(), so the GPU's translation can only lead into the five scratch pages
_GPUJOB_ENTRY = "{uint64_tpa=(uint64_t)(j->base+next);returnput64(j,at,(uint32_t)pa|flags,(uint32_t)(pa>>32));}"
_GPUJOB_BUILD = ("{inte=0;for(unsignedi=0;i<NPAGES*PG/4u&&!e;i++)e=w32(j->base+4u*i,0,j);if(e)returne;"
                 "e=entry(j,0x0000u,0x1000u,GPU_JOB_PTE_TABLE);if(!e)e=entry(j,0x1000u,0x2000u,GPU_JOB_PTE_TABLE);if(!e)e=entry(j,0x2000u,0x3000u,GPU_JOB_PTE_TABLE);"
                 "if(!e)e=entry(j,0x3000u+8u*1u,0x4000u,GPU_JOB_PTE_LEAF);if(!e)e=w32(j->base+0x4000u+0x10u,GPU_JOB_HDR_W4,j);"
                 "if(!e)e=put64(j,0x4000u+0x20u,GPU_JOB_TARGET_VA,0);if(!e)e=w32(j->base+0x4000u+0x28u,GPU_JOB_WV_IMM64,j);"
                 "if(!e)e=put64(j,0x4000u+0x30u,GPU_JOB_MAGIC_LO,GPU_JOB_MAGIC_HI);if(!e)e=put64(j,0x4000u+0x100u,0xa5a5a5a5u,0xa5a5a5a5u);returne;}")
_GPUJOB_W32_SITES = sorted([
    "w32(j->base+off,lo,j)", "w32(j->base+off+4u,hi,j)",						# put64
    "w32(j->base+4u*i,0,j)", "w32(j->base+0x4000u+0x10u,GPU_JOB_HDR_W4,j)", "w32(j->base+0x4000u+0x28u,GPU_JOB_WV_IMM64,j)",	# build
    "w32(A_AS_TRANSCFG_LO,transcfg,j)", "w32(A_AS_TRANSCFG_HI,0,j)", "w32(A_AS_TRANSTAB_LO,tablo,j)", "w32(A_AS_TRANSTAB_HI,tabhi,j)",
    "w32(A_AS_MEMATTR_LO,GPU_JOB_MEMATTR_LO,j)", "w32(A_AS_MEMATTR_HI,GPU_JOB_MEMATTR_HI,j)", "w32(A_AS_COMMAND,AS_CMD_UPDATE,j)",	# as_update
    "w32(A_JS_HEAD_NEXT_LO,GPU_JOB_JOB_VA,j)", "w32(A_JS_HEAD_NEXT_HI,0,j)", "w32(A_JS_AFF_NEXT_LO,GPU_SHADER_PRESENT,j)", "w32(A_JS_AFF_NEXT_HI,0,j)",
    "w32(A_JS_CFG_NEXT,GPU_JOB_JS_CONFIG,j)", "w32(A_JS_CMD_NEXT,JS_CMD_START,j)", "w32(A_JS_CMD,JS_CMD_HARDSTOP_1,j)",
    "w32(A_JOB_CLEAR,IRQ_SLOT0_BITS,j)", "w32(A_MMU_CLEAR,IRQ_SLOT0_BITS,j)",					# gpu_job_run
])


def check_gpujob_source_text(src: str, name: str = "gpujob.c") -> None:
    check_usbgadget_source_text(src, name, **_GPUJOB_KW)
    code = _strip_c_comments(src)
    regs = dict(re.findall(r"(?m)^#define\s+(A_\w+)\s+\(GPU_MALI \+ (0x[0-9a-f]+u)\)\s*(?:/\*.*\*/)?\s*$", src.replace("\t", " ")))
    regs = {k: v for k, v in regs.items()}
    if regs != _GPUJOB_REGS:
        raise AssertionError(f"{name}: register defines differ from the audited set: {sorted(set(regs.items()) ^ set(_GPUJOB_REGS.items()))}")
    for need, pat in (("PG", r"#define\s+PG\s+0x1000u\b"), ("NPAGES", r"#define\s+NPAGES\s+5u\b"), ("AS_CMD_UPDATE", r"#define\s+AS_CMD_UPDATE\s+1u\b"),
                      ("JS_CMD_START", r"#define\s+JS_CMD_START\s+1u\b"), ("JS_CMD_HARDSTOP_1", r"#define\s+JS_CMD_HARDSTOP_1\s+7u\b"),
                      ("IRQ_SLOT0_BITS", r"#define\s+IRQ_SLOT0_BITS\s+0x00010001u\b")):
        if not re.search(pat, code):
            raise AssertionError(f"{name}: {need} is not the audited constant")
    m = re.search(r"#define\s+J_POLLS\s+(\d+)u\b", code)
    if not m or int(m.group(1)) > 200000:
        raise AssertionError(f"{name}: J_POLLS must be a plain constant <= 200000")
    rest = re.sub(r"(?m)^#define\s+A_\w+.*$", "", code).replace("defined(GPU_MALI)", "")
    if "GPU_MALI" in rest:
        raise AssertionError(f"{name}: GPU_MALI used outside the audited A_* defines")
    a, b = _c_function_span(code, "w32_allowed")
    body = re.sub(r"\s+", "", code[a:b][code[a:b].index("{") + 1:-1])
    if body != _GPUJOB_ALLOW:
        raise AssertionError(f"{name}: w32_allowed is not the audited allowlist (scratch window + exact register/value pairs): {body}")
    sites = [re.sub(r"\s+", "", m.group(0)) for m in re.finditer(r"(?<![\w])w32\s*\([^()]*\)", code)]
    sites = [x for x in sites if not x.startswith("w32(uintptr_tpa")]
    if sorted(sites) != _GPUJOB_W32_SITES:
        raise AssertionError(f"{name}: unexpected w32() call sites: {sorted(set(sites) ^ set(_GPUJOB_W32_SITES))}")
    for fn, want in (("entry", _GPUJOB_ENTRY), ("build", _GPUJOB_BUILD)):
        fa, fb = _c_function_span(code, fn)
        if re.sub(r"\s+", "", code[fa:fb][code[fa:fb].index("{"):]) != want:
            raise AssertionError(f"{name}: {fn}() is not the audited page-table builder (all PAs derive from j->base)")
    if "JS_FEATURE_SET_VALUE_JOB(1u<<2)" not in re.sub(r"\s+", "", code) or not re.search(r"#define\s+JS_STATUS_ACTIVE\s+0x08u\b", code):
        raise AssertionError(f"{name}: JS_FEATURE_SET_VALUE_JOB / JS_STATUS_ACTIVE are not the audited constants")
    # no loop construct but the bounded ones: for (...; i < J_POLLS / NPAGES * PG / 4u ...), and put_txt / put_hex's string loops; no while elsewhere, no recursion
    if re.search(r"\bgoto\b|\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    pa_, pb_ = _c_function_span(code, "put_txt")
    for m2 in re.finditer(r"\bwhile\b", code):
        if not (pa_ <= m2.start() < pb_):
            raise AssertionError(f"{name}: 'while' outside put_txt (waits are the J_POLLS-bounded for loops)")
    for m2 in re.finditer(r"for\s*\(([^;]*);([^;]*);", code):
        cond = re.sub(r"\s+", "", m2.group(2))
        if not re.search(r"<(J_POLLS|NPAGES\*PG/4u|digits|2|4)(&&|$)", cond) and "i<" not in cond:
            raise AssertionError(f"{name}: unbounded for loop condition '{cond}'")
    names = re.findall(r"(?m)^(?:__attribute__\(\([\w, ]+\)\)\s+)?(?:static\s+)?(?:const\s+)?(?:int|void|uint32_t|uint64_t|uintptr_t|unsigned)(?:\s+|\s*\*+\s*)(\w+)\s*\([^)]*\)\s*\{", code)
    calls = {}
    for fn in names:
        fa, fb = _c_function_span(code, fn)
        calls[fn] = {g for g in names if re.search(r"\b" + g + r"\s*\(", code[fa:fb][code[fa:fb].index("{"):])}

    def reaches(x, y, seen=()):
        return any(c == y or (c not in seen and reaches(c, y, seen + (c,))) for c in calls.get(x, ()))
    for fn in names:
        if reaches(fn, fn):
            raise AssertionError(f"{name}: {fn} is (mutually) recursive")
    # the read-only preconditions come first: no w32 before the refusal in gpu_job_run
    ga, gb = _c_function_span(code, "gpu_job_run")
    run = re.sub(r"\s+", "", code[ga:gb])
    refuse = run.find("why=precheck(j);if(why){say(\"refused:\",why);return1;}")
    first_w = min([run.find(x) for x in ("build(", "as_update(", "w32(") if run.find(x) >= 0] or [-1])
    if refuse < 0 or first_w < refuse:
        raise AssertionError(f"{name}: gpu_job_run must precheck and refuse before its first write")
    pa_, pb_ = _c_function_span(code, "precheck")
    pre = code[pa_:pb_]
    if re.search(r"\bw32\s*\(", pre):
        raise AssertionError(f"{name}: precheck must be read-only")
    for need in ("scratch-bad", "no-timer", "js0-absent", "js0-no-set-value", "shaders-not-ready", "job-irq-pending", "mmu-irq-pending", "as0-active", "js0-busy", "gpu-fault"):
        if need not in pre:
            raise AssertionError(f"{name}: precheck lost '{need}'")
    if len(re.findall(r"return\s+0\s*;", pre)) != 1:
        raise AssertionError(f"{name}: precheck must return 0 (all clear) only at its end")
    # cleanup is unconditional: the AS0 disable (TRANSCFG_OFF) is reached on every path after the precheck
    if "off=as_update(j,GPU_JOB_TRANSCFG_OFF,0,0);" not in run:
        raise AssertionError(f"{name}: AS0 must always be set back to UNMAPPED")
    if "jc.base=sc;jc.l0_lo=(uint32_t)sc;jc.l0_hi=(uint32_t)(sc>>32);" not in run or "e=build(j);" not in run or "uintptr_tsc=gpu_job_scratch();" not in run:
        raise AssertionError(f"{name}: the context (TRANSTAB = the scratch base) and build(j) are pinned: the GPU's page tables start at the scratch")
    if run.count("stopped=hardstop(j);") != 2 or run.count("if(!waited){") != 1 or "if(sub&&!stopped&&jsst==JS_STATUS_ACTIVE){" not in run:
        raise AssertionError(f"{name}: hard stop only after the wait timed out, or for a job still ACTIVE at cleanup, at most once in total")
    ha, hb = _c_function_span(code, "hardstop")
    if re.sub(r"\s+", "", code[ha:hb][code[ha:hb].index("{"):]) != "{return!w32(A_JS_CMD,JS_CMD_HARDSTOP_1,j);}":
        raise AssertionError(f"{name}: hardstop() is not the audited single hard-stop site")
    i_stop, i_off, i_clr = run.rfind("stopped=hardstop(j);"), run.find("off=as_update(j,GPU_JOB_TRANSCFG_OFF,0,0);"), run.find("(void)w32(A_JOB_CLEAR,")
    if not 0 < i_stop < i_off < i_clr:
        raise AssertionError(f"{name}: cleanup order must be hard stop, AS0 off, IRQ clear (kbase kill order)")
    aa, ab = _c_function_span(code, "as_update")
    asu = re.sub(r"\s+", "", code[aa:ab])
    if not 0 <= asu.find("if(as_idle())return1;") < asu.find("w32(A_AS_COMMAND,AS_CMD_UPDATE,j)"):
        raise AssertionError(f"{name}: AS_ACTIVE must be idle before the UPDATE is written (kbase write_cmd)")


def gpujob_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "gpujob.c").read_text()
    check_gpujob_source_text(good)  # control must pass
    ins = "static int r32"
    cases = [
        ("inline asm", good.replace(ins, 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\n' + ins, 1)),
        ("a second write path", good.replace(ins, "static void evil(void) { try_write32(0x1c500030UL, 1); }\n" + ins, 1)),
        ("a pointer cast of a variable", good.replace(ins, "static void evil(uintptr_t pa) { *(uint32_t *)pa = 0; }\n" + ins, 1)),
        ("w32 skips the allowlist", good.replace("if (!w32_allowed(pa, v, j))", "if (0)", 1)),
        ("w32 writes before the guard", good.replace("\tif (!w32_allowed(pa, v, j))\\n".replace("\\n", "\n") + "\t\treturn -2;\n\treturn try_write32(pa, v) ? 0 : -1;", "\t(void)try_write32(pa, v);\n\tif (!w32_allowed(pa, v, j))\n\t\treturn -2;\n\treturn 0;", 1)),
        ("w32_allowed always returns 1", re.sub(r"(static int w32_allowed\(uintptr_t pa, uint32_t v, const struct jctx \*j\)\n\{).*?\n\}\n", r"\1\n\treturn 1;\n}\n", good, count=1, flags=re.S)),
        ("GPU_COMMAND allowed (soft reset)", good.replace("\tif (pa == A_JOB_CLEAR || pa == A_MMU_CLEAR)", "\tif (pa == GPU_MALI + 0x30u)\n\t\treturn v == 1;\n\tif (pa == A_JOB_CLEAR || pa == A_MMU_CLEAR)", 1)),
        ("any TRANSCFG value", good.replace("return v == GPU_JOB_TRANSCFG_ON || v == GPU_JOB_TRANSCFG_OFF;", "return 1;", 1)),
        ("the scratch window widened", good.replace("pa < j->base + NPAGES * PG", "pa < j->base + 64u * PG", 1)),
        ("any JS0 command", good.replace("return v == JS_CMD_HARDSTOP_1;", "return v < 8;", 1)),
        ("any IRQ clear value", good.replace("return v == IRQ_SLOT0_BITS;", "return 1;", 1)),
        ("LOCKADDR register defined", good.replace("#define A_GPU_FAULT", "#define A_AS_LOCKADDR\t(GPU_MALI + 0x2410u)\n#define A_GPU_FAULT", 1)),
        ("an unlisted write site", good.replace("\tgpu_job_cache_flush(sc + 0x4000u, PG);", "\t(void)w32(A_JOB_CLEAR, IRQ_SLOT0_BITS, j);\n\tgpu_job_cache_flush(sc + 0x4000u, PG);", 1)),
        ("a raw register", good.replace(ins, "static uintptr_t evil(void) { return GPU_MALI + 0x1a0u; }\n" + ins, 1)),
        ("a write before the preconditions", good.replace("\tgpu_s2mpu_dump(sc);", "\t(void)w32(A_JS_CMD_NEXT, JS_CMD_START, j);\n\tgpu_s2mpu_dump(sc);", 1)),
        ("the refusal does not return", good.replace('say("refused: ", why);\n\t\treturn 1;', 'say("refused: ", why);', 1)),
        ("precheck without the IRQ check", good.replace('return "job-irq-pending";', "(void)v;", 1)),
        ("an unbounded wait", good.replace("for (unsigned i = 0; i < J_POLLS; i++) {\n\t\tif (r32(A_JOB_RAW", "for (;;) {\n\t\tif (r32(A_JOB_RAW", 1)),
        ("J_POLLS beyond the cap", good.replace("#define J_POLLS\t\t200000u", "#define J_POLLS\t\t4000000000u", 1)),
        ("no AS0 disable", good.replace("off = as_update(j, GPU_JOB_TRANSCFG_OFF, 0, 0);", "off = 0;", 1)),
        ("the data page moved to an arbitrary DRAM address", good.replace("e = entry(j, 0x3000u + 8u * 1u, 0x4000u, GPU_JOB_PTE_LEAF);", "e = put64(j, 0x3000u + 8u * 1u, 0x80000000u | GPU_JOB_PTE_LEAF, 0);", 1)),
        ("an entry points off the scratch", good.replace("e = entry(j, 0x1000u, 0x2000u, GPU_JOB_PTE_TABLE);", "e = entry(j, 0x1000u, 0x2000u + 0x100000u, GPU_JOB_PTE_TABLE);", 1)),
        ("TRANSTAB is not the scratch", good.replace("jc.l0_lo = (uint32_t)sc;", "jc.l0_lo = (uint32_t)sc + 0x10000u;", 1)),
        ("entry() derives from an arbitrary base", good.replace("uint64_t pa = (uint64_t)(j->base + next);", "uint64_t pa = (uint64_t)next;", 1)),
        ("no JS0 capability check", good.replace('return "js0-no-set-value";', "(void)v;", 1)),
        ("no JS_PRESENT check", good.replace('return "js0-absent";', "(void)v;", 1)),
        ("IRQ clear before AS0 off", good.replace("\toff = as_update(j, GPU_JOB_TRANSCFG_OFF, 0, 0);", "\t(void)w32(A_JOB_CLEAR, IRQ_SLOT0_BITS, j);\n\toff = as_update(j, GPU_JOB_TRANSCFG_OFF, 0, 0);", 1)),
        ("UPDATE without the idle wait", good.replace("\tif (as_idle())\t\t\t\t/* the previous command has not finished: no UPDATE on top of it */\n\t\treturn 1;\n", "", 1)),
        ("a second hard stop", good.replace("\t\t\t\t\tstopped = hardstop(j);\n\t\t\t\t\tif (stopped)\n\t\t\t\t\t\twaited = wait_event();", "\t\t\t\t\tstopped = hardstop(j);\n\t\t\t\t\tstopped = hardstop(j);\n\t\t\t\t\tif (stopped)\n\t\t\t\t\t\twaited = wait_event();", 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"gpujob source selftest case did not mutate: {label}"
        try:
            check_gpujob_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: gpujob source {label} -> {str(exc)[:160]}")
        else:
            raise AssertionError(f"gpujob source self-test FAILED to catch: {label}")
    print(f"PASS gpujob source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


def check_touchprobe_object_text(disasm: str, undefined: set[str], local_text: set[str], sections: str,
                                 name: str = "touchprobe.o", allowed_undef: set[str] = _TOUCH_ALLOWED_UNDEF) -> int:
    """Writer audit (try_write32 only by a bl inside w32, undef subset, no banned insns) plus the store audit on
    everything except w32: every store is stack based or goes through an argument buffer (x0-x7 chain)."""
    check_usbgadget_object_text(disasm, undefined, name, allowed_undef)
    blocks = re.split(r"(?m)^(?=[0-9a-f]+ <[^>]+>:$)", disasm)
    rest = "".join(b for b in blocks if not re.match(r"[0-9a-f]+ <w32>:", b))
    return check_usb_object_text(rest, local_text | {"w32"}, undefined, sections, name,
                                 allowed_undef=allowed_undef,
                                 trusted_args=frozenset(f"x{i}" for i in range(8)),
                                 allow_adrp=False, require_clean_data=True)


def _touch_obj_info(obj_path: pathlib.Path):
    objdump, nm = _cross_tool("objdump"), _cross_tool("nm")

    def run(*cmd):
        p = subprocess.run(cmd, capture_output=True, text=True)
        assert p.returncode == 0, f"{cmd[0]} failed: {p.stderr.strip()}"
        return p.stdout

    undefined, local_text = set(), set()
    for line in run(nm, str(obj_path)).splitlines():
        toks = line.split()
        if len(toks) == 2 and toks[0] == "U":
            undefined.add(toks[1])
        elif len(toks) == 3 and toks[1] in ("t", "T"):
            local_text.add(toks[2])
    return run(objdump, "-dr", str(obj_path)), undefined, local_text, run(objdump, "-h", str(obj_path))


def check_touchprobe_object(obj_path: pathlib.Path, allowed_undef: set[str] = _TOUCH_ALLOWED_UNDEF) -> int:
    assert obj_path.is_file(), f"{obj_path} not found (run shim/build.sh first)"
    disasm, undefined, local_text, sections = _touch_obj_info(obj_path)
    return check_touchprobe_object_text(disasm, undefined, local_text, sections, str(obj_path), allowed_undef)


def touchprobe_object_selftest(obj_path: pathlib.Path, allowed_undef: set[str] = _TOUCH_ALLOWED_UNDEF,
                               tag: str = "touchprobe") -> int:
    """Mutations of the real object's disassembly: each must be rejected."""
    disasm, undefined, local_text, sections = _touch_obj_info(obj_path)
    check_touchprobe_object_text(disasm, undefined, local_text, sections, f"{tag} selftest", allowed_undef)  # control
    evil = ("0000000000ffff00 <evil>:\n"
            "  ffff00:\td2a22229 \tmovz\tx9, #0x1111, lsl #16\n"
            "  ffff04:\tb9000129 \tstr\tw0, [x9]\n"
            "  ffff08:\td65f03c0 \tret\n")
    evil_sp = ("0000000000ffff00 <evil>:\n"
               "  ffff00:\tb90013e0 \tstr\tw0, [sp, #16]\n"
               "  ffff04:\td65f03c0 \tret\n")
    evil_w = ("0000000000ffff00 <evil>:\n"
              "  ffff00:\t94000000 \tbl\t0 <try_write32>\n"
              "\t\t\tffff00: R_AARCH64_CALL26\ttry_write32\n"
              "  ffff04:\td65f03c0 \tret\n")
    check_touchprobe_object_text(disasm + evil_sp, undefined, local_text | {"evil"}, sections, f"{tag} selftest", allowed_undef)
    caught = 0
    for label, extra, und in (
        ("store through a movz/movk absolute pointer outside w32", evil, undefined),
        ("second bl try_write32 outside w32", evil_w, undefined),
        ("undefined symbol memcpy", evil_sp, undefined | {"memcpy"}),
    ):
        try:
            check_touchprobe_object_text(disasm + extra, und, local_text | {"evil"}, sections, f"{tag} selftest", allowed_undef)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: {tag} object {label} -> {exc}")
        else:
            raise AssertionError(f"{tag} object self-test FAILED to catch: {label}")
    print(f"PASS {tag} object self-test: {caught}/3 bad objects rejected")
    return caught


# ---- udump.o: read-only dump framer object audit (Spec 01 gate honesty) ----
#
# shim/udump.c frames the userdata dump and must stay free of MMIO: it
# reaches hardware only through the audited surfaces - usbgadget_write/
# usbgadget_ready for the wire, con_str/con_hex2 for text - plus the
# caller's hooks (a bounded UFS reader, the log pump, fakeable time).
# It carries NO static state, so .data/.bss must be empty, and every
# store must be provably to the stack or an argument buffer (x0-x7
# chain); adrp and movz/movk pointers can never be a store base, so no
# register window can be written through. Fail closed, like the audits
# above. "No WRITE opcodes" holds because the object's whole undefined
# surface is this set: it cannot call or build any SCSI/UFS command.

_UDUMP_ALLOWED_UNDEF = {
    "usbgadget_write",   # bounded bulk IN pieces (usbgadget.c)
    "usbgadget_ready",   # configured+DTR+no-fault query (usbgadget.c)
    "con_str",           # console/ramlog line out (console.c)
    "con_hex2",          # console/ramlog line out (console.c)
}


def check_udump_object(obj_path: pathlib.Path) -> int:
    """Run the no-MMIO store audit on a built udump.o."""
    assert obj_path.is_file(), f"{obj_path} not found (run shim/build.sh first)"
    objdump, nm = _cross_tool("objdump"), _cross_tool("nm")

    def run(*cmd):
        p = subprocess.run(cmd, capture_output=True, text=True)
        assert p.returncode == 0, f"{cmd[0]} failed: {p.stderr.strip()}"
        return p.stdout

    undefined = set()
    for line in run(nm, "-u", str(obj_path)).splitlines():
        toks = line.split()
        if len(toks) == 2 and toks[0] == "U":
            undefined.add(toks[1])
    local_text = set()
    for line in run(nm, str(obj_path)).splitlines():
        toks = line.split()
        if len(toks) == 3 and toks[1] == "t":
            local_text.add(toks[2])
    return check_usb_object_text(
        run(objdump, "-dr", str(obj_path)),
        local_text,
        undefined,
        run(objdump, "-h", str(obj_path)),
        str(obj_path),
        allowed_undef=_UDUMP_ALLOWED_UNDEF,
        trusted_args=frozenset(f"x{i}" for i in range(8)),
        allow_adrp=False,
        require_clean_data=True,
    )


_UD_NM_U = (
    "                 U usbgadget_write\n"
    "                 U usbgadget_ready\n"
    "                 U con_str\n"
    "                 U con_hex2\n"
)
_UD_CONTROL = """0000000000000000 <udump_run>:
   0:	d10043ff 	sub	sp, sp, #0x10
   4:	39000001 	strb	w1, [x0]
   8:	39000642 	strb	w2, [x2, 8]
   c:	b90017e3 	str	w3, [sp, 12]
  10:	d65f03c0 	ret
"""


def udump_object_selftest() -> int:
    """Synthetic udump-shaped bad objects; the control carries a store
    of every sanctioned class (stack, argument register) under the
    exact udump allowed-undef set."""

    def run_case(disasm, nm_u=_UD_NM_U):
        undef = {t[1] for t in (l.split() for l in nm_u.splitlines())
                 if len(t) == 2 and t[0] == "U"}
        return check_usb_object_text(
            disasm, {"udump_run"}, undef, _SYN_H, "udump selftest",
            allowed_undef=_UDUMP_ALLOWED_UNDEF,
            trusted_args=frozenset(f"x{i}" for i in range(8)),
            allow_adrp=False, require_clean_data=True)

    n = run_case(_UD_CONTROL)
    assert n == 3, f"udump selftest control: expected 3 audited stores, saw {n}"

    cases = [
        ("try_write32 in the undef set (a write path sneaking in)",
         run_case, _UD_CONTROL, _UD_NM_U + "                 U try_write32\n"),
        ("store through a movz/movk absolute pointer (an MMIO window)",
         run_case, _UD_CONTROL.replace(
             "  10:\td65f03c0 \tret\n",
             "  10:\td2a22229 \tmovz\tx9, #0x1111, lsl #16\n"
             "  14:\tb9000129 \tstr\tw0, [x9]\n"), ""),
        ("store through an adrp-derived pointer",
         run_case, _UD_CONTROL.replace(
             "   8:\t39000642 \tstrb\tw2, [x2, 8]\n",
             "   8:\t90000009 \tadrp\tx9, 0 <udump_run>\n"
             "   c:\t39000129 \tstrb\tw2, [x9]\n"
             "  10:\tb90017e3 \tstr\tw3, [sp, 12]\n"
             "  14:\td65f03c0 \tret\n"), ""),
        ("store through an unclassified callee-saved register",
         run_case, _UD_CONTROL.replace("strb\tw2, [x2, 8]", "strb\tw2, [x20]"), ""),
        ("store through a redefined argument register in register-offset form (finding H2 bypass)",
         run_case, _UD_CONTROL.replace(
             "  10:\td65f03c0 \tret\n",
             "  10:\td2a22220 \tmov\tx0, #0x11110000\n"
             "  14:\tb8206823 \tstr\tw3, [x1, x0]\n"
             "  18:\td65f03c0 \tret\n"), ""),
    ]
    caught = 0
    for label, fn, *args in cases:
        try:
            fn(*args)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: udump {label} -> {exc}")
        else:
            raise AssertionError(f"udump object self-test FAILED to catch: {label}")
    print(f"PASS udump object self-test: {caught}/{len(cases)} bad objects rejected")
    return caught


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("boot_image", type=pathlib.Path)
    args = parser.parse_args()
    image = args.boot_image.read_bytes()
    assert image[:8] == b"ANDROID!", "missing Android boot image magic"
    kernel_size, ramdisk_size, _, header_size = struct.unpack_from("<4I", image, 8)
    header_version, = struct.unpack_from("<I", image, 40)
    assert header_version == 4, f"expected header v4, got {header_version}"
    assert header_size == 1584, f"unexpected v4 header size: {header_size}"
    assert kernel_size > 0 and ramdisk_size > 0, "kernel and ramdisk must both be present"
    page_size = 4096
    kernel_data = image[page_size:page_size + kernel_size]
    is_arm64 = len(kernel_data) > 0x3c and kernel_data[0x38:0x3c] == b"ARMd"
    is_lz4 = kernel_data[:4] == bytes([0x04, 0x22, 0x4d, 0x18])
    assert is_arm64 or is_lz4, "kernel is neither an ARM64 Image nor an LZ4-compressed Image"

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    check_ufs_safety(repo_root / "shim" / "ufs.c")
    ufs_safety_selftest()
    check_usb_safety(repo_root / "shim" / "usbprobe.c")
    check_usb_safety(repo_root / "shim" / "usbprobe.h")
    check_usb_safety(repo_root / "shim" / "wlanprobe.c")
    check_usb_safety(repo_root / "shim" / "wlanprobe.h")
    check_usb_safety(repo_root / "shim" / "gpuprobe.c")
    check_usb_safety(repo_root / "shim" / "gpuprobe.h")
    check_usb_safety(repo_root / "shim" / "gpus2mpu.c")
    check_usb_safety(repo_root / "shim" / "gpus2mpu.h")
    usb_safety_selftest()
    usb_object_selftest()
    n_stores = check_usb_object(repo_root / "shim" / "out" / "usbprobe.o")
    print(f"PASS usb object audit: {n_stores} stores, all stack-based; "
          "undef/.data/.bss/instruction bans clean")
    n_stores = check_usb_object(repo_root / "shim" / "out" / "wlanprobe.o")
    print(f"PASS wlanprobe object audit: {n_stores} stores, all stack-based "
          "(same policy as usbprobe.o)")
    n_stores = check_usb_object(repo_root / "shim" / "out" / "gpuprobe.o")
    print(f"PASS gpuprobe object audit: {n_stores} stores, all stack-based "
          "(same policy as usbprobe.o)")
    n_stores = check_usb_object(repo_root / "shim" / "out" / "gpus2mpu.o")
    print(f"PASS gpus2mpu object audit: {n_stores} stores, all stack-based "
          "(same read-only policy as gpuprobe.o: the S2MPU is never written)")
    usbproto_object_selftest()
    n_stores = check_usbproto_object(repo_root / "shim" / "out" / "usbproto.o")
    print(f"PASS usbproto object audit: {n_stores} stores, all stack/arg/"
          "adrp-image based; undef empty; bans clean")
    n_stores = check_usbproto_object(repo_root / "shim" / "out" / "usbpool.o")
    print(f"PASS usbpool object audit: {n_stores} stores, all stack/arg/"
          "adrp-image based; undef empty; bans clean (same no-MMIO audit as usbproto.o)")
    usbgadget_source_selftest()
    check_usbgadget_source_text((repo_root / "shim" / "usbgadget.c").read_text())
    print("PASS usbgadget source audit: no asm; try_write32 and the DWC3 base only in w32/r32; "
          "no literal pointer casts; w32 consults its exact allowlist")
    usbgadget_object_selftest()
    check_usbgadget_object(repo_root / "shim" / "out" / "usbgadget.o")
    print("PASS usbgadget writer audit: try_write32 referenced only by a "
          "bl inside w32; undef subset; no banned instructions")
    touchprobe_source_selftest()
    check_usbgadget_source_text((repo_root / "shim" / "touchprobe.c").read_text(), "touchprobe.c", **_TOUCH_KW)
    print("PASS touchprobe source audit: no asm / volatile / pointer casts; try_write32 only in w32; w32 checks "
          "w32_allowed before writing; allowlist depends on the address")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "touchprobe.o")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "touchprobe.o")
    print(f"PASS touchprobe object audit: try_write32 referenced only by a bl inside w32; {n_stores} stores outside w32, "
          "all stack/argument based; undef subset; no banned instructions")
    acpm_source_selftest()
    check_usbgadget_source_text((repo_root / "shim" / "acpm.c").read_text(), "acpm.c", **_ACPM_KW)
    print("PASS acpm source audit: no asm / volatile / pointer casts; try_write32 only in w32; w32 checks "
          "w32_allowed before writing; allowlist depends on the address (this proves the write PATH; the ADDRESSES come from SRAM "
          "words checked by acpm_describe and by shim/acpm_host_test.c test_guard, not by this audit)")
    gpupower_source_selftest()
    check_gpupower_source_text((repo_root / "shim" / "gpupower.c").read_text())
    print("PASS gpupower source audit: no asm / volatile / pointer casts; try_write32 only in w32; w32 checks w32_allowed before "
          "writing; the allowlist is exactly SHADER_PWRON/TILER_PWRON/SHADER_PWROFF/L2_PWROFF (LO words) with the present masks; "
          "precheck() before the first write; waits bounded by GP_POLLS (cap 200000) and CNTPCT; no recursion / loop-variable writes; w32 call sites pinned")
    for stack, objname in ((False, "gpupower.o"), (True, "gpupower_stack.o")):
        obj = repo_root / "shim" / "out" / objname
        gpupower_object_variant_selftest(obj, stack)
        check_gpupower_variant_text(_touch_obj_info(obj)[0], stack, objname)
        print(f"PASS {objname} variant audit: {'4 + 2 (STACK_PWRON/STACK_PWROFF)' if stack else '4'} audited write sites, STACK_PWRON offset {'present once' if stack else 'absent'}")
        if stack:
            touchprobe_object_selftest(obj, _GPUPOWER_ALLOWED_UNDEF, "gpupower_stack")
            n_stores = check_touchprobe_object(obj, _GPUPOWER_ALLOWED_UNDEF)
            print(f"PASS gpupower_stack object audit: try_write32 referenced only by a bl inside w32; {n_stores} stores outside w32, all stack/argument based")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "gpupower.o", _GPUPOWER_ALLOWED_UNDEF, "gpupower")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "gpupower.o", _GPUPOWER_ALLOWED_UNDEF)
    print(f"PASS gpupower object audit: try_write32 referenced only by a bl inside w32; {n_stores} stores outside w32, "
          "all stack/argument based; undef subset {try_read32,try_write32,con_str,touch_ticks,counter_freq}; no banned instructions")
    gpuclk_source_selftest()
    check_gpuclk_source_text((repo_root / "shim" / "gpuclk.c").read_text())
    print("PASS gpuclk source audit: no asm / volatile / pointer casts / MMIO accessors; no acpm_dvfs_set; one request site "
          "acpm_dvfs_reassert(GPU_CLK_KHZ) in the order G3DL2 then G3D, after every ACPM gate and a clean first GET; no retry loops")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "gpuclk.o", _GPUCLK_ALLOWED_UNDEF, "gpuclk")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "gpuclk.o", _GPUCLK_ALLOWED_UNDEF)
    print(f"PASS gpuclk object audit: no try_write32/try_read32 reference; {n_stores} stores, all stack/argument based; undef subset (the audited ACPM client, console, gpu_probe_cmu)")
    gpurails_source_selftest()
    check_gpurails_source_text((repo_root / "shim" / "gpurails.c").read_text())
    print("PASS gpurails source audit: read-only; no asm / volatile / pointer casts / MMIO accessors; the only ACPM call is acpm_s11_read over soc.json's register list, "
          "after every ACPM gate; the first failure stops the loop")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "gpurails.o", _GPURAILS_ALLOWED_UNDEF, "gpurails")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "gpurails.o", _GPURAILS_ALLOWED_UNDEF)
    print(f"PASS gpurails object audit: no try_write32/try_read32 reference; {n_stores} stores, all stack/argument based; undef subset (the audited ACPM client, console)")
    gpurailon_selftest()
    print("PASS gpu-rail-on source audit: acpm_s11_rail_set = one UPDATE of S2MPG11 0x15 under mask 0xC0 with value 0x40/0x00 behind the 'must be off' and OUT1-window guards; "
          "gpurailon.c has no MMIO, two rail-set sites (enable once, restore once); shim.c skips the power cycle unless the rail is verified on and restores after it, always")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "gpurailon.o", _GPURAILON_ALLOWED_UNDEF, "gpurailon")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "gpurailon.o", _GPURAILON_ALLOWED_UNDEF)
    print(f"PASS gpurailon object audit: no try_write32/try_read32 reference; {n_stores} stores, all stack/argument based; undef subset (the audited ACPM client)")
    gpurailon_object_selftest(repo_root / "shim" / "out" / "acpm_railon.o")
    check_acpm_rail_set_object_text(_touch_obj_info(repo_root / "shim" / "out" / "acpm_railon.o")[0], "acpm_railon.o", True)
    check_acpm_rail_set_object_text(_touch_obj_info(repo_root / "shim" / "out" / "acpm.o")[0], "acpm.o", False)
    touchprobe_object_selftest(repo_root / "shim" / "out" / "acpm_railon.o", _ACPM_ALLOWED_UNDEF, "acpm_railon")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "acpm_railon.o", _ACPM_ALLOWED_UNDEF)
    print(f"PASS acpm_railon.o object audit: try_write32 only by a bl inside w32; {n_stores} stores outside w32, all stack/argument based; acpm_s11_rail_set pinned to reg 0x15 / mask 0xC0 / value 0x40|0 and three xfer calls")
    wlanrails_source_selftest()
    check_wlanrails_source_text((repo_root / "shim" / "wlanrails.c").read_text())
    print("PASS wlanrails source audit: read-only; no asm / volatile / pointer casts / try_write32; MMIO only the eight cited words (CMU_HSI2 behind the UFS VER gate, PMU+0x3C10); "
          "the only ACPM call is acpm_s10_read over the four LDO control registers, after every ACPM gate; the first failure stops the loop")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "wlanrails.o", _WLANRAILS_ALLOWED_UNDEF, "wlanrails")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "wlanrails.o", _WLANRAILS_ALLOWED_UNDEF)
    print(f"PASS wlanrails object audit: no try_write32 reference; {n_stores} stores, all stack/argument based; undef subset (try_read32, the audited ACPM client, console)")
    wlanen_source_selftest()
    check_wlanen_source_text((repo_root / "shim" / "wlanen.c").read_text())
    print("PASS wlanen source audit: strict writer audit; the allowlist is exactly GPP14.CON field [11:8] (value 0 or 1) and GPP14.DAT bit 2; four w32 sites (enable DAT, CON; restore DAT, CON); "
          "every refusal before the first write; restore unconditional and loud; one 200 ms wait after a verified enable")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "wlanen.o", _WLANEN_ALLOWED_UNDEF, "wlanen")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "wlanen.o", _WLANEN_ALLOWED_UNDEF)
    print(f"PASS wlanen object audit: try_write32 referenced only by a bl inside w32; {n_stores} stores outside w32, all stack/argument based; undef subset")
    wlanen_w32_object_selftest(repo_root / "shim" / "out" / "wlanen.o")
    check_wlanen_w32_object_text(_touch_obj_info(repo_root / "shim" / "out" / "wlanen.o")[0], "wlanen.o")
    print("PASS wlanen w32 object audit: the compiled w32 keeps its address compare (0x108401C0 / +4), masks 0xF00 / 0x4 and the -2 refusal; its four call sites pass exactly 0x108401C0 / 0x108401C4")
    wlansmc_source_selftest()
    check_wlansmc_source_text((repo_root / "shim" / "wlansmc.c").read_text())
    print("PASS wlansmc source audit: one `smc #0`, function id 0x82000504, option READ (0) a constant, address guard = exactly PMU 0x17463EC4 / 0x17463EC0, two calls, no loops, no writer")
    wlansmc_object_selftest(repo_root / "shim" / "out" / "wlansmc.o")
    n_stores = check_wlansmc_object(repo_root / "shim" / "out" / "wlansmc.o")
    print(f"PASS wlansmc object audit: exactly one smc, in smc_priv_read; x0 = 0x82000504, x2 = 0 (READ), x3 = 0; address compare against 0x17463EC0/+4 with a refusal that skips it; {n_stores} stores, all stack/argument based")
    wlansmc_object_selftest(repo_root / "shim" / "out" / "wlansmc_iso.o", True)
    n_stores = check_wlansmc_object(repo_root / "shim" / "out" / "wlansmc_iso.o", True)
    print(f"PASS wlansmc_iso object audit: exactly three smc (read, rmw set, rmw clear); the RMW pair is constants only: x0 = 0x82000504, x1 = 0x17463EC4, x2 = 2, x3 = 1, x4 = 1 / 0; {n_stores} stores, all stack/argument based")
    wlan_shim_selftest()
    print("PASS wlan shim sequence audit: wlan_rails(), wlan_smc_read(), wlan_iso_run() and wlan_en_run() called once each, in their own flags (+ wlan_iso_run), the read-only parts before the writers")
    gpujob_source_selftest()
    check_gpujob_source_text((repo_root / "shim" / "gpujob.c").read_text())
    print("PASS gpujob source audit: strict writer audit; the allowlist is the scratch window plus exactly the audited AS0 / JS0 / IRQ-clear register-value pairs "
          "(no GPU_COMMAND, no LOCKADDR, no IRQ mask, no S2MPU/PMU); 21 pinned write sites; precheck before the first write; bounded waits; AS0 always disabled")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "gpujob.o", _GPUJOB_ALLOWED_UNDEF, "gpujob")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "gpujob.o", _GPUJOB_ALLOWED_UNDEF)
    print(f"PASS gpujob object audit: try_write32 referenced only by a bl inside w32; {n_stores} stores outside w32, all stack/argument based; undef subset")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "gpupower_job.o", _GPUPOWER_ALLOWED_UNDEF | {"gpu_job_run"}, "gpupower_job")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "gpupower_job.o", _GPUPOWER_ALLOWED_UNDEF | {"gpu_job_run"})
    check_gpupower_variant_text(_touch_obj_info(repo_root / "shim" / "out" / "gpupower_job.o")[0], False, "gpupower_job.o")
    print(f"PASS gpupower_job.o object audit: same writes as gpupower.o ({n_stores} stores outside w32, all stack/argument based); the only addition is the call to gpu_job_run")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "acpm.o", _ACPM_ALLOWED_UNDEF, "acpm")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "acpm.o", _ACPM_ALLOWED_UNDEF)
    print(f"PASS acpm object audit: try_write32 referenced only by a bl inside w32; {n_stores} stores outside w32, "
          "all stack/argument based; undef subset; no banned instructions")
    udump_object_selftest()
    n_stores = check_udump_object(repo_root / "shim" / "out" / "udump.o")
    print(f"PASS udump object audit: {n_stores} stores, all stack/argument-"
          "based; .data/.bss empty; undef subset (no SCSI surface, no MMIO)")
    with tempfile.TemporaryDirectory() as temp:
        host_test = pathlib.Path(temp) / "loader_host_test"
        fdt_test = pathlib.Path(temp) / "fdt_host_test"
        subprocess.run([
            "gcc", "-Wall", "-Wextra", "-Ishim", "shim/payload.c",
            "shim/loader_host_test.c", "-o", str(host_test),
        ], check=True, cwd=repo_root)
        subprocess.run([str(host_test)], check=True, cwd=repo_root)
        subprocess.run([
            "gcc", "-Wall", "-Wextra", "-Ishim", "shim/fdt.c",
            "shim/fdt_host_test.c", "-o", str(fdt_test),
        ], check=True, cwd=repo_root)
        subprocess.run([str(fdt_test)], check=True, cwd=repo_root)
    fmt = "lz4" if is_lz4 else "raw"
    print(f"PASS header v{header_version} ({fmt}): kernel={kernel_size} ramdisk={ramdisk_size}; host tests & safety checks passed")


if __name__ == "__main__":
    main()
