#!/bin/bash
# Host test of tools/xnu-src-mirror (seed idempotence, env rewrite under `unshare -rn`, stager drift, missing sha, invalid names).
# No network: the "remotes" are local bare repos; the fake stack's stagers carry fake https urls (they mimic the url/sha lines of
# xnu/patches/00{19,20,26}-stage-*.sh) and the seed step reaches the remotes through a test-only git url insteadOf.
# Also: the strict stager parse (review xnub-mirror-r1 F1/F2), the seed flock (F4), the not-servable mirror (F6), ARTIFACTS made absolute (F5) and the
# real stage_tree() of tools/xnu-local-build (F3: its text is extracted and run against fake stagers; XLB_SCRIPT=FILE tests a mutant copy instead).
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
cd "$(dirname "$0")/.." || exit 2
here=$PWD
tool=$here/tools/xnu-src-mirror
w=$(mktemp -d) || exit 2
trap 'rm -rf "$w"' EXIT
n=0; bad=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; bad=$((bad + 1)); fi; }
has() { grep -q -- "$1" <<<"$2" && echo 1 || echo 0; }
g() { git -c user.email=t@t -c user.name=t "$@"; }
unset GIT_CONFIG_COUNT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

# three fake remotes with a pinned commit each (plus a later commit, so a --depth 1 fetch of the pinned sha is a real test)
declare -A sha
for nm in alpha beta Gamma.x; do
	r=$w/remote/$nm; mkdir -p "$r"; git init -q "$r"
	echo one >"$r/f"; g -C "$r" add f; g -C "$r" commit -q -m one; sha[$nm]=$(git -C "$r" rev-parse HEAD)
	echo two >"$r/f"; g -C "$r" commit -q -am two
	git -C "$r" config uploadpack.allowAnySHA1InWant true
done
mkstack() {   # mkstack DIR: three stagers shaped like 0019/0020/0026 (sha assignment first, https url in the fetch call)
	mkdir -p "$1"
	local i=0   # numeric prefix 00/01/02: the stager order must not depend on the locale (container C.UTF-8 sorts Gamma.x first, host en_US last;)
	for nm in alpha beta Gamma.x; do
		printf '#!/bin/bash\n# fake stager for %s\nx_sha=%s\ngit fetch -q --depth 1 \\\n    https://fake.invalid/org/%s "$x_sha"\n' "$nm" "${sha[$nm]}" "$nm" >"$1/0$i-stage-$nm.sh"; i=$((i+1))
	done
}
mkstack "$w/stack"
seedenv() {   # run the tool with the test-only "network": fake https urls -> local bare remotes
	GIT_CONFIG_COUNT=3 GIT_CONFIG_KEY_0=url.$w/remote/alpha.insteadOf GIT_CONFIG_VALUE_0=https://fake.invalid/org/alpha \
	GIT_CONFIG_KEY_1=url.$w/remote/beta.insteadOf GIT_CONFIG_VALUE_1=https://fake.invalid/org/beta \
	GIT_CONFIG_KEY_2=url.$w/remote/Gamma.x.insteadOf GIT_CONFIG_VALUE_2=https://fake.invalid/org/Gamma.x \
	"$tool" "$@" 2>&1
}
m=$w/mirror

