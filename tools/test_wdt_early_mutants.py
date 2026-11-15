"""Mutant table of patch 0333 (tools/test_0333_wdt_early.py runs it; tools/lint-mutant-needles checks the needles).

One table, one block (`# mutant-block: wdt-early`). Row (name, kind, before, after, host case that must FAIL[, extra reset edit]):
  kind "patch": before/after are patch text (patch lines carry a leading '+', so '\\n' in them becomes '\\n+'); the needle occurs exactly once in the 0333 patch text.
  kind "reset": before/after edit the extracted patched sweet_machine_reset_x (bsd/dev/sweet_restart.c) source; the optional 6th field is a second (before, after) edit applied to it too.
Both kinds are compiled into tools/wdt_host_test.c by test_0333_wdt_early.py and the named host case must report FAIL."""

# mutant-block: wdt-early
WDT_EARLY_MUTANTS = [
    ("reset path never stops the pets", "reset", "\tsweet_wdt_stop_pets();\n", "", "reset_stops_pets"),
    ("stop after the bootloader-mode write", "reset", "\tsweet_wdt_stop_pets();\n\tif (to_bootloader) {", "\tif (to_bootloader) {", "reset_stops_pets", ("\tif (!test_skip_swreset) {", "\tsweet_wdt_stop_pets();\n\tif (!test_skip_swreset) {")),
    ("stop after the SWRESET SMC", "reset", "\tsweet_wdt_stop_pets();\n", "", "reset_stops_pets", ("\tfor (;;) {", "\tsweet_wdt_stop_pets();\n\tfor (;;) {")),
    ("print before the stop store", "reset", "\tsweet_wdt_stop_pets();\n", "\tfk_printf(\"reset\\n\");\n\tsweet_wdt_stop_pets();\n", "reset_stops_pets"),
    ("park hook still sends the SWRESET SMC", "reset", "if (!test_skip_swreset) {", "if (1 || test_skip_swreset) {", "parkfail_hook"),
    ("real reset skips the SWRESET SMC", "reset", "if (!test_skip_swreset) {", "if (0 && test_skip_swreset) {", "reset_stops_pets"),
    ("spin re-enables the pets", "reset", "\tfor (;;) {", "\twdt_panicked = 0;\n\tfor (;;) {", "reset_stops_pets"),
    ("failed SMC not reported", "reset", "fk_printf(\"SWEET RESTART GAP: PMU SMC software reset returned", "if (0) fk_printf(\"SWEET RESTART GAP: PMU SMC software reset returned", "reset_stops_pets"),
    ("panic flag ignored by the poll", "patch", "\tif (ALOAD(&wdt_panicked)) {\n\t\treturn;", "\tif (0 && ALOAD(&wdt_panicked)) {\n\t\treturn;", "panic_stops_pets"),
    ("timer may not arm", "patch", "wdt_poll(1, WDT_SRC_TIMER);", "wdt_poll(0, WDT_SRC_TIMER);", "timer_arms_no_usb"),
    ("timer never re-arms itself", "patch", "\twdt_timer_in(next);\n}", "\t(void)next;\n}", "timer_arms_no_usb"),
    ("timer never started by setup", "patch", "\twdt_timer_start();   ", "\tif (0) wdt_timer_start();   ", "timer_arms_no_usb"),
    ("interval not capped by period/3", "patch", "if (wdt_threshold_ms / 2u < t) {", "if (0) {", "timer_cadence_all_periods"),
    ("timer survives the panic note", "patch", "ALOAD(&wdt_mode) != WDT_READY || ALOAD(&wdt_panicked)) {", "ALOAD(&wdt_mode) != WDT_READY) {", "panic_stops_pets"),
    ("test panic not gated on armed", "patch", "if (!ALOAD(&wdt_armed)) {\n\t\t\twdt_log(\"WDT GAP: sweetwdtabort", "if (0) {\n\t\t\twdt_log(\"WDT GAP: sweetwdtabort", "test_panic"),
    ("silent timer not reported", "patch", "ACAS(&wdt_timer_gap_said, 0, 1)", "0", "timer_silent_gap"),
    ("extra WTCNT write in the timer tick", "patch", "(void)ACAS(&wdt_timer_fired, 0, 1);", "(void)ACAS(&wdt_timer_fired, 0, 1); wdt_wr(WDT_WTCNT, wdt_count);", "timer_same_writes"),
    ("panic text always says armed", "patch", "\tif (!ALOAD(&wdt_armed)) {\n\t\treturn \"SWEET WDT GAP: NOT armed", "\tif (0) {\n\t\treturn \"SWEET WDT GAP: NOT armed", "panic_text"),
    ("stop_pets does nothing", "patch", "void\nsweet_wdt_stop_pets(void)\n{\n\tASTORE(&wdt_panicked, 1);", "void\nsweet_wdt_stop_pets(void)\n{\n\t(void)0;", "reset_stops_pets"),
    ("panic note no longer stops", "patch", "sweet_wdt_panic_note(void)\n{\n\tsweet_wdt_stop_pets();", "sweet_wdt_panic_note(void)\n{\n\t(void)0;", "panic_stops_pets"),
    ("park test not gated on armed", "patch", "if (!ALOAD(&wdt_armed)) {\n\t\t\twdt_log(\"WDT GAP: sweetwdtparkfail", "if (0) {\n\t\t\twdt_log(\"WDT GAP: sweetwdtparkfail", "parkfail_hook"),
    ("park hook does not stop before its line", "patch", "\t\t\tsweet_wdt_stop_pets();    ", "\t\t\t(void)0;    ", "parkfail_hook"),
    ("park hook silent", "patch", "wdt_log(\"SWEET WDT TEST: park SMC skipped, pets stopped,", "if (0) wdt_log(\"SWEET WDT TEST: park SMC skipped, pets stopped,", "parkfail_hook"),
    ("park range not enforced", "patch", "} else if (tp > WDT_TEST_PANIC_MAX) {\n\t\t\twdt_log(\"WDT GAP: sweetwdtparkfail", "} else if (0) {\n\t\t\twdt_log(\"WDT GAP: sweetwdtparkfail", "parkfail_hook"),
    ("park hook never called", "patch", "\t\t\twdt_test_park_fail_now();   ", "\t\t\t(void)0;   ", "parkfail_hook"),
    ("both hooks silently", "patch", "} else if (wdt_test_panic_at_ms != 0) {\n\t\t\twdt_log(\"WDT GAP: sweetwdtparkfail", "} else if (0) {\n\t\t\twdt_log(\"WDT GAP: sweetwdtparkfail", "parkfail_hook"),
    ("failed timer start silent", "patch", "wdt_log(\"WDT GAP: timer pet source not started (thread_call_allocate failed)", "(void)(\"WDT GAP: timer pet source not started (thread_call_allocate failed)", "timer_alloc_fail"),
]


import unittest  # noqa: E402  (the table above stays the first statement the manifest/needles tools read)


class TableShape(unittest.TestCase):
    """The table is data for tools/test_0333_wdt_early.py (which runs and kills every row) and tools/lint-mutant-needles; this checks its shape so a
    malformed row fails here, at the source, instead of being skipped silently by a consumer."""

    def test_rows_are_well_formed_and_named_once(self):
        names = set()
        for row in WDT_EARLY_MUTANTS:
            self.assertIn(len(row), (5, 6), row[:2])
            name, kind, old, new, case = row[:5]
            self.assertIn(kind, ("patch", "reset"), name)
            self.assertTrue(isinstance(old, str) and old, name)
            self.assertNotEqual(old, new, name)
            self.assertTrue(isinstance(case, str) and case, name)
            self.assertNotIn(name, names)
            names.add(name)
        self.assertEqual(len(names), 27)


if __name__ == "__main__":
    unittest.main()
