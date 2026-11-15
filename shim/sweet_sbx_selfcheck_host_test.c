/*
 * Host test of the Sandbox op-2 self-check (xnu/patches/0329-sweet-sandbox-selfcheck.patch) with stubbed kernel KPIs.
 * The code under test is extracted from the patch, so there is no copy to drift:
 *   sed -n '/^+#define SWEET_SBX_OP_CHECK/,/^[ +]sweet_sandbox_syscall/p' xnu/patches/0329-sweet-sandbox-selfcheck.patch \
 *     | head -n -2 | sed 's/^+//' > $O/sbx_snippet.h
 *   gcc -O1 -Wall -Wextra -Werror -DSBX_SNIPPET='"sbx_snippet.h"' -I$O -o $O/sweet_sbx_selfcheck_host_test shim/sweet_sbx_selfcheck_host_test.c
 * Run by shim/build.sh (host-tests block, after wlan_g2_host_test).
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint64_t user_addr_t;
struct proc { int pid; const char *name; const char *key; };
typedef struct proc *proc_t;
typedef struct proc *task_t;
static task_t kernel_task = (task_t)0x1;

static uint64_t g_scratch;          /* the "user" scratch word */
static uint64_t g_blk[6];
static int g_copyin_err;
static struct proc g_cur = { 10, "wifip2pd", "com.apple.private.sandbox.profile:embedded" };
static struct proc g_other = { 20, "configd", NULL };
static char g_log[4096];

#define printf(...) (void)snprintf(g_log + strlen(g_log), sizeof(g_log) - strlen(g_log), __VA_ARGS__)
static int copyin(user_addr_t a, void *d, size_t n) { (void)a; if (g_copyin_err) { return g_copyin_err; } memcpy(d, g_blk, n); return 0; }
static int copyout(const void *s, user_addr_t a, size_t n) { if (a == 0) { return 14; } memcpy((void *)(uintptr_t)a, s, n); return 0; }
static int proc_pid(proc_t p) { return p->pid; }
static task_t proc_task(proc_t p) { return p; }
static void proc_name(int pid, char *b, int n) { snprintf(b, (size_t)n, "%s", pid == g_cur.pid ? g_cur.name : g_other.name); }
static proc_t proc_find(int pid) { return pid == 20 ? &g_other : (pid == 10 ? &g_cur : NULL); }
static void proc_rele(proc_t p) { (void)p; }
static char *IOTaskGetEntitlement(task_t t, const char *key) { return (t->key && !strcmp(t->key, key)) ? strdup("wifip2pd") : NULL; }
#define kfree_data(p, n) ((void)(n), free(p))
#define strlcpy(d, s, n) ((void)snprintf((d), (n), "%s", (s)))

#include SBX_SNIPPET

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fputs(g_log, stderr); fails++; } } while (0)
#undef printf

int main(void)
{
	/* self (pid 0 and own pid) with the :embedded entitlement: answered sandboxed once per name */
	g_blk[0] = (uint64_t)(uintptr_t)&g_scratch; g_blk[1] = 0; g_blk[2] = 0;
	CHECK(sweet_sbx_self_check(&g_cur, 0) == 0 && g_scratch == 1);
	g_scratch = 0; g_blk[1] = 10;
	CHECK(sweet_sbx_self_check(&g_cur, 0) == 0 && g_scratch == 1);
	CHECK(strstr(g_log, "wifip2pd self-check answered sandboxed (entitlement com.apple.private.sandbox.profile:embedded)") != NULL);
	/* non-NULL operation: untouched */
	g_scratch = 0; g_blk[2] = 0x1234;
	CHECK(sweet_sbx_self_check(&g_cur, 0) == 0 && g_scratch == 0);
	g_blk[2] = 0;
	/* another pid without entitlement: nothing written, GAP once */
	g_blk[1] = 20;
	CHECK(sweet_sbx_self_check(&g_cur, 0) == 0 && g_scratch == 0);
	CHECK(sweet_sbx_self_check(&g_cur, 0) == 0 && g_scratch == 0);
	CHECK(strstr(g_log, "SWEET Sandbox GAP: configd self-check answered NOT sandboxed (no profile entitlement)") != NULL);
	CHECK(strstr(strstr(g_log, "NOT sandboxed") + 1, "NOT sandboxed") == NULL);
	/* another pid with the plain key */
	g_other.key = "com.apple.private.sandbox.profile"; g_blk[1] = 20;
	CHECK(sweet_sbx_self_check(&g_cur, 0) == 0 && g_scratch == 1);
	CHECK(strstr(g_log, "configd self-check answered sandboxed (entitlement com.apple.private.sandbox.profile)") != NULL);
	/* missing pid: not sandboxed, no write */
	g_scratch = 0; g_blk[1] = 999;
	CHECK(sweet_sbx_self_check(&g_cur, 0) == 0 && g_scratch == 0);
	/* copyin error is returned and logged once */
	g_copyin_err = 14;
	CHECK(sweet_sbx_self_check(&g_cur, 0) == 14);
	CHECK(sweet_sbx_self_check(&g_cur, 0) == 14);
	CHECK(strstr(g_log, "copyin failed (14)") != NULL && strstr(strstr(g_log, "copyin failed") + 1, "copyin failed") == NULL);
	g_copyin_err = 0;
	/* bounded name table: 40 distinct names never overrun, one table-full line */
	for (int i = 0; i < 40; i++) {
		char nm[16]; snprintf(nm, sizeof nm, "p%d", i);
		g_other.name = strdup(nm); g_other.key = NULL; g_blk[1] = 20;
		CHECK(sweet_sbx_self_check(&g_cur, 0) == 0);
	}
	CHECK(strstr(g_log, "table full") != NULL && strstr(strstr(g_log, "table full") + 1, "table full") == NULL);
	CHECK(sweet_sbx_no_count <= SWEET_SBX_SEEN_MAX);
	puts(fails ? "FAIL" : "PASS sweet_sbx_selfcheck_host_test");
	return fails != 0;
}
