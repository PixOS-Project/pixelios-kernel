"""resident_core: the pure decisions of the resident iOS session (tools/resident). No I/O, no clock, no phone: host-testable.

User 2026-11-11 (AGENTS hard rule 3, docs/RULES.md H3): inside a resident session the phone stays in iOS between jobs; it parks in fastboot when
idle > 30 min, when the battery is below the floor or falling, after a crash and at session end. Everything here is a function of the state the
session owner passes in; tools/resident does the I/O and logs every decision (a refused or cut job is always a GAP line, rule 7).

Job (dict, written by `resident submit`):
  id, stream, owner, thread, prio (1 = first .. 9), est_s, max_s, submitted (epoch s), kind 'user'|'kernel',
  spec {profile, kernel (path or None = the session's standard kernel), kernel_sha, args {key: value|None}, overlay (dir or None),
        overlay_files {relpath: sha256}, camera (bool: the ESP camera capture runs for this boot)}, steps [[op, value], ...], expect [regex], expect_absent [regex]
Boot spec (dict): {profile, kernel, kernel_sha, args {..}, overlays [dir, ...], overlay_files {relpath: sha256}}

Order (fairness): lower effective priority first, then the shorter estimate (shortest job first within a priority), then FIFO. A job's effective
priority improves by one level per cfg aging_s of waiting, so a long job is never starved by a stream of short ones.
Boots: a job runs on the current boot only when its spec is contained in the boot spec (same profile or profile 'any', same kernel sha, its
boot-args present with the same values, its overlay files present with the same content). Others wait for the next boundary (between two jobs);
kernel/boot-arg jobs are collected for cfg kernel_batch_s and then ALL compatible ones go into ONE reboot (incompatible ones form the next
batch: they run in sequence). A job for another profile waits at most cfg switch_wait_s while the current boot still has work.
Battery (rows `SWEET CHG P3: t=..` / `SWEET CHG MON: t=..` of patches 0287/0298; current > 0 = charging, chg-p3 [obs 2026-11-10]):
  park when the median of the last 3 readings is below floor_mv, when the mean current of the readings taken while no job ran in the last
  falling_window_s is below 0 (>= falling_min_samples readings; readings of the first settle_s after the boot came up do not count), or when a charge wait lasted charge_wait_max_s. No reading for no_data_s after
  the boot was up (a kernel without 0298 stops after 5 min): no further job on THIS boot; a reboot for jobs of another boot is allowed (that
  boot's own readings are checked first), else park. Between floor_mv and floor_mv + start_margin_mv no job starts (charge wait: the session's own idle state, no job
  holds it). A running job is cut when a reading falls below floor_mv - cut_margin_mv.
"""
import os
import re

DEFAULTS = {
    "floor_mv": 3700, "start_margin_mv": 40, "cut_margin_mv": 60, "r_int_mohm": 170, "vcell_hard_mv": 3500,
    "floor_soc": 15.0, "start_margin_soc": 3.0, "cut_soc": 10.0,
    "falling_window_s": 600, "falling_min_samples": 8, "no_data_s": 600, "charge_wait_max_s": 3600,
    "idle_park_s": 1800, "job_max_default_s": 600, "job_max_cap_s": 1800, "aging_s": 900,
    "settle_s": 300, "host_step_max_s": 600, "falling_horizon_s": 1800, "kernel_batch_s": 120, "switch_wait_s": 900, "lock_margin_s": 420, "boot_cost_s": 420, "build_s": 900,
    "default_profile": "plain",
}

BATT_RX = re.compile(r"SWEET CHG (?:P3|MON): t=-?(\d+)s current=(-?\d+) uA vcell=(\d+) mV(?: soc=(\d+)\.(\d\d) %)?")   # soc: patch 0307 (RepSOC)
P2_SOC_RX = re.compile(r"SWEET CHG P2: fg\[0x36\].*?RepSOC\[0x06\]=0x[0-9a-fA-F]+ \((\d+)\.(\d\d) %\)")   # patch 0284 phase 2, once per boot
ARG_KEY_RX = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def cfg_with(over=None):
    c = dict(DEFAULTS)
    for k, v in (over or {}).items():
        if k not in DEFAULTS:
            raise ValueError("unknown resident setting %r (known: %s)" % (k, ", ".join(sorted(DEFAULTS))))
        c[k] = type(DEFAULTS[k])(v)
    if c["floor_mv"] < 3500 or c["floor_mv"] > 4300:
        raise ValueError("floor_mv %d outside 3500..4300" % c["floor_mv"])
    return c


