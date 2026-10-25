# Review 1: GIC distributor read-only probe (draft 0108) and milestone M5 research (2026-10-24)

Reviewer: Sonnet 5.5, read-only. Tags: **[src F:L]** read from source, **[obs]** observed, **[guess]** not checked.
`K` = `~/.t3/worktrees/pixelios-gpu/kernel`, `X` = `~/pixelios-artifacts/scratch/stack-1nzT7o`, `D` = `~/pixelios-artifacts/gpu-drafts/0108-sweet-gic-probe-DRAFT.patch`,
`U` = `~/.t3/worktrees/pixelios-gpu/userland`.

## Part A: verdict SAFE WITH CONDITIONS (one real defect, A1; the rest are minor)

All accesses in `sm_gic_probe` (D hunk 1) are reads: CTLR, TYPER, IGROUPR/ISENABLER/ISPENDR/ISACTIVER word 14, IPRIORITYR byte, ICFGR, IROUTER (2x32), ISENABLER 0..30. No store. [src D]

### Checked and fine
- Offsets: IGROUPR 0x80+4*14=0xB8, ISENABLER 0x138, ISPENDR 0x238, ISACTIVER 0x338, IPRIORITYR 0x400+id (0x5C3..0x5C5), ICFGR 0xC00+4*28=0xC70, IROUTER 0x6000+8*id=0x6E18/20/28 match research 35 s.6 table (`K/docs/research/35-gpu-irq-path.md`, section 6) [src]. The array index arithmetic `d[(off)/4]` is correct (byte offset / 4). All offsets < 0x10000.
- Word count: TYPER=0x79041E, ITLinesNumber (bits 4:0) = 0x1E = 30 -> 31 ISENABLER words (0..30) = INTIDs 0..991 [obs, doc 35 s.2 table, shim log]; the loop `w < 31` stops exactly at the last implemented word; INTIDs 451-453 are in range. No read beyond the implemented range.
- Read side effects: GICD_CTLR (RWP is a read-only status bit), TYPER, ISENABLER/ISPENDR/ISACTIVER (set-registers read back the state, no clear-on-read), IGROUPR, IPRIORITYR, ICFGR, IROUTER: none have read side effects [guess: GICv3 spec IHI0069 not opened; same conclusion as doc 35 s.6]. The only GICD read already done by this port is CTLR (`T/pexpert/arm/pe_fiq.c:208,211`).
- Mapping: `ml_io_map` = `io_map(pa, size, VM_WIMG_IO, VM_PROT_DEFAULT, false)` [src X/osfmk/arm64/machine_routines.c:1556-1560]; after STARTUP_SUB_KMEM it is `kmem_alloc` in `io_submap` + `pmap_map(..., flags)` [src X/osfmk/arm/io_map.c:128-130]. VM_WIMG_IO is Device-nGnRnE for non-DRAM [src X/osfmk/arm/pmap/pmap.c:10649 comment; MAIR_DISABLE 0x00 `X/osfmk/arm64/proc_reg.h:1103`]. 0x10400000/0x10000 is 16 KiB aligned. pe_fiq.c maps the identical PA with the same call (`T/pexpert/arm/pe_fiq.c:130`) and 0102/0087/0088 map device pages the same way, so ml_io_map is the right call and a second alias of the same device page is fine (other thread maps like it; no pv tracking for I/O PAs [guess]). Cost: 64 KiB of permanent VA, irrelevant.
- Byte read: `volatile uint8_t` compiles to `ldrb`; GICv3 requires GICD_IPRIORITYR to support byte access [guess, spec not opened; doc 35 s.6 plans the byte reads]. Device-nGnRnE allows it.
- Secure/NS view: with DS=0 (SecurityExtn=1, TYPER bit 10 [obs]) an NS read of a Secure-only register or bit is RAZ, not a fault (architecture) [guess]; the port already saw `GICR_IGROUPR0` read zero from NS [src `T/pexpert/arm/pe_fiq.c:170-173`, doc 35 s.3 item 7]. So a hidden state looks like "0"; the probe cannot tell disabled from secure, as doc 35 s.7 says. Acceptable for the hazard question. CTLR will read 0x37-ish (pe_init_fiq has already written ARE_NS|EnableGrp1A|EnableGrp1 `pe_fiq.c:206-215`), so ARE_NS=1 and IROUTER is live (with ARE=0 it would be RES0). Reads of ICFGR/IROUTER for secure INTIDs are RAZ.
- Possible SError on unimplemented offset: every offset read lies in the architected, implemented GICD map for a 992-INTID GIC; reserved space inside a GICD frame is RAZ/WI by architecture. Residual risk is a SoC-level firewall in front of the GIC [guess, never observed]; CTLR/TYPER are the only GICD offsets ever read on this phone [obs]. The existing accesses to the same 64 KiB frame (CTLR) pass, so the frame is mapped and decoded. Accept: one boot cycle.
- Placement: `sm_gic_probe()` runs before `sm_bringup`, i.e. before any Mali IRQ mask is touched [src D hunk 2]. `sweet_mali_init` is only reached after the 0102 gate (PMU words, CMU mux) passed: `sweet_gpu_gs101_init` returns early otherwise [src `K/xnu/patches/0102-sweet-gpu-probe-xnu.patch:+68-110`, call at `0104.patch:25-29`]. So the GIC probe also needs `sweetgpu=0x1C500000`, and prints nothing if the GPU domain is off (harmless, but see A3).

