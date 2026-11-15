#!/bin/bash
# Host test of tools/xnu-lb-prune.sh (lab-host work-tree prune of tools/xnu-local-build). Temp dirs only, no real artifacts, no build, no phone.
# Cases: keep-recent ordering, hour window, live cwd, held gate lock, own label, floor pruning (fake free space via XNU_LOCAL_FREE_CMD), foreign-dir guard,
# symlinked work dir, disabled mode, gate-file cleanup. Mutant (drop the own-label keep): LBP_MUTANT=ownkeep (the test applies it to a copy and requires FAIL).
# Last line: RESULT: PASS cases=N | RESULT: FAIL
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
cd "$(dirname "$0")/.." || exit 2
lib=${LBP_LIB:-$PWD/tools/xnu-lb-prune.sh}
if [ -z "${LBP_MUTANT:-}" ]; then
	m=$(mktemp -d) || exit 2; trap 'rm -rf "$m"' EXIT
	sed 's/\[ "\$b" != "\$own" \] || continue/true/' "$lib" >"$m/own.sh"
	cmp -s "$lib" "$m/own.sh" && { echo "RESULT: ERROR mutant ownkeep did not apply"; exit 2; }
	if LBP_MUTANT=1 LBP_LIB=$m/own.sh bash "$0" >/dev/null 2>&1; then echo "RESULT: FAIL mutant ownkeep survived"; exit 1; fi
	echo "mutant ownkeep: killed"
fi
# shellcheck source=tools/xnu-lb-prune.sh
. "$lib"
w=$(mktemp -d) || exit 2
trap 'rm -rf "$w"; [ -z "${m:-}" ] || rm -rf "$m"; [ -z "${sp:-}" ] || kill "$sp" 2>/dev/null; [ -z "${lp:-}" ] || kill "$lp" 2>/dev/null' EXIT
n=0; bad=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; bad=1; fi; }
mk() { # LB label age_hours  (dir + build.log aged)
	mkdir -p "$1/work/$2/x"; head -c 2048 /dev/zero >"$1/work/$2/x/f"; : >"$1/work/$2/build.log"; touch "$1/work/.$2.gate-lock" "$1/work/.$2.gate-xnu-build.log"
	touch -d "$3 hours ago" "$1/work/$2" "$1/work/$2/build.log"
}
fresh() { rm -rf "$w/lb"; mkdir -p "$w/lb/work"; LB=$w/lb; }
has() { [ -d "$LB/work/$1" ] && echo 1 || echo 0; }
unset XNU_LOCAL_PRUNE XNU_LOCAL_FREE_CMD
export XNU_LOCAL_MIN_FREE_GB=0 XNU_LOCAL_PRUNE_HOURS=24 XNU_LOCAL_KEEP_RECENT=2
run() { out=$(lbp_prune "$LB/work" "$LB" own 2>&1); rc=$?; }

fresh; mk "$LB" own 900; for i in 1 2 3 4 5; do mk "$LB" t$i $((100 + i * 10)); done
run
ok "keep-recent=2: t1 t2 (newest) stay, t3 t4 t5 go; own stays even when oldest" "$([ "$(has t1)$(has t2)$(has t3)$(has t4)$(has t5)$(has own)" = 110001 ] && echo 1 || echo 0)"
ok "summary lines: pruned 3 ... t3, t4, t5 / kept 2 recent: t1, t2" "$(grep -q '^XNU LOCAL: pruned 3 work tree(s) (0.0 GB): t3, t4, t5$' <<<"$out" && grep -q '^XNU LOCAL: kept 2 recent: t1, t2 ' <<<"$out" && echo 1 || echo 0)"
ok "gate files of pruned labels removed, of kept labels stay" "$([ ! -e "$LB/work/.t3.gate-lock" ] && [ ! -e "$LB/work/.t5.gate-xnu-build.log" ] && [ -e "$LB/work/.t1.gate-lock" ] && echo 1 || echo 0)"

fresh; mk "$LB" own 1; mk "$LB" a 100; mk "$LB" b 200; mk "$LB" c 300
printf '2020-01-01T00:00:00Z\tsha\tc\tlab-host-local\t\n' >"$LB/built-on.tsv"; printf '%s\tsha\tc\tlab-host-local\t\n' "$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)" >>"$LB/built-on.tsv"
XNU_LOCAL_KEEP_RECENT=0 run
ok "ledger: c's last built-on line is 1 h old -> c stays (hour window), a and b go" "$([ "$(has a)$(has b)$(has c)" = 001 ] && echo 1 || echo 0)"