def parse_args(text):
    """'a=1 b c=2' -> {'a': '1', 'b': None, 'c': '2'}; a repeated key with another value is an error (XNU takes the last one silently)."""
    out = {}
    for tok in (text or "").split():
        k, _, v = tok.partition("=")
        if not ARG_KEY_RX.fullmatch(k):
            raise ValueError("boot-arg token %r is not key[=value]" % tok)
        val = v if "=" in tok else None
        if k in out and out[k] != val:
            raise ValueError("boot-arg %s given twice with different values (%r, %r)" % (k, out[k], val))
        out[k] = val
    return out


LIST_ARGS = ("sweetnull",)   # comma lists (program names whose stdout goes to the USB log): merged as a union, never a conflict


def _names(v):
    return [n for n in (v or "").split(",") if n]


def args_text(args):
    return " ".join(k if v is None else "%s=%s" % (k, v) for k, v in sorted(args.items()))


def battery_rows(text):
    """(t_s, vcell_mV, current_uA, soc_pct or None) per SWEET CHG P3/MON row (soc only with patch 0307)."""
    return [(int(m.group(1)), int(m.group(3)), int(m.group(2)), (int(m.group(4)) + int(m.group(5)) / 100.0) if m.group(4) else None)
            for m in BATT_RX.finditer(text)]


# shim mifclk.c:221 (mif_clk_cmdline): with --mif-clk the shim appends the sweetmif* tokens (MIF_TOK_MAX 128) to the XNU command line only
# while n + 128 < BOOT_LINE_LENGTH (shim/payload.h; 2048 with xnu patch 0320, 1024 with 0176 alone), else 'GAP: no room ... nothing asked'
# and the boot runs MIF at boot level. Cost 3 boots on 2026-11-11 at 1024 (all_demo 1018 B, all_on1 991 B, job 6f6a 898 B: C-02).
# BOOT_LINE_LENGTH must equal tools/mkpayload.py's (test_resident: core case bootline_len_one_source).
BOOT_LINE_LENGTH = 2048
LINE_MAX = BOOT_LINE_LENGTH - 1   # characters before the NUL
MIF_LINE_MAX = LINE_MAX - 128     # the shim's own refusal reserve (MIF_TOK_MAX 128, mifclk.c:221)
# shim pmicsrc.h / loader.c pmic_src_append (ALL-ON T-02/T-24): with --pmic-src the shim appends " sweetps10=0x........ sweetps11=0x.... sweetpsrc=N" (PMIC_SRC_TOK_MAX 51 = 50
# characters + NUL) AFTER the sweetmif* tokens; it never truncates (no room = nothing appended). The sweetmif* tokens are at most 4 x 22 + 13 = 101 characters
# (" sweetmif0b=" 12 + 10 digits; " sweetmifrc=N" 13). So the line the shim receives may be at most LINE_MAX - 50 B with --pmic-src alone, and LINE_MAX - 101 - 50 B with both flags.
# --pmic-src-clear (research 142) appends one more token " sweetpsclr=N" (14 characters, the code is decimal 0..10) and " sweetpspw=0xNN" (15): 79 characters in all (PMIC_SRC_TOK_MAX 80).
PSRC_TOK_CHARS = 50
PSCLR_TOK_CHARS = 79
MIF_TOK_CHARS = 101
PSRC_LINE_MAX = LINE_MAX - PSRC_TOK_CHARS
MIF_PSRC_LINE_MAX = LINE_MAX - MIF_TOK_CHARS - PSRC_TOK_CHARS
PSCLR_LINE_MAX = LINE_MAX - PSCLR_TOK_CHARS
MIF_PSCLR_LINE_MAX = LINE_MAX - MIF_TOK_CHARS - PSCLR_TOK_CHARS
MIF_FLAGS = ("--mif-clk", "--mif-int-pair", "--mif-clk-level")   # both imply --mif-clk (shim/intpair xnu 0321; shim/mif-level f54dcad6): the same sweetmif* tokens