### Findings

**A1 (MED, correctness of the "read-only" claim): `sweetmali=0x40` alone does nothing, and the working values run the WRITE path.**
`sweet_mali_init` returns before the new code when `(mode & 3u) == 0 || == 3` [src `K/xnu/patches/0104-sweet-mali-driver-core.patch:744-746`, the draft hunk adds the probe after that return, D line "started = 1; if (mode & 0x40u)"]. So `sweetmali=0x40` prints nothing, and `0x41`/`0x42` run the probe and then `sm_bringup` (kbase config writes, power-on, soft reset path) and, for 0x42, the selftest. That is not a read-only run and re-mixes a GIC question with the malireset1 conditions.
Fix (exact): in `sweet_mali_init` parse the mode first and run the probe before the `(mode & 3)` early return, with its own once flag:
```
static int started, gic_done;
uint32_t mode = 0;
if (!PE_parse_boot_argn("sweetmali", &mode, sizeof(mode))) return;
if ((mode & 0x40u) && !gic_done) { gic_done = 1; sm_gic_probe(); }
if (started || (mode & 3u) == 0 || (mode & 3u) == 3) return;
started = 1; ...
```
Then `sweetmali=0x40` is a pure read-only run (needs `sweetgpu=0x1C500000` for the gate, see A3). Update the run recipe in doc 35 s.7 item 5 accordingly. Also note `0x41|0x40` stays allowed for later.

**A2 (LOW, completeness vs doc 35 s.7):** the draft omits IGRPMODR row 14 (0xD38), the matching ISPENDR/ISACTIVER words for non-zero ISENABLER words (doc 35 s.7 item 3 asks for them), and the three Mali MASK/STATUS lines (item 4, optional). Fix: in the `for w` loop also print `d[(0x200+4*w)/4]` and `d[(0x300+4*w)/4]` when `en != 0`; add `IGRPMODR=%#x` with `d[(0xD00+4*14)/4]` to the row-14 line. Without IGRPMODR you cannot tell Group 1 NS from Group 1 Secure for bits seen in IGROUPR (group bit 1 + mode bit 1 = Secure Group 1) [guess, GICv3 two-bit group encoding].

**A3 (LOW):** an enabled bit in ISENABLER visible only on words that the loop prints; if every word is zero the log shows no `ISENABLER[` line at all, which could be mistaken for "probe died". Fix: print one final `SWEET MALI: gic probe done` line after the loop (the `--expect` regex for the run should be that line, not a register line).

**A4 (LOW, cosmetic):** `IROUTER=%#x%08x` prints `0` + `00000000` for a zero high word, i.e. `000000000`; use `%08x%08x` without `#`, or `%#llx` of a combined 64-bit. 32-bit accesses to the two halves of the 64-bit GICD_IROUTER are permitted by the GICv3 GICD (64-bit registers also accept 32-bit halves) [guess, spec not opened]; if the first run prints garbage (all-ones/0 for IROUTER with ARE_NS set) treat IROUTER as unverified, not as evidence.

