#!/bin/bash
# Host test of tools/public-push-guard with temp repos, a fake public bare remote, a fake .env and a private remote (no network, seconds).
# Host-specific words are built from pieces so this file does not trip the guard's own rules.
# Run: bash tools/test_public_push_guard.sh
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
w=$(mktemp -d); trap 'rm -rf "$w"' EXIT
export HOME=$w/home GIT_CONFIG_GLOBAL=$w/gitconfig GIT_CONFIG_SYSTEM=/dev/null; mkdir -p "$HOME"
git config --global user.email 1+t@users.noreply.github.com; git config --global user.name t; git config --global init.defaultBranch main
host="sm""uks"; ip="192.""168.1.5"; fakesecret="Zq7-fake-secret-value-91x"
pass=0; fail=0
ok() { echo "ok   $1"; pass=$((pass+1)); }
bad() { echo "FAIL $1"; fail=$((fail+1)); }

mkdir -p "$w/github.com/PixOS-Project" && git init -q --bare "$w/github.com/PixOS-Project/r.git"
git init -q --bare "$w/priv.git"
newrepo() {   # newrepo NAME -> fresh clone with the tool, hook installed, remotes pub/priv
  local r=$w/$1; git init -q "$r"; mkdir -p "$r/tools"
  cp "$here/tools/public-push-guard" "$here/tools/public-push-guard.rules" "$r/tools/"
  printf 'KEY=%s\nSHORT=ab\n# c\n' "$fakesecret" > "$r/.env"; echo .env > "$r/.gitignore"
  echo clean > "$r/a.txt"; git -C "$r" add -A; git -C "$r" commit -qm base
  git -C "$r" remote add pub "$w/github.com/PixOS-Project/r.git"; git -C "$r" remote add priv "$w/priv.git"
  (cd "$r" && python3 tools/public-push-guard install-hook >/dev/null)
  echo "$r"
}
push() { git -C "$1" push "${2:-pub}" HEAD:refs/heads/"${3:-t}" 2>&1; }
deny() {   # deny LABEL RULE FILE CONTENT : commit, push must fail, output names the rule, never the secret
  local r; r=$(newrepo "$1"); mkdir -p "$(dirname "$r/$3")"; printf '%s\n' "$4" > "$r/$3"
  git -C "$r" add -A; git -C "$r" commit -qm bad; local o; o=$(push "$r")
  if grep -q "RESULT: FAIL public-push-guard" <<<"$o" && grep -q "$2" <<<"$o" && ! grep -qF "$fakesecret" <<<"$o"; then ok "deny $1 ($2)"; else bad "deny $1 ($2): $o"; fi
}

r=$(newrepo clean); o=$(push "$r")
grep -q "RESULT: PASS public-push-guard" <<<"$o" && git --git-dir="$w/github.com/PixOS-Project/r.git" rev-parse -q --verify refs/heads/t >/dev/null && ok "clean push passes and lands" || bad "clean push: $o"

pk=ps"k"; five=5; sr=seri"al"   # fixtures built at runtime so this file is clean under its own rules
deny ip a-private-ip x.c "addr = \"$ip\";"
deny hostname b-hostname x.md "run it on $host now"
deny homepath b-home-path x.sh "cd /home/$host/x"
deny offloadpath c-host-tool-path "tools/$host-stage" "harmless"
deny wififile d-wifi-file etc/wpa_supplicant.conf "harmless"
deny wlanconf d-wifi-file wlan.conf "harmless"
deny psk d-wifi-secret n.conf "${pk}=hunter2hunter2"
deny envsecret e-env-secret doc.md "token is $fakesecret end"
deny imei f-imei n.txt "id 12345678901234$five"
deny serial f-serial n.txt "${sr}no: ABCD1234EF"

