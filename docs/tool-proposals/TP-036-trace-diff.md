---
id: TP-036
tool: "tools/trace-diff"
status: done
proposed_by: "claude-opus-5-5 (driver, method-plan)"
date: 2026-10-27
exposed_by: "PLAN-METHOD P0 step 5 + research 85 s.8 step 5 (super docs): the golden session produces raw ftrace files that no tool turns into a comparable register sequence"
extends: ""
related: "TP-035"
usage: "tools/trace-diff golden-regs GOLDEN_DIR --block wifi|gpu|dpu [--out CSV] | tools/trace-diff diff --golden CSV --run RUN_DIR [--from MARK] [--to MARK] [--block wifi]"
refs: "branch tools/trace-diff 9613a9c (tools/trace-diff, tools/test_trace_diff.py 35 tests); review super docs/reviews/trace-diff-r1.md"
---

## checked
`tools/tool-proposals check` for trace-diff, golden-regs, vmallocinfo, rwmmio: `RESULT: NONE` for all four [obs 2026-10-27]; `grep -rli 'trace-diff|golden-regs' tools docs`: no hit. TP-035 (fboot-android, golden-pull) only captures the raw files (`trace-*.txt`, `vmallocinfo-*.txt`, `iomem.txt`, kprobe `pcicfg/*` events); nothing parses them. `tools/xrun_report.py` matches `--expect` regexes against the run log line by line; it has no notion of an ordered register sequence.

## why
Without it, SPEC-WIFI.md (PLAN-METHOD P1) and every later run-plan prediction would come from reading a multi-million-line trace by hand. Research 85 s.4.1: rwmmio records the kernel VIRTUAL address, so each line has to be joined to the vmallocinfo `phys=` rows by hand [src research 85 s.4.1]. One 64 MiB/CPU buffer holds about 1.6 M events per CPU [guess, research 85 s.4.2]. That is not doable by hand. The cost cannot be measured until the session runs; the alternative is the trial-and-error loop the user stopped on 2026-10-27.

Two subcommands, one file (shared parser, Python stdlib):
1. `golden-regs GOLDEN_DIR --block B`, research 85 s.8 step 5 (a)-(d):
   - (a) Parse `trace-*.txt` in the ftrace text format `<task>-<pid> [cpu] <flags> <ts>: rwmmio_write: <caller+off/len> width=W val=0xV addr=0xA`, and the same for `rwmmio_post_read` and the `pcicfg/*` kprobe lines. The format is `"%pS width=%d val=%#llx addr=%#lx"` [src research 85 s.4.1, linux v6.1 include/trace/events/rwmmio.h].
   - (b) Join `addr` to the `vmallocinfo-<same suffix>.txt` ` ioremap` rows (`0xVA-0xVA_END size caller phys=0xPA ioremap`). Use the snapshot taken after the same step, because module reloads move mappings (s.4.1). An unjoined address is a `TRACE GAP:` line plus a count; it is never dropped silently (rule 8).
   - (c) Keep only the rows for the block. Phys ranges come from `soc/google/gs101/soc.json`, the same table as research 85 s.5.1; no constants in the tool. Callers are a second key (`dhd*`, `kbase*`, `decon*|dpp*|exynos*`).
   - (d) Split by `MARK <row>` trace_marker lines.
   - (e) Wi-Fi only: normalise to the space XNU uses. BAR0 accesses keep the current BAR0 window and the offset apart (amended 2026-10-27: the BAR0 internal layout is not cited, research 32:153 only gives the 32 KiB map size, so no win+off sum is assumed). The window comes from the `pcicfg/wrd32` kprobe writes to config pos 0x80 (bcmdhd `PCI_BAR0_WIN` = 0x80, `include_pcicfg.h:488` [src super docs/research/32 :154]). The BAR2/TCM window is config 0x84, matching `swl_wr` `SWW_CFG_WIN` [src kernel xnu/patches/0129, 0130:379].
   - Output `golden/<block>-regs.csv`: `seq,ts,src,mark,caller,op(W|R|CW|CR),width,space(bar0|bar2|cfg|mmio),win,addr,val` (win = last w32 to cfg 0x80 for bar0 / 0x84 for bar2, addr = phys - BAR base; empty win for cfg/mmio), plus a header comment with the source file sha256s.
2. `diff --golden CSV --run RUN_DIR`: align the XNU run's per-store lines with the golden CSV in `[--from MARK, --to MARK]` and print the FIRST divergence: index, golden row, run row, and the 5 rows before it.
   - Only writes are compared by default: reads carry chip state such as counters and timestamps.
   - `--reads` adds read values, with a `--mask` list for fields known to vary (e.g. shared address per boot [obs F1/F2]).
   - An access the golden has but XNU skips, and the reverse, are both divergences.
   - Alignment is ordered, not set-based, because the order is the spec.

XNU-side input (prerequisite, NOT part of this tool): XNU must print one line per store from the single writer `swl_wr()` (0129:590) / `swl_restore()`. Proposed form: `SWEET MMIO <W|R|CW|CR> wifi <space> <win|-> 0x<addr> <width> 0x<val>` (matches `swl_wr(space, win, off, val)`; width = bits as rwmmio passes it, e.g. 32 [guess, until cited from linux asm-generic/io.h log_write_mmio callers]) behind a boot-arg bit (e.g. `sweetwlan` flag 0x100), for the Wi-Fi worker's next patch. Until it exists, `diff` can only compare the per-row prose lines (`W8 cfg 0x84 := ...`) through a small table of regexes per W-row. That is weaker; the tool then reports `PARTIAL`.

## output
- `golden-regs`: one line per mark section (`mark=<row> writes=N reads=M unjoined=K`), then `RESULT: PASS block=wifi rows=N unjoined=0 csv=PATH`. Any unjoined row, or zero rows for the block, gives `PARTIAL` (exit 1). Missing or unparseable input gives `ERROR` (exit 2).
- `diff`: `FIRST DIVERGENCE at golden seq S (mark M): golden W bar0 0x18003000 0x10 32 0x1 | run W bar0 0x18003000 0x10 32 0x0`, with the 5 preceding rows. The last line is `RESULT: PASS no divergence through MARK` (exit 0), `FAIL first divergence seq=S` (exit 1), or `PARTIAL|ERROR` (exit 2).
- The CSV and the reports go under `~/pixelios-artifacts/golden/<stamp>/` (never in git, rule 4: raw traces may hold MACs). Run-side reports go into the run dir.
- Bounded: a single pass over the files, so a 1.6 M-line trace takes seconds [guess]. No device access.

## risks
None for hardware: the tool is host-only and never calls adb or fastboot. Data risk: golden files may hold serial numbers or MACs, so the tool refuses `--out` inside a git checkout (the same check as golden-pull). Correctness risks: a wrong window-register offset in step (e) would misplace every BAR0 row, so verify it from source before building (rule 7). A partial join must never pass as PASS. The ordered comparison may flag harmless reorderings (e.g. polls), so a `--collapse-polls` rule (same addr+val repeated) is needed, and it is reported in the summary, not silent.

## amendments
(none)