**A5 (INFO):** the SPI numbers 419/420/421 stay second hand (doc 35 U1); the probe prints all non-zero ISENABLER words, which is the right hedge.

Conditions to run once: fix A1 (otherwise the run is 0x41/0x42 = the reset/selftest path again), A3 recommended, A2 recommended. Run as doc 35 s.7 item 5 (`--gpu-handover --cpus 1 --qr off`, USB log, `sweetgpu=0x1C500000 sweetmali=0x40`, `fastboot boot` only). Interpret with doc 35's decision table.

## Part B: M5 (no gate on `SweetMaliUserClient`)

### B1. What the kernel does when the property is set (source X)
- `is_io_service_open_extended` calls `service->newUserClient(owningTask, (void*)owningTask, type, ...)` [X/iokit/Kernel/IOUserClient.cpp:4421-4422]. So `initWithTask`'s `securityID` is the task, and `clientHasPrivilege(securityID, kIOClientPrivilegeAdministrator)` is usable there (it reads `TASK_SECURITY_TOKEN`, requires `token.val[0] == 0` i.e. euid 0, :1411-1424 and the administrator branch [src X:1415-1420 region, `0 != token.val[0]` -> NotPrivileged]).
- After the client is created, `copyProperty(kIOUserClientEntitlementsKey)` [X:4505]. For a **non-IOUserClient2022** class (`client->uc2022` false, :4457) the key is optional: missing = allowed; `kOSBooleanFalse` = allowed; an `OSString` = `disallowAccess = !IOTaskHasEntitlement(owningTask, str)` [X:4510-4524, the check at :4514-4522]. A value that is neither string nor false is only rejected for uc2022 (:4496-4503), so for our plain `IOUserClient` a wrongly typed value would be silently ignored (allow): set an OSString. On failure: `kIOReturnNotPrivileged`, then `clientClose()`, `release()` (:4560-4568), so the single-client token is freed by `free()` ([src 0105.patch fOwner logic]).
- Then MACF `mac_iokit_check_open` (:4524-4529) and the sandbox filter callbacks; the port has no policy implementing `iokit_check_open` [guess: only AMFI/Sandbox stand-ins `0035`, `0050`, which implement `mpo_policy_syscall`/vnode hooks; not grepped for `mpo_iokit_check_open`]. Note also `0050` says "SWEET runs without a sandbox" [src K/xnu/patches/0050-sweet-sandbox-policy.patch:+9-13]: **the review's M5 guess ("the app sandbox normally denies unknown user-client classes") is false on this port**: there is no sandbox, any process of any uid can open the class today.
- `IOTaskHasEntitlement(task, name)` = `amfi->OSEntitlements.queryEntitlementBooleanWithProc(proc, name) == KERN_SUCCESS` [src X/iokit/bsddev/IOKitBSDInit.cpp:1531-1553]. It is a BOOLEAN query: the entitlement must be present and equal to `<true/>`.

### B2. Is the entitlement data provided by this port? Yes, for XML entitlements in the signature
- `K/xnu/patches/0025`: AMFI stand-in answers "none". `0035` replaces it: on `vnode_check_signature` the XML entitlements blob (checked against the code directory by `csblob_get_entitlements`) is parsed into `SweetEntitlements` and attached to the cs_blob [src 0035.patch `sweet_amfi_attach_entitlements`, `csblob_os_entitlements_set`]; `query_boolean_with_proc` = `sweet_entitlements_bool(sweet_proc_entitlements(proc), name)` = dict value `== kOSBooleanTrue` [src 0035.patch:428-431, :164 region]. `0036` marks the signature `CS_SIGNED` so `csproc_get_blob()` finds the blob [src 0036.patch hunk `*cs_flags |= CS_SIGNED`]. DER-only signatures get no entitlements (0035 header comment).
- Observed on hardware? The 0035 test keys exist only in `tools/mkinit.py` test signatures (the `sweet_amfi_debug` path). I found no observation that an IOKit entitlement gate has ever passed on this port [guess: not grepped in HANDOFF]: treat the whole entitlement route as unverified on hardware.

