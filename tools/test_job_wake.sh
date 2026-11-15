#!/bin/bash
# Host test of the owner wake of tools/heavy-job detach and tools/remote-job (R4, T-afd9): a stub webhook server on 127.0.0.1, the real tools/wake and tools/job-wake,
# a fake systemd-run (same shape as test_heavy_job_remote.sh), remote-job's `selftest` job on this host. Never touches the T3 server, a phone or the second build host.
# Last line: RESULT: PASS job-wake cases=N | RESULT: FAIL ...
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
cd "$(dirname "$0")/.." || exit 2
unset HEAVY_JOB_QUIET HEAVY_JOB_HELD OFFLOAD_QUEUE_HELD   # the gate runs us under HEAVY_JOB_QUIET=1: RESULT lines would carry the heavy-job: prefix
w=$(mktemp -d) || exit 2; srvpid=
cleanup() { [ -z "$srvpid" ] || kill "$srvpid" 2>/dev/null; rm -rf "$w"; }; trap cleanup EXIT
n=0; bad=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; bad=$((bad + 1)); fi; }
has() { grep -q -- "$1" <<<"$2" && echo 1 || echo 0; }

# stub server: records every POST (path + body) as one line in $w/posts, answers by the task-id part of the path (ok 202, gone 404, off 409)
cat >"$w/stub.py" <<'PY'
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        b = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
        open(sys.argv[2], "a").write("%s %s\n" % (self.path.split("/")[3], b))
        self.send_response({"ok": 202, "gone": 404, "off": 409}.get(self.path.split("/")[3].split("%3A")[-1], 500)); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(s.server_address[1]))
s.serve_forever()
PY
python3 -I "$w/stub.py" "$w/port" "$w/posts" & srvpid=$!
for _ in $(seq 50); do [ -s "$w/port" ] && break; sleep 0.1; done
[ -s "$w/port" ] || { echo "RESULT: FAIL job-wake cases=0 (stub server did not start)"; exit 1; }
port=$(cat "$w/port"); export WAKE_BASE=http://127.0.0.1:$port WAKE_SECRETS_DIR=$w/sec WAKE_LOG=$w/wake.log
mkdir -p "$w/sec"
for m in ok gone off; do echo "scheduled-task%3A$m/tok$(printf 'A%.0s' $(seq 40))" >"$w/sec/h-$m.hook"; chmod 600 "$w/sec/h-$m.hook"; done
posts() { wc -l <"$w/posts" 2>/dev/null || echo 0; }

# ---- the helper's own self-test (stub server inside)
st=$(python3 -I tools/job-wake --self-test 2>&1)
ok "job-wake --self-test: RESULT: PASS job-wake N/N" "$(has '^RESULT: PASS job-wake [0-9]*/[0-9]*$' "$st")"

