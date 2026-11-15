#!/usr/bin/env python3
"""ucrash_summary: a USERLAND CRASHES block for every run report (user 2026-11-11: "every task must first check what data is needed from the
phone to analyze the stack trace or errors; we miss a lot because of trial and error without knowing the error").

  ucrash_summary.lines(run)      -> [str]  (run = run.json dict with "dir"); [] when the log has no userland crash / corpse / jetsam line
  tools/ucrash_summary.py RUNDIR print the block for an existing run dir (no phone)

Input: the run's USB log (usbnet.log, else merged.log). The kernel prints these lines per crashed process (patches 0043, 0044, 0142):
  CRASH name[pid] sig N code 0x.. sub 0x.. slide 0x.. mh 0x..    | CRASH name pc/lr/sp/fp/far 0x..    | CRASH name x0 ..  | CRASH name sp+0 ..
  CRASH name bt 0x.. (up to 10 frames)                           | CRASH name reason ns N code 0x..: text
Crashes are grouped by (process, signal, reason); the block shows the count per group and the FULL report of the first crash of each group.
Decode: reason ns 2 is the signal namespace (observed: `sig 6` crashes carry `ns 2 code 0x6`); the signal name is Darwin sys/signal.h.
Other namespaces are printed raw (no constant table in the repo to cite). Frames: address - slide inside 0x180000000..0x300000000 is the shared
cache -> `ipsw dyld a2s` (PIXOS_IPSW, PIXOS_DSC; cached in-process and in PIXOS_UCRASH_CACHE json); address inside the main image (mh ..
mh+0x10000000) -> offset mh-relative + 0x100000000 (the binary's vmaddr, as tools/sweet-crash.py). Anything else, or a missing tool, is an
explicit `?0x..` frame plus one `UCRASH GAP:` line, never a dropped frame.
Also counted: `Failed to send exception EXC_CORPSE_NOTIFY. error code: N` lines and SWEET JETSAM kill lines (patches 0127, 0178).
Stdlib only."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import concurrent.futures, json, os, re, subprocess, sys

HOME = os.path.expanduser("~")
CACHE_LO, CACHE_HI = 0x180000000, 0x300000000
MAIN_SPAN, MAIN_VMADDR = 0x10000000, 0x100000000
MAX_GROUPS = 8
SIGNALS = {1: "SIGHUP", 2: "SIGINT", 3: "SIGQUIT", 4: "SIGILL", 5: "SIGTRAP", 6: "SIGABRT", 7: "SIGEMT", 8: "SIGFPE", 9: "SIGKILL",
           10: "SIGBUS", 11: "SIGSEGV", 12: "SIGSYS", 13: "SIGPIPE", 14: "SIGALRM", 15: "SIGTERM"}   # Darwin sys/signal.h
PREFIX = re.compile(r"^\d+(?:\.\d+)?\t")
HEAD = re.compile(r"CRASH (\S+?)\[(\d+)\] sig (\d+) code (0x[0-9a-f]+) sub (0x[0-9a-f]+) slide (0x[0-9a-f]+) mh (0x[0-9a-f]+)")
BODY = re.compile(r"CRASH (\S+) (pc|x\d+|sp\+\d+|bt|reason) (.*)")
REASON = re.compile(r"ns (\d+) code (0x[0-9a-f]+): ?(.*)")
CORPSE = re.compile(r"Failed to send exception EXC_CORPSE_NOTIFY\. error code: (\d+)")
JKILL = re.compile(r"SWEET JETSAM: (\S+?)\[(\d+)\] (?:kill state|per-process limit exceeded)")
HEX = re.compile(r"0x[0-9a-f]+")
A2S_LINE = re.compile(r"^(0x[0-9a-f]+): (.+)$")
DYLIB = re.compile(r"dylib=(\S+)")


def ipsw_path():
    return os.environ.get("PIXOS_IPSW", HOME + "/pixelios-artifacts/tools/ipsw")


def dsc_path():
    return os.environ.get("PIXOS_DSC", HOME + "/pixelios-artifacts/work/dsc/cache/dyld_shared_cache_arm64")


def read_log(rundir):
    for n in ("usbnet.log", "merged.log"):
        p = os.path.join(rundir, n)
        if os.path.isfile(p):
            with open(p, errors="replace") as f:
                return [PREFIX.sub("", l.rstrip("\n")) for l in f]
    return None


def parse(lines):
    """-> (crashes, corpse {code: n}, jetsam {name: n}); a crash = dict(name pid sig code sub slide mh regs{} bt[] ns rcode text)"""
    crashes, open_by_name, corpse, jetsam = [], {}, {}, {}
    for l in lines:
        m = HEAD.search(l)
        if m:
            c = dict(name=m.group(1), pid=int(m.group(2)), sig=int(m.group(3)), code=m.group(4), sub=m.group(5),
                     slide=int(m.group(6), 16), mh=int(m.group(7), 16), regs={}, bt=[], ns=None, rcode=None, text="")
            crashes.append(c)
            open_by_name[c["name"]] = c
            continue
        m = BODY.search(l)
        if m and m.group(1) in open_by_name:
            absorb(open_by_name[m.group(1)], m.group(2), m.group(3))
            continue
        m = CORPSE.search(l)
        if m:
            corpse[m.group(1)] = corpse.get(m.group(1), 0) + 1
            continue
        m = JKILL.search(l)
        if m and "kill state" in l:   # one kill prints two lines (limit exceeded + kill state): count the kill-state line
            jetsam[m.group(1)] = jetsam.get(m.group(1), 0) + 1
    return crashes, corpse, jetsam


def absorb(c, kind, rest):
    if kind == "bt":
        c["bt"] = HEX.findall(rest)
    elif kind == "reason":
        m = REASON.match(rest)
        if m:
            c["ns"], c["rcode"], c["text"] = int(m.group(1)), m.group(2), m.group(3)
    elif kind == "pc":
        pairs = re.findall(r"(\w+) (0x[0-9a-f]+)", rest)
        c["regs"].update({"pc": int(rest.split()[0], 16)})
        c["regs"].update({k: int(v, 16) for k, v in pairs})
    else:
        c["regs"][kind] = HEX.findall(rest)


def reason_text(c):
    if c["ns"] is None:
        return "no reason line"
    if c["ns"] == 2:
        try:
            sn = SIGNALS.get(int(c["rcode"], 16), "signal %d" % int(c["rcode"], 16))
        except ValueError:   # lint-silent: expected rcode is always 0x.. by the REASON regex
            sn = "?"
        return "reason ns 2 (signal namespace) %s: %s" % (sn, c["text"] or "(none)")
    return "reason ns %d code %s: %s" % (c["ns"], c["rcode"], c["text"] or "(none)")


def group_key(c):
    return (c["name"], c["sig"], c["ns"], c["rcode"], c["text"] if c["ns"] not in (None, 2) else "")


def load_cache():
    p = os.environ.get("PIXOS_UCRASH_CACHE", HOME + "/pixelios-artifacts/work/ucrash-a2s.json")
    try:
        with open(p) as f:
            return p, json.load(f)
    except (OSError, ValueError):   # lint-silent: expected first use or damaged cache: start empty, a2s refills it
        return p, {}


def save_cache(p, d):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(d, f)
    except OSError as e:
        return "UCRASH GAP: a2s cache %s not written: %s" % (p, e)
    return None


def a2s_batch(addrs, gaps):
    """unslid addresses -> {addr: 'sym + off [dylib]'}; failures add a GAP line and leave the address out"""
    ipsw, dsc = ipsw_path(), dsc_path()
    if not (os.path.isfile(ipsw) or any(os.access(os.path.join(d, ipsw), os.X_OK) for d in os.environ.get("PATH", "").split(":"))):
        gaps.append("UCRASH GAP: ipsw not found (%s; set PIXOS_IPSW): shared-cache frames are not symbolicated" % ipsw)
        return {}
    if not os.path.isfile(dsc):
        gaps.append("UCRASH GAP: dyld shared cache not found (%s; set PIXOS_DSC): shared-cache frames are not symbolicated" % dsc)
        return {}
    a2c = os.path.join(os.path.dirname(dsc), "a2s.cache")
    flags = ["--cache", a2c] if os.path.isfile(a2c) else []

    def one(a):   # `ipsw dyld a2s` takes exactly ONE address (extra arguments are ignored without a word): one call per address
        try:
            r = subprocess.run([ipsw, "dyld", "a2s", dsc] + flags + ["0x%x" % a], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            return a, None, "%s: %s" % (type(e).__name__, e)
        sym, dylib = None, ""
        for l in (r.stdout + "\n" + r.stderr).splitlines():
            m = DYLIB.search(l)
            if m:
                dylib = os.path.basename(m.group(1))
            m = A2S_LINE.match(l.strip())
            if m and int(m.group(1), 16) == a:
                sym = m.group(2) + ((" [%s]" % dylib) if dylib else "")
        why = None if sym else "exit %d: %s" % (r.returncode, (r.stderr.strip().splitlines() or ["no symbol printed"])[-1][:100])
        return a, sym, why

    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for a, sym, why in ex.map(one, addrs):
            if sym:
                out[a] = sym
            else:
                gaps.append("UCRASH GAP: ipsw dyld a2s 0x%x failed: %s" % (a, why))
    return out


class Sym:
    """frame symbolication with an in-process + json cache; every frame returns a string, unresolved ones start with '?'"""

    def __init__(self):
        self.path, self.cache = load_cache()
        self.gaps, self.dirty, self.unres, self.lacking = [], False, {}, {}

    def prepare(self, c, addrs):
        need = []
        for a in addrs:
            u = a - c["slide"]
            if CACHE_LO <= u < CACHE_HI and ("%x" % u) not in self.cache and u not in need:
                need.append(u)
        if need:
            got = a2s_batch(need, self.gaps)
            for u, s in got.items():
                self.cache["%x" % u] = s
                self.dirty = True

    def frame(self, c, a):
        u = a - c["slide"]
        if CACHE_LO <= u < CACHE_HI:
            s = self.cache.get("%x" % u)
            if s:
                return "0x%x = cache 0x%x %s" % (a, u, s)
            self.unres.setdefault("unresolved by a2s (reason in the a2s GAP lines, or ipsw missing)", []).append(a)
            return "?0x%x = cache 0x%x (unresolved)" % (a, u)
        if c["mh"] <= a < c["mh"] + MAIN_SPAN:
            return "0x%x = main image 0x%x" % (a, a - c["mh"] + MAIN_VMADDR)
        self.unres.setdefault("outside the shared cache and the main image (slide/mh of the crash header)", []).append(a)
        return "?0x%x (outside cache and main image)" % a

    def lacking_scan(self, c):
        missing = [k for k in ("pc", "lr") if k not in c["regs"]] + ([] if c["bt"] else ["bt"])
        if missing:
            self.lacking.setdefault(",".join(missing), []).append("%s[%d]" % (c["name"], c["pid"]))

    def short(self, c, a):
        """one-token frame for the compact report line: function (no offset), main+0xOFF, or ?0xADDR"""
        f = self.frame(c, a)
        if f.startswith("?"):
            return f.split()[0]
        if "= main image" in f:
            return "main+0x%x" % (a - c["mh"])
        return f.split(" = cache 0x%x " % (a - c["slide"]), 1)[1].split(" + ")[0].split(" [")[0]

    def finish(self):
        for what, who in self.lacking.items():
            self.gaps.append("UCRASH GAP: %d first-crash report(s) lack %s in the log (%s)" % (len(who), what, ", ".join(who[:3])))
        for why, addrs in self.unres.items():
            self.gaps.append("UCRASH GAP: %d frame(s) %s, first 0x%x (shown as ?0x..)" % (len(addrs), why, addrs[0]))
        if self.dirty:
            g = save_cache(self.path, self.cache)
            if g:
                self.gaps.append(g)


def report_one(c, count, sym):
    r = c["regs"]
    out = ["UCRASH: %s sig %d (%s) x%d pid %d: %s" % (c["name"], c["sig"], SIGNALS.get(c["sig"], "?"), count, c["pid"], reason_text(c))]
    addrs = [r[k] for k in ("pc", "lr") if k in r] + c["bt"]
    sym.prepare(c, [int(x, 16) if isinstance(x, str) else x for x in addrs])
    sym.lacking_scan(c)
    if "pc" in r:
        ctx = ["code %s sub %s slide 0x%x mh 0x%x" % (c["code"], c["sub"], c["slide"], c["mh"])]
        ctx += ["%s 0x%x" % (k, r[k]) for k in ("sp", "fp", "far") if k in r]
        out.append("UCRASH:   " + "  ".join(ctx))
    for k in ("pc", "lr"):
        if k in r:
            out.append("UCRASH:   %s %s" % (k, sym.frame(c, r[k])))
    for i, a in enumerate(int(x, 16) for x in c["bt"]):
        out.append("UCRASH:   bt%d %s" % (i, sym.frame(c, a)))
    return out


def corpse_lines(corpse):
    if not corpse:
        return []
    line = "UCRASH: EXC_CORPSE_NOTIFY send failures: %d (%s)" % (sum(corpse.values()), ", ".join("error code %s x%d" % kv for kv in sorted(corpse.items())))
    if "5" in corpse:
        line += "; code 5 = no crash reporter receives the corpses, no .ips exists, this block is the only crash data"
    return [line]


def compact_one(c, count, sym):
    r = c["regs"]
    fr = [r[k] for k in ("pc", "lr") if k in r] + [int(x, 16) for x in c["bt"][:3]]
    sym.prepare(c, fr)
    return "UCRASH: %s sig %d (%s) x%d: %s | %s" % (c["name"], c["sig"], SIGNALS.get(c["sig"], "?"), count,
                                                     reason_text(c).replace("reason ", "").replace(" (signal namespace)", ""),
                                                     " < ".join(sym.short(c, a) for a in fr) or "no frames in the log")


def build(lines, full=True):
    """full: every shown group's first crash in detail; else one line per group (the report.txt form)"""
    crashes, corpse, jetsam = parse(lines)
    out = []
    if crashes:
        groups = {}
        for c in crashes:
            groups.setdefault(group_key(c), []).append(c)
        order = sorted(groups.values(), key=lambda g: -len(g))
        out.append("UCRASH: %d userland crash(es) in %d group(s) (process, signal, reason)" % (len(crashes), len(order)))
        sym = Sym()
        for g in order[:MAX_GROUPS]:
            if full:
                out += report_one(g[0], len(g), sym)
            else:
                out.append(compact_one(g[0], len(g), sym))
                sym.lacking_scan(g[0])
        if len(order) > MAX_GROUPS:
            rest = order[MAX_GROUPS:]
            out.append("UCRASH: +%d more group(s), %d crash(es), not shown: %s" % (
                len(rest), sum(len(g) for g in rest), ", ".join(sorted({g[0]["name"] for g in rest}))))
        sym.finish()
        gaps = list(dict.fromkeys(sym.gaps))
        if full or len(gaps) <= 1:
            out += gaps
        else:   # report.txt keeps its 40-line budget on every host (the offload host has no ipsw/cache: more gaps); every gap is in ucrash.txt
            out.append("UCRASH GAP: %d gap(s), all listed in ucrash.txt; first: %s" % (len(gaps), gaps[0][len("UCRASH GAP: "):][:160]))
    out += corpse_lines(corpse)
    if jetsam:
        out.append("UCRASH: jetsam kills: %d (%s)" % (sum(jetsam.values()), ", ".join("%s x%d" % kv for kv in sorted(jetsam.items()))))
    return out