### B3. Would setting the property block our own test binary? **Yes, as built today.**
- `U/tools/sweetmali-test/build.sh:12`: `codesign -f -s - out/sweetmali-test` = ad-hoc, **no `--entitlements`**. `U/tools/overlay-ios-root.sh:58` copies it unmodified into `/usr/local/bin`; launchd starts it as `UserName=root` (`:42`), program `/usr/local/bin/sweetmali-test`. No entitlements blob -> `ent == NULL` -> `KERN_DENIED` -> `IOTaskHasEntitlement` false -> `kIOReturnNotPrivileged`. The three launchd jobs (nomap/map/wc) would all fail to open. Also the binary lives on the root filesystem so it is a platform binary (0035 marks MNT_ROOTFS binaries platform), but that does not imply any entitlement (`sweet_entitlements_bool` looks only at the dict).
- Setting the property in `initWithTask` (the review's M5 fix text, `gpu-mali-reset-review-1.md:105`) works mechanically: `initWithTask` runs before the check at X:4505-4522 [src X:4421, 4505], and the failure path frees the token.

### B4. Minimal safe gating design (keeps our binaries working)
Pick one; B4a is the smallest and needs no signing change, B4b is the Apple-style gate.

**B4a (recommended first, a uid check, no dependency on the unverified entitlement path):** in `SweetMaliUserClient::initWithTask` (patch 0105, `0105.patch:123-139`), after `IOUserClient::initWithTask` succeeds and BEFORE `OSCompareAndSwap`, add
`if (IOUserClient::clientHasPrivilege(securityID, kIOClientPrivilegeAdministrator) != kIOReturnSuccess) { IOLog("SWEET MALI: open refused, not root\n"); return false; }`
[src: `securityID` is the owning task, X:4421; the administrator check is euid 0, X:1415-1420]. The launchd jobs run as root (`overlay-ios-root.sh:42`) so they pass; a mobile-uid app is refused (no sandbox exists to do it, B1). Return before touching `fLock`/`fBo` is wrong if they are already allocated: put the check FIRST (right after the base `initWithTask`) so nothing leaks, and `free()` must tolerate null `fLock/fBo` (it must already, since `initWithTask` returns false after allocating, `0105.patch:~129-131`: confirm `free()` frees both only if non-null). Note: root is not a security boundary against root daemons, which is acceptable until M4/H1 are fixed and a second client exists.

**B4b (entitlement, later):** `setProperty(kIOUserClientEntitlementsKey, "com.pixelios.sweetmali")` as an OSString in `initWithTask` (before return), AND sign the test binary with `codesign -f -s - --entitlements sweetmali.entitlements out/sweetmali-test` where the plist is `<key>com.pixelios.sweetmali</key><true/>` (XML must be present; a new Xcode also embeds DER, the XML blob is what `csblob_get_entitlements` reads [guess: verify on the real signature with `codesign -d --entitlements :-`]). Gate before use: boot once with `sweet_amfi_debug=1` and check `SWEET AMFI: sweetmali-test: 1 entitlements` is logged for the signed binary (0035 debug line) so the unverified route is observed before the property is set on a flashed image. If the property were set first and the binary were not re-signed/re-flashed, all three launchd jobs fail and a 13+8 minute cycle is wasted (cf. AP-23).

**Do not** use `kOSBooleanFalse` (allow-all) or a non-string value: for a non-2022 class both mean "allow" (X:4510-4524).

### B5. Fixes to apply to the M5 text in `gpu-mali-reset-review-1.md`
1. Replace the sandbox claim at line 99 with: "there is no sandbox on this port (`0050`), so any uid can open the class".
2. Replace the fix at line 105 with B4a now, B4b once the test binary is re-signed with entitlements and `sweet_amfi_debug=1` shows them.
3. Add to the condition at line 206 (`Fix H1 before any userland client ...`): "and the B3 fact: the current ad-hoc signed test binary has no entitlements, so B4b needs a re-sign (U/tools/sweetmali-test/build.sh:12) and a new overlay."

## Summary
Part A SAFE WITH CONDITIONS: fix A1 (probe is unreachable with 0x40 alone, and 0x41/0x42 run the write path), recommended A2/A3/A4. Part B: setting the entitlement property as written would block all three launchd test jobs (no entitlements in the ad-hoc signature, `build.sh:12`); use a root-uid check via `clientHasPrivilege(securityID, kIOClientPrivilegeAdministrator)` now, entitlement later after re-signing and an `sweet_amfi_debug=1` observation.
