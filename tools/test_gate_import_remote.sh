#!/bin/bash
# Host test of tools/gate-import-remote (temp git repo + fake remote-job out dir; never touches the real shim/out; ~1 s).
# Cases: good import (files + exact formats), refusals (pre-validation label, identity vs expected, sha, dirty tree, missing/odd GATE_LINE, quick/dirty line,
# tampered gate.log, spoofed label, commit.txt), and a mutation per refusal: the tool with that one check disabled must NOT give the same refusal.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
cd "$(dirname "$0")/.." || exit 2
w=$(mktemp -d) || exit 2; trap 'rm -rf "$w"' EXIT
imp=$w/imp; mkdir -p "$imp"; cp tools/gate-import-remote "$imp/"; tool=$imp/gate-import-remote   # the importer lives in its own dir with its own pin
src=$PWD/tools/gate-import-remote
n=0; bad=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; bad=$((bad + 1)); fi; }
t=$w/wt; mkdir -p "$t/tools" "$t/shim"
git -C "$t" init -q && git -C "$t" config user.email t@t && git -C "$t" config user.name t
mkdir -p "$t/tools/third_party"; echo tp >"$t/tools/third_party/mkbootimg.py"
tphash=$(cd "$t" && find -L tools/third_party -type f -not \( -path '*/__pycache__/*' -name '*.pyc' \) -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum | sha256sum | cut -d' ' -f1)
printf 'image=sha256:fakeimg\nverlist=aa\nthird_party=%s\n' "$tphash" >"$imp/gate-identity.expected"; echo 'tools/x' >"$t/tools/x"; printf 'shim/out/\ntools/third_party\nresearch/\n' >"$t/.gitignore"
printf 'import os\nprint("g", int(os.path.exists("research/ipsw-x/d22ap.adt")))  # stub of gate_select.py --state\n' >"$t/tools/gate_select.py"
echo "# stub check-all.sh (the registry entry hashes it)" >"$t/tools/check-all.sh"
export GATE_PASS_DIR=$t/../gate-pass-test   # never the real registry of this host
git -C "$t" add -A && git -C "$t" commit -qm init
sha=$(git -C "$t" rev-parse HEAD); short=$(git -C "$t" rev-parse --short HEAD)
ihex=$(sha256sum <"$imp/gate-identity.expected" | cut -d' ' -f1)
# mk DIR : a consistent good out dir
mk() {
	local d=$1; mkdir -p "$d"
	cp "$imp/gate-identity.expected" "$d/identity.txt"
	printf '%s\n%s\n' "$sha" "$(git -C "$t" ls-tree -r HEAD tools | sha256sum)" >"$d/commit.txt"
	printf 'step A\nGATE PASS %s\n' "$short" >"$d/gate.log"; echo "GATE PASS $short" >"$d/GATE_LINE"
	echo "RESULT: PASS remote-job gate-pre where=offload-host sha=$sha tree=clean-at-bundle elapsed=9s prov provenance=offload-host gcc14 formal (offload-host container, identity sha256:$ihex, image sha256:fakeimg) log=/x/job.log" >"$d/RESULT"
}
run() { bash "${3:-$tool}" "$1" "$2" 2>&1; }   # run OUT WT [TOOL]
rm -rf "$t/shim/out"
mk "$w/good"; o=$(run "$w/good" "$t"); rc=$?
ok "good import rc=0 + RESULT: PASS" "$([ $rc = 0 ] && [[ $o == *"RESULT: PASS gate-import-remote"* ]] && echo 1 || echo 0)"
[ "$(cat "$t/shim/out/gate.stamp")" = "$sha" ] && s=1 || s=0; ok "gate.stamp is the full sha" $s
ok "T-0210: stdout has the GATE_LINE at line start before the RESULT line (tick auto-merge reads it)" "$(printf '%s\n' "$o" | grep -n -E "^GATE PASS $short\$|^RESULT: PASS gate-import-remote" | sed 's/:.*//' | tr '\n' , | grep -q '^[0-9]*,[0-9]*,$' && printf '%s\n' "$o" | grep -qx "GATE PASS $short" && echo 1 || echo 0)"
ok "gate.result is 'GATE PASS <short> <epoch>' (check-all.sh format)" "$(grep -Eq "^GATE PASS $short [0-9]+\$" "$t/shim/out/gate.result" && echo 1 || echo 0)"
ok "gate.remote.json has runner/image/identity/log sha/commit" "$(python3 -c '
import json,sys
j=json.load(open(sys.argv[1]));print(int(j["runner"]=="offload-host" and j["image"]=="sha256:fakeimg" and len(j["identity_sha256"])==64 and len(j["gate_log_sha256"])==64 and len(j["remote_job_commit"])==40 and j["imported_at"]>0))' "$t/shim/out/gate.remote.json")"
ok "registry entry written like check-all.sh on a full PASS: check-all.sh blob + gate_select --state, named by the full sha" "$([[ $o == *"registry written"* ]] && [[ $o == *"registry=written"* ]] && [ "$(cat "$GATE_PASS_DIR/$(git -C "$t" rev-parse HEAD)")" = "$(git -C "$t" hash-object tools/check-all.sh) $(cd "$t" && python3 tools/gate_select.py --state)" ] && echo 1 || echo 0)"
ok "no temp files left" "$(! compgen -G "$t/shim/out/*.[0-9]*" >/dev/null && echo 1 || echo 0)"
ok "real shim/out untouched (target is the temp repo)" "$([ ! -e "$t/../shim" ] && echo 1 || echo 0)"

# refusal cases: name | mutate-out-dir command | message substring | check pattern in the tool (the mutant drops that refuse)
refcase() { # name msg pattern mutator...
	local name=$1 msg=$2 pat=$3; shift 3
	rm -rf "$t/shim/out" "$w/c"; mk "$w/c"; ( cd "$w/c" && "$@" ) >/dev/null 2>&1
	o=$(run "$w/c" "$t"); rc=$?
	ok "refuse: $name (rc=1, '$msg', nothing written)" "$([ $rc = 1 ] && [[ $o == *"REFUSED: "*"$msg"* ]] && [ ! -e "$t/shim/out/gate.result" ] && echo 1 || echo 0)"
	sed "\\#$pat#s#refuse \"[^\"]*\"#true#" "$src" >"$imp/mut.sh"
	cmp -s "$src" "$imp/mut.sh" && { ok "mutant for $name differs from the tool (pattern '$pat' found)" 0; return; }
	rm -rf "$t/shim/out"; o=$(run "$w/c" "$t" "$imp/mut.sh")
	if [ "${STRICT:-}" = 1 ]; then ok "mutation: $name check disabled -> RESULT: PASS (single defect)" "$([[ $o == *"RESULT: PASS gate-import-remote"* ]] && echo 1 || echo 0)"
	else ok "mutation: $name check disabled -> no longer refused for that reason" "$([[ $o != *"REFUSED: "*"$msg"* ]] && echo 1 || echo 0)"; fi
}
refcase prevalidation "not a formal offload-host" 'RESULT line is not' sed -i 's/ formal (offload-host container, identity sha256:\([0-9a-f]*\), image \([^)]*\))/ pre-validation (offload-host container, identity MISMATCH (see x), NOT formal)/' RESULT
STRICT=1 refcase "identity != committed expected" "differs from the importer's pin" 'differs from the pin' sh -c "printf 'image=sha256:fakeimg\\nthird_party=%s\\n' $tphash >identity.txt; sed -i 's/identity sha256:[0-9a-f]*/identity sha256:'\$(sha256sum <identity.txt | cut -d' ' -f1)'/' RESULT"
STRICT=1 refcase "identity no trailing newline (label over same bytes)" "differs from the importer's pin" 'differs from the pin' sh -c "printf 'image=sha256:fakeimg\\nverlist=aa\\nthird_party=%s' $tphash >identity.txt; sed -i 's/identity sha256:[0-9a-f]*/identity sha256:'\$(sha256sum <identity.txt | cut -d' ' -f1)'/' RESULT"
STRICT=1 refcase "spoofed label hex" "(spoofed label?)" 'spoofed label' sed -i "s/identity sha256:[0-9a-f]*/identity sha256:$(printf 'b%.0s' $(seq 64))/" RESULT
STRICT=1 refcase "sha mismatch" "RESULT sha" 'RESULT sha' sed -i 's/sha=[0-9a-f]\{40\}/sha=0000000000000000000000000000000000000000/' RESULT
refcase "missing GATE_LINE" "missing or empty" 'missing or empty' rm GATE_LINE
STRICT=1 refcase "odd GATE_LINE (not a prefix of HEAD)" "is not a prefix" 'is not a prefix' sh -c 'echo "GATE PASS deadbee" >GATE_LINE; echo "GATE PASS deadbee" >>gate.log'
refcase "FAIL GATE_LINE" "GATE_LINE is not" 'GATE_LINE is not' sh -c "echo 'GATE FAIL $short x' >GATE_LINE"
STRICT=1 refcase "quick GATE_LINE" "quick or dirty" 'quick or dirty' sh -c "echo 'GATE PASS $short quick: ran 2 of 9 steps' >GATE_LINE; echo 'GATE PASS $short quick: ran 2 of 9 steps' >>gate.log"
STRICT=1 refcase "tampered gate.log" "gate.log last gate line" 'gate.log last gate line' sh -c "echo 'GATE FAIL $short x' >>gate.log"
STRICT=1 refcase "commit.txt mismatch" "commit.txt" 'commit.txt does not' sh -c 'echo junk >commit.txt'
refcase "local run label" "not a formal offload-host" 'RESULT line is not' sed -i 's/where=offload-host/where=local/' RESULT

