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
import json
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
_USB_FLAGS_ONLY = {"cmp", "cmn", "tst", "ccmp", "ccmn"}	# ccmn: flags-only like ccmp (the compiled memw_ok uses it, research 40)
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


_WLANSMC_LINK_SET = re.sub(r"\s+", "", """int wlan_link_iso_set(uint32_t *rd)
{
	uint32_t before = 0, iso0 = 0, iso0b = 0, after = 0;
	struct smc_res r = { 0, 0, 0, 0 }, rs = { 0, 0, 0, 0 };
	int rok = 0, reached;

	*rd = 0;
	if (!try_read32(ISO_REG, &before) || !try_read32(WLAN_PMU_PCIE0_ISO, &iso0))
		return 1;
	if ((before & 1u) || smc_priv_read(ISO_REG, &r) || (int32_t)(uint32_t)r.a0 < 0 || (uint32_t)r.a0 != before)
		return 1;
	smc_priv_rmw_set(&rs);
	reached = poll_bit(1u, &after, &rok);
	(void)try_read32(WLAN_PMU_PCIE0_ISO, &iso0b);
	*rd = rok ? after : 0u;
	return (rok && reached && (int32_t)(uint32_t)rs.a0 == 0 && ((after ^ before) & ~1u) == 0u && iso0b == iso0) ? 0 : 2;
}""")
_WLANSMC_LINK_CLEAR = re.sub(r"\s+", "", """int wlan_link_iso_clear(uint32_t *rd)
{
	uint32_t rb = 0, fin = 0;
	struct smc_res rc = { 0, 0, 0, 0 };
	int rok2 = 0, fin_ok;

	smc_priv_rmw_clear(&rc);
	(void)poll_bit(0u, &rb, &rok2);
	wait_ticks(counter_freq() / ISO_FINAL_DIV);
	fin_ok = try_read32(ISO_REG, &fin);
	*rd = fin_ok ? fin : 0u;
	return (rok2 && !(rb & 1u) && fin_ok && !(fin & 1u)) ? 0 : 2;
}""")


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
        if len(re.findall(r"\b" + fn + r"\s*\(", fcode)) != 3:	# the definition, the call in wlan_iso_run and the call in the --wlan-link wrapper
            raise AssertionError(f"{name}: {fn} has exactly two call sites (wlan_iso_run, the wlan_link_iso_* wrapper)")
    if "#ifndef WLAN_LINK_RUN" not in iso_src or "#endif\t/* !WLAN_LINK_RUN */" not in iso_src or "#ifdef WLAN_LINK_RUN" not in iso_src:
        raise AssertionError(f"{name}: wlan_iso_run must be compiled out of a --wlan-link image and the wrappers into it")
    for fn, want in (("wlan_link_iso_set", _WLANSMC_LINK_SET), ("wlan_link_iso_clear", _WLANSMC_LINK_CLEAR)):
        wa, wb = _c_function_span(code, fn)
        if re.sub(r"\s+", "", code[wa:wb]) != want:
            raise AssertionError(f"{name}: {fn} is not the audited wrapper")
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
        # --wlan-link wrappers
        ("link set without the SMC READ check", good.replace("if ((before & 1u) || smc_priv_read(ISO_REG, &r) || (int32_t)(uint32_t)r.a0 < 0 || (uint32_t)r.a0 != before)", "if (before & 1u)", 1)),
        ("link clear made conditional", good.replace("\tsmc_priv_rmw_clear(&rc);\n\t(void)poll_bit(0u, &rb, &rok2);", "\tif (*rd)\n\t\tsmc_priv_rmw_clear(&rc);\n\t(void)poll_bit(0u, &rb, &rok2);", 1)),
        ("link set sends the clear value", good.replace("\tsmc_priv_rmw_set(&rs);\n\treached = poll_bit(1u, &after, &rok);", "\tsmc_priv_rmw_clear(&rs);\n\treached = poll_bit(1u, &after, &rok);", 1)),
        ("wlan_iso_run left in the link image", good.replace("#ifndef WLAN_LINK_RUN\t/* the --wlan-iso procedure; a --wlan-link image has the two steps below instead */\n", "", 1)),
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


# ---- --wlan-link: the table (soc/google/gs101/wlan_seq.json -> out/wlan_seq.h), the interpreter wlanlink.c, the objects (docs/research/29 section 14) ----
_WLAN_SEQ_SHA = "2ecd5871c3047730c83f08af5d325e554ea61e133089b5752edf144078222e27"
_WLAN_SEQ_COUNTS = {"rows": 1412, "teardown": 47, "write_rows": 296, "distinct_targets": 197}
_WLAN_SEQ_TARGET_BLOCKS = {"ATU": 11, "CFG": 6, "DBI": 1, "ELBI": 12, "GPH2": 6, "GPP14": 2, "IA": 37, "MEM": 4, "PCS": 5, "PMA": 113}
FULL_MASK = 0xFFFFFFFF
_WLAN_WRITE_KINDS = ("gpio_dat", "gpio_con", "mmio_rmw", "mmio_write")
_WLAN_WIN = {"GPP14": 0x18, "GPH2": 0x18, "PMA": 0x2000, "PCS": 0x1000, "ELBI": 0x2000, "IA": 0x1000, "DBI": 0x1000, "PMU": 0x4000, "ATU": 0x1000, "CFG": 0x2000, "MEM": 0x1000}
# ATU outbound region 0 (S26, research 31 s.2.2) and region 1 (S29, research 36 s.2): the only (offset, value) pairs a table row may write: the S26 / S29 values and the reset values the teardown restores (twin of wlanlink.c atu_ok
# without the r1ok state); region 1 = exactly 0x204 (CR2) / 0x208 (LOWER_BASE) / 0x210 (LIMIT) / 0x214 (LOWER_TARGET), never CR1 0x200, UPPER_BASE 0x20C, UPPER_TARGET 0x218
_WLAN_ATU_OK = {(0x000, 0x4), (0x000, 0), (0x004, 0x80000000), (0x004, 0), (0x008, 0x60FFE000), (0x008, 0), (0x00C, 0), (0x010, 0x60FFFFFF), (0x010, 0xFFF), (0x014, 0x01000000), (0x014, 0), (0x018, 0),
                (0x204, 0x80000000), (0x204, 0), (0x208, 0x60000000), (0x208, 0), (0x210, 0x6000FFFF), (0x210, 0xFFF), (0x214, 0x60000000), (0x214, 0)}
_WLAN_S29_WRITES = ((0x208, 0x60000000), (0x210, 0x6000FFFF), (0x214, 0x60000000), (0x204, 0x80000000))	# region 1: base, limit, target, ENABLE last (Linux's order, rc.c:1402-1414; CR1 / UPPER_* skipped: already at the written value)
_WLAN_S32_TEAR_HEAD = (("mmio_write", "CFG", 0x80, 0x18000000, FULL_MASK, 32), ("wait_eq", "CFG", 0x80, 0x18000000, FULL_MASK, 32))	# S32: the window back to ChipCommon FIRST of all, then its one-shot read-back
_WLAN_S33_CHAIN = (0x18012000, 0x18120000, 0x18126000)	# S33 (trimmed by research 40): the only forward window moves after S32, each only from its predecessor (0x18105000 first); the SYS_MEM core page moved to S35
_WLAN_S34_WINDOWS = {34: (0x18001000,), 35: (0x18126000, 0x18026000), 36: (0x18120000,)}	# research 40 s.5: the forward window moves of the write steps (0x18126000 -> 0x18001000 -> 0x18126000 -> 0x18026000 -> 0x18120000)
_WLAN_MEM_OFFS = {0x18000000: (0, 0xAC, 0xFC, 0x1E0), 0x18012000: (0x600, 0x604, 0x608, 0x618, 0x61C), 0x18120000: (0x408, 0x800, 0x804), 0x18126000: (0x408, 0x800, 0x804), 0x18001000: (0x1E8,), 0x18026000: (0, 0x10, 0x40)}	# the exact BAR0 read list per window (research 38 s.3, research 40 s.5); 0x18105000 = the EROM dwords below 0xE00
# the BAR0 WRITE allowlist (research 40 s.5), twin of wlanlink.c memw_ok without the live predecessor state: (step, kind, window, offset, mask, value). Nothing else is writable behind BAR0.
_WLAN_MEM_WRITES = ((34, "mmio_rmw", 0x18001000, 0x1E8, 0x200, 0x200),
                    (35, "mmio_write", 0x18126000, 0x800, 0xFFFFFFFF, 1), (35, "mmio_write", 0x18126000, 0x408, 0xFFFFFFFF, 3), (35, "mmio_write", 0x18126000, 0x800, 0xFFFFFFFF, 0), (35, "mmio_write", 0x18126000, 0x408, 0xFFFFFFFF, 1), (35, "mmio_write", 0x18026000, 0x10, 0xFFFFFFFF, 0),
                    (36, "mmio_write", 0x18120000, 0x800, 0xFFFFFFFF, 1), (36, "mmio_write", 0x18120000, 0x408, 0xFFFFFFFF, 0x23), (36, "mmio_write", 0x18120000, 0x800, 0xFFFFFFFF, 0), (36, "mmio_write", 0x18120000, 0x408, 0xFFFFFFFF, 0x21))
_WLAN_S29_TEAR_HEAD = (("ATU", 0x204, 0), ("CFG", 0x04, 0), ("CFG", 0x80, 0x18001000))	# region 1 DISABLE FIRST (RC-local), Command := 0, BAR0 window := observed original
_WLAN_S29_TEAR_ATU1 = ((0x208, 0), (0x210, 0xFFF), (0x214, 0))	# after the 4 BAR rows, before the region 0 rows
_WLAN_S26_WRITES = ((0x008, 0x60FFE000), (0x00C, 0), (0x010, 0x60FFFFFF), (0x014, 0x01000000), (0x018, 0), (0x000, 0x4), (0x004, 0x80000000))	# the order of Linux's exynos_pcie_rc_prog_viewport_cfg0, CR2 (enable) last
_WLAN_S26_TEAR = ((0x004, 0), (0x000, 0), (0x008, 0), (0x00C, 0), (0x010, 0xFFF), (0x014, 0), (0x018, 0))	# CR2 (disable) FIRST, then the reset values observed in run w3a-n25
# S27 (research 32 s.4.2 step A): the endpoint config dwords read through the CFG window (BAR0..5, CardBus, subsystem, cap pointer, int line, capability area 0x40..0x7C, Broadcom window/SPROM registers 0x80/0x84/0x88/0xAC, PCIe capability 0xB0..0xFC, ext header, L1SS LTR, L1SS); 0x8C..0xA8 are deliberately NOT read (0xA4 PCI_BACKPLANE_DATA is a backplane access) and ATU region 1 (+0x200..+0x218)
_WLAN_S27_CFG = (0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x34, 0x3C) + tuple(range(0x40, 0x80, 4)) + (0x80, 0x84, 0x88, 0xAC) + tuple(range(0xB0, 0x100, 4)) + (0x100, 0x1B4, 0x240, 0x244, 0x248, 0x24C)
_WLAN_S27_ATU = (0x200, 0x204, 0x208, 0x20C, 0x210, 0x214, 0x218)
# S28 (research 33) + S29 (research 36): the only (offset, value) pairs a CFG window write may have (twin of wlanlink.c cfg_ok without the state): all ones (sizing), the observed originals (low 0x4, high 0; sizing restore + teardown), the assigned
# addresses, Command 0 / 0x2 (MEM only, never MASTER), PCI_BAR0_WIN 0x18000000 / its observed original 0x18001000 / the S32 EROM page 0x18105000 (research 37 s.9.1 run B; eromptr observed in run w3a-n31): the window is only ever one of these three; S33 (research 38): the four chain pages PMU 0x18012000, ARM CA7 wrapper 0x18120000, SYS_MEM wrapper 0x18126000, SYS_MEM core 0x18026000 (EROM bases observed in run w3a-n32b)
_WLAN_CFG_OK = {(0x10, 0xFFFFFFFF), (0x10, 0x4), (0x10, 0x60000004), (0x14, 0xFFFFFFFF), (0x14, 0), (0x18, 0xFFFFFFFF), (0x18, 0x4), (0x18, 0x60400004), (0x1C, 0xFFFFFFFF), (0x1C, 0),
                (0x04, 0x2), (0x04, 0), (0x80, 0x18000000), (0x80, 0x18001000), (0x80, 0x18105000), (0x80, 0x18012000), (0x80, 0x18120000), (0x80, 0x18126000), (0x80, 0x18026000)}
_WLAN_S28_TEAR = ((0x10, 4), (0x14, 0), (0x18, 4), (0x1C, 0))	# the BAR originals observed in run w3a-n27: FIRST in the teardown, before the ATU disable (the CFG window dies with it)
_WLAN_ELBI_OK = {0x054, 0x080, 0x388, 0x3A0, 0x3A4, 0x3A8, 0x3BC, 0x474, 0x1078, 0x1400, 0x1404, 0x1408}
_WLAN_HDR_ROW = re.compile(r"\{ (\d+)u, WLK_(\w+), WLB_(\w+), (0x[0-9a-f]+)u, (0x[0-9a-f]+)u, (0x[0-9a-f]+)u, (\d+)u, (\d+)u \},")


