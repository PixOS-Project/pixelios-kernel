# Review lint-c-20t-r1: kernel commit 77cfffd (base dbd8631), branch tools/lint-c-20t

Reviewer: Claude Sonnet 5.5, adversarial. Tags: [obs] = observed by running it now, [src] = read from source (file:line), [guess].
Scope: `git diff dbd8631 77cfffd` = 17 shim/*.c, tools/lint-c, tools/test-bluejay-boot.py. No shell file in the diff (lint-c and test-bluejay-boot.py are python), so `shellcheck -S warning` has nothing to check [obs: shellcheck on tools/lint-c only reports the python shebang as non-shell].

## Verdict: SAFE WITH CONDITIONS

No new MMIO write, no changed MMIO address/mask/value, no changed loop bound or timeout was found (req. 2: none). Every widening cast is value-equivalent for the reachable ranges. The conditions are two small doc/log fixes (F1, F2), both non-blocking for a hardware run. The code that writes hardware (gpupower power_on) is a pure extraction with the same order and same early-exit semantics, and its audit pin is stricter than before.

## How it was checked [obs]

- Built dbd8631 and 77cfffd (git archive into /tmp/rv/old, /tmp/rv/new) with shim/build.sh and the shim compiler (aarch64-none-elf-gcc 15.2, -O2 -Werror), 7 option sets (bluejay: usb-gadget+udump+wlan-rails+wlan-link+gpu-job+gpu-clk+touch+el2; boot-xnu+gpu-handover+s2mpu; gpu-power=stack+rails+pstore-fast; wlan-iso; wlan-en; sweet; and a full build of `--gpu-power=stack --gpu-rails --wlan-probe` with mkbootimg). All compile clean under -Werror.
- Compared `objdump -dr` of every .o. Byte-identical text: shim.o, udump.o, ufs.o, mmu.o, pstore.o, wlanlink.o, wlansmc*.o, acpm.o differs only in `cmp w4,w2` -> `cmp w2,w4` followed by `b.ne` (symmetric, xfer, acpm.c). Differing: acpm_railon, console, fdt, gpujob, gpupower{,_stack,_job}, gpuprobe, gpurails, gpus2mpu, usbprobe, wlanprobe. Per-function mnemonic-multiset comparison (script /tmp/rv/cmpfn.py) plus a read of each differing function: see F4 table.
- Ran the repo's own audits on the new objects (source selftests + object audits for gpupower plain/stack/job, gpujob, gpurails, usbprobe/wlanprobe/gpuprobe/gpus2mpu, wlanlink, acpm, wlansmc, ufs safety): all PASS, and the full `build.sh` run for `--gpu-power=stack --gpu-rails --wlan-probe` passed its audit section (302 PASS lines) and all 73 host tests (re-run by hand: fail=0).
- tools/lint-c on new tree: `RESULT: PASS files=32 findings=0`; on the old tree: `RESULT: FAIL files=32 findings=52` (matches the "52" in the brief).

## Findings

### F1 (LOW, doc accuracy) tools/lint-c:17-19: the comment says `-D__asm__(x)=` "drops only the `__asm__("..")` label form"
It also erases every basic-asm statement written `__asm__("...")`: shim/udump.c:54 and :55 (`__asm__("crc32b %w0, %w0, %w1" : "+r"(c) : "r"(v));`, `crc32x`). Those become empty statements for cppcheck only. [obs] cppcheck output for udump.c and wlansmc.c is identical with and without the define (2 and 1 lines), and clang-tidy and the compiler never see the define, so nothing is hidden today. But the statement is false and a future finding inside an asm statement could silently vanish.
Fix: reword to "drops every `__asm__(` use (the register labels in shim.c:120-123, wlansmc.c:48-51,177-184,... and the two crc32 statements in udump.c:54-55); cppcheck does not analyse asm anyway". Optional hardening: use `-D__asm__(x)=` only when the file is shim.c/wlansmc.c (EXTRA table), leaving udump.c unaffected.

### F2 (LOW, rule 8 wording) shim/gpus2mpu.c:87-90 new `default` in base_of()
The default is unreachable (only caller loops i < 4, gpus2mpu.c:110) and does log, but with the plain tag `con_str("GPU", "S2MPU base_of: index beyond G3D3, reading G3D3")` while the project convention for "never silent" lines is a `... GAP:` prefix that the run summary greps (wlanprobe.c:243 uses `WIFI GAP:`). The read still goes to G3D3 (a fault-guarded read, no new write), so it is safe.
Fix: `con_str("GPU", "GPU GAP: S2MPU base_of: index beyond G3D3, reading G3D3");` (check the summary grep pattern first, tools/xrun); re-run `tools/test-bluejay-boot.py` (usb_safety on gpus2mpu.c passed with the current text [obs]).

### F3 (INFO, behaviour) shim/gpurails.c:106-109 and :114-119 rname()/mname() switch defaults
Old: any k >= 9 returned "LDO_CTRL1", any mode >= 3 returned "on-normal". New: case 9 / case 3 explicit, other values print "REG?" / "mode?" (display labels only, no register access). Unreachable: ACPM_S11_NREG = 10 (shim/oA/board_config.h:170, generated from soc.json) and all three decode() callers mask `& 3u` (gpurails.c:206-208). Not a hardware path. No fix needed.

### F4 (INFO) objdump differences, all benign [obs: compared + read]
| file / function | difference | why benign |
|---|---|---|
| fdt.o fdt_read_reg_entry | `lsl w1,w10,#2; ldr [x0,w1,uxtw]` -> `ldr [x0,w10,uxtw #2]` | `ac` is 1..2 (fdt.c:128-132 rejects 0 and >2), `ac*4` cannot wrap |
| console.o put_char | `mul`+`add uxtw` -> `sxtw`+`madd` | operand `(y0+gy*SCALE+sy)` is int; sign-extension only differs for a negative value, which cannot occur: y0 = MARGIN + row*CH_H with row >= 0 (console.c:164-168), and `y0 + CH_H > fb_h` is rejected above. Old 32-bit wrap of `*(fb_stride/4)` needed > 2^32 elements (16 GiB), the FB is 0x9e3400 bytes (board_config.h FB_SIZE) |
| console.o con_clear | `lsr/uxtw` -> `ubfiz/and` | y < fb_h, product < 2^32 |
| gpuprobe.o fvmap_dom | `umaddl` -> `umull`+add | idx is GPU_FVMAP_IDX_* (constants 2 call sites gpuprobe.c:252-253); `orv + 8*l` bounded by `nlv <= FVMAP_MAX_LV` and `orv + 8*nlv <= FVMAP_BYTES` (gpuprobe.c:186) before the loop |
| gpuprobe.o cmu_dump | same multiset | `4u*k`, k small loop |
| usbprobe.o usbprobe_report | merged `s==1||s==3` compare, `row*16u` as u64, `(row*4+col)*4u` as uintptr | row < USBPROBE_PHY_DUMP_WORDS/4 = 4 (usbprobe.h:45), put_hex takes uint64_t (usbprobe.c:74); connect_speed_name returns the same string for every input (0 HS,1 and 3 FS,2 LS,4 SS,5 SSP, else "(?)"); header comment at usbprobe.c:14-15 says 1 FS and "2/3 low/full-speed variants", consistent with both being printable as FS, and the raw value is printed by the caller |
| gpujob.o gpu_job_run (build, w32_allowed inlined) | extra spills/tail-merge | build() loop bound is still 5120 (`mov x25,#0x1400`), scratch window still `base + 0x5000`; `w32` and `w32_allowed` function bodies are not in the diff list, so byte-identical |
| acpm.o xfer, acpm_railon.o | `cmp` operand order flip before `b.ne` | symmetric condition |
| gpupower*.o gpu_power_cycle | power_on() inlined, frame size changes (224 -> 208 bytes), regalloc | same sequence of calls by name (24/29/29 calls, identical order list). Source-level equivalence shown in F5 |
| gpurails.o, gpus2mpu.o, wlanprobe.o | switch default added | intentional, see F2/F3/F6 |
| udump.o ufs.o mmu.o pstore.o wlanlink.o wlansmc*.o shim.o | none | byte-identical text in all 7 builds |

### F5 (INFO, verified equivalent) shim/gpupower.c power_on() extraction (:346-371, call at :396)
Old: `else if` chain assigning `up` (initial "ok"); every later step ran only if all earlier steps passed. New: each step `return`s the same string on failure and `return "ok"` at the end; `up = power_on()` is assigned once before `t1 = touch_ticks()` (gpupower.c:396-397), so the following dump/OFF sequence is unchanged. Order for plain: TILER_ON write, tiler wait, L2 wait, SHADER_ON write, shader wait; stack build inserts STACK_ON write + stack wait between the L2 wait and the shaders (old chain gpupower.c@dbd8631:369-383, same). Same masks (GPU_TILER_PRESENT, GPU_STACK_PRESENT, GPU_SHADER_PRESENT), same uname() strings, no retry. `int e` removal: no other use (compiles under -Werror). dname() default "unknown-code" (gpupower.c:336-337): old fallthrough returned "ok" for unknown codes; callers pass 0..4 only (power_off()/gpu_power_cycle()), and the new string is shown in the POWER line and makes the cycle fail (`down[0] != 'o'`), strictly louder.
The `precheck()` loops `unsigned i/h` -> `uintptr_t` (gpupower.c:269-276): constants 0x10u*i, 4u*h, i<3, h<2, no wrap either way; only reads.

### F6 (INFO, verified) other hunks
- acpm.c: every `4u * i` -> `(uintptr_t)4u * i`: i is a loop counter < 8, < ACPM_DESC_WORDS or < 4, or in acpm_qword the 64-bit guard `(uint64_t)off + 4u*(uint64_t)i + 4u > c->sram_size` (acpm.c:321) already rejects every i whose 32-bit product could wrap. D_ID is a small constant. No change in address.
- console.c, fdt.c, gpuprobe.c, gpujob.c: see F4.
- ufs.c:306,312,707,715 `ents + b*BLOCK`, `entries + i*128`: GPT `nent > 128` rejected (ufs.c:399, :703) so i*128 < 16 KiB. Object identical.
- usbprobe.c / wlanlink.c: wlanlink `DELAY_MAX_US 250000u` -> `250000ull` (wlanlink.c:33): same value, only the type of the comparison `r.a > DELAY_MAX_US` (r.a is a narrower unsigned) widens to u64; wlanlink.o byte-identical in all builds; pin `#defineDELAY_MAX_US250000ull` and the "longer delay" mutant updated in lock-step (test-bluejay-boot.py:3871, :3972); mutant still rejected (wlanlink selftest 268/268 before and after).
- udump.c:219,292,299 dropped `pos =` on the last put_dec of a line: `pos` is not read afterwards in any of the three (next use is `pos = put_str(line,..., 0, ...)` or return); put_dec is a pure buffer writer (no volatile/MMIO). udump.o identical.
- wlansmc.c:296 dropped `rest = "n/a"` initialiser: wlansmc.o/_iso/_link byte-identical and -Wmaybe-uninitialized under -Werror is clean, so every path sets `rest` before use.
- shim.c:1121 `(int64_t)smc(...)` cast: smc returns uint64_t (x0), same bits; only `(uint32_t)ret[i]` is printed. shim.o identical.
- wlanprobe.c:242-247 `default:` prints `WIFI GAP: wlan_probe_l2_group: unknown group 0x<g>`, loud (rule 8), read-only, reached only for group > 4 (WLAN_L2_GROUPS = 5, wlanprobe.h:84); groups 0..4 take the same paths as before (host test calls 0..4, passes).
- gpurails.c, gpus2mpu.c, mmu.c, pstore.c, wlanprobe.c are in the diff (they were not in the original 52-line list); all equivalent as above.
- Linker symbols (req. 1b): the NOLINT cites are right: `ENTRY(_head)` shim.ld:1, `_stack_top` shim.ld:15, `_end = .` shim.ld:19, `_head:` start.S:4 [src]. Nothing renamed (`_head/_end/_stack_top` are still the same identifiers in mmu.c:57-58, pstore.c:75-76, usbprobe.c:42-43, ufs.c:849-850), link unchanged (shim.elf linked in every build [obs]). `// NOLINT(bugprone-reserved-identifier)` sits on the same line as the extern, as clang-tidy requires (lint-c: 0 findings).

### F7 (INFO) tools/test-bluejay-boot.py pin edits do not loosen the audit
- gpujob pins `_GPUJOB_ALLOW`, `_GPUJOB_BUILD`, mutant "scratch window widened": each mirrors the source text exactly (`(uintptr_t)NPAGES*PG`, `uintptr_ti`); the widening mutant now replaces the new spelling, so it still bites. gpujob selftest 29/29 before and after, objects pass.
- wlanlink: see F6.
- gpupower: the old pins (first `w32(` in gpu_power_cycle after the refusal; ON order tiler<stack<shader by find(); `else if ((e=w32(G_STACK_ON` chain; stack ready before shader) are replaced by (a) an exact whitespace-free pin of the whole power_on() body (`_GPUPOWER_ON`, `_GPUPOWER_ON_STACK`) which I diffed against shim/gpupower.c:346-371 for both variants: identical, (b) w32 counts power_on=2/3, gpu_power_cycle=0, power_off=1 (so no write can be added to gpu_power_cycle), (c) `up=power_on();` exactly once, power_on( occurs exactly twice in the file (definition + that call), and it comes before the OFF sequence, (d) first `power_on(` after the `if (why) {...return 1;}` refusal. That is strictly stronger than the old text (old checks only ordered find() positions).
- Mutants: the 36 gpupower mutants are all rejected (36/36 before and after; three were rewritten to the new text). I also wrote 15 extra mutants (/tmp/rv/mut.py) against check_gpupower_source_text: swapped tiler/shader write, dropped L2 wait, every wait `return` turned into `(void)0`, ignored write error, extra `w32` in gpu_power_cycle, power_on twice, power_on before the refusal, retry loop, changed shader mask, power_on called from power_off, uname swapped, precheck dropped: all killed, none survived, none a no-op.

## Requirement checklist
1. (a) widening casts: all equivalent (F4/F6). (b) reserved identifiers: cited correctly (F6). (c) switch defaults: gpurails/gpus2mpu/gpupower dname/wlanprobe are display or unreachable, all log (F2 wording, F3, F5, F6). (d) dead stores: udump `pos`, wlansmc `rest`, no volatile/MMIO involved. (e) gpupower assignment-in-if split: order and side effects preserved (F5). (f) shim.c cast and usbprobe branch merge: equivalent.
2. New/changed MMIO write, address, mask, value, loop bound, timeout: none found.
3. Pins: F7.
4. lint-c `-D__asm__(x)=`: needed ([obs] without it `cppcheck shim/shim.c` stops with `shim/shim.c:120:error:syntaxError:syntax error`; with it the file is checked and reports style notes only); description over-claims (F1).
5. wlanprobe/gpurails/gpus2mpu/mmu/pstore edits: present and equivalent (F6).
6. Before/after objdump: F4.

## Status (driver 20u)
F1 (comment reworded) and F2 (`GPU GAP:` prefix) fixed in 113e2c5; lint-c RESULT PASS files=32 findings=0 after the fix. F3-F7 info, no change.
