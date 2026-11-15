#!/bin/bash
# Tests of tools/offload-conf.sh + tools/offload_conf.py (T-dd93, R66): the host config loader. Last line RESULT: PASS|FAIL offload-conf.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd); w=$(mktemp -d); trap 'rm -rf "$w"' EXIT
fails=0; n=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
unset OFFLOAD_HOST OFFLOAD_USER OFFLOAD_ROOT OFFLOAD_FOREIGN_RE LAB_HOME REMOTE_JOB_HOST OFFLOAD_CONF
sh_get() { ( . "$here/tools/offload-conf.sh"; offload_conf_load "$here"; printf '%s' "${!1:-}" ) 2>"$w/err"; }   # KEY: value after loading (stderr in $w/err)
py_get() { python3 -I -c 'import sys;sys.path.insert(0,sys.argv[1]);import offload_conf;print(offload_conf.load(sys.argv[2])[0].get(sys.argv[3],""),end="")' "$here/tools" "$here" "$1" 2>"$w/perr"; }
cat >"$w/c.conf" <<'C'
# comment
OFFLOAD_HOST=h.example
OFFLOAD_USER=u
OFFLOAD_ROOT=/srv/x/pixos
OFFLOAD_FOREIGN_RE=vitest|a b.*(c)
LAB_HOME=
BOGUS=1
not a pair
C
export OFFLOAD_CONF=$w/c.conf
ok "sh: host gets the user prefix" "$([ "$(sh_get OFFLOAD_HOST)" = u@h.example ] && echo 1 || echo 0)"
ok "sh: unknown key and non-pair line are loud (OFFLOAD GAP), not silent" "$(grep -q 'unknown key BOGUS ignored' "$w/err" && grep -q 'ignored line without KEY=VALUE' "$w/err" && echo 1 || echo 0)"
ok "sh: value with | space ( ) arrives intact, never evaluated" "$([ "$(sh_get OFFLOAD_FOREIGN_RE)" = 'vitest|a b.*(c)' ] && echo 1 || echo 0)"
lh=$( export HOME="$w/fakehome"; sh_get LAB_HOME )
ok "sh: empty LAB_HOME falls back to the home directory" "$([ "$lh" = "$w/fakehome" ] && echo 1 || echo 0)"
ok "sh: environment beats the file" "$([ "$(OFFLOAD_ROOT=/env/root/x sh_get OFFLOAD_ROOT)" = /env/root/x ] && echo 1 || echo 0)"
ok "py: root, user, regex equal the sh loader" "$([ "$(py_get OFFLOAD_ROOT)" = "$(sh_get OFFLOAD_ROOT)" ] && [ "$(py_get OFFLOAD_USER)" = u ] && [ "$(py_get OFFLOAD_FOREIGN_RE)" = 'vitest|a b.*(c)' ] && echo 1 || echo 0)"
ok "py: host stays raw (offload-sync composes user@host itself); unknown key loud" "$([ "$(py_get OFFLOAD_HOST)" = h.example ] && grep -q 'unknown key BOGUS ignored' "$w/perr" && echo 1 || echo 0)"
printf 'echo pwned >%s\nOFFLOAD_HOST=$(touch %s)\n' "$w/pwned" "$w/pwned2" >"$w/evil.conf"
OFFLOAD_CONF=$w/evil.conf sh_get OFFLOAD_HOST >/dev/null; OFFLOAD_CONF=$w/evil.conf py_get OFFLOAD_HOST >/dev/null
ok "a hostile config line runs nothing in either loader" "$([ ! -e "$w/pwned" ] && [ ! -e "$w/pwned2" ] && echo 1 || echo 0)"
ok "OFFLOAD_CONF=none: no file read, no host" "$([ -z "$(OFFLOAD_CONF=none sh_get OFFLOAD_HOST)" ] && [ -z "$(OFFLOAD_CONF=none py_get OFFLOAD_HOST)" ] && echo 1 || echo 0)"
ok "a named but missing file: nothing loaded, no crash" "$([ -z "$(OFFLOAD_CONF=$w/none.conf sh_get OFFLOAD_HOST)" ] && echo 1 || echo 0)"
mkdir -p "$w/main/tools" "$w/wt"; git -C "$w/main" init -q; printf 'OFFLOAD_ROOT=/from/main/clone\n' >"$w/main/tools/offload-host.conf"
git -C "$w/main" -c user.email=t@t -c user.name=t commit -q --allow-empty -m i; git -C "$w/main" worktree add -q "$w/wt/x" -b x 2>/dev/null
unset OFFLOAD_CONF
ok "a worktree reads the config of the MAIN clone (git common dir)" "$([ "$( . "$here/tools/offload-conf.sh"; offload_conf_load "$w/wt/x"; printf '%s' "$OFFLOAD_ROOT")" = /from/main/clone ] && echo 1 || echo 0)"
ok "committed example parses and its keys are all known (no OFFLOAD GAP)" "$(OFFLOAD_CONF=$here/tools/offload-host.conf.example sh_get OFFLOAD_HOST >/dev/null; [ ! -s "$w/err" ] && echo 1 || echo 0)"
ok "the real config file is gitignored" "$(git -C "$here" check-ignore -q tools/offload-host.conf && echo 1 || echo 0)"
# ---- per-use switches: the offline file, shell loader and python twin must give the same answer (master / use / absent / missing file / unknown use)
sh_off() { ( . "$here/tools/offload-conf.sh"; offload_conf_load "$here"; offload_off_reason "$here" "$@" ) 2>/dev/null; }   # [USE]: reason or nothing
py_off() { python3 -I -c 'import sys;sys.path.insert(0,sys.argv[1]);import offload_conf;r=offload_conf.off_reason(sys.argv[2],sys.argv[3] or None);print(r or "",end="")' "$here/tools" "$here" "${1:-}" 2>/dev/null; }
uses=$( . "$here/tools/offload-conf.sh"; echo "$OFFLOAD_USES" )
ok "the use list of the shell and python loaders is the same" "$([ "$uses" = "$(python3 -I -c 'import sys;sys.path.insert(0,sys.argv[1]);import offload_conf;print(*offload_conf.USES)' "$here/tools")" ] && echo 1 || echo 0)"
printf 'OFFLOAD_QUEUE_LABEL=lbl\n' >"$w/l.conf"; export OFFLOAD_CONF=$w/l.conf
export OFFLOAD_OFFLINE_FILE=$w/off
printf '# c\nother\tnot us\nlbl:test\ttests are noisy\nlbl:gate\tgate lane busy\n' >"$w/off"
agree=1; for u in "" build gate test sync ci bogus; do [ "$(sh_off $u)" = "$(py_off $u)" ] || { agree=0; echo "  differ for use='$u': sh='$(sh_off $u)' py='$(py_off $u)'"; }; done
ok "per-use rows: sh and py agree for every use" "$agree"
ok "per-use rows: only the listed uses are off" "$([ -n "$(sh_off test)" ] && [ -n "$(sh_off gate)" ] && [ -z "$(sh_off build)" ] && [ -z "$(sh_off sync)" ] && [ -z "$(sh_off ci)" ] && [ -z "$(sh_off)" ] && echo 1 || echo 0)"
ok "per-use rows: the reason is shown" "$(sh_off test | grep -q '^tests are noisy ' && echo 1 || echo 0)"
printf 'lbl\tmaster off\nlbl:test\tuse row\n' >"$w/off"
agree=1; for u in "" build gate test sync ci; do [ "$(sh_off $u)" = "$(py_off $u)" ] || agree=0; done
ok "master row: every use off with the master reason, sh and py agree" "$([ "$agree" = 1 ] && for u in "" build gate test sync ci; do sh_off $u | grep -q '^master off ' || exit 1; done && echo 1 || echo 0)"
printf 'lbl\n' >"$w/off"
ok "master row without a reason: 'no reason given'" "$(sh_off ci | grep -q '^no reason given ' && py_off ci | grep -q '^no reason given ' && echo 1 || echo 0)"
printf 'nothing here\tx\n' >"$w/off"
ok "absent row: host on in both loaders" "$([ -z "$(sh_off test)" ] && [ -z "$(py_off test)" ] && [ -z "$(sh_off)" ] && echo 1 || echo 0)"
rm -f "$w/off"
ok "missing offline file: host on in both loaders" "$([ -z "$(sh_off test)" ] && [ -z "$(py_off test)" ] && echo 1 || echo 0)"
printf 'lbl:test\tx\n' >"$w/off"
gapl=$( . "$here/tools/offload-conf.sh"; offload_conf_load "$here"; offload_off_reason "$here" bogus 2>&1 >/dev/null )
ok "unknown use is loud (GAP on stderr) and checks the master only" "$(grep -q 'OFFLOAD GAP: unknown offload use' <<<"$gapl" && [ -z "$(sh_off bogus)" ] && echo 1 || echo 0)"
ok "OFFLOAD_OFFLINE_FILE=none ignores the file" "$([ -z "$(OFFLOAD_OFFLINE_FILE=none sh_off test)" ] && [ -z "$(OFFLOAD_OFFLINE_FILE=none py_off test)" ] && echo 1 || echo 0)"
if [ "$fails" = 0 ]; then echo "RESULT: PASS offload-conf $n checks"; else echo "RESULT: FAIL offload-conf $fails of $n failed"; exit 1; fi
