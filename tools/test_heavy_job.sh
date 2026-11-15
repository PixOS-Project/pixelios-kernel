#!/bin/bash
# Host test of tools/heavy-job (lock, memory gate, PSI, re-entrancy, rc mapping, local FIFO, kill paths, offload-host queue) and of the tools/remote-job
# resubmit check (TP-042 req 7), with a fake ssh/scp/docker; no network, no real host, no phone.
# Since PLAN-GATE-2X K4a the cases live in the parts tools/test_heavy_job_{basic,fifo,prio,r3,r4,offload-host,remote}.sh (setup + helpers: tools/test_heavy_job_lib.sh),
# each wrapped by its own tools/test_heavy_job*.py so the unit shards run them in parallel. This script runs all parts in turn (manual use).
# Last line: RESULT: PASS cases=N (sum over the parts) | RESULT: FAIL ...
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
d=$(dirname "$0"); total=0; bad=0
for p in basic fifo prio r3 r4 offload remote stale light cycle route; do
	out=$(bash "$d/test_heavy_job_$p.sh" 2>&1); rc=$?
	c=$(sed -n 's/^RESULT: PASS cases=\([0-9]*\)$/\1/p' <<<"$out" | tail -1)
	if [ "$rc" = 0 ] && [ -n "$c" ]; then total=$((total + c)); echo "part $p: PASS cases=$c"; else bad=$((bad + 1)); echo "part $p: FAIL rc=$rc"; echo "$out" | tail -n 20; fi
done
if [ $bad = 0 ]; then echo "RESULT: PASS cases=$total"; exit 0; fi
echo "RESULT: FAIL parts=$bad"; exit 1
