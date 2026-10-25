#!/bin/bash
# Test of the host-test plumbing (gate round 2 F2/F9) without a build: (1) shim/build.sh has no `gcc ... && $O/test` chains (under set -e a failed
# compile there is skipped), (2) build.sh's chk() stops a `set -e` script on a compile error and lists the compiled *host_test* outputs,
# (3) the gate's host-tests step (extracted from check-all.sh) fails on a missing binary, a missing list or a failing test,
# (4) the --wlan-iso --wlan-en gate step accepts only the intended refusal. ~1 s. Ends with RESULT: PASS|FAIL.
cd "$(dirname "$0")/.." || exit 2
T=$(mktemp -d) || exit 2; trap 'rm -rf "$T"' EXIT
fail=0; bad() { echo "FAIL $*"; fail=1; }
# any line that builds a *host_test* output or runs a $O/ binary after && / ; / || / `then` without going through chk (any compiler word, $O or ${O})
guard() { grep -nE -e '-o +"?\$\{?O\}?"?/[A-Za-z0-9_$]*host_test' -e '(&&|;|\|\||then) +"?\$\{?O\}?"?/' "$1" | grep -v 'chk '; }
guard shim/build.sh && bad "build.sh has a host test compile / && run that bypasses chk"
for l in 'gcc -o ${O}/x_host_test x.c && ${O}/x_host_test' 'if gcc -o $O/foo x.c; then $O/foo; fi' 'gcc x.c; then ${O}/foo' 'true; "${O}"/foo' 'gcc -o ${O}/y_host_test y.c'; do
	printf '%s\n' "$l" >"$T/g.txt"; guard "$T/g.txt" >/dev/null || bad "guard misses: $l"
done
printf '%s\n' 'chk "x" gcc -o ${O}/x_host_test x.c' 'chk "x" ${O}/x_host_test' >"$T/g.txt"; guard "$T/g.txt" && bad "guard flags a chk line"
# (2) chk() from build.sh (first definition, up to its closing brace)
awk '/^chk\(\) \{/{f=1} f{print} f&&/exit 1; fi; \}/{exit}' shim/build.sh >"$T/chk.sh"
[ -s "$T/chk.sh" ] || bad "chk() not found in build.sh"
echo 'int main(void){ return nope; }' >"$T/bad.c"; echo 'int main(void){ return 0; }' >"$T/ok.c"
O=$T/o; mkdir -p "$O"
cat >"$T/run.sh" <<EOT
set -euo pipefail; O=$O
. $T/chk.sh
chk "ok_host_test (compile)" gcc -o \$O/ok_host_test $T/ok.c
chk "ok_host_test" \$O/ok_host_test
chk "bad_host_test (compile)" gcc -o \$O/bad_host_test $T/bad.c
echo CONTINUED
EOT
bash "$T/run.sh" >"$T/run.out" 2>&1 && bad "a compile error did not stop the script"
grep -q CONTINUED "$T/run.out" && bad "script continued after a compile error"
[ "$(cat "$O/host-tests.list" 2>/dev/null)" = "$(printf 'ok_host_test\nbad_host_test')" ] || bad "host-tests.list: $(cat "$O/host-tests.list" 2>/dev/null)"
# (3) the gate step
cmd=$(sed -n "s/^[[:space:]]*step A host-tests[[:space:]]*bash -c '\(.*\)'\$/\1/p" tools/check-all.sh)
[ -n "$cmd" ] || bad "host-tests step not found in check-all.sh"
g=$T/g; d=$g/shim/out/gate/build-0; mkdir -p "$d"
for n in a_host_test b_host_test; do printf '#!/bin/sh\nexit 0\n' >"$d/$n"; chmod +x "$d/$n"; done
step() { (cd "$g" && bash -c "$cmd" >"$T/step.out" 2>&1); }
printf 'a_host_test\nb_host_test\n' >"$d/host-tests.list"; step || bad "complete set failed: $(cat "$T/step.out")"
printf '#!/bin/sh\nexit 0\n' >"$d/c_host_test"; chmod +x "$d/c_host_test"; step && bad "an unlisted host test binary passed"; rm "$d/c_host_test"
rm "$d/b_host_test"; step && bad "a missing binary was skipped"
printf '#!/bin/sh\nexit 1\n' >"$d/b_host_test"; chmod +x "$d/b_host_test"; step && bad "a failing test passed"
rm "$d/host-tests.list"; step && bad "a missing list passed"
# (4) the refusal check: a stub build.sh that fails for another reason must fail the step
w=$(grep -o "bash -c 'cd shim && B=\"./build.sh --board bluejay --avb --usb-gadget --wlan-probe\".*--wlan-iso --wlan-en --out out/gate/build-wlan-4.*'" tools/check-all.sh | head -1)
[ -n "$w" ] || bad "build-wlan step not found"
tail=${w#*&& \{ o=}; tail="o=${tail%\'}"; tail=${tail% \}}
mkdir -p "$g/shim"; printf '#!/bin/bash\necho "gcc: error: something else"; exit 1\n' >"$g/shim/build.sh"; chmod +x "$g/shim/build.sh"
(cd "$g/shim" && B=./build.sh bash -c "$tail") && bad "an unrelated build failure passed the wlan-iso/wlan-en check"
printf '#!/bin/bash\necho "--wlan-iso cannot be combined with --wlan-en (x)" >&2; exit 2\n' >"$g/shim/build.sh"
(cd "$g/shim" && B=./build.sh bash -c "$tail") || bad "the intended refusal failed the check"
if [ $fail = 0 ]; then echo "RESULT: PASS"; else echo "RESULT: FAIL"; exit 1; fi