r=$(newrepo placeholder); printf 'psk=<..>\npsk=...\npassword=$PASS\npassword=\n' > "$r/p.conf"; git -C "$r" add -A; git -C "$r" commit -qm p
o=$(push "$r"); grep -q "RESULT: PASS public-push-guard" <<<"$o" && ok "placeholder psk passes" || bad "placeholder: $o"
r=$(newrepo callexpr); printf 'version, pik, p''sk = struct.unpack("<I", blob)\n' > "$r/k.py"; git -C "$r" add -A; git -C "$r" commit -qm p
o=$(push "$r"); grep -q "RESULT: PASS public-push-guard" <<<"$o" && ok "psk = call(..) (vendored avbtool) passes" || bad "callexpr: $o"

# history: a secret added then removed in a later commit is still denied (added lines of every pushed commit)
r=$(newrepo hist); echo "$ip" > "$r/h.txt"; git -C "$r" add -A; git -C "$r" commit -qm h; git -C "$r" rm -q h.txt; git -C "$r" commit -qm rm
o=$(push "$r"); grep -q "RESULT: FAIL.*" <<<"$o" && ok "deny content removed again in a later commit" || bad "hist: $o"

r=$(newrepo priv); echo "$ip $host" > "$r/x.txt"; git -C "$r" add -A; git -C "$r" commit -qm x
o=$(push "$r" priv); grep -q "RESULT: PASS public-push-guard skip (private remote)" <<<"$o" && ok "private remote skipped" || bad "private: $o"

r=$(newrepo nohook); rm "$r/tools/public-push-guard"; sed -i "s#^FB=.*#FB='$w/none/public-push-guard'#" "$r/.git/hooks/pre-push"; echo "$ip" > "$r/x.txt"; git -C "$r" add -A; git -C "$r" commit -qm x
o=$(push "$r"); grep -q "RESULT: FAIL public-push-guard missing" <<<"$o" && ok "missing tool + public url: hook fails closed" || bad "missing tool: $o"
o=$(push "$r" priv); grep -q "tool missing, private remote .* allowed" <<<"$o" && git --git-dir="$w/priv.git" rev-parse -q --verify refs/heads/t >/dev/null && ok "missing tool + private url: push allowed" || bad "missing tool private: $o"

# worktree without the tool, recorded fallback present: the guard (not the missing-tool path) refuses
r=$(newrepo fb); cp -r "$r/tools" "$w/fbtools"; sed -i "s#^FB=.*#FB='$w/fbtools/public-push-guard'#" "$r/.git/hooks/pre-push"
rm -r "$r/tools"; printf 'k=%s\n' "$fakesecret" > "$r/leak.txt"; git -C "$r" add -A; git -C "$r" commit -qm leak
o=$(push "$r"); grep -q "RESULT: FAIL public-push-guard [0-9]* finding" <<<"$o" && ! grep -q "missing" <<<"$o" && ! grep -qF "$fakesecret" <<<"$o" && ok "fallback guard refuses a leak without a local tool" || bad "fallback: $o"

r=$(newrepo rep); o=$(cd "$r" && python3 tools/public-push-guard report HEAD --remote none); grep -q "^RESULT: PASS public-push-guard report" <<<"$o" && ok "report mode on a clean tree" || bad "report: $o"

