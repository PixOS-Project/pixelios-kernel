/*
 * Milestone 5 probe: can we talk to the internal UFS that ABL booted from?
 *
 * Runs with the MMU off (all memory Device, so DMA is coherent with no cache
 * maintenance). Dumps the UFSHCI registers ABL left behind and, if the host
 * is enabled and the link is up, points the transfer request list at our own
 * buffers and issues READ-ONLY SCSI commands on LUN 0. Nothing is ever
 * written to the device: there is no SCSI WRITE in this file.
 *
 * Register layout and descriptor formats: JEDEC UFSHCI (JESD223).
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"
#include "ufs.h"
#include "ufsdiag.h"
#include "ufsinit.h"

#ifndef UFS_BASE
#define UFS_BASE	0x01d84000UL
#endif

#define REG_CAP		0x00
#define REG_VER		0x08
#define REG_IS		0x20
#define REG_IE		0x24
#define REG_HCS		0x30
#define REG_HCE		0x34
/* UIC error indicator registers (JESD223, 0x38..0x48). */
#define REG_UECPA	0x38
#define REG_UECDL	0x3c
#define REG_UECN		0x40
#define REG_UECT		0x44
#define REG_UECDME	0x48
#define REG_UTRLBA	0x50
#define REG_UTRLBAU	0x54
#define REG_UTRLDBR	0x58
#define REG_UTRLCLR	0x5c
#define REG_UTRLRSR	0x60

#define HCS_DP		(1u << 0)
#define HCS_UTRLRDY	(1u << 1)
#define HCS_UCRDY	(1u << 3)

#define BLOCK		4096u

static inline uint32_t ufs_rd(uint32_t off) { return *(volatile uint32_t *)(UFS_BASE + off); }
static inline void ufs_wr(uint32_t off, uint32_t v) { *(volatile uint32_t *)(UFS_BASE + off) = v; }

/* UTP transfer request descriptor (32 bytes). */
struct utrd {
	uint32_t dw0;		/* [31:28] CT, [26:25] DD, [24] I */
	uint32_t dw1;
	uint32_t dw2;		/* [7:0] OCS */
	uint32_t dw3;
	uint32_t ucdba;		/* command descriptor base, 128-byte aligned */
	uint32_t ucdbau;
	uint32_t resp;		/* [31:16] response UPIU offset, [15:0] length (DWORDs) */
	uint32_t prdt;		/* [31:16] PRDT offset, [15:0] entries (DWORDs/entries) */
};

/* UTP command descriptor: command UPIU, response UPIU, PRDT. */
struct ucd {
	uint8_t cmd[512];
	uint8_t resp[512];
	struct {
		uint32_t addr_lo, addr_hi, rsvd, dbc;	/* dbc = byte count - 1 */
	} prdt[2];		/* 2 x 4 KiB, the size of the static data buffers */
};

static struct utrd utrl[32] __attribute__((aligned(1024)));
static struct ucd ucd __attribute__((aligned(128)));
static uint8_t data[2 * BLOCK] __attribute__((aligned(4096)));

static uint64_t ticks(void)
{
	uint64_t t;
	__asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(t));
	return t;
}

static void zero(void *p, unsigned n)
{
	volatile uint8_t *b = p;
	for (unsigned i = 0; i < n; i++)
		b[i] = 0;
}

