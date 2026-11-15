#!/usr/bin/env python3
"""tools/lint-silent: fixtures (the pre-fix SweetSurface stub and a switch with no logging default must FAIL), allow fixtures, the allowlist pins, the real tree, and mutants of the lint itself (rule 8, AP-102)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.join(TOOLS, "lint-silent")

# the SweetSurface answers of the stack before the GPU stream's fix: selectors 6/7 answered Unsupported, the default accepts and ignores, a key id is dropped; no line anywhere (AP-102)
PREFIX_SWEETSURFACE = (
    "IOReturn SweetSurface::externalMethod(uint32_t selector, IOExternalMethodArguments *args)\n{\n\tswitch (selector) {\n\tcase 0:\n\t\treturn create(args);\n"
    "\tcase 6:\n\tcase 7:\n\t\treturn kIOReturnUnsupported;\n\tdefault:\n\t\treturn kIOReturnSuccess;\t/* accept and ignore */\n\t}\n}\n"
    "IOReturn SweetSurface::setProp(uint32_t key, uint64_t v)\n{\n\tswitch (key) {\n\tcase kIOSurfaceWidth:\n\t\twidth = v;\n\t\tbreak;\n\tcase kIOSurfaceAddress:\n\t\tbreak;\t/* ignored */\n\t}\n\treturn 0;\n}\n")
SWITCH_NO_LOG_DEFAULT = "int dispatch(int cmd)\n{\n\tswitch (cmd) {\n\tcase 1:\n\t\treturn 10;\n\tcase 2:\n\t\treturn 20;\n\tdefault:\n\t\tbreak;\n\t}\n\treturn 0;\n}\n"

# (relative path, content, kinds that must be found in it ("" = must be clean))
FIXTURES = [
    ("shim/a_prefix.cpp", PREFIX_SWEETSURFACE, ("C-UNSUPPORTED", "C-SWITCH")),
    ("shim/b_switch.c", SWITCH_NO_LOG_DEFAULT, ("C-SWITCH",)),
    ("shim/c_nodefault.c", "int h(int sel)\n{\n\tswitch (sel) {\n\tcase 1: return 0;\n\tcase 2: return 1;\n\t}\n\treturn 0;\n}\n", ("C-SWITCH",)),
    ("shim/d_enosys.c", "int g(void)\n{\n\treturn ENOSYS;\n}\n", ("C-UNSUPPORTED",)),
    ("shim/e_enotsup_assign.c", "int g(void)\n{\n\tint ret;\n\tret = ENOTSUP;\n\treturn ret;\n}\n", ("C-UNSUPPORTED",)),
    ("shim/f_eopnotsupp.c", "int g(void)\n{\n\treturn -EOPNOTSUPP;\n}\n", ("C-UNSUPPORTED",)),
    ("shim/g_notimpl.c", "int j(void)\n{\n\t/* cycle learning is not implemented. */\n\treturn 0;\n}\n", ("C-NOT-IMPL",)),
    ("shim/h_table.c", "int k(int key)\n{\n\tfor (int i = 0; i < ARRAY_SIZE(tbl); i++)\n\t\tif (tbl[i].key == key)\n\t\t\treturn i;\n\treturn -1;\n}\n", ("C-TABLE",)),
    ("shim/i_logged.c", "int f(int sel)\n{\n\tif (sel == 3) {\n\t\tswl_gap(\"sel\", 3);\n\t\treturn kIOReturnUnsupported;\n\t}\n\treturn 0;\n}\n", ()),
    ("shim/j_noted.c", "int f(int sel)\n{\n\tsweet_note_ignored(\"sel\", sel);\n\treturn ENOTSUP;\n}\n", ()),
    ("shim/k_printed.c", "int f(void)\n{\n\tprintf(\"SWEET GAP: sel 6 refused\\n\");\n\treturn EOPNOTSUPP;\n}\n", ()),
    ("shim/l_switch_ok.c", "int h(int sel)\n{\n\tswitch (sel) {\n\tcase 1: return 0;\n\tdefault:\n\t\tprintf(\"SWEET GAP: sel %d\\n\", sel);\n\t\treturn -1;\n\t}\n}\n", ()),
    ("shim/m_switch_tail_ok.c", "int h(int sel)\n{\n\tswitch (sel) {\n\tcase 1: return 0;\n\t}\n\tcon_str(\"X GAP:\", \"unhandled selector\");\n\treturn -1;\n}\n", ()),
    ("shim/n_switch_not_selector.c", "int h(int mode)\n{\n\tswitch (mode) {\n\tcase 1: return 0;\n\tdefault: break;\n\t}\n\treturn 0;\n}\n", ()),
    ("shim/o_comparison.c", "int f(int e)\n{\n\tif (e == ENOTSUP)\n\t\treturn 0;\n\treturn e;\n}\n", ()),
    ("shim/p_string_only.c", "int f(void)\n{\n\tprintf(\"never returns ENOTSUP\\n\");\n\treturn 0;\n}\n", ()),
    ("shim/q_table_ok.c", "int k(int key)\n{\n\tfor (int i = 0; i < ARRAY_SIZE(tbl); i++)\n\t\tif (tbl[i].key == key)\n\t\t\treturn i;\n\tprintf(\"X GAP: key %d\\n\", key);\n\treturn -1;\n}\n", ()),
    ("shim/r_table_walk.c", "int k(void)\n{\n\tfor (int i = 0; i < ARRAY_SIZE(fields); i++)\n\t\tput(fields[i].key, fields[i].value);\n\treturn 0;\n}\n", ()),
    ("shim/s_panic_notimpl.c", "void p(void)\n{\n\tpanic(\"SWEET CPU: resume is not implemented\");\n}\n", ()),
    ("shim/t_event_type_hotkey.c", "int s(int type)\n{\n\tswitch (type) {\n\tcase kIOHIDEventTypeSymbolicHotKey: return 1;\n\tdefault: break;\n\t}\n\treturn 0;\n}\n", ()),
    # F8: a string that is only stored, a log in dead code, an unrelated log far after the switch, a nested default: none of these is the log
    ("shim/u_string_stored.c", "int f(int sel)\n{\n\tconst char *u = \"SWEET GAP: x\";\n\t(void)u;\n\treturn kIOReturnUnsupported;\n}\n", ("C-UNSUPPORTED",)),
    ("shim/v_dead_if0.c", "int f(int sel)\n{\n\tif (0) printf(\"X GAP: a\\n\");\n\treturn ENOSYS;\n}\n", ("C-UNSUPPORTED",)),
    ("shim/w2_dead_swl.c", "int f(int sel)\n{\n\tif (0) swl_gap(\"sel\", 3);\n\treturn ENOSYS;\n}\n", ("C-UNSUPPORTED",)),
    ("shim/w_dead_pp.c", "int f(int sel)\n{\n#if 0\n\tprintf(\"X GAP: a\\n\");\n#endif\n\treturn ENOSYS;\n}\n", ("C-UNSUPPORTED",)),
    ("shim/x_far_gap.c", "int h(int sel)\n{\n\tswitch (sel) {\n\tcase 1: return 0;\n\t}\n" + "\tx++;\n" * 12 + "\tprintf(\"X GAP: later\\n\");\n\treturn -1;\n}\n", ("C-SWITCH",)),
    ("shim/y_nested_default.c", "int h(int sel, int mode)\n{\n\tswitch (sel) {\n\tcase 1:\n\t\tswitch (mode) {\n\t\tdefault: printf(\"X GAP: mode\\n\");\n\t\t}\n\t\treturn 0;\n\t}\n\treturn 1;\n}\n", ("C-SWITCH",)),
    ("shim/z_own_default_nested_silent.c", "int h(int sel, int mode)\n{\n\tswitch (sel) {\n\tcase 1:\n\t\tswitch (mode) {\n\t\tdefault: break;\n\t\t}\n\t\treturn 0;\n\tdefault:\n\t\tprintf(\"X GAP: sel %d\\n\", sel);\n\t\treturn -1;\n\t}\n}\n", ()),
    ("xnu/patches/0001-ctx-only.patch", "--- a/IOKit/X.cpp\n+++ b/IOKit/X.cpp\n@@ -10,4 +10,4 @@ IOReturn X::m(int sel)\n {\n \tif (sel == 3)\n \t\treturn kIOReturnUnsupported;\n \treturn 0;\n }\n", ()),
    ("xnu/patches/0002-added.patch", "--- a/IOKit/X.cpp\n+++ b/IOKit/X.cpp\n@@ -10,4 +10,6 @@ IOReturn X::m(int sel)\n {\n \tif (sel == 3)\n+\t\treturn kIOReturnUnsupported;\n \treturn 0;\n }\n", ("C-UNSUPPORTED",)),
    ("xnu/patches/0003-added-logged.patch", "--- a/IOKit/X.cpp\n+++ b/IOKit/X.cpp\n@@ -10,4 +10,6 @@ IOReturn X::m(int sel)\n {\n \tif (sel == 3)\n+\t\treturn (printf(\"SWEET GAP: 3\\n\"), kIOReturnUnsupported);\n \treturn 0;\n }\n", ()),
    # a hunk that starts inside a /* */ comment: the words in it are prose, not code
    ("xnu/patches/0004-mid-comment.patch", "--- a/IOKit/X.cpp\n+++ b/IOKit/X.cpp\n@@ -10,4 +10,5 @@\n  *   sel 4   lookup\n+ *   sel 6,7: return kIOReturnUnsupported\n  */\n int x;\n", ()),
    ("xnu/patches/0005-switch-added.patch", "--- /dev/null\n+++ b/bsd/X.c\n@@ -0,0 +1,9 @@\n+int d(int cmd)\n+{\n+\tswitch (cmd) {\n+\tcase 1: return 1;\n+\tdefault: break;\n+\t}\n+\treturn 0;\n+}\n", ("C-SWITCH",)),
    ("xnu/patches/0006-switch-ctx-header.patch", "--- a/bsd/X.c\n+++ b/bsd/X.c\n@@ -1,6 +1,7 @@ int d(int cmd)\n int d(int cmd)\n {\n \tswitch (cmd) {\n+\tcase 3: return 3;\n \tdefault: break;\n \t}\n \treturn 0;\n }\n", ()),
    ("xnu/patches/0007-gap-in-context.patch", "--- a/bsd/X.c\n+++ b/bsd/X.c\n@@ -1,6 +1,7 @@\n int f(int sel)\n {\n \tprintf(\"SWEET GAP: x\\n\");\n+\tif (sel == 4) return ENOTSUP;\n \treturn 0;\n }\n", ()),
    ("tools/n_bareret.py", "def f():\n    try:\n        return x()\n    except OSError:\n        return\n", ("PY-EXCEPT",)),
    ("tools/n_kill_assign.sh", "#!/bin/bash\nst=$(kill -0 \"$pid\" 2>&1 || true)\n", ()),
    ("tools/a_bare.py", "try:\n    x()\nexcept:\n    pass\n", ("PY-EXCEPT",)),
    ("tools/b_ret.py", "def f():\n    try:\n        return x()\n    except OSError:\n        return None\n", ("PY-EXCEPT",)),
    ("tools/c_cont.py", "for i in y:\n    try:\n        x(i)\n    except ValueError:\n        continue\n", ("PY-EXCEPT",)),
    ("tools/d_logged.py", "try:\n    x()\nexcept OSError as e:\n    print('X GAP: %s' % e)\n", ()),
    ("tools/e_raise.py", "try:\n    x()\nexcept OSError:\n    raise\n", ()),
    ("tools/f_signal.py", "import queue\ntry:\n    x()\nexcept queue.Empty:\n    pass\n", ()),
    ("tools/o_marker.py", "try:\n    x()\nexcept FileNotFoundError:   # lint-silent: expected the file is optional, absent is the normal answer\n    pass\n", ()),
    ("tools/p_marker_broad.py", "try:\n    x()\nexcept Exception:   # lint-silent: expected everything is normal here, trust me\n    pass\n", ("PY-EXCEPT",)),
    ("tools/q_marker_short.py", "try:\n    x()\nexcept FileNotFoundError:   # lint-silent: expected ok\n    pass\n", ("PY-EXCEPT",)),
    ("tools/r_marker_bare.py", "try:\n    x()\nexcept:   # lint-silent: expected everything is normal here, trust me\n    pass\n", ("PY-EXCEPT",)),
    # F2: a file the lint cannot read is a PARSE finding, never a clean file
    ("tools/s_syntax.py", "print 'x'\n", ("PARSE",)),
    # F3: the roots that ship C or scripts (soc/*/shim, qrlog, xnu/qr templates, docs/golden); tools/*_c.c host tests are not firmware
    ("soc/google/gs101/shim/ufsx.c", "int g(void)\n{\n\treturn ENOSYS;\n}\n", ("C-UNSUPPORTED",)),
    ("qrlog/q.c", "int g(void)\n{\n\treturn ENOSYS;\n}\n", ("C-UNSUPPORTED",)),
    ("xnu/qr/t.c.in", "int g(void)\n{\n\treturn ENOSYS;\n}\n", ("C-UNSUPPORTED",)),
    ("docs/golden/g.sh", "#!/bin/bash\nn=$(grep -c x f || true)\n", ("SH-OR-TRUE",)),
    ("tools/test_x_c.c", "int g(void)\n{\n\treturn ENOSYS;\n}\n", ()),
    ("tools/blob", "\0\1\2 not text\n", ()),
    ("tools/blob2", "#!/bin/bash\n\0\nn=$(grep x f || true)\n", ()),   # a NUL in the head: binary, not a script
    # F9: a constant answer, a lone constant assignment, contextlib.suppress and a timeout around more than one blocking read are as silent as `pass`
    ("tools/t_ret_false.py", "def f():\n    try:\n        return x()\n    except Exception:\n        return False\n", ("PY-EXCEPT",)),
    ("tools/u_ret_empty.py", "def f():\n    try:\n        return x()\n    except KeyError:\n        return {}\n", ("PY-EXCEPT",)),
    ("tools/v_assign_none.py", "def f():\n    try:\n        v = x()\n    except OSError:\n        v = None\n    return v\n", ("PY-EXCEPT",)),
    ("tools/w_suppress.py", "import contextlib\nwith contextlib.suppress(Exception):\n    x()\n", ("PY-EXCEPT",)),
    ("tools/w2_suppress_named.py", "from contextlib import suppress\nwith suppress(OSError):\n    x()\n", ("PY-EXCEPT",)),
    ("tools/w3_suppress_marked.py", "from contextlib import suppress\nwith suppress(FileNotFoundError):   # lint-silent: expected the cache file may not exist yet\n    x()\n", ()),
    ("tools/w4_suppress_marked_broad.py", "from contextlib import suppress\nwith suppress(OSError, Exception):   # lint-silent: expected everything may fail here, trust me\n    x()\n", ("PY-EXCEPT",)),
    ("tools/x_timeout_long.py", "try:\n    s.recv(4096)\n    s.send(b)\nexcept TimeoutError:\n    pass\n", ("PY-EXCEPT",)),
    ("tools/y_timeout_recv.py", "def f(s):\n    try:\n        return s.recv(4096)\n    except TimeoutError:\n        return None\n", ()),
    ("tools/z_truthy_sentinel.py", "def f():\n    try:\n        return x()\n    except KeyError:\n        return 'missing'\n", ()),
    ("tools/z2_marked_ret.py", "def f():\n    try:\n        return x()\n    except ProcessLookupError:   # lint-silent: expected the pid is gone: not alive\n        return False\n", ()),
    ("tools/g_assign.sh", "#!/bin/bash\nn=$(grep -c x f || true)\n", ("SH-OR-TRUE",)),
    ("tools/h_tested.sh", "#!/bin/bash\nfoo > out.txt || true\n[ -s out.txt ] && echo ok\n", ("SH-OR-TRUE",)),
    ("tools/i_cleanup.sh", "#!/bin/bash\nrm -f x || true\nmkdir -p y || true\nkill $p || true\n", ()),
    ("tools/j_plain.sh", "#!/bin/bash\nfoo || true\necho done\n", ()),
    ("tools/k_stderr_redirect.sh", "#!/bin/bash\nA cat /proc/cmdline 2> \"$ie\" | tr x y | { grep -v z || true; } | tee o\n[ -s \"$ie\" ] && echo e\n", ()),
    ("tools/l_shift.sh", "#!/bin/bash\nmode=${1:-}; shift || true\n", ()),
    ("tools/m_gapf.sh", "#!/bin/bash\nn=$(grep -c x f) || { rc=$?; [ \"$rc\" -eq 1 ] || echo \"m GAP: grep rc=$rc\" >&2; }\n", ()),
    ("tools/n2_tested_far.sh", "#!/bin/bash\nfoo > out.txt || true\n" + "echo a\n" * 8 + "[ -s out.txt ] && echo ok\n", ("SH-OR-TRUE",)),
    ("tools/n3_continuation.sh", "#!/bin/bash\nn=$(foo \\\n    || true)\n", ("SH-OR-TRUE",)),
    ("tools/n4_exit0.sh", "#!/bin/bash\nn=$(foo || exit 0)\n", ("SH-OR-TRUE",)),
    ("tools/n5_kill_in_url.sh", "#!/bin/bash\ncurl http://h/kill > out.txt || true\n[ -s out.txt ] && echo ok\n", ("SH-OR-TRUE",)),
    ("tools/n6_rm_after.sh", "#!/bin/bash\nfoo > out.txt || true; rm -f z\n[ -s out.txt ] && echo ok\n", ("SH-OR-TRUE",)),
    ("tools/n8_grep_use.sh", "#!/bin/bash\nfoo > r.txt || true\ngrep -q ok r.txt && echo ok\n", ("SH-OR-TRUE",)),
    ("tools/n10_bin_true.sh", "#!/bin/bash\nn=$(foo || /bin/true)\n", ("SH-OR-TRUE",)),
    ("tools/n11_semi_true.sh", "#!/bin/bash\nfoo > out.txt; true\n[ -s out.txt ] && echo ok\n", ("SH-OR-TRUE",)),
    ("tools/n12_far_clean.sh", "#!/bin/bash\nfoo > out.txt || true\n" + "echo a\n" * 14 + "[ -s out.txt ] && echo ok\n", ()),
    ("tools/n13_plain_exit0.sh", "#!/bin/bash\nfoo || exit 0\n", ()),
]


def lint(lintpath, root):
    return subprocess.run([sys.executable, "-I", lintpath, "--root", root], capture_output=True, text=True, timeout=120)


def write_tree(root, files, allow=""):
    for p, body in files:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(body)
    os.makedirs(os.path.join(root, "tools"), exist_ok=True)
    with open(os.path.join(root, "tools", "lint-silent-allow.txt"), "w") as f:
        f.write(allow)


def check_fixtures(lintpath, root):
    """[] when every fixture file shows exactly the kinds it must; else the mismatches (used both for the tree and for the mutants)."""
    out = lint(lintpath, root)
    bad = []
    for rel, _, kinds in FIXTURES:
        lines = [l for l in out.stdout.splitlines() if (" %s:" % rel) in l or (" %s " % rel) in l]
        found = {l.split()[0] for l in lines}
        if kinds and not set(kinds) <= found:
            bad.append("%s: want %s found %s" % (rel, kinds, sorted(found)))
        if not kinds and found:
            bad.append("%s: want clean found %s" % (rel, sorted(found)))
    if out.returncode != 1:
        bad.append("rc=%d, want 1 (fixtures contain findings)" % out.returncode)
    return bad


ALLOW_BAD = [("shim/x.c", "int g(void)\n{\n\treturn ENOSYS;\n}\n")]


def pin_sig(files):
    """the sig= the lint itself computes for shim/x.c of FILES (tools/lint-silent --sigs)"""
    with tempfile.TemporaryDirectory() as d:
        write_tree(d, files)
        o = subprocess.run([sys.executable, "-I", LINT, "--sigs", "--root", d], capture_output=True, text=True, timeout=60).stdout
    return next(l.split("sig=")[1] for l in o.splitlines() if l.startswith("shim/x.c "))


SIG = pin_sig(ALLOW_BAD)
R = "stub kept until the driver exists"
SWAPPED = [("shim/x.c", "int g(void)\n{\n\treturn ENOTSUP;\n}\n")]   # one finding, but another one than the pinned ENOSYS
ALLOW_CASES = [
    # (label, files, allowlist, rc, text that must appear)
    ("allowlisted", ALLOW_BAD, "shim/x.c hits=1 sig=%s %s\n" % (SIG, R), 0, "allowed=1"),
    ("grew", [("shim/x.c", ALLOW_BAD[0][1] + "int h(void)\n{\n\treturn ENOTSUP;\n}\n")], "shim/x.c hits=1 sig=%s %s\n" % (SIG, R), 1, "ALLOW-GREW"),
    ("shrank", ALLOW_BAD, "shim/x.c hits=2 sig=%s %s\n" % (SIG, R), 1, "ALLOW-SHRUNK"),
    ("stale", [("shim/x.c", "int g(void) { return 0; }\n")], "shim/x.c hits=1 sig=%s %s\n" % (SIG, R), 1, "ALLOW-SHRUNK"),
    ("swap: fix one, add another, same count", SWAPPED, "shim/x.c hits=1 sig=%s %s\n" % (SIG, R), 1, "ALLOW-CHANGED"),
    ("no reason", ALLOW_BAD, "shim/x.c hits=1 sig=%s\n" % SIG, 1, "ALLOW-NO-REASON"),
    ("short reason", ALLOW_BAD, "shim/x.c hits=1 sig=%s x\n" % SIG, 1, "ALLOW-NO-REASON"),
    ("whitespace reason", ALLOW_BAD, "shim/x.c hits=1 sig=%s               \n" % SIG, 1, "ALLOW-NO-REASON"),
    ("no sig", ALLOW_BAD, "shim/x.c hits=1 %s\n" % R, 1, "ALLOW-NO-SIG"),
    ("no pin", ALLOW_BAD, "shim/x.c %s\n" % R, 1, "ALLOW-NO-PIN"),
    ("missing file", [], "shim/gone.c hits=1 sig=%s removed a long time ago\n" % SIG, 1, "ALLOW-MISSING-FILE"),
    ("duplicate", ALLOW_BAD, "shim/x.c hits=1 sig=%s first reason text\nshim/x.c hits=1 sig=%s second reason text\n" % (SIG, SIG), 1, "ALLOW-DUPLICATE"),
]

# mutants of the lint: (id, old, new); each applied to a scratch copy, the fixture battery or an allow case must then fail
MUTANTS = [
    ("m-enosys-dropped", "kIOReturnUnsupported|ENOTSUP|EOPNOTSUPP|ENOSYS", "kIOReturnUnsupported|ENOTSUP|EOPNOTSUPP"),
    ("m-return-form", r"(?:\breturn\b[^;{}]*?|(?<![=!<>])=(?!=)[^;{}=]*?)", r"(?:(?<![=!<>])=(?!=)[^;{}=]*?)"),
    ("m-assign-form", r"|(?<![=!<>])=(?!=)[^;{}=]*?)\b(%s)\b", r")\b(%s)\b"),
    ("m-comparison-counts", "(?<![=!<>])=(?!=)", "="),
    ("m-gapcall-swl", r"(?:swl_gap|sweet_note_ignored)", r"(?:sweet_note_ignored)"),
    ("m-gapcall-note", r"(?:swl_gap|sweet_note_ignored)", r"(?:swl_gap)"),
    ("m-gap-string", '        for m in re.finditer(r"GAP:", strs[s:e]):', "        for m in []:"),
    ("m-unsupported-logged-ignored", "        logged = gap_in(s, e)\n        if not logged:", "        logged = False\n        if not logged:"),
    ("m-added-ignored", "                if isadd(m.start(1)):", "                if True:"),
    ("m-notimpl-gone", 'r"not[\\s*/]+implemented"', 'r"zzznotimplementedzzz"'),
    ("m-panic-not-loud", "if not PANIC_RE.search(code, s, e):", "if True:"),
    ("m-switch-default-ok", "            ok = gap_in(sec_s, sec_e)", "            ok = True"),
    ("m-switch-tail-ok", "            ok = gap_in(be, min(tail_e, nl10))", "            ok = True"),
    ("m-switch-header-added", "        if not isadd(hdr_off):\n            continue", "        pass"),
    ("m-switch-words", '"sel", "selector", "selectors", "key", "tag", "method", "cmd", "command", "ioctl", "opcode", "request", "brequest", "req"', '"zzz"'),
    ("m-label-key", 'LABEL_WORDS = SEL_WORDS - {"key", "tag"}', "LABEL_WORDS = SEL_WORDS"),
    ("m-table-compare", r'f = re.search(el + r"\s*[=!]=|[=!]=\s*" + el + r"|\b(?:strn?c?a?s?e?cmp|memcmp)\s*\([^;]*?" + el, body)', r"f = re.search(el, body)"),
    ("m-table-gone", "                if f and words(fld) & FIELD_WORDS and isadd(s + m.start()):", "                if False:"),
    ("m-mid-comment", "    if mid_comment and re.match", "    if False and re.match"),
    ("m-py-continue", "isinstance(st, (ast.Pass, ast.Continue, ast.Break))", "isinstance(st, (ast.Pass,))"),
    ("m-py-return-none", "if isinstance(st, ast.Return) and (st.value is None", "if isinstance(st, ast.Return) and (False"),
    ("m-py-signals", "if what in PY_SIGNALS or (what in PY_TIMEOUTS and single_poll(nd.body)):", "if what in PY_TIMEOUTS and single_poll(nd.body):"),
    ("m-marker-gone", "    return bool(mk) and len(mk.group(1).strip()) >= MIN_REASON and what not in BROAD", "    return False"),
    ("m-marker-reason", "    return bool(mk) and len(mk.group(1).strip()) >= MIN_REASON and what not in BROAD", "    return bool(mk) and what not in BROAD"),
    ("m-marker-broad", "    return bool(mk) and len(mk.group(1).strip()) >= MIN_REASON and what not in BROAD", "    return bool(mk) and len(mk.group(1).strip()) >= MIN_REASON"),
    ("m-parse-py", "[(getattr(_err, \"lineno\", None) or 1, \"PARSE\", \"not scanned (%s: %s): fix the file so the lint can read it\" % (type(_err).__name__, _err))]\n    lines = text.split", "[]\n    lines = text.split"),
    ("m-parse-counted", "        if not any(k == \"PARSE\" for _, k, _ in f):", "        if True:"),
    ("m-root-soc", ' "soc", "qrlog", "xnu/qr", "docs/golden")\nFULL', ' "qrlog", "xnu/qr", "docs/golden")\nFULL'),
    ("m-root-qrlog", ' "soc", "qrlog", "xnu/qr", "docs/golden")\nFULL', ' "soc", "xnu/qr", "docs/golden")\nFULL'),
    ("m-root-xnuqr", ' "soc", "qrlog", "xnu/qr", "docs/golden")\nFULL', ' "soc", "qrlog", "docs/golden")\nFULL'),
    ("m-root-golden", ' "soc", "qrlog", "xnu/qr", "docs/golden")\nFULL', ' "soc", "qrlog", "xnu/qr")\nFULL'),
    ("m-c-in", "rel.endswith(C_EXT + C_IN)", "rel.endswith(C_EXT)"),
    ("m-c-roots", 'C_ROOTS = ("shim/", "soc/", "qrlog/", "xnu/qr/")', 'C_ROOTS = ("shim/",)'),
    ("m-binary-skip", '            if b"\\0" in raw:', "            if False:"),
    ("m-full-depth", "if d not in FULL_DEPTH and dp != top:", "if dp != top:"),
    ("m-allow-sig", "            elif now != sig:", "            elif False:"),
    ("m-allow-nosig", "        if not sg:", "        if False:"),
    ("m-gap-incall", "            if live(p) and q >= 0 and in_call(q):", "            if live(p) and q >= 0:"),
    ("m-gap-dead-call", "            if live(m.start()):", "            if True:"),
    ("m-gap-dead-str", "            if live(p) and q >= 0 and in_call(q):", "            if q >= 0 and in_call(q):"),
    ("m-dead-pp", 'dead += [', 'dead = dead[:0] + ['),
    ("m-depth1", "            if marks[m.start()] == 1:", "            if True:"),
    ("m-switch-window", "            ok = gap_in(be, min(tail_e, nl10))", "            ok = gap_in(be, tail_e)"),
    ("m-py-const-return", "if isinstance(st, ast.Return) and (st.value is None or const_value(st.value)):", "if isinstance(st, ast.Return) and (st.value is None):"),
    ("m-py-const-assign", "const_value(st.value):   # `except KeyError: v = None`", "False:"),
    ("m-py-const-falsy", "(isinstance(v, ast.Constant) and not v.value) or", "isinstance(v, ast.Constant) or"),
    ("m-py-suppress", "                if isinstance(c, ast.Call) and (getattr(c.func, \"id\", None) == \"suppress\" or getattr(c.func, \"attr\", None) == \"suppress\"):", "                if False:"),
    ("m-py-timeout-always", "(what in PY_TIMEOUTS and single_poll(nd.body))", "(what in PY_TIMEOUTS)"),
    ("m-py-timeout-single", "    if len(body) != 1:\n        return False", "    if len(body) < 1:\n        return False"),
    ("m-py-marker-suppress", "                    if marked(lines, nd.lineno, \"Exception\" if set(what.split(\", \")) & (BROAD | {\"(nothing)\"}) else what):", "                    if marked(lines, nd.lineno, what):"),
    ("m-sh-cleanup", "if CLEANUP.search(seg):", "if False:"),
    ("m-sh-cleanup-whole-line", "if CLEANUP.search(seg):", "if CLEANUP.search(l):"),
    ("m-sh-continuation", "ll = logical_lines(lines)", "ll = [(i + 1, x) for i, x in enumerate(lines)]"),
    ("m-sh-window", "TESTED_WINDOW = 12", "TESTED_WINDOW = 3"),
    ("m-sh-window-unbounded", "TESTED_WINDOW = 12", "TESTED_WINDOW = 99"),
    ("m-sh-exit0", "|:|exit\\s+0)\\b|", "|:)\\b|"),
    ("m-sh-bintrue", "(?:(?:/usr)?(?:/bin/)?true|:|exit", "(?:true|:|exit"),
    ("m-sh-semi-true", "|;\\s*(?:(?:/usr)?(?:/bin/)?true|:)\\s*(?:$|[)}#])", ""),
    ("m-sh-grep-use", "\\b(?:test|grep|case)\\s", "\\b(?:test)\\s"),
    ("m-sh-assign", "        if a or tested:", "        if tested:"),
    ("m-sh-tested", "        if a or tested:", "        if a:"),
    ("m-sh-stderr-redirect", r'(?<![\d&])>>?\s*(\S+)', r'>>?\s*(\S+)'),
    ("m-sh-assign-segment", r're.split(r";|&&|\|\|\s*\{?|\{", head)[-1]', "head"),
    ("m-allow-grew", "            if len(fl) > pin:", "            if False:"),
    ("m-allow-shrunk", "            elif len(fl) < pin:", "            elif False:"),
    ("m-allow-stale", "        if rel not in res:", "        if False:"),
    ("m-allow-reason", "len((sg.group(2) or \"\").strip()) < MIN_REASON", "False"),
    ("m-allow-pin", "        if not pin:", "        if False:"),
    ("m-allow-dup", "        if parts[0] in allow:", "        if False:"),
    ("m-allow-missing-file", "        if not os.path.isfile(os.path.join(root, parts[0])):", "        if False:"),
    ("m-exit-code", 'return 1 if findings else 0\n\n\ndef print_sigs', 'return 0\n\n\ndef print_sigs'),
    ("m-patch-only-added", "        elif hunk_ok and cur is not None and l[:1] in (\"+\", \" \")", "        elif hunk_ok and cur is not None and l[:1] in (\"+\",)"),
]


class T(unittest.TestCase):
    def test_self_test(self):
        p = subprocess.run([sys.executable, "-I", LINT, "--self-test"], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("RESULT: PASS self-test", p.stdout)

    def test_usage_is_exit_2(self):
        p = subprocess.run([sys.executable, "-I", LINT, "--bogus"], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2)

    def test_fixtures(self):
        """(a) the pre-fix SweetSurface stub and (b) a switch with a non-logging default FAIL; every allow fixture passes."""
        with tempfile.TemporaryDirectory() as d:
            write_tree(d, [(p, b) for p, b, _ in FIXTURES])
            bad = check_fixtures(LINT, d)
            self.assertEqual(bad, [], "\n".join(bad))
            out = lint(LINT, d).stdout
            self.assertIn("C-SWITCH shim/b_switch.c:3", out)
            self.assertIn("C-UNSUPPORTED shim/a_prefix.cpp:8", out)
            self.assertIn("RESULT: FAIL silent", out)

    def test_prefix_sweetsurface_alone_fails(self):
        with tempfile.TemporaryDirectory() as d:
            write_tree(d, [("shim/SweetSurface.cpp", PREFIX_SWEETSURFACE)])
            p = lint(LINT, d)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("C-UNSUPPORTED shim/SweetSurface.cpp:8", p.stdout)
            self.assertIn("C-SWITCH shim/SweetSurface.cpp:3", p.stdout)

    def test_every_line_names_file_line_and_do_instead(self):
        with tempfile.TemporaryDirectory() as d:
            write_tree(d, [(p, b) for p, b, _ in FIXTURES])
            for l in lint(LINT, d).stdout.splitlines():
                if l.startswith("RESULT"):
                    continue
                self.assertRegex(l, r"^[A-Z-]+ \S+:\d+ ", l)
                self.assertRegex(l, r"(add|log|print|check|say|narrow|implement|test|drop)", l)

    def test_allowlist(self):
        for label, files, allow, rc, want in ALLOW_CASES:
            with tempfile.TemporaryDirectory() as d:
                write_tree(d, files, allow)
                p = lint(LINT, d)
                self.assertEqual(p.returncode, rc, label + "\n" + p.stdout)
                self.assertIn(want, p.stdout, label)
        with tempfile.TemporaryDirectory() as d:
            write_tree(d, [])
            os.remove(os.path.join(d, "tools", "lint-silent-allow.txt"))
            p = lint(LINT, d)
            self.assertEqual(p.returncode, 1)
            self.assertIn("MISSING-ALLOWLIST", p.stdout)

    def test_unreadable_file_is_a_finding_not_a_scanned_file(self):
        """F2: a .py with a syntax error and a shell script that is not UTF-8 FAIL with PARSE and do not count in files=N"""
        with tempfile.TemporaryDirectory() as d:
            write_tree(d, [("tools/good.py", "x = 1\n"), ("tools/bad.py", "print 'x'\n")])
            with open(os.path.join(d, "tools", "latin.sh"), "wb") as f:
                f.write(b"#!/bin/bash\necho \xff\xfe\n")
            p = lint(LINT, d)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("PARSE tools/bad.py:1", p.stdout)
            self.assertIn("PARSE tools/latin.sh:1", p.stdout)
            self.assertIn("files=1 ", p.stdout)

    def test_real_tree(self):
        """the real tree has no finding except the Wi-Fi ones that stay open for fix-21 (21y): 0132/0133 'not implemented' in sweet_wlan.c; the allowlist pins everything else."""
        root = os.path.dirname(TOOLS)
        p = lint(LINT, root)
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        for l in p.stdout.splitlines():
            if l.startswith("RESULT"):
                continue
            self.assertRegex(l, r"xnu/patches/013[23]-sweet-wlan", "unexpected finding on the real tree: " + l)

    def test_mutants_of_the_lint_are_killed(self):
        """each mutant of tools/lint-silent (scratch copy) must break the fixture battery or an allow case (rule 8: a lint nobody can fail is no lint)."""
        with open(LINT) as f:
            src = f.read()
        survived = []
        with tempfile.TemporaryDirectory() as scratch:
            fx = os.path.join(scratch, "fx")
            write_tree(fx, [(p, b) for p, b, _ in FIXTURES])
            allow_roots = []
            for i, (label, files, allow, rc, want) in enumerate(ALLOW_CASES):
                r = os.path.join(scratch, "al%d" % i)
                write_tree(r, files, allow)
                allow_roots.append((r, rc, want))
            parse_root = os.path.join(scratch, "parse")
            write_tree(parse_root, [("tools/good.py", "x = 1\n"), ("tools/bad.py", "print 'x'\n")])
            nofile = os.path.join(scratch, "nofile")
            write_tree(nofile, [])
            os.remove(os.path.join(nofile, "tools", "lint-silent-allow.txt"))
            self.assertEqual(check_fixtures(LINT, fx), [], "unmutated lint must pass the battery first")
            for mid, old, new in MUTANTS:
                self.assertEqual(src.count(old), 1, "mutant anchor must occur exactly once: %s (%d)" % (mid, src.count(old)))
                mp = os.path.join(scratch, "mut-" + mid)
                os.makedirs(mp)
                mpath = os.path.join(mp, "lint-silent")
                with open(mpath, "w") as f:
                    f.write(src.replace(old, new))
                killed = bool(check_fixtures(mpath, fx))
                if not killed:
                    for r, rc, want in allow_roots:
                        o = lint(mpath, r)
                        if o.returncode != rc or want not in o.stdout:
                            killed = True
                            break
                if not killed:
                    o = lint(mpath, parse_root)
                    killed = o.returncode != 1 or "PARSE tools/bad.py:1" not in o.stdout or "files=1 " not in o.stdout
                if not killed:
                    o = lint(mpath, nofile)
                    killed = o.returncode != 1 or "MISSING-ALLOWLIST" not in o.stdout
                if not killed:
                    survived.append(mid)
                shutil.rmtree(mp)
        self.assertEqual(survived, [], "mutants that survived: %s" % survived)


if __name__ == "__main__":
    unittest.main()
