#!/usr/bin/env python3
"""
Adversarial hardening tests for tools/devd.c (review iteration 2).
=================================================
Covers the four vulnerability classes from the adversarial review:
  1. RUN stall / freeze / orphan leak (timeout must bound AND kill the
     process group; a stalled client must not freeze the daemon).
  2. Self-kill via SIGNAL (daemon pid rejected).
  3. Signal allowlist (SIGQUIT/negative/huge signos rejected).
  4. Symlink escape from allowed roots (PUSH/PULL via realpath policy).

Compiles devd twice: stock, and with -DDEVD_RUN_TIMEOUT_SEC=2 so timeout
paths are exercised in seconds. Exits 0 only if every case passes.
"""

import json
import sys

import devd_hardening_lib as H
import devd_hardening_misc_lib as MISC
import devd_hardening_push_lib as PUSH
import devd_hardening_run_lib as RUN
import test_devd_lib as lib


def main() -> int:
    lib.reexec_in_netns(__file__)  # review devd-r2 N2: the phone-mode rows need 127.0.0.2 on lo; never a silent skip
    H.compile_devd()
    print("--- devd adversarial hardening suite (review iter 2 + 3) ---")
    fast = H.Daemon(H.FAST_BIN)
    try:
        H.step(RUN.test_run_timeout, fast)
        H.step(RUN.test_run_grandchild_killed, fast)
    finally:
        fast.stop()
    stock = H.Daemon(H.STOCK_BIN)
    try:
        H.step(RUN.test_stalled_client_no_freeze, stock)
        H.step(RUN.test_signal_guards, stock)
        H.step(PUSH.test_symlink_policy, stock)
        H.step(PUSH.test_push_reject_framing, stock)
        H.step(RUN.test_run_pipelined_trailing_ok, stock)
        H.step(PUSH.test_push_reject_inflight, stock)
        H.step(PUSH.test_push_rename_failure, stock)
        H.step(RUN.test_run_policy, stock)
        H.step(RUN.test_run_framing, stock)
        H.step(RUN.test_run_child_env, stock)
        H.step(RUN.test_deaf_client_group_killed, stock)
        H.step(PUSH.test_push_policy, stock)
        H.step(MISC.test_peer_check, stock)
        H.step(MISC.test_auth, stock)
    finally:
        stock.stop()
    H.step(MISC.test_key_gate)
    H.step(MISC.test_phone_mode)
    H.step(MISC.test_allowlist_matches_patch)
    H.step(MISC.test_cloexec_restart)
    H.step(MISC.test_exit_and_gap_logging)
    ok = not H.FAILED and not H.ABORTED
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'} {H.passed_count} passed, {len(H.FAILED)} failed, "
          f"{len(H.ABORTED)} aborted, {len(H.skipped)} skipped" + (f" -> {H.FAILED + H.ABORTED}" if not ok else ""))
    if H.REPORT:
        with open(H.REPORT, "w") as f:
            json.dump({"passed": H.PASSED, "failed": H.FAILED, "aborted": H.ABORTED, "skipped": H.skipped}, f)
    return 0 if ok else 1


# T-c24f: `unittest discover` (tools/gate-unit-shards) runs this script through lib.script_case: exit 0 + last `RESULT: PASS`
ScriptRun = lib.script_case(__file__, 900)


if __name__ == "__main__":
    sys.exit(main())
