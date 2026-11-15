#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, out, ...) are set by the sourced test_heavy_job_lib.sh and its helpers
# Part 'prio' of the tools/heavy-job host test (T-8bf1, T-f4a2): the local queue admits by ticket priority (P0 first .. P4), FIFO inside a level, an aged waiter one level up per MAXAGE (never straight to the front),
# unknown label = P2; status prints prio=Pn per waiter and the admission line names why the head was chosen. Setup + helpers: test_heavy_job_lib.sh.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2

# fake docs/queue/INDEX.tsv (the derived board; priority = column 10): the priority comes from the ticket id in the heavy-job label
{ printf 'id\ttitle\tstatus\towner\tlease\tdeps\tresources\tmodel\tblocked\tpriority\tsummary\teffort\n'
  for r in "T-0000 P0" "T-aaaa P2" "T-bbbb P1" "T-cccc P0" "T-dddd P3" "T-eeee P1" "T-9999 P4"; do
	printf '%s\tt\topen\t-\t-\t-\tnone\tsonnet-5-5\t-\t%s\ts\tmedium\n' "${r% *}" "${r#* }"
  done; } >"$w/INDEX.tsv"
export HEAVY_JOB_QUEUE_INDEX=$w/INDEX.tsv
unset HEAVY_JOB_MAXAGE HEAVY_JOB_REEVAL

# startp TAG LABEL [ENV=VAL...]: a waiter that appends TAG to $w/lorder when its CMD runs; out $w/TAG.out, pid $w/TAG.pid
startp() { local tag=$1 lbl=$2; shift 2
	( env "$@" "$here/tools/heavy-job" run --class analysis --label "$lbl" --wait 120 -- bash -c 'echo '"$tag"' >>'"$w"'/lorder' >"$w/$tag.out" 2>&1 ) & echo $! >"$w/$tag.pid"; }
order() { tr -d '\n' <"$w/lorder"; }

# 1. a P1 waiter queued AFTER a P2 waiter is admitted first
: >"$w/lorder"; holdl 3; startp X x-T-aaaa; lqwait 2; startp Y y-T-bbbb; lqwait 3
hj status
st1=$out
waitl lh X Y
ok "P1 waiter queued after a P2 waiter is admitted first (order Y X)" "$([ "$(order)" = YX ] && echo 1 || echo 0)"
ok "status: waiters in admission order with prio=Pn (P1 waiter #1, P2 waiter #2, holder prio=P2 for an unknown label)" "$(echo "$(has 'local waiter #1: .*label=y-T-bbbb .*prio=P1' "$st1")$(has 'local waiter #2: .*label=x-T-aaaa .*prio=P2' "$st1")$(has 'local holder: .*label=lh .*prio=P2' "$st1")" | grep -q 111 && echo 1 || echo 0)"
ok "admission line names the head and why: 'queue: admitted NNNNNN.y-T-bbbb prio=P1 (best priority ...)'" "$(has '^heavy-job: queue: admitted [0-9]\{6\}\.y-T-bbbb prio=P1 (best priority' "$(cat "$w/Y.out")")"
ok "ticket line: priority and where it came from are printed ('queue: prio=P1 (T-bbbb ...)')" "$(has '^heavy-job: queue: prio=P1 (T-bbbb' "$(cat "$w/Y.out")")"
ok "no ticket left behind (case 1)" "$([ "$(lq)" = 0 ] && echo 1 || echo 0)"

# 2. equal priority is FIFO, P0 before P1, P1 before P3
: >"$w/lorder"; holdl 4; startp D d-T-dddd; lqwait 2; startp B1 b-T-bbbb; lqwait 3; startp B2 e-T-eeee; lqwait 4; startp C c-T-cccc; lqwait 5
waitl lh D B1 B2 C
ok "order by priority, FIFO inside a level: P0 C, then P1 B1 B2 in arrival order, then P3 D (order C B1 B2 D)" "$([ "$(order)" = CB1B2D ] && echo 1 || echo 0)"

