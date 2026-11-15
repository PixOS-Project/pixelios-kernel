"""tools/test_ufs_irq_core.py support (patch 0354): the mutants of the core (each must be killed by the driver of ufs_irq_core_lib.py) and the equivalent ones (must stay equivalent)."""

def WROW(reg):
    """a write row for `reg` in mode ON AND a value rule that accepts it: the allowlist and the value rule are two layers, a mutant must open both"""
    return [("\t{ SUI_W, SUI_MB(SUI_MODE_ON),                       SUI_REG_IS },", "\t{ SUI_W, SUI_MB(SUI_MODE_ON),                       SUI_REG_IS },\n\t{ SUI_W, SUI_MB(SUI_MODE_ON), %s }," % reg),
            ("\t\treturn v == 0u || (v == SUI_IE_STAGE1 && st->spi_att && !st->ie_on && !st->armed);\n\t}\n\treturn 0;", "\t\treturn v == 0u || (v == SUI_IE_STAGE1 && st->spi_att && !st->ie_on && !st->armed);\n\t}\n\treturn off == %s;" % reg)]


# (name, old text, new text): each old text occurs exactly once in the core; each mutant must still compile and the driver must FAIL
MUTANTS = (
    # --- arm order and values
    ("arm: IE written before the GIC attach", "\tr = o->spi_attach_off(o->ctx, rb);\n", "\t(void)sui_wr(st, o, SUI_REG_IE, SUI_IE_STAGE1);\n\tr = o->spi_attach_off(o->ctx, rb);\n"),
    ("arm: no UTRLDBR check", "\tif (v != 0u) {\n\t\tsui_first_why(st, SUI_Y_ARM_BUSY);", "\tif (0) {\n\t\tsui_first_why(st, SUI_Y_ARM_BUSY);"),
    ("arm: busy check inverted (v == 0)", "\tif (v != 0u) {\n\t\tsui_first_why(st, SUI_Y_ARM_BUSY);", "\tif (v == 0u) {\n\t\tsui_first_why(st, SUI_Y_ARM_BUSY);"),
    ("arm: attach failure ignored", "\tif (r != 0) {\n\t\tsui_first_why(st, SUI_Y_ATTACH);", "\tif (0) {\n\t\tsui_first_why(st, SUI_Y_ATTACH);"),
    ("arm: stale IS cleared with a blanket value", "if (is != 0u && (r = sui_wr(st, o, SUI_REG_IS, is)) != 0) {\n\t\tgoto fail_bus;", "if (is != 0u && (r = sui_wr(st, o, SUI_REG_IS, 0xFFFFFFFFu)) != 0) {\n\t\tgoto fail_bus;"),
    ("arm: stale IS not cleared", "if (is != 0u && (r = sui_wr(st, o, SUI_REG_IS, is)) != 0) {\n\t\tgoto fail_bus;", "if (0) {\n\t\tgoto fail_bus;"),
    ("arm: IE value 0x5 instead of 0x1", "if ((r = sui_wr(st, o, SUI_REG_IE, SUI_IE_STAGE1)) != 0) {", "if ((r = sui_wr(st, o, SUI_REG_IE, 0x5u)) != 0) {"),
    ("arm: IE read-back mismatch ignored", "\tif (v != SUI_IE_STAGE1) {\n\t\to->note(o->ctx, SUI_G_IE_RB", "\tif (0) {\n\t\to->note(o->ctx, SUI_G_IE_RB"),
    ("arm: IE read-back not disarming", "\t\to->note(o->ctx, SUI_G_IE_RB, v, 0u);\n\t\tsui_disarm(st, o, SUI_Y_IE_RB);\n\t\treturn -1;", "\t\to->note(o->ctx, SUI_G_IE_RB, v, 0u);\n\t\treturn -1;"),
    ("arm: SPI enabled before the IE write", None, None),   # built below (swap)
    ("arm: enable failure ignored", "\tif (r != 0 || en != 1u) {", "\tif (0) {"),
    ("arm: enable read-back bit not checked", "\tif (r != 0 || en != 1u) {", "\tif (r != 0) {"),
    ("arm: armed set before the enable", "\to->note(o->ctx, SUI_N_ARMED, v, 0u);\n", "\to->note(o->ctx, SUI_N_ARMED, v, 0u);\n\tst->armed = 1;\n"),
    ("arm: armed never set", "\t__atomic_store_n(&st->armed, 1, __ATOMIC_RELEASE);\n\treturn 0;\nfail_bus:", "\treturn 0;\nfail_bus:"),
    ("arm: UTRIACR bit 31 GAP dropped", "\tif (v & 0x80000000u) {", "\tif (0) {"),
    ("arm: UTRIACR GAP on bit 30", "\tif (v & 0x80000000u) {", "\tif (v & 0x40000000u) {"),
    ("arm: UTRIACR read dropped", "\tfoff = SUI_REG_UTRIACR;\n\tif ((r = sui_rd(st, o, SUI_REG_UTRIACR, &v)) != 0) {\n\t\tgoto fail_bus;\n\t}\n", ""),
    ("arm: bus failure of the early read not noted", "\to->note(o->ctx, SUI_G_ARM_ACC, SUI_REG_UTRLDBR, r == SUI_E_REFUSED ? 1u : 0u);   /* nothing attached or written yet: not \"disarming\" */\n", ""),
    ("arm: the early failure uses the \"disarming\" note", "o->note(o->ctx, SUI_G_ARM_ACC, SUI_REG_UTRLDBR, r == SUI_E_REFUSED ? 1u : 0u);", "o->note(o->ctx, SUI_G_BUS, SUI_REG_UTRLDBR, 0u);"),
    ("arm: the early failure names offset 0", "o->note(o->ctx, SUI_G_ARM_ACC, SUI_REG_UTRLDBR,", "o->note(o->ctx, SUI_G_ARM_ACC, 0u,"),
    ("arm: the failing offset is not passed (finding 3d)", "SUI_G_BUS, foff, 0u);", "SUI_G_BUS, 0u, 0u);"),
    ("arm: the offset of the IS accesses is not tracked", "\tfoff = SUI_REG_IS;\n", ""),
    ("arm: the offset of the UTRIACR read is not tracked", "\tfoff = SUI_REG_UTRIACR;\n", ""),
    ("arm: the offset of the IE accesses is not tracked", "\tfoff = SUI_REG_IE;\n", ""),
    ("arm: fail_bus does not disarm", "\tsui_disarm(st, o, SUI_Y_BUS);\n\treturn -1;\nearly_bus:", "\treturn -1;\nearly_bus:"),
    ("arm: mode check dropped", "\tif (st->mode != SUI_MODE_ON) {\n\t\treturn -1;\n\t}\n\tr = sui_rd(st, o, SUI_REG_UTRLDBR, &v);", "\tr = sui_rd(st, o, SUI_REG_UTRLDBR, &v);"),
    ("arm: attach rc note carries 0", "o->note(o->ctx, SUI_G_ATTACH, (uint32_t)r, 0u);", "o->note(o->ctx, SUI_G_ATTACH, 0u, 0u);"),
    ("arm: spi_att not recorded", "\tst->spi_att = 1;\n\to->note(o->ctx, SUI_N_ATTACH1", "\to->note(o->ctx, SUI_N_ATTACH1"),
    # --- ISR
    ("isr: W1C of the wrong value (UTRCS only)", "sui_wr(st, o, SUI_REG_IS, is) != 0 || sui_rd(st, o, SUI_REG_IS, &again)", "sui_wr(st, o, SUI_REG_IS, is & 1u) != 0 || sui_rd(st, o, SUI_REG_IS, &again)"),
    ("isr: W1C of everything", "sui_wr(st, o, SUI_REG_IS, is) != 0 || sui_rd(st, o, SUI_REG_IS, &again)", "sui_wr(st, o, SUI_REG_IS, 0xFFFFFFFFu) != 0 || sui_rd(st, o, SUI_REG_IS, &again)"),
    ("isr: no W1C", "sui_wr(st, o, SUI_REG_IS, is) != 0 || sui_rd(st, o, SUI_REG_IS, &again)", "0 || sui_rd(st, o, SUI_REG_IS, &again)"),
    ("isr: W1C written even when IS is 0", "\tif (is != 0u) {\n\t\tuint32_t again = 0;", "\t{\n\t\tuint32_t again = 0;"),
    ("isr: no IS re-read after the W1C (finding 4)", "sui_wr(st, o, SUI_REG_IS, is) != 0 || sui_rd(st, o, SUI_REG_IS, &again) != 0", "sui_wr(st, o, SUI_REG_IS, is) != 0"),
    ("isr: re-read failure ignored", "|| sui_rd(st, o, SUI_REG_IS, &again) != 0) {", "|| (sui_rd(st, o, SUI_REG_IS, &again), 0)) {"),
    ("isr: re-read value not counted", "\t\tif (again != 0u) {\n\t\t\t__atomic_fetch_add(&st->isr_again, 1u, __ATOMIC_RELAXED);\n\t\t}\n", ""),
    ("isr: re-read counted when it reads 0", "\t\tif (again != 0u) {\n\t\t\t__atomic_fetch_add(&st->isr_again", "\t\tif (1) {\n\t\t\t__atomic_fetch_add(&st->isr_again"),
    ("isr: re-read value written back", "\t\tif (again != 0u) {\n\t\t\t__atomic_fetch_add(&st->isr_again, 1u, __ATOMIC_RELAXED);", "\t\tif (again != 0u) {\n\t\t\t(void)sui_wr(st, o, SUI_REG_IS, again);\n\t\t\t__atomic_fetch_add(&st->isr_again, 1u, __ATOMIC_RELAXED);"),
    ("isr: re-read value treated as an interrupt (is |= again)", "\t\tif (again != 0u) {\n\t\t\t__atomic_fetch_add(&st->isr_again, 1u, __ATOMIC_RELAXED);", "\t\tif (again != 0u) {\n\t\t\tis |= again;\n\t\t\t__atomic_fetch_add(&st->isr_again, 1u, __ATOMIC_RELAXED);"),
    ("isr: wake before the W1C", None, None),               # built below
    ("isr: sequence not bumped", "\t\t__atomic_fetch_add(&st->isr_seq, 1u, __ATOMIC_RELEASE);\n", ""),
    ("isr: sequence bumped by 2", "__atomic_fetch_add(&st->isr_seq, 1u, __ATOMIC_RELEASE);", "__atomic_fetch_add(&st->isr_seq, 2u, __ATOMIC_RELEASE);"),
    ("isr: no wake", "\t\to->wake(o->ctx);\n\t\treturn 0;\n\t}\n\t__atomic_fetch_add(&st->spurious", "\t\treturn 0;\n\t}\n\t__atomic_fetch_add(&st->spurious"),
    ("isr: UTRCS test ignores IE", "if (is & ie & SUI_UTRCS) {", "if (is & SUI_UTRCS) {"),
    ("isr: UTRCS test inverted", "if (is & ie & SUI_UTRCS) {", "if (!(is & ie & SUI_UTRCS)) {"),
    ("isr: irqs not counted", "\t\t__atomic_fetch_add(&st->irqs, 1u, __ATOMIC_RELAXED);\n", ""),
    ("isr: spurious not counted", "\t__atomic_fetch_add(&st->spurious, 1u, __ATOMIC_RELAXED);\n", ""),
    ("isr: isr_total not counted", "\t__atomic_fetch_add(&st->isr_total, 1u, __ATOMIC_RELAXED);\n", ""),
    ("isr: other bits not remembered (UTRCS case)", "\t\tif (is & ~SUI_UTRCS) {\n\t\t\t__atomic_fetch_or(&st->err_is, is & ~SUI_UTRCS, __ATOMIC_RELAXED);\n\t\t}\n\t\to->wake(o->ctx);", "\t\to->wake(o->ctx);"),
    ("isr: other bits not remembered (spurious case)", "\tif (is & ~SUI_UTRCS) {\n\t\t__atomic_fetch_or(&st->err_is, is & ~SUI_UTRCS, __ATOMIC_RELAXED);\n\t}\n\tst->win_n++;", "\tst->win_n++;"),
    ("isr: storm threshold off by one (>=)", "if (st->win_n > SUI_STORM_MAX) {", "if (st->win_n >= SUI_STORM_MAX) {"),
    ("isr: storm threshold off by one (+1)", "if (st->win_n > SUI_STORM_MAX) {", "if (st->win_n > SUI_STORM_MAX + 1u) {"),
    ("isr: storm never fires", "if (st->win_n > SUI_STORM_MAX) {", "if (0) {"),
    ("isr: legitimate interrupts counted in the storm window", "\t\t__atomic_fetch_add(&st->irqs, 1u, __ATOMIC_RELAXED);\n", "\t\t__atomic_fetch_add(&st->irqs, 1u, __ATOMIC_RELAXED);\n\t\tst->win_n++;\n"),
    ("isr: window never restarts", "if (now - st->win_start >= SUI_WIN_NS) {", "if (0) {"),
    ("isr: window restarts one ns late", "if (now - st->win_start >= SUI_WIN_NS) {", "if (now - st->win_start > SUI_WIN_NS) {"),
    ("isr: window counter not reset", "\t\tst->win_start = now;\n\t\tst->win_n = 0;\n", "\t\tst->win_start = now;\n"),
    ("isr: storm returns 0 (line stays unmasked)", "\t\treturn sui_isr_off(st, o, SUI_Y_STORM);\n\t}\n\treturn 0;", "\t\t(void)sui_isr_off(st, o, SUI_Y_STORM);\n\t}\n\treturn 0;"),
    ("isr: mode check dropped", "\tif (st->mode != SUI_MODE_ON) {\n\t\treturn 1;\n\t}\n\t__atomic_fetch_add(&st->isr_total", "\t__atomic_fetch_add(&st->isr_total"),
    ("isr: read error ignored", "\tif (sui_rd(st, o, SUI_REG_IS, &is) != 0 || sui_rd(st, o, SUI_REG_IE, &ie) != 0) {", "\tif (0) {"),
    ("isr: write error ignored", "sui_wr(st, o, SUI_REG_IS, is) != 0 || sui_rd(st, o, SUI_REG_IS, &again)", "(sui_wr(st, o, SUI_REG_IS, is), 0) || sui_rd(st, o, SUI_REG_IS, &again)"),
    ("isr_off: IE not written back to 0", "\tif (st->ie_on) {\n\t\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n\t}\n\tst->off_pending = 1;", "\tst->off_pending = 1;"),
    ("isr_off: armed stays set", "static int\nsui_isr_off(struct sui *st, const struct sui_ops *o, unsigned why)\n{\n\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);", "static int\nsui_isr_off(struct sui *st, const struct sui_ops *o, unsigned why)\n{"),
    ("isr_off: thread not kicked", "\tst->off_pending = 1;\n\to->kick(o->ctx);\n\treturn 1;", "\tst->off_pending = 1;\n\treturn 1;"),
    ("isr_off: reason not recorded", "\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);\n\tsui_first_why(st, why);\n\tif (st->ie_on) {\n\t\t(void)sui_wr", "\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);\n\t(void)why;\n\tif (st->ie_on) {\n\t\t(void)sui_wr"),
    ("finish: no teardown after an ISR disable", "\tif (st->off_pending) {\n\t\tsui_disarm(st, o, st->why);\n\t}", "\tif (0) {\n\t\tsui_disarm(st, o, st->why);\n\t}"),
    # --- wait
    ("wait: no re-read after assert_wait", "\t\t\tr = sui_rd(st, o, SUI_REG_UTRLDBR, &v);      /* the doorbell is re-read AFTER", "\t\t\tr = 0; v = slot_bit;      /* the doorbell is re-read AFTER"),
    ("wait: re-read result ignored (always blocks)", "\t\t\tif (!(v & slot_bit)) {\n\t\t\t\tif (waiting) {\n\t\t\t\t\to->wait_cancel(o->ctx);\n\t\t\t\t}\n\t\t\t\tbreak;\n\t\t\t}", ""),
    ("wait: wait not cancelled when the re-read finds the command done", "\t\t\t\tif (waiting) {\n\t\t\t\t\to->wait_cancel(o->ctx);\n\t\t\t\t}\n\t\t\t\tbreak;", "\t\t\t\tbreak;"),
    ("wait: cancel even when not waiting", "\t\t\t\tif (waiting) {\n\t\t\t\t\to->wait_cancel(o->ctx);\n\t\t\t\t}\n\t\t\t\tbreak;", "\t\t\t\to->wait_cancel(o->ctx);\n\t\t\t\tbreak;"),
    ("wait: blocks even when assert_wait did not wait", "\t\t\tif (waiting) {\n\t\t\t\tblocked = 1;", "\t\t\tif (1) {\n\t\t\t\tblocked = 1;"),
    ("wait: bus failure on the re-read not cancelling the assert", "\t\t\t\tif (waiting) {\n\t\t\t\t\to->wait_cancel(o->ctx);\n\t\t\t\t}\n\t\t\t\tgoto bus;", "\t\t\t\tgoto bus;"),
    ("wait: fallback is 2 ms", "uint64_t wake = now + (left < SUI_FALLBACK_NS ? left : SUI_FALLBACK_NS);", "uint64_t wake = now + (left < 2 * SUI_FALLBACK_NS ? left : 2 * SUI_FALLBACK_NS);"),
    ("wait: the wake ignores the deadline", "uint64_t wake = now + (left < SUI_FALLBACK_NS ? left : SUI_FALLBACK_NS);", "uint64_t wake = now + SUI_FALLBACK_NS;"),
    ("wait: the wake is always the deadline", "uint64_t wake = now + (left < SUI_FALLBACK_NS ? left : SUI_FALLBACK_NS);", "uint64_t wake = now + left;"),
    ("wait: no spin", "\t\tif (now < spin_until) {", "\t\tif (0) {"),
    ("wait: spin forever until the deadline", "\t\tif (now < spin_until) {", "\t\tif (1) {"),
    ("wait: spin one step short", "uint64_t spin_until = now + spin_ns;", "uint64_t spin_until = now + spin_ns - 1000u;"),
    ("wait: deadline check dropped", "\t\tif (now > deadline_ns) {\n\t\t\tst->cmds++;\n\t\t\treturn SUI_W_TIMEOUT;\n\t\t}", ""),
    ("wait: deadline is inclusive (>=)", "if (now > deadline_ns) {", "if (now >= deadline_ns) {"),
    ("wait: timeout does not count the command", "\t\tif (now > deadline_ns) {\n\t\t\tst->cmds++;\n", "\t\tif (now > deadline_ns) {\n"),
    ("wait: poll_found counted without a timeout wake", "} else if (blocked && last_tmo) {", "} else if (blocked) {"),
    ("wait: poll_found counted for fast commands", "} else if (blocked && last_tmo) {", "} else if (1) {"),
    ("wait: hit test on the sequence dropped (always poll_found / fast)", "\tif (__atomic_load_n(&st->isr_seq, __ATOMIC_ACQUIRE) != st->seq0) {\n\t\tst->hits++;\n\t} else if", "\tif (0) {\n\t\tst->hits++;\n\t} else if"),
    ("wait: hits counted without an interrupt", "\tif (__atomic_load_n(&st->isr_seq, __ATOMIC_ACQUIRE) != st->seq0) {\n\t\tst->hits++;", "\tif (1) {\n\t\tst->hits++;"),
    ("wait: tmo_wakes not counted", "\t\t\t\tif (last_tmo) {\n\t\t\t\t\tst->tmo_wakes++;\n\t\t\t\t}\n", ""),
    ("wait: last_tmo not reset each round", "\t\t\tlast_tmo = 0;\n\t\t\tif (!(v & slot_bit)) {", "\t\t\tif (!(v & slot_bit)) {"),
    ("wait: cmds not counted", "\tst->cmds++;\n\tif (__atomic_load_n(&st->isr_seq", "\tif (__atomic_load_n(&st->isr_seq"),
    ("wait: noirq threshold off by one (>)", "} else if (++st->noirq_run >= SUI_NOIRQ_MAX) {", "} else if (++st->noirq_run > SUI_NOIRQ_MAX) {"),
    ("wait: noirq threshold 199", "} else if (++st->noirq_run >= SUI_NOIRQ_MAX) {", "} else if (++st->noirq_run >= SUI_NOIRQ_MAX - 1u) {"),
    ("wait: noirq never disarms", "} else if (++st->noirq_run >= SUI_NOIRQ_MAX) {\n\t\tsui_disarm(st, o, SUI_Y_NOIRQ);\n\t}", "} else if (++st->noirq_run >= SUI_NOIRQ_MAX) {\n\t}"),
    ("wait: noirq reason wrong", "sui_disarm(st, o, SUI_Y_NOIRQ);", "sui_disarm(st, o, SUI_Y_STORM);"),
    ("wait: an interrupt does not reset the run", "\t\tst->mark = __atomic_load_n(&st->isr_seq, __ATOMIC_ACQUIRE);\n\t\tst->noirq_run = 0;\n", "\t\tst->mark = __atomic_load_n(&st->isr_seq, __ATOMIC_ACQUIRE);\n"),
    ("wait: the run mark is not updated", "\t\tst->mark = __atomic_load_n(&st->isr_seq, __ATOMIC_ACQUIRE);\n\t\tst->noirq_run = 0;\n", "\t\tst->noirq_run = 0;\n"),
    ("wait: the run is reset per command only (a late interrupt does not count)", "\tif (__atomic_load_n(&st->isr_seq, __ATOMIC_ACQUIRE) != st->mark) {", "\tif (__atomic_load_n(&st->isr_seq, __ATOMIC_ACQUIRE) != st->seq0) {"),
    ("wait: bus failure answers DONE", "\treturn SUI_W_POLL;\n}\n\n/* After the doorbell", "\treturn SUI_W_DONE;\n}\n\n/* After the doorbell"),
    ("wait: bus failure without a disarm", "\tsui_disarm(st, o, r == SUI_E_REFUSED ? SUI_Y_REFUSED : SUI_Y_BUS);\n\treturn SUI_W_POLL;", "\treturn SUI_W_POLL;"),
    ("wait: refusal recorded as a bus error", "r == SUI_E_REFUSED ? SUI_Y_REFUSED : SUI_Y_BUS);\n\treturn SUI_W_POLL;", "SUI_Y_BUS);\n\treturn SUI_W_POLL;"),
    ("wait: begin takes the sequence after the fact (never updated)", "\tst->seq0 = __atomic_load_n(&st->isr_seq, __ATOMIC_ACQUIRE);", "\tst->seq0 = 0;"),
    # --- ack
    ("ack: writes UTRCS again (the old waiter write, finding 1)", "\te = (is & ~SUI_UTRCS) |", "\tif (is & SUI_UTRCS) {\n\t\t(void)sui_wr(st, o, SUI_REG_IS, SUI_UTRCS);\n\t}\n\te = (is & ~SUI_UTRCS) |"),
    ("ack: blanket W1C of what it read", "\te = (is & ~SUI_UTRCS) |", "\tif (is) {\n\t\t(void)sui_wr(st, o, SUI_REG_IS, is);\n\t}\n\te = (is & ~SUI_UTRCS) |"),
    ("ack: W1C of 0x1 even when UTRCS is clear", "\te = (is & ~SUI_UTRCS) |", "\t(void)sui_wr(st, o, SUI_REG_IS, SUI_UTRCS);\n\te = (is & ~SUI_UTRCS) |"),
    ("ack: writes IE", "\te = (is & ~SUI_UTRCS) |", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n\te = (is & ~SUI_UTRCS) |"),
    ("ack: other bits not reported", "\tif (e != 0u) {\n\t\tst->other_n++;", "\tif (0) {\n\t\tst->other_n++;"),
    ("ack: the ISR's collected bits not reported", "e = (is & ~SUI_UTRCS) | __atomic_exchange_n(&st->err_is, 0u, __ATOMIC_RELAXED);", "e = (is & ~SUI_UTRCS);"),
    ("ack: collected bits never cleared", "e = (is & ~SUI_UTRCS) | __atomic_exchange_n(&st->err_is, 0u, __ATOMIC_RELAXED);", "e = (is & ~SUI_UTRCS) | __atomic_load_n(&st->err_is, __ATOMIC_RELAXED);"),
    ("ack: rate limit dropped", "if (st->other_n <= SUI_OTHER_LOG || (st->other_n & 1023u) == 0u) {", "if (1) {"),
    ("ack: rate limit 7", "if (st->other_n <= SUI_OTHER_LOG ||", "if (st->other_n < SUI_OTHER_LOG ||"),
    ("ack: rate limit every 1000th", "(st->other_n & 1023u) == 0u", "(st->other_n % 1000u) == 0u"),
    ("ack: read failure silent", "\t\tsui_note_access(st, o, SUI_REG_IS, r, 1u);\n\t\treturn;", "\t\treturn;"),
    ("ack: read failure disarms", "\t\tsui_note_access(st, o, SUI_REG_IS, r, 1u);\n\t\treturn;", "\t\tsui_note_access(st, o, SUI_REG_IS, r, 1u);\n\t\tsui_disarm(st, o, SUI_Y_BUS);\n\t\treturn;"),
    ("access note: not rate limited", "if (st->acc_n <= SUI_OTHER_LOG || (st->acc_n & 1023u) == 0u) {", "if (1) {"),
    ("access note: rate limit 7", "if (st->acc_n <= SUI_OTHER_LOG ||", "if (st->acc_n < SUI_OTHER_LOG ||"),
    ("access note: every 1000th", "(st->acc_n & 1023u) == 0u", "(st->acc_n % 1000u) == 0u"),
    ("access note: never printed", "\t\to->note(o->ctx, SUI_G_ACCESS, off, (place << 8) | (r == SUI_E_REFUSED ? 1u : 0u));\n", "\t\t(void)o; (void)off; (void)place; (void)r;\n"),
    ("access note: not counted", "\tst->acc_n++;\n", ""),
    ("access note: the place is lost", "(place << 8) |", "((place & 0u) << 8) |"),
    ("access note: a refusal reported as a bus failure", "(r == SUI_E_REFUSED ? 1u : 0u));\n\t}\n}", "(r == 12345 ? 1u : 0u));\n\t}\n}"),
    ("access note: the offset is lost", "o->note(o->ctx, SUI_G_ACCESS, off,", "o->note(o->ctx, SUI_G_ACCESS, off & 0u,"),
    # --- disarm
    ("disarm: IE not written back", "\tif (st->ie_on && sui_wr(st, o, SUI_REG_IE, 0u) != 0) {", "\tif (0) {"),
    ("disarm: IE written even when it was never on", "\tif (st->ie_on && sui_wr(st, o, SUI_REG_IE, 0u) != 0) {", "\tif (sui_wr(st, o, SUI_REG_IE, 0u) != 0) {"),
    ("disarm: IS not cleared", "\t\t} else if (is != 0u && sui_wr(st, o, SUI_REG_IS, is) != 0) {", "\t\t} else if (0) {"),
    ("disarm: IS cleared with a blanket value", "\t\t} else if (is != 0u && sui_wr(st, o, SUI_REG_IS, is) != 0) {", "\t\t} else if (is != 0u && sui_wr(st, o, SUI_REG_IS, 0xFFFFFFFFu) != 0) {"),
    ("disarm: no detach", "\tif (st->spi_att) {\n\t\tif (o->spi_detach(o->ctx) != 0) {", "\tif (0) {\n\t\tif (o->spi_detach(o->ctx) != 0) {"),
    ("disarm: detach failure silent", "\t\t\to->note(o->ctx, SUI_G_TEARDOWN, 3u, 0u);\n", ""),
    ("disarm: spi_att not cleared", "\t\tst->spi_att = 0;\n\t}\n\tst->off_pending = 0;", "\t}\n\tst->off_pending = 0;"),
    ("disarm: off_pending not cleared", "\tst->off_pending = 0;\n\tif (!st->off_noted) {", "\tif (!st->off_noted) {"),
    ("disarm: armed not cleared", "\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);\n\tsui_first_why(st, why);\n\tif (st->ie_on && sui_wr", "\tsui_first_why(st, why);\n\tif (st->ie_on && sui_wr"),
    ("disarm: reason not recorded", "\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);\n\tsui_first_why(st, why);\n\tif (st->ie_on && sui_wr", "\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);\n\t(void)why;\n\tif (st->ie_on && sui_wr"),
    ("disarm: the last reason wins", "\tuint8_t none = SUI_Y_NONE;\n\t__atomic_compare_exchange_n(&st->why, &none, (uint8_t)y, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED);", "\tst->why = (uint8_t)y;"),
    ("disarm: GAP every time", "\tif (!st->off_noted) {\n\t\tst->off_noted = 1;", "\tif (1) {\n\t\tst->off_noted = 1;"),
    ("disarm: no GAP line", "\tif (!st->off_noted) {\n\t\tst->off_noted = 1;", "\tif (0) {\n\t\tst->off_noted = 1;"),
    ("disarm: restart reported as a GAP", "\t\tif (st->why == SUI_Y_RESTART) {", "\t\tif (0) {"),
    ("disarm: a failure is reported as a quiesce", "\t\tif (st->why == SUI_Y_RESTART) {", "\t\tif (1) {"),
    ("disarm: IE failure silent", "\t\to->note(o->ctx, SUI_G_TEARDOWN, 1u, 0u);\n", ""),
    ("disarm: IS read failure silent", "\t\t\to->note(o->ctx, SUI_G_TEARDOWN, 2u, 0u);\n", ""),
    ("disarm: IS write failure silent", "\t\t\to->note(o->ctx, SUI_G_TEARDOWN, 2u, 1u);\n", ""),
    ("disarm: IS touched only when IE was on", "\tint touched = st->ie_on || st->spi_att;", "\tint touched = st->ie_on;"),
    ("disarm: the GAP carries the wrong irq count", "o->note(o->ctx, SUI_G_OFF, st->why, st->irqs);", "o->note(o->ctx, SUI_G_OFF, st->why, 0u);"),
    # --- judge
    ("judge: IS write allowed in mode RO", "\t{ SUI_W, SUI_MB(SUI_MODE_ON),                       SUI_REG_IS },", "\t{ SUI_W, SUI_MB(SUI_MODE_ON) | SUI_MB(SUI_MODE_RO), SUI_REG_IS },"),
    ("judge: IE write allowed in mode RO", "\t{ SUI_W, SUI_MB(SUI_MODE_ON),                       SUI_REG_IE },", "\t{ SUI_W, SUI_MB(SUI_MODE_ON) | SUI_MB(SUI_MODE_RO), SUI_REG_IE },"),
    ("judge: UTRIACR write row (and the value rule)", WROW("SUI_REG_UTRIACR"), None),
    ("judge: UTRLDBR write row (and the value rule)", WROW("SUI_REG_UTRLDBR"), None),
    ("judge: UTRLCNR write row (and the value rule)", WROW("SUI_REG_UTRLCNR"), None),
    ("judge: a read row for 0x78 (UTMRLDBR)", "\t{ SUI_R, SUI_MB(SUI_MODE_ON) | SUI_MB(SUI_MODE_RO), SUI_REG_VER },", "\t{ SUI_R, SUI_MB(SUI_MODE_ON) | SUI_MB(SUI_MODE_RO), SUI_REG_VER },\n\t{ SUI_R, SUI_MB(SUI_MODE_ON), 0x78u },"),
    ("judge: UTRLDBR read missing in RO", "\t{ SUI_R, SUI_MB(SUI_MODE_ON) | SUI_MB(SUI_MODE_RO), SUI_REG_UTRLDBR },", "\t{ SUI_R, SUI_MB(SUI_MODE_ON), SUI_REG_UTRLDBR },"),
    ("judge: UTRLCNR read allowed in mode 0", "\t{ SUI_R, SUI_MB(SUI_MODE_ON) | SUI_MB(SUI_MODE_RO), SUI_REG_UTRLCNR },", "\t{ SUI_R, SUI_MB(SUI_MODE_0) | SUI_MB(SUI_MODE_ON) | SUI_MB(SUI_MODE_RO), SUI_REG_UTRLCNR },"),
    ("judge: access type not compared", "sui_rows[i].off == off && sui_rows[i].acc == acc &&", "sui_rows[i].off == off &&"),
    ("judge: mode not compared", "&& (sui_rows[i].modes & SUI_MB(st->mode))) {", ") {"),
    ("judge: IS value rule allows a blanket value", "return v != 0u && v != 0xFFFFFFFFu && (seen & v) == v;", "return v != 0u && ((seen & v) == v || v == 0xFFFFFFFFu);"),
    ("judge: IS value rule allows 0", "return v != 0u && v != 0xFFFFFFFFu && (seen & v) == v;", "return v != 0xFFFFFFFFu && (seen & v) == v;"),
    ("judge: IS value rule allows bits never read", "return v != 0u && v != 0xFFFFFFFFu && (seen & v) == v;", "return v != 0u && v != 0xFFFFFFFFu;"),
    ("judge: IS value rule allows any overlap with the bits read", "(seen & v) == v;", "(seen & v) != 0u;"),
    ("judge: IS reads not remembered", "\tif (off == SUI_REG_IS) {\n\t\t__atomic_fetch_or(&st->is_union, *v, __ATOMIC_RELAXED);\n\t}\n", ""),
    ("judge: IS value rule allows exactly UTRCS again (the old waiter clause)", "&& (seen & v) == v;", "&& (v == SUI_UTRCS || (seen & v) == v);"),
    ("judge: IS value rule never allows a bit that was read", "&& (seen & v) == v;", "&& (seen & v) == 0xFFFFFFFFu;"),
    ("judge: IE rule allows any value", "return v == 0u || (v == SUI_IE_STAGE1 && st->spi_att && !st->ie_on && !st->armed);", "return 1;"),
    ("judge: IE rule allows 0x5 too", "return v == 0u || (v == SUI_IE_STAGE1 && st->spi_att && !st->ie_on && !st->armed);", "return v == 0u || ((v == SUI_IE_STAGE1 || v == 5u) && st->spi_att && !st->ie_on && !st->armed);"),
    ("judge: IE rule allows 1 before the attach", "return v == 0u || (v == SUI_IE_STAGE1 && st->spi_att && !st->ie_on && !st->armed);", "return v == 0u || (v == SUI_IE_STAGE1 && !st->ie_on && !st->armed);"),
    ("judge: IE rule allows 1 while armed", "return v == 0u || (v == SUI_IE_STAGE1 && st->spi_att && !st->ie_on && !st->armed);", "return v == 0u || (v == SUI_IE_STAGE1 && st->spi_att && !st->ie_on);"),
    ("judge: IE rule allows 1 while IE is on", "return v == 0u || (v == SUI_IE_STAGE1 && st->spi_att && !st->ie_on && !st->armed);", "return v == 0u || (v == SUI_IE_STAGE1 && st->spi_att && !st->armed);"),
    ("judge: IE rule refuses 0", "return v == 0u || (v == SUI_IE_STAGE1", "return (v == SUI_IE_STAGE1"),
    ("judge: IE state not tracked", "\tif (off == SUI_REG_IE) {\n\t\tst->ie_on = v != 0u;\n\t}\n", ""),
    ("judge: IE state tracks the wrong way", "st->ie_on = v != 0u;", "st->ie_on = v == 0u;"),
    ("judge: refusal not counted (read)", "\tif (!sui_judge(st, SUI_R, off, 0u)) {\n\t\t__atomic_fetch_add(&st->refused, 1u, __ATOMIC_RELAXED);\n", "\tif (!sui_judge(st, SUI_R, off, 0u)) {\n"),
    ("judge: refusal not counted (write)", "\tif (!sui_judge(st, SUI_W, off, v)) {\n\t\t__atomic_fetch_add(&st->refused, 1u, __ATOMIC_RELAXED);\n", "\tif (!sui_judge(st, SUI_W, off, v)) {\n"),
    ("judge: read refusal returns the bus error code", "\t\t__atomic_fetch_add(&st->refused, 1u, __ATOMIC_RELAXED);\n\t\treturn SUI_E_REFUSED;\n\t}\n\tif (o->rd(", "\t\t__atomic_fetch_add(&st->refused, 1u, __ATOMIC_RELAXED);\n\t\treturn SUI_E_BUS;\n\t}\n\tif (o->rd("),
    ("judge: a refused write still reaches the bus", "\tif (!sui_judge(st, SUI_W, off, v)) {\n\t\t__atomic_fetch_add(&st->refused, 1u, __ATOMIC_RELAXED);\n\t\treturn SUI_E_REFUSED;\n\t}\n", "\tif (!sui_judge(st, SUI_W, off, v)) {\n\t\t__atomic_fetch_add(&st->refused, 1u, __ATOMIC_RELAXED);\n\t}\n"),
    ("judge: reads are not judged", "\tif (!sui_judge(st, SUI_R, off, 0u)) {", "\tif (0) {"),
    # --- boot-arg / strings
    ("bootarg: 3 accepted as stage 1", "\tif (v == 1u) {\n\t\treturn SUI_MODE_ON;\n\t}", "\tif (v == 1u || v == 3u) {\n\t\treturn SUI_MODE_ON;\n\t}"),
    ("bootarg: invalid value without a GAP", "\t*gap = 1;\n\treturn SUI_MODE_0;\n}", "\treturn SUI_MODE_0;\n}"),
    ("bootarg: invalid value counts as stage 1", "\t*gap = 1;\n\treturn SUI_MODE_0;\n}", "\t*gap = 1;\n\treturn SUI_MODE_ON;\n}"),
    ("bootarg: absent honours the value", "\tif (!present || v == 0u) {", "\t(void)present;\n\tif (v == 0u) {"),
    ("bootarg: 2 is stage 1", "\tif (v == 2u) {\n\t\treturn SUI_MODE_RO;", "\tif (v == 2u) {\n\t\treturn SUI_MODE_ON;"),
    ("bootarg: 1 is read only", "\tif (v == 1u) {\n\t\treturn SUI_MODE_ON;", "\tif (v == 1u) {\n\t\treturn SUI_MODE_RO;"),
    ("mode string: armed shown as idle", "return st->armed ? \"irq\" : \"idle\";", "return \"idle\";"),
    ("mode string: reason ignored", "\tif (st->why != SUI_Y_NONE && st->why < sizeof off / sizeof off[0]) {\n\t\treturn off[st->why];\n\t}\n", ""),
    # --- ie_off (review 0354 finding 2)
    ("ie_off: no write", "\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);\n\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n}", "\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);\n\t(void)o;\n}"),
    ("ie_off: armed not cleared", "\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);\n\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n}", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n}"),
    ("ie_off: writes even when IE is not on", "\tif (st->mode != SUI_MODE_ON || !st->ie_on) {\n\t\treturn;\n\t}\n\t__atomic_store_n", "\tif (st->mode != SUI_MODE_ON) {\n\t\treturn;\n\t}\n\t__atomic_store_n"),
    ("ie_off: runs only while armed (misses a disarm in progress)", "\tif (st->mode != SUI_MODE_ON || !st->ie_on) {\n\t\treturn;\n\t}\n\t__atomic_store_n", "\tif (st->mode != SUI_MODE_ON || !st->armed) {\n\t\treturn;\n\t}\n\t__atomic_store_n"),
    ("ie_off: writes IE 1", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n}\n\n/* The interrupt wait", "\t(void)sui_wr(st, o, SUI_REG_IE, 1u);\n}\n\n/* The interrupt wait"),
    ("ie_off: also detaches (not lock-free safe)", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n}\n\n/* The interrupt wait", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n\t(void)o->spi_detach(o->ctx);\n}\n\n/* The interrupt wait"),
    ("ie_off: logs", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n}\n\n/* The interrupt wait", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n\to->note(o->ctx, SUI_G_OFF, 0u, 0u);\n}\n\n/* The interrupt wait"),
    ("ie_off: also reads IS", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n}\n\n/* The interrupt wait", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n\t{ uint32_t x = 0; (void)sui_rd(st, o, SUI_REG_IS, &x); }\n}\n\n/* The interrupt wait"),
    ("ie_off: writes IS too", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n}\n\n/* The interrupt wait", "\t(void)sui_wr(st, o, SUI_REG_IE, 0u);\n\t(void)o->wr(o->ctx, SUI_REG_IS, 1u);\n}\n\n/* The interrupt wait"),
    # --- stage 0
    ("ro: init writes", "\to->note(o->ctx, SUI_N_RO1, ia, cn);", "\t(void)o->wr(o->ctx, SUI_REG_IS, is);\n\to->note(o->ctx, SUI_N_RO1, ia, cn);"),
    ("ro: init in any mode", "\tif (st->mode != SUI_MODE_RO) {\n\t\treturn -1;\n\t}\n\tif ((r = sui_rd(st, o, (foff = SUI_REG_UTRLDBR), &db)) != 0 ||", "\tif ((r = sui_rd(st, o, (foff = SUI_REG_UTRLDBR), &db)) != 0 ||"),
    ("ro: busy not reported", "\tif (db != 0u) {\n\t\to->note(o->ctx, SUI_G_ARM_BUSY, db, 0u);\n\t}\n\to->note(o->ctx, SUI_N_RO1", "\to->note(o->ctx, SUI_N_RO1"),
    ("ro: UTRIACR and UTRLCNR swapped in the log", "o->note(o->ctx, SUI_N_RO1, ia, cn);", "o->note(o->ctx, SUI_N_RO1, cn, ia);"),
    ("ro: IE and IS swapped in the log", "o->note(o->ctx, SUI_N_RO2, ie, is);", "o->note(o->ctx, SUI_N_RO2, is, ie);"),
    ("ro: read failure silent", "\t\tsui_note_access(st, o, foff, r, 3u);     /* once, reads only: nothing to disarm */\n\t\treturn -1;", "\t\treturn -1;"),
    ("ro: cmd runs in any mode", "\tif (st->mode != SUI_MODE_RO) {\n\t\treturn;\n\t}\n\tr = sui_rd(st, o, SUI_REG_IS, &is);", "\tr = sui_rd(st, o, SUI_REG_IS, &is);"),
    ("ro: cmds not counted", "\tst->ro_cmds++;\n", ""),
    ("ro: is0 not counted", "\t\tst->ro_is0++;\n", ""),
    ("ro: OCS read when UTRCS is clear", "\tif (is & SUI_UTRCS) {\n\t\tst->ro_is0++;", "\tif (1) {\n\t\tst->ro_is0++;"),
    ("ro: OCS compares the whole word", "if ((o->ocs(o->ctx) & 0xffu) == 0x0fu) {", "if (o->ocs(o->ctx) == 0x0fu) {"),
    ("ro: OCS preset is 0x0e", "if ((o->ocs(o->ctx) & 0xffu) == 0x0fu) {", "if ((o->ocs(o->ctx) & 0xffu) == 0x0eu) {"),
    ("ro: OCS 0 counted as still preset", "if ((o->ocs(o->ctx) & 0xffu) == 0x0fu) {", "if ((o->ocs(o->ctx) & 0xffu) != 0x0fu) {"),
    ("ro: isx not counted", "\t\tst->ro_isx++;\n", ""),
    ("ro: isx counted for UTRCS", "\tif (is & ~SUI_UTRCS) {\n\t\tst->ro_isx++;", "\tif (is) {\n\t\tst->ro_isx++;"),
    ("ro: read failure of the per-command IS silent", "\t\tsui_note_access(st, o, SUI_REG_IS, r, 2u);      /* rate limited like the others: this runs once per command */\n", ""),
)

# mutants that must be equivalent (same behaviour, so the driver must still pass): name, old, new
EQUIVALENT = (
    ("ie_off: the mode check is only one of two layers (the judge has no IE write row in modes 0 and RO)", "\tif (st->mode != SUI_MODE_ON || !st->ie_on) {\n\t\treturn;\n\t}\n\t__atomic_store_n", "\tif (!st->ie_on) {\n\t\treturn;\n\t}\n\t__atomic_store_n"),
    ("judge: a UTRLCNR write row alone is still stopped by the value rule (two independent layers)", "\t{ SUI_W, SUI_MB(SUI_MODE_ON),                       SUI_REG_IS },", "\t{ SUI_W, SUI_MB(SUI_MODE_ON),                       SUI_REG_IS },\n\t{ SUI_W, SUI_MB(SUI_MODE_ON), SUI_REG_UTRLCNR },"),
    ("the storm window start is initialised on the first ISR only (win_start update order)", "\t\tst->win_start = now;\n\t\tst->win_n = 0;\n", "\t\tst->win_n = 0;\n\t\tst->win_start = now;\n"),
    ("arm: the stale IS read result is also used for the aggregation test order (UTRIACR GAP before the note)", "\to->note(o->ctx, SUI_N_UTRIACR, v, 0u);\n\tif (v & 0x80000000u) {\n\t\to->note(o->ctx, SUI_G_AGGR, v, 0u);\n\t}\n", "\tif (v & 0x80000000u) {\n\t\to->note(o->ctx, SUI_G_AGGR, v, 0u);\n\t}\n\to->note(o->ctx, SUI_N_UTRIACR, v, 0u);\n"),
)


def swap_mutants(core):
    """mutants that move text instead of replacing it"""
    m = []
    a, b = "\to->note(o->ctx, SUI_N_ARMED, v, 0u);\n\tr = o->spi_enable(o->ctx, &en);\n\tif (r != 0 || en != 1u) {\n\t\to->note(o->ctx, SUI_G_ENABLE, (uint32_t)r, en);\n\t\tsui_disarm(st, o, SUI_Y_ENABLE);\n\t\treturn -1;\n\t}\n", None
    assert a in core
    en_block = "\tr = o->spi_enable(o->ctx, &en);\n\tif (r != 0 || en != 1u) {\n\t\to->note(o->ctx, SUI_G_ENABLE, (uint32_t)r, en);\n\t\tsui_disarm(st, o, SUI_Y_ENABLE);\n\t\treturn -1;\n\t}\n"
    ie_start = "\tif ((r = sui_wr(st, o, SUI_REG_IE, SUI_IE_STAGE1)) != 0) {"
    assert core.count(en_block) == 1 and core.count(ie_start) == 1
    m.append(("arm: SPI enabled before the IE write", core.replace(en_block, "", 1).replace(ie_start, en_block + ie_start, 1)))
    wk = "\t\to->wake(o->ctx);\n\t\treturn 0;\n\t}\n\t__atomic_fetch_add(&st->spurious"
    w1 = core[core.index("\tif (is != 0u) {\n\t\tuint32_t again = 0;"):core.index("\tif (now - st->win_start")]
    assert core.count(wk) == 1 and core.count(w1) == 1
    m.append(("isr: W1C after the wake (service before ack)", core.replace(w1, "", 1).replace(wk, "\t\to->wake(o->ctx);\n" + w1 + "\t\treturn 0;\n\t}\n\t__atomic_fetch_add(&st->spurious", 1)))
    d1 = "\t__atomic_store_n(&st->armed, 0, __ATOMIC_RELEASE);\n\tsui_first_why(st, why);\n\tif (st->ie_on && sui_wr(st, o, SUI_REG_IE, 0u) != 0) {\n\t\to->note(o->ctx, SUI_G_TEARDOWN, 1u, 0u);\n\t}\n"
    d2 = "\tif (st->spi_att) {\n\t\tif (o->spi_detach(o->ctx) != 0) {\n\t\t\to->note(o->ctx, SUI_G_TEARDOWN, 3u, 0u);\n\t\t}\n\t\tst->spi_att = 0;\n\t}\n"
    assert core.count(d1) == 1 and core.count(d2) == 1
    m.append(("disarm: detach before the IE write", core.replace(d2, "", 1).replace(d1, d2 + d1, 1)))
    d3 = "\tif (touched) {\n\t\tif (sui_rd(st, o, SUI_REG_IS, &is) != 0) {"
    i = core.index(d3)
    j = core.index(d2)
    assert i < j
    m.append(("disarm: detach before the IS clear", core[:i] + d2 + core[i:j] + core[j + len(d2):]))
    return m


def mutants(core):
    out = []
    for name, a, b in MUTANTS:
        if a is None:
            continue
        mc = core
        for x, y in (a if isinstance(a, list) else [(a, b)]):
            assert mc.count(x) == 1, "mutant text does not occur exactly once: %s (%d)" % (name, mc.count(x))
            mc = mc.replace(x, y, 1)
        out.append((name, mc))
    out.extend(swap_mutants(core))
    return out


