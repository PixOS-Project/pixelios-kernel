#!/bin/bash
# Host test of the public-by-default enforcement (T-6070, RULES R7): the pre-commit and commit-msg hooks of tools/hooks and the gate step
# `public-guard` (tools/public-push-guard gate) block a leaked host name, private IP, /home path, .env value, host-named path, commit message and branch name.
# Temp repos only, no network, seconds. Host-specific words are built from pieces so this file does not trip the guard's own rules.
# Run: bash tools/test_public_by_default.sh
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
w=$(mktemp -d); trap 'rm -rf "$w"' EXIT
export HOME=$w/home GIT_CONFIG_GLOBAL=$w/gitconfig GIT_CONFIG_SYSTEM=/dev/null; mkdir -p "$HOME"
unset T3CODE_THREAD_ID PIXOS_ALLOW_NO_TRAILER PIXOS_ROLES_FILE
git config --global user.email 1+t@users.noreply.github.com; git config --global user.name t; git config --global init.defaultBranch work
host="sm""uks"; ip="192.""168.1.5"; hp="/home/sm""uks/x"; fakesecret="Zq7-fake-secret-value-91x"
pass=0; fail=0
ok() { echo "ok   $1"; pass=$((pass+1)); }
bad() { echo "FAIL $1"; fail=$((fail+1)); }

newrepo() {   # newrepo NAME -> temp repo with the tool, rules, a fake gitignored .env and the tracked hooks active via core.hooksPath
  local r=$w/$1; git init -q "$r"; mkdir -p "$r/tools" "$r/.hooks"
  cp "$here/tools/public-push-guard" "$here/tools/public-push-guard.rules" "$r/tools/"
  cp "$here/tools/hooks/pre-commit" "$here/tools/hooks/commit-msg" "$r/.hooks/"; chmod +x "$r/.hooks/"*
  printf 'KEY=%s\n' "$fakesecret" > "$r/.env"; printf '.env\n.hooks/\n' > "$r/.gitignore"
  echo clean > "$r/a.txt"; PIXOS_ALLOW_NO_TRAILER=1 git -C "$r" add -A >/dev/null 2>&1
  git -C "$r" -c core.hooksPath=/dev/null commit -qm base
  git -C "$r" config core.hooksPath "$r/.hooks"
  echo "$r"
}
commit() { git -C "$1" commit -q --trailer "PixOS-Worker: t" -m "${2:-change}" 2>&1; }   # hooks run; trailer present so only the guard can block
blocked() {   # blocked LABEL RULE FILE CONTENT : the commit must fail in the hook, name the rule, never print the secret
  local r; r=$(newrepo "$1"); mkdir -p "$(dirname "$r/$3")"; printf '%s\n' "$4" > "$r/$3"; git -C "$r" add -A
  local o rc; o=$(commit "$r"); rc=$?
  if [ $rc != 0 ] && grep -q "RESULT: FAIL public-push-guard staged" <<<"$o" && grep -q "$2" <<<"$o" && ! grep -qF "$fakesecret" <<<"$o" && ! grep -qF "$ip" <<<"$o"; then ok "pre-commit blocks $1 ($2)"; else bad "pre-commit $1 ($2) rc=$rc: $o"; fi
  # the same tip committed around the hook must fail the gate step
  git -C "$r" -c core.hooksPath=/dev/null commit -qm tip --trailer "PixOS-Worker: t"
  o=$(cd "$r" && python3 tools/public-push-guard gate HEAD 2>&1); rc=$?
  if [ $rc != 0 ] && grep -q "RESULT: FAIL public-push-guard gate" <<<"$o" && grep -q "$2" <<<"$o"; then ok "gate step fails on $1 ($2)"; else bad "gate $1 ($2) rc=$rc: $o"; fi
}

r=$(newrepo clean); echo fine > "$r/b.txt"; git -C "$r" add -A; o=$(commit "$r"); rc=$?
[ $rc = 0 ] && ok "clean commit passes both hooks" || bad "clean commit rc=$rc: $o"
o=$(cd "$r" && python3 tools/public-push-guard gate HEAD 2>&1); grep -q "^RESULT: PASS public-push-guard gate" <<<"$o" && ok "gate step passes on a clean tip" || bad "gate clean: $o"

blocked host    b-hostname      doc.md   "run it on $host now"
blocked ip      a-private-ip    x.c      "addr = \"$ip\";"
blocked homepath b-home-path    x.sh     "cd $hp"
blocked envvalue e-env-secret   cfg.txt  "token = $fakesecret"
blocked hostpath c-host-tool-path "tools/$host-stage" "harmless"
blocked wififile d-wifi-file    wlan.conf "harmless"