# pin handling: missing / placeholder pin in the importer's dir; the target's own copy is irrelevant
pinmut() { # name msg pattern setup...
	local name=$1 msg=$2 pat=$3; shift 3
	rm -rf "$t/shim/out"; mk "$w/c"; cp "$imp/gate-identity.expected" "$w/pin.bak"; ( cd "$imp" && "$@" ) >/dev/null 2>&1
	o=$(run "$w/c" "$t"); rc=$?
	ok "refuse: $name (rc=1, '$msg')" "$([ $rc = 1 ] && [[ $o == *"$msg"* ]] && echo 1 || echo 0)"
	sed "\\#$pat#s#refuse \"[^\"]*\"#true#" "$src" >"$imp/mut.sh"; o=$(run "$w/c" "$t" "$imp/mut.sh")
	ok "mutation: $name check disabled -> not refused for that reason" "$([[ $o != *"$msg"* ]] && echo 1 || echo 0)"
	cp "$w/pin.bak" "$imp/gate-identity.expected"
}
pinmut "pin missing" "missing or empty" 'pin missing' rm gate-identity.expected
pinmut "pin has TODO" "TODO/UNMEASURED/MISSING" 'TODO/UNMEASURED' sh -c "echo 'gcc=TODO' >>gate-identity.expected"
pinmut "pin has UNMEASURED" "TODO/UNMEASURED/MISSING" 'TODO/UNMEASURED' sh -c "echo 'gcc=UNMEASURED' >>gate-identity.expected"
pinmut "pin has MISSING" "TODO/UNMEASURED/MISSING" 'TODO/UNMEASURED' sh -c "echo 'gcc=MISSING' >>gate-identity.expected"
# a target-side copy of the pin is ignored: a different one committed in the target does not make a mismatching identity pass
rm -rf "$t/shim/out"; mk "$w/c"; echo 'image=sha256:other' >"$w/c/identity.txt"; sed -i "s/identity sha256:[0-9a-f]*/identity sha256:$(sha256sum <"$w/c/identity.txt" | cut -d' ' -f1)/" "$w/c/RESULT"
cp "$w/c/identity.txt" "$t/tools/gate-identity.expected"; git -C "$t" add -A; git -C "$t" commit -qm copy
sha=$(git -C "$t" rev-parse HEAD); short=$(git -C "$t" rev-parse --short HEAD); mk "$w/d"; echo 'image=sha256:other' >"$w/d/identity.txt"
sed -i "s/identity sha256:[0-9a-f]*/identity sha256:$(sha256sum <"$w/d/identity.txt" | cut -d' ' -f1)/; s/image sha256:fakeimg/image sha256:other/" "$w/d/RESULT"
o=$(run "$w/d" "$t"); ok "target's own pin copy is not trusted (rc=1)" "$([[ $o == *"differs from the importer's pin"* ]] && echo 1 || echo 0)"
# commit.txt is checked against the TARGET's HEAD + tools tree (changing the target tools tree after bundling is refused)
rm -rf "$t/shim/out"; mk "$w/e"; echo y >>"$t/tools/x"; git -C "$t" commit -qam tools2
sha2=$(git -C "$t" rev-parse HEAD); sed -i "s/sha=[0-9a-f]\{40\}/sha=$sha2/" "$w/e/RESULT"; short=$(git -C "$t" rev-parse --short HEAD); echo "GATE PASS $short" >"$w/e/GATE_LINE"; printf 'GATE PASS %s\n' "$short" >"$w/e/gate.log"
o=$(run "$w/e" "$t"); ok "commit.txt of an older target tree refused" "$([[ $o == *"commit.txt does not match"* ]] && echo 1 || echo 0)"
sha=$sha2
# gate.log is read only from OUTDIR/gate.log (no ../ fallback)
rm -rf "$t/shim/out"; mk "$w/f/out"; cp "$w/f/out/gate.log" "$w/f/gate.log"; rm "$w/f/out/gate.log"
o=$(run "$w/f/out" "$t"); ok "gate.log only in OUTDIR (../ copy not used)" "$([[ $o == *"gate.log missing or empty"* ]] && echo 1 || echo 0)"
sed '/for f in RESULT/s/refuse "[^"]*"/true/' "$src" >"$imp/mut.sh"; o=$(run "$w/f/out" "$t" "$imp/mut.sh"); ok "mutation: gate.log-missing check disabled -> not refused for that reason" "$([[ $o != *"missing or empty"* ]] && echo 1 || echo 0)"