# 3. T-f4a2: aging is ONE level per HEAVY_JOB_MAXAGE (eff = max(0, prio - age/MAXAGE); sort key admitted, eff, prio, number), not "older than MAXAGE goes first"
# 3a. P3 D aged 3 levels (eff P0) goes before the fresh P1 Y (eff P1): the old "aged P3 goes first after MAXAGE", restated as 3 levels (MAXAGE 4 s: D waits 12 s+, Y arrives ~1 s before the release)
: >"$w/lorder"; holdl 13.5; startp D d-T-dddd HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 2; sleep 12; startp Y y-T-bbbb HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 3
waitl lh D Y
ok "aged P3 waiter (3 x MAXAGE 4 s = 3 levels, eff P0) goes before the fresh P1 waiter (order D Y)" "$([ "$(order)" = DY ] && echo 1 || echo 0)"
# 3a-ctl. P3 D aged ONE level only (eff P2) does not pass a fresh P1 (this is what the old eff=-1 got wrong for every waiter older than MAXAGE)
: >"$w/lorder"; holdl 6.5; startp D d-T-dddd HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 2; sleep 5; startp Y y-T-bbbb HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 3
waitl lh D Y
ok "P3 aged 1 level (eff P2) is still behind the fresh P1 (order Y D)" "$([ "$(order)" = YD ] && echo 1 || echo 0)"
: >"$w/lorder"; holdl 4; startp D d-T-dddd HEAVY_JOB_MAXAGE=3600; lqwait 2; startp Y y-T-bbbb HEAVY_JOB_MAXAGE=3600; lqwait 3
waitl lh D Y
ok "control: the same two waiters with MAXAGE 3600 are not aged: P1 Y first (order Y D)" "$([ "$(order)" = YD ] && echo 1 || echo 0)"
# (a) a P2 job aged 1 x MAXAGE (eff P1) loses to a fresh P1 (same eff, better prio); status shows the effective level
: >"$w/lorder"; holdl 6.5; startp X x-T-aaaa HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 2; sleep 3; startp Y y-T-bbbb HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 3; sleep 2
HEAVY_JOB_MAXAGE=4 hj status; sta=$out
waitl lh X Y
ok "(a) P2 aged 1 x MAXAGE vs fresh P1: the fresh P1 first (order Y X)" "$([ "$(order)" = YX ] && echo 1 || echo 0)"
ok "(a) status shows the effective level of the aged waiter: 'prio=P2 eff=P1 aged Ns'" "$(has 'waiter #[0-9]: .*label=x-T-aaaa .*prio=P2 eff=P1 aged [0-9]*s' "$sta")"
ok "(a) status: the fresh P1 waiter has no eff= (not aged)" "$(has 'waiter #[0-9]: .*label=y-T-bbbb .*prio=P1$' "$sta")"
# (b) a P2 job aged 2 x MAXAGE (eff P0) beats a fresh P1: bounded wait; the admission line says how far it moved
: >"$w/lorder"; holdl 11.5; startp X x-T-aaaa HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 2; sleep 9; startp Y y-T-bbbb HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 3
waitl lh X Y
ok "(b) P2 aged 2 x MAXAGE vs fresh P1: the aged P2 first (order X Y)" "$([ "$(order)" = XY ] && echo 1 || echo 0)"
ok "(b) admission line: 'admitted ...x-T-aaaa prio=P2 (aged Ns: +2 levels ... eff=P0'" "$(has '^heavy-job: queue: admitted [0-9]\{6\}\.x-T-aaaa prio=P2 (aged [0-9]*s: +2 levels.*eff=P0' "$(cat "$w/X.out")")"
# (c) equal effective level and equal priority: FIFO (both aged 1 level, P2: the older first)
: >"$w/lorder"; holdl 6.5; startp X x-T-aaaa HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 2; sleep 1; startp Z z-T-aaaa HEAVY_JOB_MAXAGE=4 HEAVY_JOB_REEVAL=1; lqwait 3
waitl lh X Z
ok "(c) two P2 jobs aged alike: FIFO (order X Z)" "$([ "$(order)" = XZ ] && echo 1 || echo 0)"
ok "no ticket left behind (case 3)" "$([ "$(lq)" = 0 ] && echo 1 || echo 0)"

