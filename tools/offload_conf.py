"""offload_conf (T-dd93, R66): python loader for tools/offload-host.conf; twin of tools/offload-conf.sh (same file, same keys, env wins; off_reason() = the per-use offline switches).
Only `KEY=VALUE` lines of KEYS are read (never evaluated). get(key) -> value or None; load() returns (dict, path-or-None) and prints OFFLOAD GAP lines to stderr."""
import os, subprocess, sys

KEYS = ("OFFLOAD_HOST", "OFFLOAD_USER", "OFFLOAD_ROOT", "OFFLOAD_FOREIGN_RE", "LAB_HOME", "OFFLOAD_QUEUE_LABEL")


def path(root):
    c = os.environ.get("OFFLOAD_CONF")
    if c:
        return None if c == "none" else c
    try:
        g = subprocess.run(["git", "-C", root, "rev-parse", "--git-common-dir"], capture_output=True, text=True).stdout.strip()
        if g:
            g = g if os.path.isabs(g) else os.path.join(root, g)
            p = os.path.join(os.path.dirname(os.path.normpath(g)), "tools", "offload-host.conf")
            if os.path.isfile(p):
                return p
    except OSError:   # lint-silent: expected the git-common-dir probe failed: fall through to the conf next to this checkout
        pass
    p = os.path.join(root, "tools", "offload-host.conf")
    return p if os.path.isfile(p) else None


def load(root=None):
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    f = path(root)
    d = {}
    if f:
        try:
            lines = open(f).read().splitlines()
        except OSError:
            print("OFFLOAD GAP: host config %s is not readable; no offload values loaded" % f, file=sys.stderr)
            return d, None
        for line in lines:
            if not line or line.startswith("#"):
                continue
            k, eq, v = line.partition("=")
            if not eq or not k.replace("_", "").isalpha() or not k.isupper():
                print("OFFLOAD GAP: %s: ignored line without KEY=VALUE" % f, file=sys.stderr)
            elif k not in KEYS:
                print("OFFLOAD GAP: %s: unknown key %s ignored" % (f, k), file=sys.stderr)
            elif v:
                d[k] = v
    for k in KEYS:
        if os.environ.get(k):
            d[k] = os.environ[k]
    return d, f


def get(key, root=None):
    return load(root)[0].get(key)


# Per-use switches (twin of offload_off_reason in tools/offload-conf.sh; tools/test_offload_conf.sh proves the two agree). The use list is defined ONCE in the shell file; this tuple is its twin.
USES = ("build", "gate", "test", "sync", "ci")


def offline_file(root):
    """The queue's `offline` file (same resolution as the shell twin): $OFFLOAD_OFFLINE_FILE (`none` = ignore), else $PIXOS_SUPER/docs/queue/offline, else <main clone>/../../docs/queue/offline."""
    f = os.environ.get("OFFLOAD_OFFLINE_FILE")
    if f:
        return None if f == "none" else f
    if os.environ.get("PIXOS_SUPER"):
        return os.path.join(os.environ["PIXOS_SUPER"], "docs", "queue", "offline")
    g = subprocess.run(["git", "-C", root, "rev-parse", "--git-common-dir"], capture_output=True, text=True).stdout.strip()
    g = os.path.join(root, ".git") if not g else (g if os.path.isabs(g) else os.path.join(root, g))
    return os.path.normpath(os.path.join(g, "..", "..", "..", "docs", "queue", "offline"))


def off_reason(root=None, use=None, label=None):
    """-> the reason text (with the file in parentheses) when the master row `<label>` or the row `<label>:<use>` is in the offline file, else None. Unknown use: GAP line on stderr, master only."""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if use and use not in USES:
        print("OFFLOAD GAP: unknown offload use '%s' (valid: %s); only the master switch is checked" % (use, " ".join(USES)), file=sys.stderr)
        use = None
    label = label or os.environ.get("OFFLOAD_QUEUE_LABEL") or load(root)[0].get("OFFLOAD_QUEUE_LABEL") or "offload-host"
    f = offline_file(root)
    try:
        lines = open(f).read().splitlines() if f else []
    except OSError:   # lint-silent: expected a missing or unreadable offline file means the host is on (the shell twin does the same)
        return None
    hit = None
    for line in lines:
        if not line or line.startswith("#"):
            continue
        h, _, r = line.partition("\t")
        if h == label:
            return "%s (%s)" % (r or "no reason given", f)
        if use and h == "%s:%s" % (label, use) and hit is None:
            hit = "%s (%s)" % (r or "no reason given", f)
    return hit