# --- review gate-import-r1 additions ---
STRICT=1 refcase "label image != identity.txt image" "label image" 'label image' sed -i 's/image sha256:fakeimg)/image sha256:other)/' RESULT
refcase "multi-line GATE_LINE" "more than one line" 'more than one line' sh -c "printf 'GATE PASS $short\nGATE PASS $short\n' >GATE_LINE"
STRICT=1 refcase "+dirty tag" "quick or dirty" 'quick or dirty' sh -c "echo 'GATE PASS $short x +dirty' >GATE_LINE; echo 'GATE PASS $short x +dirty' >>gate.log"
STRICT=1 refcase "(see tag" "quick or dirty" 'quick or dirty' sh -c "echo 'GATE PASS $short x (see /y)' >GATE_LINE; echo 'GATE PASS $short x (see /y)' >>gate.log"
STRICT=1 refcase "partial quick note" "quick or dirty" 'quick or dirty' sh -c "echo 'GATE PASS $short quick: no usable stamp, ran full quick: ran 1 of 9 steps' >GATE_LINE; echo 'GATE PASS $short quick: no usable stamp, ran full quick: ran 1 of 9 steps' >>gate.log"
for c in tab:$'\t' cr:$'\r' backslash:'\\'; do
	STRICT=1 refcase "GATE_LINE with ${c%%:*}" "control or non-ASCII or backslash" 'control or non-ASCII' sh -c "printf 'GATE PASS $short x%sy\n' '${c#*:}' >GATE_LINE; printf 'GATE PASS $short x%sy\n' '${c#*:}' >>gate.log"