def line_limit(xrun_args):
    """(limit B, why) of the boot-arg line the host may hand the shim for these xrun args; (None, '') when the shim appends nothing."""
    xa = xrun_args or []
    mif, clr = any(f in xa for f in MIF_FLAGS), "--pmic-src-clear" in xa
    psrc = "--pmic-src" in xa or clr	# --pmic-src-clear implies --pmic-src
    tok, name = (PSCLR_TOK_CHARS, "--pmic-src-clear") if clr else (PSRC_TOK_CHARS, "--pmic-src")
    if mif and psrc:
        return (MIF_PSCLR_LINE_MAX if clr else MIF_PSRC_LINE_MAX), "MIF and %s: the shim appends up to %d + %d B of sweetmif*/sweetps* tokens" % (name, MIF_TOK_CHARS, tok)
    if mif:
        return MIF_LINE_MAX, "MIF (--mif-clk / --mif-int-pair)"
    if psrc:
        return (PSCLR_LINE_MAX if clr else PSRC_LINE_MAX), "%s: the shim appends up to %d B of sweetps* tokens" % (name, tok)
    return None, ""


def bootline_of(text):
    """The 'boot-args: ...' line of `xrun --print-bootargs` output, None when absent."""
    for ln in (text or "").splitlines():
        if ln.startswith("boot-args:"):
            return ln.split(":", 1)[1].strip()
    return None




def mif_line_check(xrun_args, line):
    """'' when the boot may run; else why it is refused: a --mif-clk / --pmic-src boot whose boot-arg line is over line_limit() bytes."""
    lim, why = line_limit(xrun_args)
    if lim is not None and line is not None and len(line.encode()) > lim:
        return "boot-arg line %d B > %d B with %s: the shim would drop the sweetmif*/sweetps* tokens (mifclk.c:221, loader.c pmic_src_append; C-02); shorten args/sweetnull" % (
            len(line.encode()), lim, why)
    return ""


# ---- submit guards (coordinator 2026-11-12: a setup slip seen twice gets a host guard, checked before anything is queued)
# program basename (a --push DST or the program of a --run) -> kernel strings it needs; ALL-ON rules G3
KERNEL_NEEDS = {
    "pixnav": ["SWEET GESTURE: id %u begin", "sweet: swaps and distinct frames"],   # patch 0312 (kern.sweetgesture) + the frame counter
    "bt-mgrd": ["BT VSC: adapter ON"],
}
RUN_ARG_NEEDS = {("fcapctl", "panel"): ["sweetpanel"]}   # kern.sweetpanel (panel readback); ALL-ON 2026-11-12: "SPNL" is no kernel string (allon-k18: 0 hits)


def job_kernel_needs(steps, needs_kernel=()):
    """[(regex, source, shown)] the job's kernel must contain: its --needs-kernel REGEXes + the KERNEL_NEEDS/RUN_ARG_NEEDS rows (literal
    strings) of its programs."""
    out = [(rx, "--needs-kernel", rx) for rx in needs_kernel or []]
    for op, val in steps:
        words = val.split(" ") if op == "run" else ([val.rpartition(":")[2]] if op == "push" else [])
        if not words:
            continue
        prog = os.path.basename(words[0])
        for lit in KERNEL_NEEDS.get(prog, []):
            out.append((re.escape(lit), "%s %s" % (op, prog), lit))
        if op == "run" and len(words) > 1:
            for lit in RUN_ARG_NEEDS.get((prog, words[1]), []):
                out.append((re.escape(lit), "run %s %s" % (prog, words[1]), lit))
    return out


def missing_kernel_needs(needs, kernel_bytes):
    return ["%s (needed by %s)" % (shown, src) for rx, src, shown in needs if not re.search(rx.encode(), kernel_bytes)]


def profile_inherit_check(job, boot_info, cfg, std_sha):
    """G2: a job with neither --profile nor --kernel runs on the CURRENT boot; '' or why it is refused. BOOT_INFO = {profile, kernel_sha}
    of the running boot, or None (no session / between boots)."""
    if job["spec"]["profile"] != "any" or job["spec"].get("kernel"):
        return ""
    if not boot_info:
        return "no --profile/--kernel and no running session boot to inherit"
    if job["kind"] == "kernel" and (cfg["default_profile"], std_sha) != (boot_info.get("profile"), boot_info.get("kernel_sha")):
        return "no --profile/--kernel and the session would switch to %s (kernel %s; now %s, %s)" % (
            cfg["default_profile"], (std_sha or "?")[:12], boot_info.get("profile"), (boot_info.get("kernel_sha") or "?")[:12])
    return ""