out=$(seedenv seed --stack "$w/stack" --dir "$m"); rc=$?
ok "seed: PASS exit 0 seeded=3 present=0" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS seeded=3 present=0 ' "$out")" = 1 ] && echo 1 || echo 0)"
ok "seed: refs/pinned/<sha> exists in each mirror (names alpha, beta, Gamma.x)" "$(for nm in alpha beta Gamma.x; do git --git-dir="$m/$nm.git" rev-parse -q --verify "refs/pinned/${sha[$nm]}^{commit}" >/dev/null || echo bad; done | grep -q bad && echo 0 || echo 1)"
# F2 (mutant "seed accepts whatever the fetch returned"): a git on PATH whose fetch answers with another commit than the pinned sha
realgit=$(command -v git); mkdir -p "$w/bin"; wrong=$(git -C "$w/remote/alpha" rev-parse HEAD)   # alpha's later commit, not the pin
[ "$wrong" != "${sha[alpha]}" ] || { echo "RESULT: ERROR test setup: alpha pin equals its HEAD"; exit 2; }
printf '#!/bin/bash\nargs=("$@"); for a in "$@"; do [ "$a" = fetch ] && args[$((${#args[@]} - 1))]=%s; done\nexec %s "${args[@]}"\n' "$wrong" "$realgit" >"$w/bin/git"; chmod +x "$w/bin/git"
out=$(PATH=$w/bin:$PATH seedenv seed --stack "$w/stack" --dir "$w/mw"); rc=$?
ok "seed: a fetch that returns another commit than the pin is refused (FAILED alpha, RESULT: FAIL exit 1)" "$([ $rc = 1 ] && [ "$(has "^xnu-src-mirror: seed FAILED alpha ${sha[alpha]} " "$out")" = 1 ] && has '^RESULT: FAIL ' "$out" || echo 0)"
ok "seed: the refused fetch leaves NO refs/pinned/<pin> in the mirror" "$(git --git-dir="$w/mw/alpha.git" rev-parse -q --verify "refs/pinned/${sha[alpha]}" >/dev/null 2>&1 && echo 0 || echo 1)"

# F4: seeding is serialised by flock on MIRRORDIR/.seed.lock (bounded wait, loud)
hold() {   # hold LOCKFILE SECS: a background holder of the flock; waits until it really holds it; pid in $hpid
	rm -f "$w/ready"; ( flock 9; : >"$w/ready"; exec sleep "$2" ) 9>"$1" & hpid=$!
	for _ in $(seq 100); do [ -e "$w/ready" ] && return 0; sleep 0.05; done; echo "test setup: holder never locked" >&2; return 1
}
mkdir -p "$w/m4"; hold "$w/m4/.seed.lock" 30
out=$(XNU_MIRROR_LOCK_WAIT=1 seedenv seed --stack "$w/stack" --dir "$w/m4"); rc=$?
kill "$hpid" 2>/dev/null; wait "$hpid" 2>/dev/null
ok "seed under a held lock: waits (loud line), times out after XNU_MIRROR_LOCK_WAIT, RESULT: FAIL exit 1, nothing seeded" "$([ $rc = 1 ] && [ "$(has "^xnu-src-mirror: another seed holds $w/m4/.seed.lock; waiting up to 1s" "$out")" = 1 ] && [ "$(has "still held after 1s" "$out")" = 1 ] && [ "$(has '^RESULT: FAIL .*lock-timeout' "$out")" = 1 ] && [ ! -d "$w/m4/alpha.git" ] && echo 1 || echo 0)"
hold "$w/m4/.seed.lock" 2
out=$(XNU_MIRROR_LOCK_WAIT=30 seedenv seed --stack "$w/stack" --dir "$w/m4"); rc=$?
wait "$hpid" 2>/dev/null
ok "seed waits for the holder to finish, then seeds: RESULT: PASS seeded=3" "$([ $rc = 0 ] && [ "$(has 'another seed holds' "$out")" = 1 ] && has '^RESULT: PASS seeded=3 present=0 ' "$out" || echo 0)"
mkdir -p "$w/m6"; hold "$w/m6/.seed.lock" 30
out=$(XNU_MIRROR_LOCK_WAIT=1 "$tool" seed --stack "$w/stack" --dir "$m" 2>&1); rc=$?
kill "$hpid" 2>/dev/null; wait "$hpid" 2>/dev/null
ok "seed with every ref present takes no lock (a held lock elsewhere is irrelevant): PASS present=3" "$([ $rc = 0 ] && has '^RESULT: PASS seeded=0 present=3 ' "$out" || echo 0)"
out=$(XNU_MIRROR_LOCK_WAIT=abc "$tool" seed --stack "$w/stack" --dir "$w/m7" 2>&1); rc=$?
ok "XNU_MIRROR_LOCK_WAIT not a number: RESULT: ERROR exit 2" "$([ $rc = 2 ] && has "^RESULT: ERROR XNU_MIRROR_LOCK_WAIT='abc'" "$out" || echo 0)"