# 4. unknown label = P2 (no ticket id; ticket id absent from the INDEX), loud about the second
: >"$w/lorder"; holdl 4; startp D d-T-dddd; lqwait 2; startp U plainlabel; lqwait 3; startp Z z-T-ffff; lqwait 4
waitl lh D U Z
ok "unknown label (no ticket id) and a ticket id missing from the INDEX count as P2: both before P3 D, FIFO between them (order U Z D)" "$([ "$(order)" = UZD ] && echo 1 || echo 0)"
ok "ticket id missing from the INDEX is said, not silent ('prio: T-ffff not in ...INDEX.tsv; P2')" "$(has 'prio: T-ffff not in .*INDEX.tsv.*P2' "$(cat "$w/Z.out")")"
ok "lower-case t-xxxx maps too" "$(: >"$w/lorder"; holdl 3; startp X x-T-aaaa; lqwait 2; startp Y y-t-bbbb; lqwait 3; waitl lh X Y; [ "$(order)" = YX ] && echo 1 || echo 0)"

# 5. INDEX missing: every label is P2, FIFO, and it is said loudly (rule 8)
: >"$w/lorder"; holdl 3; startp X x-T-aaaa HEAVY_JOB_QUEUE_INDEX="$w/nope.tsv"; lqwait 2; startp Y y-T-bbbb HEAVY_JOB_QUEUE_INDEX="$w/nope.tsv"; lqwait 3
waitl lh X Y
ok "INDEX unreadable: plain FIFO (order X Y) and a loud 'prio: INDEX ... unreadable' line" "$([ "$(order)" = XY ] && echo "$(has 'prio: INDEX .*nope.tsv.* unreadable' "$(cat "$w/Y.out")")" | grep -q 1 && echo 1 || echo 0)"

# 6. head-of-line stays: an admitted head that waits for memory is not overtaken by a later P0
cp "$w/mem.small" "$w/mem.fifo"; : >"$w/lorder"
( HEAVY_JOB_MEMINFO=$w/mem.fifo "$here/tools/heavy-job" run --class build --label mh-T-dddd --wait 60 -- bash -c 'echo H >>'"$w"'/lorder' >"$w/mh.out" 2>&1 ) & echo $! >"$w/mh.pid"
lqwait 1
( HEAVY_JOB_MEMINFO=$w/mem.big "$here/tools/heavy-job" run --class build --label ml-T-cccc --wait 60 -- bash -c 'echo L >>'"$w"'/lorder' >"$w/ml.out" 2>&1 ) & echo $! >"$w/ml.pid"
lqwait 2; sleep 3; cp "$w/mem.big" "$w/mem.fifo"
waitl mh ml
ok "admitted head (P3) waiting for memory is not overtaken by a later P0 (order H L)" "$([ "$(order)" = HL ] && echo 1 || echo 0)"

# 7. HEAVY_JOB_MAXAGE must be whole seconds
out=$(HEAVY_JOB_MAXAGE=abc "$here/tools/heavy-job" run --class analysis --label bad-maxage --wait 5 -- echo should-not-run 2>&1); rcm=$?
ok "HEAVY_JOB_MAXAGE=abc: RESULT: ERROR usage, exit 2, CMD not run, no ticket" "$([ $rcm = 2 ] && [ "$(has '^RESULT: ERROR usage: HEAVY_JOB_MAXAGE' "$out")" = 1 ] && ! grep -q '^should-not-run' <<<"$out" && [ "$(lq)" = 0 ] && echo 1 || echo 0)"
ok "no ticket left behind (end)" "$([ "$(lq)" = 0 ] && echo 1 || echo 0)"