# ---- heavy-job detach --wake-hook (fake systemd-run: the wrapper runs the job in place)
mkdir -p "$w/dt"
# shellcheck disable=SC2016  # printf format is single-quoted on purpose: $1 and $@ belong to the generated stub
printf '#!/bin/bash\nwhile [ "$1" != -- ]; do shift; done\nshift\nexec "$@"\n' >"$w/dt/sdrun"
# shellcheck disable=SC2016  # printf format is single-quoted on purpose: $1 and $@ belong to the generated stub
printf '#!/bin/bash\n[ "$1" = --user ] && shift\ncase $1 in is-active) echo active ;; esac\n' >"$w/dt/sctl"; chmod +x "$w/dt/sdrun" "$w/dt/sctl"
export HEAVY_JOB_SYSTEMD_RUN=$w/dt/sdrun HEAVY_JOB_SYSTEMCTL=$w/dt/sctl
endwait() { for _ in $(seq 100); do grep -q "^WAKE \(ok\|FAIL\)" "$1" 2>/dev/null && return; sleep 0.1; done; }
dwake() { out=$(tools/heavy-job detach --label "$1" --log "$2" --wake-hook "$3" -- bash -c "echo job-ran; echo \$HJ_WAKE_HOOK\$HJ_WAKE_TOOL >$w/leak; exit $4" 2>&1 </dev/null); rc=$?; }
: >"$w/posts"
dwake dw-ok "$w/d1.log" h-ok 0; endwait "$w/d1.log"
ok "detach rc=0: PASS, exactly one POST, event 'job dw-ok rc=0', 'WAKE ok' after the DETACHED-END line" "$([ $rc = 0 ] && [ "$(posts)" = 1 ] && grep -q '"event": "job dw-ok rc=0"' "$w/posts" && [ "$(grep -n '^heavy-job: DETACHED-END rc=0' "$w/d1.log" | cut -d: -f1)" -lt "$(grep -n '^WAKE ok: hook=h-ok job=dw-ok rc=0' "$w/d1.log" | cut -d: -f1)" ] && echo 1 || echo 0)"
ok "detach: the job does not see the wake settings (HJ_WAKE_* dropped before CMD)" "$([ -e "$w/leak" ] && [ "$(tr -d '\n' <"$w/leak")" = "" ] && echo 1 || echo 0)"
: >"$w/posts"
dwake dw-fail "$w/d2.log" h-ok 3; endwait "$w/d2.log"
ok "detach, job exits 3: DETACHED-END rc=3 and the wake carries rc=3, once" "$([ "$(has '^heavy-job: DETACHED-END rc=3 unit=' "$(cat "$w/d2.log")")" = 1 ] && [ "$(posts)" = 1 ] && grep -q '"event": "job dw-fail rc=3"' "$w/posts" && echo 1 || echo 0)"
: >"$w/posts"
dwake dw-404 "$w/d3.log" h-gone 0; endwait "$w/d3.log"
ok "detach, hook answers 404: 'WAKE FAIL: 404 hook=h-gone' in the log, job rc untouched (DETACHED-END rc=0, exit 0), one POST" "$([ $rc = 0 ] && [ "$(posts)" = 1 ] && grep -q '^WAKE FAIL: 404 hook=h-gone job=dw-404' "$w/d3.log" && grep -q '^heavy-job: DETACHED-END rc=0' "$w/d3.log" && echo 1 || echo 0)"
dwake dw-409 "$w/d4.log" h-off 0; endwait "$w/d4.log"
ok "detach, hook disabled: 'WAKE FAIL: 409 hook=h-off'" "$(has '^WAKE FAIL: 409 hook=h-off job=dw-409' "$(cat "$w/d4.log")")"
WAKE_BASE=http://127.0.0.1:1 dwake dw-ref "$w/d5.log" h-ok 0; endwait "$w/d5.log"
ok "detach, server refused: 'WAKE FAIL: refused hook=h-ok'" "$(has '^WAKE FAIL: refused hook=h-ok job=dw-ref' "$(cat "$w/d5.log")")"
: >"$w/posts"
dwake dw-none "$w/d6.log" nohook 0; w6=$out; endwait "$w/d6.log"
ok "detach, no hook file: WARNING at launch, 'WAKE FAIL: off hook=nohook' at the end, no POST" "$([ "$(has 'WARNING: --wake-hook nohook: no hook file' "$w6")" = 1 ] && [ "$(has '^WAKE FAIL: off hook=nohook job=dw-none' "$(cat "$w/d6.log")")" = 1 ] && [ "$(posts)" = 0 ] && echo 1 || echo 0)"
: >"$w/posts"
out=$(tools/heavy-job detach --label dw-plain --log "$w/d7.log" -- bash -c 'exit 0' 2>&1 </dev/null); rc=$?; sleep 0.5
ok "detach without --wake-hook: no POST, no WAKE line (unchanged behaviour)" "$([ $rc = 0 ] && [ "$(posts)" = 0 ] && ! grep -q WAKE "$w/d7.log" && echo 1 || echo 0)"
# T-fb2c: no --wake-hook, but `queue claim` registered thread-<id>.hook for T3CODE_THREAD_ID: that hook is the default; another thread id (no file) stays unhooked
cp "$w/sec/h-ok.hook" "$w/sec/thread-mcp-abc.hook"
: >"$w/posts"
out=$(T3CODE_THREAD_ID=mcp:abc tools/heavy-job detach --label dw-dflt --log "$w/d9.log" -- bash -c 'exit 0' 2>&1 </dev/null); rc=$?; endwait "$w/d9.log"
ok "detach without --wake-hook + registered thread hook: default hook used (one POST, WAKE ok hook=thread-mcp-abc)" "$([ $rc = 0 ] && [ "$(posts)" = 1 ] && [ "$(has '^WAKE ok: hook=thread-mcp-abc job=dw-dflt' "$(cat "$w/d9.log")")" = 1 ] && echo 1 || echo 0)"
: >"$w/posts"
out=$(T3CODE_THREAD_ID=mcp:nofile tools/heavy-job detach --label dw-nof --log "$w/d10.log" -- bash -c 'exit 0' 2>&1 </dev/null); rc=$?; sleep 0.5
ok "detach, thread without a registered hook: no POST, no WAKE line" "$([ $rc = 0 ] && [ "$(posts)" = 0 ] && ! grep -q WAKE "$w/d10.log" && echo 1 || echo 0)"
: >"$w/posts"
out=$(T3CODE_THREAD_ID=mcp:abc tools/remote-job --where local selftest 2>&1 </dev/null); rc=$?
ok "remote-job without --wake-hook + registered thread hook: default hook used (WAKE ok, one POST)" "$([ $rc = 0 ] && [ "$(posts)" = 1 ] && [ "$(has '^WAKE ok: hook=thread-mcp-abc job=selftest' "$out")" = 1 ] && echo 1 || echo 0)"
out=$(tools/heavy-job detach --label dw-bad --log "$w/d8.log" --wake-hook 'a/b' -- true 2>&1 </dev/null); rc=$?
ok "detach: bad hook label -> RESULT: ERROR usage exit 2, nothing launched" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR usage: --wake-hook' "$out")" = 1 ] && [ ! -e "$w/d8.log" ] && echo 1 || echo 0)"

