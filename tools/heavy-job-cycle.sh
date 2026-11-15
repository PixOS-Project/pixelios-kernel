# shellcheck shell=bash
# shellcheck disable=SC2154  # sq, qk, lock, hq, hj_canon, hj_blob_self, hj_canon_blob, out(), stickets(), waiter_blob() are set by the sourcing tools/heavy-job
# tools/heavy-job-cycle.sh (T-70cd): wait-cycle breaker of the LOCAL queue. SOURCED by tools/heavy-job, never run. Module missing = `status` says so, nothing else changes.
# WHY: copies of heavy-job of different blobs order the queue by different rules, so waiter A (old rule) can name B ahead while B (trunk rule) names A ahead; the lock sits free
#   and every gate stops [obs 2026-11-03: lock free 53 min, 13 waiters of 4 blobs; 3 occurrences that day].
# RULE (only the CANONICAL copy applies it; run by `status` and by every waiter poll, so at most HEAVY_JOB_REEVAL s after the floor):
#   cycle = the heavy-job lock is free, nobody is admitted/running, and its last change (lock mtime, newest admitted ticket mtime) plus the oldest waiter are both >= FLOOR s old.
#   FLOOR = HEAVY_JOB_CYCLE_FLOOR, default 120. A head that is just starting (< FLOOR) is no cycle. A cycle prints ONE `heavy-job: WAIT-CYCLE free=Ns waiters=N edges=a->b,...`
#   (a waits behind b in the canonical order), then every waiter of a blob other than the canonical one is RE-QUEUED: its detached scope is killed (SIGKILL: no end line, no wake)
#   and the SAME scope name starts again through the canonical copy with the wrapper of the old one (its cmdline, so the wake hook fires once at the real end), cwd, environment,
#   command, label (hence ticket + priority), log, and its ORIGINAL queue seq and enqueue epoch (HEAVY_JOB_SEQ / HEAVY_JOB_EPOCH, read once and dropped here): it keeps its place and
#   its age; the queue's `waiting:` marker (unit + log) stays valid. Every re-queue prints `heavy-job: CYCLE-REQUEUE unit=U old_blob=X` (also in the unit log). A waiter that cannot
#   be re-queued (no unit = a foreground caller waits on it; unreadable cmdline/environ/cwd/log; no systemd-run) is a `HEAVY GAP:` line and is left untouched.
#   T-842e: a waiter whose scope layout cannot be parsed (wrapper not found above it, or the scope's command does not start with heavy-job = an old-layout copy) would keep the head for
#   good (obs 2026-11-08: 109 min, lock free). Such a waiter is ENDED while still queued: its heavy-job process gets SIGKILL (never the scope, never a started job: the ticket is re-read and must
#   still be `waiting` with the same pid), the ticket lock goes with it, the next waiter admits, and the owner's detach wrapper sees rc=137 and wakes the owner. Prints
#   `heavy-job: CYCLE-END unit=U pid=P reason=<gap>` (also in the unit log when the wrapper's log is known). Other gaps (no unit, unreadable environ/cwd/log, no systemd-run, not a heavy-job call) stay left untouched.
# Test seam: HEAVY_JOB_PROC (default /proc), HEAVY_JOB_CYCLE_FLOOR.

hj_cyc_floor=${HEAVY_JOB_CYCLE_FLOOR:-120}; hj_seq=${HEAVY_JOB_SEQ:-}; hj_ep=${HEAVY_JOB_EPOCH:-}; unset HEAVY_JOB_SEQ HEAVY_JOB_EPOCH