def report_form(rd, log):
    """compact lines for report.txt (the 40-line report budget, tools/test_xrun_report.py); the full block goes to RUNDIR/ucrash.txt"""
    out = build(log, full=False)
    if not any(l.startswith("UCRASH: ") and "userland crash" in l for l in out):
        return out
    try:
        with open(os.path.join(rd, "ucrash.txt"), "w") as f:
            f.write("\n".join(build(log, full=True)) + "\n")
        out.append("UCRASH: full first-crash reports (pc/lr/sp/far, 10 symbolicated frames per group): ucrash.txt in the run dir or tools/ucrash_summary.py RUNDIR")
    except OSError as e:
        out.append("UCRASH GAP: ucrash.txt not written (%s): run tools/ucrash_summary.py RUNDIR for the full block" % e)
    return out


def lines(run):
    """report.txt entry point: never raises (a failure is one UCRASH GAP line)"""
    try:
        rd = run.get("dir") if isinstance(run, dict) else None
        if not rd:
            return ["UCRASH GAP: run has no dir"]
        log = read_log(rd)
        if log is None:
            return ["UCRASH GAP: no usbnet.log/merged.log in %s: userland crashes unknown" % rd]
        return report_form(rd, log)
    except Exception as e:  # the report must still print when the summary breaks: one loud line instead
        return ["UCRASH GAP: ucrash_summary failed: %s: %s" % (type(e).__name__, e)]


def main(argv):
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0 if len(argv) == 2 else 2
    out = lines({"dir": argv[1]})
    print("\n".join(out) if out else "UCRASH: none (no userland CRASH, EXC_CORPSE_NOTIFY or jetsam kill line in the log)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