def profile_args_check(job_args, profile_args):
    """G4: job --args keys that the profile sets to another value (LIST_ARGS merge, never a conflict); '' or why."""
    clash = ["%s=%s (profile %s)" % (k, v, profile_args[k]) for k, v in sorted(job_args.items())
             if k not in LIST_ARGS and k in profile_args and profile_args[k] != v]
    return ("--args overrides the profile: " + ", ".join(clash)) if clash else ""


# a userland program's stdout mirrored by sweetnull ('[  134.497] |backboardd| ...'): its text is not a kernel panic line (ALL-ON T-28
# 2026-11-12: a --restart backboardd job with backboardd mirrored; an 'Assertion failed' from libsweetgl must not end the session)
MIRROR_RX = re.compile(r"^\S*\s*\[\s*[0-9.]+\] \|[^|\s]+\| ")


# ---- notice routing (coordinator 2026-11-12: replaced threads kept waking on RESIDENT JOB END/START notices addressed to the submitter)
def parse_owner_file(text):
    """owners/<key>: first non-comment line = the current owner thread id; 'former <id>' lines = replaced owners. -> (current, [former])"""
    cur, former = None, []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if ln.startswith("former "):
            former.append(ln.split(None, 1)[1].strip())
        elif cur is None:
            cur = ln
    return cur, former


def resolve_notice(submitter, owner_key, owners):
    """(thread or None, how) for a job notice. OWNERS = {key: (current, [former])}. A job with an owner key goes to that key's current
    owner; a submitter that is a former owner of a key goes to that key's current owner; with no owner it goes to nobody (the caller logs
    a GAP). A submitter with no owner file anywhere keeps the old behaviour (its own thread)."""
    if owner_key:
        cur = (owners.get(owner_key) or (None, []))[0]
        return (cur, "owner %s" % owner_key) if cur else (None, "owner key %s has no owner file/current thread" % owner_key)
    for key, (cur, former) in sorted(owners.items()):
        if submitter and submitter in former:
            return (cur, "submitter replaced: owner %s" % key) if cur else (None, "submitter replaced (owner %s) and no current owner" % key)
        if submitter and submitter == cur:
            return cur, "owner %s" % key
    return (submitter, "submitter") if submitter else (None, "no submitter thread and no owner key")


def p2_soc(text):
    """The boot's phase-2 fuel-gauge RepSOC (%), None when the text has no P2 fg line."""
    m = P2_SOC_RX.search(text)
    return (int(m.group(1)) + int(m.group(2)) / 100.0) if m else None