# hj_seq_pick: stake() calls it after sweeping dead tickets; sets hj_seqn (the ticket number) and hj_ep (enqueue epoch) when HEAVY_JOB_SEQ names a free number
hj_seq_pick() {
	local f b
	hj_seqn=
	[ -n "$hj_seq" ] || { hj_ep=; return 0; }
	if ! [[ $hj_seq =~ ^[0-9]{1,15}$ ]]; then out "HEAVY GAP: HEAVY_JOB_SEQ='$hj_seq' is not a ticket number, using the next one"; hj_ep=; return 0; fi
	for f in $(stickets); do b=${f##*/}; if [ "$((10#${b%%.*}))" = "$((10#$hj_seq))" ]; then out "HEAVY GAP: HEAVY_JOB_SEQ=$hj_seq is taken by live ticket $b, using the next number"; hj_ep=; return 0; fi; done
	[[ $hj_ep =~ ^[0-9]{1,12}$ ]] || hj_ep=
	hj_seqn=$((10#$hj_seq)); out "heavy-job: queue: CYCLE-REQUEUE keeps ticket number $hj_seqn${hj_ep:+ and enqueue epoch $hj_ep}"
}

# hj_cycle_gap UNIT WHY: a waiter we cannot re-queue
hj_cycle_gap() { out "HEAVY GAP: CYCLE-REQUEUE $1 not re-queued: $2"; }

# hj_cycle_end TICKETFILE PID UNIT WHY [LOG]: ends a queued waiter that cannot be re-queued (T-842e). Never a started job: the ticket is re-read, it must still be `waiting` under the same pid.
hj_cycle_end() {
	local f=$1 pid=$2 un=$3 why=$4 log=${5:-} tp st
	[ -e "$f" ] || return 0
	read -r _ _ tp _ st _ <"$f" 2>/dev/null
	if [ "$st" != waiting ] || [ "$tp" != "$pid" ]; then hj_cycle_gap "unit=$un" "$why; not ended: ticket ${f##*/} is ${st:-gone} under pid ${tp:-none}"; return 0; fi
	hj_cycle_gap "unit=$un" "$why"
	out "heavy-job: CYCLE-END unit=$un pid=$pid reason=$why"
	[ -z "$log" ] || [ ! -w "$log" ] || echo "heavy-job: CYCLE-END unit=$un pid=$pid reason=$why: the queued waiter was killed so the queue moves on (rc 137 wakes the owner)" >>"$log" 2>/dev/null
	kill -KILL "$pid" 2>/dev/null
}

# hj_cycle_requeue TICKETFILE PID UNIT BLOB EPOCH: stops one stale waiter's scope and starts it again through the canonical copy under the same name, number and age
hj_cycle_requeue() {
	local f=$1 pid=$2 un=$3 bl=$4 ep=$5 px=${HEAVY_JOB_PROC:-/proc} sr=${HEAVY_JOB_SYSTEMD_RUN:-systemd-run} sc=${HEAVY_JOB_SYSTEMCTL:-systemctl}
	local -a a=() wa=() ev=() ca=()
	local w=$pid i k n=0 cwd log b nn st
	[ -e "$f" ] || { out "heavy-job: CYCLE-REQUEUE unit=$un skipped: ticket ${f##*/} is gone (the job moved on while the cycle was being broken)"; return 0; }
	[ -n "$un" ] || { hj_cycle_gap "ticket=${f##*/} pid=$pid" "no detached unit in the ticket (a foreground caller waits on it)"; return 0; }
	[ -r "$px/$pid/cmdline" ] && mapfile -d '' -t a <"$px/$pid/cmdline" 2>/dev/null && [ "${#a[@]}" -gt 0 ] || { hj_cycle_gap "unit=$un" "cannot read $px/$pid/cmdline"; return 0; }
	k=-1; for i in 0 1 2; do case ${a[$i]:-} in */heavy-job|heavy-job) k=$i; break ;; esac; done
	[ "$k" -ge 0 ] && [ "${a[$((k + 1))]:-}" = run ] || { hj_cycle_gap "unit=$un" "$px/$pid/cmdline is not a 'heavy-job run' call"; return 0; }
	while [ "$n" -lt 8 ]; do   # the scope's wrapper (bash -c SCRIPT UNIT CMD...) is the first ancestor whose $0 is the unit
		w=$(awk '/^PPid:/ { print $2 }' "$px/$w/status" 2>/dev/null); [[ $w =~ ^[0-9]+$ ]] && [ "$w" -gt 1 ] || { w=; break; }
		mapfile -d '' -t wa <"$px/$w/cmdline" 2>/dev/null || { w=; break; }
		[ "${wa[1]:-}" = -c ] && [ "${wa[3]:-}" = "$un" ] && break
		n=$((n + 1))
	done
	[ -n "$w" ] && [ "${wa[1]:-}" = -c ] && [ "${wa[3]:-}" = "$un" ] || { hj_cycle_end "$f" "$pid" "$un" "wrapper process of the scope not found above pid $pid"; return 0; }
	[ -r "$px/$w/environ" ] && mapfile -d '' -t ev <"$px/$w/environ" 2>/dev/null && [ "${#ev[@]}" -gt 0 ] || { hj_cycle_gap "unit=$un" "cannot read $px/$w/environ"; return 0; }
	cwd=$(readlink "$px/$w/cwd" 2>/dev/null) && [ -d "$cwd" ] || { hj_cycle_gap "unit=$un" "cannot read the working directory of the wrapper"; return 0; }
	log=$(readlink "$px/$w/fd/1" 2>/dev/null) && [ -f "$log" ] || { hj_cycle_gap "unit=$un" "the wrapper's log (fd 1) is not a readable file"; return 0; }
	ca=("${wa[@]:4}"); k=-1; for i in 0 1 2; do case ${ca[$i]:-} in */heavy-job|heavy-job) k=$i; break ;; esac; done
	[ "$k" -ge 0 ] || { hj_cycle_end "$f" "$pid" "$un" "the scope's command does not start with heavy-job" "$log"; return 0; }
	ca[k]=$hj_canon
	command -v "$sr" >/dev/null 2>&1 && "$sc" --user show-environment >/dev/null 2>&1 || { hj_cycle_gap "unit=$un" "$sr or '$sc --user' unavailable"; return 0; }
	read -r _ _ _ _ st _ <"$f" 2>/dev/null; [ "$st" = waiting ] || { hj_cycle_gap "unit=$un" "ticket ${f##*/} is no longer waiting (${st:-gone})"; return 0; }   # re-read: never kill a job that started meanwhile
	b=${f##*/}; nn=$((10#${b%%.*}))
	out "heavy-job: CYCLE-REQUEUE unit=$un old_blob=${bl:-unknown} ticket=$b label=${b#*.} pid=$pid"
	echo "heavy-job: CYCLE-REQUEUE unit=$un old_blob=${bl:-unknown} ticket=$b: stopped, restarted through $hj_canon with the same ticket number" >>"$log" 2>/dev/null
	"$sc" --user kill --signal=SIGKILL --kill-whom=all "$un.scope" >/dev/null 2>&1; "$sc" --user stop "$un.scope" >/dev/null 2>&1
	for i in 1 2 3 4 5 6 7 8 9 10; do ( flock -n 3 ) 3<"$f" 2>/dev/null && break; sleep 0.2; done   # the old ticket lock is gone with its process
	( cd "$cwd" && exec env -i "${ev[@]}" HEAVY_JOB_SEQ="$nn" HEAVY_JOB_EPOCH="$ep" "$sr" --user --scope --quiet --collect --unit="$un" -- "${wa[@]:0:4}" "${ca[@]}" >>"$log" 2>&1 </dev/null ) & disown
	for i in $(seq 20); do
		# back = the ticket is held again, or the new process already logged that it took the old number (a head that starts at once can finish before the first look)
		if { [ -e "$f" ] && ! ( flock -n 3 ) 3<"$f" 2>/dev/null; } || grep -q "queue: CYCLE-REQUEUE keeps ticket number $nn" "$log" 2>/dev/null; then out "heavy-job: CYCLE-REQUEUED unit=$un ticket=$b (taken again by the canonical copy)"; return 0; fi
		sleep 0.5
	done
	hj_cycle_gap "unit=$un" "the scope was stopped but ticket $b did not come back within 10 s (see $log); the queue tick relaunches the ticket"
}

# hj_cycle_check: see RULE above; local queue, canonical copy only
hj_cycle_check() {
	[ "$qk" = local ] && [ -n "$hj_canon_blob" ] && [ "$hj_blob_self" = "$hj_canon_blob" ] || return 0
	[[ $hj_cyc_floor =~ ^[0-9]+$ ]] || { out "HEAVY GAP: HEAVY_JOB_CYCLE_FLOOR='$hj_cyc_floor' is not whole seconds, wait-cycle breaker off"; return 0; }
	local now f pid ep st rest fs m nw=0 oldest=0 prev='' edges='' bl age
	local -a stale=()
	flock -u 7 2>/dev/null   # `status` took the lock itself to test it: free it for the test below
	( flock -n 9 ) 9<"$lock" 2>/dev/null || return 0   # held: a job runs
	fs=$(stat -c %Y "$lock" 2>/dev/null) || return 0; now=$(date +%s)
	for f in $(stickets); do
		( flock -n 3 ) 3<"$f" 2>/dev/null && continue   # owner dead: swept by the next taker
		pid=; ep=; st=; rest=; read -r _ _ pid ep st rest <"$f" 2>/dev/null || continue
		case $st in admitted|running) m=$(stat -c %Y "$f" 2>/dev/null) || m=$now; [ "$m" -le "$fs" ] || fs=$m; continue ;; esac
		[[ $ep =~ ^[0-9]+$ ]] || continue
		nw=$((nw + 1)); age=$((now - ep)); [ "$age" -le "$oldest" ] || oldest=$age
		[ -z "$prev" ] || edges+="${edges:+,}${f##*/}->${prev##*/}"; prev=$f
		bl=; [[ $rest =~ blob=([0-9a-f]+) ]] && bl=${BASH_REMATCH[1]}; [ -n "$bl" ] || bl=$(waiter_blob "$pid")
		[ "$bl" = "$hj_canon_blob" ] || stale+=("$f|$pid|${rest// /;}|$bl|$ep")
	done
	[ "$nw" -gt 0 ] && [ "$((now - fs))" -ge "$hj_cyc_floor" ] && [ "$oldest" -ge "$hj_cyc_floor" ] || return 0
	out "heavy-job: WAIT-CYCLE free=$((now - fs))s waiters=$nw edges=${edges:-none}"
	[ "${#stale[@]}" -gt 0 ] || { out "heavy-job: WAIT-CYCLE: no waiter of another blob than the canonical $hj_canon_blob; nothing to re-queue (read the edges)"; return 0; }
	mkdir -p "$hq" 2>/dev/null
	(   # one breaker at a time (status and several waiters poll together)
		flock -n 5 || exit 0
		for m in "${stale[@]}"; do
			IFS='|' read -r f pid rest bl ep <<<"$m"
			st=; [[ $rest =~ unit=([^;]+) ]] && st=${BASH_REMATCH[1]}
			hj_cycle_requeue "$f" "$pid" "$st" "$bl" "$ep"
		done
	) 5>>"$hq/.cycle.lock"
}