# ---- remote-job --wake-hook (job `selftest`, --where local: no ssh)
: >"$w/posts"
out=$(tools/remote-job --where local --wake-hook h-ok selftest 2>&1 </dev/null); rc=$?
ok "remote-job selftest PASS: exactly one POST 'job selftest rc=0', WAKE ok after the RESULT line, exit 0" "$([ $rc = 0 ] && [ "$(posts)" = 1 ] && grep -q '"event": "job selftest rc=0"' "$w/posts" && [ "$(has '^RESULT: PASS remote-job selftest' "$out")" = 1 ] && [ "$(tail -1 <<<"$out")" = "WAKE ok: hook=h-ok job=selftest rc=0" ] && echo 1 || echo 0)"
: >"$w/posts"
out=$(tools/remote-job --where local --wake-hook h-ok selftest-fail 2>&1 </dev/null); rc=$?
ok "remote-job job FAIL: exit 1 kept, one POST 'job selftest-fail rc=1'" "$([ $rc = 1 ] && [ "$(posts)" = 1 ] && grep -q '"event": "job selftest-fail rc=1"' "$w/posts" && echo 1 || echo 0)"
: >"$w/posts"
out=$(tools/remote-job --where local --wake-hook h-gone selftest 2>&1 </dev/null); rc=$?
ok "remote-job, hook answers 404: 'WAKE FAIL: 404 hook=h-gone', the job's exit 0 unchanged" "$([ $rc = 0 ] && [ "$(has '^WAKE FAIL: 404 hook=h-gone job=selftest' "$out")" = 1 ] && [ "$(has '^RESULT: PASS remote-job selftest' "$out")" = 1 ] && echo 1 || echo 0)"
: >"$w/posts"
out=$(tools/remote-job --where local selftest 2>&1 </dev/null); rc=$?
ok "remote-job without --wake-hook: no POST, no WAKE line" "$([ $rc = 0 ] && [ "$(posts)" = 0 ] && [ "$(has WAKE "$out")" = 0 ] && echo 1 || echo 0)"
out=$(tools/remote-job --where local --wake-hook 'x y' selftest 2>&1 </dev/null); rc=$?
ok "remote-job: bad hook label -> RESULT: ERROR exit 2, nothing run" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR remote-job: --wake-hook needs a plain hook label' "$out")" = 1 ] && echo 1 || echo 0)"

echo "RESULT: $([ $bad = 0 ] && echo PASS || echo FAIL) job-wake cases=$n$([ $bad = 0 ] || echo " failed=$bad")"
[ $bad = 0 ]