# the *.example shape is the allowed way: placeholders pass
r=$(newrepo example); printf 'TOKEN=<your token>\nHOST=<host>\npsk=<..>\n' > "$r/cfg.example"; git -C "$r" add -A; o=$(commit "$r"); rc=$?
[ $rc = 0 ] && ok "a *.example with placeholders passes" || bad "example rc=$rc: $o"

# commit-msg: message and branch name
r=$(newrepo msg); echo x > "$r/m.txt"; git -C "$r" add -A; o=$(commit "$r" "fix on $host at $ip"); rc=$?
if [ $rc != 0 ] && grep -q "RESULT: FAIL public-push-guard msg" <<<"$o" && grep -q "b-hostname" <<<"$o" && ! grep -qF "$ip" <<<"$o"; then ok "commit-msg blocks a host name in the message"; else bad "msg rc=$rc: $o"; fi
r=$(newrepo escape); echo x > "$r/m.txt"; git -C "$r" add -A; o=$(PIXOS_ALLOW_NO_TRAILER=1 git -C "$r" commit -q -m "ssh $host" 2>&1); rc=$?
[ $rc != 0 ] && grep -q "RESULT: FAIL public-push-guard msg" <<<"$o" && ok "PIXOS_ALLOW_NO_TRAILER does not bypass the message guard" || bad "escape rc=$rc: $o"
r=$(newrepo branch); git -C "$r" checkout -q -b "tools/$host-fix"; echo x > "$r/m.txt"; git -C "$r" add -A; o=$(commit "$r"); rc=$?
[ $rc != 0 ] && grep -q "BRANCH_NAME" <<<"$o" && ok "commit-msg blocks a host name in the branch name" || bad "branch rc=$rc: $o"

# gate step ratchet: a baselined finding passes loudly (PUBLIC GAP), a new one fails, a removed one makes the baseline stale
r=$(newrepo ratchet); echo "ip $ip" > "$r/old.txt"; git -C "$r" add -A; git -C "$r" -c core.hooksPath=/dev/null commit -qm old --trailer "PixOS-Worker: t"
(cd "$r" && python3 tools/public-push-guard gate --write-baseline >/dev/null)
o=$(cd "$r" && python3 tools/public-push-guard gate HEAD 2>&1); rc=$?
[ $rc = 0 ] && grep -q "^PUBLIC GAP: 1 finding" <<<"$o" && ok "baselined finding passes and prints PUBLIC GAP" || bad "baselined rc=$rc: $o"
echo "ip $ip" >> "$r/old.txt"; git -C "$r" add -A; git -C "$r" -c core.hooksPath=/dev/null commit -qm more --trailer "PixOS-Worker: t"
o=$(cd "$r" && python3 tools/public-push-guard gate HEAD 2>&1); rc=$?
[ $rc != 0 ] && grep -q "NEW a-private-ip old.txt" <<<"$o" && ok "a second finding above the baseline fails" || bad "above baseline rc=$rc: $o"
git -C "$r" rm -q -f old.txt; git -C "$r" -c core.hooksPath=/dev/null commit -qm gone --trailer "PixOS-Worker: t"
o=$(cd "$r" && python3 tools/public-push-guard gate HEAD 2>&1); rc=$?
[ $rc != 0 ] && grep -q "STALE a-private-ip old.txt" <<<"$o" && ok "a removed finding makes the baseline stale (ratchet)" || bad "stale rc=$rc: $o"
r=$(newrepo envgate); echo "k $fakesecret" > "$r/e.txt"; git -C "$r" add -A; git -C "$r" -c core.hooksPath=/dev/null commit -qm e --trailer "PixOS-Worker: t"
(cd "$r" && python3 tools/public-push-guard gate --write-baseline >/dev/null); o=$(cd "$r" && python3 tools/public-push-guard gate HEAD 2>&1); rc=$?
[ $rc != 0 ] && ok "an .env value can never be baselined" || bad "env baseline rc=$rc: $o"

# the real tree: the repo this test lives in passes its own gate step
o=$(cd "$here" && python3 tools/public-push-guard gate HEAD 2>&1); grep -q "^RESULT: PASS public-push-guard gate" <<<"$o" && ok "this repo's HEAD passes the gate step" || bad "real HEAD: $(echo "$o" | tail -3)"

echo "$([ $fail = 0 ] && echo "RESULT: PASS public-by-default" || echo "RESULT: FAIL public-by-default") $pass ok, $fail failed"
[ $fail = 0 ]