def _wlan_boards():
    import importlib.util
    spec = importlib.util.spec_from_file_location("boards", pathlib.Path(__file__).resolve().parent / "boards.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wlan_row_allowed(blk, off, mask, value=None) -> bool:
    """The Python twin of wlanlink.c w32_allowed (windows, per-block rules, pin masks), for the TABLE audit."""
    if blk in ("GPP14", "GPH2"):
        w = {0x00: 4, 0x08: 4, 0x0C: 4, 0x14: 4, 0x04: 1, 0x10: 2}.get(off)
        if w is None or off >= _WLAN_WIN[blk]:
            return False
        pins = (1 << 2) if blk == "GPP14" else (1 << 0) | (1 << 1)
        allowed = sum(((1 << w) - 1) << (p * w) for p in range(8) if pins & (1 << p))
        return mask != 0 and (mask & ~allowed) == 0
    if blk in ("PMA", "PCS"):
        return off % 4 == 0 and off < _WLAN_WIN[blk]
    if blk == "IA":
        return off % 4 == 0 and off < 0x180
    if blk == "ELBI":
        return off in _WLAN_ELBI_OK
    if blk == "DBI":
        return off == 0x890
    if blk == "ATU":
        return mask == FULL_MASK and (off, value) in _WLAN_ATU_OK
    if blk == "CFG":
        return mask == FULL_MASK and (off, value) in _WLAN_CFG_OK
    return False


def _wlan_mem_write_windows(rows):
    """[(row, window)] for the BAR0 write rows: the window is the last cfg 0x80 write before the row (the table twin of the live window select)."""
    win, out = None, []
    for x in rows:
        if x["blk"] == "CFG" and x["off"] == 0x80 and x["kind"] == "mmio_write":
            win = x["value"]
        if x["blk"] == "MEM" and x["kind"] in _WLAN_WRITE_KINDS:
            out.append((x, win))
    return out


def check_wlan_seq_table(doc: dict, header: str, pin: str | None = _WLAN_SEQ_SHA, name: str = "wlan_seq.json") -> None:
    import hashlib
    rows, tear = doc["rows"], doc["teardown"]
    sha = hashlib.sha256(json.dumps({"rows": rows, "teardown": tear}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if sha != doc["table_sha256"]:
        raise AssertionError(f"{name}: table_sha256 does not match the rows")
    if pin is not None and sha != pin:
        raise AssertionError(f"{name}: the table hash {sha} is not the audited {pin}: a changed table needs a new review and a new pin in tools/test-bluejay-boot.py")
    # header = table (the allowlist the interpreter uses is exactly the audited table)
    hdr = _WLAN_HDR_ROW.findall(header)
    want = [(str(r["step"]), r["kind"].upper(), r["blk"] or "NONE", hex(r["off"]), hex(r["mask"]), hex(r["value"]),
             str(r["delay_us"] if r["kind"] in ("delay", "wait_eq", "wait_mask") else (1 if r.get("probe") else 0) | (2 if r.get("log") else 0) | (4 if r.get("eot") else 0) | (8 if r.get("eot") == 2 else 0)), str(r["polls"])) for r in rows]
    want += [(str(t["requires"]), t["kind"].upper(), t["blk"] or "NONE", hex(t["off"]), hex(t["mask"]), hex(t["value"]), str(t["delay_us"]), str(t["polls"])) for t in tear]
    if hdr != want or f'WLAN_SEQ_SHA256 "{sha}"' not in header:
        raise AssertionError(f"{name}: out/wlan_seq.h differs from the JSON table")
    # coverage
    steps = sorted({r["step"] for r in rows})
    if steps != list(range(0, 37)) or any(u["step"] == 22 for u in doc["unknown"]) is False or doc["last_step"] != 36 or doc["default_last_step"] != 19:
        raise AssertionError(f"{name}: step coverage must be S00..S36 with S22 a documented skip (unknown text kept), last 36, default 19: {steps}")
    # S22 (RC setup) is a DOCUMENTED SKIP (driver decision 2026-10-23): exactly one row, a 0 us delay, no write, tagged guess, saying so; its blocker text stays in `unknown`
    s22 = [r for r in rows if r["step"] == 22]
    if len(s22) != 1 or s22[0]["kind"] != "delay" or s22[0]["delay_us"] != 0 or s22[0]["tag"] != "guess" or s22[0]["blk"] is not None or "deliberately skipped" not in s22[0].get("note", "") \
            or "NOT enumerable" not in s22[0]["note"]:
        raise AssertionError(f"{name}: step coverage: S22 must be exactly one 0 us delay row tagged guess with the skip note, no write: {s22}")
    # S23: LTSSM enable (ELBI 0x54 = 1) then the L0 wait on ELBI 0x2C8 (mask 0x3F == 0x11), MAX_TIMEOUT (common.h:20) = 4800 polls of <= 12 us: a bounded wait that ends as `timeout`
    s23 = [(r["kind"], r["blk"], r["off"], r["mask"], r["value"], r["delay_us"], r["polls"]) for r in rows if r["step"] == 23]
    if s23 != [("mmio_write", "ELBI", 0x54, FULL_MASK, 1, 0, 0), ("wait_eq", "ELBI", 0x2C8, 0x3F, 0x11, 12, 4800)]:
        raise AssertionError(f"{name}: S23 must be exactly LTSSM enable (ELBI 0x54 = 1) and the L0 wait (ELBI 0x2C8 & 0x3F == 0x11, 4800 polls x 12 us): {s23}")
    # a block that needs a probe (PMA PCS ELBI IA DBI ATU CFG MEM) is never touched by a row before its probe row (the interpreter refuses it at run time; this is the table twin)
    seen = set()
    for r in rows:
        if r["blk"] in ("PMA", "PCS", "ELBI", "IA", "DBI", "ATU", "CFG", "MEM"):
            if r.get("probe"):
                seen.add(r["blk"])
            elif r["blk"] not in seen:
                raise AssertionError(f"{name}: row {r['id']} (S{r['step']:02d}) touches {r['blk']} before its probe row")
    # S24: the READ-ONLY link report (explicit --wlan-link=24 only): the pinned DBI / ELBI offsets, every row read_only + log (a = 2, never a probe), no write kind, no restore, nothing else in the step
    s24 = [(r["kind"], r["blk"], r["off"], r.get("log"), r.get("probe"), r["restore"]) for r in rows if r["step"] == 24]
    s24_offs = [("DBI", o) for o in (0x000, 0x004, 0x008, 0x034, 0x040, 0x070, 0x07C, 0x080, 0x0A0, 0x710, 0x80C, 0x890)] + [("ELBI", 0x2C8)]
    if s24 != [("read_only", b, o, True, None, None) for b, o in s24_offs]:
        raise AssertionError(f"{name}: S24 must be exactly the 13 read_only log rows {s24_offs}, no write, no probe, no restore: {s24}")
    # S25: READ-ONLY iATU layout (explicit --wlan-link=25 only): DBI words, then the ATU block probe (a guarded read, not logged), then outbound region 0 CR1..UPPER_TARGET, then ELBI 0x2C8; all log, no write, no restore
    s25 = [(r["kind"], r["blk"], r["off"], r.get("log"), r.get("probe"), r["restore"]) for r in rows if r["step"] == 25]
    s25_want = [("read_only", "DBI", o, True, None, None) for o in (0x018, 0x03C, 0x8BC, 0x8F8, 0x8FC, 0x900)] + [("read_only", "ATU", 0x000, None, True, None)] + \
               [("read_only", "ATU", o, True, None, None) for o in (0x000, 0x004, 0x008, 0x00C, 0x010, 0x014, 0x018)] + [("read_only", "ELBI", 0x2C8, True, None, None)]
    if s25 != s25_want:
        raise AssertionError(f"{name}: S25 must be exactly the DBI words, the ATU probe, ATU 0x000..0x018 and ELBI 0x2C8, read_only, logged (the probe not), no write, no restore: {s25}")
    # S26: the iATU region 0 write step (explicit --wlan-link=26 only): the seven ATU writes in Linux's order (CR2 enable LAST), each full-word, each followed by a logged read-back of the same register, then the
    # CFG window probe (a guarded read, not logged: its block is the first CFG access) and logged reads of window +0x00/+0x04/+0x08/+0x0C; nothing else. Each write's restore is the teardown row for the same ATU offset.
    tid = {}
    for t in tear:
        if t["blk"] == "ATU":
            tid.setdefault(("ATU", t["off"]), t["id"])	# the FIRST teardown row of an offset (a later duplicate is caught by the teardown-head audit)
    s26 = [(r["kind"], r["blk"], r["off"], r["value"] if r["kind"] == "mmio_write" else None, r.get("log"), r.get("probe"), r["restore"]) for r in rows if r["step"] == 26]
    s26_want = []
    for o, v in _WLAN_S26_WRITES:
        s26_want += [("mmio_write", "ATU", o, v, None, None, tid.get(("ATU", o))), ("read_only", "ATU", o, None, True, None, None)]
    s26_want += [("wait_eq", "ATU", 0x004, None, None, None, None)]	# review 7 finding 2: ONE-SHOT CR2 == 0x80000000 check, so the CFG probe is never issued through a region that did not latch
    s26_want += [("read_only", "CFG", 0x000, None, None, True, None)] + [("read_only", "CFG", o, None, True, None, None) for o in (0x000, 0x004, 0x008, 0x00C)]
    s26_chk = [(r["mask"], r["value"], r["delay_us"], r["polls"]) for r in rows if r["step"] == 26 and r["kind"] == "wait_eq"]
    if s26 != s26_want or s26_chk != [(FULL_MASK, 0x80000000, 0, 1)] or any(r["kind"] == "mmio_write" and r["mask"] != FULL_MASK for r in rows if r["step"] == 26):
        raise AssertionError(f"{name}: S26 must be exactly the 7 ATU full-word writes {[(hex(o), hex(v)) for o, v in _WLAN_S26_WRITES]} each followed by its logged read-back, then ONE one-shot wait_eq ATU CR2 == 0x80000000, then the CFG probe and the CFG reads +0x0/+0x4/+0x8/+0xC, each write restored by the teardown row of its offset: {s26}")
    # the CFG window is written ONLY by S28, S29, S32, S33..S36 (the window moves, nothing else) and their teardown rows (the allowlist twin checks offset and value; this checks the place): S26, S27, S30 and S31 only read it, nothing else touches it
    def _cfg_write_bad(x):
        st = x.get("step", x.get("requires"))
        if st not in (28, 29, 32, 33, 34, 35, 36) or x["kind"] != "mmio_write":
            return True
        if st == 32 and (x["off"], x["value"]) not in ((0x80, 0x18105000), (0x80, 0x18000000)):
            return True
        if x.get("step") == 33 and (x["off"], x["value"]) not in tuple((0x80, c) for c in _WLAN_S33_CHAIN):
            return True
        return x.get("step") in (34, 35, 36) and (x["off"], x["value"]) not in tuple((0x80, c) for c in _WLAN_S34_WINDOWS[x["step"]])
    if any(x["blk"] == "CFG" and x["kind"] in _WLAN_WRITE_KINDS and _cfg_write_bad(x) for x in rows + tear) \
            or any(x["blk"] == "CFG" and x["kind"] in ("wait_eq", "wait_mask") and x["step"] not in (28, 29, 31, 32, 33, 34, 35, 36) for x in rows) or any(x["blk"] == "CFG" and x.get("step") not in (26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36) for x in rows) \
            or any(x["blk"] == "CFG" and not ((x["kind"] == "mmio_write" and x["requires"] in (28, 29, 32)) or (x["kind"] == "wait_eq" and x["requires"] == 32)) for x in tear):
        raise AssertionError(f"{name}: the CFG window is read-only except S28 (BAR sizing/assignment), S29 (Command, BAR0 window), S32 (the BAR0 window move to the EROM page), S33-S36 (the window moves of the chip steps) and their teardown rows; only S26-S36 touch it")
    # BAR0 (MEM, research 36 / 37): read-only, never in the teardown, no write kind anywhere; S30 reads offset 0 only, S31 (run A) reads ChipCommon 0x00 (chip id re-check), 0xAC, 0xFC, 0x1E0 only; S32 (run B) re-reads 0xFC through the ChipCommon window,
    # then (window 0x18105000) the EROM dwords 0x000..0xDFC, nothing from ER_REMAPCONTROL 0xE00 on (aidmp.h, review 13 M1)
    # S33..S36 (research 38 s.3, research 40 s.5): the exact offsets of the window the table has selected so far (the last CFG 0x80 write before the row): _WLAN_MEM_OFFS; S34..S36 may also WRITE the exact (step, kind, window, offset, mask, value) tuples of _WLAN_MEM_WRITES and nothing else
    win_now, bad_mem = None, []
    for x in rows:
        if x["blk"] == "CFG" and x["off"] == 0x80 and x["kind"] == "mmio_write":
            win_now = x["value"]
        if x["blk"] == "MEM" and x["step"] >= 33:
            reads = x["kind"] in ("read_only", "wait_eq") or (x["step"] >= 34 and x["kind"] == "wait_mask")
            wrt = x["kind"] in _WLAN_WRITE_KINDS and (x["step"], x["kind"], win_now, x["off"], x["mask"], x["value"]) in _WLAN_MEM_WRITES
            if not ((reads and x["off"] in _WLAN_MEM_OFFS.get(win_now, ())) or wrt):
                bad_mem.append(x["id"])
    mem_ok = lambda x: (x["kind"] in ("read_only", "wait_eq") and (x["off"] in (0,) if x["step"] == 30 else x["off"] in (0, 0xAC, 0xFC, 0x1E0) if x["step"] == 31 else (x["off"] % 4 == 0 and 0 <= x["off"] < 0xE00) if x["step"] == 32 else False)) or (33 <= x["step"] <= 36 and x["id"] not in bad_mem)
    if any(x["blk"] == "MEM" and not mem_ok(x) for x in rows) or any(x["blk"] == "MEM" for x in tear):
        raise AssertionError(f"{name}: BAR0 (MEM) is read-only except the exact write tuples of S34-S36: S30 reads offset 0, S31 reads offsets 0/0xAC/0xFC/0x1E0 only, S32 reads dwords below 0xE00, S33-S36 only the exact offsets of their window (no teardown row, no other step)")
    if sorted((x["step"], x["kind"], w, x["off"], x["mask"], x["value"]) for x, w in _wlan_mem_write_windows(rows)) != sorted(_WLAN_MEM_WRITES):
        raise AssertionError(f"{name}: the BAR0 write rows must be exactly the {len(_WLAN_MEM_WRITES)} tuples of the write allowlist (S34 SRPWR, S35 SYS_MEM reset + bankidx, S36 ARM CA7 halt): nothing missing, nothing extra")
    # S27: the READ-ONLY endpoint config dump (explicit --wlan-link=27 only): the pinned CFG window offsets, then ATU outbound region 1 CR1..UPPER_TARGET; every row read_only + log (a = 2, never a probe), no write, no restore, nothing else
    if any(r["step"] == 27 and r["blk"] == "CFG" and 0x8C <= r["off"] <= 0xA8 for r in rows):
        raise AssertionError(f"{name}: no S27 CFG row may lie in 0x8C..0xA8 (0xA4 PCI_BACKPLANE_DATA is a backplane access; 0xAC is allowed)")
    s27 = [(r["kind"], r["blk"], r["off"], r.get("log"), r.get("probe"), r["restore"]) for r in rows if r["step"] == 27]
    s27_want = [("read_only", "CFG", o, True, None, None) for o in _WLAN_S27_CFG] + [("read_only", "ATU", o, True, None, None) for o in _WLAN_S27_ATU]
    if s27 != s27_want or any(r["kind"] != "read_only" or r["mask"] != FULL_MASK or r["delay_us"] or r["polls"] for r in rows if r["step"] == 27):
        raise AssertionError(f"{name}: S27 must be exactly the {len(_WLAN_S27_CFG)} CFG window reads and the 7 ATU region 1 reads, read_only, logged, no probe, no write, no restore: {s27}")
    # S28: BAR0/BAR2 sizing + assignment (research 33): Command precondition, then per BAR (low, high): original check, all ones, [guard + log], original back; then the two assignments with logged read-backs. Pinned independently of the generator.
    BIG = 0xFFFFFFFF
    s28_want = [("wait_eq", 0x04, 0xFFFF, 0, None)]
    for lo, orig, guard in ((0x10, 4, 0xFFC00000), (0x18, 4, 0xFFC00000)):
        hi = lo + 4
        s28_want += [("wait_eq", lo, BIG, orig, None), ("wait_eq", hi, BIG, 0, None), ("mmio_write", lo, BIG, BIG, lo), ("wait_mask", lo, guard, guard, None), ("read_only", lo, BIG, 0, None), ("mmio_write", lo, BIG, orig, lo),
                     ("mmio_write", hi, BIG, BIG, hi), ("wait_mask", hi, BIG, BIG, None), ("read_only", hi, BIG, 0, None), ("mmio_write", hi, BIG, 0, hi)]
    for lo, addr in ((0x10, 0x60000004), (0x18, 0x60400004)):
        s28_want += [("mmio_write", lo, BIG, addr, lo), ("wait_eq", lo, BIG, addr, None), ("wait_eq", lo + 4, BIG, 0, None)]
    tkey = {t["off"]: t["id"] for t in tear if t["blk"] == "CFG"}
    s28 = [(r["kind"], r["off"], r["mask"], r["value"], r["restore"]) for r in rows if r["step"] == 28]
    if s28 != [(k, o, m, v, tkey.get(rs) if rs else None) for k, o, m, v, rs in s28_want] or any(r["blk"] != "CFG" or r.get("probe") or bool(r.get("log")) != (r["kind"] == "read_only") or (r["kind"] in ("wait_eq", "wait_mask")) != (r["polls"] == 1 and r["delay_us"] == 0) or (r["kind"] not in ("wait_eq", "wait_mask") and (r["polls"] or r["delay_us"])) for r in rows if r["step"] == 28):
        raise AssertionError(f"{name}: S28 must be exactly the Command==0 check, the sizing sequence of BAR0 (0x10/0x14) and BAR2 (0x18/0x1C) with their one-shot guards and the two assignments 0x60000004 / 0x60400004, never Command (+0x04) as a write, each all-ones write restored by the teardown BAR row of its offset: {s28}")
    # S29 (research 36): preconditions (reads), the four region 1 writes (base, limit, target, ENABLE last; CR1 / UPPER_* are NOT written: precondition reads), PCI_BAR0_WIN := 0x18000000, three logs, then the ARM: Command := 0x2 (MEM only, never MASTER)
    # with a one-shot read-back. No MEM row (BAR0 is S30's). Each write is restored by the teardown row of its register. Pinned independently of the generator.
    t29 = {(t["blk"], t["off"]): t["id"] for t in tear if t["requires"] == 29}
    s29_want = [("wait_eq", "CFG", 0x04, 0xFFFF, 0, None), ("wait_eq", "CFG", 0x10, BIG, 0x60000004, None), ("wait_eq", "CFG", 0x14, BIG, 0, None), ("wait_eq", "CFG", 0x80, BIG, 0x18001000, None),
                ("wait_eq", "ATU", 0x204, 0x80000000, 0, None), ("wait_eq", "ATU", 0x200, BIG, 0, None), ("wait_eq", "ATU", 0x20C, BIG, 0, None), ("wait_eq", "ATU", 0x218, BIG, 0, None)]
    for o, v in _WLAN_S29_WRITES:
        s29_want += [("mmio_write", "ATU", o, BIG, v, t29.get(("ATU", o))), ("wait_eq", "ATU", o, BIG, v, None)]
    s29_want += [("mmio_write", "CFG", 0x80, BIG, 0x18000000, t29.get(("CFG", 0x80))), ("wait_eq", "CFG", 0x80, BIG, 0x18000000, None), ("read_only", "CFG", 0x88, BIG, 0, None), ("wait_eq", "CFG", 0x88, BIG, 0x00080080, None), ("read_only", "CFG", 0xBC, BIG, 0, None), ("wait_eq", "CFG", 0xBC, 0xFFFF0000, 0x10110000, None),
                 ("read_only", "CFG", 0x104, BIG, 0, None), ("mmio_write", "CFG", 0x04, BIG, 0x2, t29.get(("CFG", 0x04))), ("wait_eq", "CFG", 0x04, 0xFFFF, 0x2, None)]
    s29 = [(r["kind"], r["blk"], r["off"], r["mask"], r["value"], r["restore"]) for r in rows if r["step"] == 29]
    if s29 != s29_want or any(r.get("probe") or bool(r.get("log")) != (r["kind"] == "read_only") or (r["kind"] == "wait_eq") != (r["polls"] == 1 and r["delay_us"] == 0) or (r["kind"] != "wait_eq" and (r["polls"] or r["delay_us"])) for r in rows if r["step"] == 29):
        raise AssertionError(f"{name}: S29 must be exactly the preconditions, the region 1 writes {[(hex(o), hex(v)) for o, v in _WLAN_S29_WRITES]} (each full-word, one-shot read-back, restored by the teardown row of its register), PCI_BAR0_WIN := 0x18000000, the three logs and the ARM Command := 0x2 (MEM only, never MASTER) with its one-shot read-back: {s29}")
    # S30 (research 36): the probe read of BAR0+0 (the first MEM TLP), the logged copy, the link / AER logs, and the one-shot id check (low 16 bits == 0x4389). No write, no restore.
    s30_want = [("read_only", "MEM", 0, BIG, 0, True, None), ("read_only", "MEM", 0, BIG, 0, None, True), ("read_only", "CFG", 0xBC, BIG, 0, None, True), ("read_only", "CFG", 0x104, BIG, 0, None, True), ("wait_eq", "MEM", 0, 0xFFFF, 0x4389, None, None)]
    s30 = [(r["kind"], r["blk"], r["off"], r["mask"], r["value"], r.get("probe"), r.get("log")) for r in rows if r["step"] == 30]
    if s30 != s30_want or any(r["restore"] or (r["kind"] == "wait_eq") != (r["polls"] == 1 and r["delay_us"] == 0) or (r["kind"] != "wait_eq" and (r["polls"] or r["delay_us"])) for r in rows if r["step"] == 30):
        raise AssertionError(f"{name}: S30 must be exactly the MEM probe read, the logged MEM read, the CFG 0xBC / 0x104 logs and the one-shot chip id check (mask 0xFFFF == 0x4389), no write, no restore: {s30}")
    # S31 (research 37 s.9.1 run A, READ-ONLY): the window precondition (cfg 0x80 == 0x18000000, no move), the three logged ChipCommon reads (eromptr 0xFC, clk_ctl_st 0x1E0, capabilities_ext 0xAC), the link / AER logs, then the one-shot
    # eromptr check (4 KiB aligned inside 0x18000000..0x1FFFFFFF: mask 0xF8000FFF == 0x18000000) and the chip id re-check. No write of any kind, no restore, no probe.
    s31_want = [("wait_eq", "CFG", 0x80, BIG, 0x18000000, None, None), ("read_only", "MEM", 0xFC, BIG, 0, None, True), ("read_only", "MEM", 0x1E0, BIG, 0, None, True), ("read_only", "MEM", 0xAC, BIG, 0, None, True),
                ("read_only", "CFG", 0xBC, BIG, 0, None, True), ("read_only", "CFG", 0x104, BIG, 0, None, True), ("wait_eq", "MEM", 0xFC, 0xF8000FFF, 0x18000000, None, None), ("wait_eq", "MEM", 0, BIG, 0x10034389, None, None)]
    s31 = [(r["kind"], r["blk"], r["off"], r["mask"], r["value"], r.get("probe"), r.get("log")) for r in rows if r["step"] == 31]
    if s31 != s31_want or any(r["restore"] or r["kind"] in _WLAN_WRITE_KINDS or (r["kind"] == "wait_eq") != (r["polls"] == 1 and r["delay_us"] == 0) or (r["kind"] != "wait_eq" and (r["polls"] or r["delay_us"])) for r in rows if r["step"] == 31) or any(t["requires"] == 31 for t in tear):
        raise AssertionError(f"{name}: S31 must be exactly the READ-ONLY run A rows (window precondition, MEM reads 0xFC/0x1E0/0xAC, CFG 0xBC/0x104 logs, eromptr and chip id one-shot checks), no write, no restore, no teardown row: {s31}")
    # S32 (research 37 s.9.1 run B): window precondition (cfg 0x80 == 0x18000000), the eromptr pin (MEM 0xFC == exactly 0x18105000, read through the 0x18000000 window), THE one write (cfg 0x80 := 0x18105000, full word, restored by the first
    # teardown row) with its one-shot read-back, 896 logged read_only MEM dwords at offsets 0, 4, .. 0xDFC in ascending order (a first-dword check after dword 0: mask 0xF == 0x1 = a COMPONENT descriptor, refuses all-ones and zeros; review 13 L2), then the CFG 0xBC / 0x104 logs. Nothing else.
    s32_want = [("wait_eq", "CFG", 0x80, BIG, 0x18000000, None, None, None), ("wait_eq", "MEM", 0xFC, BIG, 0x18105000, None, None, None), ("mmio_write", "CFG", 0x80, BIG, 0x18105000, None, None, "T01"), ("wait_eq", "CFG", 0x80, BIG, 0x18105000, None, None, None),
                ("read_only", "MEM", 0, BIG, 0, None, True, None), ("wait_eq", "MEM", 0, 0xF, 0x1, None, None, None)] + [("read_only", "MEM", 4 * i, BIG, 0, None, True, None) for i in range(1, 0xE00 // 4)] + \
               [("read_only", "CFG", 0xBC, BIG, 0, None, True, None), ("read_only", "CFG", 0x104, BIG, 0, None, True, None)]
    s32 = [(r["kind"], r["blk"], r["off"], r["mask"], r["value"], r.get("probe"), r.get("log"), r["restore"]) for r in rows if r["step"] == 32]
    if s32 != s32_want or any((r["kind"] == "wait_eq") != (r["polls"] == 1 and r["delay_us"] == 0) or (r["kind"] != "wait_eq" and (r["polls"] or r["delay_us"])) for r in rows if r["step"] == 32):
        raise AssertionError(f"{name}: S32 must be exactly the window precondition, the eromptr pin 0x18105000, the ONE window write cfg 0x80 := 0x18105000 (restored by teardown T01) with its one-shot read-back, 896 logged MEM dwords 0..0xDFC ascending with the first-dword check, and the CFG 0xBC/0x104 logs: {[x for x in s32 if x not in s32_want][:3]}")
    # W-C1c: the dump stops at the first EOT dword 0xF (observed 0-based dword 217, row 637 in run w3a-n32; that reads past it hung the phone is a guess, the run was most likely the host --timeout 600): exactly the 896 logged S32 MEM dump rows carry eot = 1 and the LAST one eot = 2 (refuses a dump
    # that never reached an EOT), no other row anywhere has the flag
    s32_dump = [r for r in rows if r["step"] == 32 and r["blk"] == "MEM" and r["kind"] == "read_only"]
    if [r.get("eot", 0) for r in s32_dump] != [1] * (0xE00 // 4 - 1) + [2] or any("eot" in x for x in rows if x["step"] != 32 or x not in s32_dump) or any("eot" in t for t in tear):
        raise AssertionError(f"{name}: the S32 EROM dump rows must carry eot = 1 (stop after a 0xF dword) and the last one eot = 2 (refuse without an EOT); no other row has the flag")
    # S33 (research 38, trimmed by research 40: READ-ONLY chip state): the precondition (window == the S32 end state 0x18105000), then per window of the chain (PMU, ARM CA7 wrapper, SYS_MEM wrapper): the move (full word, restored by the S32 teardown row T01) with its one-shot read-back and the exact
    # reads; the pmurev guard (0x604 & 0xFF == 0x28) AFTER the PMU logs; the RESET_CTL upper-bit guards; the link / AER logs. NO SYS_MEM-up gate and no SYS_MEM core page any more (SYS_MEM is down at power-up, observed run w3a-n33; both moved to S35). Pinned independently of the generator.
    tw = tear[0]["id"]
    BIGW = FULL_MASK
    mv = lambda v: [("mmio_write", "CFG", 0x80, BIGW, v, None, None, tw, 0, 0), ("wait_eq", "CFG", 0x80, BIGW, v, None, None, None, 0, 1)]
    lg = lambda o: ("read_only", "MEM", o, BIGW, 0, None, True, None, 0, 0)
    cfglogs = [("read_only", "CFG", 0xBC, BIGW, 0, None, True, None, 0, 0), ("read_only", "CFG", 0x104, BIGW, 0, None, True, None, 0, 0)]
    w1 = lambda blk, off, mask, v, us=0, polls=1: ("wait_eq", blk, off, mask, v, None, None, None, us, polls)
    s33_want = [w1("CFG", 0x80, BIGW, 0x18105000)] + mv(0x18012000) + [lg(o) for o in (0x604, 0x600, 0x608, 0x618, 0x61C)] + [w1("MEM", 0x604, 0xFF, 0x28)] + \
               mv(0x18120000) + [lg(o) for o in (0x408, 0x800, 0x804)] + [w1("MEM", 0x800, BIGW, 1)] + \
               mv(0x18126000) + [lg(o) for o in (0x408, 0x800, 0x804)] + [w1("MEM", 0x800, 0xFFFFFFFE, 0)] + cfglogs
    key9 = lambda r: (r["kind"], r["blk"], r["off"], r["mask"], r["value"], r.get("probe"), r.get("log"), r["restore"], r["delay_us"], r["polls"])
    s33 = [key9(r) for r in rows if r["step"] == 33]
    if s33 != s33_want or any(t["requires"] >= 33 for t in tear):
        raise AssertionError(f"{name}: S33 must be exactly the window precondition, the three chain moves {[hex(c) for c in _WLAN_S33_CHAIN]} (each restored by teardown T01, each read back once), the exact reads per window, the pmurev and RESET_CTL guards and the logs (no SYS_MEM-up gate, no core page); no teardown row of its own: {[x for x in s33 if x not in s33_want][:3]} / missing {[x for x in s33_want if x not in s33][:3]}")
    # S34 (research 40 s.4, the FIRST BAR0 write): precondition (window == the S33 end state 0x18126000), the move to the PCIe core page 0x18001000, powerctl logged, the SRPWR request (RMW bit 0x200 of BAR0+0x1E8), its one-shot read-back, the status wait (bit 0x20000, 150 x 100 us = 15 ms), powerctl logged, the link / AER logs
    wr_ = lambda blk, off, v, kind="mmio_write", mask=BIGW: (kind, blk, off, mask, v, None, None, None, 0, 0)
    dl = lambda us: ("delay", None, 0, BIGW, 0, None, None, None, us, 0)
    ws = lambda off, mask, v, us, polls: ("wait_eq", "MEM", off, mask, v, None, None, None, us, polls)

    def ai_reset(want):
        fin = want & ~2
        return [ws(0x804, BIGW, 0, 10, 30), wr_("MEM", 0x800, 1), dl(10), ws(0x800, 1, 1, 0, 1), ws(0x804, BIGW, 0, 10, 30), wr_("MEM", 0x408, want), ws(0x408, 0x3FFF, want, 0, 1), ws(0x804, BIGW, 0, 10, 30), ws(0x804, BIGW, 0, 10, 30),
                wr_("MEM", 0x800, 0), ws(0x804, BIGW, 0, 10, 30), ws(0x800, 1, 0, 10, 10), wr_("MEM", 0x408, fin), ws(0x408, 0x3FFF, fin, 0, 1), dl(1)]
    s34_want = [w1("CFG", 0x80, BIGW, 0x18126000)] + [("mmio_write", "CFG", 0x80, BIGW, 0x18001000, None, None, tw, 0, 0), w1("CFG", 0x80, BIGW, 0x18001000)] + [lg(0x1E8), wr_("MEM", 0x1E8, 0x200, "mmio_rmw", 0x200), w1("MEM", 0x1E8, 0x200, 0x200),
                ("wait_mask", "MEM", 0x1E8, 0x20000, 0x20000, None, None, None, 100, 150), lg(0x1E8)] + cfglogs
    # S35: the window move to the SYS_MEM wrapper, the pre-state logs, DHD's _ai_core_reset(0, 0) unrolled once (every write has a read-back or a wait that stops the run), the post-state logs, the SYS_MEM-up gate (IOCTL & 3 == 1, RESET_CTL & 1 == 0, RESET_ST == 0: one-shot) BEFORE the move to the core
    # page, coreinfo, bankidx := 0 (BAR0+0x10), the 10 us delay, bankidx / bankinfo logs and the bankidx read-back (after the logs: a mismatch loses no data)
    s35_want = mv(0x18126000) + [lg(o) for o in (0x408, 0x800, 0x804)] + ai_reset(0x3) + [lg(o) for o in (0x408, 0x800, 0x804)] + [w1("MEM", 0x408, 3, 1), w1("MEM", 0x800, 1, 0), w1("MEM", 0x804, BIGW, 0)] + \
               mv(0x18026000) + [lg(0), wr_("MEM", 0x10, 0), dl(10), lg(0x10), lg(0x40), w1("MEM", 0x10, BIGW, 0)] + cfglogs
    # S36: the window move to the ARM CA7 wrapper, the pre-state logs, DHD's si_core_reset(CPUHALT, CPUHALT) = _ai_core_reset with IOCTL 0x23 then 0x21, the post-state logs, the link / AER logs: the LAST chip write of the series
    s36_want = mv(0x18120000) + [lg(o) for o in (0x408, 0x800, 0x804)] + ai_reset(0x23) + [lg(o) for o in (0x408, 0x800, 0x804)] + cfglogs
    for stp, want_s in ((34, s34_want), (35, s35_want), (36, s36_want)):
        got = [key9(r) for r in rows if r["step"] == stp]
        if got != want_s or any(t["requires"] == stp for t in tear):
            raise AssertionError(f"{name}: S{stp} must be exactly the audited write sequence of research 40 s.4 (windows {[hex(c) for c in _WLAN_S34_WINDOWS[stp]]}, every write with its read-back / wait, the up gate before the core page, bankidx only 0, the ARM halt last), no teardown row of its own: {[x for x in got if x not in want_s][:3]} / missing {[x for x in want_s if x not in got][:3]}")
    if any(x["blk"] == "ATU" and x["kind"] in _WLAN_WRITE_KINDS and x.get("step", x.get("requires")) not in (26, 29) for x in rows + tear):
        raise AssertionError(f"{name}: ATU writes are only for S26, S29 and their teardown")
    if any(r.get("log") and (r["kind"] != "read_only" or r.get("probe") or r["step"] not in (24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36)) for r in rows + tear) or any("log" in t for t in tear):
        raise AssertionError(f"{name}: the log flag is only for the non-probe read_only rows of S24..S36")
    if [r["step"] for r in rows] != sorted(r["step"] for r in rows) or [r["id"] for r in rows] != list(range(len(rows))):
        raise AssertionError(f"{name}: rows must be in step order with sequential ids")
    # the default run (N = 19) must not release PERST nor enable the LTSSM
    for r in rows:
        if r["step"] <= doc["default_last_step"] and r["kind"] in _WLAN_WRITE_KINDS:
            if r["blk"] == "GPH2" and r["off"] == 4 and (r["mask"] & r["value"] & 1):
                raise AssertionError(f"{name}: row {r['id']} (S{r['step']:02d}) raises PERST inside the default run")
            if r["blk"] == "ELBI" and r["off"] == 0x54:
                raise AssertionError(f"{name}: row {r['id']} enables the LTSSM inside the default run")
    if not any(r["step"] == 20 and r["blk"] == "GPH2" and r["off"] == 4 and r["value"] & 1 for r in rows):
        raise AssertionError(f"{name}: S20 must be the PERST release")
    # every write is inside the allowlist; every write kind has bounded parameters; every row has a cite and a valid tag
    wr = [x for x in rows + tear if x["kind"] in _WLAN_WRITE_KINDS]
    for x in wr:
        if x["blk"] == "MEM" and x["step"] in (34, 35, 36):
            continue		# validated above against the exact (step, kind, window, offset, mask, value) tuples of _WLAN_MEM_WRITES
        if not _wlan_row_allowed(x["blk"], x["off"], x["mask"], x["value"]) or (x["kind"] == "mmio_write" and x["mask"] != 0xFFFFFFFF) or (x["blk"] == "ATU" and x["kind"] != "mmio_write"):  # twin of w32: a full write needs a whole-word mask
            raise AssertionError(f"{name}: {x['id']} writes {x['blk']}+{x['off']:#x} mask {x['mask']:#x}, outside the allowlist")
    for x in rows + tear:
        if not x["cite"] or x["tag"] not in ("read from source", "observed", "guess") or x["delay_us"] > 250000 or x["polls"] > 20000:
            raise AssertionError(f"{name}: {x['id']}: cite/tag/bounds")
        if x["kind"] in ("wait_eq", "wait_mask") and x["polls"] < 1:
            raise AssertionError(f"{name}: {x['id']}: a wait needs a poll count")
        if x["kind"] == "smc_iso" and (x["blk"], x["off"], x["mask"], x["value"] & ~1) != ("PMU", 0x3EC4, 1, 0):
            raise AssertionError(f"{name}: {x['id']}: the only SMC target is PMU 0x3EC4 mask 1 value 0/1")
    if any(x["kind"] == "smc_iso" for x in rows) is False or sum(1 for x in tear if x["kind"] == "smc_iso") != 1 or [x["kind"] for x in tear if x["requires"] == 5] != ["smc_iso"]:
        raise AssertionError(f"{name}: exactly one isolation clear in the teardown")
    last_t = max(i for i, x in enumerate(tear) if x["kind"] == "smc_iso")
    if any(x["blk"] in ("PMA", "PCS", "ELBI", "IA", "DBI", "ATU", "CFG") for x in tear[last_t + 1:]):
        raise AssertionError(f"{name}: the isolation clear comes after every PHY/controller access (PMA PCS ELBI IA DBI ATU CFG)")
    if any(t["kind"] == "read_only" for t in tear):
        raise AssertionError(f"{name}: no teardown row is read_only (run_row would read its delay_us as the log/eot flag bits, review 14 L2)")
    # the teardown: S29 first (region 1 DISABLE, Command := 0, BAR0 window := the observed original), then the 4 BAR rows (the CFG window dies with the region 0 disable), then the 3 region 1 resets, then the ATU region 0 is disabled (CR2 := 0) and its
    # registers go back to their reset values, all before PERST low (the first non-ATU row); no ATU / CFG row anywhere else
    n32, n_pre, n_cfg, n_a1, n_atu = len(_WLAN_S32_TEAR_HEAD), len(_WLAN_S29_TEAR_HEAD), len(_WLAN_S28_TEAR), len(_WLAN_S29_TEAR_ATU1), len(_WLAN_S26_TEAR)
    row_of = lambda t: (t["kind"], t["blk"], t["off"], t["value"], t["mask"], t["requires"])
    if [row_of(t) for t in tear[:n32]] != [(k, b, o, v, m, rq) for k, b, o, v, m, rq in _WLAN_S32_TEAR_HEAD] or [(t["delay_us"], t["polls"]) for t in tear[:n32]] != [(0, 0), (0, 1)]:
        raise AssertionError(f"{name}: the teardown must start with exactly the S32 rows {[(k, hex(v)) for k, _, _, v, _, _ in _WLAN_S32_TEAR_HEAD]} (the BAR0 window back to ChipCommon FIRST of all, with a one-shot read-back)")
    tear_all, tear = tear, tear[n32:]	# the S29 / S28 / S26 head checks below index the teardown after the S32 head
    if [row_of(t) for t in tear[:n_pre]] != [("mmio_write", b, o, v, FULL_MASK, 29) for b, o, v in _WLAN_S29_TEAR_HEAD]:
        raise AssertionError(f"{name}: the teardown must start with exactly the S29 rows {[(b, hex(o), hex(v)) for b, o, v in _WLAN_S29_TEAR_HEAD]} (region 1 disable FIRST, then Command := 0, then the BAR0 window back)")
    if [row_of(t) for t in tear[n_pre:n_pre + n_cfg]] != [("mmio_write", "CFG", o, v, FULL_MASK, 28) for o, v in _WLAN_S28_TEAR] or any(t["blk"] == "CFG" for t in tear[n_pre + n_cfg:]):
        raise AssertionError(f"{name}: the teardown must continue with exactly the 4 BAR rows {[(hex(o), hex(v)) for o, v in _WLAN_S28_TEAR]} (before the ATU region 0 disable: the CFG window dies with it) and have no other CFG row")
    if [row_of(t) for t in tear[n_pre + n_cfg:n_pre + n_cfg + n_a1]] != [("mmio_write", "ATU", o, v, FULL_MASK, 29) for o, v in _WLAN_S29_TEAR_ATU1]:
        raise AssertionError(f"{name}: the teardown must continue with exactly the 3 region 1 reset rows {[(hex(o), hex(v)) for o, v in _WLAN_S29_TEAR_ATU1]}")
    a0 = n_pre + n_cfg + n_a1
    if [row_of(t) for t in tear[a0:a0 + n_atu]] != [("mmio_write", "ATU", o, v, FULL_MASK, 26) for o, v in _WLAN_S26_TEAR] or any(t["blk"] == "ATU" for t in tear[a0 + n_atu:]):
        raise AssertionError(f"{name}: the teardown must continue (after the S29 and BAR rows) with exactly the 7 ATU rows {[(hex(o), hex(v)) for o, v in _WLAN_S26_TEAR]} (CR2 disable first) and have no other ATU row")
    t_p = tear[a0 + n_atu]
    if (t_p["requires"], t_p["blk"], t_p["off"], t_p["value"], t_p["kind"]) != (20, "GPH2", 4, 0, "gpio_dat"):
        raise AssertionError(f"{name}: the teardown continues with PERST low right after the ATU rows")
    tear = tear_all
    ids = {t["id"] for t in tear}
    # the BAR0 writes of S34-S36 have no teardown row: nothing is undone on the chip (the window is restored FIRST, T01/T02, and the module power is cut by the WLAN_EN / PERST rows; persistence across WLAN_EN low is a guess, research 40 s.5); their restore key must be None
    exempt = lambda r: (r["step"] == 7 and (r["off"] >= 8 or r["kind"] == "gpio_con" and r["restore"] is None)) or (r["blk"] == "MEM" and r["step"] in (34, 35, 36) and r["restore"] is None)
    for r in rows:
        if r["kind"] in _WLAN_WRITE_KINDS + ("smc_iso",) and r["restore"] not in ids and not exempt(r):
            raise AssertionError(f"{name}: row {r['id']} (S{r['step']:02d}) has no restore action")
    # the numbers the report quotes
    targets = {(x["blk"], x["off"]) for x in wr}
    counts = {"rows": len(rows), "teardown": len(tear), "write_rows": len(wr), "distinct_targets": len(targets)}
    per_blk = {}
    for b, _ in targets:
        per_blk[b] = per_blk.get(b, 0) + 1
    if pin is not None and (counts != _WLAN_SEQ_COUNTS or per_blk != _WLAN_SEQ_TARGET_BLOCKS):
        raise AssertionError(f"{name}: counts {counts} / {per_blk} differ from the audited {_WLAN_SEQ_COUNTS} / {_WLAN_SEQ_TARGET_BLOCKS}")


def wlan_seq_selftest() -> int:
    import copy
    root = pathlib.Path(__file__).resolve().parents[1]
    boards = _wlan_boards()
    doc = json.loads((root / "soc/google/gs101/wlan_seq.json").read_text())
    header = boards.wlan_seq_render(doc)
    check_wlan_seq_table(doc, header)  # control must pass

    def mut(fn, pin=None):
        d = copy.deepcopy(doc)
        fn(d)
        h = boards.wlan_seq_render(d) if pin is not False else header
        return d, h

    def add_row(d, **kw):
        base = {"id": len(d["rows"]), "step": 19, "kind": "mmio_write", "blk": "PMA", "off": 0, "mask": 0xFFFFFFFF, "value": 0, "delay_us": 0, "polls": 0, "restore": "T07", "cite": "x", "tag": "guess", "probe": False}
        base.update(kw)
        d["rows"].append(base)
        d["rows"].sort(key=lambda r: r["step"])  # stable: the mutation lands in step order, so the renderer's order check does not pre-empt the audit under test
        for n, r in enumerate(d["rows"]):
            r["id"] = n

    def resha(d):
        import hashlib
        d["table_sha256"] = hashlib.sha256(json.dumps({"rows": d["rows"], "teardown": d["teardown"]}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def f_extra_pa(d):
        add_row(d, blk="PMA", off=0x2000)
        resha(d)

    def f_other_pin(d):
        add_row(d, kind="gpio_dat", blk="GPH2", off=4, mask=1 << 5, value=1 << 5, restore="T01")
        resha(d)

    def f_perst_early(d):
        add_row(d, kind="gpio_dat", blk="GPH2", off=4, mask=1, value=1, restore="T01")
        resha(d)

    def f_unknown_row(d):
        add_row(d, step=22, kind="mmio_write", blk="ELBI", off=0x54, value=1, restore="T02")
        resha(d)

    def f_partial_write(d):
        add_row(d, blk="PMA", off=0x10, mask=0xFF, value=1)
        resha(d)

    def f_dbi(d):
        add_row(d, blk="DBI", off=0x8BC, value=1, step=19)
        resha(d)

    def f_restore_removed(d):
        next(r for r in d["rows"] if r["step"] == 8 and r["kind"] == "mmio_write")["restore"] = None
        resha(d)

    def f_iso_last(d):
        d["teardown"].append(dict(d["teardown"][0], id=f"T{len(d['teardown']) + 1:02d}", blk="PMA", off=0x400, kind="mmio_write", mask=0xFFFFFFFF, requires=8))
        resha(d)

    def f_other_smc(d):
        r = next(x for x in d["rows"] if x["kind"] == "smc_iso")
        r["off"] = 0x3EC0
        resha(d)

    def f_step_overflow(d):
        add_row(d, step=34)
        resha(d)

    def first_row(d, step, **sel):
        return next(r for r in d["rows"] if r["step"] == step and all(r.get(k) == v for k, v in sel.items()))

    def set_row(step, key, val, **sel):
        def f(d):
            first_row(d, step, **sel)[key] = val
            resha(d)
        return f

    def f_atu_write(d):
        d["teardown"].append(dict(d["teardown"][0], id=f"T{len(d['teardown']) + 1:02d}", blk="ATU", off=0x8, kind="mmio_write", mask=0xFFFFFFFF, requires=8))
        resha(d)

    def tear_append(**kw):
        def f(d):
            d["teardown"].append(dict(d["teardown"][0], id=f"T{len(d['teardown']) + 1:02d}", **kw))
            resha(d)
        return f

    def f_s26_swap_cr2_first(d):
        i = next(n for n, r in enumerate(d["rows"]) if r["step"] == 26)
        j = next(n for n, r in enumerate(d["rows"]) if r["step"] == 26 and r["off"] == 4 and r["kind"] == "mmio_write")
        d["rows"][i], d["rows"][j] = d["rows"][j], d["rows"][i]
        for n, r in enumerate(d["rows"]):
            r["id"] = n
        resha(d)

    def f_s26_cr2_check_late(d):
        i = next(n for n, r in enumerate(d["rows"]) if r["step"] == 26 and r["kind"] == "wait_eq")
        d["rows"][i], d["rows"][i + 1] = d["rows"][i + 1], d["rows"][i]
        for n, r in enumerate(d["rows"]):
            r["id"] = n
        resha(d)

    def f_s27_removed(**sel):
        def f(d):
            d["rows"].remove(first_row(d, 27, **sel))
            for n, r in enumerate(d["rows"]):
                r["id"] = n
            resha(d)
        return f

    def f_s26_row_removed(**sel):
        def f(d):
            d["rows"].remove(first_row(d, 26, **sel))
            for n, r in enumerate(d["rows"]):
                r["id"] = n
            resha(d)
        return f

    def f_s26_cfg_probe_late(d):
        i = next(n for n, r in enumerate(d["rows"]) if r["step"] == 26 and r.get("probe"))
        d["rows"][i], d["rows"][i + 1] = d["rows"][i + 1], d["rows"][i]
        for n, r in enumerate(d["rows"]):
            r["id"] = n
        resha(d)

    def f_s28_removed(**sel):
        def f(d):
            d["rows"].remove(first_row(d, 28, **sel))
            for n, r in enumerate(d["rows"]):
                r["id"] = n
            resha(d)
        return f

    def f_rm(step, **sel):	# generic: the first row of a step matching sel removed
        def f(d):
            d["rows"].remove(first_row(d, step, **sel))
            for n, r in enumerate(d["rows"]):
                r["id"] = n
            resha(d)
        return f

    def f_swap(step, sel_a, sel_b):	# two rows of a step swapped (ids renumbered)
        def f(d):
            a, b = first_row(d, step, **sel_a), first_row(d, step, **sel_b)
            i, j = d["rows"].index(a), d["rows"].index(b)
            d["rows"][i], d["rows"][j] = d["rows"][j], d["rows"][i]
            for n, r in enumerate(d["rows"]):
                r["id"] = n
            resha(d)
        return f

    def f_restore_xchg(step, sel_a, sel_b):	# the restore keys of two rows exchanged
        def f(d):
            a, b = first_row(d, step, **sel_a), first_row(d, step, **sel_b)
            a["restore"], b["restore"] = b["restore"], a["restore"]
            resha(d)
        return f

    def f_tear_reordered(order_fn):	# the teardown rows reordered (ids and restore references renumbered consistently)
        def f(d):
            order = order_fn(d["teardown"])
            new = {x["id"]: f"T{n + 1:02d}" for n, x in enumerate(order)}
            for x in order:
                x["id"] = new[x["id"]]
            d["teardown"] = order
            for r in d["rows"]:
                if r["restore"]:
                    r["restore"] = new.get(r["restore"], "T99")
            resha(d)
        return f

    SH = len(_WLAN_S32_TEAR_HEAD)	# the S32 head rows precede the S29 head in the teardown

    def f_pa_first(d):	# PERST low moved to the head of the teardown, ids and restore references renumbered consistently
        t = d["teardown"]
        order = t[:SH] + [t[SH + 17]] + t[SH:SH + 17] + t[SH + 18:]
        new = {x["id"]: f"T{n + 1:02d}" for n, x in enumerate(order)}
        for x in order:
            x["id"] = new[x["id"]]
        d["teardown"] = order
        for r in d["rows"]:
            if r["restore"]:
                r["restore"] = new[r["restore"]]
        resha(d)

    def f_restore_swapped(d):
        a = first_row(d, 26, kind="mmio_write", off=0x8)
        b = first_row(d, 26, kind="mmio_write", off=0x10)
        a["restore"], b["restore"] = b["restore"], a["restore"]
        resha(d)

    def f_atu_probe_late(d):
        i = next(n for n, r in enumerate(d["rows"]) if r["step"] == 25 and r.get("probe"))
        d["rows"][i], d["rows"][i + 1] = d["rows"][i + 1], d["rows"][i]
        for n, r in enumerate(d["rows"]):
            r["id"] = n
        resha(d)

    def f_s25_removed(d):
        d["rows"].remove(first_row(d, 25, off=0x8F8))
        for n, r in enumerate(d["rows"]):
            r["id"] = n
        resha(d)

    def f_s24_removed(d):
        d["rows"].remove(first_row(d, 24, off=0x70))
        for n, r in enumerate(d["rows"]):
            r["id"] = n
        resha(d)

    cases = [
        ("an extra write beyond a window", f_extra_pa, None, "outside the allowlist"),
        ("an extra pin of a GPIO bank", f_other_pin, None, "outside the allowlist"),
        ("a partial-mask mmio_write", f_partial_write, None, "outside the allowlist"),
        ("an extra DBI register", f_dbi, None, "outside the allowlist"),
        ("PERST raised inside the default run", f_perst_early, None, "default run"),
        ("a write row in the skipped step S22", f_unknown_row, None, "step coverage"),
        ("S22 with its row removed", lambda d: (d["rows"].remove(next(r for r in d["rows"] if r["step"] == 22)), [r.__setitem__("id", n) for n, r in enumerate(d["rows"])], resha(d)), None, "step coverage"),
        ("S22 with a nonzero delay", lambda d: (next(r for r in d["rows"] if r["step"] == 22).__setitem__("delay_us", 5), resha(d)), None, "step coverage"),
        ("S22 without the skip note", lambda d: (next(r for r in d["rows"] if r["step"] == 22).__setitem__("note", "ok"), resha(d)), None, "step coverage"),
        ("S23 wait with another poll count", lambda d: (next(r for r in d["rows"] if r["step"] == 23 and r["kind"] == "wait_eq").__setitem__("polls", 20000), resha(d)), None, "S23 must be"),
        ("a restore removed", f_restore_removed, None, "no restore action"),
        ("a PHY access after the isolation clear", f_iso_last, None, "isolation clear"),
        ("another SMC target", f_other_smc, None, "only SMC target"),
        ("a step index overflow", f_step_overflow, None, "malformed"),
        ("log flag on a write row", set_row(8, "log", True, kind="mmio_write"), None, "log flag"),
        ("log flag on a probe row", set_row(19, "log", True, probe=True), None, "log flag"),
        ("log flag on a non-S24 read_only row", set_row(16, "log", True, kind="read_only"), None, "log flag"),
        ("an S24 row removed", f_s24_removed, None, "S24 must be exactly"),
        ("an S24 row changed (offset)", set_row(24, "off", 0x74, off=0x70), None, "S24 must be exactly"),
        ("an ATU write row (teardown, S08)", f_atu_write, None, "ATU writes are only"),
        ("an ATU write at an unlisted offset (teardown S26)", tear_append(blk="ATU", off=0x20, kind="mmio_write", mask=0xFFFFFFFF, value=0, requires=26), None, "outside the allowlist"),
        ("an ATU write of an unlisted value (teardown S26)", tear_append(blk="ATU", off=0x4, kind="mmio_write", mask=0xFFFFFFFF, value=0x80000001, requires=26), None, "outside the allowlist"),
        ("an ATU partial-mask write (teardown S26)", tear_append(blk="ATU", off=0x4, kind="mmio_rmw", mask=0x80000000, value=0, requires=26), None, "outside the allowlist"),
        ("an ATU write in an early step (before the probe)", lambda d: (add_row(d, step=19, blk="ATU", off=0x8, value=0, restore="T03"), resha(d)), None, "before its probe row"),
        ("an extra ATU write in S26", lambda d: (add_row(d, step=26, blk="ATU", off=0x8, value=0, restore="T03"), resha(d)), None, "S26 must be exactly"),
        ("a CFG write row (teardown)", tear_append(blk="CFG", off=0x4, kind="mmio_write", mask=0xFFFFFFFF, value=0, requires=26), None, "CFG window is read-only"),
        ("a CFG read row in the teardown", tear_append(blk="CFG", off=0, kind="read_only", mask=0xFFFFFFFF, requires=26), None, "CFG window is read-only"),
        ("an ATU read-only teardown row after the isolation clear", tear_append(blk="ATU", off=0, kind="read_only", mask=0xFFFFFFFF, requires=26), None, "isolation clear"),
        ("a DBI read-only teardown row after the isolation clear", tear_append(blk="DBI", off=0, kind="read_only", mask=0xFFFFFFFF, requires=19), None, "isolation clear"),
        ("a CFG row outside S26", lambda d: (add_row(d, step=19, kind="read_only", blk="CFG", off=0, restore=None, probe=True), resha(d)), None, "CFG window is read-only"),
        ("the CFG probe flag removed", set_row(26, "probe", False, blk="CFG", off=0, probe=True), None, "before its probe row"),
        ("the CFG probe moved after its first read", f_s26_cfg_probe_late, None, "before its probe row"),
        ("S26 CR2 enable first", f_s26_swap_cr2_first, None, "S26 must be exactly"),
        ("an S26 read-back removed", f_s26_row_removed(kind="read_only", blk="ATU", off=0x10), None, "S26 must be exactly"),
        ("an S27 CFG read removed", f_s27_removed(blk="CFG", off=0x88), None, "S27 must be exactly"),
        ("an S27 ATU region 1 read removed", f_s27_removed(blk="ATU", off=0x210), None, "S27 must be exactly"),
        ("an S27 CFG read at another offset", set_row(27, "off", 0x1000, blk="CFG", off=0x100), None, "S27 must be exactly"),
        ("an S27 ATU read of region 0", set_row(27, "off", 0x004, blk="ATU", off=0x204), None, "S27 must be exactly"),
        ("an S27 CFG read at 0xA4 (PCI_BACKPLANE_DATA)", lambda d: (add_row(d, step=27, kind="read_only", blk="CFG", off=0xA4, restore=None, log=True), resha(d)), None, "0x8C..0xA8"),
        ("an S27 CFG write", lambda d: (add_row(d, step=27, blk="CFG", off=0x10, value=0xFFFFFFFF, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("an S27 ATU region 1 write", lambda d: (add_row(d, step=27, blk="ATU", off=0x204, value=0x80000000, restore="T01"), resha(d)), None, "S27 must be exactly"),
        ("an S27 ATU region 1 write (teardown)", tear_append(blk="ATU", off=0x204, kind="mmio_write", mask=0xFFFFFFFF, value=0, requires=27), None, "ATU writes are only"),
        ("the log flag removed from an S27 read", set_row(27, "log", False, blk="CFG", off=0x10), None, "S27 must be exactly"),
        ("a probe flag on an S27 read", set_row(27, "probe", True, blk="CFG", off=0x10), None, "S27 must be exactly"),
        ("an S26 CFG read removed", f_s26_row_removed(kind="read_only", blk="CFG", off=0xC), None, "S26 must be exactly"),
        ("the S26 CR2 one-shot check removed", f_s26_row_removed(kind="wait_eq", blk="ATU"), None, "S26 must be exactly"),
        ("the S26 CR2 check polls more than once", set_row(26, "polls", 2, kind="wait_eq", blk="ATU"), None, "S26 must be exactly"),
        ("the S26 CR2 check waits for another value", set_row(26, "value", 0x80000001, kind="wait_eq", blk="ATU"), None, "S26 must be exactly"),
        ("the S26 CR2 check moved after the CFG probe", f_s26_cr2_check_late, None, "S26 must be exactly"),
        ("an S26 write value changed", set_row(26, "value", 0x60FFE004, kind="mmio_write", off=0x8), None, "S26 must be exactly"),
        ("an S26 write restored by another row", f_restore_swapped, None, "S26 must be exactly"),
        ("log flag on the S26 CFG probe", set_row(26, "log", True, blk="CFG", probe=True), None, "S26 must be exactly"),
        ("log flag on an S26 write", set_row(26, "log", True, kind="mmio_write", off=0x10), None, "S26 must be exactly"),
        ("the ATU disable not first in the teardown", f_pa_first, None, "teardown must"),
        ("an ATU restore value changed (LIMIT reset 0)", lambda d: (next(t for t in d["teardown"] if t["blk"] == "ATU" and t["off"] == 0x10).__setitem__("value", 0), resha(d)), None, "outside the allowlist"),
        ("an ATU restore value changed (CR1 left at 4)", lambda d: (next(t for t in d["teardown"] if t["blk"] == "ATU" and t["off"] == 0x0).__setitem__("value", 4), resha(d)), None, "teardown must"),
        ("an ATU restore row removed", lambda d: (d["teardown"].remove(next(t for t in d["teardown"] if t["blk"] == "ATU" and t["off"] == 0x18)), [t.__setitem__("id", f"T{n + 1:02d}") for n, t in enumerate(d["teardown"])], resha(d)), None, "S26 must be exactly"),
        ("the ATU probe flag removed", set_row(25, "probe", False, blk="ATU", off=0), None, "before its probe row"),
        ("the ATU probe moved after its first read", f_atu_probe_late, None, "before its probe row"),
        ("an S25 row removed", f_s25_removed, None, "S25 must be exactly"),
        ("an S25 row changed (offset)", set_row(25, "off", 0x20, blk="ATU", off=0x18), None, "S25 must be exactly"),
        ("an S25 row turned to ELBI", set_row(25, "blk", "ELBI", blk="ATU", off=0x14), None, "S25 must be exactly"),
        ("log flag on the S25 ATU probe", set_row(25, "log", True, blk="ATU", probe=True), None, "S25 must be exactly"),
        ("an S24 row without the log flag", set_row(24, "log", False, off=0x70), None, "S24 must be exactly"),
        ("the S28 BAR teardown rows dropped", f_tear_reordered(lambda t: t[:SH] + (lambda t: t[:3] + t[7:])(t[SH:])), None, "S28 must be exactly"),
        ("one S28 BAR teardown row dropped (BAR2 high)", f_tear_reordered(lambda t: t[:SH] + (lambda t: t[:6] + t[7:])(t[SH:])), None, "S28 must be exactly"),
        ("the S28 BAR teardown after the ATU disable", f_tear_reordered(lambda t: t[:SH] + (lambda t: t[:3] + t[7:17] + t[3:7] + t[17:])(t[SH:])), None, "teardown must"),
        ("an S28 BAR teardown value changed (BAR0 left at the assigned address)", lambda d: (next(t for t in d["teardown"] if t["blk"] == "CFG" and t["off"] == 0x10).__setitem__("value", 0x60000004), resha(d)), None, "teardown must"),
        ("an S28 BAR teardown row at another offset", lambda d: (next(t for t in d["teardown"] if t["blk"] == "CFG" and t["off"] == 0x10).__setitem__("off", 0x04), resha(d)), None, "S28 must be exactly"),
        ("an S28 Command write", lambda d: (add_row(d, step=28, blk="CFG", off=0x04, value=2, restore="T01"), resha(d)), None, "S28 must be exactly"),
        ("an S28 write at another config offset (0x20)", lambda d: (add_row(d, step=28, blk="CFG", off=0x20, value=0xFFFFFFFF, restore="T01"), resha(d)), None, "S28 must be exactly"),
        ("an S28 write at BAR0 window select (0x80)", lambda d: (add_row(d, step=28, blk="CFG", off=0x80, value=0x18000000, restore="T01"), resha(d)), None, "S28 must be exactly"),
        ("an S28 assignment moved (BAR0 at 0x60000008)", set_row(28, "value", 0x60000008, kind="mmio_write", off=0x10, value=0x60000004), None, "S28 must be exactly"),
        ("an S28 assignment overlapping (BAR2 at 0x60000004)", set_row(28, "value", 0x60000004, kind="mmio_write", off=0x18, value=0x60400004), None, "S28 must be exactly"),
        ("an S28 assignment value outside the allowlist (0x60400000)", set_row(28, "value", 0x60400000, kind="mmio_write", off=0x18, value=0x60400004), None, "S28"),
        ("an S28 sizing write restored by the wrong BAR row", lambda d: first_row(d, 28, kind="mmio_write", off=0x10, value=0xFFFFFFFF).__setitem__("restore", first_row(d, 28, kind="mmio_write", off=0x18, value=0xFFFFFFFF)["restore"]) or resha(d), None, "S28 must be exactly"),
        ("the S28 size guard removed (BAR2 low)", f_s28_removed(kind="wait_mask", off=0x18), None, "S28 must be exactly"),
        ("the S28 high-half guard removed (BAR0 high)", f_s28_removed(kind="wait_mask", off=0x14), None, "S28 must be exactly"),
        ("the S28 size guard weakened (bits [31:24] only)", set_row(28, "mask", 0xFF000000, kind="wait_mask", off=0x10), None, "S28 must be exactly"),
        ("the S28 Command precondition removed", f_s28_removed(kind="wait_eq", off=0x04), None, "S28 must be exactly"),
        ("the S28 Command precondition weakened (MEM bit only)", set_row(28, "mask", 0x2, kind="wait_eq", off=0x04), None, "S28 must be exactly"),
        ("an S28 guard that polls more than once", set_row(28, "polls", 2, kind="wait_mask", off=0x10), None, "S28 must be exactly"),
        ("the S28 original written back removed (BAR0 low)", f_s28_removed(kind="mmio_write", off=0x10, value=4), None, "S28 must be exactly"),
        ("an S28 CFG write in S26", lambda d: (add_row(d, step=26, blk="CFG", off=0x10, value=0xFFFFFFFF, restore="T01"), resha(d)), None, "S26 must be exactly"),
        ("an S28 wait on a non-CFG block", set_row(28, "blk", "ATU", kind="wait_eq", off=0x04), None, "S28 must be exactly"),
        ("a CFG wait row outside S28", lambda d: (add_row(d, step=27, kind="wait_eq", blk="CFG", off=0x10, restore=None, polls=1), resha(d)), None, "CFG window is read-only"),
        ("an S29 Command write of MASTER (0x6)", set_row(29, "value", 0x6, kind="mmio_write", blk="CFG", off=0x04), None, "S29 must be exactly"),
        ("an S29 Command write of MEM|IO (0x3)", set_row(29, "value", 0x3, kind="mmio_write", blk="CFG", off=0x04), None, "S29 must be exactly"),
        ("an S29 Command write with a status bit (0x00100002)", set_row(29, "value", 0x00100002, kind="mmio_write", blk="CFG", off=0x04), None, "S29 must be exactly"),
        ("an extra S29 Command write of MASTER", lambda d: (add_row(d, step=29, blk="CFG", off=0x04, value=0x6, restore="T02"), resha(d)), None, "S29 must be exactly"),
        ("an S29 region 1 LIMIT of 2 MiB (Linux's value)", set_row(29, "value", 0x601FFFFF, kind="mmio_write", blk="ATU", off=0x210), None, "S29 must be exactly"),
        ("an S29 region 1 wrong LIMIT (0x6001FFFF)", set_row(29, "value", 0x6001FFFF, kind="mmio_write", blk="ATU", off=0x210), None, "S29 must be exactly"),
        ("an S29 region 1 LOWER_TARGET changed (0x60400000)", set_row(29, "value", 0x60400000, kind="mmio_write", blk="ATU", off=0x214), None, "S29 must be exactly"),
        ("an S29 region 1 LOWER_BASE changed (0x60400000)", set_row(29, "value", 0x60400000, kind="mmio_write", blk="ATU", off=0x208), None, "S29 must be exactly"),
        ("an S29 window select changed (0x18001000)", set_row(29, "value", 0x18001000, kind="mmio_write", blk="CFG", off=0x80), None, "S29 must be exactly"),
        ("the S29 region 1 enable before the target", f_swap(29, dict(kind="mmio_write", blk="ATU", off=0x204), dict(kind="mmio_write", blk="ATU", off=0x214)), None, "S29 must be exactly"),
        ("the S29 Command arm before the window select", f_swap(29, dict(kind="mmio_write", blk="CFG", off=0x04), dict(kind="mmio_write", blk="CFG", off=0x80)), None, "S29 must be exactly"),
        ("an S29 CR1 write", lambda d: (add_row(d, step=29, blk="ATU", off=0x200, value=0, restore="T01"), resha(d)), None, "S29 must be exactly"),
        ("an S29 UPPER_BASE write", lambda d: (add_row(d, step=29, blk="ATU", off=0x20C, value=0, restore="T01"), resha(d)), None, "S29 must be exactly"),
        ("an S29 UPPER_TARGET write", lambda d: (add_row(d, step=29, blk="ATU", off=0x218, value=0, restore="T01"), resha(d)), None, "S29 must be exactly"),
        ("an S29 SPROM control write (0x88)", lambda d: (add_row(d, step=29, blk="CFG", off=0x88, value=0, restore="T02"), resha(d)), None, "S29 must be exactly"),
        ("the S29 Command precondition removed", f_rm(29, kind="wait_eq", blk="CFG", off=0x04, value=0), None, "S29 must be exactly"),
        ("the S29 Command read-back removed", f_rm(29, kind="wait_eq", blk="CFG", off=0x04, value=0x2), None, "S29 must be exactly"),
        ("the S29 region 1 disabled precondition removed", f_rm(29, kind="wait_eq", blk="ATU", off=0x204, value=0), None, "S29 must be exactly"),
        ("the S29 region 1 enable read-back removed", f_rm(29, kind="wait_eq", blk="ATU", off=0x204, value=0x80000000, mask=0xFFFFFFFF), None, "S29 must be exactly"),
        ("the S29 BAR0 assignment precondition removed", f_rm(29, kind="wait_eq", blk="CFG", off=0x10), None, "S29 must be exactly"),
        ("the S29 window precondition removed", f_rm(29, kind="wait_eq", blk="CFG", off=0x80, value=0x18001000), None, "S29 must be exactly"),
        ("the S29 window read-back removed", f_rm(29, kind="wait_eq", blk="CFG", off=0x80, value=0x18000000), None, "S29 must be exactly"),
        ("an S29 read-back that polls more than once", set_row(29, "polls", 2, kind="wait_eq", blk="ATU", off=0x208), None, "S29 must be exactly"),
        ("an S29 write restored by another row (region 1 base / window)", f_restore_xchg(29, dict(kind="mmio_write", blk="ATU", off=0x208), dict(kind="mmio_write", blk="CFG", off=0x80)), None, "S29 must be exactly"),
        ("an S29 log flag removed (AER before)", set_row(29, "log", False, kind="read_only", blk="CFG", off=0x104), None, "S29 must be exactly"),
        ("a MEM read in S29 (BAR0 before its step)", lambda d: (add_row(d, step=29, kind="read_only", blk="MEM", off=0, restore=None, probe=True), resha(d)), None, "BAR0 (MEM)"),
        ("a MEM write row in S30", lambda d: (add_row(d, step=30, kind="mmio_write", blk="MEM", off=0, value=0, restore="T01"), resha(d)), None, "BAR0 (MEM)"),
        ("a MEM read at offset 4", set_row(30, "off", 4, kind="read_only", blk="MEM", probe=True), None, "BAR0 (MEM)"),
        ("a MEM row in the teardown", tear_append(blk="MEM", off=0, kind="read_only", mask=0xFFFFFFFF, requires=30), None, "BAR0 (MEM)"),
        ("a MEM write in the teardown", tear_append(blk="MEM", off=0, kind="mmio_write", mask=0xFFFFFFFF, value=0, requires=30), None, "BAR0 (MEM)"),
        ("the S30 chip id check removed", f_rm(30, kind="wait_eq"), None, "S30 must be exactly"),
        ("the S30 chip id mask weakened (0xFF)", set_row(30, "mask", 0xFF, kind="wait_eq", blk="MEM"), None, "S30 must be exactly"),
        ("a teardown row became read_only (delay_us read as flag bits)", lambda d: (next(t for t in d["teardown"] if t["blk"] == "ATU" and t["off"] == 0x0).__setitem__("kind", "read_only"), resha(d)), None, "no teardown row is read_only"),
        ("the S30 chip id value changed (0x4390)", set_row(30, "value", 0x4390, kind="wait_eq", blk="MEM"), None, "S30 must be exactly"),
        ("the S30 chip id check that polls more than once", set_row(30, "polls", 2, kind="wait_eq", blk="MEM"), None, "S30 must be exactly"),
        ("the S30 MEM probe flag removed", set_row(30, "probe", False, kind="read_only", blk="MEM", probe=True), None, "before its probe row"),
        ("the S30 MEM log flag removed", set_row(30, "log", False, kind="read_only", blk="MEM", probe=None), None, "S30 must be exactly"),
        ("the S30 link log removed", f_rm(30, kind="read_only", blk="CFG", off=0xBC), None, "S30 must be exactly"),
        ("an S30 ATU write", lambda d: (add_row(d, step=30, blk="ATU", off=0x204, value=0, restore="T01"), resha(d)), None, "S30 must be exactly"),
        ("an S30 Command write", lambda d: (add_row(d, step=30, blk="CFG", off=0x04, value=0x2, restore="T02"), resha(d)), None, "CFG window is read-only"),
        ("an S31 MEM write row", lambda d: (add_row(d, step=31, kind="mmio_write", blk="MEM", off=0xFC, value=0, restore="T01"), resha(d)), None, "BAR0 (MEM)"),
        ("an S31 MEM RMW row", lambda d: (add_row(d, step=31, kind="mmio_rmw", blk="MEM", off=0x1E0, mask=0x1, value=0, restore="T01"), resha(d)), None, "BAR0 (MEM)"),
        ("an S31 MEM read at offset 0x100", set_row(31, "off", 0x100, kind="read_only", blk="MEM"), None, "BAR0 (MEM)"),
        ("an S31 MEM read at offset 0x1000", lambda d: (add_row(d, step=31, kind="read_only", blk="MEM", off=0x1000, restore=None), resha(d)), None, "BAR0 (MEM)"),
        ("an S31 CFG 0x80 write (window move)", lambda d: (add_row(d, step=31, blk="CFG", off=0x80, value=0x18001000, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("an S31 Command write", lambda d: (add_row(d, step=31, blk="CFG", off=0x04, value=0x4, restore="T02"), resha(d)), None, "CFG window is read-only"),
        ("an S31 ATU write", lambda d: (add_row(d, step=31, blk="ATU", off=0x204, value=0, restore="T01"), resha(d)), None, "S31 must be exactly"),
        ("an S31 PMA write", lambda d: (add_row(d, step=31, blk="PMA", off=0x100, value=0, restore="T01"), resha(d)), None, "S31 must be exactly"),
        ("a teardown row required by S31", tear_append(blk="CFG", off=0x80, kind="mmio_write", mask=0xFFFFFFFF, value=0x18001000, requires=31), None, "CFG window is read-only"),
        ("a restore on an S31 read", set_row(31, "restore", "T01", kind="read_only", blk="MEM"), None, "S31 must be exactly"),
        ("the S31 window precondition removed", f_rm(31, kind="wait_eq", blk="CFG", off=0x80), None, "S31 must be exactly"),
        ("the S31 eromptr check removed", f_rm(31, kind="wait_eq", blk="MEM", off=0xFC), None, "S31 must be exactly"),
        ("the S31 eromptr check weakened (alignment dropped)", set_row(31, "mask", 0xF8000000, kind="wait_eq", blk="MEM", off=0xFC), None, "S31 must be exactly"),
        ("the S31 eromptr range widened", set_row(31, "mask", 0x00000FFF, kind="wait_eq", blk="MEM", off=0xFC), None, "S31 must be exactly"),
        ("the S31 chip id re-check removed", f_rm(31, kind="wait_eq", blk="MEM", off=0, value=0x10034389), None, "S31 must be exactly"),
        ("the S31 chip id re-check weakened (mask 0xFFFF)", set_row(31, "mask", 0xFFFF, kind="wait_eq", blk="MEM", off=0), None, "S31 must be exactly"),
        ("the S31 chip id re-check value changed (low 16 bits only)", set_row(31, "value", 0x4389, kind="wait_eq", blk="MEM", off=0), None, "S31 must be exactly"),
        ("the S31 eromptr log flag removed", set_row(31, "log", False, kind="read_only", blk="MEM", off=0xFC), None, "S31 must be exactly"),
        ("the S31 clk_ctl_st read removed", f_rm(31, kind="read_only", blk="MEM", off=0x1E0), None, "S31 must be exactly"),
        ("the S31 AER log removed", f_rm(31, kind="read_only", blk="CFG", off=0x104), None, "S31 must be exactly"),
        ("an S31 read that polls more than once", set_row(31, "polls", 2, kind="wait_eq", blk="MEM", off=0xFC), None, "S31 must be exactly"),
        ("the S29 region 1 disable dropped from the teardown", f_tear_reordered(lambda t: t[:SH] + (lambda t: t[1:])(t[SH:])), None, "S29 must be exactly"),
        ("the S29 Command restore dropped from the teardown", f_tear_reordered(lambda t: t[:SH] + (lambda t: t[:1] + t[2:])(t[SH:])), None, "S29 must be exactly"),
        ("the S29 window restore dropped from the teardown", f_tear_reordered(lambda t: t[:SH] + (lambda t: t[:2] + t[3:])(t[SH:])), None, "S29 must be exactly"),
        ("the S29 region 1 resets dropped from the teardown", f_tear_reordered(lambda t: t[:SH] + (lambda t: t[:7] + t[10:])(t[SH:])), None, "S29 must be exactly"),
        ("the S29 region 1 disable not first in the teardown", f_tear_reordered(lambda t: t[:SH] + (lambda t: [t[1], t[0]] + t[2:])(t[SH:])), None, "teardown must start with exactly the S29"),
        ("the S29 Command restore left armed (value 2)", lambda d: (d["teardown"][SH + 1].__setitem__("value", 2), resha(d)), None, "teardown must start with exactly the S29"),
        ("the S29 window restore left at ChipCommon", lambda d: (d["teardown"][SH + 2].__setitem__("value", 0x18000000), resha(d)), None, "teardown must start with exactly the S29"),
        ("the S29 region 1 disable required only from S28", lambda d: (d["teardown"][SH + 0].__setitem__("requires", 28), resha(d)), None, "S29 must be exactly"),
        ("the S29 region 1 LIMIT reset to 0", lambda d: (next(t for t in d["teardown"] if t["blk"] == "ATU" and t["off"] == 0x210 and t["requires"] == 29).__setitem__("value", 0), resha(d)), None, "outside the allowlist"),
        ("the S29 region 1 resets before the BAR rows", f_tear_reordered(lambda t: t[:SH] + (lambda t: t[:3] + t[7:10] + t[3:7] + t[10:])(t[SH:])), None, "4 BAR rows"),
        # S32 (research 37 s.9.1 run B), review-12 style: each defect of the new window move / dump / restore
        ("an S32 MEM write row", lambda d: (add_row(d, step=32, kind="mmio_write", blk="MEM", off=0xFC, value=0, restore="T01"), resha(d)), None, "BAR0 (MEM)"),
        ("an S32 MEM RMW row", lambda d: (add_row(d, step=32, kind="mmio_rmw", blk="MEM", off=0x100, mask=0x1, value=0, restore="T01"), resha(d)), None, "BAR0 (MEM)"),
        ("an S32 dump span too wide (read at 0x1000)", lambda d: (add_row(d, step=32, kind="read_only", blk="MEM", off=0x1000, restore=None, log=True), resha(d)), None, "BAR0 (MEM)"),
        ("an S32 dump span too wide (read at ER_REMAPCONTROL 0xE00, review 13 M1)", lambda d: (add_row(d, step=32, kind="read_only", blk="MEM", off=0xE00, restore=None, log=True), resha(d)), None, "BAR0 (MEM)"),
        ("an S32 dump span too wide (read at 0xFFC)", lambda d: (add_row(d, step=32, kind="read_only", blk="MEM", off=0xFFC, restore=None, log=True), resha(d)), None, "BAR0 (MEM)"),
        ("an S32 dump dword moved past the cap (0xDFC -> 0xE00)", set_row(32, "off", 0xE00, kind="read_only", blk="MEM", off=0xDFC), None, "BAR0 (MEM)"),
        ("an S32 dump span too wide (an unaligned offset)", set_row(32, "off", 0x1E, blk="MEM", off=0x1C), None, "BAR0 (MEM)"),
        ("an S32 dump with a 1025th dword inside the span", lambda d: (add_row(d, step=32, kind="read_only", blk="MEM", off=0x800, restore=None, log=True), resha(d)), None, "S32 must be exactly"),
        ("an S32 dump dword dropped", f_rm(32, kind="read_only", blk="MEM", off=0xDFC), None, "S32 must be exactly"),
        ("an S32 dump dword offset changed", set_row(32, "off", 0x40, kind="read_only", blk="MEM", off=0x1C), None, "S32 must be exactly"),
        ("an S32 dump dword without the log flag", set_row(32, "log", False, kind="read_only", blk="MEM", off=0x400), None, "S32 must be exactly"),
        ("W-C1c: an S32 dump row without the eot flag", set_row(32, "eot", 0, kind="read_only", blk="MEM", off=0x400), None, "eot = 1"),
        ("W-C1c: the last S32 dump row without eot = 2 (no refusal without an EOT)", set_row(32, "eot", 1, kind="read_only", blk="MEM", off=0xDFC), None, "eot = 1"),
        ("W-C1c: eot = 2 on a middle S32 dump row", set_row(32, "eot", 2, kind="read_only", blk="MEM", off=0x400), None, "eot = 1"),
        ("W-C1c: eot flag on an S32 CFG log row", set_row(32, "eot", 1, kind="read_only", blk="CFG", off=0xBC), None, "eot = 1"),
        ("W-C1c: eot flag on an S31 log row", set_row(31, "eot", 1, kind="read_only", blk="MEM", off=0xFC), None, "eot = 1"),
        ("an S32 dump dword turned into a probe", set_row(32, "probe", True, kind="read_only", blk="MEM", off=0x10), None, "S32 must be exactly"),
        ("the S32 window write to the wrong value (0x18105004)", set_row(32, "value", 0x18105004, kind="mmio_write", blk="CFG", off=0x80), None, "CFG window is read-only"),
        ("the S32 window write to the S29 original (0x18001000)", set_row(32, "value", 0x18001000, kind="mmio_write", blk="CFG", off=0x80), None, "CFG window is read-only"),
        ("the S32 window write left at ChipCommon (no move)", set_row(32, "value", 0x18000000, kind="mmio_write", blk="CFG", off=0x80), None, "S32 must be exactly"),
        ("an S32 Command write", lambda d: (add_row(d, step=32, blk="CFG", off=0x04, value=0x2, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("an S32 BAR write", lambda d: (add_row(d, step=32, blk="CFG", off=0x10, value=0x60000004, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("an S32 SPROM (0x88) write", lambda d: (add_row(d, step=32, blk="CFG", off=0x88, value=0, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("an extra S32 window write", lambda d: (add_row(d, step=32, blk="CFG", off=0x80, value=0x18000000, restore="T01"), resha(d)), None, "S32 must be exactly"),
        ("an S32 PMA write", lambda d: (add_row(d, step=32, blk="PMA", off=0x100, value=0, restore="T01"), resha(d)), None, "S32 must be exactly"),
        ("an S32 ATU write", lambda d: (add_row(d, step=32, blk="ATU", off=0x204, value=0, restore="T01"), resha(d)), None, "S32 must be exactly"),
        ("the S32 window write without a restore", set_row(32, "restore", None, kind="mmio_write", blk="CFG", off=0x80), None, "S32 must be exactly"),
        ("the S32 window write restored by another teardown row", set_row(32, "restore", "T05", kind="mmio_write", blk="CFG", off=0x80), None, "S32 must be exactly"),
        ("the S32 window precondition removed", f_rm(32, kind="wait_eq", blk="CFG", off=0x80, value=0x18000000), None, "S32 must be exactly"),
        ("the S32 window read-back removed", f_rm(32, kind="wait_eq", blk="CFG", off=0x80, value=0x18105000), None, "S32 must be exactly"),
        ("the S32 window read-back checks another value", set_row(32, "value", 0x18000000, kind="wait_eq", blk="CFG", off=0x80, value=0x18105000), None, "S32 must be exactly"),
        ("the S32 window read-back polls twice", set_row(32, "polls", 2, kind="wait_eq", blk="CFG", off=0x80, value=0x18105000), None, "S32 must be exactly"),
        ("the S32 eromptr pin removed", f_rm(32, kind="wait_eq", blk="MEM", off=0xFC), None, "S32 must be exactly"),
        ("the S32 eromptr pin changed (0x18109000)", set_row(32, "value", 0x18109000, kind="wait_eq", blk="MEM", off=0xFC), None, "S32 must be exactly"),
        ("the S32 eromptr pin weakened (4 KiB aligned range only)", lambda d: (first_row(d, 32, kind="wait_eq", blk="MEM", off=0xFC).update(mask=0xF8000FFF, value=0x18000000), resha(d)), None, "S32 must be exactly"),
        ("the S32 first-dword check removed", f_rm(32, kind="wait_eq", blk="MEM", off=0), None, "S32 must be exactly"),
        ("the S32 first-dword check weakened (mask 0)", set_row(32, "mask", 0, kind="wait_eq", blk="MEM", off=0), None, "S32 must be exactly"),
        ("the S32 first-dword check back to mask 0x6 == 0 (review 13 L2)", set_row(32, "mask", 0x6, kind="wait_eq", blk="MEM", off=0, value=0x1), None, "S32 must be exactly"),
        ("the S32 first-dword check accepts zeros (value 0)", set_row(32, "value", 0, kind="wait_eq", blk="MEM", off=0), None, "S32 must be exactly"),
        ("the S32 first-dword check accepts a non-COMPONENT type (mask 0x1)", set_row(32, "mask", 0x1, kind="wait_eq", blk="MEM", off=0), None, "S32 must be exactly"),
        ("the S32 first-dword check moved after the next dword", f_swap(32, dict(kind="wait_eq", blk="MEM", off=0), dict(kind="read_only", blk="MEM", off=4)), None, "S32 must be exactly"),
        ("the S32 AER log removed", f_rm(32, kind="read_only", blk="CFG", off=0x104), None, "S32 must be exactly"),
        ("an S32 CFG read of another offset", lambda d: (add_row(d, step=32, kind="read_only", blk="CFG", off=0x88, restore=None, log=True), resha(d)), None, "S32 must be exactly"),
        ("the S32 window restore dropped from the teardown", f_tear_reordered(lambda t: t[1:]), None, "S32 must be exactly"),
        ("the S32 window restore read-back dropped from the teardown", f_tear_reordered(lambda t: t[:1] + t[2:]), None, "teardown must start with exactly the S32"),
        ("the S32 window restore not first in the teardown", f_tear_reordered(lambda t: t[2:5] + t[:2] + t[5:]), None, "S32 must be exactly"),
        ("the S32 window restore left at the EROM page", lambda d: (d["teardown"][0].__setitem__("value", 0x18105000), resha(d)), None, "teardown must start with exactly the S32"),
        ("the S32 window restore to the S29 original (skips the ChipCommon step)", lambda d: (d["teardown"][0].__setitem__("value", 0x18001000), resha(d)), None, "CFG window is read-only"),
        ("the S32 window restore read-back polls twice", lambda d: (d["teardown"][1].__setitem__("polls", 2), resha(d)), None, "teardown must start with exactly the S32"),
        ("the S32 window restore required only from S31 (not S32)", lambda d: (d["teardown"][0].__setitem__("requires", 31), resha(d)), None, "CFG window is read-only"),
        ("the S32 window restore required by a step beyond the table", lambda d: (d["teardown"][0].__setitem__("requires", 34), resha(d)), None, "malformed"),
        ("a teardown MEM row", tear_append(blk="MEM", off=0, kind="read_only", mask=0xFFFFFFFF, requires=32), None, "BAR0 (MEM)"),
        ("a teardown CFG window write to another value (S32)", tear_append(blk="CFG", off=0x80, kind="mmio_write", mask=0xFFFFFFFF, value=0x18105004, requires=32), None, "CFG window is read-only"),
        ("a second S32 teardown window write", tear_append(blk="CFG", off=0x80, kind="mmio_write", mask=0xFFFFFFFF, value=0x18000000, requires=32), None, "isolation clear"),
        # S33 (research 38, W-C2), AP-16: each defect class at the table level (wrong window value, extra write, extra read offset, restore skipped, gate ignored)
        ("S33 window move to a wrong PMU page (0x18013000)", set_row(33, "value", 0x18013000, kind="mmio_write", blk="CFG", value=0x18012000), None, "CFG window is read-only"),
        ("S33 window move to a wrong ARM CA7 wrapper page (0x18121000)", set_row(33, "value", 0x18121000, kind="mmio_write", blk="CFG", value=0x18120000), None, "CFG window is read-only"),
        ("S33 window move to a wrong SYS_MEM wrapper page (0x18127000)", set_row(33, "value", 0x18127000, kind="mmio_write", blk="CFG", value=0x18126000), None, "CFG window is read-only"),
        ("S33 window move back to ChipCommon (a forward 0x18000000 write)", set_row(33, "value", 0x18000000, kind="mmio_write", blk="CFG", value=0x18120000), None, "CFG window is read-only"),
        ("S33 window moves in another order (ARM wrapper after the SYS_MEM wrapper)", f_swap(33, dict(kind="mmio_write", blk="CFG", value=0x18120000), dict(kind="mmio_write", blk="CFG", value=0x18126000)), None, "S33 must be exactly"),
        ("an extra S33 window write (a fourth move: the core page belongs to S35)", lambda d: (add_row(d, step=33, blk="CFG", off=0x80, value=0x18026000, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("an extra S33 window write to ChipCommon", lambda d: (add_row(d, step=33, blk="CFG", off=0x80, value=0x18000000, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("an S33 Command write", lambda d: (add_row(d, step=33, blk="CFG", off=0x04, value=0x2, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("an S33 BAR write", lambda d: (add_row(d, step=33, blk="CFG", off=0x10, value=0x60000004, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("an S33 MEM write row (PMU pmucontrol)", lambda d: (add_row(d, step=33, kind="mmio_write", blk="MEM", off=0x600, value=0, restore="T01"), resha(d)), None, "BAR0 (MEM)"),
        ("an S33 MEM RMW row (wrapper RESET_CTL)", lambda d: (add_row(d, step=33, kind="mmio_rmw", blk="MEM", off=0x800, mask=1, value=0, restore="T01"), resha(d)), None, "BAR0 (MEM)"),
        ("an S33 PMA write", lambda d: (add_row(d, step=33, blk="PMA", off=0x100, value=0, restore="T01"), resha(d)), None, "S33 must be exactly"),
        ("an S33 ATU write", lambda d: (add_row(d, step=33, blk="ATU", off=0x204, value=0, restore="T01"), resha(d)), None, "S33 must be exactly"),
        ("an S33 GPIO write", lambda d: (add_row(d, step=33, kind="gpio_dat", blk="GPH2", off=4, mask=1, value=0, restore="T01"), resha(d)), None, "S33 must be exactly"),
        ("an extra S33 read offset in the PMU window (0x60C)", set_row(33, "off", 0x60C, kind="read_only", blk="MEM", off=0x608), None, "BAR0 (MEM)"),
        ("an extra S33 read offset in the PMU window (pmutimer 0x614)", set_row(33, "off", 0x614, kind="read_only", blk="MEM", off=0x618), None, "BAR0 (MEM)"),
        ("an extra S33 read offset in the ARM wrapper (ioctrlset 0x400)", set_row(33, "off", 0x400, kind="read_only", blk="MEM", off=0x408), None, "BAR0 (MEM)"),
        ("an extra S33 read offset in the wrapper (resetreadid 0x808)", set_row(33, "off", 0x808, kind="read_only", blk="MEM", off=0x804), None, "BAR0 (MEM)"),
        ("an extra S33 read in the SYS_MEM core page (offset 4)", lambda d: (add_row(d, step=33, kind="read_only", blk="MEM", off=0x4, restore=None, log=True), resha(d)), None, "BAR0 (MEM)"),
        ("an S33 PMU read after the window left the PMU page (0x604 in the ARM wrapper)", lambda d: (add_row(d, step=33, kind="read_only", blk="MEM", off=0x604, restore=None, log=True), resha(d)), None, "BAR0 (MEM)"),
        ("an S33 read at ER_REMAPCONTROL-style offset 0xE00", lambda d: (add_row(d, step=33, kind="read_only", blk="MEM", off=0xE00, restore=None, log=True), resha(d)), None, "BAR0 (MEM)"),
        ("an S33 read at an unaligned offset (0x605)", set_row(33, "off", 0x605, kind="read_only", blk="MEM", off=0x600), None, "BAR0 (MEM)"),
        ("the S33 PMU capabilities read dropped", f_rm(33, kind="read_only", blk="MEM", off=0x604), None, "S33 must be exactly"),
        ("the S33 window move without a restore", set_row(33, "restore", None, kind="mmio_write", blk="CFG", value=0x18012000), None, "S33 must be exactly"),
        ("the S33 window move restored by another teardown row", set_row(33, "restore", "T05", kind="mmio_write", blk="CFG", value=0x18120000), None, "S33 must be exactly"),
        ("an S33 teardown row of its own (a window write)", tear_append(blk="CFG", off=0x80, kind="mmio_write", mask=0xFFFFFFFF, value=0x18000000, requires=33), None, "CFG window is read-only"),
        ("an S33 teardown MEM row", tear_append(blk="MEM", off=0x604, kind="read_only", mask=0xFFFFFFFF, requires=33), None, "BAR0 (MEM)"),
        ("the S33 window restore dropped from the teardown (T01/T02 removed)", f_tear_reordered(lambda t: t[2:]), None, "S"),
        ("the S33 window precondition removed", f_rm(33, kind="wait_eq", blk="CFG", off=0x80, value=0x18105000), None, "S33 must be exactly"),
        ("the S33 window precondition checks another value (ChipCommon)", set_row(33, "value", 0x18000000, kind="wait_eq", blk="CFG", off=0x80, value=0x18105000), None, "S33 must be exactly"),
        ("the S33 PMU window read-back removed", f_rm(33, kind="wait_eq", blk="CFG", off=0x80, value=0x18012000), None, "S33 must be exactly"),
        ("the S33 ARM wrapper window read-back checks another value", set_row(33, "value", 0x18012000, kind="wait_eq", blk="CFG", off=0x80, value=0x18120000), None, "S33 must be exactly"),
        ("the S33 window read-back polls twice", set_row(33, "polls", 2, kind="wait_eq", blk="CFG", off=0x80, value=0x18126000), None, "S33 must be exactly"),
        ("the S33 pmurev guard removed", f_rm(33, kind="wait_eq", blk="MEM", off=0x604), None, "S33 must be exactly"),
        ("the S33 pmurev guard weakened (mask 0x0F)", set_row(33, "mask", 0x0F, kind="wait_eq", blk="MEM", off=0x604), None, "S33 must be exactly"),
        ("the S33 pmurev guard value changed (0x27)", set_row(33, "value", 0x27, kind="wait_eq", blk="MEM", off=0x604), None, "S33 must be exactly"),
        ("the S33 pmurev guard before the logs", f_swap(33, dict(kind="wait_eq", blk="MEM", off=0x604), dict(kind="read_only", blk="MEM", off=0x604)), None, "S33 must be exactly"),
        ("the S33 ARM wrapper RESET_CTL guard removed", f_rm(33, kind="wait_eq", blk="MEM", off=0x800, value=1), None, "S33 must be exactly"),
        ("the S33 ARM wrapper RESET_CTL guard weakened (mask 0)", set_row(33, "mask", 0, kind="wait_eq", blk="MEM", off=0x800, value=1), None, "S33 must be exactly"),
        ("the S33 ARM wrapper RESET_CTL guard back to the upper-bit test (review 16 finding 3: mask 0xFFFFFFFE value 0)", lambda d: ([r.update(mask=0xFFFFFFFE, value=0) for r in d["rows"] if r["step"] == 33 and r["kind"] == "wait_eq" and r["blk"] == "MEM" and r["off"] == 0x800 and r["value"] == 1], resha(d)), None, "S33 must be exactly"),
        ("the S33 ARM wrapper RESET_CTL guard accepts 0 (value 0)", set_row(33, "value", 0, kind="wait_eq", blk="MEM", off=0x800, value=1), None, "S33 must be exactly"),
        ("the S33 AER log removed", f_rm(33, kind="read_only", blk="CFG", off=0x104), None, "S33 must be exactly"),
        # S33 trimmed + S34..S36 (research 40, W-C3), AP-16: each defect class at the table level (wrong value, extra write, wrong order, missing read-back, restore skipped, window chain broken, bankidx other than 0)
        ("S33 still has a SYS_MEM-up gate row", lambda d: (add_row(d, step=33, kind="wait_eq", blk="MEM", off=0x408, mask=3, value=1, polls=1, restore=None), resha(d)), None, "S33 must be exactly"),
        ("S33 ends in a fourth window (the SYS_MEM core page)", lambda d: (add_row(d, step=33, blk="CFG", off=0x80, value=0x18026000, restore="T01"), resha(d)), None, "CFG window is read-only"),
        ("S33 SYS_MEM wrapper RESET_CTL guard removed", f_rm(33, kind="wait_eq", blk="MEM", off=0x800, mask=0xFFFFFFFE), None, "S33 must be exactly"),
        ("S34 SRPWR request value wrong (0x400)", set_row(34, "value", 0x400, kind="mmio_rmw", blk="MEM"), None, "BAR0 (MEM)"),
        ("S34 SRPWR request mask wider (0x700)", set_row(34, "mask", 0x700, kind="mmio_rmw", blk="MEM"), None, "BAR0 (MEM)"),
        ("S34 SRPWR request at another offset (0x1EC)", set_row(34, "off", 0x1EC, kind="mmio_rmw", blk="MEM"), None, "BAR0 (MEM)"),
        ("S34 SRPWR request as a full write", lambda d: (first_row(d, 34, kind="mmio_rmw", blk="MEM").update(kind="mmio_write", mask=0xFFFFFFFF), resha(d)), None, "BAR0 (MEM)"),
        ("S34 SRPWR request moved to S35", set_row(34, "step", 35, kind="mmio_rmw", blk="MEM"), None, "malformed"),
        ("S34 SRPWR request without the core page window (window still 0x18126000)", f_rm(34, kind="mmio_write", blk="CFG", value=0x18001000), None, "BAR0 (MEM)"),
        ("S34 SRPWR write read-back removed", f_rm(34, kind="wait_eq", blk="MEM", off=0x1E8), None, "S34 must be exactly"),
        ("S34 SRPWR status wait removed", f_rm(34, kind="wait_mask", blk="MEM", off=0x1E8), None, "S34 must be exactly"),
        ("S34 SRPWR status wait on another bit (0x10000)", set_row(34, "mask", 0x10000, kind="wait_mask", blk="MEM", off=0x1E8), None, "S34 must be exactly"),
        ("S34 SRPWR status wait made one-shot", set_row(34, "polls", 1, kind="wait_mask", blk="MEM", off=0x1E8), None, "S34 must be exactly"),
        ("S34 SRPWR status wait unbounded (20000 polls)", set_row(34, "polls", 20000, kind="wait_mask", blk="MEM", off=0x1E8), None, "S34 must be exactly"),
        ("S34 SRPWR status wait before the request", f_swap(34, dict(kind="mmio_rmw", blk="MEM"), dict(kind="wait_mask", blk="MEM")), None, "S34 must be exactly"),
        ("S34 window move without a restore", set_row(34, "restore", None, kind="mmio_write", blk="CFG", value=0x18001000), None, "S34 must be exactly"),
        ("S34 window move to the wrong core page (0x18002000)", set_row(34, "value", 0x18002000, kind="mmio_write", blk="CFG", value=0x18001000), None, "CFG window is read-only"),
        ("S34 window move read-back removed", f_rm(34, kind="wait_eq", blk="CFG", off=0x80, value=0x18001000), None, "S34 must be exactly"),
        ("S34 precondition removed", f_rm(34, kind="wait_eq", blk="CFG", off=0x80, value=0x18126000), None, "S34 must be exactly"),
        ("S34 powerctl log (before) removed", f_rm(34, kind="read_only", blk="MEM", off=0x1E8), None, "S34 must be exactly"),
        ("S34 a teardown row of its own", tear_append(blk="MEM", off=0x1E8, kind="mmio_rmw", mask=0x200, value=0, requires=34), None, "BAR0 (MEM)"),
        ("S35 IOCTL value wrong (0x7)", set_row(35, "value", 7, kind="mmio_write", blk="MEM", off=0x408, value=3), None, "BAR0 (MEM)"),
        ("S35 final IOCTL keeps FGC (0x3)", set_row(35, "value", 3, kind="mmio_write", blk="MEM", off=0x408, value=1), None, "BAR0 write rows must be"),
        ("S35 RESET_CTL assert wrong (2)", set_row(35, "value", 2, kind="mmio_write", blk="MEM", off=0x800, value=1), None, "BAR0 (MEM)"),
        ("S35 RESET_CTL release wrong (0x100)", set_row(35, "value", 0x100, kind="mmio_write", blk="MEM", off=0x800, value=0), None, "BAR0 (MEM)"),
        ("S35 IOCTL set through ioctrlset 0x400", set_row(35, "off", 0x400, kind="mmio_write", blk="MEM", off=0x408, value=3), None, "BAR0 (MEM)"),
        ("S35 bankidx value other than 0 (1)", set_row(35, "value", 1, kind="mmio_write", blk="MEM", off=0x10), None, "BAR0 (MEM)"),
        ("S35 bankidx value other than 0 (0x80, the ROM flag)", set_row(35, "value", 0x80, kind="mmio_write", blk="MEM", off=0x10), None, "BAR0 (MEM)"),
        ("S35 bankidx written at another offset (0x14)", set_row(35, "off", 0x14, kind="mmio_write", blk="MEM", off=0x10), None, "BAR0 (MEM)"),
        ("S35 bankidx written in the SYS_MEM wrapper window (core page move dropped)", f_rm(35, kind="mmio_write", blk="CFG", value=0x18026000), None, "BAR0 (MEM)"),
        ("S35 an extra bank (a second bankidx write)", lambda d: (add_row(d, step=35, kind="mmio_write", blk="MEM", off=0x10, value=0, restore=None), resha(d)), None, "BAR0 write rows must be"),
        ("S35 an extra repeat of the RESET_CTL assert", lambda d: (add_row(d, step=35, kind="mmio_write", blk="MEM", off=0x800, value=1, restore=None), resha(d)), None, "BAR0 (MEM)"),
        ("S35 an extra BAR0 write (ioctrlclear 0x404)", lambda d: (add_row(d, step=35, kind="mmio_write", blk="MEM", off=0x404, value=0, restore=None), resha(d)), None, "BAR0 (MEM)"),
        ("S35 an extra BAR0 write (resetreadid 0x808)", lambda d: (add_row(d, step=35, kind="mmio_write", blk="MEM", off=0x808, value=0, restore=None), resha(d)), None, "BAR0 (MEM)"),
        ("S35 an extra BAR0 RMW", lambda d: (add_row(d, step=35, kind="mmio_rmw", blk="MEM", off=0x408, mask=2, value=0, restore=None), resha(d)), None, "BAR0 (MEM)"),
        ("S35 a BAR0 write with a restore key", set_row(35, "restore", "T05", kind="mmio_write", blk="MEM", off=0x10), None, "S35 must be exactly"),
        ("S35 a BAR0 write row in S33", lambda d: (add_row(d, step=33, kind="mmio_write", blk="MEM", off=0x800, value=1, restore=None), resha(d)), None, "BAR0 (MEM)"),
        ("S35 the IOCTL write before the RESET_CTL assert", f_swap(35, dict(kind="mmio_write", blk="MEM", off=0x800, value=1), dict(kind="mmio_write", blk="MEM", off=0x408, value=3)), None, "S35 must be exactly"),
        ("S35 the release before the IOCTL write", f_swap(35, dict(kind="mmio_write", blk="MEM", off=0x408, value=3), dict(kind="mmio_write", blk="MEM", off=0x800, value=0)), None, "S35 must be exactly"),
        ("S35 the final IOCTL before the release", f_swap(35, dict(kind="mmio_write", blk="MEM", off=0x800, value=0), dict(kind="mmio_write", blk="MEM", off=0x408, value=1)), None, "S35 must be exactly"),
        ("S35 the RESET_CTL assert read-back removed", f_rm(35, kind="wait_eq", blk="MEM", off=0x800, mask=1, value=1), None, "S35 must be exactly"),
        ("S35 the IOCTL read-back removed", f_rm(35, kind="wait_eq", blk="MEM", off=0x408, mask=0x3FFF, value=3), None, "S35 must be exactly"),
        ("S35 the IOCTL read-back weakened (mask 0)", set_row(35, "mask", 0, kind="wait_eq", blk="MEM", off=0x408, mask=0x3FFF, value=3), None, "S35 must be exactly"),
        ("S35 the RESET_CTL release wait removed", f_rm(35, kind="wait_eq", blk="MEM", off=0x800, mask=1, value=0, polls=10), None, "S35 must be exactly"),
        ("S35 the RESET_CTL release wait one-shot (no bounded poll)", set_row(35, "polls", 1, kind="wait_eq", blk="MEM", off=0x800, mask=1, value=0, polls=10), None, "S35 must be exactly"),
        ("S35 the final IOCTL read-back removed", f_rm(35, kind="wait_eq", blk="MEM", off=0x408, mask=0x3FFF, value=1), None, "S35 must be exactly"),
        ("S35 a RESET_ST wait removed", f_rm(35, kind="wait_eq", blk="MEM", off=0x804, polls=30), None, "S35 must be exactly"),
        ("S35 the RESET_ST wait unbounded", set_row(35, "polls", 20000, kind="wait_eq", blk="MEM", off=0x804, polls=30), None, "S35 must be exactly"),
        ("S35 the 10 us delay after the assert removed", f_rm(35, kind="delay", delay_us=10), None, "S35 must be exactly"),
        ("S35 the up gate removed (IOCTL)", f_rm(35, kind="wait_eq", blk="MEM", off=0x408, mask=3, value=1), None, "S35 must be exactly"),
        ("S35 the up gate removed (RESET_CTL bit 0)", f_rm(35, kind="wait_eq", blk="MEM", off=0x800, mask=1, value=0, polls=1), None, "S35 must be exactly"),
        ("S35 the up gate weakened (IOCTL mask 0x2)", set_row(35, "mask", 0x2, kind="wait_eq", blk="MEM", off=0x408, mask=3, value=1), None, "S35 must be exactly"),
        ("S35 the up gate weakened (IOCTL value 0)", set_row(35, "value", 0, kind="wait_eq", blk="MEM", off=0x408, mask=3, value=1), None, "S35 must be exactly"),
        ("S35 the up gate weakened (RESET_CTL mask 0)", set_row(35, "mask", 0, kind="wait_eq", blk="MEM", off=0x800, mask=1, value=0, polls=1), None, "S35 must be exactly"),
        ("S35 the up gate polls many times (a wait loop instead of a stop)", set_row(35, "polls", 20000, kind="wait_eq", blk="MEM", off=0x408, mask=3, value=1), None, "S35 must be exactly"),
        ("S35 the up gate RESET_ST removed", f_rm(35, kind="wait_eq", blk="MEM", off=0x804, mask=0xFFFFFFFF, polls=1), None, "S35 must be exactly"),
        ("S35 the up gate RESET_ST expects non-zero", set_row(35, "value", 1, kind="wait_eq", blk="MEM", off=0x804, polls=1), None, "S35 must be exactly"),
        ("S35 the core page entered before the up gate", f_swap(35, dict(kind="wait_eq", blk="MEM", off=0x408, mask=3, value=1), dict(kind="mmio_write", blk="CFG", value=0x18026000)), None, "BAR0 (MEM)"),
        ("S35 the core page window move without a restore", set_row(35, "restore", None, kind="mmio_write", blk="CFG", value=0x18026000), None, "S35 must be exactly"),
        ("S35 the wrapper window move without a restore", set_row(35, "restore", None, kind="mmio_write", blk="CFG", value=0x18126000), None, "S35 must be exactly"),
        ("S35 the core page window read-back removed", f_rm(35, kind="wait_eq", blk="CFG", off=0x80, value=0x18026000), None, "S35 must be exactly"),
        ("S35 the wrapper window read-back removed", f_rm(35, kind="wait_eq", blk="CFG", off=0x80, value=0x18126000), None, "S35 must be exactly"),
        ("S35 the window move to the ARM wrapper instead of the SYS_MEM wrapper", set_row(35, "value", 0x18120000, kind="mmio_write", blk="CFG", value=0x18126000), None, "CFG window is read-only"),
        ("S35 the SYS_MEM reset in the PCIe core page window (move dropped)", f_rm(35, kind="mmio_write", blk="CFG", value=0x18126000), None, "BAR0 (MEM)"),
        ("S35 bankidx read-back removed", f_rm(35, kind="wait_eq", blk="MEM", off=0x10), None, "S35 must be exactly"),
        ("S35 bankidx read-back before the logs", f_swap(35, dict(kind="wait_eq", blk="MEM", off=0x10), dict(kind="read_only", blk="MEM", off=0x10)), None, "S35 must be exactly"),
        ("S35 coreinfo log removed", f_rm(35, kind="read_only", blk="MEM", off=0), None, "S35 must be exactly"),
        ("S35 bankinfo log removed", f_rm(35, kind="read_only", blk="MEM", off=0x40), None, "S35 must be exactly"),
        ("S35 bankidx written before the coreinfo log", f_swap(35, dict(kind="read_only", blk="MEM", off=0), dict(kind="mmio_write", blk="MEM", off=0x10)), None, "S35 must be exactly"),
        ("S35 a read of the SYS_MEM core extracoreinfo (0x8)", lambda d: (add_row(d, step=35, kind="read_only", blk="MEM", off=0x8, restore=None, log=True), resha(d)), None, "BAR0 (MEM)"),
        ("S35 the AER log removed", f_rm(35, kind="read_only", blk="CFG", off=0x104), None, "S35 must be exactly"),
        ("S35 a teardown row of its own (an undo write)", tear_append(blk="MEM", off=0x800, kind="mmio_write", mask=0xFFFFFFFF, value=1, requires=35), None, "BAR0 (MEM)"),
        ("S36 CPUHALT value wrong (0x22: no CPUHALT)", set_row(36, "value", 0x22, kind="mmio_write", blk="MEM", off=0x408, value=0x23), None, "BAR0 (MEM)"),
        ("S36 CPUHALT value wrong (0x63)", set_row(36, "value", 0x63, kind="mmio_write", blk="MEM", off=0x408, value=0x23), None, "BAR0 (MEM)"),
        ("S36 final IOCTL without CPUHALT (0x1)", set_row(36, "value", 1, kind="mmio_write", blk="MEM", off=0x408, value=0x21), None, "BAR0 (MEM)"),
        ("S36 final IOCTL with FGC (0x23)", set_row(36, "value", 0x23, kind="mmio_write", blk="MEM", off=0x408, value=0x21), None, "BAR0 write rows must be"),
        ("S36 an extra ARM BAR0 write (a second halt)", lambda d: (add_row(d, step=36, kind="mmio_write", blk="MEM", off=0x408, value=0x21, restore=None), resha(d)), None, "BAR0 write rows must be"),
        ("S36 ARM window move to the SYS_MEM wrapper (ARM reset values written to SYS_MEM)", set_row(36, "value", 0x18126000, kind="mmio_write", blk="CFG", value=0x18120000), None, "CFG window is read-only"),
        ("S36 ARM window move without a restore", set_row(36, "restore", None, kind="mmio_write", blk="CFG", value=0x18120000), None, "S36 must be exactly"),
        ("S36 ARM window read-back removed", f_rm(36, kind="wait_eq", blk="CFG", off=0x80, value=0x18120000), None, "S36 must be exactly"),
        ("S36 the ARM halt IOCTL read-back removed", f_rm(36, kind="wait_eq", blk="MEM", off=0x408, mask=0x3FFF, value=0x23), None, "S36 must be exactly"),
        ("S36 the final ARM IOCTL read-back removed", f_rm(36, kind="wait_eq", blk="MEM", off=0x408, mask=0x3FFF, value=0x21), None, "S36 must be exactly"),
        ("S36 the ARM release wait removed", f_rm(36, kind="wait_eq", blk="MEM", off=0x800, mask=1, value=0, polls=10), None, "S36 must be exactly"),
        ("S36 the ARM halt moved into S35 (ARM before the SYS_MEM core page)", set_row(36, "step", 35, kind="mmio_write", blk="CFG", value=0x18120000), None, "CFG window is read-only"),
        ("S36 the final IOCTL log (CPUHALT latched) removed", f_rm(36, kind="read_only", blk="MEM", off=0x408), None, "S36 must be exactly"),
        ("S36 a teardown row of its own", tear_append(blk="CFG", off=0x80, kind="mmio_write", mask=0xFFFFFFFF, value=0x18000000, requires=36), None, "CFG window is read-only"),
        ("the window restore required only by S36 (restore skipped for earlier stops)", lambda d: (d["teardown"][0].__setitem__("requires", 36), resha(d)), None, "CFG window is read-only"),
        ("the window restore required only by S33", lambda d: (d["teardown"][0].__setitem__("requires", 33), resha(d)), None, "CFG window is read-only"),
        ("the window restore read-back removed from the teardown (S34..S36 stops unobserved)", f_tear_reordered(lambda t: t[:1] + t[2:]), None, "teardown must start with exactly the S32"),
        ("a table with a step 37", lambda d: (add_row(d, step=37), resha(d)), None, "malformed"),
        ("a changed value (hash pin)", lambda d: (d["rows"][7].__setitem__("value", 2), resha(d)), _WLAN_SEQ_SHA, "audited"),
    ]
    caught = 0
    for label, fn, pin, why in cases:
        d = copy.deepcopy(doc)
        fn(d)
        try:
            h = boards.wlan_seq_render(d)
            check_wlan_seq_table(d, h, pin=pin)
        except (AssertionError, ValueError) as exc:
            caught += 1
            print(f"self-test caught: wlan_seq {label} -> {exc}")
            assert why in str(exc) or why == "malformed", (label, str(exc))
        else:
            raise AssertionError(f"wlan_seq self-test FAILED to catch: {label}")
    # the header must be the table: edit one cell of the header
    bad = header.replace(", 0x28u, 0u, 0u },", ", 0x29u, 0u, 0u },", 1)
    assert bad != header
    try:
        check_wlan_seq_table(doc, bad)
    except AssertionError as exc:
        caught += 1
        print(f"self-test caught: wlan_seq a header cell differing from the JSON -> {exc}")
    else:
        raise AssertionError("wlan_seq self-test FAILED to catch a header cell")
    # a hand edit that keeps the old hash is refused when the header is generated
    d = copy.deepcopy(doc)
    d["rows"][7]["value"] = 2
    try:
        boards.wlan_seq_render(d)
    except ValueError as exc:
        caught += 1
        print(f"self-test caught: wlan_seq a row edited without the hash -> {exc}")
    else:
        raise AssertionError("wlan_seq self-test FAILED to catch an edit with the old hash")
    print(f"PASS wlan_seq table self-test: {caught}/{len(cases) + 2} bad tables rejected")
    return caught


_WLANLINK_KW = dict(base_macro=None, strict=True, guard_funcs=("w32_allowed",))
_WLANLINK_ALLOWED_UNDEF = {"try_read32", "try_write32", "con_str", "counter_freq", "wait_ticks", "touch_ticks", "pstore_save", "wlan_link_iso_set", "wlan_link_iso_clear"}
_WLANLINK_PINS = {
    "w32_allowed": "if(((old^v)&~mask)!=0u)return0;if(pa>=WLAN_GPP14&&pa<WLAN_GPP14+0x18u)return(mask&~gpio_field_mask((uint32_t)(pa-WLAN_GPP14),1u<<(unsigned)WLAN_GPP14_EN_PIN))==0u&&mask!=0u;if(pa>=WLAN_GPH2&&pa<WLAN_GPH2+0x18u)return(mask&~gpio_field_mask((uint32_t)(pa-WLAN_GPH2),(1u<<(unsigned)WLAN_GPH2_PERST_PIN)|2u))==0u&&mask!=0u;"
                   "if(pa>=WLAN_PMA&&pa<WLAN_PMA+0x2000u)return!(pa&3u);if(pa>=WLAN_PCS&&pa<WLAN_PCS+0x1000u)return!(pa&3u);if(pa>=WLAN_IA&&pa<WLAN_IA+0x180u)return!(pa&3u);"
                   "if(pa>=WLAN_ELBI&&pa<WLAN_ELBI+0x2000u)returnelbi_ok((uint32_t)(pa-WLAN_ELBI));if(pa==WLAN_DBI+0x890u)return1;if(pa>=WLAN_ATU&&pa<WLAN_ATU+0x1000u)returnmask==0xFFFFFFFFu&&atu_ok((uint32_t)(pa-WLAN_ATU),v,c->r1ok);if(pa>=WLAN_CFG&&pa<WLAN_CFG+0x1000u)returnmask==0xFFFFFFFFu&&cfg_ok((uint32_t)(pa-WLAN_CFG),old,v,c);if(pa>=WLAN_MEM&&pa<WLAN_MEM+0x1000u)return!c->tear&&memw_ok(c->win,(uint32_t)(pa-WLAN_MEM),old,v,c->pred);return0;",
    "cfg_ok": "intlo=(off==0x10u||off==0x18u),hi=(off==0x14u||off==0x1cu);if(off==0x04u)returnv==0u||(v==0x2u&&(old&0xFFFFu)==0u&&c->bar0==0x60000004u);if(off==0x80u)return(v==0x18001000u&&(c->tear||(old==0x18126000u&&(c->cmd&0xFFFFu)==0x0002u&&c->bar0==0x60000004u)))||(v==0x18000000u&&(old==0x18001000u||(c->tear&&(old==0x18105000u||old==0x18000000u||old==0x18012000u||old==0x18120000u||old==0x18126000u||old==0x18026000u))))||(v==0x18105000u&&!c->tear&&old==0x18000000u&&(c->cmd&0xFFFFu)==0x0002u&&c->bar0==0x60000004u)||(!c->tear&&(c->cmd&0xFFFFu)==0x0002u&&c->bar0==0x60000004u&&((v==0x18012000u&&old==0x18105000u)||(v==0x18120000u&&old==0x18012000u)||(v==0x18126000u&&old==0x18120000u)||(v==0x18126000u&&old==0x18001000u)||(v==0x18026000u&&old==0x18126000u)||(v==0x18120000u&&old==0x18026000u)));if(!lo&&!hi)return0;if(v==(lo?0x4u:0u))return1;if((c->cmd&0xFFFFu)!=0u)return0;if(v==0xFFFFFFFFu)returnold==(lo?0x4u:0u);returnlo&&old==0x4u&&v==(off==0x10u?0x60000004u:0x60400004u);",
    "mem_ok": "if(win==0x18105000u)returnoff<0xE00u;if(win==0x18000000u)returnoff==0u||off==0xACu||off==0xFCu||off==0x1E0u;if(win==0x18012000u)returnoff==0x600u||off==0x604u||off==0x608u||off==0x618u||off==0x61Cu;if(win==0x18120000u||win==0x18126000u)returnoff==0x408u||off==0x800u||off==0x804u;if(win==0x18001000u)returnoff==0x1E8u;if(win==0x18026000u)returnoff==0u||off==0x10u||off==0x40u;return0;",
    "memw_ok": "uint32_twant;if(win==0x18001000u)returnoff==0x1E8u&&old!=0xFFFFFFFFu&&(old&0xF0000000u)!=0u&&v==(old|0x200u);if(win==0x18126000u||win==0x18120000u){want=win==0x18126000u?0x3u:0x23u;if(off==0x800u)returnv==1u||(v==0u&&(pred&0x3FFFu)==want);if(off==0x408u)return(v==want&&(pred&1u)==1u)||(v==(want&~0x2u)&&(pred&1u)==0u&&(old&0x3FFFu)==want);return0;}if(win==0x18026000u)returnoff==0x10u&&v==0u&&pred!=0xFFFFFFFFu&&(pred&0x3E0u)==0u&&(pred&0x1Fu)!=0u;return0;",
    "memw_pred": "if(off==0x800u)return0x408u;if(off==0x408u)return0x800u;if(off==0x10u)return0u;return0xFFFFFFFFu;",
    "atu_ok": "return(off==0x000u&&(v==0x4u||v==0u))||(off==0x004u&&(v==0x80000000u||v==0u))||(off==0x008u&&(v==(uint32_t)WLAN_CFG||v==0u))||(off==0x00cu&&v==0u)||(off==0x010u&&(v==(uint32_t)WLAN_CFG+0x1fffu||v==0xfffu))||(off==0x014u&&(v==0x01000000u||v==0u))||(off==0x018u&&v==0u)||(off==0x204u&&(v==0u||(v==0x80000000u&&r1ok)))||(off==0x208u&&(v==0x60000000u||v==0u))||(off==0x210u&&(v==0x6000FFFFu||v==0xfffu))||(off==0x214u&&(v==0x60000000u||v==0u));",
    "elbi_ok": "returnoff==0x054u||off==0x080u||off==0x388u||off==0x3a0u||off==0x3a4u||off==0x3a8u||off==0x3bcu||off==0x474u||off==0x1078u||off==0x1400u||off==0x1404u||off==0x1408u;",
    "size_of": "if(blk==WLB_GPP14||blk==WLB_GPH2)return0x18u;if(blk==WLB_PMA||blk==WLB_ELBI||blk==WLB_CFG)return0x2000u;if(blk==WLB_PCS||blk==WLB_IA||blk==WLB_DBI||blk==WLB_ATU||blk==WLB_MEM)return0x1000u;if(blk==WLB_PMU)return0x4000u;return0u;",
    "w32_blk_ok": "return!(blk==WLB_CFG&&off>=0x1000u);",
    "ctx_read": "uint32_tlb=0,lim=0,lt=0,cr1=1,ub=1,ut=1;c->cmd=0xFFFFu;c->bar0=0xFFFFFFFFu;c->r1ok=0;if(blk==WLB_CFG){if(!try_read32(WLAN_CFG+4u,&c->cmd))c->cmd=0xFFFFu;if(!try_read32(WLAN_CFG+0x10u,&c->bar0))c->bar0=0xFFFFFFFFu;}if(blk==WLB_ATU)c->r1ok=try_read32(WLAN_ATU+0x208u,&lb)&&try_read32(WLAN_ATU+0x210u,&lim)&&try_read32(WLAN_ATU+0x214u,&lt)&&lb==0x60000000u&&lim==0x6000FFFFu&&lt==0x60000000u&&try_read32(WLAN_ATU+0x200u,&cr1)&&try_read32(WLAN_ATU+0x20Cu,&ub)&&try_read32(WLAN_ATU+0x218u,&ut)&&cr1==0u&&ub==0u&&ut==0u;",
    "gpio_field_mask": "uint32_tm=0,w;if(off==0x00u||off==0x08u||off==0x0cu||off==0x14u)w=4u;elseif(off==0x04u)w=1u;elseif(off==0x10u)w=2u;elsereturn0;for(unsignedpin=0;pin<8u;pin++)if(pins&(1u<<pin))m|=((1u<<w)-1u)<<(pin*w);returnm;",
}

# sha256 of the whitespace-stripped body of each safety-critical function (reviewed text, 2026-10-23). A change needs a new review AND a new pin here: these are what guarantee the teardown runs, a
# block is probed before it is touched, the address is bounded and the writer's mask rule holds (Opus review H1: the text pins above cover only the allowlist).
_WLANLINK_BODY_SHA = {
    "w32": "9db43b4e13fcc238c9984b518d8a3b86aff50c6ba1bf011e7fd471fd456777d4",
    "get_row": "d73e01074e7c551f652a4322ee7bfde2a53d8f3c0a76087a53c8247c354406c9",
    "r32": "92a4305f153302ec38e0192d309e90064086bd8511c51dcf80656def295ca0be",
    "pa_of": "9d86b866272f0aa42d1adb2ad38ddecf54d9bba0d5e69825d725eb95ec04a2a6",
    "base_of": "1d9d26d25b5a2af62e1b253d336f131a89e1f98cc80311a38b8477c803292cda",
    "need_probe": "44c0b22ce25cae99db97ca86bcec9e471ebb8fcbfa077005de813f1073652b02",
    "delay_us": "10a70ec802b5fa345f4b514fa73dd3b1a3e383a977aedb6f4f5f1271c386dfcd",
    "poll_row": "2bab73a5704648f41011acc830dfa02985d0a67c197f790eb79765ab0b5a6877",
    "run_row": "03d3e0ed514fba523defc5b89639a8f3f895f3537343f51ba4d2fcbc5e43b96c",
    "wlan_link_run": "3b3f2e45e3fb2075da616ebe3e1e4463847f8e7b05cee3d6f80c624e93dc08a0",
}


def check_wlanlink_source_text(src: str, name: str = "wlanlink.c") -> None:
    check_usbgadget_source_text(src, name, **_WLANLINK_KW)
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    if "__attribute__((noipa))staticintmem_ok(uint32_twin,uint32_toff)" not in norm or len(re.findall(r"\bmem_ok\s*\(", code)) != 2:
        raise AssertionError(f"{name}: mem_ok must be noipa (the object audit evaluates the compiled function) and have exactly one caller, r32")
    if "__attribute__((noipa))staticintmemw_ok(uint32_twin,uint32_toff,uint32_told,uint32_tv,uint32_tpred)" not in norm or len(re.findall(r"\bmemw_ok\s*\(", code)) != 2 or "returnc->win" in norm:
        raise AssertionError(f"{name}: memw_ok must be noipa (the object audit evaluates the compiled function) and have exactly one caller, w32_allowed")
    if "__attribute__((noipa))staticuint32_tmemw_pred(uint32_toff)" not in norm or len(re.findall(r"\bmemw_pred\s*\(", code)) != 2:
        raise AssertionError(f"{name}: memw_pred must be noipa and have exactly one caller, w32")
    if "__attribute__((noipa))staticintr32(" not in norm:
        raise AssertionError(f"{name}: r32 must be noipa (review 10 L5: the object audit checks the compiled MEM guard)")
    for bad in ("volatile", "__asm__", "asm", "goto", "smc", "hvc"):
        if re.search(r"\b" + bad + r"\b", code):
            raise AssertionError(f"{name}: '{bad}' is banned here (the SMC steps are wlan_link_iso_set/clear in wlansmc.c)")
    if re.search(r"\bdo\s*\{|for\s*\(\s*;", code):
        raise AssertionError(f"{name}: unbounded loop construct")
    for fn, want in _WLANLINK_PINS.items():
        a, b = _c_function_span(code, fn)
        body = re.sub(r"\s+", "", code[a:b])
        body = body[body.index("{") + 1:-1]
        if body != want:
            raise AssertionError(f"{name}: {fn} is not the audited text: {body}")
    import hashlib
    for fn, want in _WLANLINK_BODY_SHA.items():
        a, b = _c_function_span(code, fn)
        body = re.sub(r"\s+", "", code[a:b])
        got = hashlib.sha256(body[body.index("{") + 1:-1].encode()).hexdigest()
        if got != want:
            raise AssertionError(f"{name}: {fn} is not the reviewed text (sha256 {got}); a change needs a new review and a new pin in tools/test-bluejay-boot.py")
    # reads can have side effects on MMIO: try_read32 only inside r32 (PMU words 0x3EC0/0x3EC4 + block windows + the two BAR0 guard words), w32 (the RMW old value) and ctx_read (the state the stateful allowlist rules need)
    spans = [_c_function_span(code, f) for f in ("r32", "w32", "ctx_read")]
    for m in re.finditer(r"\btry_read32\s*\(", code):
        if not any(x <= m.start() < y for x, y in spans):
            raise AssertionError(f"{name}: try_read32 outside r32/w32/ctx_read")
    if len(re.findall(r"\bctx_read\s*\(", code)) != 2 or "ctx_read(r.blk,&c);" not in norm:
        raise AssertionError(f"{name}: ctx_read has exactly one call site: w32")
    # the single writer
    wa, wb = _c_function_span(code, "w32")
    w = re.sub(r"\s+", "", code[wa:wb])
    for need in ("if(!get_row(idx,tear,&r))return-2;", "if(r.kind!=WLK_GPIO_DAT&&r.kind!=WLK_GPIO_CON&&r.kind!=WLK_MMIO_RMW&&r.kind!=WLK_MMIO_WRITE)return-2;", "if(!tear&&r.step>WLAN_LINK_N)return-2;",
                 "if(!w32_blk_ok(r.blk,r.off))return-2;pa=pa_of(r.blk,r.off);if(!pa)return-2;", "if(r.blk==WLB_MEM||(r.kind==WLK_MMIO_WRITE&&r.blk!=WLB_CFG)){old=r.val;}elseif(!try_read32(pa,&old)){return-1;}", "ctx_read(r.blk,&c);", "c.win=c.pred=0u;",
                 "if(r.blk==WLB_MEM){uint32_tpr=memw_pred(r.off);if(tear||!try_read32(WLAN_CFG+0x80u,&c.win)||!r32(WLB_MEM,r.off,&old)||(pr!=0xFFFFFFFFu&&!r32(WLB_MEM,pr,&c.pred)))return-2;}", "v=(old&~r.mask)|(r.val&r.mask);",
                 "if(!w32_allowed(pa,r.mask,old,v,&c))return-2;", "returntry_write32(pa,v)?0:-1;"):
        if need not in w:
            raise AssertionError(f"{name}: w32 lost '{need}'")
    if "__attribute__((noipa))staticintw32(unsignedidx,inttear)" not in norm:
        raise AssertionError(f"{name}: w32 must be noipa with the signature (unsigned idx, int tear): the row comes from the const table, never from the caller")
    if len(re.findall(r"\bw32\s*\(", code)) != 2 or "e=w32(idx,tear);" not in norm:
        raise AssertionError(f"{name}: w32 has exactly one call site: run_row")
    if len(re.findall(r"\bwlan_link_iso_set\s*\(", code)) != 1 or len(re.findall(r"\bwlan_link_iso_clear\s*\(", code)) != 1 or "e=(r.val&1u)?wlan_link_iso_set(rd):wlan_link_iso_clear(rd);" not in norm:
        raise AssertionError(f"{name}: the SMC steps: exactly one call each, only for a smc_iso row")
    if "if(r.blk!=WLB_PMU||r.off!=0x3EC4u||r.mask!=1u)returnST_REFUSED;" not in norm:
        raise AssertionError(f"{name}: the SMC row must be PMU 0x3EC4 mask 1")
    # pins: constants, probe rule, refusal before the first write, teardown always, bounded loops
    for need in ("_Static_assert(WLAN_ATU==WLAN_DBI+0x300000u,", "_Static_assert(WLAN_CFG==0x60FFE000UL,", "_Static_assert(WLAN_MEM==0x60000000UL,", "#defineDELAY_MAX_US250000u", "#definePOLL_CAP20000u", "#defineWLAN_LINK_NWLAN_SEQ_DEFAULT_N", "if(r.a>DELAY_MAX_US)returnST_REFUSED;",
                 "if(r.blk!=WLB_NONE&&(need_probe(r.blk)&~*probed)&&!(r.kind==WLK_READ_ONLY&&r.a==1u))returnST_REFUSED;",
                 "if(r.a==1u)*probed|=need_probe(r.blk);",
                 "if(r.a&2u)step_line('R',r.step,\"\",1,idx,1,v,0,0);if(r.a&4u){if(v==0xFu)*probed|=EOT_SEEN;elseif(r.a&8u)returnST_REFUSED;}returnST_OK;",
                 "if((probed&EOT_SEEN)&&r.kind==WLK_READ_ONLY&&(r.a&4u))continue;", "#defineEOT_SEEN(1u<<31)", "if(blk==WLB_PMU&&off!=0x3EC0u&&off!=0x3EC4u)return0;",
                 "if(blk==WLB_MEM){uint32_tcr2=0,cmd=0,win=0,r1[4]={1,1,1,1};if(!try_read32(WLAN_CFG+0x80u,&win)||!mem_ok(win,off)||!try_read32(WLAN_ATU+0x204u,&cr2)||cr2!=0x80000000u||!try_read32(WLAN_CFG+4u,&cmd)||(cmd&0xFFFFu)!=0x0002u)return0;if(!try_read32(WLAN_ATU+0x200u,&r1[0])||!try_read32(WLAN_ATU+0x208u,&r1[1])||!try_read32(WLAN_ATU+0x210u,&r1[2])||!try_read32(WLAN_ATU+0x214u,&r1[3])||r1[0]!=0u||r1[1]!=0x60000000u||r1[2]!=0x6000FFFFu||r1[3]!=0x60000000u)return0;}",
                 "if(!nrows){step_line('S',s,\"refused:UNKNOWNstep(nocitedrows),nothingwritten\",0,0,0,0,0,0);return2;}",
                 "if(need_probe(r.blk)&~probed)continue;", "for(i=0;started&&i<WLAN_SEQ_NTEAR;i++){", "if(!get_row(i,1,&r)||r.step>hi)continue;",
                 "if(!counter_freq()){", "if(stopped==ST_OK)step_line('S',s,\"ok\"", "for(s=0;s<=WLAN_LINK_N&&stopped==ST_OK;s++){", "pstore_save();//the lastline"):
        if need.replace("//the lastline", "") not in norm:
            raise AssertionError(f"{name}: lost '{need}'")
    ra, rb = _c_function_span(code, "wlan_link_run")
    run = re.sub(r"\s+", "", code[ra:rb])
    plan, main, tdn = run.find("for(s=0;s<=WLAN_LINK_N;s++){"), run.find("for(s=0;s<=WLAN_LINK_N&&stopped==ST_OK;s++){"), run.find("for(i=0;started&&i<WLAN_SEQ_NTEAR;i++){")
    if not 0 < plan < main < tdn or run.count("run_row(") != 2 or "if(started&&" in run or "if(stopped" in run[tdn - 40:tdn]:
        raise AssertionError(f"{name}: order = plan check, steps, teardown (unconditional)")
    allowed_for = [r"i<sizeof\(t\)&&v", r"i<digits&&pos\+1<cap", r"pin<8u", r"i<r->b&&i<POLL_CAP", r"i<16u", r"s<=WLAN_LINK_N;", r"s<=WLAN_LINK_N&&stopped==ST_OK", r"i<WLAN_SEQ_NROWS", r"started&&i<WLAN_SEQ_NTEAR"]
    for m in re.finditer(r"\bfor\s*\(((?:[^()]|\([^()]*\))*)\)", code):
        h = re.sub(r"\s+", "", m.group(1))
        if not any(re.search(a, h) for a in allowed_for):
            raise AssertionError(f"{name}: unaudited loop header 'for ({m.group(1)})'")
    for m in re.finditer(r"\bwhile\b", code):
        if not any(a <= m.start() < b for a, b in [_c_function_span(code, "put_txt"), _c_function_span(code, "put_dec")]):
            raise AssertionError(f"{name}: 'while' outside the string helpers")


def wlanlink_source_selftest() -> int:
    good = (pathlib.Path(__file__).resolve().parents[1] / "shim" / "wlanlink.c").read_text()
    check_wlanlink_source_text(good)  # control must pass
    ins = "static int get_row"
    cases = [
        ("inline asm store", good.replace(ins, 'static void evil(void) { __asm__ volatile("str wzr, [x0]"); }\n' + ins, 1)),
        ("second write path", good.replace(ins, "static void evil(void) { try_write32(0x14440004UL, 0); }\n" + ins, 1)),
        ("w32 skips the allowlist", good.replace("if (!w32_allowed(pa, r.mask, old, v, &c))", "if (0)", 1)),
        ("w32 ignores the step bound", good.replace("if (!tear && r.step > WLAN_LINK_N)", "if (0)", 1)),
        ("w32 takes the row from the caller", good.replace("static int w32(unsigned idx, int tear)", "static int w32(unsigned idx, int tear, uint32_t extra)", 1)),
        ("allowlist: GPP14 any pin", good.replace("1u << (unsigned)WLAN_GPP14_EN_PIN)) == 0u && mask != 0u;", "0xffu)) == 0u && mask != 0u;", 1)),
        ("allowlist: GPH2 pin 2", good.replace("(1u << (unsigned)WLAN_GPH2_PERST_PIN) | 2u", "(1u << (unsigned)WLAN_GPH2_PERST_PIN) | 6u", 1)),
        ("allowlist: DBI +0x8bc", good.replace("pa == WLAN_DBI + 0x890u", "pa == WLAN_DBI + 0x8bcu", 1)),
        ("allowlist: ELBI +0x2c8", good.replace("off == 0x054u ||", "off == 0x054u || off == 0x2c8u ||", 1)),
        ("allowlist: PMA window wider", good.replace("pa < WLAN_PMA + 0x2000u", "pa < WLAN_PMA + 0x4000u", 1)),
        ("window size", good.replace("if (blk == WLB_PMA || blk == WLB_ELBI || blk == WLB_CFG)\n\t\treturn 0x2000u;", "if (blk == WLB_PMA || blk == WLB_ELBI || blk == WLB_CFG)\n\t\treturn 0x20000u;", 1)),
        ("no probe rule", good.replace("(need_probe(r.blk) & ~*probed) && ", "0 && ", 1)),
        ("probe never recorded", good.replace("\t\tif (r.a == 1u)\n\t\t\t*probed |= need_probe(r.blk);", "", 1)),
        ("no UNKNOWN-step refusal", good.replace("\t\tif (!nrows) {", "\t\tif (0) {", 1)),
        ("teardown conditional on the result", good.replace("for (i = 0; started && i < WLAN_SEQ_NTEAR; i++) {", "for (i = 0; started && stopped == ST_OK && i < WLAN_SEQ_NTEAR; i++) {", 1)),
        ("teardown skips nothing it should run", good.replace("if (!get_row(i, 1, &r) || r.step > hi)", "if (!get_row(i, 1, &r))", 1)),
        ("a second smc call", good.replace("e = (r.val & 1u) ? wlan_link_iso_set(rd) : wlan_link_iso_clear(rd);", "e = (r.val & 1u) ? wlan_link_iso_set(rd) : wlan_link_iso_clear(rd);\n\t\t(void)wlan_link_iso_set(rd);", 1)),
        ("a retry loop", good.replace("\t\t\tst = run_row(i, 0, &probed, &rd);\n\t\t\tif (st != ST_OK) {", "\t\t\tst = run_row(i, 0, &probed, &rd);\n\t\t\twhile (st == ST_TIMEOUT)\n\t\t\t\tst = run_row(i, 0, &probed, &rd);\n\t\t\tif (st != ST_OK) {", 1)),
        ("unbounded poll", good.replace("i < r->b && i < POLL_CAP", "i < r->b", 1)),
        ("W-C1c: EOT ignored (never recorded)", good.replace("\t\t\t\t*probed |= EOT_SEEN;", "", 1)),
        ("W-C1c: reads continue past the EOT", good.replace("\t\t\tif ((probed & EOT_SEEN) && r.kind == WLK_READ_ONLY && (r.a & 4u))\n\t\t\t\tcontinue;", "", 1)),
        ("W-C1c: wrong EOT value (0xE)", good.replace("if (v == 0xFu)\n\t\t\t\t*probed", "if (v == 0xEu)\n\t\t\t\t*probed", 1)),
        ("W-C1c: EOT is any non-zero dword", good.replace("if (v == 0xFu)\n\t\t\t\t*probed", "if (v != 0u)\n\t\t\t\t*probed", 1)),
        ("W-C1c: no refusal when the dump ends without an EOT", good.replace("else if (r.a & 8u)\n\t\t\t\treturn ST_REFUSED;", "", 1)),
        ("W-C1c: restore skipped on the EOT path", good.replace("for (i = 0; started && i < WLAN_SEQ_NTEAR; i++) {", "for (i = 0; started && !(probed & EOT_SEEN) && i < WLAN_SEQ_NTEAR; i++) {", 1)),
        ("W-C1c: the EOT skip also drops non-dump rows", good.replace("r.kind == WLK_READ_ONLY && (r.a & 4u))\n\t\t\t\tcontinue;", "r.kind == WLK_READ_ONLY)\n\t\t\t\tcontinue;", 1)),
        ("longer delay", good.replace("#define DELAY_MAX_US	250000u", "#define DELAY_MAX_US	2500000u", 1)),
        ("another default N", good.replace("#define WLAN_LINK_N	WLAN_SEQ_DEFAULT_N", "#define WLAN_LINK_N	WLAN_SEQ_LAST", 1)),
        ("teardown never marked started", good.replace("\t\tstarted = 1;\n", "", 1)),
        ("teardown hi not tracked", good.replace("\t\thi = s;\n", "", 1)),
        ("no probe needed", good.replace("return (blk == WLB_PMA || blk == WLB_PCS", "return 0u && (blk == WLB_PMA || blk == WLB_PCS", 1)),
        ("pa_of without the window bound", good.replace(" || off >= size_of(blk))", ")", 1)),
        ("swapped block base", good.replace("return WLAN_PCS;", "return WLAN_PMA;", 1)),
        ("ATU needs no probe", good.replace(" || blk == WLB_DBI || blk == WLB_ATU || blk == WLB_CFG || blk == WLB_MEM) ? 1u << blk : 0u;", " || blk == WLB_DBI || blk == WLB_CFG || blk == WLB_MEM) ? 1u << blk : 0u;", 1)),
        ("ATU base swapped", good.replace("return WLAN_ATU;", "return WLAN_DBI;", 1)),
        ("allowlist: ATU window writable", good.replace("\tif (pa == WLAN_DBI + 0x890u)", "\tif (pa >= WLAN_ATU && pa < WLAN_ATU + 0x1000u)\n\t\treturn !(pa & 3u);\n\tif (pa == WLAN_DBI + 0x890u)", 1)),
        ("allowlist: ATU another offset", good.replace("(off == 0x018u && v == 0u)", "(off == 0x01cu && v == 0u)", 1)),
        ("allowlist: ATU another value", good.replace("(v == 0x4u || v == 0u)", "(v == 0x4u || v == 0x5u)", 1)),
        ("allowlist: ATU limit another value", good.replace("(uint32_t)WLAN_CFG + 0x1fffu", "(uint32_t)WLAN_CFG + 0x2fffu", 1)),
        ("allowlist: ATU partial mask", good.replace("return mask == 0xFFFFFFFFu && atu_ok(", "return atu_ok(", 1)),
        ("allowlist: CFG window writable", good.replace("\tif (pa == WLAN_DBI + 0x890u)", "\tif (pa >= WLAN_CFG && pa < WLAN_CFG + 0x2000u)\n\t\treturn !(pa & 3u);\n\tif (pa == WLAN_DBI + 0x890u)", 1)),
        ("CFG needs no probe", good.replace(" || blk == WLB_ATU || blk == WLB_CFG || blk == WLB_MEM) ? 1u << blk : 0u;", " || blk == WLB_ATU || blk == WLB_MEM) ? 1u << blk : 0u;", 1)),
        ("CFG base swapped", good.replace("return WLAN_CFG;", "return WLAN_ATU;", 1)),
        ("CFG window size dropped", good.replace("blk == WLB_ELBI || blk == WLB_CFG)", "blk == WLB_ELBI)", 1)),
        ("ATU base assert removed", good.replace('_Static_assert(WLAN_ATU == WLAN_DBI + 0x300000u, "WLAN_ATU must be WLAN_DBI + 0x300000");', "", 1)),
        ("CFG base assert changed", good.replace("WLAN_CFG == 0x60FFE000UL", "WLAN_CFG == 0x40FFE000UL", 1)),
        ("value tweaked after the allowlist", good.replace("\treturn try_write32(pa, v) ? 0 : -1;", "\tv |= 0x80u;\n\treturn try_write32(pa, v) ? 0 : -1;", 1)),
        ("partial-mask full write allowed", good.replace("r.kind == WLK_MMIO_WRITE && r.mask != 0xFFFFFFFFu", "0", 1)),
        ("GPIO read-back removed", good.replace("\t\tif ((v & r.mask) != (r.val & r.mask))\n\t\t\treturn ST_FAULT;", "", 1)),
        ("poll timeout reported as ok", good.replace("\treturn ST_TIMEOUT;\n}", "\treturn ST_OK;\n}", 1)),
        ("teardown stops at the first failure", good.replace("\t\t\ttear_bad++;", "\t\t\ttear_bad++;\n\t\t\tbreak;", 1)),
        ("allowlist: CFG window wider than 4 KiB", good.replace("pa < WLAN_CFG + 0x1000u", "pa < WLAN_CFG + 0x2000u", 1)),
        ("allowlist: CFG partial mask", good.replace("return mask == 0xFFFFFFFFu && cfg_ok(", "return cfg_ok(", 1)),
        ("allowlist: CFG Command offset 0x04", good.replace("int lo = (off == 0x10u || off == 0x18u), hi = (off == 0x14u || off == 0x1cu);", "int lo = (off == 0x10u || off == 0x18u), hi = (off == 0x14u || off == 0x1cu || off == 0x04u);", 1)),
        ("allowlist: CFG another BAR offset (0x20)", good.replace("(off == 0x10u || off == 0x18u), hi", "(off == 0x10u || off == 0x18u || off == 0x20u), hi", 1)),
        ("allowlist: CFG restore lets every low value through", good.replace("if (v == (lo ? 0x4u : 0u))\n\t\treturn 1;", "if (v == (lo ? 0x4u : 0u) || lo)\n\t\treturn 1;", 1)),
        ("allowlist: CFG Command decode not checked", good.replace("if ((c->cmd & 0xFFFFu) != 0u)\n\t\treturn 0;", "", 1)),
        ("allowlist: CFG Command check weakened to MEM only", good.replace("(c->cmd & 0xFFFFu) != 0u", "(c->cmd & 0x2u) != 0u", 1)),
        ("allowlist: CFG all ones from any state", good.replace("return old == (lo ? 0x4u : 0u);", "return 1;", 1)),
        ("allowlist: CFG assignment from any state", good.replace("return lo && old == 0x4u && v ==", "return lo && v ==", 1)),
        ("allowlist: CFG assignment of another address", good.replace("0x60400004u", "0x60800004u", 1)),
        ("allowlist: CFG assignment of the high half", good.replace("return lo && old == 0x4u && v ==", "return old == 0x4u && v ==", 1)),
        ("w32 CFG writes check the table value, not the register", good.replace("if (r.blk == WLB_MEM || (r.kind == WLK_MMIO_WRITE && r.blk != WLB_CFG)) {", "if (r.blk == WLB_MEM || r.kind == WLK_MMIO_WRITE) {", 1)),
        ("w32 does not read the live state", good.replace("\tctx_read(r.blk, &c);", "\tc.cmd = 0u; c.bar0 = 0x60000004u; c.r1ok = 1;", 1)),
        ("ctx_read assumes Command 0 when unreadable", good.replace("if (!try_read32(WLAN_CFG + 4u, &c->cmd))\n\t\t\tc->cmd = 0xFFFFu;", "if (!try_read32(WLAN_CFG + 4u, &c->cmd))\n\t\t\tc->cmd = 0u;", 1)),
        ("ctx_read assumes BAR0 assigned when unreadable", good.replace("if (!try_read32(WLAN_CFG + 0x10u, &c->bar0))\n\t\t\tc->bar0 = 0xFFFFFFFFu;", "if (!try_read32(WLAN_CFG + 0x10u, &c->bar0))\n\t\t\tc->bar0 = 0x60000004u;", 1)),
        ("ctx_read: region 1 always ready", good.replace("c->r1ok = try_read32(", "c->r1ok = 1 || try_read32(", 1)),
        ("ctx_read: region 1 ready without the limit", good.replace(" && lim == 0x6000FFFFu", "", 1)),
        ("ctx_read: region 1 ready without the target", good.replace(" && lt == 0x60000000u", "", 1)),
        ("ctx_read: a second call site", good.replace(ins, "static void evil(void) { struct w32_ctx c; ctx_read(WLB_CFG, &c); }\n" + ins, 1)),
        ("allowlist: CFG Command takes MASTER", good.replace("(v == 0x2u && (old", "((v == 0x2u || v == 0x6u) && (old", 1)),
        ("allowlist: CFG Command takes any value", good.replace("return v == 0u || (v == 0x2u", "return 1 || (v == 0x2u", 1)),
        ("allowlist: CFG Command arm from any Command", good.replace("(old & 0xFFFFu) == 0u && c->bar0", "c->bar0", 1)),
        ("allowlist: CFG Command arm without the BAR0 state", good.replace(" && c->bar0 == 0x60000004u", "", 1)),
        ("allowlist: CFG window select any value", good.replace("return (v == 0x18001000u && (c->tear", "return 1 || (v == 0x18001000u && (c->tear", 1)),
        ("allowlist: CFG window select from any state", good.replace("(v == 0x18000000u && (old == 0x18001000u || (c->tear && (old == 0x18105000u || old == 0x18000000u || old == 0x18012000u || old == 0x18120000u || old == 0x18126000u || old == 0x18026000u))))", "(v == 0x18000000u)", 1)),
        ("allowlist: CFG window select another value", good.replace("v == 0x18000000u && (old", "v == 0x18000004u && (old", 1)),
        ("allowlist: CFG SPROM control 0x88", good.replace("if (off == 0x80u)", "if (off == 0x80u || off == 0x88u)", 1)),
        ("allowlist: ATU region 1 enable without the state", good.replace("(v == 0x80000000u && r1ok)", "(v == 0x80000000u)", 1)),
        ("allowlist: ATU region 1 CR1", good.replace("(off == 0x208u &&", "(off == 0x200u && v == 0u) || (off == 0x208u &&", 1)),
        ("allowlist: ATU region 1 UPPER_TARGET", good.replace("(off == 0x208u &&", "(off == 0x218u && v == 0u) || (off == 0x208u &&", 1)),
        ("allowlist: ATU region 1 limit of 2 MiB", good.replace("v == 0x6000FFFFu", "v == 0x601FFFFFu", 1)),
        ("allowlist: ATU region 1 another target", good.replace("(off == 0x214u && (v == 0x60000000u", "(off == 0x214u && (v == 0x60400000u", 1)),
        ("allowlist: ATU region 1 reset value of LIMIT dropped", good.replace("(off == 0x210u && (v == 0x6000FFFFu || v == 0xfffu))", "(off == 0x210u && v == 0x6000FFFFu)", 1)),
        ("allowlist: MEM window writable", good.replace("\tif (pa == WLAN_DBI + 0x890u)", "\tif (pa >= WLAN_MEM && pa < WLAN_MEM + 0x1000u)\n\t\treturn !(pa & 3u);\n\tif (pa == WLAN_DBI + 0x890u)", 1)),
        ("MEM window size dropped", good.replace("blk == WLB_ATU || blk == WLB_MEM)\n\t\treturn 0x1000u;", "blk == WLB_ATU)\n\t\treturn 0x1000u;", 1)),
        ("MEM base swapped", good.replace("return WLAN_MEM;", "return WLAN_CFG;", 1)),
        ("MEM needs no probe", good.replace(" || blk == WLB_CFG || blk == WLB_MEM) ? 1u << blk : 0u;", " || blk == WLB_CFG) ? 1u << blk : 0u;", 1)),
        ("MEM base assert changed (BAR2)", good.replace("WLAN_MEM == 0x60000000UL", "WLAN_MEM == 0x60400000UL", 1)),
        ("MEM base assert removed", good.replace('_Static_assert(WLAN_MEM == 0x60000000UL, "WLAN_MEM must be the pcie_1 MEM range start 0x60000000 (BAR0, read-only; the exact offsets per BAR0 window are in mem_ok)");', "", 1)),
        ("MEM read without the region 1 guard", good.replace("!try_read32(WLAN_ATU + 0x204u, &cr2) || cr2 != 0x80000000u || ", "", 1)),
        ("MEM read with the region 1 guard weakened", good.replace("cr2 != 0x80000000u", "(cr2 & 0x80000000u) == 0u", 1)),
        ("w32 without the MEM/config-space refusal", good.replace("\tif (!w32_blk_ok(r.blk, r.off))\n\t\treturn -2;\n", "", 1)),
        ("w32_blk_ok refuses BAR0 again (pinned text drift)", good.replace("return !(blk == WLB_CFG && off >= 0x1000u);", "return !(blk == WLB_MEM || (blk == WLB_CFG && off >= 0x1000u));", 1)),
        ("w32_blk_ok lets config space above 4 KiB through", good.replace("return !(blk == WLB_CFG && off >= 0x1000u);", "return !(blk == WLB_CFG && off >= 0x2000u);", 1)),
        ("MEM read without the window re-check (limit)", good.replace(" || r1[2] != 0x6000FFFFu", "", 1)),
        ("MEM read without the window re-check (all)", good.replace("\t\tif (!try_read32(WLAN_ATU + 0x200u, &r1[0])", "\t\tif (0 && !try_read32(WLAN_ATU + 0x200u, &r1[0])", 1)),
        ("ctx_read: region 1 ready without CR1", good.replace(" && try_read32(WLAN_ATU + 0x200u, &cr1)", "", 1).replace(" && cr1 == 0u", "", 1)),
        ("ctx_read: region 1 ready with CR1 not checked", good.replace(" && cr1 == 0u", "", 1)),
        ("ctx_read: region 1 ready with UPPER_BASE not checked", good.replace(" && ub == 0u", "", 1)),
        ("ctx_read: region 1 ready with UPPER_TARGET not checked", good.replace(" && ut == 0u", "", 1)),
        ("r32 is not noipa (inlined, unaudited)", good.replace("__attribute__((noipa)) static int r32", "static int r32", 1)),
        ("ctx_read touches the ATU for every block", good.replace("if (blk == WLB_ATU)\n\t\tc->r1ok", "if (1)\n\t\tc->r1ok", 1)),
        ("MEM read without the Command guard", good.replace(" || !try_read32(WLAN_CFG + 4u, &cmd) || (cmd & 0xFFFFu) != 0x0002u", "", 1)),
        ("MEM read with the Command guard weakened (MEM bit only)", good.replace("(cmd & 0xFFFFu) != 0x0002u", "(cmd & 0x2u) != 0x2u", 1)),
        # S32 (research 37 s.9.1 run B): the window move, its restore, the window-dependent BAR0 reads, the teardown filter
        ("allowlist: window move from any state", good.replace("(v == 0x18105000u && !c->tear && old == 0x18000000u && ", "(v == 0x18105000u && !c->tear && ", 1)),
        ("allowlist: window move allowed in the teardown", good.replace("(v == 0x18105000u && !c->tear && ", "(v == 0x18105000u && ", 1)),
        ("allowlist: window move without the Command check", good.replace("old == 0x18000000u && (c->cmd & 0xFFFFu) == 0x0002u && c->bar0 == 0x60000004u) ||", "old == 0x18000000u && c->bar0 == 0x60000004u) ||", 1)),
        ("allowlist: window move without the BAR0 state", good.replace("old == 0x18000000u && (c->cmd & 0xFFFFu) == 0x0002u && c->bar0 == 0x60000004u) ||", "old == 0x18000000u && (c->cmd & 0xFFFFu) == 0x0002u) ||", 1)),
        ("allowlist: window move to another page", good.replace("(v == 0x18105000u && !c->tear", "(v == 0x18106000u && !c->tear", 1)),
        ("allowlist: window move accepts any page", good.replace("(v == 0x18105000u && !c->tear && old == 0x18000000u", "((v & 0xFFFFF000u) == 0x18100000u && !c->tear && old == 0x18000000u", 1)),
        ("w32 never marks a teardown row", good.replace("\tc.tear = tear;", "\tc.tear = 0;", 1)),
        ("w32 marks every row a teardown row", good.replace("\tc.tear = tear;", "\tc.tear = 1;", 1)),
        # S33 (research 38, W-C2): the window chain, the restore from every window, the per-window read list (mem_ok), r32's use of it. AP-16: each defect class (wrong window value, extra write, extra read offset, restore skipped, gate ignored) as a source mutant
        ("allowlist: window restore to ChipCommon from any state in a forward row", good.replace("(c->tear && (old == 0x18105000u || old == 0x18000000u || old == 0x18012000u || old == 0x18120000u || old == 0x18126000u || old == 0x18026000u))", "(1 && (old == 0x18105000u || old == 0x18000000u || old == 0x18012000u || old == 0x18120000u || old == 0x18126000u || old == 0x18026000u))", 1)),
        ("allowlist: window restore to ChipCommon from any old value", good.replace("(v == 0x18000000u && (old == 0x18001000u || (c->tear && (old == 0x18105000u || old == 0x18000000u || old == 0x18012000u || old == 0x18120000u || old == 0x18126000u || old == 0x18026000u))))", "(v == 0x18000000u && (old == 0x18001000u || c->tear))", 1)),
        ("allowlist: window restore lost (teardown cannot restore the EROM page)", good.replace("(c->tear && (old == 0x18105000u || old == 0x18000000u || old == 0x18012000u || old == 0x18120000u || old == 0x18126000u || old == 0x18026000u))", "(c->tear && old == 0x18000000u)", 1)),
        ("allowlist: window restore lost from the SYS_MEM core page (S33)", good.replace(" || old == 0x18026000u))))", "))))", 1)),
        ("allowlist: window restore lost from the PMU page (S33)", good.replace(" || old == 0x18012000u", "", 1)),
        ("allowlist: S33 move from any state", good.replace("((v == 0x18012000u && old == 0x18105000u) ||", "((v == 0x18012000u) ||", 1)),
        ("allowlist: S33 move skips a link of the chain (ARM wrapper from the EROM page)", good.replace("(v == 0x18120000u && old == 0x18012000u)", "(v == 0x18120000u && old == 0x18105000u)", 1)),
        ("allowlist: S33 SYS_MEM core page from the ARM wrapper (gate bypass at window level)", good.replace("(v == 0x18026000u && old == 0x18126000u)", "(v == 0x18026000u && old == 0x18120000u)", 1)),
        ("allowlist: S33 move to another page (PMU 0x18013000)", good.replace("(v == 0x18012000u && old == 0x18105000u)", "(v == 0x18013000u && old == 0x18105000u)", 1)),
        ("allowlist: S33 move to a wrong ARM wrapper page", good.replace("(v == 0x18120000u && old == 0x18012000u)", "(v == 0x18121000u && old == 0x18012000u)", 1)),
        ("allowlist: S33 move to a wrong SYS_MEM wrapper page", good.replace("(v == 0x18126000u && old == 0x18120000u)", "(v == 0x18127000u && old == 0x18120000u)", 1)),
        ("allowlist: S33 move to a wrong SYS_MEM core page", good.replace("(v == 0x18026000u && old == 0x18126000u)", "(v == 0x18027000u && old == 0x18126000u)", 1)),
        ("allowlist: S33 move accepts any page of the chain region", good.replace("(v == 0x18012000u && old == 0x18105000u)", "((v & 0xFFFF0000u) == 0x18010000u && old == 0x18105000u)", 1)),
        ("allowlist: S33 move allowed in the teardown", good.replace("(!c->tear && (c->cmd & 0xFFFFu) == 0x0002u && c->bar0 == 0x60000004u &&", "((c->cmd & 0xFFFFu) == 0x0002u && c->bar0 == 0x60000004u &&", 1)),
        ("allowlist: S33 move without the Command check", good.replace("(!c->tear && (c->cmd & 0xFFFFu) == 0x0002u && c->bar0", "(!c->tear && c->bar0", 1)),
        ("allowlist: S33 move without the BAR0 state", good.replace("(!c->tear && (c->cmd & 0xFFFFu) == 0x0002u && c->bar0 == 0x60000004u &&", "(!c->tear && (c->cmd & 0xFFFFu) == 0x0002u &&", 1)),
        ("allowlist: S33 one more window (page 0x18027000 after the core page)", good.replace("(v == 0x18120000u && old == 0x18026000u)));", "(v == 0x18120000u && old == 0x18026000u) || (v == 0x18027000u && old == 0x18026000u)));", 1)),
        ("mem_ok: no check at all", good.replace("\tif (win == 0x18105000u)\n\t\treturn off < 0xE00u;", "\treturn 1;\n\tif (win == 0x18105000u)\n\t\treturn off < 0xE00u;", 1)),
        ("mem_ok: the EROM bound dropped (review 13 M1)", good.replace("return off < 0xE00u;", "return 1;", 1)),
        ("mem_ok: the EROM bound moved to 0x1000", good.replace("return off < 0xE00u;", "return off < 0x1000u;", 1)),
        ("mem_ok: the EROM bound moved to 0xE00 inclusive", good.replace("return off < 0xE00u;", "return off <= 0xE00u;", 1)),
        ("mem_ok: the EROM page window changed", good.replace("if (win == 0x18105000u)", "if (win == 0x18106000u)", 1)),
        ("mem_ok: the ChipCommon window changed", good.replace("if (win == 0x18000000u)", "if (win == 0x18001000u)", 1)),
        ("mem_ok: the PMU window changed", good.replace("if (win == 0x18012000u)", "if (win == 0x18013000u)", 1)),
        ("mem_ok: the ARM CA7 wrapper window changed", good.replace("if (win == 0x18120000u || win == 0x18126000u)", "if (win == 0x18121000u || win == 0x18126000u)", 1)),
        ("mem_ok: the SYS_MEM wrapper window changed", good.replace("win == 0x18126000u)\n\t\treturn off == 0x408u", "win == 0x18127000u)\n\t\treturn off == 0x408u", 1)),
        ("mem_ok: the SYS_MEM core window changed", good.replace("if (win == 0x18026000u)", "if (win == 0x18027000u)", 1)),
        ("mem_ok: the SYS_MEM core page read widened (offset 0 or 4)", good.replace("return off == 0u || off == 0x10u || off == 0x40u;", "return off == 0u || off == 0x10u || off == 0x40u || off == 4u;", 1)),
        ("mem_ok: the SYS_MEM core page takes any offset", good.replace("return off == 0u || off == 0x10u || off == 0x40u;", "return 1;", 1)),
        ("mem_ok: another window falls through to allowed", good.replace("\treturn 0;\n}\n\n/* The BAR0 WRITE allowlist", "\treturn 1;\n}\n\n/* The BAR0 WRITE allowlist", 1)),
        ("mem_ok: PMU offset 0x60C (res_state) added", good.replace("off == 0x608u ||", "off == 0x608u || off == 0x60Cu ||", 1)),
        ("mem_ok: PMU offset 0x614 (pmutimer, ILP sensitive) added", good.replace("off == 0x618u ||", "off == 0x618u || off == 0x614u ||", 1)),
        ("mem_ok: PMU pmucapabilities 0x604 dropped", good.replace("off == 0x604u ||", "", 1)),
        ("mem_ok: PMU max_res_mask widened to 0x620", good.replace("off == 0x61Cu;", "off == 0x620u;", 1)),
        ("mem_ok: wrapper ioctrlset 0x400 added", good.replace("return off == 0x408u ||", "return off == 0x400u || off == 0x408u ||", 1)),
        ("mem_ok: wrapper ioctrlclear 0x404 added", good.replace("off == 0x408u || off == 0x800u", "off == 0x408u || off == 0x404u || off == 0x800u", 1)),
        ("mem_ok: wrapper resetreadid 0x808 added", good.replace("off == 0x804u;", "off == 0x804u || off == 0x808u;", 1)),
        ("mem_ok: wrapper RESET_ST moved to 0x808", good.replace("off == 0x804u;", "off == 0x808u;", 1)),
        ("mem_ok: wrapper iostatus 0x500 added", good.replace("off == 0x804u;", "off == 0x804u || off == 0x500u;", 1)),
        ("mem_ok: ChipCommon offset 0x100 added", good.replace("off == 0xFCu ||", "off == 0xFCu || off == 0x100u ||", 1)),
        ("mem_ok: ChipCommon offset 0xFC dropped", good.replace(" || off == 0xFCu", "", 1)),
        ("mem_ok: ChipCommon offset 0x1E0 widened to 0x1E4", good.replace("off == 0x1E0u;", "off == 0x1E4u;", 1)),
        ("mem_ok: ChipCommon offset 0xAC widened to 0xB0", good.replace("off == 0xACu", "off == 0xB0u", 1)),
        ("mem_ok: not noipa (inlined, unaudited)", good.replace("__attribute__((noipa)) static int mem_ok", "static int mem_ok", 1)),
        ("mem_ok: a second caller", good.replace(ins, "static int evil2(uint32_t w) { return mem_ok(w, 0x100u); }\n" + ins, 1)),
        ("r32: the window select is not read", good.replace("!try_read32(WLAN_CFG + 0x80u, &win) || !mem_ok(win, off) || ", "", 1)),
        ("r32: mem_ok not consulted", good.replace("!mem_ok(win, off)", "0", 1)),
        ("r32: mem_ok result inverted", good.replace("!mem_ok(win, off)", "mem_ok(win, off)", 1)),
        ("r32: mem_ok called with a fixed window", good.replace("!mem_ok(win, off)", "!mem_ok(0x18105000u, off)", 1)),
        ("r32: mem_ok called with a fixed offset", good.replace("!mem_ok(win, off)", "!mem_ok(win, 0u)", 1)),
        ("teardown skipped after a refusal (S33 gate stops)", good.replace("for (i = 0; started && i < WLAN_SEQ_NTEAR; i++) {", "for (i = 0; started && stopped != ST_REFUSED && i < WLAN_SEQ_NTEAR; i++) {", 1)),
        ("teardown skipped after a fault (S33 window write fault)", good.replace("for (i = 0; started && i < WLAN_SEQ_NTEAR; i++) {", "for (i = 0; started && stopped != ST_FAULT && i < WLAN_SEQ_NTEAR; i++) {", 1)),
        ("gate ignored: a one-shot wait that does not hold is not a stop", good.replace("return (st != ST_OK && r.b == 1u) ? (st == ST_TIMEOUT ? ST_REFUSED : st) : st;", "return st == ST_TIMEOUT ? ST_OK : st;", 1)),
        ("gate ignored: the run continues after a refusal", good.replace("for (s = 0; s <= WLAN_LINK_N && stopped == ST_OK; s++) {", "for (s = 0; s <= WLAN_LINK_N && stopped != ST_FAULT; s++) {", 1)),
        ("teardown filter skips the last started step", good.replace("if (!get_row(i, 1, &r) || r.step > hi)", "if (!get_row(i, 1, &r) || r.step >= hi)", 1)),
        ("teardown only after a clean run", good.replace("for (i = 0; started && i < WLAN_SEQ_NTEAR; i++) {", "for (i = 0; started && !stopped && i < WLAN_SEQ_NTEAR; i++) {", 1)),
        ("teardown row order reversed", good.replace("for (i = 0; started && i < WLAN_SEQ_NTEAR; i++) {", "for (i = WLAN_SEQ_NTEAR; started && i-- > 0;) {", 1)),
        # S34-S36 (research 40, W-C3): the first BAR0 writes. AP-16: every defect class as a source mutant (wrong value, extra write, wrong order / missing predecessor, restore skipped, window chain broken, bankidx other than 0, missing guards)
        ("S34: powerctl request writes another bit (0x400)", good.replace("v == (old | 0x200u);", "v == (old | 0x400u);", 1)),
        ("S34: powerctl request may also clear bits", good.replace("(old & 0xF0000000u) != 0u && v == (old | 0x200u);", "(old & 0xF0000000u) != 0u && (v & 0x200u) != 0u;", 1)),
        ("S34: powerctl request allowed on a dead read (all ones)", good.replace("off == 0x1E8u && old != 0xFFFFFFFFu && (old & 0xF0000000u) != 0u", "off == 0x1E8u && (old & 0xF0000000u) != 0u", 1)),
        ("S34: powerctl request without the PowerDomain id check (review 16 finding 2)", good.replace(" && (old & 0xF0000000u) != 0u", "", 1)),
        ("S34: powerctl PowerDomain id check on the wrong nibble (0x0F000000)", good.replace("(old & 0xF0000000u) != 0u", "(old & 0x0F000000u) != 0u", 1)),
        ("S34: powerctl PowerDomain id check inverted (id must be 0)", good.replace("(old & 0xF0000000u) != 0u", "(old & 0xF0000000u) == 0u", 1)),
        ("S34: powerctl neighbour offset 0x1EC writable", good.replace("return off == 0x1E8u && old", "return (off == 0x1E8u || off == 0x1ECu) && old", 1)),
        ("S34: powerctl written in the ChipCommon window", good.replace("if (win == 0x18001000u)\t\t/* SRPWR", "if (win == 0x18001000u || win == 0x18000000u)\t\t/* SRPWR", 1)),
        ("S34: powerctl window changed (0x18002000)", good.replace("if (win == 0x18001000u)\t\t/* SRPWR", "if (win == 0x18002000u)\t\t/* SRPWR", 1)),
        ("S35: SYS_MEM IOCTL wrong value (0x7)", good.replace("want = win == 0x18126000u ? 0x3u : 0x23u;", "want = win == 0x18126000u ? 0x7u : 0x23u;", 1)),
        ("S36: ARM IOCTL wrong value (0x63, an extra bit)", good.replace("want = win == 0x18126000u ? 0x3u : 0x23u;", "want = win == 0x18126000u ? 0x3u : 0x63u;", 1)),
        ("S36: ARM IOCTL without CPUHALT (0x3)", good.replace("want = win == 0x18126000u ? 0x3u : 0x23u;", "want = win == 0x18126000u ? 0x3u : 0x3u;", 1)),
        ("S35: final IOCTL keeps FGC (no drop of 0x2)", good.replace("(v == (want & ~0x2u) && (pred", "(v == want && (pred", 1)),
        ("S35: IOCTL write without reset asserted (predecessor RESET_CTL dropped)", good.replace("(v == want && (pred & 1u) == 1u) ||", "(v == want) ||", 1)),
        ("S35: final IOCTL before the release (RESET_CTL predecessor dropped)", good.replace("(v == (want & ~0x2u) && (pred & 1u) == 0u &&", "(v == (want & ~0x2u) &&", 1)),
        ("S35: final IOCTL without the reset IOCTL as old value", good.replace(" && (old & 0x3FFFu) == want);", ");", 1)),
        ("S35: release without the IOCTL predecessor", good.replace("v == 1u || (v == 0u && (pred & 0x3FFFu) == want);", "v == 1u || v == 0u;", 1)),
        ("S35: release with the wrong predecessor mask", good.replace("(pred & 0x3FFFu) == want);", "(pred & 0x1u) == (want & 0x1u));", 1)),
        ("S35: RESET_CTL takes another value (0x3)", good.replace("return v == 1u || (v == 0u", "return v == 1u || v == 3u || (v == 0u", 1)),
        ("S35: wrapper ioctrlset 0x400 writable", good.replace("if (off == 0x408u)\t/* pred = RESET_CTL", "if (off == 0x408u || off == 0x400u)\t/* pred = RESET_CTL", 1)),
        ("S35: wrapper RESET_ST 0x804 writable", good.replace("if (off == 0x800u)\t/* pred = IOCTL", "if (off == 0x800u || off == 0x804u)\t/* pred = IOCTL", 1)),
        ("S36: another wrapper window writable (0x18121000)", good.replace("if (win == 0x18126000u || win == 0x18120000u) {", "if (win == 0x18126000u || win == 0x18120000u || win == 0x18121000u) {", 1)),
        ("S36: the PMU window writable like a wrapper", good.replace("if (win == 0x18126000u || win == 0x18120000u) {", "if (win == 0x18126000u || win == 0x18120000u || win == 0x18012000u) {", 1)),
        ("S35: bankidx takes another value (v != 0)", good.replace("return off == 0x10u && v == 0u && pred", "return off == 0x10u && v <= 1u && pred", 1)),
        ("S35: bankidx takes any value", good.replace("return off == 0x10u && v == 0u && pred", "return off == 0x10u && pred", 1)),
        ("S35: bankidx without the coreinfo dead-read check", good.replace(" && pred != 0xFFFFFFFFu && (pred & 0x3E0u) == 0u", " && (pred & 0x3E0u) == 0u", 1)),
        ("S35: bankidx without the no-ROM-bank (nrb == 0) check", good.replace(" && (pred & 0x3E0u) == 0u && (pred & 0x1Fu) != 0u;", " && (pred & 0x1Fu) != 0u;", 1)),
        ("S35: bankidx without the RAM-bank (nb >= 1) check", good.replace(" && (pred & 0x3E0u) == 0u && (pred & 0x1Fu) != 0u;", " && (pred & 0x3E0u) == 0u;", 1)),
        ("S35: bankidx with the brcmfmac nrb mask", good.replace("(pred & 0x3E0u) == 0u && (pred & 0x1Fu)", "(pred & 0xF0u) == 0u && (pred & 0x1Fu)", 1)),
        ("S35: bankidx register moved (0x14)", good.replace("return off == 0x10u && v == 0u && pred", "return off == 0x14u && v == 0u && pred", 1)),
        ("S35: coreinfo writable like bankidx", good.replace("return off == 0x10u && v == 0u && pred", "return (off == 0x10u || off == 0u) && v == 0u && pred", 1)),
        ("S35: bankinfo predecessor offset changed in memw_pred (bankidx pred = bankinfo)", good.replace("\tif (off == 0x10u)\n\t\treturn 0u;", "\tif (off == 0x10u)\n\t\treturn 0x40u;", 1)),
        ("S35: memw_pred has no predecessor for bankidx", good.replace("\tif (off == 0x10u)\n\t\treturn 0u;", "", 1)),
        ("S35: memw_pred RESET_CTL predecessor is the wrong register (0x404)", good.replace("if (off == 0x800u)\n\t\treturn 0x408u;", "if (off == 0x800u)\n\t\treturn 0x404u;", 1)),
        ("S35: memw_pred IOCTL predecessor is RESET_ST", good.replace("if (off == 0x408u)\n\t\treturn 0x800u;", "if (off == 0x408u)\n\t\treturn 0x804u;", 1)),
        ("mem_ok: powerctl window read widened (0x1EC)", good.replace("if (win == 0x18001000u)\n\t\treturn off == 0x1E8u;", "if (win == 0x18001000u)\n\t\treturn off == 0x1E8u || off == 0x1ECu;", 1)),
        ("mem_ok: powerctl window takes any offset", good.replace("if (win == 0x18001000u)\n\t\treturn off == 0x1E8u;", "if (win == 0x18001000u)\n\t\treturn 1;", 1)),
        ("mem_ok: the SYS_MEM core bankinfo read dropped", good.replace("off == 0x10u || off == 0x40u;", "off == 0x10u;", 1)),
        ("mem_ok: the SYS_MEM core extracoreinfo 0x8 read added", good.replace("off == 0u || off == 0x10u || off == 0x40u;", "off == 0u || off == 0x8u || off == 0x10u || off == 0x40u;", 1)),
        ("allowlist: the BAR0 branch also writes in the teardown", good.replace("return !c->tear && memw_ok(", "return memw_ok(", 1)),
        ("allowlist: the BAR0 branch ignores the live window (fixed window)", good.replace("memw_ok(c->win, (uint32_t)(pa - WLAN_MEM)", "memw_ok(0x18126000u, (uint32_t)(pa - WLAN_MEM)", 1)),
        ("allowlist: the BAR0 branch ignores the live predecessor", good.replace("old, v, c->pred);", "old, v, 3u);", 1)),
        ("allowlist: the BAR0 branch passes the new value as the old one", good.replace("memw_ok(c->win, (uint32_t)(pa - WLAN_MEM), old, v, c->pred)", "memw_ok(c->win, (uint32_t)(pa - WLAN_MEM), v, v, c->pred)", 1)),
        ("allowlist: the BAR0 branch window is 8 KiB", good.replace("pa < WLAN_MEM + 0x1000u)\n\t\treturn !c->tear", "pa < WLAN_MEM + 0x2000u)\n\t\treturn !c->tear", 1)),
        ("w32: the BAR0 old value is the table value (no live read)", good.replace(" || !r32(WLB_MEM, r.off, &old)", "", 1)),
        ("w32: the BAR0 predecessor is never read", good.replace(" || (pr != 0xFFFFFFFFu && !r32(WLB_MEM, pr, &c.pred))", "", 1)),
        ("w32: the BAR0 window select is never read", good.replace("!try_read32(WLAN_CFG + 0x80u, &c.win) || ", "", 1)),
        ("w32: the BAR0 branch allowed in the teardown", good.replace("if (tear || !try_read32(WLAN_CFG + 0x80u, &c.win)", "if (!try_read32(WLAN_CFG + 0x80u, &c.win)", 1)),
        ("w32: the BAR0 old value is read raw (no r32 guards)", good.replace("!r32(WLB_MEM, r.off, &old)", "!try_read32(pa, &old)", 1)),
        ("w32: the BAR0 predecessor is read raw (no r32 guards)", good.replace("!r32(WLB_MEM, pr, &c.pred)", "!try_read32(WLAN_MEM + pr, &c.pred)", 1)),
        ("w32: the BAR0 context is not reset (stale win / pred)", good.replace("\tc.win = c.pred = 0u;\n", "", 1)),
        ("w32: the BAR0 refusal returns a fault code", good.replace("(pr != 0xFFFFFFFFu && !r32(WLB_MEM, pr, &c.pred)))\n\t\t\treturn -2;", "(pr != 0xFFFFFFFFu && !r32(WLB_MEM, pr, &c.pred)))\n\t\t\treturn 0;", 1)),
        ("memw_ok: not noipa (inlined, unaudited)", good.replace("__attribute__((noipa)) static int memw_ok", "static int memw_ok", 1)),
        ("memw_ok: a second caller", good.replace(ins, "static int evil3(uint32_t w) { return memw_ok(w, 0x800u, 1u, 0u, 3u); }\n" + ins, 1)),
        ("memw_pred: not noipa", good.replace("__attribute__((noipa)) static uint32_t memw_pred", "static uint32_t memw_pred", 1)),
        ("memw_pred: a second caller", good.replace(ins, "static uint32_t evil4(void) { return memw_pred(0x800u); }\n" + ins, 1)),
        ("window chain: S34 core page move from any state", good.replace("(v == 0x18001000u && (c->tear || (old == 0x18126000u && ", "(v == 0x18001000u && (c->tear || (1 && ", 1)),
        ("window chain: S34 core page move from the EROM page", good.replace("(old == 0x18126000u && (c->cmd & 0xFFFFu) == 0x0002u && c->bar0 == 0x60000004u)))", "(old == 0x18105000u && (c->cmd & 0xFFFFu) == 0x0002u && c->bar0 == 0x60000004u)))", 1)),
        ("window chain: S34 core page move without the Command check", good.replace("(old == 0x18126000u && (c->cmd & 0xFFFFu) == 0x0002u && c->bar0 == 0x60000004u)))", "(old == 0x18126000u && c->bar0 == 0x60000004u)))", 1)),
        ("window chain: S34 core page move without the BAR0 state", good.replace("(old == 0x18126000u && (c->cmd & 0xFFFFu) == 0x0002u && c->bar0 == 0x60000004u)))", "(old == 0x18126000u && (c->cmd & 0xFFFFu) == 0x0002u)))", 1)),
        ("window chain: S35 wrapper from the PMU page", good.replace("(v == 0x18126000u && old == 0x18001000u)", "(v == 0x18126000u && old == 0x18012000u)", 1)),
        ("window chain: S35 wrapper return from any state", good.replace("(v == 0x18126000u && old == 0x18001000u)", "(v == 0x18126000u)", 1)),
        ("window chain: S36 ARM wrapper from the SYS_MEM wrapper (core page skipped)", good.replace("(v == 0x18120000u && old == 0x18026000u)", "(v == 0x18120000u && old == 0x18126000u)", 1)),
        ("window chain: S36 ARM wrapper from any state", good.replace("(v == 0x18120000u && old == 0x18026000u)", "(v == 0x18120000u)", 1)),
        ("window chain: S36 ARM wrapper page changed", good.replace("(v == 0x18120000u && old == 0x18026000u)", "(v == 0x18121000u && old == 0x18026000u)", 1)),
        ("window chain: the core page 0x18001000 allowed in the teardown without being the original", good.replace("(v == 0x18001000u && (c->tear ||", "(v == 0x18002000u && (c->tear ||", 1)),
        ("window chain: restore to ChipCommon lost from the PCIe core page", good.replace("(v == 0x18000000u && (old == 0x18001000u ||", "(v == 0x18000000u && (old == 0x18002000u ||", 1)),
        ("restore skipped after a timeout", good.replace("for (i = 0; started && i < WLAN_SEQ_NTEAR; i++) {", "for (i = 0; started && stopped != ST_TIMEOUT && i < WLAN_SEQ_NTEAR; i++) {", 1)),
        ("a one-shot wait that does not hold continues (BAR0 read-back ignored)", good.replace("return (st != ST_OK && r.b == 1u) ? (st == ST_TIMEOUT ? ST_REFUSED : st) : st;", "return (st != ST_OK && r.b == 1u) ? ST_OK : st;", 1)),
        ("a wait row that times out continues (SRPWR wait ignored)", good.replace("\treturn ST_TIMEOUT;\n}", "\treturn ST_OK;\n}", 1)),
        ("a raw read of another register", good.replace(ins, "static void evil(uint32_t *v) { (void)try_read32(0x17463EC8UL, v); }\n" + ins, 1)),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != good, f"wlanlink source selftest case did not mutate: {label}"
        try:
            check_wlanlink_source_text(bad, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlanlink source {label} -> {exc}")
        else:
            raise AssertionError(f"wlanlink source self-test FAILED to catch: {label}")
    print(f"PASS wlanlink source self-test: {caught}/{len(cases)} bad sources rejected")
    return caught


def check_wlan_link_n(effective: int, explicit: str | None, default: int = 19, last: int = 36) -> None:
    """Build-time policy: an image whose effective step bound N is >= 20 (PERST release, LTSSM enable) is accepted ONLY when the builder typed `--wlan-link=N` with exactly that N; anything without an
    explicit N is the default run (N = default_last_step = 19: no PERST, no LTSSM). `effective` is read from the preprocessed wlanlink.c by shim/build.sh."""
    if not 0 <= effective <= last:
        raise AssertionError(f"--wlan-link: effective N={effective} is outside 0..{last}")
    if explicit in (None, ""):
        if effective != default:
            raise AssertionError(f"--wlan-link: an image without an explicit =N must be the default N={default}, but the effective N is {effective}")
    elif not explicit.isdigit() or int(explicit) != effective:
        raise AssertionError(f"--wlan-link: explicit N={explicit!r} differs from the effective N={effective}")


def check_wlan_link_n_sources(link_src: str, build_sh: str, name: str = "wlanlink.c") -> None:
    """The two places that decide N: build.sh sets WLAN_LINK_N only from an explicit `--wlan-link=N`; wlanlink.c defaults it to WLAN_SEQ_DEFAULT_N and the FIRST line it logs prints it."""
    code = re.sub(r"\s+", "", _strip_c_comments(link_src))
    if "#ifndefWLAN_LINK_N#defineWLAN_LINK_NWLAN_SEQ_DEFAULT_N#endif" not in code:
        raise AssertionError(f"{name}: WLAN_LINK_N must default to WLAN_SEQ_DEFAULT_N")
    ra, rb = _c_function_span(_strip_c_comments(link_src), "wlan_link_run")
    run = re.sub(r"\s+", "", _strip_c_comments(link_src)[ra:rb])
    first = run.find("emit(line,p);")
    want = 'p=put_txt(line,sizeof(line),0,"LINKtablesha=");'
    if first < 0 or want not in run[:first] or 'p=put_txt(line,sizeof(line),p,"N=");p=put_dec(line,sizeof(line),p,WLAN_LINK_N);emit(line,p);' not in run[:first + 13]:
        raise AssertionError(f"{name}: the first LINK line must print `N=` and WLAN_LINK_N")
    if any(tok in run[:first] for tok in ("step_line(", "con_str(", "run_row(", "w32(", "return")):
        raise AssertionError(f"{name}: nothing may be logged, run or returned before the first LINK line (table sha, rows, N)")
    sh = build_sh.splitlines()
    assigns = [l.strip() for l in sh if re.match(r"\s*wlan_link_n=|.*[;)]\s*wlan_link_n=", l) and "wlan_link_n=$" not in l and "wlan_link_n=)" not in l]
    flags = [l.strip() for l in sh if "-DWLAN_LINK_N=" in l]
    ok_assign = [l for l in assigns if l.startswith("--wlan-link=*)") and 'wlan_link_n="${1#--wlan-link=}"' in l]
    if len(assigns) != 2 or len(ok_assign) != 1 or sum(1 for l in assigns if l.startswith("wlan_link_n=")) != 1:
        raise AssertionError(f"build.sh: wlan_link_n may be set only by the declaration and `--wlan-link=*)`: {assigns}")
    if len(flags) != 2 or sum(1 for l in flags if l.startswith('[ -z "$wlan_link_n" ] || CFLAGS="$CFLAGS -DWLAN_LINK_N=$wlan_link_n"')) != 1 or sum(1 for l in flags if "wlanlink_host_test" in l) != 1:
        raise AssertionError(f"build.sh: -DWLAN_LINK_N= only from an explicit --wlan-link=N (and in the host-test loop): {flags}")


def wlan_link_n_selftest() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    src, sh = (root / "shim" / "wlanlink.c").read_text(), (root / "shim" / "build.sh").read_text()
    check_wlan_link_n_sources(src, sh)  # control must pass
    for eff, exp in ((19, None), (19, ""), (7, "7"), (20, "20"), (23, "23"), (24, "24"), (25, "25"), (26, "26"), (27, "27"), (28, "28"), (29, "29"), (30, "30"), (31, "31"), (32, "32"), (33, "33"), (34, "34"), (35, "35"), (36, "36")):
        check_wlan_link_n(eff, exp)
    caught = 0
    bad_n = [(19, "20"), (20, None), (21, ""), (23, "22"), (24, None), (25, None), (26, None), (27, None), (28, None), (29, None), (30, None), (31, None), (32, None), (33, None), (34, None), (35, None), (36, None), (37, "37"), (36, "35"), (35, "36"), (35, "34"), (34, "35"), (34, "33"), (33, "32"), (32, "33"), (32, "31"), (31, "32"), (31, "30"), (27, "26"), (30, "29"), (29, "28"), (26, "25"), (25, "24"), (24, "23"), (20, "x"), (22, "019")]
    for eff, exp in bad_n:
        try:
            check_wlan_link_n(eff, exp)
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlan-link N effective={eff} explicit={exp!r} -> {exc}")
        else:
            raise AssertionError(f"wlan-link N self-test FAILED to catch effective={eff} explicit={exp!r}")
    emit_n = 'p = put_txt(line, sizeof(line), p, " N=");\n\tp = put_dec(line, sizeof(line), p, WLAN_LINK_N);'
    cases = [
        ("first line prints another N", src.replace(emit_n, emit_n.replace("WLAN_LINK_N", "WLAN_SEQ_DEFAULT_N"), 1), sh),
        ("first line prints no N", src.replace(emit_n, "", 1), sh),
        ("a step line before the first LINK line", src.replace("\tp = put_txt(line, sizeof(line), 0, \"LINK table sha=\");", "\tstep_line('S', 0, \"x\", 0, 0, 0, 0, 0, 0);\n\tp = put_txt(line, sizeof(line), 0, \"LINK table sha=\");", 1), sh),
        ("another default N", src.replace("#define WLAN_LINK_N	WLAN_SEQ_DEFAULT_N", "#define WLAN_LINK_N	22", 1), sh),
        ("bare --wlan-link sets N", src, sh.replace("--wlan-link) wlan_link=1;", "--wlan-link) wlan_link_n=20; wlan_link=1;", 1)),
        ("N flag unconditional", src, sh.replace('[ -z "$wlan_link_n" ] || CFLAGS="$CFLAGS -DWLAN_LINK_N=$wlan_link_n"', 'CFLAGS="$CFLAGS -DWLAN_LINK_N=${wlan_link_n:-23}"', 1)),
    ]
    for label, bs, bsh in cases:
        assert (bs, bsh) != (src, sh), f"wlan-link N selftest case did not mutate: {label}"
        try:
            check_wlan_link_n_sources(bs, bsh, "selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlan-link N {label} -> {exc}")
        else:
            raise AssertionError(f"wlan-link N self-test FAILED to catch: {label}")
    print(f"PASS wlan-link N policy self-test: {caught}/{len(bad_n) + len(cases)} bad N policies rejected")
    return caught


_WLANLINK_W32_CONSTS = (r"#0x890\b", r"movk\s+x\d+, #0x1480, lsl #16", r"cmp\s+x\d+, #0x17f\b", r"cmp\s+x\d+, #0xfff\b", r"cmp\s+x\d+, #0x17\b", r"cmp\s+w\d+, #0x54\b", r"cmp\s+w\d+, #0x80\b",
                        r"cmp\s+w\d+, #0x474\b", r"cmp\s+w\d+, #0x3bc\b",
                        r"mov\s+w\d+, #0x1000000\b", r"mov\s+w\d+, #0x80000000\b", r"movk\s+w\d+, #0x60ff, lsl #16", r"mov\s+w\d+, #0x60ffffff\b",
                        r"movk\s+w\d+, #0x6040, lsl #16", r"tst\s+w\d+, #0xffff\b",
                        r"cmp\s+w\d+, #0x204\b", r"cmp\s+w\d+, #0x208\b", r"cmp\s+w\d+, #0x210\b", r"cmp\s+w\d+, #0x214\b", r"mov\s+w\d+, #0x6000ffff\b", r"mov\s+w\d+, #0x60000000\b",
                        r"movk\s+w\d+, #0x1800, lsl #16", r"movk\s+w\d+, #0x6000, lsl #16",
                        r"add\s+w\d+, w\d+, #0x105, lsl #12", r"mov\s+w\d+, #0x18120000\b", r"add\s+w\d+, w\d+, #0x12, lsl #12", r"add\s+w\d+, w\d+, #0x26, lsl #12")	# the ATU values (S26): LOWER_TARGET, CR2 enable, LOWER_BASE (CFG window), LIMIT; the CFG BAR2 assignment 0x60400004 and the Command mask 0xffff (S28); S29: region 1 offsets 0x204/0x208/0x210/0x214 and values 0x60000000 / 0x6000ffff, the window select 0x18000000 / 0x18001000, the BAR0 state 0x60000004


_A64_PARSE: dict = {}


def _a64_call(block: str, args: dict, name: str, fn: str, max_steps: int = 200) -> int:
    """A tiny AArch64 evaluator for ONE pure, call-free function (objdump text of its block): runs it from the first instruction to `ret` and returns w0. Supports exactly the integer subset the compiler uses for the
    window / offset decisions (mov movk add sub and orr eor lsr lsl cmp tst ccmp cset csel b b.cond cbz cbnz ret); anything else is an error (fail closed). args: register number -> value (x0 = window, x1 = offset). AP-16: audits
    the BEHAVIOUR of the compiled code, not its instruction order or register choice."""
    if block not in _A64_PARSE:	# parse once per function text (the memw_ok model calls it ~10^5 times)
        _ins = []
        n_ops = {"mov": 2, "cmp": 2, "tst": 2, "cset": 2, "csetm": 2, "and": 3, "orr": 3, "eor": 3, "lsr": 3, "lsl": 3, "csel": 4, "csinc": 4, "ccmp": 4, "ccmn": 4, "tbz": 3, "tbnz": 3}
        for m in re.finditer(r"(?m)^\s*([0-9a-f]+):\t[0-9a-f]{8} \t(\S+)[ \t]*([^\n]*)", block):
            mn_, ops_ = m.group(2), m.group(3).split("\t")[0].split("//")[0].strip()
            o_ = [x.strip() for x in ops_.split(",")]
            # review 15 findings 3/4: fail closed (when the instruction is EXECUTED) on any operand form that is not modelled (shifted immediates / registers were silently ignored)
            bad_ = (mn_ in n_ops and len(o_) != n_ops[mn_]) or (mn_ == "movk" and not (len(o_) == 2 or (len(o_) == 3 and re.fullmatch(r"lsl #(16|32|48)", o_[2])))) or \
                   (mn_ in ("add", "sub") and not (len(o_) == 3 or (len(o_) == 4 and o_[2].startswith("#") and re.fullmatch(r"lsl #(0|12)", o_[3]))))
            _ins.append((int(m.group(1), 16), mn_, ops_, o_, bool(o_) and o_[0][:1] == "x", bad_))
        _A64_PARSE[block] = (_ins, {a[0]: i for i, a in enumerate(_ins)})
    ins, idx = _A64_PARSE[block]
    M32, M64 = 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF
    rg = [0] * 32
    for k, v in args.items():
        rg[k] = v & M64

    def reg(x):
        x = x.strip()
        if x in ("wzr", "xzr"):
            return 0
        return rg[int(x[1:])] & (M32 if x[0] == "w" else M64)

    def put(x, v):
        x = x.strip()
        if x in ("wzr", "xzr"):
            return
        rg[int(x[1:])] = v & (M32 if x[0] == "w" else M64)

    def val(x, wide):
        x = x.strip()
        if x.startswith("#"):
            return int(x[1:], 0) & (M64 if wide else M32)
        return reg(x)

    def shifted(ops):	# [Rd, Rn, #imm (, lsl #12)] -> imm value
        v = int(ops[2].strip()[1:], 0) if ops[2].strip().startswith("#") else None
        if v is not None and len(ops) > 3:
            v <<= int(ops[3].split("#")[1])
        return v

    def sub_flags(a, b, wide):
        m = M64 if wide else M32
        sb = 63 if wide else 31
        r = (a - b) & m
        return ((r >> sb) & 1, int(r == 0), int(a >= b), int((((a ^ b) & (a ^ r)) >> sb) & 1))	# N Z C V

    def cond(c, f):
        n, z, cc, v = f
        t = {"eq": z, "ne": not z, "hi": cc and not z, "ls": (not cc) or z, "hs": cc, "cs": cc, "lo": not cc, "cc": not cc, "mi": n, "pl": not n, "vs": v, "vc": not v, "ge": n == v, "lt": n != v, "gt": (not z) and n == v, "le": z or n != v}
        return bool(t[c])

    fl = (0, 0, 0, 0)
    pc = 0
    for _ in range(max_steps):
        addr, mn, ops, o, wide, bad = ins[pc]
        nxt = pc + 1
        if bad:
            raise AssertionError(f"{name}: {fn}: unmodelled operand form `{mn} {ops}` at {addr:x} (the evaluator fails closed)")
        if mn == "ret":
            return rg[0] & M32
        if mn == "mov":
            put(o[0], val(o[1], wide))
        elif mn == "movk":
            sh = int(o[2].split("#")[1]) if len(o) > 2 else 0
            put(o[0], (reg(o[0]) & ~(0xFFFF << sh)) | (int(o[1][1:], 0) << sh))
        elif mn in ("add", "sub"):
            b = shifted(o) if o[2].startswith("#") else reg(o[2])
            put(o[0], reg(o[1]) + b if mn == "add" else reg(o[1]) - b)
        elif mn in ("and", "orr", "eor"):
            a_, b = reg(o[1]), val(o[2], wide)
            put(o[0], a_ & b if mn == "and" else a_ | b if mn == "orr" else a_ ^ b)
        elif mn == "lsr":
            put(o[0], reg(o[1]) >> (val(o[2], wide) % (64 if wide else 32)))
        elif mn == "lsl":
            put(o[0], reg(o[1]) << (val(o[2], wide) % (64 if wide else 32)))
        elif mn == "cmp":
            fl = sub_flags(reg(o[0]), val(o[1], o[0][:1] == "x"), o[0][:1] == "x")
        elif mn == "tst":
            r_ = reg(o[0]) & val(o[1], o[0][:1] == "x")
            fl = ((r_ >> (63 if o[0][:1] == "x" else 31)) & 1, int(r_ == 0), 0, 0)
        elif mn == "ccmp":
            fl = sub_flags(reg(o[0]), val(o[1], o[0][:1] == "x"), o[0][:1] == "x") if cond(o[3], fl) else tuple((int(o[2][1:], 0) >> k) & 1 for k in (3, 2, 1, 0))
        elif mn == "ccmn":	# flags of Rn + imm (research 40: the compiled memw_ok compares against 0xFFFFFFFF with cmn #1)
            wd = o[0][:1] == "x"
            m_ = M64 if wd else M32
            sb_ = 63 if wd else 31
            if cond(o[3], fl):
                a_, b_ = reg(o[0]), val(o[1], wd)
                r_ = (a_ + b_) & m_
                fl = ((r_ >> sb_) & 1, int(r_ == 0), int(a_ + b_ > m_), int(((~(a_ ^ b_) & (a_ ^ r_)) >> sb_) & 1))
            else:
                fl = tuple((int(o[2][1:], 0) >> k) & 1 for k in (3, 2, 1, 0))
        elif mn == "cset":
            put(o[0], 1 if cond(o[1], fl) else 0)
        elif mn == "csetm":
            put(o[0], (M64 if wide else M32) if cond(o[1], fl) else 0)
        elif mn == "csel":
            put(o[0], reg(o[1]) if cond(o[3], fl) else reg(o[2]))
        elif mn == "csinc":
            put(o[0], reg(o[1]) if cond(o[3], fl) else reg(o[2]) + 1)
        elif mn in ("tbz", "tbnz"):
            tgt = int(o[2].split()[0], 16)
            if tgt not in idx:
                raise AssertionError(f"{name}: {fn}: branch to {tgt:x} outside the function")
            if ((reg(o[0]) >> int(o[1][1:], 0)) & 1) == (mn == "tbnz"):
                nxt = idx[tgt]
        elif mn.startswith("b.") or mn == "b" or mn in ("cbz", "cbnz"):
            tgt = int((o[1] if mn in ("cbz", "cbnz") else o[0]).split()[0], 16)
            if tgt not in idx:
                raise AssertionError(f"{name}: {fn}: branch to {tgt:x} outside the function")
            if mn == "b" or (cond(mn[2:], fl) if mn.startswith("b.") else (reg(o[0]) == 0) == (mn == "cbz")):
                nxt = idx[tgt]
        else:
            raise AssertionError(f"{name}: {fn}: unsupported instruction {mn} {ops} at {addr:x} (the evaluator fails closed)")
        pc = nxt
    raise AssertionError(f"{name}: {fn} did not terminate in {max_steps} steps")


def _wlanlink_mem_ok_model(blocks: list, name: str) -> None:
    """The COMPILED mem_ok (noipa, pure): for a grid of windows x offsets it accepts exactly (research 38 s.3 + the older steps): 0x18105000 -> offset < 0xE00; 0x18000000 -> {0, 0xAC, 0xFC, 0x1E0}; 0x18012000 -> the five PMU words;
    0x18120000 / 0x18126000 -> {0x408, 0x800, 0x804}; research 40: 0x18001000 -> {0x1E8}, 0x18026000 -> {0, 0x10, 0x40}; every other window -> nothing."""
    m = [b for b in blocks if re.match(r"[0-9a-f]+ <mem_ok>:", b)]
    if len(m) != 1 or re.search(r"\b(bl|blr|br|ldr|ldrb|ldrh|str|strb|strh|stp|ldp|adrp)\b", m[0]) or "R_AARCH64" in m[0]:
        raise AssertionError(f"{name}: expected exactly one mem_ok function, pure (no call, no load/store, no relocation)")
    exp = dict(_WLAN_MEM_OFFS)
    wins = list(exp) + [0x18105000, 0x18011000, 0x18013000, 0x18012004, 0x18121000, 0x18127000, 0x18125000, 0x18027000, 0x18025000, 0x18100000, 0x18106000, 0x18104000, 0x18105004, 0x38105000, 0x98012000, 0, 1, 0xFFFFFFFF]
    offs = sorted(set(range(0, 0x1010, 4)) | {1, 2, 3, 0xAD, 0x601, 0x605, 0x409, 0x801, 0xDFF, 0xE01, 0xFFFFFFFF, 0xFFFFFFFC, 0x80000600, 0x100000600, 0x100000000})
    for win in wins:
        for off in offs:
            want = off < 0xE00 if win == 0x18105000 else off in exp.get(win, ())
            got = _a64_call(m[0], {0: win, 1: off & 0xFFFFFFFF}, name, "mem_ok") != 0 if off < 0x100000000 else None
            if got is None:
                continue
            if bool(got) != want:
                raise AssertionError(f"{name}: the compiled mem_ok is wrong: window {win:#x} offset {off:#x} {'refused' if want else 'accepted'} (rule: EROM page offsets < 0xE00; ChipCommon 0/0xAC/0xFC/0x1E0; PMU 0x600/0x604/0x608/0x618/0x61C; ARM CA7 + SYS_MEM wrapper 0x408/0x800/0x804; PCIe core page 0x1E8; SYS_MEM core 0/0x10/0x40; any other window nothing)")


# the BAR0 write rules, spelled out as DATA (independent of the C text and of memw_ok's instruction order): (window, offset, new value) -> (pred mask, pred value, old mask, old value); research 40 s.5
_MEMW_EXACT = {(0x18126000, 0x800, 1): (0, 0, 0, 0), (0x18126000, 0x800, 0): (0x3FFF, 0x3, 0, 0), (0x18126000, 0x408, 0x3): (1, 1, 0, 0), (0x18126000, 0x408, 0x1): (1, 0, 0x3FFF, 0x3),
               (0x18120000, 0x800, 1): (0, 0, 0, 0), (0x18120000, 0x800, 0): (0x3FFF, 0x23, 0, 0), (0x18120000, 0x408, 0x23): (1, 1, 0, 0), (0x18120000, 0x408, 0x21): (1, 0, 0x3FFF, 0x23)}


def _memw_spec(win: int, off: int, old: int, v: int, pred: int) -> bool:
    if win == 0x18001000:	# SRPWR request: only bit 0x200 set (0 -> 1 or already 1), the other bits as read, not a dead read, only when PowerDomain id [31:28] != 0 (si_srpwr_cap)
        return off == 0x1E8 and old != 0xFFFFFFFF and (old & 0xF0000000) != 0 and v == old | 0x200
    if win == 0x18026000:	# bankidx := 0, only with a live coreinfo: not a dead read, no ROM bank (nrb = (coreinfo & 0x3E0) >> 5 == 0), at least one RAM bank (nb = coreinfo & 0x1F)
        return off == 0x10 and v == 0 and pred != 0xFFFFFFFF and (pred & 0x3E0) == 0 and (pred & 0x1F) >= 1
    r = _MEMW_EXACT.get((win, off, v))
    return r is not None and (pred & r[0]) == r[1] and (old & r[2]) == r[3]


_MEMW_WINS = (0x18001000, 0x18126000, 0x18120000, 0x18026000, 0x18000000, 0x18012000, 0x18105000, 0x18127000, 0x18121000, 0x18025000, 0)
_MEMW_OFFS = (0x0, 0x10, 0x14, 0x40, 0x1E8, 0x1EC, 0x400, 0x408, 0x800, 0x804)
_MEMW_VALS = (0, 1, 2, 3, 0x5, 0x21, 0x23, 0x25, 0x200, 0x201, 0x3FFF, 0x4003, 0xFFFFFFFF)
_MEMW_VALS_COARSE = (0, 1, 0x23, 0x200, 0xFFFFFFFF)
_MEMW_LIVE_WINS = (0x18001000, 0x18126000, 0x18120000, 0x18026000)


def _wlanlink_memw_model(blocks: list, name: str) -> None:
    """The COMPILED memw_ok (noipa, pure) and memw_pred: over a grid of windows x offsets x old x new x predecessor values the compiled code agrees with _memw_spec (the exact BAR0 write tuples: SRPWR request, SYS_MEM / ARM reset sequence with their
    IOCTL values 0x3/0x1 and 0x23/0x21, bankidx := 0 behind the coreinfo checks) and memw_pred names the right companion register. AP-16: behaviour of the compiled code, whatever registers and branch order the compiler picked."""
    m = [b for b in blocks if re.match(r"[0-9a-f]+ <memw_ok>:", b)]
    pr = [b for b in blocks if re.match(r"[0-9a-f]+ <memw_pred>:", b)]
    for blk, fn in ((m, "memw_ok"), (pr, "memw_pred")):
        if len(blk) != 1 or re.search(r"\b(bl|blr|br|ldr|ldrb|ldrh|str|strb|strh|stp|ldp|adrp)\b", blk[0]) or "R_AARCH64" in blk[0]:
            raise AssertionError(f"{name}: expected exactly one {fn} function, pure (no call, no load/store, no relocation)")
    for off in (0x0, 0x4, 0x10, 0x14, 0x40, 0x400, 0x404, 0x408, 0x40C, 0x800, 0x804, 0x808, 0x1E8, 0x80000800, 0x80000408, 0x10010):
        want = {0x800: 0x408, 0x408: 0x800, 0x10: 0}.get(off, 0xFFFFFFFF)
        got = _a64_call(pr[0], {0: off}, name, "memw_pred")
        if got != want:
            raise AssertionError(f"{name}: the compiled memw_pred(offset {off:#x}) returns {got:#x}, want {want:#x} (IOCTL <- RESET_CTL 0x800, RESET_CTL <- IOCTL 0x408, bankidx <- coreinfo 0, none otherwise)")
    for win in _MEMW_WINS:
        live = win in _MEMW_LIVE_WINS		# the four windows that have a rule get the full value cross product, every other window must refuse every sampled tuple
        vals = _MEMW_VALS if live else _MEMW_VALS_COARSE
        for off in _MEMW_OFFS:
            for old in vals:
                for v in vals:
                    for pred in vals:
                        want = _memw_spec(win, off, old, v, pred)
                        got = _a64_call(m[0], {0: win, 1: off, 2: old, 3: v, 4: pred}, name, "memw_ok") != 0
                        if got != want:
                            raise AssertionError(f"{name}: the compiled memw_ok is wrong: window {win:#x} offset {off:#x} old {old:#x} new {v:#x} predecessor {pred:#x} is {'refused' if want else 'accepted'} (rule: research 40 s.5 write tuples)")
    for old in (0, 0x200, 0x0FFFFFFF, 0x0FFFFFFF & ~0x200, 0x00020200, 0x10000000, 0x30020000, 0x80000000, 0xF0000200, 0xF0000000, 0xFFFFFFFF):	# SRPWR needs PowerDomain id [31:28] != 0 (review 16 finding 2)
        for v in (old | 0x200, old, old & ~0x200 & 0xFFFFFFFF, 0x200, 0x10000200, 0xFFFFFFFF):
            want = _memw_spec(0x18001000, 0x1E8, old, v, 0)
            got = _a64_call(m[0], {0: 0x18001000, 1: 0x1E8, 2: old, 3: v, 4: 0}, name, "memw_ok") != 0
            if got != want:
                raise AssertionError(f"{name}: the compiled memw_ok SRPWR rule is wrong: old {old:#x} new {v:#x} is {'refused' if want else 'accepted'} (needs PowerDomain id [31:28] != 0)")


def check_wlanlink_r32_object_text(blocks: list, name: str) -> None:
    """Review 10 L5 + S32 + S33: the compiled MEM read guard (r32 is noipa, so it is one function): the FIRST guard read is the BAR0 window select (cfg 0x80 = CFG+0x80); its value goes (same stack slot) into the call of mem_ok(window, offset)
    whose zero result is the refusal (the same target as the read failure); then region 1 CR2 == 0x80000000 at ATU+0x204, Command low half == 0x2 after a try_read32, the window re-check (offsets 0x200 / 0x208 / 0x210 / 0x214 and the values
    0x60000000 / 0x6000ffff): seven guard reads plus the load (tail call), never a write. The window / offset decision itself is audited by BEHAVIOUR in _wlanlink_mem_ok_model."""
    r = [b for b in blocks if re.match(r"[0-9a-f]+ <r32>:", b)]
    if len(r) != 1 or "try_write32" in r[0]:
        raise AssertionError(f"{name}: expected exactly one r32 function that never writes")
    t = r[0]
    c26 = list(re.finditer(r"R_AARCH64_CALL26\s+try_read32", t))
    if len(c26) != 7 or len(re.findall(r"R_AARCH64_JUMP26\s+try_read32", t)) != 1:
        raise AssertionError(f"{name}: r32 must hold seven guard reads (window select, CR2, Command, CR1, base, limit, target) and the load as one tail call")
    pre, seg = t[:c26[0].start()], t[c26[0].end():c26[1].start()]
    if not re.search(r"mov\s+x\d+, #0xe080\b", pre) or not re.search(r"movk\s+x\d+, #0x60ff, lsl #16", pre):
        raise AssertionError(f"{name}: the first guard read of the compiled r32 is no longer the BAR0 window select CFG+0x80 (0x60ffe080)")
    slot = re.findall(r"add\s+x1, sp, #(0x[0-9a-f]+)", pre)
    mc = re.search(r"cbz\s+w0, ([0-9a-f]+) <r32\+0x[0-9a-f]+>", seg)
    if not slot or not mc:
        raise AssertionError(f"{name}: r32: cannot find the window slot (add x1, sp, #N before the first guard read) or the read-failure refusal (cbz w0)")
    refuse = mc.group(1)
    if not re.search(r"(?m)^\s*%s:\t[0-9a-f]+ \t[^\n]*\n(?:[^\n]*\n){0,2}?[^\n]*\tmov\s+w0, #0x0\b[^\n]*\n(?:[^\n]*\n){0,2}?[^\n]*\tret\b" % refuse, t):
        raise AssertionError(f"{name}: the compiled r32 refusal target is no longer `mov w0, #0x0 ... ret`")
    sl = int(slot[-1], 16)
    if not re.search(r"ldr\s+w0, \[sp, #%d\]\n(?:[^\n]*\n){0,3}?[^\n]*\bbl\s+[0-9a-f]+ <mem_ok>\n[^\n]*\tcbz\s+w0, %s <r32\+0x[0-9a-f]+>" % (sl, refuse), seg + "\n"):
        raise AssertionError(f"{name}: the compiled r32 no longer calls mem_ok(window just read, offset) and refuses on a zero result (ldr w0,[sp,#{sl}] ... bl mem_ok; cbz w0 -> refusal)")
    if len(re.findall(r"\bbl\s+[0-9a-f]+ <mem_ok>", t)) != 1 or not re.search(r"mov\s+w1, w\d+\n(?:[^\n]*\n){0,2}?[^\n]*\bbl\s+[0-9a-f]+ <mem_ok>", seg):
        raise AssertionError(f"{name}: r32 must call mem_ok exactly once, with the offset argument (mov w1, <offset>)")
    for rx in (r"mov\s+w\d+, #0x80000000\b", r"mov\s+x\d+, #0x204\b", r"mov\s+x\d+, #0x200\b", r"mov\s+x\d+, #0x208\b", r"mov\s+x\d+, #0x210\b", r"mov\s+x\d+, #0x214\b",
               r"mov\s+w\d+, #0x60000000\b", r"mov\s+w\d+, #0x6000ffff\b"):
        if not re.search(rx, t):
            raise AssertionError(f"{name}: the compiled r32 lost the MEM guard constant /{rx}/")
    if not re.search(r"R_AARCH64_CALL26\s+try_read32(?:.*\n)*?.*\bcmp\s+w\d+, #0x2\n", t):
        raise AssertionError(f"{name}: the compiled r32 lost the Command == 0x2 compare after a guard read")
    _wlanlink_mem_ok_model(blocks, name)
    _wlanlink_memw_model(blocks, name)


def check_wlanlink_w32_object_text(disasm: str, name: str = "wlanlink.o") -> None:
    """The COMPILED w32 keeps its independent allowlist (review B1 of wlanen: a specialised helper loses its address compare): the window / offset compare constants are in it, there is one
    try_write32 call and one or two try_read32 calls in it (the old value), one call of ctx_read (the live state for the stateful rules: its own function holds the other guarded reads), and w32 is called from exactly one place."""
    blocks = re.split(r"(?m)^(?=[0-9a-f]+ <[^>]+>:$)", disasm)
    w = [b for b in blocks if re.match(r"[0-9a-f]+ <w32>:", b)]
    if len(w) != 1:
        raise AssertionError(f"{name}: expected exactly one w32 function, found {len(w)}")
    for rx in _WLANLINK_W32_CONSTS:
        if not re.search(rx, w[0]):
            raise AssertionError(f"{name}: the compiled w32 lost the allowlist compare /{rx}/ (specialised away?)")
    if len(re.findall(r"R_AARCH64_CALL26\s+try_write32", w[0])) != 1 or not 2 <= len(re.findall(r"R_AARCH64_CALL26\s+try_read32", w[0])) <= 3 or not 1 <= len(re.findall(r"\bbl\s+[0-9a-f]+ <ctx_read>", w[0])) <= 2:
        raise AssertionError(f"{name}: w32 has exactly one try_write32, two or three try_read32 calls (the old value of a non-BAR0 row; the BAR0 window select; the compiler may duplicate one) and one or two ctx_read calls (ONE call site in the source, pinned; the compiler duplicated it for the BAR0 path)")
    if len(re.findall(r"\bbl\s+[0-9a-f]+ <r32>", w[0])) != 2 or len(re.findall(r"\bbl\s+[0-9a-f]+ <memw_pred>", w[0])) != 1 or not re.search(r"mov\s+x\d+, #0xe080\b", w[0]):
        raise AssertionError(f"{name}: w32 must read the BAR0 old value and the predecessor through r32 (two calls), call memw_pred once and read the window select cfg 0x80 (0x60ffe080) itself")
    if [b.splitlines()[0] for b in blocks if b is not w[0] and re.search(r"\bbl\s+[0-9a-f]+ <memw_pred>", b)]:
        raise AssertionError(f"{name}: memw_pred must be called only from w32")
    cx = [b for b in blocks if re.match(r"[0-9a-f]+ <ctx_read>:", b)]
    if len(cx) != 1 or "try_write32" in cx[0] or not 8 <= len(re.findall(r"R_AARCH64_CALL26\s+try_read32", cx[0])) <= 10:
        raise AssertionError(f"{name}: ctx_read must exist once, never write, and hold the eight guarded state reads (Command, BAR0, region 1 CR1 / base / upper base / limit / target / upper target)")
    for off in ("200", "208", "20c", "210", "214", "218"):	# review 10 L2: all six region 1 registers are read (CR1, LOWER_BASE, UPPER_BASE, LIMIT, LOWER_TARGET, UPPER_TARGET)
        if not re.search(r"mov\s+x\d+, #0x" + off + r"\b", cx[0]):
            raise AssertionError(f"{name}: ctx_read lost the region 1 state read of ATU+0x{off}")
    check_wlanlink_r32_object_text(blocks, name)
    if [b.splitlines()[0] for b in blocks if b is not w[0] and re.search(r"\bbl\s+[0-9a-f]+ <ctx_read>", b)]:
        raise AssertionError(f"{name}: ctx_read must be called only from w32")
    callers = [b.splitlines()[0] for b in blocks if b is not w[0] and re.search(r"\bbl\s+[0-9a-f]+ <w32>", b)]
    if len(callers) != 1 or "<run_row" not in callers[0]:
        raise AssertionError(f"{name}: w32 must have exactly one caller, run_row: {callers}")


def wlanlink_object_selftest(obj_path: pathlib.Path) -> int:
    disasm = _touch_obj_info(obj_path)[0]
    check_wlanlink_w32_object_text(disasm, "wlanlink selftest")  # control
    wblk = re.search(r"(?ms)^[0-9a-f]+ <w32>:\n.*?(?=^[0-9a-f]+ <|\Z)", disasm).group(0)
    rblk = re.search(r"(?ms)^[0-9a-f]+ <r32>:\n.*?(?=^[0-9a-f]+ <|\Z)", disasm).group(0)
    cblk = re.search(r"(?ms)^[0-9a-f]+ <ctx_read>:\n.*?(?=^[0-9a-f]+ <|\Z)", disasm).group(0)
    mblk = re.search(r"(?ms)^[0-9a-f]+ <mem_ok>:\n.*?(?=^[0-9a-f]+ <|\Z)", disasm).group(0)
    mwblk = re.search(r"(?ms)^[0-9a-f]+ <memw_ok>:\n.*?(?=^[0-9a-f]+ <|\Z)", disasm).group(0)
    pblk = re.search(r"(?ms)^[0-9a-f]+ <memw_pred>:\n.*?(?=^[0-9a-f]+ <|\Z)", disasm).group(0)
    def seg_mut(rb, fn):	# apply fn to the text between the first and the second try_read32 call of an r32 block
        c = list(re.finditer(r"R_AARCH64_CALL26\s+try_read32", rb))
        return rb[:c[0].end()] + fn(rb[c[0].end():c[1].start()]) + rb[c[1].start():]

    cases = [
        ("w32 specialised: no compares", disasm.replace(wblk, "0000000000000100 <w32>:\n 100:\ta9bd7bfd \tstp\tx29, x30, [sp, #-48]!\n 104:\t94000000 \tbl\t0 <try_read32>\n\t\t\t104: R_AARCH64_CALL26\ttry_read32\n 108:\t94000000 \tbl\t0 <try_write32>\n\t\t\t108: R_AARCH64_CALL26\ttry_write32\n 10c:\td65f03c0 \tret\n", 1)),
        ("DBI offset changed", disasm.replace("#0x890", "#0x8bc", 1)),
        ("IA window widened", re.sub(r"(cmp\s+x\d+, )#0x17f\b", r"\1#0x1ff", disasm, count=1)),
        ("ELBI offset dropped", re.sub(r"(cmp\s+w\d+, )#0x54\b", r"\1#0x55", disasm, count=1)),
        ("ATU limit value changed", disasm.replace(wblk, wblk.replace("#0x60ffffff", "#0x61ffffff", 1), 1)),
        ("CFG BAR2 assignment changed", disasm.replace(wblk, wblk.replace("#0x6040", "#0x6080", 1), 1)),
        ("CFG Command mask narrowed", disasm.replace(wblk, re.sub(r"tst\tw(\d+), #0xffff", r"tst\tw\1, #0x2", wblk, count=1), 1)),
        ("ATU CR2 value changed", disasm.replace(wblk, wblk.replace("#0x80000000", "#0x80000001"), 1)),
        ("ATU region 1 LIMIT changed", disasm.replace(wblk, re.sub(r"mov\tw(\d+), #0x6000ffff", r"mov\tw\1, #0x6001ffff", wblk, count=1), 1)),
        ("ATU region 1 offset 0x204 changed", disasm.replace(wblk, re.sub(r"cmp\tw(\d+), #0x204", r"cmp\tw\1, #0x205", wblk, count=1), 1)),
        ("ATU region 1 offset 0x214 changed", disasm.replace(wblk, re.sub(r"cmp\tw(\d+), #0x214", r"cmp\tw\1, #0x218", wblk, count=1), 1)),
        ("CFG window select changed (0x18000000 -> 0x19000000)", disasm.replace(wblk, re.sub(r"movk\tw(\d+), #0x1800, lsl #16", r"movk\tw\1, #0x1900, lsl #16", wblk), 1)),
        ("CFG Command arm BAR0 state changed", disasm.replace(wblk, re.sub(r"movk\tw(\d+), #0x6000, lsl #16", r"movk\tw\1, #0x6100, lsl #16", wblk), 1)),
        ("w32 without the ctx_read call (every compiled copy)", disasm.replace(wblk, re.sub(r"bl\s+([0-9a-f]+) <ctx_read>", r"nop", wblk), 1)),
        ("a second caller of ctx_read", disasm + "0000000000fff100 <evil2>:\n fff100:\t97fffe78 \tbl\t100 <ctx_read>\n fff104:\td65f03c0 \tret\n"),
        ("ctx_read writes", disasm.replace("<ctx_read>:\n", "<ctx_read>:\n 1:\t94000000 \tbl\t0 <try_write32>\n\t\t\t1: R_AARCH64_CALL26\ttry_write32\n", 1)),
        ("ATU base (CFG window) changed", disasm.replace(wblk, wblk.replace("#0x60ff, lsl #16", "#0x61ff, lsl #16"), 1)),
        ("r32 Command compare weakened", disasm.replace(rblk, re.sub(r"cmp\tw(\d+), #0x2\n", r"cmp\tw\1, #0x3\n", rblk), 1)),
        ("r32 CR2 value changed", disasm.replace(rblk, re.sub(r"mov\tw(\d+), #0x80000000", r"mov\tw\1, #0x80000001", rblk), 1)),
        ("r32 CR2 offset changed", disasm.replace(rblk, re.sub(r"mov\tx(\d+), #0x204", r"mov\tx\1, #0x208", rblk), 1)),
        ("r32 window limit changed", disasm.replace(rblk, re.sub(r"mov\tw(\d+), #0x6000ffff", r"mov\tw\1, #0x6001ffff", rblk), 1)),
        ("r32 window target changed", disasm.replace(rblk, re.sub(r"mov\tw(\d+), #0x60000000", r"mov\tw\1, #0x60400000", rblk), 1)),
        ("r32 CR1 read dropped", disasm.replace(rblk, re.sub(r"mov\tx(\d+), #0x200\b", r"mov\tx\1, #0x2f0", rblk), 1)),
        ("r32 window select register changed (cfg 0x80 -> 0x84)", disasm.replace(rblk, re.sub(r"mov\tx(\d+), #0xe080\b", r"mov\tx\1, #0xe084", rblk), 1)),
        ("w32 window move page changed (0x105000 -> 0x106000)", disasm.replace(wblk, re.sub(r"#0x105, lsl #12", r"#0x106, lsl #12", wblk), 1)),
        ("w32 S33 ARM CA7 wrapper window changed (0x18120000 -> 0x18121000, every occurrence)", disasm.replace(wblk, re.sub(r"#0x18120000\b", r"#0x18121000", wblk), 1)),
        ("w32 S33 PMU window page changed (0x12 -> 0x13, every occurrence)", disasm.replace(wblk, re.sub(r"(add\tw\d+, w\d+, )#0x12, lsl #12", r"\1#0x13, lsl #12", wblk), 1)),
        ("w32 S33 SYS_MEM core window page changed (0x26 -> 0x27, every occurrence)", disasm.replace(wblk, re.sub(r"(add\tw\d+, w\d+, )#0x26, lsl #12", r"\1#0x27, lsl #12", wblk), 1)),
        ("w32 window page arithmetic dropped", disasm.replace(wblk, re.sub(r"add\tw(\d+), w(\d+), #0x105, lsl #12", r"mov\tw\1, w\2", wblk), 1)),
        # S33: the compiled mem_ok is audited by behaviour; each defect class (wrong window value, extra read offset, widened range, dropped check) changes the constants of the compiled function, whatever registers the compiler picks (AP-16)
        ("mem_ok EROM page window changed (mov #0x5000)", disasm.replace(mblk, re.sub(r"mov\tw(\d+), #0x5000\b", r"mov\tw\1, #0x6000", mblk), 1)),
        ("mem_ok EROM page window changed (movk #0x1810)", disasm.replace(mblk, re.sub(r"movk\tw(\d+), #0x1810, lsl #16", r"movk\tw\1, #0x1811, lsl #16", mblk), 1)),
        ("mem_ok ChipCommon window derivation changed (0x105000 -> 0x106000)", disasm.replace(mblk, re.sub(r"#0x105, lsl #12", r"#0x106, lsl #12", mblk), 1)),
        ("mem_ok PMU window changed (0x12 -> 0x13 pages)", disasm.replace(mblk, re.sub(r"(add\tw\d+, w\d+, )#0x12, lsl #12", r"\1#0x13, lsl #12", mblk), 1)),
        ("mem_ok SYS_MEM wrapper window changed (0x126 -> 0x127)", disasm.replace(mblk, re.sub(r"#0x126, lsl #12", r"#0x127, lsl #12", mblk), 1)),
        ("mem_ok ARM CA7 wrapper window changed (0x18120000 -> 0x18121000)", disasm.replace(mblk, re.sub(r"#0x18120000\b", r"#0x18121000", mblk), 1)),
        ("mem_ok SYS_MEM core window changed (0x26 -> 0x27 pages)", disasm.replace(mblk, re.sub(r"#0x26, lsl #12", r"#0x27, lsl #12", mblk), 1)),
        ("mem_ok SYS_MEM core page: offset 8 accepted (mask 0xffffffef -> 0xffffffe7)", disasm.replace(mblk, re.sub(r"and\tw(\d+), w(\d+), #0xffffffef\b", r"and\tw\1, w\2, #0xffffffe7", mblk), 1)),
        ("mem_ok SYS_MEM core page: bankinfo 0x40 moved (0x44)", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0x40\n", r"cmp\tw\1, #0x44\n", mblk), 1)),
        ("mem_ok PCIe core page window changed (0x1 -> 0x2 pages)", disasm.replace(mblk, re.sub(r"#0x1, lsl #12", r"#0x2, lsl #12", mblk), 1)),
        ("mem_ok PCIe core page offset 0x1E8 -> 0x1EC", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0x1e8\b", r"cmp\tw\1, #0x1ec", mblk), 1)),
        ("mem_ok PCIe core page takes any offset", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0x1e8\n(\s+[0-9a-f]+:\t[0-9a-f]+ \t)cset\tw(\d+), eq", r"cmp\tw\1, #0x1e8\n\2mov\tw\3, #0x1", mblk), 1)),
        ("mem_ok offset 0xAC changed", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0xac\b", r"cmp\tw\1, #0xa8", mblk), 1)),
        ("mem_ok offset 0xFC changed", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0xfc\b", r"cmp\tw\1, #0xf8", mblk), 1)),
        ("mem_ok offset 0x1E0 changed", disasm.replace(mblk, re.sub(r"mov\tw(\d+), #0x1e0\b", r"mov\tw\1, #0x1e4", mblk), 1)),
        ("mem_ok offset 0 dropped from ChipCommon", disasm.replace(mblk, re.sub(r"ccmp\tw(\d+), #0x0,", r"ccmp\tw\1, #0x1,", mblk), 1)),
        ("mem_ok wrapper offset 0x408 changed to 0x400", disasm.replace(mblk, re.sub(r"mov\tw(\d+), #0x408\b", r"mov\tw\1, #0x400", mblk), 1)),
        ("mem_ok wrapper offset mask widened (0x804 -> also 0x808)", disasm.replace(mblk, re.sub(r"and\tw(\d+), w(\d+), #0xfffffffb\b", r"and\tw\1, w\2, #0xfffffff3", mblk), 1)),
        ("mem_ok wrapper offset 0x800 changed to 0x808", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0x800\b", r"cmp\tw\1, #0x808", mblk), 1)),
        ("mem_ok PMU bit set changed (0x11000111 -> 0x11000113: offset 0x601)", disasm.replace(mblk, re.sub(r"mov\tx(\d+), #0x111\b", r"mov\tx\1, #0x113", mblk), 1)),
        ("mem_ok PMU bit set changed (0x11000111 -> 0x11100111: offset 0x614)", disasm.replace(mblk, re.sub(r"movk\tx(\d+), #0x1100, lsl #16", r"movk\tx\1, #0x1110, lsl #16", mblk), 1)),
        ("mem_ok PMU range check widened (0x1d -> 0x7f)", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0x1d\b", r"cmp\tw\1, #0x7f", mblk), 1)),
        ("mem_ok PMU range check dropped (csel condition inverted)", disasm.replace(mblk, re.sub(r"csel\tw(\d+), w(\d+), wzr, cc", r"csel\tw\1, w\2, wzr, cs", mblk), 1)),
        ("mem_ok EROM bound widened (0xdff -> 0xfff, review 13 M1)", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0xdff\b", r"cmp\tw\1, #0xfff", mblk), 1)),
        ("mem_ok EROM bound narrowed (0xdff -> 0x7ff)", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0xdff\b", r"cmp\tw\1, #0x7ff", mblk), 1)),
        ("mem_ok EROM bound condition inverted (ls -> hi)", disasm.replace(mblk, re.sub(r"cset\tw(\d+), ls\b", r"cset\tw\1, hi", mblk), 1)),
        ("mem_ok EROM bound compare dropped", disasm.replace(mblk, re.sub(r"cmp\tw(\d+), #0xdff\b", r"cmp\tw\1, #0x0", mblk), 1)),
        # review 15 findings 3/4: any non-zero result is an accept (r32 refuses only on cbz w0); operand forms the evaluator does not model fail closed (AP-16: regex over the whole block, no register numbers)
        ("mem_ok reject path returns 2 (r32 accepts any non-zero)", disasm.replace(mblk, re.sub(r"mov\tw(\d+), #0x0\b", r"mov\tw\1, #0x2", mblk), 1)),
        ("mem_ok EROM bound shifted immediate (cmp #0xdff, lsl #12)", disasm.replace(mblk, re.sub(r"(cmp\tw\d+, #0xdff)\b", r"\1, lsl #12", mblk), 1)),
        ("mem_ok wrapper mask with a shifted register (and w, w, w, lsl #3)", disasm.replace(mblk, re.sub(r"and\tw(\d+), w(\d+), #0xfffffffb\b", r"and\tw\1, w\2, w\2, lsl #3", mblk), 1)),
        ("mem_ok PMU range with a shifted register (sub w, w, w, lsl #2)", disasm.replace(mblk, re.sub(r"sub\tw(\d+), w(\d+), #0x600\b", r"sub\tw\1, w\2, w\2, lsl #2", mblk), 1)),
        ("mem_ok calls another function", disasm.replace(mblk, mblk.replace("ret", "bl\t0 <try_write32>\n\t\t\t1: R_AARCH64_CALL26\ttry_write32\n  1:\td65f03c0 \tret", 1), 1)),
        ("mem_ok became a load (reads memory)", disasm.replace(mblk, mblk.replace("cmp\tw1, #0xdff", "ldr\tw1, [x1]", 1), 1)),
        ("r32 does not call mem_ok", disasm.replace(rblk, re.sub(r"bl\t([0-9a-f]+) <mem_ok>", r"nop", rblk), 1)),
        ("r32 ignores the mem_ok result", disasm.replace(rblk, re.sub(r"(<mem_ok>\n\s+[0-9a-f]+:\t[0-9a-f]+ \t)cbz\tw0,", r"\1cbnz\tw1,", rblk), 1)),
        ("r32 passes another stack slot as the window", disasm.replace(rblk, re.sub(r"ldr\tw0, \[sp, #60\]", r"ldr\tw0, [sp, #56]", rblk), 1)),
        ("r32 passes no offset to mem_ok", disasm.replace(rblk, re.sub(r"mov\tw1, w(\d+)\n(\s+[0-9a-f]+:\t[0-9a-f]+ \tbl\t[0-9a-f]+ <mem_ok>)", r"mov\tw1, wzr\n\2", rblk), 1)),
        ("r32 mem_ok refusal goes to a different target", disasm.replace(rblk, re.sub(r"(<mem_ok>\n\s+[0-9a-f]+:\t[0-9a-f]+ \tcbz\tw0, )[0-9a-f]+ <r32\+0x[0-9a-f]+>", r"\g<1>7fc <r32+0xb0>", rblk), 1)),
        ("r32 writes", disasm.replace("<r32>:\n", "<r32>:\n 1:\t94000000 \tbl\t0 <try_write32>\n\t\t\t1: R_AARCH64_CALL26\ttry_write32\n", 1)),
        ("r32 guard read dropped", disasm.replace(rblk, re.sub(r"R_AARCH64_CALL26\ttry_read32", "R_AARCH64_NONE\ttry_read32", rblk, count=1), 1)),
        ("ctx_read lost the CR1 read", disasm.replace(cblk, re.sub(r"mov\tx(\d+), #0x200\b", r"mov\tx\1, #0x2f0", cblk), 1)),
        ("ctx_read lost the UPPER_BASE read", disasm.replace(cblk, re.sub(r"mov\tx(\d+), #0x20c\b", r"mov\tx\1, #0x2fc", cblk), 1)),
        ("ctx_read lost the UPPER_TARGET read", disasm.replace(cblk, re.sub(r"mov\tx(\d+), #0x218\b", r"mov\tx\1, #0x2f8", cblk), 1)),
        # S34-S36 (research 40, W-C3): the compiled memw_ok / memw_pred are audited by BEHAVIOUR (_wlanlink_memw_model); each defect class (wrong value, extra write offset, wrong window, wrong order / missing predecessor, bankidx other than 0 or without the nrb / nb checks; the `coreinfo != all ones` test is implied by the nrb check, so it has no mutant) changes the compiled constants or branches (AP-16: regex over the whole block, no register numbers)
        ("memw_ok PCIe core page window changed (mov #0x1000 -> #0x2000)", disasm.replace(mwblk, re.sub(r"mov\tw(\d+), #0x1000\b", r"mov\tw\1, #0x2000", mwblk), 1)),
        ("memw_ok PCIe core page window changed (movk #0x1800 -> #0x1900)", disasm.replace(mwblk, re.sub(r"movk\tw(\d+), #0x1800, lsl #16", r"movk\tw\1, #0x1900, lsl #16", mwblk), 1)),
        ("memw_ok SYS_MEM wrapper window changed (0x125 -> 0x126 pages)", disasm.replace(mwblk, re.sub(r"#0x125, lsl #12", r"#0x126, lsl #12", mwblk), 1)),
        ("memw_ok ARM CA7 wrapper window changed (0x18120000 -> 0x18121000)", disasm.replace(mwblk, re.sub(r"#0x18120000\b", r"#0x18121000", mwblk), 1)),
        ("memw_ok SYS_MEM core page window changed (0x100 -> 0x101 pages)", disasm.replace(mwblk, re.sub(r"#0x100, lsl #12", r"#0x101, lsl #12", mwblk), 1)),
        ("memw_ok ARM IOCTL value changed (0x23 -> 0x63, an extra bit)", disasm.replace(mwblk, re.sub(r"mov\tw(\d+), #0x23\b", r"mov\tw\1, #0x63", mwblk), 1)),
        ("memw_ok ARM IOCTL value changed (0x23 -> 0x22, CPUHALT only)", disasm.replace(mwblk, re.sub(r"mov\tw(\d+), #0x23\b", r"mov\tw\1, #0x22", mwblk), 1)),
        ("memw_ok SYS_MEM IOCTL value changed (0x3 -> 0x7)", disasm.replace(mwblk, re.sub(r"mov\tw(\d+), #0x3\b", r"mov\tw\1, #0x7", mwblk), 1)),
        ("memw_ok wrapper selection inverted (csel ne -> eq)", disasm.replace(mwblk, re.sub(r"csel\tw(\d+), w(\d+), w(\d+), ne", r"csel\tw\1, w\2, w\3, eq", mwblk), 1)),
        ("memw_ok final IOCTL keeps FGC (csinc dropped: mov #0x21 -> #0x23)", disasm.replace(mwblk, re.sub(r"mov\tw(\d+), #0x21\b", r"mov\tw\1, #0x23", mwblk), 1)),
        ("memw_ok RESET_CTL offset moved (0x800 -> 0x808)", disasm.replace(mwblk, re.sub(r"cmp\tw(\d+), #0x800\b", r"cmp\tw\1, #0x808", mwblk), 1)),
        ("memw_ok IOCTL offset moved (0x408 -> 0x400)", disasm.replace(mwblk, re.sub(r"cmp\tw(\d+), #0x408\b", r"cmp\tw\1, #0x400", mwblk), 1)),
        ("memw_ok powerctl offset moved (0x1e8 -> 0x1ec)", disasm.replace(mwblk, re.sub(r"cmp\tw(\d+), #0x1e8\b", r"cmp\tw\1, #0x1ec", mwblk), 1)),
        ("memw_ok bankidx offset moved (0x10 -> 0x14)", disasm.replace(mwblk, re.sub(r"cmp\tw(\d+), #0x10\b", r"cmp\tw\1, #0x14", mwblk), 1)),
        ("memw_ok powerctl request bit changed (0x200 -> 0x400)", disasm.replace(mwblk, re.sub(r"orr\tw(\d+), w(\d+), #0x200\b", r"orr\tw\1, w\2, #0x400", mwblk), 1)),
        # the compiler folded `old != 0xFFFFFFFF && (old & 0xF0000000) != 0` into `old + 0xF0000000 <u 0xEFFFFFFF` (refuse old = 0 and old = all ones): audit the behaviour by changing the constants (AP-16), the evaluator grid decides
        ("memw_ok powerctl request on a dead read (all ones accepted)", disasm.replace(mwblk, re.sub(r"(mov\tw\d+, )#0xefffffff\b", r"\1#0xf0000000", mwblk), 1)),
        ("memw_ok powerctl request without the PowerDomain id check (id 0 accepted)", disasm.replace(mwblk, re.sub(r"(mov\tw\d+, )#0xefffffff\b", r"\1#0xfffffffe", mwblk), 1)),
        ("memw_ok powerctl PowerDomain id on another nibble", disasm.replace(mwblk, re.sub(r"(mov\tw\d+, )#0xf0000000\b", r"\1#0xe0000000", mwblk), 1)),
        ("memw_ok release without the IOCTL predecessor (mask 0x3fff -> 0x3ffe)", disasm.replace(mwblk, re.sub(r"and\tw(\d+), w(\d+), #0x3fff\b", r"and\tw\1, w\2, #0x3ffe", mwblk), 1)),
        ("memw_ok IOCTL mask widened (0x3fff -> 0xffff, the old value check)", disasm.replace(mwblk, re.sub(r"and\tw(\d+), w(\d+), #0x3fff\b", r"and\tw\1, w\2, #0xffff", mwblk), 1)),
        ("memw_ok IOCTL write without the reset asserted (tbnz -> tbz)", disasm.replace(mwblk, re.sub(r"\ttbnz\tw4, #0,", r"\ttbz\tw4, #0,", mwblk), 1)),
        ("memw_ok IOCTL set while in reset check weakened (and #0x1 -> #0x3)", disasm.replace(mwblk, re.sub(r"and\tw(\d+), w4, #0x1\b", r"and\tw\1, w4, #0x3", mwblk), 1)),
        ("memw_ok assert takes another value (mov #0x1 -> #0x3)", disasm.replace(mwblk, re.sub(r"mov\tw(\d+), #0x1\b[^\n]*\n(\s+[0-9a-f]+:\t[0-9a-f]+ \tcmp\tw3, w\1)", r"mov\tw\1, #0x3\n\2", mwblk), 1)),
        ("memw_ok release takes any value (cbnz -> cbz)", disasm.replace(mwblk, re.sub(r"\tcbnz\tw3,", r"\tcbz\tw3,", mwblk), 1)),
        ("memw_ok bankidx takes any value (cbnz -> cbz on the value)", disasm.replace(mwblk, re.sub(r"\tcbnz\tw0, ([0-9a-f]+) <memw_ok", r"\tcbz\tw0, \1 <memw_ok", mwblk), 1)),
        ("memw_ok bankidx ROM-bank mask changed (0x3e0 -> 0xf0)", disasm.replace(mwblk, re.sub(r"and\tw(\d+), w4, #0x3e0\b", r"and\tw\1, w4, #0xf0", mwblk), 1)),
        ("memw_ok bankidx RAM-bank check dropped (tst #0x1f -> #0x0 form)", disasm.replace(mwblk, re.sub(r"tst\tx4, #0x1f\b", r"tst\tx4, #0x20", mwblk), 1)),
        ("memw_ok bankidx RAM-bank condition inverted (cset ne -> eq)", disasm.replace(mwblk, re.sub(r"cset\tw(\d+), ne\b", r"cset\tw\1, eq", mwblk), 1)),
        ("memw_ok powerctl result inverted (cset eq -> ne)", disasm.replace(mwblk, re.sub(r"cset\tw(\d+), eq\b", r"cset\tw\1, ne", mwblk), 1)),
        ("memw_ok reject path accepts (mov #0x0 -> #0x1)", disasm.replace(mwblk, re.sub(r"mov\tw(\d+), #0x0\b", r"mov\tw\1, #0x1", mwblk), 1)),
        ("memw_ok branch to the SRPWR path inverted (b.eq -> b.ne)", disasm.replace(mwblk, re.sub(r"(cmp\tw0, w5\n\s+[0-9a-f]+:\t[0-9a-f]+ \t)b\.eq", r"\1b.ne", mwblk, count=1), 1)),
        ("memw_ok calls another function", disasm.replace(mwblk, mwblk.replace("ret", "bl\t0 <try_write32>\n\t\t\t1: R_AARCH64_CALL26\ttry_write32\n  1:\td65f03c0 \tret", 1), 1)),
        ("memw_ok became a load", disasm.replace(mwblk, mwblk.replace("cmp\tw1, #0x800", "ldr\tw1, [x1]", 1), 1)),
        ("memw_pred RESET_CTL companion changed (IOCTL 0x408 -> 0x404)", disasm.replace(pblk, re.sub(r"mov\tw(\d+), #0x408\b", r"mov\tw\1, #0x404", pblk), 1)),
        ("memw_pred IOCTL companion changed (RESET_CTL 0x800 -> 0x804)", disasm.replace(pblk, re.sub(r"mov\tw(\d+), #0x800\b", r"mov\tw\1, #0x804", pblk), 1)),
        ("memw_pred bankidx has no companion (cmp #0x10 -> #0x11)", disasm.replace(pblk, re.sub(r"cmp\tw0, #0x10\b", r"cmp\tw0, #0x11", pblk), 1)),
        ("memw_pred bankidx companion becomes the sentinel (csetm ne -> eq)", disasm.replace(pblk, re.sub(r"csetm\tw(\d+), ne", r"csetm\tw\1, eq", pblk), 1)),
        ("memw_pred RESET_CTL offset moved (0x800 -> 0x808)", disasm.replace(pblk, re.sub(r"cmp\tw0, #0x800\b", r"cmp\tw0, #0x808", pblk), 1)),
        ("memw_pred IOCTL offset moved (0x408 -> 0x40c)", disasm.replace(pblk, re.sub(r"cmp\tw0, #0x408\b", r"cmp\tw0, #0x40c", pblk), 1)),
        ("memw_pred calls another function", disasm.replace(pblk, pblk.replace("ret", "bl\t0 <try_write32>\n\t\t\t1: R_AARCH64_CALL26\ttry_write32\n  1:\td65f03c0 \tret", 1), 1)),
        ("w32 does not call memw_pred", disasm.replace(wblk, re.sub(r"bl\t([0-9a-f]+) <memw_pred>", r"nop", wblk), 1)),
        ("w32 reads the BAR0 predecessor through only one r32 call", disasm.replace(wblk, re.sub(r"bl\t([0-9a-f]+) <r32>", r"nop", wblk, count=1), 1)),
        ("w32 reads the BAR0 window select from another register (0x84)", disasm.replace(wblk, re.sub(r"mov\tx(\d+), #0xe080\b", r"mov\tx\1, #0xe084", wblk), 1)),
        ("a second caller of memw_pred", disasm + "0000000000fff200 <evil3>:\n fff200:\t97fffe78 \tbl\t100 <memw_pred>\n fff204:\td65f03c0 \tret\n"),
        ("a second try_write32 in w32", disasm.replace("R_AARCH64_CALL26\ttry_write32", "R_AARCH64_CALL26\ttry_write32\n 1fc:\t94000000 \tbl\t0 <try_write32>\n\t\t\t1fc: R_AARCH64_CALL26\ttry_write32", 1)),
        ("a second caller of w32", disasm + "0000000000fff000 <evil>:\n fff000:\t97fffe78 \tbl\t100 <w32>\n fff004:\td65f03c0 \tret\n"),
    ]
    caught = 0
    for label, bad in cases:
        assert bad != disasm, f"wlanlink object selftest case did not mutate: {label}"
        try:
            check_wlanlink_w32_object_text(bad, "wlanlink selftest")
        except AssertionError as exc:
            caught += 1
            print(f"self-test caught: wlanlink object {label} -> {exc}")
        else:
            raise AssertionError(f"wlanlink object self-test FAILED to catch: {label}")
    print(f"PASS wlanlink w32 object self-test: {caught}/{len(cases)} bad objects rejected")
    return caught


def check_shim_wlan_sequence(src: str, name: str = "shim.c") -> None:
    """shim.c calls wlan_rails(), wlan_smc_read() and wlan_en_run() exactly once each, inside their own flag, in the WLAN section (after wlan_probe_l1, before the level-2 gate), in that order."""
    code = _strip_c_comments(src)
    norm = re.sub(r"\s+", "", code)
    pos = []
    for fn, flag in (("wlan_rails", "WLAN_RAILS"), ("wlan_link_run", "WLAN_LINK_RUN"), ("wlan_smc_read", "WLAN_SMC_READ"), ("wlan_iso_run", "WLAN_ISO_RUN"), ("wlan_en_run", "WLAN_EN_RUN")):
        if len(re.findall(r"\b" + fn + r"\s*\(", code)) != 1 or f"#ifdef{flag}" not in norm or f"(void){fn}();" not in norm:
            raise AssertionError(f"{name}: {fn}() must be called exactly once, as `(void){fn}();` inside #ifdef {flag}")
        i = norm.find(f"(void){fn}();")
        if not norm.rfind(f"#ifdef{flag}", 0, i) > norm.find("wlan_probe_l1();") or not i < norm.find("wlan_probe_l2_open()"):
            raise AssertionError(f"{name}: {fn}() must sit in its own #ifdef {flag} block after wlan_probe_l1() and before the level-2 gate")
        pos.append(i)
    if not re.search(r"#if defined\(WLAN_ISO_RUN\) && defined\(WLAN_EN_RUN\)\n#error", src):
        raise AssertionError(f"{name}: --wlan-iso and --wlan-en must be impossible to combine (#error in shim.c)")
    if not re.search(r"#if defined\(WLAN_LINK_RUN\) && \(defined\(WLAN_ISO_RUN\) \|\| defined\(WLAN_EN_RUN\) \|\| defined\(WLAN_SMC_READ\)\)\n#error", src):
        raise AssertionError(f"{name}: --wlan-link must be impossible to combine with --wlan-iso / --wlan-en / --wlan-smc-read (#error in shim.c)")
    m = re.search(r"#if defined\(WLAN_ISO_RUN\) \|\| defined\(WLAN_LINK_RUN\)\n(?:(?!#endif).)*?l2 skipped(?:(?!#endif).)*?#else\n(.*?)#endif\t/\* !\(WLAN_ISO_RUN \|\| WLAN_LINK_RUN\) \*/", src, re.S)
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
        ("level 2 not skipped in an iso image", shim_src.replace("#if defined(WLAN_ISO_RUN) || defined(WLAN_LINK_RUN)\n\t/* review 2 F2", "#if 0\n\t/* review 2 F2", 1)),
        ("iso and en combinable", shim_src.replace("#if defined(WLAN_ISO_RUN) && defined(WLAN_EN_RUN)\n#error", "#if 0\n#error", 1)),
        ("level-2 SUMMARY outside the skip", shim_src.replace("\twlan_probe_l2_group(4);\t\t\t/* SUMMARY, also when gated */\n#endif\t/* !(WLAN_ISO_RUN || WLAN_LINK_RUN) */", "#endif\t/* !(WLAN_ISO_RUN || WLAN_LINK_RUN) */\n\twlan_probe_l2_group(4);", 1)),
        ("link combinable with iso", shim_src.replace("#if defined(WLAN_LINK_RUN) && (defined(WLAN_ISO_RUN) || defined(WLAN_EN_RUN) || defined(WLAN_SMC_READ))\n#error", "#if 0\n#error", 1)),
        ("a second wlan_link_run call", shim_src.replace("\t(void)wlan_link_run();\n", "\t(void)wlan_link_run();\n\t(void)wlan_link_run();\n", 1)),
        ("wlan_link_run outside its flag", shim_src.replace("#ifdef WLAN_LINK_RUN\n\t/* W3a", "#if 1\n\t/* W3a", 1)),
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
    parser.add_argument("boot_image", type=pathlib.Path, nargs="?")
    parser.add_argument("--wlan-link-effective", type=int, help="build.sh: the effective WLAN_LINK_N of the wlanlink.o it just built (checked against --wlan-link-explicit, then exit)")
    parser.add_argument("--wlan-link-explicit", default="", help="build.sh: the N typed as --wlan-link=N ('' = none)")
    args = parser.parse_args()
    if args.wlan_link_effective is not None:
        check_wlan_link_n(args.wlan_link_effective, args.wlan_link_explicit)
        print(f"PASS wlan-link N: effective N={args.wlan_link_effective} (explicit {args.wlan_link_explicit or 'none: default'})")
        return
    assert args.boot_image, "boot_image required"
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
    wlan_seq_selftest()
    _doc = json.loads((repo_root / "soc/google/gs101/wlan_seq.json").read_text())
    check_wlan_seq_table(_doc, (repo_root / "shim" / "out" / "wlan_seq.h").read_text())
    print(f"PASS wlan_seq table audit: sha256 {_WLAN_SEQ_SHA[:16]} pinned; header == JSON; S00..S36 (S22 = one 0 us delay row, a documented skip); the default run (N=19) never raises PERST or enables the LTSSM; "
          f"{_WLAN_SEQ_COUNTS['write_rows']} write rows on {_WLAN_SEQ_COUNTS['distinct_targets']} distinct registers in 9 blocks (BAR0: 4 registers, written only by the exact S34-S36 tuples), all inside the allowlist; every write row has a restore or is a BAR0 write dropped with the module power (research 40 s.5)")
    wlanlink_source_selftest()
    check_wlanlink_source_text((repo_root / "shim" / "wlanlink.c").read_text())
    wlan_link_n_selftest()
    print("PASS wlan-link N audit: build.sh sets WLAN_LINK_N only from an explicit --wlan-link=N, the default is WLAN_SEQ_DEFAULT_N (19), the first LINK line prints N")
    print("PASS wlanlink source audit: strict writer audit; the single writer w32(idx, tear) reads the row from the const table and re-checks pa/mask/value against the pinned allowlist; "
          "probe-before-touch, UNKNOWN-step refusal before the first write, unconditional teardown, bounded loops, SMC only through wlan_link_iso_set/clear")
    touchprobe_object_selftest(repo_root / "shim" / "out" / "wlanlink.o", _WLANLINK_ALLOWED_UNDEF, "wlanlink")
    n_stores = check_touchprobe_object(repo_root / "shim" / "out" / "wlanlink.o", _WLANLINK_ALLOWED_UNDEF)
    print(f"PASS wlanlink object audit: try_write32 referenced only by a bl inside w32; {n_stores} stores outside w32, all stack/argument based; undef subset")
    wlanlink_object_selftest(repo_root / "shim" / "out" / "wlanlink.o")
    check_wlanlink_w32_object_text(_touch_obj_info(repo_root / "shim" / "out" / "wlanlink.o")[0], "wlanlink.o")
    print("PASS wlanlink w32 object audit: the compiled w32 keeps its window / offset compares, one try_write32, one caller (run_row)")
    wlansmc_object_selftest(repo_root / "shim" / "out" / "wlansmc_link.o", True)
    n_stores = check_wlansmc_object(repo_root / "shim" / "out" / "wlansmc_link.o", True)
    print(f"PASS wlansmc_link object audit: exactly three smc (read, rmw set, rmw clear), constants only; {n_stores} stores, all stack/argument based")
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
