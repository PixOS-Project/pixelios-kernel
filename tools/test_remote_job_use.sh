#!/bin/bash
# shellcheck disable=SC2154  # sourced by tools/test_remote_job.sh (never run alone): uses its repo, rj, ok, has, w, out, rc, FAKE_LOG
# Cases of tools/remote-job for the per-use offload switches (`<label>:<use>` rows of the offline list) and the `test-run` job (heavy-job --light routing, tools/heavy-job-offload.sh). Moved out of
# test_remote_job.sh to keep that file under the 800-line size lint (R29).
# per-use switches (web board): a `<label>:<use>` row switches ONE use off, only for the jobs of that use; the master row (above) switches all
printf 'offload-host:test\tnoisy tests\n' >"$w/off.list"; rj selftest
ok "use=test row: selftest (use test) is refused the host, loud 'OFFLOAD OFF: use=test' + reason, ran local" "$([ $rc = 0 ] && [ "$(has '^REMOTE: offload-host unavailable (OFFLOAD OFF: use=test .*noisy tests' "$out")$(has 'where=local' "$out")" = 11 ] && echo 1 || echo 0)"
printf 'offload-host:gate\tgate lane busy\n' >"$w/off.list"; rj selftest
ok "use=gate row: selftest (use test) still runs on offload-host" "$([ $rc = 0 ] && [ "$(has 'where=offload-host' "$out")$(has 'OFFLOAD OFF' "$out")" = 10 ] && echo 1 || echo 0)"
rj unit
ok "use=gate row: unit (use gate, offload-only) is refused, exit 2, 'OFFLOAD OFF: use=gate', never run local" "$([ $rc = 2 ] && [ "$(has 'OFFLOAD OFF: use=gate .*gate lane busy' "$out")$(has 'where=local' "$out")" = 10 ] && echo 1 || echo 0)"
printf 'offload-host:test\tx\n' >"$w/off.list"; rj unit
ok "use=test row: unit (use gate) is not affected" "$([ $rc = 0 ] && has 'where=offload-host' "$out" || echo 0)"
printf 'offload-host:bogus\tx\n' >"$w/off.list"; rj selftest
ok "an unknown use row switches nothing off (selftest still on offload-host)" "$([ $rc = 0 ] && has 'where=offload-host' "$out" || echo 0)"
rm -f "$w/off.list"
# test-run (heavy-job --light routing): CMD runs in the (fake) container on the committed HEAD; job.log = CMD's output, job.rc = CMD's rc; offload-only; phone words refused
export REMOTE_JOB_GATE_PREFIX=env
rj --where offload-host test-run -- echo ran-in-test-run
ok "test-run: PASS on offload-host, CMD output in job.log, job.rc 0" "$([ $rc = 0 ] && [ "$(has 'where=offload-host' "$out")$(has 'ran-in-test-run' "$(cat "$repo/logs/remote-jobs/test-run/job.log")")$(cat "$repo/logs/remote-jobs/test-run/job.rc")" = 110 ] && echo 1 || echo 0)"
rj --where offload-host test-run -- bash -c 'exit 7'
ok "test-run: CMD rc 7 -> remote-job exit 1 (FAIL), job.rc 7, no fallback" "$([ $rc = 1 ] && [ "$(cat "$repo/logs/remote-jobs/test-run/job.rc")$(has 'where=local' "$out")" = 70 ] && echo 1 || echo 0)"
rj --where offload-host test-run -- adb devices
ok "test-run: a phone word in CMD is refused (exit 2), no ssh" "$([ $rc = 2 ] && [ "$(has 'phone word' "$out")" = 1 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
rj --scan-only test-run -- echo hi; r1=$rc; rj --scan-only test-run -- fastboot x; r2=$rc
ok "--scan-only: exit 0 without a phone word, 2 with one, never ssh" "$([ $r1 = 0 ] && [ $r2 = 2 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
rj --where offload-host test-run
ok "test-run without '-- CMD' is a usage error (exit 2)" "$([ $rc = 2 ] && has 'usage' "$out" || echo 0)"
rj selftest -- echo x
ok "'-- CMD' for another job is a usage error (exit 2)" "$([ $rc = 2 ] && has 'only for the test-run job' "$out" || echo 0)"
rj --where local test-run -- echo x
ok "test-run is offload-only: --where local refused (exit 2)" "$([ $rc = 2 ] && has 'offload-only' "$out" || echo 0)"
printf 'offload-host:test\tnoisy tests\n' >"$w/off.list"; rj --where offload-host test-run -- echo x
ok "test-run with the use=test row: exit 2, OFFLOAD OFF: use=test, CMD never ran" "$([ $rc = 2 ] && [ "$(has 'OFFLOAD OFF: use=test' "$out")" = 1 ] && echo 1 || echo 0)"
rm -f "$w/off.list"; unset REMOTE_JOB_GATE_PREFIX
