#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, launch, lockfree, ...) are set by the sourced test_heavy_job_lib.sh and its helpers
# Part 'offload-host' of the tools/heavy-job host test (split from test_heavy_job.sh, PLAN-GATE-2X K4a); setup + helpers: test_heavy_job_lib.sh.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2

# ---- offload-host queue (TP-042 amendment): fake ssh answers from files, FIFO with concurrent waiters, no network
fs=$w/fs; mkdir -p "$fs"
cat >"$w/fssh" <<'F'
#!/bin/bash
cat >/dev/null
echo x >>"$FS/count"
[ -e "$FS/hang1" ] && { rm -f "$FS/hang1"; exec sleep 30; }   # a stalled ssh, first call only (exec: the timeout kill reaches the sleep itself)
[ -e "$FS/hang" ] && exec sleep 30
[ ! -e "$FS/down" ] || { echo "ssh: connect to host: Connection refused" >&2; exit 255; }
cat "$FS/answer"
F
chmod +x "$w/fssh"
export FS=$fs REMOTE_JOB_SSH=$w/fssh REMOTE_JOB_HOST=fake.invalid OFFLOAD_QUEUE_DIR=$w/q OFFLOAD_REPROBE=1
ans 0.5 33554432
hj offload-host run --owner alice --label s-ok --wait 20 -- bash -c 'echo held=$OFFLOAD_QUEUE_HELD'
ok "offload-host admitted: CMD runs with OFFLOAD_QUEUE_HELD=label, RESULT PASS, log written" "$([ $rc = 0 ] && echo "$(has 'held=s-ok' "$out")$(has 'offload-host admitted' "$out")$(has '^RESULT: PASS offload-queue label=s-ok owner=alice rc=0' "$out")$([ -s logs/heavy-job/offload-host/s-ok.log ] && echo 1 || echo 0)" | grep -q 1111 && echo 1 || echo 0)"
ok "ticket released after the run" "$([ -z "$(ls "$w"/q/[0-9]*.* 2>/dev/null)" ] && echo 1 || echo 0)"
hj offload-host run --owner alice --label s-f --wait 20 -- bash -c 'exit 5'
ok "offload-host run keeps CMD rc: 5 -> FAIL exit 1" "$([ $rc = 1 ] && [ "$(has '^RESULT: FAIL offload-queue label=s-f' "$out")" = 1 ] && echo 1 || echo 0)"
REASON='load1 9.5 >= 8' refuse load 9.5 33554432
REASON='MemAvailable 8.0G <= 12G' refuse mem 0.5 8388608
REASON='rj- container running: rj-gate-pre.1' refuse container 0.5 33554432 rj-gate-pre.1,
REASON='foreign heavy process: 4242 node vitest' refuse foreign 0.5 33554432 '' '4242 node vitest;'
# T-ee35: host switched off by order (queue offline list): the probe refuses with a loud OFF line naming the reason; switched on again = admitted; label of another host = ignored
ans 0.5 33554432; printf 'offload-host\tuser order: shut down\n' >"$w/off.list"
hj offload-host run --owner alice --label s-off --wait 2 -- echo should-not-run
ok "T-ee35: host listed offline: nothing new routed (loud 'OFFLOAD OFF ... user order: shut down'), CMD not run, exit 2" "$([ $rc = 2 ] && echo "$(has 'OFFLOAD OFF: use=all switched off by order' "$out")$(has 'user order: shut down' "$out")$(has '^should-not-run' "$out")" | grep -q 110 && echo 1 || echo 0)"
printf '# comment\nother\tnot ours\n' >"$w/off.list"
hj offload-host run --owner alice --label s-on --wait 20 -- echo ran-on
ok "T-ee35: offline list without this host's row: admitted (switch back on)" "$([ $rc = 0 ] && has '^ran-on' "$out" || echo 0)"
rm -f "$w/off.list"
# boundaries (r2 M6/M7): the rule is strict, load < 8 and MemAvailable > 12 GB
REASON='load1 8 >= 8' refuse loadeq 8 33554432
REASON='MemAvailable 12.0G <= 12G' refuse memeq 0.5 12582912
ans 7.9 12713984; hj offload-host run --owner alice --label s-just --wait 20 -- echo just-ok
ok "just inside both limits (load 7.9, 12.1 GB) is admitted" "$([ $rc = 0 ] && [ "$(has '^just-ok' "$out")" = 1 ] && echo 1 || echo 0)"
# stale-ticket sweep in stake (r2 M2): a ticket nobody locks that sorts AFTER ours is only removed by the sweep
ans 9.9 33554432; echo 0 >"$w/q/.seq"; : >"$w/q/000009.dead"
hj offload-host run --owner alice --label s-sweep --wait 2 -- echo should-not-run
ok "stake sweeps a stale ticket (000009.dead removed; the refused run itself exits 2)" "$([ ! -e "$w/q/000009.dead" ] && [ $rc = 2 ] && echo 1 || echo 0)"
# r2 F3: no answer about rj- containers is a probe error, never admission
ans 0.5 33554432; printf 'load=0.5\nmemkb=33554432\nforeign=\n' >"$fs/answer"
hj offload-host run --owner alice --label s-norj --wait 20 -- echo should-not-run
ok "probe answer without rj= is a probe error (RESULT: ERROR, loud reason), CMD not run" "$([ $rc = 2 ] && [ "$(has 'no rj= answer' "$out")" = 1 ] && ! grep -q '^should-not-run' <<<"$out" && echo 1 || echo 0)"
printf 'load=0.5\nmemkb=33554432\nrjerr=docker ps failed: boom\nforeign=\n' >"$fs/answer"
hj offload-host run --owner alice --label s-rjerr --wait 20 -- echo should-not-run
ok "probe rjerr= (docker ps failed) is a probe error, CMD not run" "$([ $rc = 2 ] && [ "$(has 'docker check failed on offload-host: docker ps failed: boom' "$out")" = 1 ] && ! grep -q '^should-not-run' <<<"$out" && echo 1 || echo 0)"
# r2 F4: a stalled ssh times out (loud), the queue loop retries, nothing is admitted on the error
ans 0.5 33554432; : >"$fs/hang1"
OFFLOAD_PROBE_TIMEOUT=1 hj offload-host run --owner alice --label s-hang1 --wait 20 -- echo after-retry
ok "stalled probe: loud 'timed out' line, retried, then admitted and CMD runs" "$([ $rc = 0 ] && echo "$(has 'probe error: ssh probe timed out after 1s' "$out")$(has 'offload-host admitted' "$out")$(has '^after-retry' "$out")" | grep -q 111 && echo 1 || echo 0)"
: >"$fs/hang"
OFFLOAD_PROBE_TIMEOUT=1 hj offload-host run --owner alice --label s-hang --wait 3 -- echo should-not-run
ok "ssh stalled for good: never admitted, RESULT: ERROR after --wait, CMD not run" "$([ $rc = 2 ] && [ "$(has 'probe error: ssh probe timed out' "$out")" = 1 ] && [ "$(has '^RESULT: ERROR' "$out")" = 1 ] && ! grep -q '^should-not-run' <<<"$out" && echo 1 || echo 0)"
rm -f "$fs/hang"; ans 0.5 33554432
# r1 F1 (replaces r2 F6 "empty .seq is a loud error", which stopped the whole lane): an empty .seq is repaired LOUDLY and the run goes on
printf '' >"$w/q/.seq"
hj offload-host run --owner alice --label s-seq --wait 20 -- echo seq-repaired-ran
ok ".seq empty: loud 'offload-host queue: .seq repaired to 1 (empty)', run proceeds rc 0, CMD ran, no ticket left" "$([ $rc = 0 ] && [ "$(has 'offload-host queue: \.seq repaired to 1 (empty)' "$out")" = 1 ] && [ "$(has '^seq-repaired-ran$' "$out")" = 1 ] && [ -z "$(ls "$w"/q/[0-9]*.* 2>/dev/null)" ] && echo 1 || echo 0)"
echo 5 >"$w/q/.seq"
ans 0.5 33554432; : >"$fs/down"
hj offload-host run --owner alice --label s-down --wait 20 -- echo should-not-run
ok "ssh down: RESULT: ERROR offload-host unreachable, exit 2, CMD not run" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR offload-host unreachable' "$out")" = 1 ] && ! grep -q '^should-not-run' <<<"$out" && echo 1 || echo 0)"
hj offload-host status
ok "offload-host status with ssh down ends in RESULT: ERROR offload-host unreachable" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR offload-host unreachable' "$out")" = 1 ] && echo 1 || echo 0)"
rm -f "$fs/down"
hj offload-host status
ok "offload-host status (idle): queue empty, probe verdict, RESULT: PASS" "$([ $rc = 0 ] && echo "$(has 'queue empty' "$out")$(has 'would admit' "$out")$(has '^RESULT: PASS offload-status' "$out")" | grep -q 111 && echo 1 || echo 0)"
hj offload-host wait --wait 5
ok "offload-host wait on an empty queue returns PASS" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS offload-queue empty' "$out")" = 1 ] && echo 1 || echo 0)"
# nested: no second ticket
rm -f "$w/q/.seq"
out=$(OFFLOAD_QUEUE_HELD=outer "$here/tools/heavy-job" offload-host run --owner alice --label s-nest --wait 5 -- echo nested-ran 2>&1); rc=$?
ok "nested offload-host run: one line, no second ticket, CMD runs" "$([ $rc = 0 ] && echo "$(has 'nested in outer, no second ticket' "$out")$(has nested-ran "$out")" | grep -q 11 && [ ! -e "$w/q/.seq" ] && echo 1 || echo 0)"
# FIFO: refuse admission, start A B C in order, then admit; CMDs record the order
: >"$w/order"; ans 9.9 33554432
startw A; qwait 1; startw B; qwait 2; startw C; qwait 3
hj offload-host status
ok "offload-host status lists the head in admission and the waiters in order" "$(grep -n . <<<"$out" | grep -q '' && echo "$(has 'head (admission): owner=oA label=qA' "$out")$(has 'waiter #1:.*owner=oB label=qB' "$out")$(has 'waiter #2:.*owner=oC label=qC' "$out")$(has 'would refuse: load1 9.9' "$out")" | grep -q 1111 && echo 1 || echo 0)"
ans 0.5 33554432
for x in A B C; do wait "$(cat "$w/q$x.pid")" 2>/dev/null; done
ok "FIFO: three concurrent waiters ran strictly in arrival order A B C" "$([ "$(tr -d '\n' <"$w/order")" = ABC ] && echo 1 || echo 0)"
# a killed middle waiter is skipped
: >"$w/order"; ans 9.9 33554432
startw A; qwait 1; startw B; qwait 2; startw C; qwait 3
kill -9 "$(cat "$w/qB.pid")"; sleep 0.3
ans 0.5 33554432
for x in A C; do wait "$(cat "$w/q$x.pid")" 2>/dev/null; done
ok "killed middle waiter (kill -9) is skipped: A then C ran, B never" "$([ "$(tr -d '\n' <"$w/order")" = AC ] && echo 1 || echo 0)"
ok "no ticket is left behind" "$([ -z "$(ls "$w"/q/[0-9]*.* 2>/dev/null)" ] && echo 1 || echo 0)"
# status while a job runs + wait + --wait expiry
ans 0.5 33554432
( "$here/tools/heavy-job" offload-host run --owner bob --label long --wait 60 -- bash -c 'echo 1 >'"$w"'/long.start; sleep 5' >/dev/null 2>&1 ) & echo $! >"$w/long.pid"
for _ in $(seq 100); do [ -s "$w/long.start" ] && break; sleep 0.1; done
hj offload-host status
ok "offload-host status shows the holder owner/label/pid/age" "$(has 'holder: owner=bob label=long pid=[0-9]* age=[0-9]*s' "$out")"
hj offload-host run --owner carol --label impatient --wait 2 -- echo should-not-run
ok "--wait expiry behind a running job: RESULT: ERROR offload-queue, exit 2, CMD not run" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR offload-queue' "$out")" = 1 ] && ! grep -q '^should-not-run' <<<"$out" && echo 1 || echo 0)"
hj offload-host wait --wait 30
ok "offload-host wait returns PASS when the queue drains" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS offload-queue empty' "$out")" = 1 ] && echo 1 || echo 0)"
wait "$(cat "$w/long.pid")" 2>/dev/null
# r2 F1: offload-host wait on an unopenable ticket honours --wait and is loud (chmod 000 does not stop root)
if [ "$(id -u)" = 0 ]; then echo "note: r2 F1 offload-host wait row skipped (running as root)"
else
	: >"$w/q/000007.unread"; chmod 000 "$w/q/000007.unread"
	s=$(date +%s.%N); out=$(timeout 8 "$here/tools/heavy-job" offload-host wait --wait 3 2>&1); rc=$?; el=$(elapsed "$s")
	ok "r2 F1: offload-host wait with an unopenable ticket: RESULT: ERROR offload-queue: cannot open ticket 000007.unread, exit 2 after ${el}s (not a spin: timeout 8 would give 124)" "$([ $rc = 2 ] && awk -v e="$el" 'BEGIN{exit !(e<5)}' && has '^RESULT: ERROR offload-queue: cannot open ticket 000007\.unread' "$out" | grep -q 1 && echo 1 || echo 0)"
	rm -f "$w/q/000007.unread"
