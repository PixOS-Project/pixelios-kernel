#!/bin/bash
# Mutation check of tools/audit_memo.py (docs/research/gate-speed.md section 3): with a WARM memo, every mutant of an input of the
# memoized self-tests must still FAIL, and the unmutated control must pass again. Cheap parts only: runs the wlan_seq and wlanlink
# source self-tests from a throwaway copy in a temp dir (never the repo tree, never shim/out, no build). The object self-test needs
# an aarch64 objdump + a built shim/out/wlanlink.o; its mutants are NOT covered here (its key is the disasm text, see test_audit_memo.py).
# Last line: RESULT: PASS mutants=N caught=N (exit 0) | RESULT: FAIL survived=NAMES (1) | RESULT: ERROR ... (2). Logs: logs/audit-memo-mut/<UTC>/
cd "$(dirname "$0")/.." || exit 2
root=$PWD; ts=$(date -u +%Y%m%dT%H%M%SZ); logd=$root/logs/audit-memo-mut/$ts; mkdir -p "$logd" || { echo "RESULT: ERROR no log dir"; exit 2; }
w=$(mktemp -d) || exit 2; trap 'rm -rf "$w"' EXIT
err() { echo "RESULT: ERROR $* (logs $logd)"; exit 2; }
mkdir -p "$w/pristine/tools" "$w/pristine/soc/google/gs101" "$w/pristine/shim" "$w/run"
for f in audit_memo.py test-bluejay-boot.py boards.py; do cp "tools/$f" "$w/pristine/tools/" || err "missing tools/$f"; done
cp soc/google/gs101/wlan_seq.json "$w/pristine/soc/google/gs101/" && cp shim/wlanlink.c "$w/pristine/shim/" || err "missing inputs"
cat >"$w/driver.py" <<'PY'
import importlib.util, pathlib, sys
root = pathlib.Path.cwd(); sys.path.insert(0, str(root / "tools"))
sp = importlib.util.spec_from_file_location("tbb", root / "tools/test-bluejay-boot.py"); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
m._memo(m.wlan_seq_selftest)
m._memo(m.wlanlink_source_selftest)
m.check_wlanlink_source_text((root / "shim/wlanlink.c").read_text())  # the audit of the real source: never memoized
print("DRIVER PASS")
PY
export PIXELIOS_AUDIT_MEMO_DIR=$w/memo; unset PIXELIOS_AUDIT_NO_MEMO
# fresh FILE: copy of the pristine tree
fresh() { rm -rf "$w/run"; cp -r "$w/pristine" "$w/run"; }
drive() { (cd "$w/run" && python3 -I "$w/driver.py") >"$logd/$1.log" 2>&1; echo $? >"$logd/$1.rc"; }
# edit FILE OLD NEW: exact, once
edit() { python3 -I - "$w/run/$1" "$2" "$3" <<'PY' || err "mutation text not found in $1: $2"
import sys; p, a, b = sys.argv[1:]; s = open(p).read(); assert s.count(a) >= 1; open(p, "w").write(s.replace(a, b, 1))
PY
}
fresh; t0=$(date +%s.%N); drive cold; t1=$(date +%s.%N)
[ "$(cat "$logd/cold.rc")" = 0 ] && grep -q "^DRIVER PASS" "$logd/cold.log" || err "cold run failed (cold.log)"
n=$(ls "$w/memo"/*.json 2>/dev/null | wc -l); [ "$n" = 2 ] || err "cold run wrote $n memo entries, expected 2"
fresh; drive warm; t2=$(date +%s.%N)
[ "$(cat "$logd/warm.rc")" = 0 ] || err "warm control failed (warm.log)"
[ -z "$(grep -v -e '(memo [0-9a-f]\{16\})$' -e '^DRIVER PASS$' "$logd/warm.log")" ] || err "warm control has lines without (memo ...) (warm.log)"
awk -v a="$t0" -v b="$t1" -v c="$t2" 'BEGIN{exit !((c-b)*3 < (b-a))}' || err "warm run not under 1/3 of the cold run"
mutants=0; caught=0; survived=
# mut NAME EXPECTED_REGEX FILE OLD NEW: apply to a fresh copy, run with the warm memo, must exit non-zero with an Assertion/ValueError matching EXPECTED_REGEX
mut() {
	mutants=$((mutants+1)); fresh; edit "$3" "$4" "$5"; drive "$1"
	if [ "$(cat "$logd/$1.rc")" != 0 ] && grep -q -E "^(AssertionError|ValueError)" "$logd/$1.log" && grep -q -E "$2" "$logd/$1.log" && ! grep -q "^DRIVER PASS" "$logd/$1.log"; then caught=$((caught+1)); echo "caught $1"
	else survived="$survived${survived:+,}$1"; echo "SURVIVED $1"; fi
}
# 3: source mutants (forbidden write): the DBI offset of the allowlist; a second write path in run_row
mut src-dbi-offset "audited text|reviewed text|rejected|FAILED" shim/wlanlink.c "pa == WLAN_DBI + 0x890u" "pa == WLAN_DBI + 0x8bcu"
mut src-second-write "AssertionError" shim/wlanlink.c "static int run_row(" "static int run_row_dummy(void) { return try_write32(WLAN_PMA, 0); }
static int run_row("
# 4: audit mutant: the source audit accepts everything -> the warm self-test must rerun (script sha changed) and FAIL to catch
mut audit-gutted "FAILED to catch" tools/test-bluejay-boot.py "def check_wlanlink_source_text(src: str, name: str = \"wlanlink.c\") -> None:
" "def check_wlanlink_source_text(src: str, name: str = \"wlanlink.c\") -> None:
    return
"
# 5: data mutant: one value changed without re-hashing
mut data-value "table_sha256|sha256|does not match" soc/google/gs101/wlan_seq.json '"value": 0}' '"value": 1}'
# 6: dep-tracking mutant: the renderer emits a different header
mut render-mask "AssertionError|header" tools/boards.py "{r['mask']:#x}u, {r['value']:#x}u" "{r['mask']^1:#x}u, {r['value']:#x}u"
fresh; drive control-after
{ [ "$(cat "$logd/control-after.rc")" = 0 ] && grep -q "^DRIVER PASS" "$logd/control-after.log"; } || err "unmutated control failed after the mutants (control-after.log)"
if [ -n "$survived" ]; then echo "RESULT: FAIL survived=$survived (logs $logd)"; exit 1; fi
echo "RESULT: PASS mutants=$mutants caught=$caught (logs $logd)"
