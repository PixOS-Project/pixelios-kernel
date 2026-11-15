/* acpm_dvfs_mif_top (T-5030): included by acpm.c after dvfs_set(), which it calls; empty unless MIF_CLK. */
#ifdef MIF_CLK
/* --mif-clk: FREQ_REQ for MIF (dom 0) at ONE rate only: the table TOP row (ACPM_DVFS_MIF_KHZ = soc.json
 * MIF ceiling = row 0), or with --mif-clk-level the one row MIF_CLK_LEVEL_KHZ (ACPM_DVFS_MIF_REQ_KHZ, acpm_gpudom.h); INT, every CPU/GPU domain and every other rate are refused before
 * anything is sent. A MIF already at the requested rate is refused with ACPM_E_ON (nothing to ask for):
 * the pre-read then IS the witness. */
int acpm_dvfs_mif_top(const struct acpm_cfg *c, struct acpm_trace *t)
{
	/* style-allow: sized-types int is the status type of every acpm_* call */
	/* style-allow: assert audited client, refusals are logged */
	return dvfs_set(c, ACPM_DVFS_MIF_IDX, ACPM_DVFS_MIF_REQ_KHZ, t, 2);
}
#endif