# F6: git refuses a mirror owned by another uid (dubious ownership). safe.directory in GIT_CONFIG_* does NOT reach a local-path fetch (git strips it),
# so env lists such a mirror as NOT mirrored with the reason; the global git config is the remedy. GIT_TEST_ASSUME_DIFFERENT_OWNER is git's own test knob.
out=$(GIT_TEST_ASSUME_DIFFERENT_OWNER=1 GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$m/alpha.git" git --git-dir="$w/mw/alpha.git" fetch -q "$m/alpha.git" "${sha[alpha]}" 2>&1); rc=$?
ok "control: safe.directory via GIT_CONFIG_* does not make a local-path fetch accept a foreign-owned mirror (why the tool does not emit it)" "$([ $rc != 0 ] && has 'dubious ownership' "$out" || echo 0)"
out=$(GIT_TEST_ASSUME_DIFFERENT_OWNER=1 "$tool" env --stack "$w/stack" --dir "$m" 2>&1 >"$w/foreign.out"); rc=$?
ok "env on foreign-owned mirrors: exit 0, nothing on stdout, 3 'not servable' + 3 NOT mirrored lines, mirrored=0 of 3" "$([ $rc = 0 ] && [ ! -s "$w/foreign.out" ] && [ "$(grep -c 'is not servable by git' <<<"$out")" = 3 ] && [ "$(grep -c 'NOT mirrored' <<<"$out")" = 3 ] && has 'mirrored=0 of 3 ' "$out" || echo 0)"
printf '[safe]\n\tdirectory = *\n' >"$w/safe.cfg"
out=$(GIT_TEST_ASSUME_DIFFERENT_OWNER=1 GIT_CONFIG_GLOBAL="$w/safe.cfg" "$tool" env --stack "$w/stack" --dir "$m" 2>&1 >/dev/null); rc=$?
ok "env on foreign-owned mirrors with safe.directory in the global git config (the documented remedy): mirrored=3 of 3" "$([ $rc = 0 ] && has 'mirrored=3 of 3 ' "$out" || echo 0)"

mv "$w/remote" "$w/remote.gone"   # a second seed must not touch the remote at all
out=$(seedenv seed --stack "$w/stack" --dir "$m"); rc=$?
ok "seed again: idempotent, offline (remotes gone), seeded=0 present=3" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS seeded=0 present=3 ' "$out")" = 1 ] && echo 1 || echo 0)"

envout=$("$tool" env --stack "$w/stack" --dir "$m" 2>"$w/env.err"); rc=$?
ok "env: exit 0, 3 sources, RESULT on stderr, no NOT mirrored line" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS mirrored=3 of 3 ' "$(cat "$w/env.err")")" = 1 ] && [ "$(has 'NOT mirrored' "$(cat "$w/env.err")")" = 0 ] && [ "$(grep -c '^export GIT_CONFIG_KEY_' <<<"$envout")" = 3 ] && has '^export GIT_CONFIG_COUNT=3$' "$envout" || echo 0)"
if unshare -rn true 2>/dev/null; then
	# no network namespace, no test insteadOf: only the tool's env can make the fake https url fetchable
	res=$( (unset GIT_CONFIG_COUNT; eval "$envout"; unshare -rn bash -c '
		set -e; t=$(mktemp -d); git init -q "$t"
		git -C "$t" fetch -q --depth 1 https://fake.invalid/org/Gamma.x '"${sha[Gamma.x]}"'
		git -C "$t" checkout -q FETCH_HEAD; git -C "$t" rev-parse HEAD; rm -rf "$t"') 2>&1)
	ok "env: git fetch of the fake https url succeeds under unshare -rn and checks out the pinned sha" "$([ "$res" = "${sha[Gamma.x]}" ] && echo 1 || echo 0)"
	res=$( (unset GIT_CONFIG_COUNT; unshare -rn bash -c 't=$(mktemp -d); git init -q "$t"; git -C "$t" fetch -q --depth 1 https://fake.invalid/org/Gamma.x '"${sha[Gamma.x]}"' && echo FETCHED') 2>&1)
	ok "control: without the env the same fetch fails under unshare -rn" "$(has FETCHED "$res" | grep -q 1 && echo 0 || echo 1)"
else
	echo "SKIPPED: unshare -rn unavailable on this host (the no-network fetch rows)"
fi
res=$( (export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=a.b GIT_CONFIG_VALUE_0=c; eval "$("$tool" env --stack "$w/stack" --dir "$m" 2>/dev/null)"; echo "$GIT_CONFIG_COUNT") )
ok "env: keeps a preexisting GIT_CONFIG_COUNT (index continues: 1 + 3 = 4)" "$([ "$res" = 4 ] && echo 1 || echo 0)"
ok "env output is shell-quoted (eval round trip keeps the mirror path with a space)" "$(
	"$tool" env --stack "$w/stack" --dir "$w/mir ror" >/dev/null 2>&1; cp -r "$m" "$w/mir ror"
	e=$("$tool" env --stack "$w/stack" --dir "$w/mir ror" 2>/dev/null); (eval "$e"; [ "$GIT_CONFIG_KEY_0" = "url.$w/mir ror/alpha.git.insteadOf" ] && echo 1 || echo 0))"

# drift: a stager with no parsable sha / no url / an ambiguous fetch is an ERROR naming the file, never skipped or guessed (F1)
mkstack "$w/drift"; printf '#!/bin/bash\nx_sha=notahexvalue\ngit fetch https://fake.invalid/org/delta "$x_sha"\n' >"$w/drift/99-stage-bad.sh"
out=$("$tool" seed --stack "$w/drift" --dir "$w/m2" 2>&1); rc=$?
ok "no parsable sha: RESULT: ERROR exit 2 naming the file" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/drift/99-stage-bad.sh: .x_sha is not assigned a <40 hex>" "$out" || echo 0)"
out=$("$tool" env --stack "$w/drift" --dir "$m" 2>&1 >/dev/null); rc=$?
ok "env: same drift is RESULT: ERROR exit 2 with nothing on stdout" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/drift/99-stage-bad.sh" "$out" || echo 0)"
# stager forms the old first-match parse got wrong (review xnub-mirror-r1 F1): each must be ERROR naming the file, never PASS with a wrong pair
stg() {   # stg NAME 'file content': a one-stager stack $w/s-NAME, run `seed` on it
	rm -rf "$w/s-$1"; mkdir -p "$w/s-$1"; printf '%s\n' "$2" >"$w/s-$1/00-stage-x.sh"
	out=$("$tool" seed --stack "$w/s-$1" --dir "$w/ms-$1" 2>&1); rc=$?
}
A1=${sha[alpha]} B1=${sha[beta]}
stg nofetch '#!/bin/bash
x_sha='"$A1"
ok "no fetch call: RESULT: ERROR exit 2, 0 fetch calls, names the file" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/s-nofetch/00-stage-x.sh has 0 git fetch/clone calls" "$out" || echo 0)"
stg cmturl '#!/bin/bash
# see https://example.com/docs/readme
old_sha='"$B1"'
new_sha='"$A1"'
git fetch https://github.com/o/a "$new_sha"
git fetch https://github.com/o/b "$other_sha"'
ok "reviewer counter-example (url in a comment + two sha assignments + two fetches): ERROR, not PASS mirrored=0" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/s-cmturl/00-stage-x.sh has 2 git fetch/clone calls" "$out" || echo 0)"
stg twofetch '#!/bin/bash
x_sha='"$A1"'
git fetch https://github.com/o/a "$x_sha"
git fetch https://github.com/o/b "$x_sha"'
ok "two fetch calls in one stager: ERROR exit 2" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/s-twofetch/00-stage-x.sh has 2 git fetch/clone calls" "$out" || echo 0)"
stg twosha '#!/bin/bash
x_sha='"$B1"'
x_sha='"$A1"'
git fetch https://github.com/o/a "$x_sha"'
ok "two assignments of the sha variable: ERROR exit 2 (the old parse took the first)" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/s-twosha/00-stage-x.sh has 2 assignments of .x_sha" "$out" || echo 0)"
stg cmturl1 '#!/bin/bash
# see https://example.com/docs/readme
x_sha='"$A1"'
git fetch https://github.com/o/a "$x_sha"'
ok "a comment url BEFORE the real fetch is ignored: the fetch url is used (no ERROR, seed targets mirror a)" "$([ "$(has 'seed FAILED a ' "$out")" = 1 ] && [ "$(has 'example.com' "$out")" = 0 ] && echo 1 || echo 0)"
stg cmtonly '#!/bin/bash
# fetched from https://github.com/o/a
x_sha='"$A1"'
git fetch "$url" "$x_sha"'
ok "url only in a comment (fetch via a variable): ERROR exit 2, no https:// outside comments" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/s-cmtonly/00-stage-x.sh has no https:// url outside comments" "$out" || echo 0)"
stg indirect '#!/bin/bash
base=https://github.com/o
x_sha='"$A1"'
git fetch "$base/hfs" "$x_sha"'
ok "url behind a variable (base=https://...; fetch \"\$base/hfs\"): ERROR exit 2, not a wrong mirror name" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/s-indirect/00-stage-x.sh: its fetch call is not" "$out" || echo 0)"
stg literal '#!/bin/bash
git fetch https://github.com/o/a '"$A1"
ok "literal sha instead of a variable: ERROR exit 2" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/s-literal/00-stage-x.sh: its fetch call is not" "$out" || echo 0)"
stg stray '#!/bin/bash
x_sha='"$A1"'
git fetch https://github.com/o/a "$x_sha"
python3 -c "print(\"https://example.com/other\")"'
ok "a second https:// token in live code: ERROR exit 2 naming both" "$([ $rc = 2 ] && has "^RESULT: ERROR $w/s-stray/00-stage-x.sh has several https:// tokens .*example.com/other" "$out" || echo 0)"
stg clone '#!/bin/bash
x_sha='"$A1"'
git clone -q https://github.com/o/a "$x_sha"'
ok "git clone with the sha variable parses like fetch (no ERROR; the unreachable seed FAILS loudly instead)" "$([ $rc = 1 ] && has "^xnu-src-mirror: seed FAILED a $A1 " "$out" || echo 0)"
stg wrongvar '#!/bin/bash
other_sha='"$B1"'
x_sha=nothex
git fetch https://github.com/o/a "$x_sha"'
ok "the sha of the variable the fetch uses is taken, not any other *_sha line: ERROR (x_sha not hex)" "$([ $rc = 2 ] && has "x_sha is not assigned a <40 hex>" "$out" || echo 0)"
mkstack "$w/nourl"
mkdir -p "$w/empty"; out=$("$tool" seed --stack "$w/empty" --dir "$w/m2" 2>&1); rc=$?
ok "no stagers at all: RESULT: ERROR exit 2" "$([ $rc = 2 ] && has '^RESULT: ERROR no \*.sh stagers' "$out" || echo 0)"

# a mirror lacking the pinned sha: loud line, no rewrite for that source, the others stay
mkstack "$w/newpin"; cp -r "$m" "$w/m3"
git init -q "$w/remote.gone/zeta"; echo z >"$w/remote.gone/zeta/f"; g -C "$w/remote.gone/zeta" add f; g -C "$w/remote.gone/zeta" commit -q -m z
zs=$(git -C "$w/remote.gone/zeta" rev-parse HEAD)
sed -i "s|^x_sha=.*|x_sha=$zs|" "$w/newpin/01-stage-beta.sh"
envout=$("$tool" env --stack "$w/newpin" --dir "$w/m3" 2>"$w/env2.err"); rc=$?
ok "missing pinned sha: exit 0, stderr 'NOT mirrored: beta <sha>', mirrored=2 of 3" "$([ $rc = 0 ] && [ "$(has "^xnu-src-mirror: NOT mirrored: beta $zs\$" "$(cat "$w/env2.err")")" = 1 ] && has '^RESULT: PASS mirrored=2 of 3 ' "$(cat "$w/env2.err")" || echo 0)"
ok "missing pinned sha: no rewrite for beta, rewrites for alpha and Gamma.x" "$([ "$(has 'fake.invalid/org/beta' "$envout")" = 0 ] && [ "$(has 'fake.invalid/org/alpha' "$envout")" = 1 ] && [ "$(has 'fake.invalid/org/Gamma.x' "$envout")" = 1 ] && [ "$(has '^export GIT_CONFIG_COUNT=2$' "$envout")" = 1 ] && echo 1 || echo 0)"
envout=$("$tool" env --stack "$w/stack" --dir "$w/nodir" 2>"$w/env3.err"); rc=$?
ok "empty/missing mirror dir: 3 NOT mirrored lines, no stdout, exit 0" "$([ $rc = 0 ] && [ -z "$envout" ] && [ "$(grep -c 'NOT mirrored' "$w/env3.err")" = 3 ] && echo 1 || echo 0)"
out=$(seedenv seed --stack "$w/newpin" --dir "$w/m3"); rc=$?
ok "seed with an unreachable remote: RESULT: FAIL exit 1, FAILED line names the source" "$([ $rc = 1 ] && [ "$(has '^xnu-src-mirror: seed FAILED beta ' "$out")" = 1 ] && has '^RESULT: FAIL seeded=0 present=2 failed=1 ' "$out" || echo 0)"

# invalid names refused
for badn in 'x%y' 'x;y' '..'; do
	mkdir -p "$w/badn"; rm -f "$w/badn"/*; printf '#!/bin/bash\nx_sha=%s\ngit fetch "https://fake.invalid/org/%s" "$x_sha"\n' "${sha[alpha]}" "$badn" >"$w/badn/00-stage-x.sh"
	out=$("$tool" env --stack "$w/badn" --dir "$w/m2" 2>&1); rc=$?
	ok "invalid mirror name '$badn': RESULT: ERROR exit 2" "$([ $rc = 2 ] && has '^RESULT: ERROR .*invalid mirror name' "$out" || echo 0)"
done
mkdir -p "$w/dup"; printf '#!/bin/bash\nx_sha=%s\ngit fetch https://b.invalid/o/same "$x_sha"\n' "${sha[alpha]}" >"$w/dup/00-stage-x.sh"
sed 's|b.invalid|c.invalid|' "$w/dup/00-stage-x.sh" >"$w/dup/01-stage-y.sh"
out=$("$tool" env --stack "$w/dup" --dir "$w/m2" 2>&1); rc=$?
ok "one mirror name used by two urls: RESULT: ERROR exit 2" "$([ $rc = 2 ] && has 'used by two urls' "$out" || echo 0)"
out=$("$tool" bogus 2>&1); rc=$?
ok "bad subcommand: RESULT: ERROR usage exit 2" "$([ $rc = 2 ] && has '^RESULT: ERROR usage' "$out" || echo 0)"
# the real stack parses (no network: env only reads refs; an empty dir lists them as NOT mirrored)
out=$("$tool" env --dir "$w/nodir" 2>&1 >/dev/null); rc=$?
ok "the real xnu/patches stack parses: 3 sources (libpthread, hfs, BearSSL)" "$([ $rc = 0 ] && [ "$(grep -c 'NOT mirrored' <<<"$out")" = 3 ] && has 'mirrored=0 of 3 ' "$out" || echo 0)"

# F5: ARTIFACTS is made absolute at the top of xnu-local-build (stage_tree cd's into the work tree, a relative path would land inside it). Run the real
# script (no heavy-job, no build: it stops at `mkdir -p $LB` because /proc is not writable) with a relative path and read the path it reports.
xlb=${XLB_SCRIPT:-$here/tools/xnu-local-build}
rel=$(realpath -m --relative-to="$(dirname "$xlb")/.." /proc/xnu-mirror-test)
out=$(cd "$here" && env -u LOCALBUILD HEAVY_JOB_HELD=1 XLB_INNER=1 ARTIFACTS="$rel" bash "$xlb" 2>&1); rc=$?
ok "xnu-local-build: relative ARTIFACTS ($rel) is reported as the absolute /proc/xnu-mirror-test/localbuild" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR cannot create LOCALBUILD /proc/xnu-mirror-test/localbuild$' "$out")" = 1 ] && echo 1 || echo 0)"

# F3: the real stage_tree() of xnu-local-build (extracted from the script text, so a change to its mirror lines is under test) against fake stagers:
# the mirror must be seeded and its env applied BEFORE each stager runs, an incomplete mirror and a failing stager must be loud.
[ -r "$xlb" ] || { echo "RESULT: ERROR test setup: cannot read $xlb"; exit 2; }
{ sed -n '/^finish() /p;/^say() /p' "$xlb"; sed -n '/^stage_tree() {$/,/^}$/p' "$xlb"; } >"$w/funcs.sh"
[ "$(grep -c '^stage_tree() {$\|^finish() \|^say() ' "$w/funcs.sh")" = 3 ] && grep -q 'xnu-src-mirror' "$w/funcs.sh" || { echo "RESULT: ERROR test setup: could not extract finish/say/stage_tree from $xlb"; exit 2; }
cat >"$w/harness.sh" <<'HEOF'
set -euo pipefail
shopt -s nullglob
source "$FUNCS"
START=$(date +%s); HB=$H/hb; LB=$H/lb; MACOS_VERSION=13.5; KDKROOT=$LB/no-kdk
stagers=("$stack"/*.sh); ours=()
mkdir -p "$W"
stage_tree "$H/tree"
echo "HARNESS: stage_tree returned"
HEOF
runstage() {   # runstage NAME STACKDIR [seedenv]: fresh sandbox $w/h-NAME; the stagers append their evidence to $REC; out in $hout, rc in $hrc
	H=$w/h-$1; rm -rf "$H"; mkdir -p "$H/pristine/bsd/sys" "$H/pristine/libsyscall" "$H/pristine/makedefs" "$H/pristine/SETUP/config" "$H/pristine/osfmk/kern"
	for f in bsd/sys/make_symbol_aliasing.sh libsyscall/Libsyscall.xcconfig bsd/sys/Makefile makedefs/MakeInc.def makedefs/MakeInc.cmd SETUP/config/doconf osfmk/kern/kalloc.h; do echo x >"$H/pristine/$f"; done
	mkdir -p "$H/A/scratch"; : >"$H/rec"
	local tenv=()
	[ "${3:-}" != seedenv ] || tenv=(GIT_CONFIG_COUNT=3
		"GIT_CONFIG_KEY_0=url.$w/remote.gone/alpha.insteadOf" GIT_CONFIG_VALUE_0=https://fake.invalid/org/alpha
		"GIT_CONFIG_KEY_1=url.$w/remote.gone/beta.insteadOf" GIT_CONFIG_VALUE_1=https://fake.invalid/org/beta
		"GIT_CONFIG_KEY_2=url.$w/remote.gone/Gamma.x.insteadOf" GIT_CONFIG_VALUE_2=https://fake.invalid/org/Gamma.x)
	hout=$(env "${tenv[@]}" FUNCS="$w/funcs.sh" H="$H" ROOT="$here" stack="$2" A="$H/A" LOG="$H/A/scratch/log" P="$H/pristine" W="$H/W" REC="$H/rec" MIR="$H/A/git-mirror" \
		bash "$w/harness.sh" 2>&1); hrc=$?
	hlog=$(cat "$H/A/scratch/log" 2>/dev/null); hrec=$(cat "$H/rec")
}
recstack() {   # recstack DIR: mkstack plus, after each fetch call, evidence lines: stager name, GIT_CONFIG_* seen, whether the pinned ref is already in the mirror
	mkstack "$1"
	for f in "$1"/*.sh; do
		printf 'echo "RAN $(basename "$0")" >>"$REC"\nenv | grep -E "^GIT_CONFIG_(KEY|VALUE)_" >>"$REC" || true\nfor m in "$MIR"/*.git; do git --git-dir="$m" for-each-ref --format="PINNED %%(refname)" refs/pinned >>"$REC"; done\n' >>"$f"
	done
}
recstack "$w/rstack"
runstage order "$w/rstack" seedenv
ok "stage_tree: with a seedable network, harness ends rc 0 and says '3 of 3 sources from the local mirror', no 'mirror incomplete'" "$([ $hrc = 0 ] && [ "$(has 'xnu-src-mirror: 3 of 3 sources from the local mirror' "$hlog")" = 1 ] && [ "$(has 'mirror incomplete' "$hlog$hout")" = 0 ] && has 'HARNESS: stage_tree returned' "$hout" || echo 0)"
ok "stage_tree: seed BEFORE the stagers: when each of the 3 stagers ran, the pinned refs of the mirror existed (3 refs seen by the last stager)" "$([ "$(grep -c '^RAN ' <<<"$hrec")" = 3 ] && [ "$(grep -c "^PINNED refs/pinned/${sha[Gamma.x]}\$" <<<"$hrec")" -ge 1 ] && [ "$(awk '/^RAN /{r++} /^PINNED/{if (r==1) p++} END{print p+0}' <<<"$hrec")" = 3 ] && echo 1 || echo 0)"
ok "stage_tree: env BEFORE the stagers: every stager sees the url.<mirror>.insteadOf keys for its mirror path" "$([ "$(grep -c "^GIT_CONFIG_KEY_[0-9]*=url.$H/A/git-mirror/.*\.git\.insteadOf\$" <<<"$hrec")" -ge 3 ] && echo 1 || echo 0)"
runstage nomirror "$w/rstack"
ok "stage_tree: unseedable mirror is loud, not silent: 'mirror incomplete (alpha beta Gamma.x )', '0 of 3', stagers still run (network path)" "$([ "$(has 'xnu-src-mirror: 0 of 3 sources from the local mirror' "$hlog")" = 1 ] && [ "$(has 'xnu-local-build: mirror incomplete (alpha beta Gamma.x ), stagers need the network' "$hlog")" = 1 ] && [ "$(grep -c '^RAN ' <<<"$hrec")" = 3 ] && [ "$(has 'seed FAILED' "$hlog")" = 1 ] && echo 1 || echo 0)"
mkdir -p "$w/badstack"; cp "$w/rstack"/*.sh "$w/badstack/"; printf '#!/bin/bash\nx_sha=%s\ngit fetch https://fake.invalid/org/zed "$x_sha"\n' "${sha[alpha]}" >"$w/badstack/05-nofetch-ok.sh"; sed -i 's|^git fetch.*||;s|^    https.*||' "$w/badstack/05-nofetch-ok.sh"
runstage envfail "$w/badstack"
ok "stage_tree: xnu-src-mirror env failing (drift) is loud: 'mirror incomplete (xnu-src-mirror env failed, see LOG)', the ERROR line is in the log" "$([ "$(has 'xnu-local-build: mirror incomplete (xnu-src-mirror env failed, see ' "$hlog")" = 1 ] && [ "$(has '^RESULT: ERROR .*05-nofetch-ok.sh has 0 git fetch/clone calls' "$hlog")" = 1 ] && echo 1 || echo 0)"
recstack "$w/failstack"; printf 'exit 1\n' >>"$w/failstack/01-stage-beta.sh"
runstage stagerfail "$w/failstack" seedenv
ok "stage_tree: a failing stager aborts the build loudly: rc 2, 'RESULT: ERROR stager 01-stage-beta.sh failed (network?)', the next stager did not run" "$([ $hrc = 2 ] && [ "$(has '^RESULT: ERROR stager 01-stage-beta.sh failed (network?)' "$hout")" = 1 ] && [ "$(has 'HARNESS: stage_tree returned' "$hout")" = 0 ] && [ "$(grep -c '^RAN ' <<<"$hrec")" = 2 ] && echo 1 || echo 0)"

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