fresh; mk "$LB" own 1; mk "$LB" in 20; mk "$LB" out 30
XNU_LOCAL_KEEP_RECENT=0 run
ok "hour window 24: a 20 h tree stays, a 30 h tree goes" "$([ "$(has in)$(has out)" = 10 ] && echo 1 || echo 0)"
XNU_LOCAL_KEEP_RECENT=0 XNU_LOCAL_PRUNE_HOURS=10 run
ok "hour window 10: the 20 h tree goes too" "$([ "$(has in)" = 0 ] && echo 1 || echo 0)"

fresh; mk "$LB" own 1; mk "$LB" cw 500; mk "$LB" lk 500; mk "$LB" gone 500
( cd "$LB/work/cw/x" && exec sleep 300 ) & sp=$!
( exec 9>>"$LB/work/.lk.gate-lock"; flock -n 9; exec sleep 300 ) & lp=$!
sleep 1
XNU_LOCAL_KEEP_RECENT=0 run
kill $sp $lp 2>/dev/null; sp=
ok "live cwd inside a tree keeps it; held gate lock keeps it; the idle old tree goes" "$([ "$(has cw)$(has lk)$(has gone)" = 110 ] && echo 1 || echo 0)"

fresh; mk "$LB" own 1; mk "$LB" a 100; mk "$LB" b 200; mk "$LB" c 300; mk "$LB" d 400
printf '4\n' >"$w/free"   # fake free GB: 4 at start, +10 per tree gone
cat >"$w/freecmd" <<EOT
#!/bin/bash
echo \$((4 + 10 * (4 - \$(ls -d $LB/work/*/ | wc -l) + 1)))
EOT
chmod +x "$w/freecmd"
XNU_LOCAL_KEEP_RECENT=3 XNU_LOCAL_MIN_FREE_GB=20 XNU_LOCAL_FREE_CMD=$w/freecmd run
ok "floor 20 GB: keep-recent 3 keeps a b c, d is old; the floor then prunes oldest-first (c) until free >= 20; a b own stay" "$([ "$(has own)$(has a)$(has b)$(has c)$(has d)" = 11100 ] && echo 1 || echo 0)"
fresh; mk "$LB" own 1; mk "$LB" a 100; mk "$LB" fresh 2
XNU_LOCAL_KEEP_RECENT=5 XNU_LOCAL_MIN_FREE_GB=999 XNU_LOCAL_FREE_CMD="echo 3" run
ok "floor unreachable: GAP line, rc 0, own + hour-window trees stay, a goes" "$([ $rc = 0 ] && grep -q '^XNU LOCAL GAP: free 3 GB < floor 999 GB' <<<"$out" && [ "$(has own)$(has fresh)$(has a)" = 110 ] && echo 1 || echo 0)"

fresh; mk "$LB" own 1; mk "$LB" a 100; mkdir -p "$w/foreign/f1"
out=$(lbp_prune "$w/foreign" "$LB" own 2>&1); rc=$?
ok "foreign dir: rc 98 REFUSING, nothing deleted" "$([ $rc = 98 ] && grep -q '^XNU LOCAL: REFUSING to prune' <<<"$out" && [ -d "$w/foreign/f1" ] && echo 1 || echo 0)"
rm -rf "$w/lb2"; mkdir -p "$w/lb2" "$w/real/t"; ln -s "$w/real" "$w/lb2/work"
out=$(lbp_prune "$w/lb2/work" "$w/lb2" own 2>&1)
ok "symlinked work dir: WARNING, target intact" "$(grep -q 'is a symlink, prune skipped' <<<"$out" && [ -d "$w/real/t" ] && echo 1 || echo 0)"

fresh; mk "$LB" own 1; mk "$LB" a 100
XNU_LOCAL_PRUNE=0 XNU_LOCAL_KEEP_RECENT=0 run
ok "XNU_LOCAL_PRUNE=0: one loud DISABLED line, nothing deleted" "$(grep -q '^XNU LOCAL: work-tree prune DISABLED' <<<"$out" && [ "$(has a)" = 1 ] && echo 1 || echo 0)"

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; else echo "RESULT: FAIL cases=$n"; exit 1; fi