# T-9305: staged during a merge scans only content that is new to BOTH parents; what the merge brings from the other parent is already on it
mergerepo() {   # mergerepo NAME -> conflicted merge of main (holding leaked content) into feat, a.txt still to resolve
  local r; r=$(newrepo "$1"); git -C "$r" checkout -q -b feat; echo feat > "$r/a.txt"; git -C "$r" commit -qam feat
  git -C "$r" checkout -q main; echo main > "$r/a.txt"; printf 'addr = "%s"\nrun on %s\n' "$ip" "$host" > "$r/leak.txt"; mkdir -p "$r/sub"; echo "$ip" > "$r/sub/n.txt"
  git -C "$r" add -A; git -C "$r" commit -qm main; git -C "$r" checkout -q feat; git -C "$r" merge main >/dev/null 2>&1
  echo "$r"
}
r=$(mergerepo mergeok); echo both > "$r/a.txt"; git -C "$r" add -A
o=$(cd "$r" && python3 tools/public-push-guard staged 2>&1); grep -q "^RESULT: PASS public-push-guard staged" <<<"$o" && ok "staged merge of main: content already on main is not rescanned" || bad "merge staged: $o"
r=$(mergerepo mergebad); printf 'both\nnew addr = "%s"\n' "$ip" > "$r/a.txt"; git -C "$r" add -A
o=$(cd "$r" && python3 tools/public-push-guard staged 2>&1); grep -q "^RESULT: FAIL public-push-guard staged 1 finding" <<<"$o" && ok "staged merge: a leak added by the resolution is still denied" || bad "merge staged leak: $o"
r=$(mergerepo mergepath); echo both > "$r/a.txt"; echo x > "$r/etc_wlan.conf"; mv "$r/etc_wlan.conf" "$r/wlan.conf"; git -C "$r" add -A
o=$(cd "$r" && python3 tools/public-push-guard staged 2>&1); grep -q "^RESULT: FAIL public-push-guard staged" <<<"$o" && ok "staged merge: a new file added in the merge is still checked" || bad "merge staged path: $o"

r=$(newrepo secmode); printf 'PIXOS_WIFI_SECURITY=wpa2-psk\nPIXOS_WIFI_PSK=%s\n' "$fakesecret" >> "$r/.env"; printf 'mode wpa2-psk\n' > "$r/a.txt"; git -C "$r" add -A
o=$(cd "$r" && python3 tools/public-push-guard staged 2>&1); grep -q "^RESULT: PASS public-push-guard staged" <<<"$o" && ok "a *_SECURITY mode name (wpa2-psk) in the .env is not a secret value (T-2952)" || bad "security mode: $o"
printf 'mode wpa2-psk %s\n' "$fakesecret" > "$r/a.txt"; git -C "$r" add -A
o=$(cd "$r" && python3 tools/public-push-guard staged 2>&1); grep -q "^RESULT: FAIL public-push-guard staged" <<<"$o" && ok "the PSK key next to a mode name is still denied (T-2952)" || bad "security mode leak: $o"

# T-536a: identity rule. Personal e-mails are built from pieces; the output must never carry the full address.
pers="per""son@exam""ple.org"; mask="p***@exam""ple.org"
mkbad() {   # mkbad NAME -> clean repo + one commit authored AND committed by the personal e-mail
  local r; r=$(newrepo "$1"); echo more > "$r/b.txt"; git -C "$r" add -A
  GIT_AUTHOR_EMAIL=$pers GIT_COMMITTER_EMAIL=$pers git -C "$r" -c core.hooksPath=/dev/null commit -qm personal; echo "$r"
}
r=$(mkbad idpush); o=$(push "$r" pub idbranch)
if grep -q "RESULT: FAIL public-push-guard" <<<"$o" && grep -q "g-identity" <<<"$o" && grep -qF "$mask" <<<"$o" && ! grep -qF "$pers" <<<"$o" && grep -q "do instead: git config --local user.email" <<<"$o" \
   && ! git --git-dir="$w/github.com/PixOS-Project/r.git" rev-parse -q --verify refs/heads/idbranch >/dev/null; then ok "pre-push: a commit with a personal e-mail is refused, masked, with a do-instead"; else bad "id push: $o"; fi
