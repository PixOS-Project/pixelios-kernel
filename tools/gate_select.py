#!/usr/bin/env python3
"""Step selection for `tools/check-all.sh --quick`.
Usage: gate_select.py STAMP_FILE   -> prints the steps to run (space separated), or FULL when there is no usable stamp."""
import pathlib, re, subprocess, sys

ALL = ["build", "build-fast", "build-usb", "build-wlan", "build-gpu", "build-gpupower", "build-gpuclk", "build-gpurails", "build-gpurailon", "build-gpujob", "build-touch", "build-udump", "host-tests", "boot-safety",
       "unit", "phone-state", "hang-recover", "probe-run", "rootless", "public-split", "validate", "shellcheck"]
SHIM = ALL[:14]
REPO = pathlib.Path(__file__).resolve().parent.parent
BUILD_SH = REPO / "shim" / "build.sh"


def shim_build_inputs():
    """build.sh reads xnu patches (host-test headers: `../xnu/patches/NNNN-...`) and runs repo tools (`../tools/boards.py` ...): read both from build.sh itself."""
    try:
        text = BUILD_SH.read_text()
    except OSError:
        return ()
    return tuple(sorted({m for m in re.findall(r"\.\./(tools/[\w.-]+|xnu/patches/\d{4})", text)}))


SHIM_PREFIX = ("shim/", "soc/", "device/", "tools/imgmeta", "tools/mk", "tools/test-bluejay-boot.py",
               "tools/check-all.sh", "tools/gate_select.py", "tools/audit_memo.py") + shim_build_inputs()
UNIT_PREFIX = ("tools/", "soc/", "device/", "qrlog/", "xnu/qr/", "xnu/patches/0081", "xnu/patches/0086", "xnu/patches/0094")


def is_shell(f):
    """Same rule as tools/shell-files: *.sh or a sh/bash/dash shebang on line 1 (the file may be gone: deleted files select nothing)."""
    if f.endswith(".sh"):
        return True
    try:
        with open(REPO / f, "rb") as fh:
            first = fh.readline(80)
    except OSError:
        return False
    return re.match(rb"#!\s*(/usr/bin/env\s+)?(/[a-z/]*/)?(ba|da)?sh(\s|$)", first) is not None


def select(files):
    """Steps needed for this list of changed paths. validate (patch numbering, headers, script syntax) and public-split (every
    tracked file assigned to a public repo, ~1 s) always run."""
    need = {"validate", "public-split"}
    for f in files:
        if f.startswith(SHIM_PREFIX) and not (f.startswith("shim/") and f.endswith(".md")):
            need.update(SHIM)
        if is_shell(f):
            need.add("shellcheck")
        if f.startswith(UNIT_PREFIX):
            need.update(("unit", "phone-state", "hang-recover", "probe-run", "rootless"))
    return [s for s in ALL if s in need]


def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True)


def base_from_stamp(path):
    try:
        base = open(path).read().strip()
    except OSError:
        return None
    ok = base and git("cat-file", "-e", base + "^{commit}").returncode == 0 and \
        git("merge-base", "--is-ancestor", base, "HEAD").returncode == 0
    return base if ok else None


def changed(base):
    tracked = git("diff", "--name-only", base).stdout.split()
    new = git("ls-files", "--others", "--exclude-standard").stdout.split()
    return tracked + new


if __name__ == "__main__":
    b = base_from_stamp(sys.argv[1])
    print("FULL" if b is None else " ".join(select(changed(b))))
