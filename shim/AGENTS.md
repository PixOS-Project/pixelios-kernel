# shim/

Bare-metal AArch64 code that runs under `fastboot boot` before XNU (MMU off, EL2->EL1, no libc, `-mgeneral-regs-only
-mstrict-align`). Every bug here can hang or reset the phone.

- Freestanding C only: no libc, no stdlib allocation, no static initialisers holding absolute pointers (build.sh rejects
  ABS64 relocations); build pointer tables at run time.
- Every wait is counter-bounded; the watchdogs are disabled, so a hang stays a hang.
- UFS is read-only: the only commands that may reach the doorbell are NOP OUT, TEST UNIT READY, READ CAPACITY(10) and
  READ(10) inside the `userdata` GPT range. `ufs.c` checks the real UPIU bytes at the doorbell; do not bypass it.
- MMIO writes go through the allowlisted helpers (`usbgadget.c` `w32_allowed`, `ufsinit.c` register set). Adding a
  register means citing the Linux/ABL source in the comment and extending the audit in `tools/test-bluejay-boot.py`.
- Test with host tests first (`*_host_test.c`, fake hardware), then `tools/check-all.sh`, then hardware.
- SoC-specific code lives under `soc/<vendor>/<soc>/shim/`; keep `shim/` generic and key code off `SOC_GS101`.
- `--touch-probe[=run[,mux,cs,osc|top,loop,rst,misopu][,railsr|rails]]` (`touchprobe.c`): phase 1 reads only (USI/SPI registers only if the sysreg mode is
  SPI); `run` writes only through `w32` / `w32_allowed` / `wmask` (per-bit masks: the XNU `sweettouchclk=3 sweettouchvar=1` registers plus the
  docs/research/21 experiments, each only with its build option). Adding a register or bit means extending `wmask`, `touch_host_test.c`'s
  independent expectation and the negative tests, and citing the source. Output text and options: `docs/AUTOMATION.md`.

- PMIC/ACPM/PMU: allowed since 2026-10-22 (docs/SAFETY.md). Values and addresses must come from cited Linux/ABL sources; every write goes
  through the audited allowlist; read before write; bounded waits. Fuses/OTP/PMIC trim and charger limits stay off limits.
- `railsr` / `rails` (`acpm.c`, docs/research/23; work without `run`): ask the ACPM firmware to READ (`railsr`) or also set bit 7 of (`rails`) the touch rail
  registers S2MPG10 0x5C (LDO25M) / 0x5D (LDO26M). Only `acpm.c` writes SRAM / mailbox, through its own `w32` + `w32_allowed(pa, v, guard)` (one request slot,
  the request queue front, the reply queue rear, mailbox INTGR1/INTCR0 with the channel bit; armed only after the descriptor validated, both queues are
  empty and the soc.json `acpm` gates are set). The gates: `layout_verified`, `elem_unit_verified`, per-rail `vsel_min/vsel_max` (null = unknown = refuse; `tools/boards.py` derives and checks them, and fails on null with a verified flag).
  The two flags ship at 0: the first run (`railsr`) prints the descriptor, the queue-buffer words and `ACPM unsure: ...` and writes nothing; flip a flag only after comparing the dump with
  doc 23 (+ its erratum: `q_elem_size` is BYTES, must be 16). The request is never rolled back once its front is written (`acpm_guard`: slot and front writes close at the front write). A refusal performs zero writes (`acpm_host_test.c`, `rails_host_test.c`). Same audit as `touchprobe.c` (source + object, mutation self-tests).
- `--gpu-power` (`gpupower.c`, implies `--gpu-probe`, docs/research/17 section 2.4): opt-in Mali-G78 power on/off cycle. Writes only through `w32` / `w32_allowed`: exactly the LO words of
  SHADER_PWRON, TILER_PWRON, SHADER_PWROFF, L2_PWROFF, each only with its soc.json present mask; zero writes unless the preconditions hold; always powers back off (kbase JM order, a PWROFF only where READY != 0). The source audit
  pins the register defines and the allowlist text, so adding a register means extending `_GPUPOWER_REGS` / `_GPUPOWER_ALLOW`, `gpupower_host_test.c` and the negative tests, and citing kbase file:line. Output: `docs/AUTOMATION.md`.
- `--gpu-clk` (BUILT, UNRUN, premise refuted by the PLL_CON3 decode, doc 27; `gpuclk.c`, implies `--gpu-probe`, docs/AUTOMATION.md): asks the ACPM DVFS channel for the G3DL2 / G3D init rate (151000 kHz). `gpuclk.c` has NO MMIO access (audit: no `try_write32`/`try_read32`, stack-only stores); its single request site is
  `acpm_dvfs_reassert()` in `acpm.c` (GPU_CLK images only: domains 5/6, ceiling rate only, same `xfer()`/writer allowlist as the CPU DVFS client). The acpm.c DVFS tables hold the CPU and the GPU domains; `ACPM_DVFS_N` stays the CPU count that the touch probe iterates.
  Adding a domain or a rate means soc.json + `_ACPM_DVFS_DOMS`/`_GPU_PINS` in `tools/boards.py` + the host tests + the source-audit pins in `check_gpuclk_source_text`.
