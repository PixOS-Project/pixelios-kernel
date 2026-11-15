/*
 * pixps: list processes on the phone for the hot-reload loop (tools/hot ps / restart).
 *
 *   pixps            print every process: "PID PPID UID COMM"
 *   pixps NAME       print only processes whose p_comm equals NAME exactly
 *                    (p_comm is cut to MAXCOMLEN = 16 chars by the kernel, so give
 *                    at most the first 16 chars of a long name)
 *
 * devd (tools/devd.c) RUNs programs without a shell, and only below /usr/local/ or
 * /private/var/dev/, so there is no ps/pgrep on the root to find the pid that
 * `devsh signal` needs. This reads sysctl {CTL_KERN, KERN_PROC, KERN_PROC_ALL}
 * (the interface ps(1) uses, <sys/sysctl.h> struct kinfo_proc).
 *
 * Exit: 0 = at least one line printed; 1 = NAME given and no process has it
 * (a line "pixps: no process named NAME" on stderr); 2 = sysctl error (errno text
 * on stderr). Every exit except 0 prints why (AGENTS rule 8).
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/types.h>

int main(int argc, char **argv)
{
    const char *want = argc > 1 ? argv[1] : NULL;
    int mib[4] = { CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0 };
    size_t len = 0;
    struct kinfo_proc *kp = NULL;

    if (argc > 2) {
        fprintf(stderr, "pixps: usage: pixps [NAME]\n");
        return 2;
    }
    /* the table can grow between the size query and the read: retry a few times with headroom */
    for (int tries = 0; tries < 5; tries++) {
        if (sysctl(mib, 4, NULL, &len, NULL, 0) != 0) {
            fprintf(stderr, "pixps: sysctl size: %s\n", strerror(errno));
            return 2;
        }
        len += len / 4 + 16 * sizeof(struct kinfo_proc);
        free(kp);
        kp = malloc(len);
        if (!kp) {
            fprintf(stderr, "pixps: malloc %zu failed\n", len);
            return 2;
        }
        if (sysctl(mib, 4, kp, &len, NULL, 0) == 0)
            break;
        if (errno != ENOMEM || tries == 4) {
            fprintf(stderr, "pixps: sysctl read: %s\n", strerror(errno));
            free(kp);
            return 2;
        }
    }
    size_t n = len / sizeof(struct kinfo_proc);
    int shown = 0;
    for (size_t i = 0; i < n; i++) {
        const char *comm = kp[i].kp_proc.p_comm;
        if (want && strncmp(comm, want, MAXCOMLEN) != 0)
            continue;
        printf("%d %d %u %s\n", (int)kp[i].kp_proc.p_pid, (int)kp[i].kp_eproc.e_ppid,
               (unsigned)kp[i].kp_eproc.e_ucred.cr_uid, comm);
        shown++;
    }
    free(kp);
    if (want && shown == 0) {
        fprintf(stderr, "pixps: no process named %s\n", want);
        return 1;
    }
    if (shown == 0) {
        fprintf(stderr, "pixps: sysctl returned an empty process table\n");
        return 2;
    }
    return 0;
}
