#!/usr/bin/env python3
"""panic_capture: parse the panic log the kernel sends over USB at panic time (patch 0242, PXPANIC frames in usbnet.log) into PANIC: lines; called by tools/xrun_report.py

  tools/panic_capture.py RUN_DIR      print the PANIC:/PANIC GAP: lines of one run dir (reads RUN_DIR/usbnet.log, as if a panic was seen)
  tools/panic_capture.py --self-test  inline checks (also run by tools/test_panic_capture.py)
Last line: RESULT: PASS|FAIL panic_capture ...

Input: the lines of usbnet.log (tools/usbnet-log.py, epoch prefix already stripped). The panicking CPU (osfmk/kern/debug.c
sweet_panic_usb, patch 0242) sends to 192.168.7.1:5140, the live-log port, so its datagrams land in the same file as text:
  PXPANIC usb begin sid=S frames=N bytes=B
  PXPANIC frame I/N sid=S[ cont]      then the frame's lines; "cont": the kernel cut the frame inside a line and added a newline
                                      after it, so its last line joins the first line of frame I+1
  PXPANIC usb end sid=S sent=K/N
The reassembled text is "PXPANIC full sid=S cap=C lost=L tail=T\\n" + the panic log (debug_putc from the panic(cpu line on) +
"\\n--- PXPANIC console-log tail (T bytes) ---\\n" + the raw console-ring tail. The kernel text is used verbatim: the panic string
is its own `panic(cpu N caller 0x...): ...` line, pc/lr/sp/fp/esr/far come from the register dump inside the panic string
(exception panics only), the backtrace from its `lr: 0x... fp: 0x...` lines. Nothing is inferred; a missing piece is a
`PANIC GAP:` line (rule 8). The kernel's own refusals are its `SWEET PANIC USB GAP:` lines (hold screen only, nothing is sent)."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import os, re, sys

BEGIN = re.compile(r"^PXPANIC usb begin sid=([0-9a-f]{8}) frames=(\d+) bytes=(\d+)\s*$")
FRAME = re.compile(r"^PXPANIC frame (\d+)/(\d+) sid=([0-9a-f]{8})( cont)?\s*$")
END = re.compile(r"^PXPANIC usb end sid=([0-9a-f]{8}) sent=(\d+)/(\d+)\s*$")
HDR = re.compile(r"^PXPANIC full sid=([0-9a-f]{8}) cap=(\d+) lost=(\d+) tail=(\d+)\n")
TAIL = re.compile(r"\n--- PXPANIC console-log tail \((\d+) bytes\) ---\n")
TAIL_LINE = "--- PXPANIC console-log tail ("
REG = {k: re.compile(r"(?<![0-9a-zA-Z_])%s:\s*(0x[0-9a-fA-F]+)" % k) for k in ("pc", "lr", "sp", "fp", "esr", "far")}
BT = re.compile(r"^\s*lr:\s*(0x[0-9a-fA-F]+)\s+fp:\s*(0x[0-9a-fA-F]+)\s*$", re.M)
PANIC_LINE = re.compile(r"^panic\(cpu \d+ caller (0x[0-9a-fA-F]+)\): ?(.*)$", re.M)


def cut(s, n):
    s = s.replace("\t", " ").rstrip()
    return s if len(s) <= n else s[: n - 3] + "..."


def _cap(caps, sid, n):
    return caps.setdefault(sid, {"sid": sid, "frames": {}, "n": n, "bytes": None, "end": None, "begin": False})


def split(lines):
    """usbnet lines -> (keep, caps). keep: [(index of the usbnet line it came from, text)] for the run log: every line outside a
    capture, plus each capture's panic log rejoined into whole lines (at the index of its first frame line); the PXPANIC control
    lines and the console-log tail (it repeats lines the live log already carried) are left out. caps: {sid: capture dict}."""
    caps, keep, cur, fr_no = {}, [], None, None
    for i, ln in enumerate(lines):
        m = BEGIN.match(ln)
        if m:
            cur = _cap(caps, m.group(1), int(m.group(2)))
            cur["begin"], cur["bytes"], fr_no = True, int(m.group(3)), None
            continue
        m = FRAME.match(ln)
        if m:
            cur = _cap(caps, m.group(3), int(m.group(2)))
            fr_no = int(m.group(1))
            cur["frames"].setdefault(fr_no, {"lines": [], "cont": bool(m.group(4))})
            if cur.get("at") is None:
                cur["at"] = i
                keep.append((i, cur))       # placeholder: replaced by the rejoined panic log below
            continue
        m = END.match(ln)
        if m:
            _cap(caps, m.group(1), int(m.group(3)))["end"] = (int(m.group(2)), int(m.group(3)))
            cur, fr_no = None, None
            continue
        if cur is not None and fr_no is not None:
            cur["frames"][fr_no]["lines"].append(ln)
            continue
        keep.append((i, ln))
    for c in caps.values():
        c["text"], c["missing"] = assemble(c)
    out = []
    for i, x in keep:
        if isinstance(x, str):
            out.append((i, x))
            continue
        body = x["text"]
        h = HDR.match(body)
        body = body[h.end():] if h else body
        t = TAIL.search(body)
        body = body[: t.start()] if t else body
        out += [(i, l) for l in body.split("\n")]
    return out, caps


def assemble(c):
    """frames -> (text, [missing frame numbers]); a missing frame is the line [[PXPANIC missing frame i/n]]."""
    parts, missing = [], []
    for i in range(c["n"]):
        fr = c["frames"].get(i)
        if fr is None:
            missing.append(i)
            parts.append("[[PXPANIC missing frame %d/%d]]\n" % (i, c["n"]))
            continue
        body = "\n".join(fr["lines"])
        parts.append(body if fr["cont"] else body + "\n")
    return "".join(parts), missing


def parse(text):
    """Reassembled text -> dict (kernel text verbatim)."""
    m = HDR.match(text)
    body = text[m.end():] if m else text
    tl = TAIL.search(body)
    cap, tail = (body[: tl.start()], body[tl.end():]) if tl else (body, "")
    r = {"cap": int(m.group(2)) if m else 0, "lost": int(m.group(3)) if m else 0, "tail_bytes": int(m.group(4)) if m else 0,
         "has_hdr": bool(m), "has_tail": bool(tl)}
    pl = PANIC_LINE.search(cap)
    r["string"], r["caller"] = (pl.group(0), pl.group(1)) if pl else (None, None)
    # registers only from the panic string block: from the panic line to the first blank line / "Debugger message" / missing frame
    blk = cap[pl.start():] if pl else ""
    e = re.search(r"\n\s*\n|\nDebugger message|\n\[\[PXPANIC missing", blk)
    blk = blk[: e.start()] if e else blk
    r["regs"] = {k: (rx.search(blk).group(1) if rx.search(blk) else None) for k, rx in REG.items()}
    r["bt"] = BT.findall(cap)
    r["tail"] = [l for l in tail.splitlines() if l.strip() and not l.startswith("[[PXPANIC missing")]
    return r


def lines(caps, panic_seen=False, usb_kernel=None):
    """PANIC: lines for report.txt from split()'s caps. panic_seen: the run shows a panic (marker, test panic fired);
    usb_kernel: the boot log has the patch 0242 line (True/False/None = unknown). No capture and no panic seen -> []."""
    if not caps:
        if not panic_seen:
            return []
        why = ("the kernel has patch 0242 (SWEET FAULT USB line) but no PXPANIC datagram arrived (its SWEET PANIC USB GAP line on the "
               "hold screen says why)" if usb_kernel else
               "the boot log has no SWEET FAULT USB line (kernel without patch 0242, or the line was not captured)" if usb_kernel is False else
               "no PXPANIC lines in usbnet.log")
        return ["PANIC GAP: panic seen but no panic capture: %s" % why]
    c = sorted(caps.values(), key=lambda c: len(c["missing"]))[0]     # fewest missing frames
    b = parse(c["text"])
    st = "complete" if not c["missing"] else "missing_frames=" + ",".join(map(str, c["missing"]))
    end = ("end sent=%d/%d" % c["end"]) if c["end"] else "no end line"
    out = ["PANIC: source=usbnet sid=%s frames=%d cap=%d lost=%d tail=%d %s, %s" % (c["sid"], c["n"], b["cap"], b["lost"], b["tail_bytes"], st, end)]
    if len(caps) > 1:
        out.append("PANIC: %d captures in the log (sids %s); shown: %s" % (len(caps), ",".join(caps), c["sid"]))
    if not c["begin"]:
        out.append("PANIC GAP: no begin line for sid %s" % c["sid"])
    if c["end"] is None or c["end"][0] != c["end"][1]:
        out.append("PANIC GAP: the kernel did not report all %d frames sent (%s)" % (c["n"], end))
    if not b["has_hdr"]:
        out.append("PANIC GAP: no PXPANIC full header in the reassembled text (frame 0 missing?)")
    s = b["string"]
    out.append(("PANIC: string " + cut(s, 300)) if s else "PANIC GAP: no panic(cpu line in the capture")
    rg = " ".join("%s=%s" % (k, b["regs"][k]) for k in ("pc", "lr", "sp", "fp", "esr", "far") if b["regs"].get(k))
    out.append(("PANIC: regs " + rg) if rg else
               "PANIC: regs none in the panic string (a panic() call, not an exception)" + ("; caller=" + b["caller"] if b["caller"] else ""))
    if b["bt"]:
        out.append("PANIC: backtrace %d frames (lr): %s" % (len(b["bt"]), " ".join(a for a, _ in b["bt"][:24])))
    else:
        out.append("PANIC GAP: no backtrace lines (lr: fp:) in the capture%s" % (" (frames missing)" if c["missing"] else ""))
    if b["has_tail"]:
        out += ["PANIC: tail " + cut(l, 200) for l in b["tail"][-12:]] or ["PANIC GAP: console-log tail empty in the capture"]
    elif b["tail_bytes"]:
        out.append("PANIC GAP: console-log tail (%d bytes) announced but its marker line was not received" % b["tail_bytes"])
    if b["lost"]:
        out.append("PANIC GAP: %d bytes of the panic log did not fit the 8 KiB capture (patch 0242 SWEET_PANIC_CAP)" % b["lost"])
    return out


# ---- self-test: the kernel's framing reproduced in Python (same rule as sweet_panic_usb_chunk in patch 0242)
FIX_CAP = ("panic(cpu 0 caller 0xfffffe0012345678): Invalid kernel stack pointer (probable overflow). at pc 0xfffffe0011112222, lr 0xfffffe0011113333 (saved state: 0xfffffe1000aaa000)\n"
           "\t  x0:  0x0000000000000001 x1:  0x0000000000000002\n"
           "\t  fp:  0xfffffe1000abc000 lr:  0xfffffe0011113333 sp:  0xfffffe1000abbf00\n"
           "\t  pc:  0xfffffe0011112222 cpsr: 0x604013c5         esr: 0x96000047  far: 0xfffffe1000abbef0\n"
           "\n"
           "Debugger message: panic\n"
           "Panicked thread: 0xfffffe0011aa0000, backtrace: 0xfffffe1000abb000, tid: 101\n"
           "\t\t  lr: 0xfffffe0010000100  fp: 0xfffffe1000abb010\n"
           "\t\t  lr: 0xfffffe0010000200  fp: 0xfffffe1000abb020\n")
FIX_TAIL = "[  610.700] wlan: eapol rx 4/4\n[  610.701] DP15 ring 3 full\n"


def chunk(s, at, mx):
    """Kernel rule: the rest when it fits; else up to mx bytes ending at the last newline inside them; no newline -> mx bytes, cont."""
    rest = len(s) - at
    if rest <= mx:
        return rest, not s.endswith("\n")
    nl = s.rfind("\n", at, at + mx)
    return (nl + 1 - at, False) if nl >= 0 else (mx, True)


def datagrams(text, sid="0badf00d", mx=1200):
    """The datagrams the kernel sends for one capture text."""
    fr, at = [], 0
    while at < len(text):
        n, cont = chunk(text, at, mx)
        fr.append((text[at:at + n], cont))
        at += n
    out = ["\nPXPANIC usb begin sid=%s frames=%d bytes=%d\n" % (sid, len(fr), len(text))]
    for i, (c, cont) in enumerate(fr):
        out.append("PXPANIC frame %d/%d sid=%s%s\n%s%s" % (i, len(fr), sid, " cont" if cont else "", c, "\n" if cont else ""))
    out.append("PXPANIC usb end sid=%s sent=%d/%d\n" % (sid, len(fr), len(fr)))
    return out


def receive(dgrams, before=""):
    """What tools/usbnet-log.py writes (one line per console line, partial lines joined across datagrams), epoch dropped."""
    ls = (before + "".join(dgrams)).split("\n")
    return ls[:-1] + ([ls[-1]] if ls[-1] else [])


def full_text(cap, tail, sid="0badf00d", lost=0):
    return "PXPANIC full sid=%s cap=%d lost=%d tail=%d\n%s\n--- PXPANIC console-log tail (%d bytes) ---\n%s" % (
        sid, len(cap), lost, len(tail), cap, len(tail), tail)


def self_test():
    fails = []
    want = ("PANIC: string panic(cpu 0 caller 0xfffffe0012345678): Invalid kernel stack pointer",
            "PANIC: regs pc=0xfffffe0011112222 lr=0xfffffe0011113333 sp=0xfffffe1000abbf00 fp=0xfffffe1000abc000 esr=0x96000047 far=0xfffffe1000abbef0",
            "PANIC: backtrace 2 frames (lr): 0xfffffe0010000100 0xfffffe0010000200",
            "PANIC: tail [  610.701] DP15 ring 3 full", " complete, end sent=")
    text = full_text(FIX_CAP, FIX_TAIL)
    # 1. whole capture after a live-log line cut mid-line; small frames force line cuts and mid-line (cont) cuts
    for mx in (1200, 100, 37):
        ul = receive(datagrams(text, mx=mx), before="[  610.6] live line\n[  610.7] half a li")
        keep, caps = split(ul)
        got = "\n".join(lines(caps, panic_seen=True))
        for w in want:
            if w not in got:
                fails.append("mx=%d: missing %r in %r" % (mx, w, got))
        if not caps or caps["0badf00d"]["text"] != text:
            fails.append("mx=%d: reassembly differs from the kernel text" % mx)
        kept = [t for _, t in keep]
        if any(x.startswith("PXPANIC") for x in kept) or "[  610.701] DP15 ring 3 full" in kept:
            fails.append("mx=%d: control or tail lines left in the run log: %r" % (mx, kept))
        if "[  610.7] half a li" not in kept or "\n".join(kept[2:]).rstrip("\n") != FIX_CAP.rstrip("\n"):
            fails.append("mx=%d: live line or the whole-line panic text missing from the run log: %r" % (mx, kept))
    # 2. mutant: one frame lost -> said so, regs before it still read, no claim of completeness
    dg = datagrams(text, mx=200)
    _, caps = split(receive(dg[:-2] + dg[-1:]))     # the last data frame lost
    got = "\n".join(lines(caps, panic_seen=True))
    if "missing_frames=%d" % (len(dg) - 3) not in got or "pc=0xfffffe0011112222" not in got or " complete" in got:
        fails.append("lost frame: %r" % got)
    # 3. mutant: the end line says fewer frames sent (the kernel stopped on a TX timeout)
    dg = datagrams(text)
    dg[-1] = dg[-1].replace("sent=1/1", "sent=0/1")
    _, caps = split(receive(dg))
    if "PANIC GAP: the kernel did not report all 1 frames sent" not in "\n".join(lines(caps, panic_seen=True)):
        fails.append("short end line not reported")
    # 4. plain panic() call: no regs claimed, caller given
    _, caps = split(receive(datagrams(full_text("panic(cpu 2 caller 0xfffffe0010abcdef): SWEET PANIC TEST: sweetpanic=20 elapsed\n", ""))))
    got = "\n".join(lines(caps, panic_seen=True))
    if "regs none" not in got or "caller=0xfffffe0010abcdef" not in got or "PANIC GAP: no backtrace" not in got:
        fails.append("plain: %r" % got)
    # 5. rule 8: panic seen, nothing received -> a GAP line; no panic -> nothing
    if not lines({}, panic_seen=True, usb_kernel=True)[0].startswith("PANIC GAP: panic seen but no panic capture: the kernel has patch 0242"):
        fails.append("gap line")
    if lines({}, panic_seen=False):
        fails.append("no panic must print nothing")
    # 6. capture overflow in the header -> GAP line
    _, caps = split(receive(datagrams(full_text(FIX_CAP, FIX_TAIL, lost=77))))
    if "PANIC GAP: 77 bytes of the panic log did not fit" not in "\n".join(lines(caps)):
        fails.append("lost bytes not reported")
    for f in fails:
        print("FAIL:", f)
    print("RESULT: %s panic_capture self-test (%d checks failed)" % ("FAIL" if fails else "PASS", len(fails)))
    return 1 if fails else 0


def main(argv):
    if argv[:1] == ["--self-test"]:
        return self_test()
    if len(argv) != 1:
        print(__doc__)
        return 2
    p = os.path.join(argv[0], "usbnet.log")
    try:
        raw = open(p, encoding="latin-1").read().split("\n")
    except OSError as e:
        print("RESULT: FAIL panic_capture cannot read %s: %s" % (p, e))
        return 1
    _, caps = split([x.split("\t", 1)[1] if "\t" in x else x for x in raw if x])
    out = lines(caps, panic_seen=True)
    print("\n".join(out))
    ok = bool(caps) and not any(x.startswith("PANIC GAP") for x in out)
    print("RESULT: %s panic_capture %s" % ("PASS" if ok else "FAIL", argv[0]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
