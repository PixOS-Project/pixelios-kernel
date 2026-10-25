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
                   "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_ed25519*", ".env", ".env.*"]
FORBIDDEN_DIRS = ("ipsw/", "Payload/")
ALLOW_TRACKED_BINARY = ()
# content is not scanned in files that quote licences or hold the planted fixtures
NO_SCAN = ("docs/*", "LICENSES/*", "LICENSE", "THIRD_PARTY.md", "xnu/patches/NOTICE.md", "tools/testdata/*", "tools/test_*.py",
           "tools/license-map.txt", "tools/check_rules/r30_license.py")
# Copyright holder (regex on the text after "Copyright") -> (id that must be in the entry's expression, substring the origin must contain)
HOLDERS = [
    (r"apple|next computer|carnegie", "APSL-2.0", None),
    (r"broadcom|wildt", "ISC", None),
    (r"nayuki", "MIT", "nayuki"),
    (r"dipa-studio", "MIT", "ios-sweet"),
    (r"cambus", "BSD-2-Clause", None),
    (r"android open source", None, "AOSP"),
]
SPDX = re.compile(r"SPDX-License-Identifier:\s*([^*\n]+)")
COPY = re.compile(r"^[\s/*#;+>-]*Copyright\b(.*)", re.I)


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
    if any(rel.startswith(d) for d in FORBIDDEN_DIRS):
        return f"path under {rel.split('/')[0]}/ (Apple payload / firmware)"
    for g in FORBIDDEN_NAMES:
        if fnmatch.fnmatchcase(base.lower(), g):
            return f"name matches {g} (firmware, Apple payload, key or secret)"
    return None


def check(root):
    out = []
    rows = read_map(root, out)
    for rel in files(root):
        why = forbidden(rel)
        if why:
            out.append(Finding(rel, 1, rel, f"{RULE} forbidden: tracked {why}"))
        hit = next((r for r in rows if fnmatch.fnmatchcase(rel, r[0])), None)
        if hit is None:
            out.append(Finding(rel, 1, rel, f"{RULE} unmapped: no line of {MAP} matches (add one with its licence and origin)"))
            continue
        if any(fnmatch.fnmatchcase(rel, g) for g in NO_SCAN):
            continue
        data = (Path(root) / rel).read_bytes()
        if b"\0" in data[:4096]:
            continue
        mapped = ids(hit[1])
        for n, l in enumerate(data.decode(errors="replace").splitlines(), 1):
            for m in SPDX.finditer(l):
                bad = ids(m.group(1)) - mapped
                if bad:
                    out.append(Finding(rel, n, l, f"{RULE} spdx: {sorted(bad)} not in map entry '{hit[1]}' (line {hit[3]} of {MAP})"))
            m = COPY.match(l)
            if m:
                for rx, need_id, need_origin in HOLDERS:
                    if re.search(rx, m.group(1), re.I) and ((need_id and need_id not in mapped) or (need_origin and need_origin.lower() not in hit[2].lower())):
                        out.append(Finding(rel, n, l, f"{RULE} holder: '{rx}' copyright needs {need_id or ''} {need_origin or ''} in map entry "
                                                      f"'{hit[1]}' / '{hit[2]}' (line {hit[3]} of {MAP})"))
    return out
