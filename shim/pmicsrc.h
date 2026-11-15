#pragma once
#include <stdint.h>

/*
 * pmicsrc.h - READ-ONLY reset-source latches of the two Tensor G1 PMICs (build.sh --pmic-src, gs101 only; docs/research/139 section 2 and 5 Phase A;
 * ALL-ON T-02/T-24). pmic_src() reads S2MPG10 STATUS1 0xC / STATUS2 0xD / PWRONSRC 0xE / OFFSRC 0xF and S2MPG11 STATUS1 0xC / OFFSRC 0xD through
 * acpm_pmic_src_read (FUNC_READ only, an allowlist of exactly those six registers) and prints them, so a silent hardware reset leaves a readable record:
 *   SWEET PMIC SRC: s2mpg10 STATUS1=0x.. STATUS2=0x.. PWRONSRC=0x.. OFFSRC=0x..
 *   SWEET PMIC SRC: s2mpg11 STATUS1=0x.. OFFSRC=0x.. (read-only, not cleared)
 * A failed read prints that register as `NAME=ERR <acpm error name>` (never a made-up value); after the first failure the remaining registers print
 * `ERR skipped` (the failed request may still be queued). If an ACPM gate refuses, every register prints `ERR refused: <reason>`, nothing is sent.
 * The interrupt registers INT1..INT6 (clear-on-read, read: mainline sec-acpm.c precious registers 0x00-0x05, review 0316) are never read. Without build.sh --pmic-src-clear nothing is written, here or in the PMIC
 * (the object has no write function, audited); with it see "Clear" below.
 * Returns 0 if all six were read.
 *
 * Command-line tokens (ALL-ON T-02/T-24; the shim console never reaches the host in a --boot-xnu boot): pmic_src(tok, cap) also writes, into the caller's
 * buffer (cap >= PMIC_SRC_TOK_MAX), up to three tokens that loader.c appends to the XNU command line after the sweetmif* ones and xnu patch 0318 prints:
 *   sweetps10=0xAABBCCDD   S2MPG10 STATUS1,STATUS2,PWRONSRC,OFFSRC, MSB first   (present only when all four were read)
 *   sweetps11=0xAABB       S2MPG11 STATUS1,OFFSRC, MSB first                     (present only when both were read)
 *   sweetpsrc=N            0 all six read; 1..6 the read of register index N-1 failed (index order: S10 STATUS1, STATUS2, PWRONSRC, OFFSRC, S11 STATUS1, OFFSRC)
 *                          and every later one was skipped; 7 an ACPM gate refused (nothing sent). Never a value for a register that was not read.
 * Always present once pmic_src ran: sweetpsrc. Worst case " sweetps10=0x........ sweetps11=0x.... sweetpsrc=N" = 50 characters + NUL.
 */
#ifdef PMIC_SRC_CLEAR
#define PMIC_SRC_TOK_MAX	80u	/* 50 + " sweetpsclr=NN" (14) + " sweetpspw=0xNN" (15) + NUL */
#else
#define PMIC_SRC_TOK_MAX	51u
#endif
#define PMIC_SRC_RC_REFUSED	7u
int32_t pmic_src(char *tok, uint32_t cap);
/*
 * Clear (build.sh --pmic-src-clear, implies --pmic-src, gs101 only; docs/research/142; the ONLY PMIC write of the flag): AFTER the six reads above succeeded and every ACPM gate passed
 * (the values are already captured for sweetps10/sweetps11), pmic_src() writes 0 to S2MPG10 OFFSRC 0xF, S2MPG10 PWRONSRC 0xE, S2MPG11 OFFSRC 0xD (in that order, Android's: google_bcl.c:3462-3463,
 * google_bcl_core.c:1155; acpm_pmic_src_clear FUNC_WRITE, value 0 only, 3-entry allowlist) and reads each back once (acpm_pmic_src_read). The first refused / failed step stops the sequence (no retry).
 * Also sweetpspw=0xNN (present only when the PWRONSRC read-back was done): the S2MPG10 PWRONSRC value read back after the write 0, reported, never an error.
 * One more token, always present with the flag once pmic_src ran, last in the line: sweetpsclr=N (step index k: 0 S10 OFFSRC, 1 S10 PWRONSRC, 2 S11 OFFSRC)
 *   0        all three written and every read-back was 0
 *   1..3     the write of step N-1 was refused or not confirmed (it MAY still have run: see PMIC SRC STATE; later steps not sent; nothing read back)
 *   4..6     the read-back of step N-4 failed (writes were done)
 *   7, 9     the read-back of OFFSRC (step N-7: 0 S10, 2 S11) was not 0 (the write was accepted but the latch did not clear); 8 is not used
 *   (the S10 PWRONSRC read-back is NOT an error: it can show the live power-on reason of this boot, hardware run resident-045835-b1 read 0x04 after the write 0)
 *   10       not attempted: a gate refused, one of the six reads failed or the token buffer was too small (nothing written)
 * N is written in DECIMAL (a hex digit A would be parsed by XNU as a string, pexpert/gen/bootargs.c getval).
 * Worst case " sweetps10=0x........ sweetps11=0x.... sweetpsrc=N sweetpsclr=NN sweetpspw=0xNN" = 79 characters + NUL.
 */
#define PMIC_CLR_WRITE		1u
#define PMIC_CLR_READ		4u
#define PMIC_CLR_NONZERO	7u
#define PMIC_CLR_SKIPPED	10u
#ifdef PMIC_SRC_CLEAR
struct acpm_cfg;
struct acpm_trace;
uint32_t pmic_src_clear(const struct acpm_cfg *c, struct acpm_trace *t, uint32_t *rb, uint32_t *nrb);	/* pmicsrc_clear.c; *rb = the *nrb read-back bytes, first one highest */
#endif
extern char pmic_src_tok[PMIC_SRC_TOK_MAX];	/* defined in shim.c (--pmic-src builds), appended by loader.c */