o=$(push "$r" priv); grep -q "skip (private remote)" <<<"$o" && ok "pre-push identity: a private remote is not checked" || bad "id private: $o"
# author noreply but committer personal (rebase / cherry-pick by a wrongly configured clone) is refused too
r=$(newrepo idcommitter); echo m > "$r/b.txt"; git -C "$r" add -A; GIT_COMMITTER_EMAIL=$pers git -C "$r" -c core.hooksPath=/dev/null commit -qm c
o=$(push "$r"); grep -q "g-identity" <<<"$o" && grep -q "committer e-mail" <<<"$o" && ! grep -qF "$pers" <<<"$o" && ok "pre-push: a personal committer e-mail alone is refused" || bad "id committer: $o"
# the gitignored allowlist: a full e-mail and an @domain suffix are accepted; a comment line is not an entry
r=$(mkbad idallow); printf '# extra identities\n%s\n' "$pers" > "$r/tools/public-push-guard.identities"
o=$(push "$r"); grep -q "RESULT: PASS public-push-guard" <<<"$o" && ok "gitignored identities file: an allowed e-mail passes" || bad "id allow: $o"
printf '@exam''ple.org\n' > "$r/tools/public-push-guard.identities"; o=$(push "$r" pub t2); grep -q "RESULT: PASS public-push-guard" <<<"$o" && ok "gitignored identities file: an @domain suffix passes" || bad "id allow domain: $o"
# pre-commit: `identity` (and `staged`) refuse the clone's own identity before a commit exists
r=$(newrepo idstaged); echo m > "$r/b.txt"; git -C "$r" add -A
o=$(cd "$r" && GIT_AUTHOR_EMAIL=$pers python3 tools/public-push-guard staged 2>&1); rc=$?
[ $rc = 1 ] && grep -q "RESULT: FAIL public-push-guard staged" <<<"$o" && grep -q "g-identity" <<<"$o" && ! grep -qF "$pers" <<<"$o" && ok "staged: a personal author e-mail in the environment is refused before the commit" || bad "id staged ($rc): $o"
o=$(cd "$r" && GIT_COMMITTER_EMAIL=$pers python3 tools/public-push-guard identity 2>&1); rc=$?
[ $rc = 1 ] && grep -q "committer e-mail" <<<"$o" && ok "identity: a personal committer e-mail is refused" || bad "id identity committer ($rc): $o"
o=$(cd "$r" && python3 tools/public-push-guard staged 2>&1); grep -q "^RESULT: PASS public-push-guard staged" <<<"$o" && ok "staged: a noreply identity passes" || bad "id staged ok: $o"
git -C "$r" config --local user.email "$pers"; o=$(cd "$r" && python3 tools/public-push-guard staged 2>&1); rc=$?
[ $rc = 1 ] && grep -q "g-identity" <<<"$o" && ok "staged: a repo-local personal user.email is refused" || bad "id staged local ($rc): $o"
# a repo with no public remote (the private super repo shares the pre-commit guard) is not identity-checked
r2=$(newrepo idprivate); git -C "$r2" remote remove pub; echo m > "$r2/b.txt"; git -C "$r2" add -A
o=$(cd "$r2" && GIT_AUTHOR_EMAIL=$pers python3 tools/public-push-guard staged 2>&1); rc=$?
[ $rc = 0 ] && grep -q "NOTE no public remote" <<<"$o" && ! grep -q "g-identity" <<<"$o" && ok "staged: a repo without a public remote is not identity-checked (loud note)" || bad "id private repo ($rc): $o"
# install-hook: refuses a clone whose identity is personal, writes a pre-commit that blocks a commit otherwise
o=$(cd "$r" && python3 tools/public-push-guard install-hook 2>&1); rc=$?
[ $rc = 2 ] && grep -q "RESULT: ERROR public-push-guard install-hook" <<<"$o" && ! grep -qF "$pers" <<<"$o" && ok "install-hook: refuses a clone with a personal identity" || bad "id install-hook ($rc): $o"
git -C "$r" config --local --unset user.email; (cd "$r" && python3 tools/public-push-guard install-hook >/dev/null 2>&1); echo m2 > "$r/b.txt"; git -C "$r" add -A
o=$(GIT_AUTHOR_EMAIL=$pers git -C "$r" commit -qm viahook 2>&1); rc=$?
[ $rc != 0 ] && grep -q "g-identity" <<<"$o" && [ "$(git -C "$r" log -1 --format=%s)" != viahook ] && ok "installed pre-commit hook: git commit with a personal identity is refused" || bad "id hook commit ($rc): $o"
git -C "$r" commit -qm viahook-ok >/dev/null 2>&1 && ok "installed pre-commit hook: a noreply commit goes through" || bad "id hook commit ok"
# `git commit --author=` reaches the hook environment too
echo m3 > "$r/b.txt"; git -C "$r" add -A; o=$(git -C "$r" commit -qm viaauthor --author="X <$pers>" 2>&1); rc=$?
[ $rc != 0 ] && grep -q "g-identity" <<<"$o" && ok "git commit --author=<personal> is refused by the pre-commit hook" || bad "id --author ($rc): $o"
git -C "$r" reset -q --hard
# gate: only BASE..REF counts (old history is not the gate's business)
r=$(newrepo idgate); git -C "$r" branch -q trunk; echo m > "$r/g.txt"; git -C "$r" add -A; git -C "$r" commit -qm clean
o=$(cd "$r" && python3 tools/public-push-guard gate HEAD 2>&1); rc=$?
[ $rc = 0 ] && grep -q "^RESULT: PASS public-push-guard gate" <<<"$o" && ok "gate: a clean trunk..HEAD passes" || bad "id gate clean ($rc): $o"
echo n > "$r/g2.txt"; git -C "$r" add -A; GIT_AUTHOR_EMAIL=$pers git -C "$r" -c core.hooksPath=/dev/null commit -qm planted
o=$(cd "$r" && python3 tools/public-push-guard gate HEAD 2>&1); rc=$?
[ $rc = 1 ] && grep -q "^RESULT: FAIL public-push-guard gate" <<<"$o" && grep -q "NEW g-identity" <<<"$o" && ! grep -qF "$pers" <<<"$o" && ok "gate: a planted bad commit in trunk..HEAD fails, masked" || bad "id gate bad ($rc): $o"
git -C "$r" branch -q -f trunk HEAD; o=$(cd "$r" && python3 tools/public-push-guard gate HEAD 2>&1); rc=$?
[ $rc = 0 ] && ok "gate: the same bad commit, once it is on trunk, is old history and passes" || bad "id gate old ($rc): $o"
o=$(cd "$r" && python3 tools/public-push-guard gate HEAD --base nosuchref 2>&1); rc=$?
[ $rc = 0 ] && grep -q "PUBLIC GAP: gate base 'nosuchref' does not resolve" <<<"$o" && grep -q "(gaps=1)" <<<"$o" && ok "gate: an unresolvable base is a loud gap, not a silent pass" || bad "id gate base ($rc): $o"
# report lists the identity findings of unpushed commits
r=$(mkbad idreport); o=$(cd "$r" && python3 tools/public-push-guard report HEAD --remote none 2>&1)
grep -q "rule g-identity: 1 finding(s)" <<<"$o" && grep -q "^RESULT: FAIL public-push-guard report" <<<"$o" && ! grep -qF "$pers" <<<"$o" && ok "report: lists the identity finding of an unpushed commit (author = committer: one finding), masked" || bad "id report: $o"
# the tracked kernel pre-push hook (core.hooksPath copies never run <git-dir>/hooks/pre-push): same decisions as the installed one
r=$(mkbad idtracked); mkdir -p "$r/.hooks"; cp "$here/tools/hooks/pre-push" "$r/.hooks/"; rm "$r/.git/hooks/pre-push"; git -C "$r" config core.hooksPath "$r/.hooks"
o=$(push "$r"); grep -q "g-identity" <<<"$o" && ok "tracked tools/hooks/pre-push (core.hooksPath) refuses a personal e-mail" || bad "id tracked hook: $o"
rm "$r/tools/public-push-guard"; o=$(push "$r"); grep -q "RESULT: FAIL public-push-guard missing" <<<"$o" && ok "tracked pre-push without the tool: fails closed for a public url" || bad "id tracked missing: $o"
echo "RESULT: $([ $fail = 0 ] && echo PASS || echo FAIL) test_public_push_guard $pass ok, $fail failed"
[ $fail = 0 ]
