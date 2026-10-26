#!/usr/bin/env python3
"""envfile: find the gitignored .env of this checkout (read-only, never prints values).

Order: $PIXOS_ENV_FILE, the worktree's own <root>/.env, else <git-common-dir>/../.env (the main clone). A worktree made with
`git worktree add` has no .env (gitignored); this lets tools use the main clone's without copying it.
  tools/envfile.py path                 print the resolved path (RESULT: ERROR, exit 2 when none)
  tools/envfile.py require KEY...       exit 2 with `RESULT: ERROR ...` unless each KEY is non-empty in the process env or the file
"""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find(root=ROOT):
    p = os.environ.get("PIXOS_ENV_FILE")
    if p:
        return p if os.path.isfile(p) else None
    own = os.path.join(root, ".env")
    if os.path.isfile(own):
        return own
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    main = os.path.join(os.path.dirname(out), ".env")
    return main if os.path.isfile(main) else None


def load(root=ROOT):
    """KEY=VALUE pairs of the resolved file ({} when none)."""
    p, d = find(root), {}
    if p:
        with open(p) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k and not k.startswith("#"):
                    d[k] = v
    return d


def load_into_environ(root=ROOT):
    for k, v in load(root).items():
        os.environ.setdefault(k, v)


def main(argv):
    if len(argv) >= 2 and argv[0] == "path":
        p = find()
        if p:
            print(p)
            return 0
        print("RESULT: ERROR no .env (own, or the main clone's via git-common-dir)")
        return 2
    if len(argv) >= 2 and argv[0] == "require":
        have = load()
        miss = [k for k in argv[1:] if not (os.environ.get(k) or have.get(k))]
        if miss:
            print("RESULT: ERROR missing %s in .env (looked at %s): create it in the main clone, or set the key in the environment; "
                  "or pass --no-camera" % (",".join(miss), find() or "no file found"))
            return 2
        print("RESULT: PASS .env has %s" % ",".join(argv[1:]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
