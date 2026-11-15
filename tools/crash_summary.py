#!/usr/bin/env python3
"""crash_summary: one CRASH: block at the top of report.txt whenever a run died (user 2026-11-09: "auto panic recovery is a must and it must
provide all the info needed, I can't be there manually resetting the phone").

  crash_summary.lines(run)        -> [str]  (run = run.json dict with "dir"); [] when the run did not crash
  tools/crash_summary.py RUNDIR   print the block for an existing run dir (no phone)

What it says, each from the run's own files:
  CRASH: cause=panic|stall|watchdog-reset|reset-unexplained|fail-pattern ... one line naming the evidence
  CRASH: string <panic string>                                  (patch 0242 capture over USB, tools/panic_capture.py)
  CRASH: slide 0x...; frame i <addr> <function+off>              (panicked thread backtrace, symbolized with the run's kernel)
  CRASH: core N pc <function+off> lr <function+off>              (the other CPUs' last pc/lr, `CORE n: PC=..., LR=...` lines)
  CRASH: last log +Ns: <line>                                    (the last USB-log lines before the phone went silent)
  CRASH: phone back as <id> at +Ns; xrun parked it: <RESULT line>
Symbols: llvm-symbolizer on run.json "kernel" (keepsyms builds carry the symbol table); static address = runtime - Kernel slide
(osfmk/kern/debug.c panic_display_kernel_aslr, non-fileset: vm_kernel_slide). A missing tool/kernel/slide is a CRASH GAP line, never silence.
Stdlib only (+ tools/panic_capture.py)."""
import glob, json, os, re, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panic_capture  # noqa: E402

SLIDE = re.compile(r"Kernel slide:\s+(0x[0-9a-fA-F]+)")
TEXTBASE = re.compile(r"Kernel text base:\s+0x[0-9a-fA-F]+")
CORE = re.compile(r"CORE (\d+): PC=(0x[0-9a-fA-F]+), LR=(0x[0-9a-fA-F]+)")
CORE_PANICKED = re.compile(r"CORE (\d+) is the one that panicked")
STALL = re.compile(r"SWEET STALL: [^\n]*")
WDT_ARMED = re.compile(r"SWEET WDT: armed ")
WDT_STOP = re.compile(r"SWEET WDT: test: petting stopped")
RESET_LINES = re.compile(r"SWEET RESTART: resetting now|SWEET REMOTE: reset|SWEET-RESET-BL")
PARK = re.compile(r"RESULT: \w+ state=\S+ target=\S+.*")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):   # lint-silent: expected a run.json key that is absent or not a number: the block prints "?" for it
        return None


def usb_lines(d):
    """[(epoch, line)] of usbnet.log ("<epoch>\\t<line>"); [] when absent."""
    out = []
    try:
        with open(os.path.join(d, "usbnet.log"), encoding="latin-1") as f:
            for raw in f:
                t, tab, ln = raw.rstrip("\n").partition("\t")
                if tab:
                    out.append((_f(t), ln))
    except FileNotFoundError:   # lint-silent: expected a run without the USB log (QR only); the block says so below
        pass
    return out


def symbolizer():
    p = shutil.which("llvm-symbolizer")
    if p:
        return p
    found = sorted(glob.glob("/usr/lib/llvm-*/bin/llvm-symbolizer"))
    return found[-1] if found else None