done
# state of the target the container could not see: refuse + mutation (strict), then restore
refstate() { # name msg pattern setup teardown
	local name=$1 msg=$2 pat=$3
	rm -rf "$t/shim/out" "$w/c"; mk "$w/c"; ( cd "$t" && eval "$4" ) >/dev/null 2>&1
	o=$(run "$w/c" "$t"); rc=$?
	ok "refuse: $name (rc=1, '$msg', nothing written)" "$([ $rc = 1 ] && [[ $o == *"REFUSED: "*"$msg"* ]] && [ ! -e "$t/shim/out/gate.result" ] && echo 1 || echo 0)"
	sed "\\#$pat#s#refuse \"[^\"]*\"#true#" "$src" >"$imp/mut.sh"; rm -rf "$t/shim/out"; o=$(run "$w/c" "$t" "$imp/mut.sh")
	ok "mutation: $name check disabled -> RESULT: PASS (single defect)" "$([[ $o == *"RESULT: PASS gate-import-remote"* ]] && echo 1 || echo 0)"
	( cd "$t" && eval "$5" ) >/dev/null 2>&1
}
refstate "local third_party differs from the container's" "tools/third_party hash" 'third_party hash' 'echo extra >tools/third_party/new.py' 'rm -f tools/third_party/new.py'
# bytecode is not source: a __pycache__/*.pyc (any python version) must not change the hash; a changed source file must (refstate above)
mkdir -p "$t/tools/third_party/gki/__pycache__"; echo b314 >"$t/tools/third_party/gki/__pycache__/m.cpython-314.pyc"
rm -rf "$t/shim/out"; mk "$w/pyc"; o=$(run "$w/pyc" "$t")
ok "adding __pycache__/*.pyc keeps the hash (import passes)" "$([[ $o == *"RESULT: PASS gate-import-remote"* ]] && echo 1 || echo 0)"
echo changed >"$t/tools/third_party/gki/__pycache__/m.cpython-314.pyc"; rm -rf "$t/shim/out"; o=$(run "$w/pyc" "$t")
ok "changing a .pyc keeps the hash (import passes)" "$([[ $o == *"RESULT: PASS gate-import-remote"* ]] && echo 1 || echo 0)"
echo b313 >"$t/tools/third_party/stray.pyc"; rm -rf "$t/shim/out"; o=$(run "$w/pyc" "$t")
ok "a .pyc OUTSIDE __pycache__ changes the hash (sourceless importable, refused)" "$([ -n "$o" ] && [[ $o == *"third_party hash"* ]] && echo 1 || echo 0)"
rm -rf "$t/tools/third_party/gki" "$t/tools/third_party/stray.pyc" "$t/shim/out"
echo changed >"$t/tools/third_party/mkbootimg.py"; o=$(run "$w/pyc" "$t")
ok "changing a real source file changes the hash (refused)" "$([ -n "$o" ] && [[ $o == *"third_party hash"* ]] && echo 1 || echo 0)"
echo tp >"$t/tools/third_party/mkbootimg.py"; rm -rf "$t/shim/out"
# the two shipped computations must stay identical
pl() { grep -o "find -L tools/third_party.*| sha256sum | cut -d' ' -f1" "$1" | sed 's# 2>/dev/null##' | head -1; }   # whole pipeline through sha256sum | cut
gi=$(pl tools/gate-identity); gr=$(pl "$src")
ok "gate-identity and gate-import-remote hash the same pipeline" "$([ -n "$gi" ] && [ "$gi" = "$gr" ] && [ "$gi" = "$(pl tools/test_gate_import_remote.sh)" ] && echo 1 || echo 0)"
# ---- ADT rule (gate-adt-mount): a=0 unchanged, a=1 only with a matching container adt=<sha256>
adtmk() { # DIR ADTLINE-VALUE|-  : good out dir whose identity.txt ends with adt=VALUE (label re-hashed over the raw bytes, as remote-job does); '-' = no adt= line
	mk "$1"; [ "$2" = - ] && return 0
	printf 'adt=%s\n' "$2" >>"$1/identity.txt"
	sed -i "s/identity sha256:[0-9a-f]*/identity sha256:$(sha256sum <"$1/identity.txt" | cut -d' ' -f1)/" "$1/RESULT"
}
mut() { sed "$1" "$src" >"$imp/mut.sh"; rm -rf "$t/shim/out"; run "$w/c" "$t" "$imp/mut.sh"; }   # mutant of the importer, same inputs
adtrefuse() { # name outdirvalue expected-substring mutate-sed-pattern(or '') ; the tree has the ADT
	local name=$1 val=$2 msg=$3 pat=$4
	rm -rf "$t/shim/out" "$w/c"; adtmk "$w/c" "$val"; o=$(run "$w/c" "$t"); rc=$?
	ok "ADT a=1: $name refused (rc=1, '$msg', nothing written)" "$([ $rc = 1 ] && [[ $o == *"REFUSED: "*"$msg"* ]] && [[ $o == *"$adth"* ]] && [ ! -e "$t/shim/out/gate.result" ] && echo 1 || echo 0)"
	[ -z "$pat" ] || { o=$(mut "/$pat/s/refuse \"[^\"]*\"/true/"); ok "mutation: ADT $name check disabled -> message changes (killed)" "$([[ $o != *"REFUSED: "*"$msg"* ]] && echo 1 || echo 0)"; }
}
mkdir -p "$t/research/ipsw-x"; printf 'APPLE-ADT-BYTES' >"$t/research/ipsw-x/d22ap.adt"; adth=$(sha256sum <"$t/research/ipsw-x/d22ap.adt" | cut -d' ' -f1)
rm -rf "$t/shim/out" "$w/c"; adtmk "$w/c" "$adth"; o=$(run "$w/c" "$t"); rc=$?
ok "ADT a=1: matching adt= accepted (rc=0, PASS, stamp written)" "$([ $rc = 0 ] && [[ $o == *"RESULT: PASS gate-import-remote"* ]] && [ "$(cat "$t/shim/out/gate.stamp")" = "$sha" ] && echo 1 || echo 0)"
o=$(mut 's#sed ./^adt=/d. "$out/identity.txt"#cat "$out/identity.txt"#'); ok "mutation: pin compare without the adt= filter -> a good a=1 import is refused (killed)" "$([[ $o != *"RESULT: PASS"* ]] && echo 1 || echo 0)"
adtrefuse "mismatched adt=" "$(printf 'x' | sha256sum | cut -d' ' -f1)" "container adt=" 'container adt='
adtrefuse "adt=none" none "container adt=none" ''
adtrefuse "missing adt= line" - "no adt= line" 'has no adt= line'
adtrefuse "mismatch names both hashes (local and container)" "$(printf 'y' | sha256sum | cut -d' ' -f1)" "sha256 $adth != container adt=" ''
: >"$t/research/ipsw-x/d22ap.adt"; e3=$(sha256sum <"$t/research/ipsw-x/d22ap.adt" | cut -d' ' -f1)
rm -rf "$t/shim/out" "$w/c"; adtmk "$w/c" "$e3"; o=$(run "$w/c" "$t"); rc=$?
ok "ADT a=1: empty local ADT file refused even with the hash of nothing" "$([ $rc = 1 ] && [[ $o == *"not a readable non-empty file"* ]] && echo 1 || echo 0)"
o=$(mut '/not a readable non-empty file/s/refuse "[^"]*"/true/'); ok "mutation: empty-ADT check disabled -> imports (killed)" "$([[ $o == *"RESULT: PASS"* ]] && echo 1 || echo 0)"
printf 'APPLE-ADT-BYTES' >"$t/research/ipsw-x/d22ap.adt"
rm -rf "$t/shim/out" "$w/c"; adtmk "$w/c" "$adth"; printf 'adt=%s\n' "$adth" >>"$w/c/identity.txt"; sed -i "s/identity sha256:[0-9a-f]*/identity sha256:$(sha256sum <"$w/c/identity.txt" | cut -d' ' -f1)/" "$w/c/RESULT"
o=$(run "$w/c" "$t"); rc=$?
ok "ADT: two adt= lines refused" "$([ $rc = 1 ] && [[ $o == *"more than one adt= line"* ]] && echo 1 || echo 0)"
o=$(mut '/more than one adt= line/s/refuse "[^"]*"/true/'); ok "mutation: single-adt= check disabled -> a=1 import with two lines passes (killed)" "$([[ $o == *"RESULT: PASS"* ]] && echo 1 || echo 0)"
cp "$t/tools/gate_select.py" "$w/gs.keep"; printf 'print("g 7")\n' >"$t/tools/gate_select.py"; git -C "$t" commit -qam odd
sha=$(git -C "$t" rev-parse HEAD); short=$(git -C "$t" rev-parse --short HEAD); rm -rf "$t/shim/out" "$w/c"; adtmk "$w/c" "$adth"
o=$(run "$w/c" "$t"); rc=$?
ok "ADT: unexpected flag from gate_select --state refused" "$([ $rc = 1 ] && [[ $o == *"unexpected ADT flag '7'"* ]] && echo 1 || echo 0)"
o=$(mut '/unexpected ADT flag/s/refuse "[^"]*"/true/'); ok "mutation: unexpected-flag refusal disabled -> imports (killed)" "$([[ $o == *"RESULT: PASS"* ]] && echo 1 || echo 0)"
cp "$w/gs.keep" "$t/tools/gate_select.py"; git -C "$t" commit -qam restore
sha=$(git -C "$t" rev-parse HEAD); short=$(git -C "$t" rev-parse --short HEAD)
# a=0 tree: unchanged (no adt= needed; a stray matching or hash adt= from a container that had the ADT is fine)
rm -rf "$t/research"
rm -rf "$t/shim/out" "$w/c"; mk "$w/c"; o=$(run "$w/c" "$t"); rc=$?
ok "ADT a=0: import unchanged without adt= line (old stamp)" "$([ $rc = 0 ] && [[ $o == *"RESULT: PASS"* ]] && echo 1 || echo 0)"
rm -rf "$t/shim/out" "$w/c"; adtmk "$w/c" none; o=$(run "$w/c" "$t"); rc=$?
ok "ADT a=0: import unchanged with adt=none" "$([ $rc = 0 ] && [[ $o == *"RESULT: PASS"* ]] && echo 1 || echo 0)"
rm -rf "$t/shim/out" "$w/c"; adtmk "$w/c" "$adth"; printf 'adt=%s\n' "$adth" >>"$w/c/identity.txt"; sed -i "s/identity sha256:[0-9a-f]*/identity sha256:$(sha256sum <"$w/c/identity.txt" | cut -d' ' -f1)/" "$w/c/RESULT"
o=$(run "$w/c" "$t"); rc=$?
ok "ADT a=0: two adt= lines refused too" "$([ $rc = 1 ] && [[ $o == *"more than one adt= line"* ]] && echo 1 || echo 0)"
o=$(mut '/more than one adt= line/s/refuse "[^"]*"/true/'); ok "mutation: single-adt= check disabled -> a=0 import with two lines passes (killed)" "$([[ $o == *"RESULT: PASS"* ]] && echo 1 || echo 0)"
rm -rf "$t/shim/out"
# gate lock held by another process (fd 8 of this shell, a different open file description than the tool's)
rm -rf "$t/shim/out" "$w/c"; mk "$w/c"; mkdir -p "$t/shim/out"; exec 8>"$t/shim/out/.gate.lock"; flock -n 8
o=$(run "$w/c" "$t"); rc=$?
ok "refuse: gate lock held (rc=1, nothing written)" "$([ $rc = 1 ] && [[ $o == *"REFUSED: a gate holds"* ]] && [ ! -e "$t/shim/out/gate.result" ] && echo 1 || echo 0)"
sed '/a gate holds/s/refuse "[^"]*"/true/' "$src" >"$imp/mut.sh"; o=$(run "$w/c" "$t" "$imp/mut.sh")
ok "mutation: lock check disabled -> RESULT: PASS (single defect)" "$([[ $o == *"RESULT: PASS gate-import-remote"* ]] && echo 1 || echo 0)"
exec 8>&-
# positive: check-all.sh's full-run note and a longer container hex are accepted; gate.result carries the HOST short; JSON stays valid with a quote in the tag; gate.remote.log = gate.log
# review gate-adt-mount-r1 F1: a "(not registered: ...)" tag (container gate skipped tests on a tree with the ADT, or no tree state) is refused, and the check is load-bearing
rm -rf "$t/shim/out" "$w/c"; mk "$w/c"; printf 'GATE PASS %s (not registered: skipped tests on a tree with the ADT, or no tree state)\n' "${sha:0:12}" >"$w/c/GATE_LINE"; cp "$w/c/GATE_LINE" "$w/c/gate.log"
o=$(run "$w/c" "$t"); rc=$?
ok "refuse: not-registered tag (rc=1, no gate.result)" "$([ $rc = 1 ] && [[ $o == *"skipped tests or had no tree state"* ]] && [ ! -e "$t/shim/out/gate.result" ] && echo 1 || echo 0)"
sed '/(not registered"\*) refuse/s/refuse "[^"]*"/true/' "$src" >"$imp/mut.sh"; rm -rf "$t/shim/out"; o=$(run "$w/c" "$t" "$imp/mut.sh")
ok "mutation: not-registered refusal disabled -> RESULT: PASS (single defect)" "$([[ $o == *"RESULT: PASS gate-import-remote"* ]] && echo 1 || echo 0)"
for tg in " quick: no usable stamp, ran full" ' say "hi"'; do
	rm -rf "$t/shim/out" "$w/c"; mk "$w/c"; hx=${sha:0:12}
	printf 'GATE PASS %s%s\n' "$hx" "$tg" >"$w/c/GATE_LINE"; cp "$w/c/GATE_LINE" "$w/c/gate.log"
	o=$(run "$w/c" "$t"); rc=$?
	ok "accepted tag '$tg' (rc=0, host short in gate.result, prefix match)" "$([ $rc = 0 ] && grep -Fq "GATE PASS $short$tg " "$t/shim/out/gate.result" && echo 1 || echo 0)"
	pj=$(python3 -c '
import json,sys
j=json.load(open(sys.argv[1]));print(int(j["gate_line"].startswith("GATE PASS ") and j["result_line"].startswith("RESULT: PASS remote-job") and bool(j["imported_by"]) and len(j["gate_log_sha256"])==64))' "$t/shim/out/gate.remote.json")
	cmp -s "$w/c/gate.log" "$t/shim/out/gate.remote.log" && cl=1 || cl=0
	ok "sidecar valid JSON (gate_line/result_line/imported_by) and gate.remote.log == gate.log, tag '$tg'" "$([ "$pj$cl" = 11 ] && echo 1 || echo 0)"
done
# a stale RUNNING gate.result is replaced, no temp files stay
ok "no mktemp leftovers after imports" "$(! compgen -G "$t/shim/out/.gate-import.*" >/dev/null && echo 1 || echo 0)"
refstate "shim/out is a symlink" "symlink" 'symlink' 'mkdir -p "$PWD/../real-so"; rm -rf shim/out; ln -s "$PWD/../real-so" shim/out' 'rm -f shim/out; rm -rf ../real-so'

# dirty tree (refusal + mutation)
rm -rf "$t/shim/out"; mk "$w/c"; echo x >"$t/untracked"
o=$(run "$w/c" "$t"); rc=$?
ok "refuse: dirty tree (rc=1)" "$([ $rc = 1 ] && [[ $o == *"not clean"* ]] && echo 1 || echo 0)"
sed '/not clean/s/refuse "[^"]*"/true/' "$src" >"$imp/mut.sh"; o=$(run "$w/c" "$t" "$imp/mut.sh")
ok "mutation: dirty check disabled -> imports" "$([[ $o == *"RESULT: PASS"* ]] && echo 1 || echo 0)"
rm -f "$t/untracked"
# ---- TP-050 lab-host lane: exactly two shapes (offload-host/offload-host/offload-host, lab-host/lab-host/lab-host); mixed shapes and lab-host pre-validation are refused
hlres() { sed -i 's/where=offload-host/where=lab-host/; s/provenance=offload-host/provenance=lab-host/; s/(offload-host container/(lab-host container/' RESULT; }
rm -rf "$t/shim/out" "$w/h"; mk "$w/h"; ( cd "$w/h" && hlres ); o=$(run "$w/h" "$t"); rc=$?
ok "lab-host: formal where=lab-host/provenance=lab-host/(lab-host container) imported (rc=0, PASS)" "$([ $rc = 0 ] && [[ $o == *"RESULT: PASS gate-import-remote"* ]] && echo 1 || echo 0)"
hj=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["runner"])' "$t/shim/out/gate.remote.json"); ok "lab-host: sidecar runner field is lab-host" "$([ "$hj" = lab-host ] && echo 1 || echo 0)"
refcase "lab-host label with where=offload-host (mixed)" "not a formal offload-host or lab-host" 'RESULT line is not' sh -c 'sed -i "s/(offload-host container/(lab-host container/; s/provenance=offload-host/provenance=lab-host/" RESULT'
refcase "offload-host label with where=lab-host (mixed)" "not a formal offload-host or lab-host" 'RESULT line is not' sed -i 's/where=offload-host/where=lab-host/' RESULT
refcase "lab-host where, offload-host provenance (mixed)" "not a formal offload-host or lab-host" 'RESULT line is not' sh -c 'sed -i "s/where=offload-host/where=lab-host/; s/(offload-host container/(lab-host container/" RESULT'
refcase "lab-host pre-validation" "not a formal offload-host or lab-host" 'RESULT line is not' sh -c 'sed -i "s/where=offload-host/where=lab-host/; s/provenance=offload-host/provenance=lab-host/; s/ formal (offload-host container, identity sha256:\([0-9a-f]*\), image \([^)]*\))/ pre-validation (lab-host container, identity MISMATCH (see x), NOT formal)/" RESULT'
# mutant: the where=/provenance= words accept any runner (the mixed-shape defect) -> the mixed shapes must then be IMPORTED, which the refusals above forbid
sed 's/where=\$1 /where=[a-z]+ /; s/provenance=\$1 /provenance=[a-z]+ /' "$src" >"$imp/mut.sh"
cmp -s "$src" "$imp/mut.sh" && ok "mutant mixed-shape differs from the tool" 0
for mx in 's/where=offload-host/where=lab-host/' 's/provenance=offload-host/provenance=lab-host/'; do
	rm -rf "$t/shim/out" "$w/m"; mk "$w/m"; ( cd "$w/m" && sed -i "$mx" RESULT ) >/dev/null 2>&1; o=$(run "$w/m" "$t" "$imp/mut.sh")
	ok "mutation: mixed shapes accepted ($mx) -> RESULT: PASS where the real tool refuses (killed by the mixed-shape refusals)" "$([[ $o == *"RESULT: PASS gate-import-remote"* ]] && echo 1 || echo 0)"
done

# usage
bash "$tool" >/dev/null 2>&1; ok "usage: no args exits 2" "$([ $? = 2 ] && echo 1 || echo 0)"

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; else echo "RESULT: FAIL cases=$n failed=$bad"; exit 1; fi