# ---- fairness ----
def eff_prio(job, now, cfg):
    waited = max(0, now - job["submitted"])
    return job["prio"] - int(waited // cfg["aging_s"])


def order_key(job, now, cfg):
    return (eff_prio(job, now, cfg), job["est_s"], job["submitted"], job["id"])


def ordered(jobs, now, cfg):
    return sorted(jobs, key=lambda j: order_key(j, now, cfg))


# ---- boot specs ----
def job_kernel_sha(job, std_sha):
    return job["spec"].get("kernel_sha") or std_sha


def same_args(a, b):
    """True when two --args dicts give the same boot line (LIST_ARGS compared as sets). User 2026-11-12 (T-51): only identical kernel +
    profile + boot line share a boot."""
    norm = lambda d: {k: (frozenset(_names(v)) if k in LIST_ARGS else v) for k, v in (d or {}).items()}  # noqa: E731
    return norm(a) == norm(b)


def compatible(job, boot, std_sha):
    """True when JOB can run on the running BOOT without a reboot."""
    if boot is None:
        return False
    s = job["spec"]
    if s.get("fresh") and boot.get("for") != job["id"]:   # --fresh-boot: only the boot made for this job (lane-t02 Q-01)
        return False
    if s["profile"] not in ("any", boot["profile"]):
        return False
    if job_kernel_sha(job, std_sha) != boot["kernel_sha"]:
        return False
    if (job["kind"] == "kernel" or s["profile"] != "any") and not same_args(s.get("args"), boot["args"]):   # only on its own boot line (T-51)
        return False
    for k, v in s.get("args", {}).items():
        if k in LIST_ARGS:
            if not set(_names(v)) <= set(_names(boot["args"].get(k))):
                return False
        elif k not in boot["args"] or boot["args"][k] != v:
            return False
    for p, h in s.get("overlay_files", {}).items():
        if boot["overlay_files"].get(p) != h:
            return False
    if s.get("camera") and not boot.get("camera"):
        return False
    return True


def base_spec(profile, std_kernel, std_sha):
    return {"profile": profile, "kernel": std_kernel, "kernel_sha": std_sha, "args": {}, "overlays": [], "overlay_files": {}, "camera": False}


def merge_into(spec, job, std_kernel, std_sha):
    """SPEC + JOB's needs -> a new spec, or (None, why) when they conflict."""
    s = job["spec"]
    prof = spec["profile"]
    if s["profile"] not in ("any", prof):
        return None, "profile %s vs %s" % (s["profile"], prof)
    ksha = job_kernel_sha(job, std_sha)
    if ksha != spec["kernel_sha"]:
        return None, "kernel %s vs %s" % (ksha[:12], spec["kernel_sha"][:12])
    args = dict(spec["args"])
    for k, v in s.get("args", {}).items():
        if k in LIST_ARGS:
            have = _names(args.get(k))
            args[k] = ",".join(have + [n for n in _names(v) if n not in have])
            continue
        if k in args and args[k] != v:
            return None, "boot-arg %s=%s vs %s" % (k, v, args[k])
        args[k] = v
    files = dict(spec["overlay_files"])
    for p, h in s.get("overlay_files", {}).items():
        if p in files and files[p] != h:
            return None, "overlay file %s differs" % p
        files[p] = h
    ovs = list(spec["overlays"]) + ([s["overlay"]] if s.get("overlay") and s["overlay"] not in spec["overlays"] else [])
    return {"profile": prof, "kernel": spec["kernel"], "kernel_sha": spec["kernel_sha"], "args": args, "overlays": ovs, "overlay_files": files,
            "camera": bool(spec.get("camera") or s.get("camera"))}, ""


def plan_boot(pending, now, cfg, std_kernel, std_sha):
    """The next boot for the PENDING jobs: seed = the first job in fairness order, then every other job that merges (in order).
    Returns (spec, [job ids in the batch], [(job id, why not)])."""
    jobs = ordered(pending, now, cfg)
    if not jobs:
        return None, [], []
    seed = jobs[0]
    prof = seed["spec"]["profile"] if seed["spec"]["profile"] != "any" else cfg["default_profile"]
    kern = seed["spec"].get("kernel") or std_kernel
    spec = base_spec(prof, kern, job_kernel_sha(seed, std_sha))
    batch, left = [], []
    if seed["spec"].get("fresh") or seed.get("solo"):   # its own boot: nothing else merges in (a fresh one is marked as made for it)
        nxt, why = merge_into(spec, seed, std_kernel, std_sha)
        if seed["spec"].get("fresh"):
            nxt["for"] = seed["id"]
        return nxt, [seed["id"]], [(j["id"], "%s boot for %s" % ("fresh" if seed["spec"].get("fresh") else "solo", seed["id"])) for j in jobs[1:]]
    for j in jobs:
        if j["spec"].get("fresh"):
            left.append((j["id"], "needs its own boot (--fresh-boot)"))
            continue
        if j.get("solo"):   # T-51 (resident_fit): its merged boot was not proven to build
            left.append((j["id"], "needs its own boot (%s)" % j["solo"][:120]))
            continue
        if j is not seed and not same_args(j["spec"].get("args"), seed["spec"].get("args")):   # user 2026-11-12: identical boot lines only
            left.append((j["id"], "boot line differs from %s (only identical kernel + profile + boot line batch)" % seed["id"]))
            continue
        nxt, why = merge_into(spec, j, std_kernel, std_sha)
        if nxt is None:
            left.append((j["id"], why))
            continue
        spec = nxt
        batch.append(j["id"])
    return spec, batch, left


# ---- battery ----
class Battery:
    def __init__(self, cfg):
        self.cfg = cfg
        self.samples = []       # (host_t, mv, ua, busy, soc or None)
        self.charge_wait_since = None
        self.boot_soc = None    # P2 RepSOC of this boot (patch 0284), the fallback when rows carry no soc

    def reset(self):
        self.samples.clear()
        self.boot_soc = None
        self.charge_wait_since = None

    def add(self, t, mv, ua, busy, soc=None):
        self.samples.append((t, mv, ua, bool(busy), soc))
        if len(self.samples) > 2000:
            del self.samples[:1000]

    def last_mv(self):
        if not self.samples:
            return None
        tail = sorted(s[1] for s in self.samples[-3:])
        return tail[len(tail) // 2]

    def comp_mv(self, mv, ua):
        """Load-compensated cell voltage: vcell - I x R (I < 0 = discharge, so comp > vcell under load; while charging comp < vcell). ALL-ON
        2026-11-11 [obs resident-182314-b1 SWEET CHG P3]: 3795 mV @ -586 mA and 3667 mV @ -1330 mA in one minute -> R = 0.17 ohm, comp ~3900 mV
        on every row while the user saw 73 %; the loaded vcell parked that phone at 'floor 3700'."""
        return int(round(mv - ua * self.cfg["r_int_mohm"] / 1e6))

    def last_comp(self):
        """(comp, vcell, ua) of the median-comp reading of the last 3, None without readings."""
        if not self.samples:
            return None
        tail = sorted((self.comp_mv(s[1], s[2]), s[1], s[2]) for s in self.samples[-3:])
        return tail[len(tail) // 2]

    def describe(self, r):
        soc = self.samples[-1][4] if self.samples else None
        return "vcell %d mV, I %d mA, comp %d mV%s" % (r[1], int(round(r[2] / 1000.0)), r[0], ", soc %.2f %%" % soc if soc is not None else "")

    def mode(self):
        """'soc' (rows carry RepSOC, patch 0307) | 'vcell+p2' (load-compensated vcell + the boot's P2 RepSOC) | 'vcell' (neither soc source)."""
        if self.samples and self.samples[-1][4] is not None:
            return "soc"
        return "vcell+p2" if self.boot_soc is not None else "vcell"

    def last_soc(self):
        tail = sorted(s[4] for s in self.samples[-3:] if s[4] is not None)
        return tail[len(tail) // 2] if tail else None

    def raw_low(self):
        """Safety net under the compensation (R is one estimate and grows on an empty cell): median raw vcell of the last 3 below vcell_hard_mv."""
        mv = self.last_mv()
        return mv is not None and mv < self.cfg["vcell_hard_mv"]

    def idle_mean_ua(self, now, boot_up_at=None):
        """Mean current of the idle readings in the window; readings of the first settle_s after the boot came up do not count (iOS start-up
        load drains: live session 20261111T160212Z parked on -21 mA from its first 2.5 min)."""
        since = max(now - self.cfg["falling_window_s"], (boot_up_at or 0) + self.cfg["settle_s"])
        w = [s[2] for s in self.samples if s[0] >= since and not s[3]]
        if len(w) < self.cfg["falling_min_samples"]:
            return None
        return sum(w) / len(w)

    def soc_projection(self, now, boot_up_at=None):
        """(drop %/s, projected soc after falling_horizon_s) from the idle soc readings of the falling window (after settle_s); None with
        fewer than falling_min_samples readings or a non-falling soc."""
        t0 = max(now - self.cfg["falling_window_s"], self.cfg["settle_s"] + (boot_up_at or 0))   # the same window as idle_mean_ua
        w = [(s[0], s[4]) for s in self.samples if s[0] >= t0 and not s[3] and s[4] is not None]
        if len(w) < self.cfg["falling_min_samples"] or w[-1][0] <= w[0][0]:
            return None
        rate = (w[0][1] - w[-1][1]) / (w[-1][0] - w[0][0])
        if rate <= 0:
            return None
        return rate, self.last_soc() - rate * self.cfg["falling_horizon_s"]

    def verdict(self, now, boot_up_at, no_falling=False):
        """Between jobs: ('ok'|'charge-wait'|'nodata'|'park', why). NO_FALLING: a reserve window with no_falling (user 2026-11-11 demo: floor only)."""
        c = self.cfg
        if not self.samples:
            if boot_up_at is not None and now - boot_up_at > c["no_data_s"]:
                return "nodata", "no battery reading for %d s after the boot was up (no SWEET CHG P3/MON row: --no-chg, or a kernel without 0287/0298)" % (now - boot_up_at)
            return "ok", "no reading yet"
        r = self.last_comp()
        mv = r[0]
        if self.raw_low():
            return "park", "battery raw vcell %d mV below the hard limit %d mV (%s)" % (self.last_mv(), c["vcell_hard_mv"], self.describe(r))
        mode = self.mode()
        if mode == "soc":
            soc = self.last_soc()
            if soc < c["floor_soc"]:
                return "park", "battery soc %.2f %% below the floor %.1f %% (%s)" % (soc, c["floor_soc"], self.describe(r))
        else:
            if mode == "vcell+p2" and self.boot_soc < c["floor_soc"]:
                return "park", "battery: boot RepSOC %.2f %% (P2) below the floor %.1f %% (%s)" % (self.boot_soc, c["floor_soc"], self.describe(r))
            if mv < c["floor_mv"]:
                return "park", "battery comp %d mV below the floor %d mV (%s)" % (mv, c["floor_mv"], self.describe(r))
        if boot_up_at is not None and now - self.samples[-1][0] > c["no_data_s"]:
            return "nodata", "last battery reading %d s old (%d mV; rows stopped: a kernel without 0298 has only the 5 min of 0287 P3 samples)" % (
                now - self.samples[-1][0], mv)
        if mode == "soc":   # T-17: falling = falling toward the floor (an all-on boot draws -0.2..-1.3 A even idle and parked 300 s after every batch)
            f = self.soc_projection(now, boot_up_at)
            if f is not None and f[1] < c["floor_soc"] + c["start_margin_soc"] and not no_falling:
                return "park", "battery falling: soc %.2f %% dropping %.2f %%/10 min, projected %.2f %% in %d s < floor + margin %.1f %%" % (
                    self.last_soc(), f[0] * 600, f[1], c["falling_horizon_s"], c["floor_soc"] + c["start_margin_soc"])
        else:
            m = self.idle_mean_ua(now, boot_up_at)
            if m is not None and m < 0 and not no_falling:
                return "park", "battery falling: mean %d uA over the idle readings of the last %d s" % (m, c["falling_window_s"])
        if (self.last_soc() < c["floor_soc"] + c["start_margin_soc"]) if mode == "soc" else (mv < c["floor_mv"] + c["start_margin_mv"]):
            if self.charge_wait_since is None:
                self.charge_wait_since = now
            if now - self.charge_wait_since > c["charge_wait_max_s"]:
                return "park", "charge wait %d s > %d s (still %s)" % (now - self.charge_wait_since, c["charge_wait_max_s"], self.describe(r))
            lim = ("%.1f %%" % (c["floor_soc"] + c["start_margin_soc"])) if mode == "soc" else ("%d mV" % (c["floor_mv"] + c["start_margin_mv"]))
            return "charge-wait", "battery below floor + margin %s: no job starts, charging in iOS (%s)" % (lim, self.describe(r))
        self.charge_wait_since = None
        return "ok", "battery %s" % self.describe(r)

    def cut(self):
        """During a job: the newest reading's comp below floor - cut margin, or the raw safety net. Returns the why text or ''."""
        if not self.samples:
            return ""
        t, mv, ua, _, soc = self.samples[-1]
        r = (self.comp_mv(mv, ua), mv, ua)
        if self.raw_low():
            return "battery raw vcell %d mV below the hard limit %d mV (%s)" % (self.last_mv(), self.cfg["vcell_hard_mv"], self.describe(r))
        if soc is not None:
            return ("battery soc %.2f %% below the cut %.1f %% (%s)" % (soc, self.cfg["cut_soc"], self.describe(r))) if soc < self.cfg["cut_soc"] else ""
        if r[0] < self.cfg["floor_mv"] - self.cfg["cut_margin_mv"]:
            return "battery comp %d mV below floor - cut margin %d mV (%s)" % (r[0], self.cfg["floor_mv"] - self.cfg["cut_margin_mv"], self.describe(r))
        return ""


# ---- reserved windows (demo slots) ----
def eligible(pending, now, reserves):
    """RESERVES = [{stream, from, to}] (epoch s). Inside a window only that stream's jobs may start; outside, a job of another stream may start
    only if its max ends before the next window opens (a demo take never waits behind someone's 30-min job). Returns (eligible, why-held)."""
    out, held = [], []
    for j in pending:
        ok = True
        for r in reserves or []:
            if r["from"] <= now < r["to"] and j["stream"] != r["stream"]:
                ok = False
                held.append("%s held: %s reserved until %s" % (j["id"], r["stream"], r["to"]))
            elif now < r["from"] and j["stream"] != r["stream"] and now + j["max_s"] > r["from"]:
                ok = False
                held.append("%s held: its max %d s would reach the %s window" % (j["id"], j["max_s"], r["stream"]))
        if ok:
            out.append(j)
    return out, held


# ---- the boundary decision ----
def decide(now, cfg, boot, pending, battery, boot_up_at, idle_since, lock_left, std_kernel, std_sha, boot_batch=(), reserves=()):
    """At a boundary (no job running). BOOT_BATCH = ids of the jobs the current boot was made for that have not run yet: they always run
    before any switch (live session 3, 2026-11-11: two jobs both past switch_wait_s rebooted each other's boot 5 times, none ran). Returns one of
       ('park', why)                    end the session: the phone goes to fastboot (battery, idle)
       ('end', why)                     end the session for the lock deadline (jobs stay queued for the next session)
       ('wait', why)                    nothing to start now
       ('run', job)                     run JOB on the current boot
       ('reboot', spec, batch, left)    boot SPEC for the job ids in BATCH (left = jobs that did not merge, with why)"""
    pending, held = eligible(pending, now, reserves)
    no_falling = any(r.get("no_falling") and r["from"] <= now < r["to"] for r in reserves or [])
    if boot is not None:
        v, why = battery.verdict(now, boot_up_at, no_falling)
        if v == "park":
            return ("park", why)
        if v == "nodata":   # no job on THIS boot; a reboot for other jobs is allowed (their boot reports fresh readings), else park
            room = lock_left - cfg["lock_margin_s"]
            other = [j for j in pending if not compatible(j, boot, std_sha) and j["max_s"] + cfg["boot_cost_s"] <= room]
            if not other:
                return ("park", why)
            spec, batch, left = plan_boot(other, now, cfg, std_kernel, std_sha)
            return ("reboot", spec, batch, left)
    else:
        v, why = "ok", "not booted"
    if not pending and held:
        return ("wait", "reserved window: " + held[0])
    if not pending:
        if boot is not None and idle_since is not None and now - idle_since > cfg["idle_park_s"]:
            return ("park", "idle %d s > %d s" % (now - idle_since, cfg["idle_park_s"]))
        if boot is None:
            return ("end", "no jobs and no boot")
        return ("wait", "no jobs")
    room = lock_left - cfg["lock_margin_s"]
    runnable = [j for j in pending if compatible(j, boot, std_sha) and j["max_s"] <= room]
    if v == "charge-wait":
        return ("wait", why)
    own = [j for j in runnable if j["id"] in boot_batch]
    if own:
        return ("run", ordered(own, now, cfg)[0])
    waiting = [j for j in pending if not compatible(j, boot, std_sha) and j["max_s"] + cfg["boot_cost_s"] <= room]
    if boot is not None and waiting:
        kernel_due = [j for j in waiting if j["kind"] == "kernel" and now - j["submitted"] >= cfg["kernel_batch_s"]]
        prof_due = [j for j in waiting if now - j["submitted"] >= cfg["switch_wait_s"]]
        if kernel_due or prof_due or not runnable:
            if not runnable and not kernel_due and not prof_due and all(j["kind"] == "kernel" for j in waiting):
                return ("wait", "collecting kernel jobs for one reboot (%d s window)" % cfg["kernel_batch_s"])
            spec, batch, left = plan_boot(waiting, now, cfg, std_kernel, std_sha)
            return ("reboot", spec, batch, left)
    if runnable:
        return ("run", ordered(runnable, now, cfg)[0])
    if waiting:   # not booted yet
        spec, batch, left = plan_boot(waiting, now, cfg, std_kernel, std_sha)
        return ("reboot", spec, batch, left)
    big = ordered(pending, now, cfg)[0]
    return ("end", "lock deadline: %d s left (margin %d s, a boot %d s), job %s needs %d s: jobs stay queued for the next session" % (
        lock_left, cfg["lock_margin_s"], cfg["boot_cost_s"], big["id"], big["max_s"]))