static uint32_t be32(const uint8_t *p)
{
	return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

/*
 * Data direction for scsi(). This enum deliberately has no write value:
 * the probe is read-only (docs/SAFETY.md). The UPIU data-direction flags
 * and the UTRD DD field in scsi() derive only from this enum, so a write
 * cannot be expressed through this interface at all.
 */
enum scsi_dir {
	SCSI_DIR_NONE	= 0,	/* command with no data transfer */
	SCSI_DIR_READ	= 1,	/* device -> host */
};
_Static_assert(SCSI_DIR_NONE == 0 && SCSI_DIR_READ == 1,
	"scsi() directions are NONE and READ only; no write direction may exist");

/*
 * UPIU transaction types this probe may issue. NOP OUT (0x00) reaches
 * the device with no SCSI layer at all, so its result separates 'path
 * to the device is dead' from 'READ CAPACITY specific'; COMMAND (0x01)
 * carries the CDBs. There is deliberately no QUERY REQUEST here:
 * queries are the UFS write path (docs/SAFETY.md).
 */
enum upiu_type {
	UPIU_NOP_OUT	= 0x00,
	UPIU_COMMAND	= 0x01,
};
_Static_assert(UPIU_NOP_OUT == 0x00 && UPIU_COMMAND == 0x01,
	"upiu types are NOP OUT and COMMAND only; no query/write type may exist");

#define UPIU_RESP_NOP_IN	0x20u
#define UPIU_RESP_RESPONSE	0x21u

/* One buffer set an attempt runs on. All pointers are physical (MMU off). */
struct ufs_dma {
	struct utrd *utrl;
	struct ucd *ucd;
	uint8_t *data;		/* >= 2*BLOCK */
	uint8_t *entries;	/* 128*128 GPT entry buffer */
};

/*
 * Build and ring one UTRD (slot 0) + command UPIU. Returns 0 on success,
 * 1 on timeout (slot cleared via UTRLCLR, write ~1 per UFSHCI), 2 on OCS
 * error, 3 on unexpected response UPIU type, 4 on nonzero SCSI status,
 * 5 when the doorbell-time opcode/flag check refused the command.
 * dword_gran selects standard DWORD-unit RUO/RUL/PRDTO/PRDTL versus the
 * byte-granular Exynos layout (UFSHCD_QUIRK_PRDT_BYTE_GRAN).
 */
static uint32_t upiu_submit(const struct ufs_dma *dma, enum upiu_type type, uint8_t lun,
    const uint8_t *cdb, unsigned cdb_len, enum scsi_dir dir,
    void *buf, uint32_t len, int dword_gran,
    uint32_t *ocs_out, uint32_t *status_out, uint32_t *resp_type_out)
{
	/* Nexus type per slot (HCI_UTRL_NEXUS_TYPE=0xFFFFFFFE, as ABL left it): slot 0 carries
	 * device-management UPIUs (NOP), slot 1 carries SCSI commands. Tag == slot. */
	unsigned slot = type == UPIU_COMMAND ? 1 : 0;
	uint32_t bit = 1u << slot;
	struct utrd *utrd = &dma->utrl[slot];
	struct ucd *u = dma->ucd;
	zero(utrd, sizeof(*utrd));
	zero(u, sizeof(*u));

	/* Command UPIU: header (12 bytes), expected length, CDB. The flags
	 * byte is derived from dir alone: R (0x40, data in) for reads, 0x00
	 * for no-data commands. There is no write direction to request. */
	u->cmd[0] = type;
	u->cmd[1] = dir == SCSI_DIR_READ ? 0x40 : 0x00;
	u->cmd[2] = lun;
	u->cmd[3] = slot;
	u->cmd[12] = len >> 24;
	u->cmd[13] = len >> 16;
	u->cmd[14] = len >> 8;
	u->cmd[15] = len;
	for (unsigned i = 0; i < cdb_len; i++)
		u->cmd[16 + i] = cdb[i];

	unsigned nprd = (len + BLOCK - 1) / BLOCK;
	uintptr_t ucd_pa = (uintptr_t)u;
	/* dw0: CT=1 (UFS storage), DD from dir (2 = device-to-host, 0 = no
	 * data), I=1; NOP OUT and TEST UNIT READY are no-data commands. */
	utrd->dw0 = (1u << 28) | ((dir == SCSI_DIR_READ ? 2u : 0u) << 25) | (1u << 24);
	utrd->dw2 = 0x0f;			/* OCS: invalid until the host updates it */
	utrd->ucdba = (uint32_t)ucd_pa;
	utrd->ucdbau = (uint32_t)(ucd_pa >> 32);
	if (dword_gran) {
		utrd->resp = (512u / 4) << 16 | (512u / 4);
		if (len)
			utrd->prdt = (1024u / 4) << 16 | 4 * nprd;   /* 16-byte entries = 4 DWORDs each */
	} else {
		utrd->resp = 512u << 16 | 512u;
		if (len)
			utrd->prdt = 1024u << 16 | 16 * nprd;
	}
	if (len) {
		zero(buf, len);		/* a short DMA must not leave the previous block looking valid */
		/* HCI_*PRDT_ENTRY_SIZE=12: each PRD entry moves at most 4 KiB. */
		for (unsigned i = 0, off = 0; off < len; i++, off += BLOCK) {
			uintptr_t buf_pa = (uintptr_t)buf + off;
			u->prdt[i].addr_lo = (uint32_t)buf_pa;
			u->prdt[i].addr_hi = (uint32_t)(buf_pa >> 32);
			u->prdt[i].dbc = (len - off < BLOCK ? len - off : BLOCK) - 1;
		}
	}
	/* Last line of defence, checked on the bytes the controller will actually fetch (not on
	 * source spelling): only NOP OUT, TEST UNIT READY (0x00), READ CAPACITY(10) (0x25) and
	 * READ(10) (0x28) may be rung, never with the UPIU write flag or a host-to-device UTRD. */
	{
		uint8_t op = u->cmd[16];
		uint8_t fl = u->cmd[1];
		uint8_t ty = u->cmd[0];
		int op_ok = op == 0x00 || op == 0x25 || op == 0x28;
		int ok = (fl & 0x20) == 0 && ((utrd->dw0 >> 25) & 3u) != 1u &&
		    (ty == UPIU_NOP_OUT || (ty == UPIU_COMMAND && op_ok));
		if (!ok)
			return 5;		/* refused: never reaches the doorbell */
	}
	__asm__ volatile("dsb sy" ::: "memory");

	ufs_wr(REG_UTRLDBR, bit);
	uint64_t start = ticks();
	/* bounded: ticks() - start > counter_freq() (1 s) returns 1 below */
	for (;;) {
		uint32_t dbr = ufs_rd(REG_UTRLDBR);
		if (!(dbr & bit))
			break;			/* doorbell cleared: complete */
		if (ticks() - start > counter_freq()) {	/* 1 s */
			ufs_wr(REG_UTRLCLR, bit);	/* gs101 has BROKEN_REQ_LIST_CLR: write 1 to clear */
			return 1;			/* timeout */
		}
	}
	__asm__ volatile("dsb sy" ::: "memory");
	/* The host's write-back of the UTRD/response can land a little after the doorbell
	 * clears (separate bus paths): wait for OCS to leave its 0x0F preset. */
	for (uint64_t w = ticks(); (utrd->dw2 & 0xff) == 0x0f && ticks() - w < counter_freq() / 100;)
		__asm__ volatile("dsb sy" ::: "memory");
	ufs_wr(REG_IS, ufs_rd(REG_IS));		/* W1C UTRCS and friends */
	*ocs_out = utrd->dw2 & 0xff;
	if (*ocs_out == 7 && (u->resp[6] || u->resp[7]))	/* BROKEN_OCS_FATAL_ERROR, Linux condition: response or status set */
		*ocs_out = 0;
	*resp_type_out = u->resp[0];
	*status_out = u->resp[7];		/* SCSI status in the response UPIU */
	if (*ocs_out) {
		con_hex2("UFS ocs err", "is=", ufs_rd(REG_IS), "hcs=", ufs_rd(REG_HCS));
		con_hex2("UFS ocs err", "slot=", slot, "dbr=", ufs_rd(REG_UTRLDBR));
		for (unsigned o = 0; o < 32; o += 8)
			con_hex2("UFS ocs resp", "", be32(u->resp + o), "", be32(u->resp + o + 4));
		con_hex2("UFS ocs utrd", "dw0=", utrd->dw0, "dw2=", utrd->dw2);
		return 2;
	}
	if (*resp_type_out != (type == UPIU_NOP_OUT ? UPIU_RESP_NOP_IN : UPIU_RESP_RESPONSE))
		return 3;
	if (type == UPIU_COMMAND && !*status_out && be32(u->resp + 12))
		return 3;			/* short transfer: residual != 0 */
	return type == UPIU_COMMAND && *status_out ? 4 : 0;
}

/*
 * Issue one SCSI command (read direction or no data) in slot 0 on the
 * shim's own static buffers, with the build-time PRDT granularity.
 * Returns 0 on success, otherwise a code describing the failure stage.
 */
static uint32_t scsi(uint8_t lun, const uint8_t *cdb, unsigned cdb_len, enum scsi_dir dir,
    void *buf, uint32_t len, uint32_t *ocs_out, uint32_t *status_out)
{
	static struct ufs_dma dma;
	if (!dma.utrl)
		dma = (struct ufs_dma){ utrl, &ucd, data, 0 };
	uint32_t resp_type;
	return upiu_submit(&dma, UPIU_COMMAND, lun, cdb, cdb_len, dir, buf, len,
			  !UFS_PRDT_BYTE_GRAN, ocs_out, status_out, &resp_type);
}

static uint32_t read_blocks(uint32_t lba, uint32_t count, void *buf)
{
	uint8_t cdb[10] = { 0x28, 0, lba >> 24, lba >> 16, lba >> 8, lba, 0, count >> 8, count, 0 };
	uint32_t ocs, st;
	return scsi(0, cdb, sizeof(cdb), SCSI_DIR_READ, buf, count * BLOCK, &ocs, &st);
}

static uint32_t read_blocks_dma(const struct ufs_dma *dma, int dword_gran, uint8_t lun,
    uint32_t lba, uint32_t count, void *buf)
{
	uint8_t cdb[10] = { 0x28, 0, lba >> 24, lba >> 16, lba >> 8, lba, 0, count >> 8, count, 0 };
	uint32_t ocs, st, rt;
	return upiu_submit(dma, UPIU_COMMAND, lun, cdb, sizeof(cdb), SCSI_DIR_READ,
			   buf, count * BLOCK, dword_gran, &ocs, &st, &rt);
}

static uint64_t le64(const uint8_t *p)
{
	uint64_t v = 0;
	for (int i = 7; i >= 0; i--)
		v = v << 8 | p[i];
	return v;
}

/* Name the filesystem whose superblock starts at block lba (f2fs/ext4). */
static const char *fs_type(uint32_t lba)
{
	if (read_blocks(lba, 1, data))
		return "read error";
	const uint8_t *sb = data + 1024;
	if (sb[0] == 0x10 && sb[1] == 0x20 && sb[2] == 0xf5 && sb[3] == 0xf2)
		return "f2fs";
	if (sb[56] == 0x53 && sb[57] == 0xef)
		return "ext4";
	return "other";
}

/* One line per GPT entry of at least 64 MiB: name, first LBA, size in MiB (4K blocks). */
static void list_partitions(uint32_t entry_lba, uint32_t nent)
{
	static uint8_t ents[128 * 128] __attribute__((aligned(4096)));
	uint32_t blocks = (nent * 128 + BLOCK - 1) / BLOCK;
	for (uint32_t b = 0; b < blocks; b++) {
		if (read_blocks(entry_lba + b, 1, ents + (uintptr_t)b * BLOCK)) {
			con_str("UFS PART", "read error");
			return;
		}
	}
	for (uint32_t i = 0; i < nent; i++) {
		const uint8_t *e = ents + (uintptr_t)i * 128;
		uint64_t first = le64(e + 32), last = le64(e + 40);
		if (!first)
			continue;
		char name[17];
		for (int c = 0; c < 16; c++) {
			uint8_t ch = e[56 + 2 * c];
			name[c] = (ch >= 32 && ch < 127) ? ch : (ch ? '?' : 0);
		}
		name[16] = 0;
		if (((last - first + 1) >> 8) < 64)	/* keep the screen to the big ones */
			continue;
		con_hex2(name, "lba=", (uint32_t)first, "MiB=", (uint32_t)((last - first + 1) >> 8));
		if (!__builtin_strcmp(name, "userdata") || !__builtin_strcmp(name, "cust") ||
		    !__builtin_strcmp(name, "cache"))
			con_str(" fs", fs_type((uint32_t)first));
	}
}

void test_ufs(void)
{
	con_str("UFS", "milestone 5 probe (read-only)");
	uint32_t cap = ufs_rd(REG_CAP), hcs = ufs_rd(REG_HCS), hce = ufs_rd(REG_HCE);
	con_hex2("UFS", "ver=", ufs_rd(REG_VER), "cap=", cap);
	con_hex2("UFS", "hce=", hce, "hcs=", hcs);
	con_hex2("UFS", "is=", ufs_rd(REG_IS), "rsr=", ufs_rd(REG_UTRLRSR));
	con_hex2("UFS", "utrlba=", ufs_rd(REG_UTRLBA), "hi=", ufs_rd(REG_UTRLBAU));
	con_hex2("UFS", "dbr=", ufs_rd(REG_UTRLDBR), "nutrs=", (cap & 0x1f) + 1);

	if (!(hce & 1) || !(hcs & HCS_DP) || !(hcs & HCS_UTRLRDY)) {
		con_str("UFS", "host not enabled/ready: stop");
		return;
	}
	if (ufs_rd(REG_UTRLDBR)) {
		con_str("UFS", "requests outstanding: stop");
		return;
	}

	/* Take over the transfer request list: stop, point at ours, run. */
	ufs_wr(REG_UTRLRSR, 0);
	uintptr_t list = (uintptr_t)utrl;
	ufs_wr(REG_UTRLBA, (uint32_t)list);
	ufs_wr(REG_UTRLBAU, (uint32_t)(list >> 32));
	ufs_wr(REG_UTRLRSR, 1);
	con_hex2("UFS", "ours=", (uint32_t)list, "rsr=", ufs_rd(REG_UTRLRSR));
	/*
	 * Never ring a doorbell unless the host really uses our list: otherwise
	 * it would execute whatever ABL left in its own slot 0 (maybe a write).
	 */
	if (ufs_rd(REG_UTRLBA) != (uint32_t)list || ufs_rd(REG_UTRLBAU) != (uint32_t)(list >> 32) ||
	    !(ufs_rd(REG_UTRLRSR) & 1)) {
		con_str("UFS", "list base not taken: stop");
		return;
	}

	uint32_t ocs = 0, st = 0, rc;
	static const uint8_t tur[6] = { 0x00 };
	/* A first command may report a unit attention; retry a few times. */
	for (int i = 0; i < 3; i++) {
		rc = scsi(0, tur, sizeof(tur), SCSI_DIR_NONE, 0, 0, &ocs, &st);
		con_hex2("UFS TUR", "rc=", rc, "ocs/st=", ocs << 8 | st);
		if (!rc)
			break;
	}

	static const uint8_t cap10[10] = { 0x25 };	/* READ CAPACITY(10) */
	rc = scsi(0, cap10, sizeof(cap10), SCSI_DIR_READ, data, 8, &ocs, &st);
	con_hex2("UFS CAP", "rc=", rc, "ocs/st=", ocs << 8 | st);
	if (!rc)
		con_hex2("UFS LUN0", "lastlba=", be32(data), "blk=", be32(data + 4));

	/* READ(10) LBA 0, 2 blocks: protective MBR and the GPT header. */
	static const uint8_t rd10[10] = { 0x28, 0, 0, 0, 0, 0, 0, 0, 2, 0 };
	rc = scsi(0, rd10, sizeof(rd10), SCSI_DIR_READ, data, 2 * BLOCK, &ocs, &st);
	con_hex2("UFS READ", "rc=", rc, "ocs/st=", ocs << 8 | st);
	if (rc)
		return;
	con_hex2("UFS MBR", "sig=", data[510] | data[511] << 8, "type=", data[450]);
	const uint8_t *gpt = data + BLOCK;
	char sig[9];
	for (int i = 0; i < 8; i++)
		sig[i] = (gpt[i] >= 32 && gpt[i] < 127) ? gpt[i] : '.';
	sig[8] = 0;
	con_str("UFS GPT", sig);
	uint32_t nent = gpt[80] | gpt[81] << 8 | gpt[82] << 16, esz = gpt[84] | gpt[85] << 8;
	uint32_t elba = gpt[72] | gpt[73] << 8;
	con_hex2("UFS GPT", "entries=", nent, "lba=", elba);
	if (esz != 128 || nent > 128)
		return;
	list_partitions(elba, nent);
}

/*
 * bluejay v2 (device/google/bluejay/docs/GATE0_RESULTS.md: every v1 command timed out, rc=1).
 * Before any SCSI command: report where the shim image, stack, and the
 * DMA buffers sit relative to the live FDT memory map (S2MPU can silently
 * block UFS DMA to some DRAM, and a no-map overlap is fatal), then try
 * labeled UTRD layouts one at a time - NOP OUT first (no SCSI layer),
 * then TEST UNIT READY, then READ CAPACITY(10) - dumping the UFSHCI
 * registers, the UTRD, and the response UPIU on every timeout.
 */

#ifndef UFS_ATTEMPTS
#define UFS_ATTEMPTS 1
#endif

static uint8_t entries[128 * 128] __attribute__((aligned(4096)));

struct ufs_attempt {
	const char *tag;	/* "A1" */
	const char *desc;
	struct ufs_dma dma;
	int dword_gran;
	char label[10];		/* "UFS A1", composed at run time */
};

static unsigned uput_str(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	dst[pos] = 0;
	return pos;
}

static unsigned uput_dec(char *dst, unsigned cap, unsigned pos, unsigned v)
{
	char tmp[10];
	int n = 0;
	do {
		tmp[n++] = (char)('0' + v % 10);
		v /= 10;
		/* bounded: v /= 10 reaches 0 in <= 10 digits */
	} while (v);
	while (n > 0 && pos + 1 < cap)
		dst[pos++] = tmp[--n];
	dst[pos] = 0;
	return pos;
}

static void ufs_attempt_label(struct ufs_attempt *a)
{
	unsigned pos = uput_str(a->label, sizeof a->label, 0, "UFS ");
	uput_str(a->label, sizeof a->label, pos, a->tag);
}

static void ufs_attempt_init(struct ufs_attempt *a, const char *tag, const char *desc,
			     const struct ufs_dma *dma, int dword_gran)
{
	a->tag = tag;
	a->desc = desc;
	a->dma = *dma;
	a->dword_gran = dword_gran;
	a->label[0] = 0;
}

/* Everything wanted after a command timeout: interrupt/host state, the
 * UIC error registers, the UTRD as the host saw it, the first 32 bytes
 * of the response UPIU, and the buffer addresses of this attempt. */
static void ufs_dump_state(const char *label, const struct ufs_dma *dma)
{
	con_hex2(label, "is=", ufs_rd(REG_IS), "ie=", ufs_rd(REG_IE));
	con_hex2(label, "hcs=", ufs_rd(REG_HCS), "dbr=", ufs_rd(REG_UTRLDBR));
	con_hex2(label, "rsr=", ufs_rd(REG_UTRLRSR), "utrlba=", ufs_rd(REG_UTRLBA));
	con_hex2(label, "utrlbau=", ufs_rd(REG_UTRLBAU), "uecpa=", ufs_rd(REG_UECPA));
	con_hex2(label, "uecdl=", ufs_rd(REG_UECDL), "uecn=", ufs_rd(REG_UECN));
	con_hex2(label, "uect=", ufs_rd(REG_UECT), "uecdme=", ufs_rd(REG_UECDME));
	const struct utrd *t = &dma->utrl[0];
	con_hex2(label, "dw0=", t->dw0, "dw1=", t->dw1);
	con_hex2(label, "dw2=", t->dw2, "dw3=", t->dw3);
	con_hex2(label, "dw4=", t->ucdba, "dw5=", t->ucdbau);
	con_hex2(label, "dw6=", t->resp, "dw7=", t->prdt);
	con_hex2(label, "ocs=", t->dw2 & 0xff, "respt=", dma->ucd->resp[0]);
	for (unsigned off = 0; off < 32; off += 8)
		con_hex2(label, "", be32(dma->ucd->resp + off), "", be32(dma->ucd->resp + off + 4));
	con_hex2(label, "utrl lo=", (uint32_t)(uintptr_t)dma->utrl, "hi=",
		 (uint32_t)((uintptr_t)dma->utrl >> 32));
	con_hex2(label, "ucd lo=", (uint32_t)(uintptr_t)dma->ucd, "hi=",
		 (uint32_t)((uintptr_t)dma->ucd >> 32));
	con_hex2(label, "data lo=", (uint32_t)(uintptr_t)dma->data, "hi=",
		 (uint32_t)((uintptr_t)dma->data >> 32));
}

/* Clear slots 0 and 1 (UTRLCLR, write 1 on gs101) and verify UTRLDBR==0. */
static int ufs_slot_clear(const char *label)
{
	ufs_wr(REG_UTRLCLR, 3);
	uint64_t start = ticks();
	/* bounded: ticks() - start > counter_freq() (1 s) returns 0 below */
	while (ufs_rd(REG_UTRLDBR) & 3) {
		if (ticks() - start > counter_freq()) {
			con_hex2(label, "dbr stuck=", ufs_rd(REG_UTRLDBR), "is=", ufs_rd(REG_IS));
			return 0;
		}
	}
	return 1;
}

/* Stop the list, point it at this attempt's UTRL, restart, verify the
 * base was accepted, clear any doorbell bits that appeared, and clear
 * pending IS bits. Never ring a doorbell onto a list that is not ours. */
static int ufs_takeover_list(const struct ufs_dma *dma, const char *label)
{
	ufs_wr(REG_UTRLRSR, 0);
	uintptr_t list = (uintptr_t)dma->utrl;
	ufs_wr(REG_UTRLBA, (uint32_t)list);
	ufs_wr(REG_UTRLBAU, (uint32_t)(list >> 32));
	__asm__ volatile("dsb sy" ::: "memory");
	ufs_wr(REG_UTRLRSR, 1);
	__asm__ volatile("dsb sy" ::: "memory");
	if (ufs_rd(REG_UTRLBA) != (uint32_t)list || ufs_rd(REG_UTRLBAU) != (uint32_t)(list >> 32) ||
	    !(ufs_rd(REG_UTRLRSR) & 1)) {
		con_str(label, "UTRL base not accepted");
		return 0;
	}
	uint32_t stale = ufs_rd(REG_UTRLDBR);
	if (stale) {
		ufs_wr(REG_UTRLCLR, stale);	/* BROKEN_REQ_LIST_CLR: write 1 */
		uint64_t start = ticks();
		/* bounded: ticks() - start > counter_freq() (1 s) returns 0 below */
		while (ufs_rd(REG_UTRLDBR)) {
			if (ticks() - start > counter_freq()) {
				con_str(label, "stale doorbell stuck: stop");
				return 0;
			}
		}
		con_hex2(label, "cleared stale=", stale, "left=", ufs_rd(REG_UTRLDBR));
	}
	uint32_t is = ufs_rd(REG_IS);
	if (is)
		ufs_wr(REG_IS, is);	/* write-1-to-clear */
	return 1;
}

/* One labeled attempt: NOP OUT, then TEST UNIT READY (unit-attention
 * retries), then READ CAPACITY(10) on LUN 0. Returns 1 only if a command
 * completed (device path alive); every timeout dumps the host state. */
static int ufs_run_attempt(struct ufs_attempt *a)
{
	ufs_attempt_label(a);
	con_str("UFS TRY", a->desc);
	if (!ufs_takeover_list(&a->dma, a->label))
		return 0;
	uint32_t ocs = 0, st = 0, rt = 0;
	uint32_t rc = upiu_submit(&a->dma, UPIU_NOP_OUT, 0, 0, 0, SCSI_DIR_NONE, 0, 0,
				  a->dword_gran, &ocs, &st, &rt);
	con_hex2(a->label, "nop rc=", rc, "ocs/rt=", ocs << 8 | rt);
	if (rc == 1) {
		ufs_dump_state(a->label, &a->dma);
		ufs_slot_clear(a->label);
		return 0;
	}
	static const uint8_t tur[6] = { 0x00 };	/* TEST UNIT READY */
	for (int i = 0; i < 3; i++) {
		rc = upiu_submit(&a->dma, UPIU_COMMAND, 0, tur, sizeof tur, SCSI_DIR_NONE, 0, 0,
				 a->dword_gran, &ocs, &st, &rt);
		con_hex2(a->label, "tur rc=", rc, "ocs/st=", ocs << 8 | st);
		if (rc == 1) {
			ufs_dump_state(a->label, &a->dma);
			ufs_slot_clear(a->label);
			return 0;
		}
		if (rc == 0)
			break;
	}
	static const uint8_t cap10[10] = { 0x25 };	/* READ CAPACITY(10) */
	rc = upiu_submit(&a->dma, UPIU_COMMAND, 0, cap10, sizeof cap10, SCSI_DIR_READ,
			 a->dma.data, 8, a->dword_gran, &ocs, &st, &rt);
	con_hex2(a->label, "cap rc=", rc, "ocs/st=", ocs << 8 | st);
	if (rc == 1) {
		ufs_dump_state(a->label, &a->dma);
		ufs_slot_clear(a->label);
		return 0;
	}
	if (rc == 0)
		con_hex2(a->label, "lun0 last=", be32(a->dma.data), "blk=", be32(a->dma.data + 4));
	return 1;
}

/* The winning attempt runs the full v1 sequence: READ CAPACITY on LUN
 * 0..3, READ(10) of LBA 0 (protective MBR + GPT header), and the GPT
 * entry list. All read-only (docs/SAFETY.md). */
/* Read-only proof of the userdata LBA range: CRC32 (zlib) of each 256 KiB of the first 1 MiB,
 * bounds-checked against the GPT entry. Compare with `dd` as root in Android (tools/check-udump.sh). */
#define UDUMP_BLOCKS 256
#define UDUMP_CHUNK 64
#ifdef UDUMP
/* ---- userdata dump support (build flag --udump) ----
 * All read-only. The staging buffer lives HERE, with the other
 * DMA-facing statics, so shim/udump.c carries no static state at all
 * and its object audit stays a pure stack/argument store proof; 4 KiB
 * aligned because every PRD entry the reader builds lands 4 KiB
 * aligned inside it. */
uint8_t ufs_udump_stage[UFS_UDUMP_STAGE_LEN] __attribute__((aligned(4096)));
static struct ufs_dma udump_win_dma;
static int udump_win_gran;
static uint64_t udump_gpt_first, udump_gpt_last;
static int udump_gpt_found;

int ufs_userdata_found(uint64_t *first, uint64_t *last)
{
	if (!udump_gpt_found || !first || !last)
		return 0;
	*first = udump_gpt_first;
	*last = udump_gpt_last;
	return 1;
}

/* Read nblocks (<= 2: the command descriptor's PRDT has two 4 KiB
 * entries) 4 KiB blocks at ABSOLUTE LBA lba into buf. Bounds-checked
 * twice (udump_run checks too): only inside the GPT 'userdata' match,
 * and only below the 32-bit LBA limit of read_blocks_dma. */
uint32_t ufs_read_userdata(uint64_t lba, uint32_t nblocks, void *buf)
{
	if (!udump_gpt_found || nblocks == 0 || nblocks > 2)
		return 1;
	if (lba > 0xFFFFFFFFULL || lba < udump_gpt_first)
		return 1;
	if (lba + nblocks - 1 > udump_gpt_last)	/* safe: lba <= 2^32-1 */
		return 1;
	return read_blocks_dma(&udump_win_dma, udump_win_gran, 0,
			       (uint32_t)lba, nblocks, buf);
}
#endif
static void userdata_crc(struct ufs_attempt *a, uint64_t first, uint64_t last)
{
	if (first + UDUMP_BLOCKS - 1 > last) {
		con_str("UDUMP", "range too small");
		return;
	}
	con_hex2("UDUMP", "first=", (uint32_t)first, "last=", (uint32_t)last);
	for (uint32_t c = 0; c < UDUMP_BLOCKS; c += UDUMP_CHUNK) {
		uint32_t crc = ~0u;
		for (uint32_t b = c; b < c + UDUMP_CHUNK; b += 2) {
			if (read_blocks_dma(&a->dma, a->dword_gran, 0, (uint32_t)first + b, 2, a->dma.data)) {
				con_hex("UDUMP read failed at blk", b);
				return;
			}
			for (unsigned i = 0; i < 2 * BLOCK; i++) {
				crc ^= a->dma.data[i];
				for (int k = 0; k < 8; k++)
					crc = crc >> 1 ^ (0xEDB88320u & -(crc & 1));
			}
		}
		con_hex2("UDUMP", "blk=", c, "crc=", ~crc);
	}
}

static void ufs_full_sequence(struct ufs_attempt *a)
{
	con_str("UFS WIN", a->desc);
	con_hex2("WIN bufs", "utrl lo=", (uint32_t)(uintptr_t)a->dma.utrl, "hi=",
		 (uint32_t)((uintptr_t)a->dma.utrl >> 32));
	con_hex2("WIN bufs", "ucd lo=", (uint32_t)(uintptr_t)a->dma.ucd, "hi=",
		 (uint32_t)((uintptr_t)a->dma.ucd >> 32));
	uint32_t ocs, st, rt;
	static const uint8_t cap10[10] = { 0x25 };
	for (uint8_t lun = 0; lun < 4; lun++) {
		uint32_t rc = 4;
		for (int try = 0; try < 3 && rc == 4; try++)	/* first command per LU: UNIT ATTENTION */
			rc = upiu_submit(&a->dma, UPIU_COMMAND, lun, cap10, sizeof cap10,
					 SCSI_DIR_READ, a->dma.data, 8, a->dword_gran, &ocs, &st, &rt);
		con_hex2("UFS LUN", "id=", lun, "rc=", rc);
		if (rc == 1) {
			ufs_dump_state(a->label, &a->dma);
			ufs_slot_clear(a->label);
			return;
		}
		if (rc)
			continue;
		con_hex2("CAPACITY", "last=", be32(a->dma.data), "blk=", be32(a->dma.data + 4));
	}
	uint32_t rc = read_blocks_dma(&a->dma, a->dword_gran, 0, 0, 2, a->dma.data);
	con_hex2("UFS READ", "rc=", rc, "blocks=", 2);
	if (rc == 1) {
		ufs_dump_state(a->label, &a->dma);
		ufs_slot_clear(a->label);
		return;
	}
	if (rc)
		return;
	uint8_t *d = a->dma.data;
	con_hex2("UFS MBR", "sig=", d[510] | d[511] << 8, "type=", d[450]);
	const uint8_t *gpt = d + BLOCK;
	con_hex2("UFS blk1", "w0=", be32(gpt), "w1=", be32(gpt + 4));
	con_hex2("UFS blk0+512", "w0=", be32(d + 512), "w1=", be32(d + 516));
	con_hex2("UFS blk0+446", "w0=", be32(d + 446), "w1=", be32(d + 450));
	char sig[9];
	for (int i = 0; i < 8; i++)
		sig[i] = (gpt[i] >= 32 && gpt[i] < 127) ? gpt[i] : '.';
	sig[8] = 0;
	con_str("UFS GPT", sig);
	uint32_t nent = gpt[80] | gpt[81] << 8 | gpt[82] << 16, esz = gpt[84] | gpt[85] << 8;
	uint32_t elba = gpt[72] | gpt[73] << 8;
	con_hex2("UFS GPT", "entries=", nent, "lba=", elba);
	if (esz != 128 || !nent || nent > 128)
		return;
	uint32_t blocks = (nent * 128 + BLOCK - 1) / BLOCK;
	for (uint32_t b = 0; b < blocks; b++) {
		if (read_blocks_dma(&a->dma, a->dword_gran, 0, elba + b, 1, a->dma.entries + (uintptr_t)b * BLOCK) == 1) {
			ufs_dump_state(a->label, &a->dma);
			ufs_slot_clear(a->label);
			return;
		}
	}
	uint64_t ud_first = 0, ud_last = 0;
	for (uint32_t i = 0; i < nent; i++) {
		const uint8_t *e = a->dma.entries + (uintptr_t)i * 128;
		uint64_t first = le64(e + 32), last = le64(e + 40);
		if (!first)
			continue;
		char name[17];
		for (int c = 0; c < 16; c++) {
			uint8_t ch = e[56 + 2 * c];
			name[c] = ch >= 32 && ch < 127 ? ch : (ch ? '?' : 0);
		}
		name[16] = 0;
		if (!ud_first && name[0] == 'u' && name[1] == 's' && name[2] == 'e' && name[3] == 'r' &&
		    name[4] == 'd' && name[5] == 'a' && name[6] == 't' && name[7] == 'a' && !name[8]) {
			ud_first = first;
			ud_last = last;
		}
		unsigned nl = 0;
		while (nl < 16 && name[nl])
			nl++;
		/* Long names get their own line so the LBA pair stays on one
		 * screen line (44 columns at the current font scale). */
		if (nl <= 10)
			con_hex2(name, "first=", (uint32_t)first, "last=", (uint32_t)last);
		else {
			con_str("GPT name", name);
			con_hex2("GPT reg", "first=", (uint32_t)first, "last=", (uint32_t)last);
		}
	}
	if (ud_first)
		userdata_crc(a, ud_first, ud_last);
#ifdef UDUMP
	/* Keep the winning attempt's DMA layout and the GPT match
	 * so the dump reads through exactly the layout that worked. */
	udump_win_dma = a->dma;
	udump_win_gran = a->dword_gran;
	/* Review M4: only a real match counts. ud_first == 0 means no 'userdata'
	 * entry was found (LBA 0 is the protective MBR, never part of it). */
	udump_gpt_first = ud_first;
	udump_gpt_last = ud_last;
	udump_gpt_found = ud_first != 0 && ud_last >= ud_first;
#endif
}

/* ---- placement analysis against the live FDT memory map ---- */

static int range_in_bank(uint64_t addr, uint64_t size, const struct fdt_memmap *mm, unsigned *bank_out)
{
	for (unsigned i = 0; i < mm->n_banks; i++) {
		uint64_t b = mm->banks[i].base, e = b + mm->banks[i].size;
		if (e >= b && addr >= b && addr + size <= e && addr + size >= addr) {
			if (bank_out)
				*bank_out = i;
			return 1;
		}
	}
	return 0;
}

static int reserved_hit(uint64_t addr, uint64_t size, const struct fdt_memmap *mm, unsigned *idx_out)
{
	for (unsigned i = 0; i < mm->n_reserved; i++) {
		uint64_t b = mm->reserved[i].base, e = b + mm->reserved[i].size;
		if (e > b && addr < e && b < addr + size) {
			if (idx_out)
				*idx_out = i;
			return 1;
		}
	}
	return 0;
}

static void report_placement(const char *what, uint64_t addr, uint64_t size,
			     const struct fdt_memmap *mm)
{
	char flg[48];
	unsigned pos = 0, bank = 0, ridx = 0;
	pos = uput_str(flg, sizeof flg, pos, "bank ");
	if (range_in_bank(addr, size, mm, &bank))
		pos = uput_dec(flg, sizeof flg, pos, bank);
	else
		pos = uput_str(flg, sizeof flg, pos, "?");
	if (reserved_hit(addr, size, mm, &ridx)) {
		pos = uput_str(flg, sizeof flg, pos, " rsv ");
		pos = uput_dec(flg, sizeof flg, pos, ridx);
		if (mm->reserved[ridx].no_map)
			pos = uput_str(flg, sizeof flg, pos, " NOMAP");
	}
	if (mm->n_reserved_dropped)
		pos = uput_str(flg, sizeof flg, pos, " RSVDROP");
	if (addr < 0x100000000ULL)
		pos = uput_str(flg, sizeof flg, pos, " below4G");
	flg[pos] = 0;
	con_hex2(what, "lo=", (uint32_t)addr, "hi=", (uint32_t)(addr >> 32));
	con_str(what, flg);
}

void test_ufs_bluejay(uintptr_t dtb)
{
	con_str("UFS", "GS101 read-only probe v2");
	con_str("UFS note", "timeout dumps IS/UTRD/UPIU");
	uint32_t cap = ufs_rd(REG_CAP), hcs = ufs_rd(REG_HCS), hce = ufs_rd(REG_HCE);
	con_hex2("UFS", "hce=", hce, "hcs=", hcs);
	con_hex2("UFS", "cap=", cap, "ver=", ufs_rd(REG_VER));
	con_hex2("UFS", "is=", ufs_rd(REG_IS), "ie=", ufs_rd(REG_IE));
	con_hex2("UFS", "utrlba=", ufs_rd(REG_UTRLBA), "hi=", ufs_rd(REG_UTRLBAU));
	con_hex2("UFS", "rsr=", ufs_rd(REG_UTRLRSR), "dbr=", ufs_rd(REG_UTRLDBR));
	/* Read-only survey first (docs/research/07): what did ABL's shutdown leave? */
	ufs_vendor_diag();
#if !UFS_ATTEMPTS
	/* -DUFS_ATTEMPTS=0 reduces the probe to the read-only survey. */
	return;
#endif
	if (!(hce & 1) || !(hcs & HCS_DP) || !(hcs & HCS_UTRLRDY)) {
		con_str("UFS", "device absent or UTRL not ready");
		return;
	}
	/* ABL shut the device link down: cold re-init (shim/ufsinit.c) before touching the list. */
	if (ufs_cold_init()) {
		con_str("UFS", "cold init failed");
		return;
	}
	/* Same takeover guard as v1: a doorbell left set by ABL would make
	 * the host fetch our zeroed slot 0 as soon as the list restarts. */
	if (ufs_rd(REG_UTRLDBR)) {
		con_str("UFS", "requests outstanding: stop");
		return;
	}

		struct fdt_memmap mm;
	int have_map = fdt_collect_memmap(dtb, &mm);
	if (!have_map)
		con_str("UFS MAP", "malformed: no placement checks");
	else
		con_hex2("UFS MAP", "banks=", mm.n_banks, "rsv=", mm.n_reserved);

	/* Linker-script symbols: _head = start.S:4 label (ENTRY, shim.ld:1), _stack_top = shim.ld:15, _end = shim.ld:19; names fixed by start.S/shim.ld/build.sh. */
	extern char _head[], _end[], _stack_top[]; // NOLINT(bugprone-reserved-identifier)
	uintptr_t sp = (uintptr_t)&cap;	/* somewhere inside the primary stack */
	report_placement("IMG", (uintptr_t)_head, (uintptr_t)_end - (uintptr_t)_head, &mm);
	report_placement("STK", sp, (uintptr_t)_stack_top - sp, &mm);
	report_placement("UTRL", (uintptr_t)utrl, sizeof utrl, &mm);
	report_placement("UCD", (uintptr_t)&ucd, sizeof ucd, &mm);
	report_placement("DATA", (uintptr_t)data, sizeof data, &mm);

	/* Buffer sets are built with runtime stores: absolute pointers must
	 * not sit in .data/.rodata (build.sh rejects ABS64 relocations). */
	struct ufs_dma own_dma;
	own_dma.utrl = utrl;
	own_dma.ucd = &ucd;
	own_dma.data = data;
	own_dma.entries = entries;
#ifdef UFS_DMA_HI_BASE
	/* Experiment (XNU's DMA lives in the 0x88.. bank): same buffers at a fixed high address. */
	own_dma.utrl = (struct utrd *)(uintptr_t)(UFS_DMA_HI_BASE);
	own_dma.ucd = (struct ucd *)(uintptr_t)(UFS_DMA_HI_BASE + 0x1000);
	own_dma.data = (uint8_t *)(uintptr_t)(UFS_DMA_HI_BASE + 0x4000);
	own_dma.entries = (uint8_t *)(uintptr_t)(UFS_DMA_HI_BASE + 0x10000);
#endif
	struct ufs_attempt attempts[1];
	unsigned n = 0;
	/* The only layout that works on hardware: our own buffers, byte-granular PRDT. */
	ufs_attempt_init(&attempts[n++], "A1", "A1 byte-gran own/v1", &own_dma, 0);
	for (unsigned i = 0; i < n; i++) {
		if (ufs_run_attempt(&attempts[i])) {
			ufs_full_sequence(&attempts[i]);
			return;
		}
	}
	con_str("UFS", "no attempt completed; see dumps");
}