fi
o3=$(HEAVY_JOB_QUIET=1 "$here/tools/heavy-job" offload-host run --owner a --label qq --wait 5 -- echo c 2>&1)
ok "HEAVY_JOB_QUIET offload-host run: no line starts with RESULT:, heavy-job: RESULT present" "$([ "$(grep -c '^RESULT:' <<<"$o3")" = 0 ] && [ "$(grep -c '^heavy-job: RESULT ' <<<"$o3")" = 1 ] && echo 1 || echo 0)"
# offload abort: TERM to heavy-job during an offload-host CMD reaches CMD's session (own session, tracked in cg), the RESULT line says (CMD killed), the fake remote-job's on_signal cleanup ran
ans 0.5 33554432; rm -f "$w"/ab.*
cat >"$w/fakerj" <<'F'
#!/bin/bash
trap 'echo cleaned >"$1.cleanup"; exit 143' TERM
echo "$$ $(ps -o sid= -p $$ | tr -d ' ')" >"$1.start"
sleep 60 & wait $!
F
chmod +x "$w/fakerj"
"$here/tools/heavy-job" offload-host run --owner alice --label s-abort --wait 20 -- "$w/fakerj" "$w/ab" >"$w/ab.out" 2>&1 & ahp=$!
for _ in $(seq 100); do [ -s "$w/ab.start" ] && break; sleep 0.1; done
read -r acp asid <"$w/ab.start"
kill -TERM "$ahp"; for _ in $(seq 150); do kill -0 "$ahp" 2>/dev/null || break; sleep 0.1; done   # bounded: without the fix heavy-job is stuck behind CMD
if kill -0 "$ahp" 2>/dev/null; then kill -KILL "$ahp" "$acp" 2>/dev/null; arc=hung; else { wait "$ahp"; } 2>/dev/null; arc=$?; fi
for _ in $(seq 50); do [ -s "$w/ab.cleanup" ] && break; sleep 0.1; done
ok "offload abort: SIGTERM to heavy-job during CMD: exit 143 (got $arc), CMD in its own session (sid $asid, not ours), its TERM cleanup ran, RESULT '... aborted by SIGTERM (CMD killed)', CMD gone, no ticket" "$([ "$arc" = 143 ] && [ "$asid" != "$(ps -o sid= -p $$ | tr -d " ")" ] && [ -s "$w/ab.cleanup" ] && ! kill -0 "$acp" 2>/dev/null && [ -z "$(ls "$w"/q/[0-9]*.* 2>/dev/null)" ] && has '^RESULT: FAIL offload-queue label=s-abort aborted by SIGTERM (CMD killed)' "$(cat "$w/ab.out")" | grep -q 1 && echo 1 || echo 0)"
unset REMOTE_JOB_SSH OFFLOAD_QUEUE_DIR OFFLOAD_REPROBE FS


if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