- `--gpu-rails` (`gpurails.c`, implies `--gpu-probe`, docs/AUTOMATION.md): READ-ONLY dump of the S2MPG11 GPU-rail registers through `acpm_s11_read()` (acpm.c, same `xfer()` as the S2MPG10 `railsr` read, FUNC_READ only, registers from soc.json `acpm.s2mpg11`). `gpurails.c` has NO MMIO access (audit); a null value in the soc.json block makes it refuse with zero ACPM traffic. Adding a register means soc.json + `_ACPM_S11_REGS` in `tools/boards.py` + the host test + the pins in `check_gpurails_source_text`.
- `--gpu-rail-on` (`gpurailon.c`, implies `--gpu-rails`, docs/AUTOMATION.md; WRITER): the only S2MPG11 write of the shim: `acpm_s11_rail_set()` in acpm.c = one FUNC_UPDATE of BUCK2S_CTRL 0x15 under mask 0xC0 with value 0x40 (mode 1 = what Linux writes) or 0x00 (off), only from exactly "off" with OUT1 in soc.json's window; `gpurailon.c` has no MMIO; shim.c skips the power cycle unless the rail is verified on and always restores afterwards. The audit pins the register, mask, values, guards, the two call sites and the shim.c order (`check_acpm_s11_writer_source`, `check_gpurailon_source_text`, `check_shim_rail_sequence`, `check_acpm_rail_set_object_text`); adding a write means all of them plus the host test and a review.
- `--gpu-s2mpu` (`gpus2mpu.c`): READ-ONLY dump of the four G3D S2MPU blocks (audited like gpuprobe.o); never write the S2MPU. `--gpu-job` (`gpujob.c`, docs/research/28; implies the whole rail/power machinery): the stage-2 WRITER. Its allowlist is the scratch window plus exact AS0 / JS0 / IRQ-clear register-value pairs (`_GPUJOB_ALLOW`, 21 pinned `w32` sites); `gpupower.c` gets exactly one hook (`gpu_job_run()` after the ON dump, pinned by the audit) and `gpujob.c` has no GPU_COMMAND / soft reset. The page-table / TRANSCFG / MEMATTR / JS_CONFIG values are derived in `tools/boards.py _gpu_job_values()` (cites in doc 28), never typed into C. Adding a write means the allowlist, the pinned site list, the host test's fake GPU, doc 28 section 7 and a review.
- `--wlan-rails` (`wlanrails.c`, implies `--wlan-probe`, docs/research/29 section 4; READ-ONLY): the S2MPG10 PCIe-PHY LDOs 0x53..0x56 through `acpm_s10_read()` (acpm.c, listed registers only, same gates as `--gpu-rails`),
  the CMU_HSI2 words only behind the UFS HCI VER gate, PMU+0x3C10. MMIO only via `rd()`; no store, no `try_write32` (`check_wlanrails_source_text`, object audit). Decoded lines are tagged `(guess)` where the bit layout is not from source.
- `--wlan-en` (`wlanen.c`, implies `--wlan-probe`, docs/research/29 W2b; WRITER, UNRUN on hardware): the only writes are `w32` of GPP14.DAT bit 2 (mask 0x4) and GPP14.CON field [11:8] (mask 0xF00, value 0 or 1), in Linux's order
  (DAT then CON to enable, DAT then CON to restore), zero writes unless WLAN_EN reads low and is a plain input and PERST reads low; always restores and reads back; `!!! RESTORE FAILED` if not. Adding a register or bit means
  extending `wmask`/`w32_allowed`, `wlanen_host_test.c` and `check_wlanen_source_text` (pinned text), and citing the source. Output and run command: `docs/AUTOMATION.md`.
- `--wlan-smc-read` (`wlansmc.c`, implies `--wlan-probe`, docs/research/29 W2a/section 12; EL3 READS only, UNRUN): the object has exactly ONE `smc #0`, in `smc_priv_read()`: x0 = 0x82000504 (SMC_CMD_PRIV_REG), x2 = 0 (PRIV_REG_OPTION_READ),
  x3 = 0, x1 = a PA that must be exactly PMU 0x17463EC4 or 0x17463EC0 (else no SMC). A WRITE/RMW option is unrepresentable (constants of the instruction sequence, audited in the disassembly:
  `check_wlansmc_object_text`). A later RMW/WRITE helper needs its own review (research 29 section 12); never widen this one.
- `--wlan-iso` (`wlansmc.c` WLAN_ISO_RUN block, linked as `wlansmc_iso.o` INSTEAD of `wlansmc.o`; implies `--wlan-smc-read --wlan-probe`; docs/research/29 section 12; EL3 WRITER, UNRUN): releases the PCIe1 PHY isolation like Linux's first
  power-on step: RMW `smc #0` x0 = 0x82000504, x1 = 0x17463EC4, x2 = 2, x3 = 1, x4 = 1, bounded read-back (bit 0 set, every other bit and 0x17463EC0 unchanged), then, after EVERY sent set, the same call with x4 = 0 (x5..x7 = 0 in both), a final read 10 ms later; the SMC READ must equal the MMIO word first. A `--wlan-iso` image compiles out level 2 and refuses `--wlan-en` (build.sh + `#error`). The two RMW
  helpers (`smc_priv_rmw_set/clear`) take no parameter but the result pointer; their constants are audited in the disassembly (`check_wlansmc_object_text(iso=True)`). Never a second set; a plain `--wlan-smc-read` image contains no RMW code.


- Coding rules: `docs/CODING-RULES.md` (CR-xx ids; each names its check). Read the rules for this area before editing; `tools/check-rules.py` enforces the lint ones in the gate.