def symbolize(kernel, slide, addrs, ret=()):
    """{runtime addr: 'func+0xoff'} for addrs; ({}, gap text) when it cannot. Addresses in `ret` are return addresses (backtrace lr,
    panic caller, LR): they are looked up at addr - 4, the call instruction, because a call to a noreturn function (panic) is the last
    instruction of its function and the return address is the NEXT function's first byte (ar-b4: caller = qrcodegen_encodeText+0x0,
    really stall_check). The printed offset is the call's."""
    tool = symbolizer()
    if not tool:
        return {}, "llvm-symbolizer not found (apt llvm): addresses stay raw"
    if not kernel or not os.path.exists(kernel):
        return {}, "kernel %s not on this host: addresses stay raw" % kernel
    if slide is None:
        return {}, "no `Kernel slide:` line in the capture: addresses stay raw"
    stat = [(a, int(a, 16) - slide - (4 if a in ret else 0)) for a in addrs]
    try:
        r = subprocess.run([tool, "--obj=" + kernel, "--functions=linkage", "--output-style=JSON"] + ["0x%x" % s for _, s in stat],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {}, "llvm-symbolizer failed (%s): addresses stay raw" % e
    out = {}
    rows = []
    for x in r.stdout.splitlines():     # llvm-symbolizer 19: one JSON value per address, a list holding one object
        x = x.strip()
        if x.startswith(("[", "{")):
            v = json.loads(x)
            rows += v if isinstance(v, list) else [v]
    for (a, s), row in zip(stat, rows):
        sym = (row.get("Symbol") or [{}])[0]
        fn = sym.get("FunctionName") or ""
        start = sym.get("StartAddress")
        if fn and fn != "??":
            off = ("+0x%x" % (s - int(start, 16))) if isinstance(start, str) and start.startswith("0x") else ""
            out[a] = fn + off
    return out, None


def _park(d):
    try:
        found = PARK.findall(open(os.path.join(d, "xrun.log"), errors="replace").read())
    except FileNotFoundError:   # lint-silent: expected a dry-run copy without xrun.log; the line then says unknown
        return None
    return found[-1] if found else None


def lines(run):
    d = run.get("dir")
    if not d or not os.path.isdir(d):
        return []
    try:   # the report's run dict is rewritten (usb_back_id, early ... dropped): the raw run.json is the source, the caller's keys fill gaps
        raw = json.load(open(os.path.join(d, "run.json")))
        run = dict(run, **raw)
    except (OSError, ValueError) as e:
        return ["CRASH GAP: run.json of %s not readable (%s): no crash summary" % (d, e)]
    ul = usb_lines(d)
    _, caps = panic_capture.split([l for _, l in ul])
    t0 = _f(run.get("t_boot"))
    log = "\n".join(l for _, l in ul)
    back = str(run.get("usb_back_id") or "")
    host_reset = bool(run.get("host_end") or run.get("early")) and not run.get("early_fail")
    cap = sorted(caps.values(), key=lambda c: len(c["missing"]))[0] if caps else None
    text = cap["text"] if cap else ""
    b = panic_capture.parse(text) if cap else None
    stall = STALL.search(text) or (STALL.search(b["string"] or "") if b else None)
    if cap and stall:
        cause = "cause=stall (patch 0279 detector: %s)" % panic_capture.cut(stall.group(0), 160)
    elif cap:
        cause = "cause=panic (capture sid %s over USB)" % cap["sid"]
    elif run.get("early_fail"):
        cause = "cause=fail-pattern (%s): the run was ended by the host on that line" % panic_capture.cut(str(run["early_fail"]), 120)
    elif back.endswith(":d001") and not host_reset:
        wdt = bool(WDT_ARMED.search(log))
        cause = ("cause=watchdog-reset (SoC watchdog armed, %s, no panic text, no host reset)" % ("petting stopped on purpose" if WDT_STOP.search(log) else "log went silent")
                 if wdt else "cause=reset-unexplained (landed in recovery, no panic capture, no host reset, watchdog not armed)")
    elif back and not host_reset and not RESET_LINES.search(log) and not run.get("restart"):
        cause = "cause=reset-unexplained (back as %s without a reset the host or the kernel announced)" % back
    else:
        return []
    out = ["CRASH: " + cause]
    if b:
        out.append("CRASH: string " + panic_capture.cut(b["string"] or "(no panic(cpu line)", 300))
        sm = SLIDE.search(text)
        # no `Kernel slide:` line but a `Kernel text base:` line = slide 0 (osfmk/kern/debug.c panic_display_kernel_aslr, non-fileset branch)
        slide = int(sm.group(1), 16) if sm else (0 if TEXTBASE.search(text) else None)
        cores = CORE.findall(text)
        addrs = [a for a, _ in b["bt"]] + [x for _, pc, lr in cores for x in (pc, lr)] + ([b["caller"]] if b["caller"] else [])
        ret = {a for a, _ in b["bt"]} | {lr for _, _, lr in cores} | ({b["caller"]} if b["caller"] else set())
        names, gap = symbolize(run.get("kernel"), slide, addrs, ret)
        if gap:
            out.append("CRASH GAP: " + gap)
        nm = lambda a: "%s %s" % (a, names.get(a, "?"))
        if b["caller"]:
            out.append("CRASH: caller " + nm(b["caller"]))
        out.append("CRASH: slide %s; panicked thread backtrace %d frames (return addresses symbolized at -4)" % (sm.group(1) if sm else ("0 (text base line)" if slide == 0 else "?"), len(b["bt"])))
        out += ["CRASH: frame %d %s" % (i, nm(a)) for i, (a, _) in enumerate(b["bt"][:16])]
        pan = CORE_PANICKED.search(text)
        out += ["CRASH: core %s pc %s lr %s" % (c, nm(pc), nm(lr)) for c, pc, lr in cores]
        if pan:
            out.append("CRASH: core %s is the one that panicked" % pan.group(1))
        if not cores:
            out.append("CRASH GAP: no `CORE n: PC=` lines in the capture (kernel without the 0242 noregion backtrace, or frames missing)")
    first = next((i for i, (_, l) in enumerate(ul) if l.lstrip().startswith("PXPANIC")), len(ul))   # the live log ends where the panic capture starts
    live = [(t, l) for t, l in ul[:first] if t is not None]
    for t, l in live[-6:]:
        out.append("CRASH: last log %s: %s" % (("+%ds" % (t - t0)) if t0 else "?", panic_capture.cut(l, 160)))
    if not live:
        out.append("CRASH GAP: no USB log lines in this run (the kernel never reached the USB log, or no usbnet.log)")
    bt = _f(run.get("usb_back"))
    park = _park(d)
    out.append("CRASH: phone back as %s%s; parked: %s" % (back or "?", (" at +%ds" % (bt - t0)) if bt and t0 else "", park or "unknown (no xrun.log line)"))
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: crash_summary.py RUNDIR")
    r = json.load(open(os.path.join(sys.argv[1], "run.json")))
    r["dir"] = sys.argv[1]
    print("\n".join(lines(r)) or "CRASH: none (the run did not crash)")