# 8. T-87a2: a label without the T- prefix still finds its ticket: bare id at the start (optional leading t), else the checkout's branch/dir; else P2 + a WARN line
pj() { ( cd "$1" && "$here/tools/heavy-job" run --class analysis --label "$2" --wait 20 -- true 2>&1 ); }   # PJ DIR LABEL: output of one uncontended run from DIR
mkdir -p "$w/repo" "$w/plain" "$w/T-9999"; git -C "$w/repo" init -q && git -C "$w/repo" symbolic-ref HEAD refs/heads/tools/T-cccc
o1=$(pj "$w/plain" bbbb-g2c); o2=$(pj "$w/plain" tdddd-g2); o3=$(pj "$w/plain" gate-T-cccc); o4=$(pj "$w/repo" ccccj); o5=$(pj "$w/T-9999" k9j); o6=$(pj "$w/plain" repo-health2)
o7=$(pj "$w/repo" ffff-x); o8=$(pj "$w/repo" cccc-x)
ok "R1 bare label id 'bbbb-g2c' (plain dir) -> its INDEX priority P1 and says where it came from" "$(has '^heavy-job: queue: prio=P1 (T-bbbb from bare label id' "$o1")"
ok "R1 leading t: 'tdddd-g2' -> P3" "$(has '^heavy-job: queue: prio=P3 (T-dddd from bare label id' "$o2")"
ok "R0 explicit 'gate-T-cccc' -> P0 (unchanged)" "$(has '^heavy-job: queue: prio=P0 (T-cccc in ' "$o3")"
ok "R2 label 'ccccj' from a checkout on branch tools/T-cccc -> P0 via the branch" "$(has "^heavy-job: queue: prio=P0 (T-cccc from checkout 'tools/T-cccc'" "$o4")"
ok "R2 label 'k9j' from a dir named T-9999 (no git) -> P4 via the dir" "$(has "^heavy-job: queue: prio=P4 (T-9999 from checkout 'T-9999'" "$o5")"
ok "R3 'repo-health2' from a plain dir stays P2 and prints the WARN line" "$(echo "$(has '^heavy-job: queue: prio=P2' "$o6")$(has '^heavy-job: WARN prio=P2: label repo-health2 names no ticket$' "$o6")" | grep -q 11 && echo 1 || echo 0)"
ok "R3 the WARN line is also in run.log" "$(grep -q '^heavy-job: WARN prio=P2: label repo-health2 names no ticket$' "$w/plain/logs/heavy-job/repo-health2/run.log" 2>/dev/null && echo 1 || echo 0)"
ok "R1 precedence: a bare id missing from the INDEX ('ffff-x') falls back to the checkout branch: P0" "$(has "^heavy-job: queue: prio=P0 (T-cccc from checkout" "$o7")"
ok "R1 precedence: bare id in the INDEX ('cccc-x') beats the checkout (here the same ticket) and is named as the bare id" "$(has 'prio=P0 (T-cccc from bare label id' "$o8")"
# status shows the WARN for a waiting/holding unticketed job
: >"$w/lorder"; holdl 3; cd "$w/plain" && startp X repo-health3; cd "$w/cwd" || exit 2; lqwait 2; hj status; st8=$out; waitl lh X
ok "R3 status: the unticketed waiter is marked 'prio=P2 WARN: label names no ticket'" "$(has 'waiter #1: .*label=repo-health3 .*prio=P2 WARN: label names no ticket' "$st8")"
ok "no ticket left behind (case 8)" "$([ "$(lq)" = 0 ] && echo 1 || echo 0)"

# 9. T-f4a2 (d): the scheduled nightly job (label nightly-YYYY-MM-DD, no ticket) is P3 with a `prio:` note line and no WARN; an unticketed P2 job goes before it
: >"$w/lorder"; holdl 4; startp N nightly-2026-11-03; lqwait 2; startp U plainlabel; lqwait 3; hj status; st9=$out
waitl lh N U
ok "(d) nightly-2026-11-03: P3 after an unticketed P2 job (order U N)" "$([ "$(order)" = UN ] && echo 1 || echo 0)"
ok "(d) status: the nightly waiter shows prio=P3 and no WARN" "$(has 'label=nightly-2026-11-03 .*prio=P3$' "$st9")"
ok "(d) the nightly ticket line says why: 'queue: prio: label nightly-2026-11-03 is a scheduled nightly job (no ticket); P3'" "$(has '^heavy-job: queue: prio: label nightly-2026-11-03 is a scheduled nightly job (no ticket); P3' "$(cat "$w/N.out")")"
ok "(d) no WARN line for the nightly label" "$([ "$(has 'WARN' "$(cat "$w/N.out")")" = 0 ] && echo 1 || echo 0)"
ok "no ticket left behind (case 9)" "$([ "$(lq)" = 0 ] && echo 1 || echo 0)"

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
