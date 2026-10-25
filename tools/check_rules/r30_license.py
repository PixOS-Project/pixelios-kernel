"""R30 (TP-030): every tracked file has a licence in tools/license-map.txt, the map names only allowed ids whose text is in LICENSES/,
SPDX/Copyright headers agree with the map entry, and no firmware / secret / Apple payload is tracked. Spec: docs/PLAN-STANDALONE.md T5, section 3.
Map: `glob<TAB>SPDX expression<TAB>origin`, fnmatch on the repo-relative path ('*' matches '/'), FIRST match wins, no catch-all.
Tracked files = `git ls-files` (a plain directory walk when root is not a git work tree, for tests). Findings carry no baseline: fix them."""
import fnmatch, os, re, subprocess
from pathlib import Path
from .lib import Finding

RULE = "R30"
MAP = "tools/license-map.txt"
ALLOWED = {"MIT", "ISC", "APSL-2.0", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0"}
# forbidden tracked material (AGENTS.md rule 4): firmware / Apple payloads / secrets, by basename; ALLOW_TRACKED_BINARY is the exception list (empty)
FORBIDDEN_NAMES = ["*.img", "*.bin", "*.ipsw", "*.dmg", "*.im4p", "*.im4m", "*.img4", "*.ota", "*.mbn", "*.pem", "*.key", "*.p12",
                   "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_ed25519*", ".env", ".env.*", "*.env", "dyld_shared_cache*", "kernelcache*"]
FORBIDDEN_DIRS = ("ipsw", "Payload")  # a directory of this name at any depth
ALLOW_TRACKED_BINARY = ()  # a file with a NUL byte in its first 4 KiB is a finding unless listed here
# content is not scanned in files that quote licences/reviews or hold the planted fixtures and the check's own marker lists
NO_SCAN = ("docs/research/*", "docs/reviews/*", "LICENSES/*", "THIRD_PARTY.md", "tools/testdata/*", "tools/test_check_rules_r30.py",
           "tools/check_rules/r30_license.py")
# markers that must not appear in a scanned file (plan check 2): GPL text / kernel-licence macros / non-commercial licences
BANNED = ("GNU General Public License", "Broadcom-WL-IPTag", "MODULE_LICENSE", "EXPORT_SYMBOL", "PolyForm")
# Copyright holder (regex on the text after "Copyright") -> (id that must be in the entry's expression, substring the origin must contain,
# LICENSES/ file that must exist). A copyright line whose holder matches NO row is a finding (allowlist).
HOLDERS = [
    (r"pixos-project", None, None, None),
    (r"apple|\bnext\b|carnegie|\bcmu\b", "APSL-2.0", None, "APSL-2.0.txt"),
    (r"broadcom|wildt", "ISC", None, "ISC-bwfm.txt"),
    (r"nayuki", "MIT", "nayuki", "MIT-qrcodegen.txt"),
    (r"dipa-studio", "MIT", "ios-sweet", "MIT-ios-sweet.txt"),
    (r"cambus", "BSD-2-Clause", None, "Spleen-BSD-2-Clause.txt"),
    (r"android open source|\baosp\b", None, "AOSP", "MIT-AOSP-avbtool.txt"),
    (r"pornin", "MIT", None, "MIT-BearSSL.txt"),
]
# device/** must not carry identifiers of the phone (AGENTS.md rule 4): serial, IMEI, a globally administered MAC
DEVICE_LEAKS = [
    (re.compile(r"androidboot\.serialno=(?!<|\*|x+\b|REDACTED)\S", re.I), "androidboot.serialno= with a value"),
    (re.compile(r"\bimei\b\W{0,4}\d{8,}", re.I), "an IMEI number"),
]
MAC = re.compile(r"(?<![0-9A-Fa-f:-])((?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})(?![0-9A-Fa-f:-])")
SPDX = re.compile(r"SPDX-License-Identifier:\s*([^*\n]+)")
# "Copyright", "(c)" followed by a year (a bare "(c)" is also a list marker in comments, e.g. "(c) poll is bounded"), or the copyright sign
COPY = re.compile(r"^[\s/*#;+>-]*(?:Copyright\b|\(c\)(?=\s*(?:19|20)\d\d)|\u00a9)(.*)", re.I)


def ids(expr):
    return {t for t in re.split(r"[\s()]+", expr) if t and t not in ("AND", "OR", "WITH")}


def read_map(root, out):
    rows = []
    f = Path(root) / MAP
    if not f.is_file():
        out.append(Finding(MAP, 1, "", f"{RULE} map-missing: {MAP} not found"))
        return rows
    for n, l in enumerate(f.read_text().splitlines(), 1):
        if not l.strip() or l.startswith("#"):
            continue
        c = l.split("\t")
        if len(c) != 3 or not all(x.strip() for x in c):
            out.append(Finding(MAP, n, l, f"{RULE} map-syntax: want glob<TAB>SPDX<TAB>origin"))
            continue
        for i in sorted(ids(c[1])):
            if i not in ALLOWED:
                out.append(Finding(MAP, n, l, f"{RULE} map-id: {i} is not in the allowlist {sorted(ALLOWED)}"))
            elif not list((Path(root) / "LICENSES").glob(f"*{i}*.txt")):
                out.append(Finding(MAP, n, l, f"{RULE} map-text: no LICENSES/*{i}*.txt for {i}"))
        if re.fullmatch(r"\*+", c[0].strip()):
            out.append(Finding(MAP, n, l, f"{RULE} map-catchall: a bare '*' glob would license every unmapped file"))
            continue
        rows.append((c[0], c[1], c[2], n))
    return rows


def files(root):
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True)
    if r.returncode == 0:
        names = [x for x in r.stdout.decode(errors="replace").split("\0") if x]
    else:
        names = [p.relative_to(root).as_posix() for p in sorted(Path(root).rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts]
    return [n for n in names if (Path(root) / n).is_file()]


def forbidden(rel):
    base = rel.rsplit("/", 1)[-1]
    if rel in ALLOW_TRACKED_BINARY:
        return None
    hit = [d for d in FORBIDDEN_DIRS if d in rel.split("/")[:-1]]
    if hit:
        return f"path under a {hit[0]}/ directory (Apple payload / firmware)"
    for g in FORBIDDEN_NAMES:
        if fnmatch.fnmatchcase(base.lower(), g):
            return f"name matches {g} (firmware, Apple payload, key or secret)"
    return None


def check(root):
    out = []
    rows = read_map(root, out)
    for t in sorted((Path(root) / "LICENSES").glob("*.txt")):
        if not t.read_text(errors="replace").strip():
            out.append(Finding(f"LICENSES/{t.name}", 1, "", f"{RULE} license-empty: licence text is empty"))
    for rel in files(root):
        why = forbidden(rel)
        if why:
            out.append(Finding(rel, 1, rel, f"{RULE} forbidden: tracked {why}"))
        hit = next((r for r in rows if fnmatch.fnmatchcase(rel, r[0])), None)
        if hit is None:
            out.append(Finding(rel, 1, rel, f"{RULE} unmapped: no line of {MAP} matches (add one with its licence and origin)"))
            continue
        data = (Path(root) / rel).read_bytes()
        if b"\0" in data[:4096]:
            if rel not in ALLOW_TRACKED_BINARY:
                out.append(Finding(rel, 1, rel, f"{RULE} binary: NUL byte in the first 4 KiB (firmware / binary blob; add to ALLOW_TRACKED_BINARY only with a reason)"))
            continue
        if any(fnmatch.fnmatchcase(rel, g) for g in NO_SCAN):
            continue
        mapped = ids(hit[1])
        for n, l in enumerate(data.decode(errors="replace").splitlines(), 1):
            for b in BANNED:
                if b in l:
                    out.append(Finding(rel, n, l, f"{RULE} banned: '{b}' (GPL / kernel-licence / non-commercial marker)"))
            if rel.startswith("device/"):
                for rx, what in DEVICE_LEAKS:
                    if rx.search(l):
                        out.append(Finding(rel, n, l, f"{RULE} device-leak: {what} (strip serials, IMEI, MACs)"))
                for m in MAC.finditer(l):
                    first = int(m.group(1)[:2], 16)
                    if not first & 2 and int(m.group(1).replace(":", "").replace("-", ""), 16):
                        out.append(Finding(rel, n, l, f"{RULE} device-leak: globally administered MAC {m.group(1)} (strip MAC addresses)"))
            for m in SPDX.finditer(l):
                bad = ids(m.group(1)) - mapped
                if bad:
                    out.append(Finding(rel, n, l, f"{RULE} spdx: {sorted(bad)} not in map entry '{hit[1]}' (line {hit[3]} of {MAP})"))
            m = COPY.match(l)
            if m:
                known = False
                for rx, need_id, need_origin, notice in HOLDERS:
                    if not re.search(rx, m.group(1), re.I):
                        continue
                    known = True
                    if (need_id and need_id not in mapped) or (need_origin and need_origin.lower() not in hit[2].lower()):
                        out.append(Finding(rel, n, l, f"{RULE} holder: '{rx}' copyright needs {need_id or ''} {need_origin or ''} in map entry "
                                                      f"'{hit[1]}' / '{hit[2]}' (line {hit[3]} of {MAP})"))
                    if notice and not (Path(root) / "LICENSES" / notice).is_file():
                        out.append(Finding(rel, n, l, f"{RULE} notice: '{rx}' copyright needs LICENSES/{notice}"))
                if not known:
                    out.append(Finding(rel, n, l, f"{RULE} holder-unknown: copyright holder is not in the allowlist {[h[0] for h in HOLDERS]}"))
    return out
