"""The self-test of tools/change-class: fixtures + mutants (T-8fe9 M2, RULES R31; Opus r1-r6 cases). A file of the classifier: a branch that edits it keeps the full ceremony."""


def _fx(path, added, ctx=("int f(void)", "{")):
    """A one-hunk diff that adds ADDED lines to PATH (a .patch path gets them as inner + lines of a new patch)."""
    if path.endswith(".patch"):
        body = ["+desc line", "+", "+diff --git a/k.c b/k.c", "+--- a/k.c", "++++ b/k.c", "+@@ -1,2 +1,9 @@"] + ["+ " + c for c in ctx] + ["++" + a for a in added] + ["+ }"]
        return "diff --git a/%s b/%s\nnew file mode 100644\n--- /dev/null\n+++ b/%s\n@@ -0,0 +1,%d @@\n%s\n" % (path, path, path, len(body), "\n".join(body))
    return "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n@@ -1,3 +1,9 @@\n%s\n%s\n }\n" % (path, path, path, path, "\n".join(" " + c for c in ctx), "\n".join("+" + a for a in added))


def _c(*lines):
    """A shim/x.c diff from raw diff body LINES (each starts with its sign)."""
    return "diff --git a/shim/x.c b/shim/x.c\n--- a/shim/x.c\n+++ b/shim/x.c\n@@ -1,5 +1,6 @@\n" + "\n".join(lines) + "\n"


def _p(*lines):
    """An edit of an existing xnu patch from raw outer diff body LINES."""
    return "diff --git a/xnu/patches/0300-x.patch b/xnu/patches/0300-x.patch\n--- a/xnu/patches/0300-x.patch\n+++ b/xnu/patches/0300-x.patch\n" \
           "@@ -1,8 +1,8 @@\n" + "\n".join(lines) + "\n"


WITNESS = ['#if defined(SWEET)', '\t/*', '\t * witness only; no state change.', '\t */', '\tif (p == current_proc() && (rv >> 8) != 0) {',
           '\t\textern void sweet_crash_log(const char *, int, int, uint64_t, uint64_t);', '\t\tuint32_t v = proc_getpid(p);',
           '\t\tprintf("SWEET X: %s[%d] v=0x%x\\n",', '\t\t    p->p_name, proc_getpid(p), v);', '\t\tsweet_crash_log(p->p_name, 0, 0, (uint64_t)rv, 0);', '\t}', '#endif']
MUTANTS = [("store", "\t\twrite32(base + 0x10, 1);"), ("assign", "\t\tg_state = 1;"), ("deref", "\t\t*(volatile uint32_t *)0x1000 = 1;"),
           ("call in log args", '\t\tprintf("%d", poke(base));'), ("increment in log", '\t\tprintf("%d", n++);'), ("asm", '\t\tasm volatile("dsb sy");'),
           ("volatile decl", "\t\tvolatile uint32_t *r = (void *)base;"), ("decl from writer", "\t\tuint32_t v2 = clm_send(base);"),
           ("if with call", "\t\tif (kick(base)) {"), ("open brace wraps code", "\t\tif (x) {"), ("close brace", "\t}"), ("#if 0", "#if 0"),
           ("#else", "#else"), ("smc", "\t\tsmc(0x82000000);"), ("memcpy", "\t\tprintf(\"%d\", memcpy(a, b, 4) != 0);"),
           # Opus r1 (docs/reviews/T-8fe9-change-class-r1.md) findings, one mutant each:
           ("f1 store after a comment", "\t\t/* c */ write32(a, 1);"), ("f1 store before a comment", "\t\twrite32(a,1); /* x */"),
           ("f2 code after comment close", "\t\t/* a"), ("f3 star line outside comment", "\t\t*(uint32_t *)REG = 1,"),
           ("f4 fn pointer call", '\t\tprintf("%d", (*fp)(a));'), ("f4 cast call", '\t\tprintf("%d", (int)(fp)(a));'),
           ("f4 indexed call", '\t\tprintf("%d", tbl[1](a));'),
           ("f5 try_read32 out-arg", '\t\tprintf("%d", try_read32(base, (uint32_t *)REG_CTRL));'),
           ("f5 try_read32 out-arg global", '\t\tprintf("%d", try_read32(base, &g_v));'), ("f6 bare macro", '\t\tprintf("%d", KICK);'),
           ("stray comment close", "\t\t end */"), ("unterminated literal", '\t\tprintf("x);'), ("quote in comment hides code", "\t\t/* it's */ g = 1; /* 'x' */")]


def _self_test_r2(want):
    """Opus r2 cases (docs/reviews/T-c05b-change-class-r2.md, fixtures tools/testdata/change-class/r2)."""
    want("r2 f1 log then return", _c(" {", '+\tprintf("x"); return (0);', " \twrite32(a, 1);", " }"), "hw-write")
    want("r2 f1 regate", _c(" \tif (on) {", '+\tprintf("x"); } if (never) { printf("z");', " \twrite32(a, 1);", " \t}"), "hw-write")
    want("r2 f1 cond regate", _c(" \tif (on) {", "+\tif (x) { } } if (never) {", " \twrite32(a, 1);", " \t}"), "hw-write")
    want("r2 f1 decl return", _c(" {", "+\tuint32_t v = 0; return v;", " \twrite32(a, 1);", " }"), "hw-write")
    want("r2 f1 log while", _c(" {", '+\tprintf("x"); while (1) printf("y");', " }"), "hw-write")
    want("r2 f1 flow in a log continuation", _c(" {", '+\tprintf("%d",', "+\t    a); return (0", "+\t);", " }"), "hw-write")
    want("r2 f2 braces across #ifdef", _c(" \tif (on) {", "+#ifdef NEVER", "+\t}", "+#endif", " \twrite32(a, 1);", "+#ifdef NEVER", "+\tif (x) {", "+#endif", " \t}"), "hw-write")
    want("r2 f2 open brace only inside #ifdef", _c(" \tif (on) {", "+#ifdef NEVER", "+\tif (x) {", "+#endif", "+\t}", " \twrite32(a, 1);", " \t}"), "hw-write")
    want("r2 f14 prev macro line ends in \\", _c(" \tfoo(); \\", '+\tprintf("x");', " \tb();"), "hw-write")
    want("r2 f14 prev #define line ends in \\", _c(" #define K() \\", '+\tprintf("x");', " \tb()"), "hw-write")
    want("r2 f3 if block captures else", _c(" \tif (a) foo();", "+\tif (dbg) {", '+\t\tprintf("x");', "+\t}", " \telse power_off();"), "hw-write")
    want("r2 f3 if block at hunk end", "diff --git a/shim/x.c b/shim/x.c\n@@ -1,2 +1,4 @@\n {\n+\tif (dbg) {\n+\t}\n", "hw-write")
    want("r2 f4 sweet_iolog out pointer", _c(" {", "+\tsweet_iolog((char *)base, 64);", " }"), "hw-write")
    want("r2 f5 member call", _c(" {", '+\tprintf("%d", obj->printf(a));', " }"), "hw-write")
    want("r2 f6 new patch inner binary", _fx("xnu/patches/0300-sweet-x.patch", []).replace("+diff --git a/k.c b/k.c", "+diff --git a/k.c b/k.c\n+GIT binary patch"), "hw-write")
    want("r2 f6 new patch inner rename", _fx("xnu/patches/0300-sweet-x.patch", []).replace("+diff --git a/k.c b/k.c", "+diff --git a/k.c b/k.c\n+rename from k.c"), "hw-write")
    want("r2 f6 new patch inner Makefile", _fx("xnu/patches/0300-sweet-x.patch", []).replace("+diff --git a/k.c b/k.c", "+diff --git a/Makefile b/Makefile"), "hw-write")
    want("r2 f6 new patch odd inner line", _fx("xnu/patches/0300-sweet-x.patch", ["\tprintf(\"x\");"]) .replace("++\tprintf", "+x\tprintf"), "hw-write")
    want("r2 f7 shadowing declaration", _c(" \tif (x) {", "+\t\tint err = 0;", " \t\terr = setup();", " \t}"), "hw-write")
    want("r2 f10 // comment continuation", _c(" {", "+\t// note \\", " \twrite32(a, 1);", " }"), "hw-write")
    want("r2 f11 ternary prev", _c(" \tv = flag ? val :", '+\tprintf("x");', " \t0;"), "hw-write")
    want("r2 f11 case label prev", _c(" \tbreak;", " \tcase 3:", '+\tprintf("x");', " \tbreak;"), "log-only")
    for mac in ("_KICK", "KK", "REG_kick"):
        want("r2 f12 macro %s" % mac, _c(" {", '+\tprintf("%%d", %s);' % mac, " }"), "hw-write")
    want("r2 f13 rename shim to md", "diff --git a/shim/x.c b/shim/x.md\nsimilarity index 100%\nrename from shim/x.c\nrename to shim/x.md\n", "hw-write")
    want("r2 f13 no file header", "@@ -1 +1 @@\n+x\n", "hw-write")
    want("r2 f14 prev with continuation", _c(" \tfoo(a, \\", '+\tprintf("x");', " \tb);"), "hw-write")
    want("r2 f14 #endif then #ifdef", _c(" {", "+#endif", "+#ifdef X", " }"), "hw-write")
    want("r2 f14 else-if joins", _c(" \t}", "+\t} else if (y) {", "+\t}", " }"), "hw-write")
    want("r2 f14 log open at run end", _c(" {", '+\tprintf("%d",', " \ta);"), "hw-write")
    want("r2 f14 problem inside a pending log", _c(" {", '+\tprintf("%d",', "+\t a); */", " }"), "hw-write")
    want("r2 f15 empty inner line", _fx("xnu/patches/0300-sweet-x.patch", ["\tprintf(\"x\");"]).replace("++\tprintf", "+\n++\tprintf"), "hw-write")


def _self_test_r3(want):
    """Opus r3 cases (docs/reviews/T-9862-change-class-r3.md, fixtures tools/testdata/change-class/r3)."""
    for dg in ("<%", "%>", "<:", ":>", "%:", "??"):
        want("r3 f1 digraph %s" % dg, _c(" {", '+\tprintf("%%d", a %s b);' % dg, " }"), "hw-write")
    want("r3 f1 if inside a condition", _c(" {", "+\tif (a && if) {", "+\t}", " }"), "hw-write")
    want("r3 f1 digraph inside a literal is text", _c(" {", '+\tprintf("<%% %%> <: :>");', " }"), "log-only")
    for fn in ("log_put_txt", "log_put_hex8"):
        want("r3 f2 %s stores through dst" % fn, _c(" {", "+\t%s(dst, 9, 0, v);" % fn, " }"), "hw-write")
    want("r3 f3 else after a comment", _c(" \tif (a) foo();", "+\tif (dbg) {", '+\t\tprintf("x");', "+\t}", " \t/* fallback */", " \telse power_off();"), "hw-write")
    want("r3 f3 else after a blank", _c(" \tif (a) foo();", "+\tif (dbg) {", "+\t}", " ", " \telse power_off();"), "hw-write")
    want("r3 f3 else after a multi-line comment", _c(" \tif (a) foo();", "+\tif (dbg) {", "+\t}", " \t/*", " \t * x", " \t */", " \telse power_off();"), "hw-write")
    want("r3 f3 else after #endif", _c(" \tif (a) foo();", "+\tif (dbg) {", "+\t}", " #endif", " \telse power_off();"), "hw-write")
    want("r3 f3 if block, comment, then code", _c(" \tfoo();", "+\tif (dbg) {", "+\t}", " \t/* c */", " \tbar();"), "log-only")
    want("r3 f5a second declarator", _c(" \tif (x) {", "+\t\tif (d) {", "+\t\tint dbg = 0, err;", "+\t\t}", " \t}"), "hw-write")
    want("r3 f5b declaration in the existing scope", _c(" \tif (x) {", "+\t\tint ret = 0;", " \t\tfoo();", " \t}"), "hw-write")
    want("r3 f5b declaration inside the added block", _c(" \tfoo();", "+\tif (d) {", "+\t\tint v = 0;", '+\t\tprintf("%d", v);', "+\t}", " \tbar();"), "log-only")
    want("r3 f6 removed read", _c(" {", '-\tprintf("%x", readl(base + 0x10));', " }"), "log-read")
    for e in ("*(uint32_t *)(base + 0x10)", "((uint32_t *)base)[4]", "((struct r *)base)->st", "(*(volatile_t)(b))"):
        want("r3 f7 computed pointer read %s" % e, _c(" {", '+\tprintf("%%x", %s);' % e, " }"), "hw-write")
    want("r3 f7 multiply is not a deref", _c(" {", '+\tprintf("%d", a * (b + 1));', " }"), "log-only")
    for mac in ("Kick", "_kick"):
        want("r3 f8/f9 macro %s" % mac, _c(" {", '+\tprintf("%%d", %s);' % mac, " }"), "hw-write")


def _self_test_r4(want):
    """Opus r4 cases (docs/reviews/T-9862-change-class-r4.md, fixtures tools/testdata/change-class/r4)."""
    want("r4 f1 CR hides a statement", _c(" {", '+\tprintf("x");\r write32(a, 2);', " }"), "hw-write")
    want("r4 f1 CR in a removed line", _c(" {", '-\tprintf("x");\r write32(a, 1);', " }"), "hw-write")
    want("r4 f1 CR ends a // comment", _c(" {", "+\t// note\r write32(a, 2);", " }"), "hw-write")
    want("r4 f4 indented ident: is no label", _c(" \tv = fast ?", " \t    cached:", '+\t    printf("x");', " \t    slow(a);"), "hw-write")
    want("r4 f8 statement expression, its ({ out of context", _c(" \t\tcompute(a);", '+\t\tprintf("x");', " \t});"), "hw-write")
    want("r4 f2 #define in a log call", _c(" {", '+\tprintf("%d",', "+#define write32 trace32", "+\t    a);", " \twrite32(a, 1);", " }"), "hw-write")
    want("r4 f3 comment hides an open if", _c(" \tif (err)", " \t\t/* log it */", '-\t\tprintf("x");', " \twrite32(a, 1);"), "hw-write")
    want("r4 f3 blank hides an open if", _c(" \tif (err)", " ", '+\t\tprintf("x");', " \twrite32(a, 1);"), "hw-write")
    want("r4 f3 #endif hides an else", _c(" \telse", " #endif", '+\tprintf("x");', " \tc();"), "hw-write")
    want("r4 f4 ternary arm looks like a label", _c(" \tv = fast ?", " \t    cached :", '+\t    printf("x");', " \t    slow(a);"), "hw-write")
    want("r4 f4 label at column 0", _c(" \tfoo();", " out:", '+\tprintf("x");', " \treturn;"), "log-only")
    want("r4 f5 C++ inner target", _fx("xnu/patches/0300-sweet-x.patch", ['\tIOLog("x");']).replace("k.c", "k.cpp"), "hw-write")
    for tok in ("(delete obj, 0)", "new_t", "new int", "reinterpret_cast<int>(a)"):
        want("r4 f5 token %s" % tok, _c(" {", '+\tprintf("%%d", %s);' % tok, " }"), "log-only" if tok == "new_t" else "hw-write")
    for e in ("(int)*(uint32_t *)base", "4[(uint32_t *)base]", "(char *)0x1c500000", "strlen((char *)base)"):
        want("r4 f6 %s" % e, _c(" {", '+\tprintf("%%x", %s);' % e, " }"), "hw-write")
    want("r4 f6 (void *) for %p", _c(" {", '+\tprintf("%p", (void *)p);', " }"), "log-only")
    want("r4 f7 extern shadow", _c(" \tif (x) {", "+\t\textern int (err);", " \t\terr = setup();", " \t}"), "hw-write")
    want("r4 f8 last statement of a statement expression", _c(" \tv = ({", " \t\tcompute(a);", '+\t\tprintf("x");', " \t});"), "hw-write")
    want("r4 f9 for header", _c(" \tfor (;", '+\t    printf("%s", "");', " \t    )", " \t\tfoo();"), "hw-write")
    want("r4 f9 for header split over lines", _c(" \tfor (", " \t    a;", '+\t    printf("x");', " \t    b)", " \t\tfoo();"), "hw-write")
    want("r4 f9 after a multi-line call", _c(" \tfoo(a,", " \t    b);", '+\tprintf("x");', " \tbar();"), "log-only")
    want("r4 f10 else in an #else branch", _c(" \tif (a) foo();", "+\tif (dbg) {", "+\t}", " #ifdef A", " \tbar();", " #else", " \telse off();", " #endif"), "hw-write")
    want("r4 f12 $ in a name", _c(" {", '+\tprintf("%d", write$printf(a, 1));', " }"), "hw-write")
    want("r4 f13 shadow inside the added block", _c(" \tfoo();", "+\tif (d) {", "+\t\tint bar = 0;", "+\t}", " \tbar();"), "hw-write")
    want("r4 (void) of a name the change did not declare", _c(" {", "+\t(void)status_reg;", " }"), "hw-write")
    for e in ("base[4]", "*base", "4[base]"):   # R31 residual: the driver reads the diff; recorded here so a change of behaviour shows
        want("R31 residual named pointer %s" % e, _c(" {", '+\tprintf("%%x", %s);' % e, " }"), "log-only")


def _self_test_r7(want, new, head, mid, log):
    """Opus r7 cases (docs/reviews/T-8dea-change-class-r7.md, fixtures tools/testdata/change-class/r7)."""
    want("r7 f1 log before an smc", _c(' \tregister uint64_t x3 __asm__("x3") = a3;', log, ' \t__asm__ volatile("smc #0"'), "hw-write")
    want("r7 f1 log after an smc, before the result", _c(' \t\t\t  "x12", "memory");', log, " \treturn x0;", " }"), "hw-write")
    want("r7 f1 asm operand line alone in view", _c(' \t\t: "+r"(x0));', log, " \treturn x0;"), "hw-write")
    want("r7 f1 inside a new patch", new(*head, "@@ -1,3 +1,4 @@", ' asm("nop");', '+\tprintf("x");', " b;", " c;"), "hw-write")
    want("r7 f1 'register' in a comment is no asm", _c(" \tfoo(); /* register x0 */", log, " \tbar();"), "log-only")
    want("r7 f2 target does not cross an outer hunk", mid(" +++ b/k.c", " @@ -1,2 +1,3 @@", " +\tfoo();", '-+\tprintf("a");', '++\tprintf("b");', " +\tbar();", "@@ -90,3 +90,3 @@", " +\tfoo();", '-+\tprintf("a");', '++\tprintf("b");', " +\tbar();"), "hw-write")
    want("r7 f2 two sections in one outer hunk: the last header wins", mid(" +++ b/k.c", " @@ -1,2 +1,3 @@", " +\tfoo();", " diff --git a/x.cpp b/x.cpp", " --- a/x.cpp", " +++ b/x.cpp", " @@ -1,2 +1,3 @@", " +\tbar();", '-+\tprintf("a");', '++\tprintf("b");'), "hw-write")
    want("r7 f3 +1 and -1 in two outer hunks", mid(" +++ b/k.c", " @@ -1,2 +1,3 @@", " +\tfoo();", '++\tprintf("b");', " +\tbar();", "@@ -90,3 +91,2 @@", " +++ b/k.c", " @@ -9,2 +9,3 @@", " +\tfoo();", '-+\tprintf("a");', " +\tbar();"), "hw-write")
    want("r7 f4 __func__ and __LINE__ in a log", _c(" \tfoo();", '+\tprintf("%s %d", __func__, __LINE__);', " \tbar();"), "log-only")
    want("r7 f4 other _names stay banned", _c(" \tfoo();", '+\tprintf("%d", __kick);', " \tbar();"), "hw-write")
    want("r7 f5 --- with the new side used up", new(*head, "@@ -1,1 +1,0 @@", "--- x ;"), "hw-write")
    want("r7 f5 +++ with the old side used up", new(*head, "@@ -1,0 +1,2 @@", "+++ x ;", "+b;"), "hw-write")
    want("r7 f5 @@ -0 form", new(*head, "@@ -0 +1 @@", "+/* c */"), "hw-write")
    want("r7 f5 one-line count default", new(*head, "@@ -1 +1 @@", "--- x ;"), "hw-write")
    want("r7 f3 counts end with the outer hunk, no inner @@ after", mid(" +++ b/k.c", " @@ -1,2 +1,3 @@", " +\tfoo();", '++\tprintf("b");', " +\tbar();", "@@ -90,3 +91,2 @@", " +++ b/k.c", " +\tfoo();", '-+\tprintf("a");', " +\tbar();"), "hw-write")


def _self_test_r8(want, new, head, log):
    """Opus r8 cases (docs/reviews/T-8dea-change-class-r8.md, fixtures tools/testdata/change-class/r8)."""
    for name, ctx in (("named operand", '[x1] "+r"(x1),'), ("comma-first operand", ', "+r"(x1)'), ("__asm__", '__asm__("isb");'), ("__asm", '__asm("nop");'), ("register", "register int x;"), ("__volatile__", "__volatile__ foo();"),
                      ("spin lock", "lck_spin_lock(l);"), ("irq off", "ml_set_interrupts_enabled(FALSE);"), ("barrier", "isb();"), ("exclusive", "x = load_exclusive(p);"), ("arm builtin", "v = __builtin_arm_ldrex(p);"),
                      ("section", 'static __attribute__((section(".boot"))) int b;'), ("console", "_cnputs(s);")):
        want("r8 f1 f4 f6 %s in view" % name, _c(" \t" + ctx, log, " \tbar();"), "hw-write")
    want("r8 f4 hunk heading", _c(" \tfoo();", log, " \tbar();").replace("+1,6 @@", "+1,6 @@ void start_kernel(void)"), "hw-write")
    want("r8 f4 inner hunk heading", new(*head, "@@ -1,3 +1,4 @@ void irq_handler(void)", " a;", " b;", " c;", '+\tprintf("x");'), "hw-write")
    want("r8 f4 poll loop", _c(" \tfor (i = 0; i < 9; i++) {", " \t\tdelay_us(1);", log, " \t}"), "hw-write")
    want("r8 f4 loop without a poll word", _c(" \tfor (i = 0; i < 9; i++) {", " \t\tfoo(i);", log, " \t}"), "log-only")
    want("r8 f4 poll word without a loop", _c(" \tdelay_us(1);", log, " \tbar();"), "log-only")
    want("r8 f3 new patch cut short", new(*head, "@@ -40,2 +40,3 @@", " a;", '+\tprintf("x");', " b;"), "hw-write")
    want("r8 f3 new patch, 2 lines above", new(*head, "@@ -40,3 +40,4 @@", " a;", " b;", '+\tprintf("x");', " d;"), "hw-write")
    want("r8 f3 new patch, 3 lines above", new(*head, "@@ -40,4 +40,5 @@", " a;", " b;", " c;", '+\tprintf("x");', " d;"), "log-only")
    want("r8 f5 removed loop body", _c(" \twhile (!ready) {", '-\t\tprintf("w");', " \t}"), "hw-write")
    want("r8 f5 removed log in a plain block", _c(" \t{", '-\t\tprintf("w");', " \t}"), "log-only")
    want("r8 f7 the logger", "diff --git a/shim/ramlog.c b/shim/ramlog.c\n--- a/shim/ramlog.c\n+++ b/shim/ramlog.c\n@@ -1,3 +1,4 @@\n \tfoo();\n" + log + "\n \tbar();\n", "hw-write")
    want("r8 f7 console target", new("diff --git a/osfmk/console/serial_console.c b/osfmk/console/serial_console.c", "--- a/osfmk/console/serial_console.c", "+++ b/osfmk/console/serial_console.c", "@@ -1,3 +1,4 @@", " a;", '+\tprintf("x");', " b;", " c;"), "hw-write")
    want("r8 f8 iokit header", new("diff --git a/iokit/IOKit/x.h b/iokit/IOKit/x.h", "--- a/iokit/IOKit/x.h", "+++ b/iokit/IOKit/x.h", "@@ -1,3 +1,4 @@", " a;", '+\tprintf("x");', " b;", " c;"), "hw-write")
    want("r8 f8 bsd header", new("diff --git a/bsd/x.h b/bsd/x.h", "--- a/bsd/x.h", "+++ b/bsd/x.h", "@@ -1,3 +1,4 @@", " a;", '+\tprintf("x");', " b;", " c;"), "log-only")
    want("r8 f9 %n", _c(" \tfoo();", '+\tprintf("%n", &x);', " \tbar();"), "hw-write")
    want("r8 f9 %ln", _c(" \tfoo();", '+\tprintf("%ln", &x);', " \tbar();"), "hw-write")
    want("r8 f9 %d", _c(" \tfoo();", '+\tprintf("%d\\n", x);', " \tbar();"), "log-only")
    want("r8 f11 __FUNCTION__", _c(" \tfoo();", '+\tprintf("%s", __FUNCTION__);', " \tbar();"), "log-only")
    want("r7 f5 --- with the new side used up, C name", new(*head, "@@ -1,1 +1,0 @@", "--- x.c ;"), "hw-write")
    want("r7 f5 one-line count default, C name", new(*head, "@@ -1 +1 @@", "--- x.c ;"), "hw-write")


def _self_test_r6(want):
    """Opus r6 cases (docs/reviews/T-8dea-change-class-r6.md, fixtures tools/testdata/change-class/r6)."""
    log = '+\tprintf("x");'
    new = lambda *inner: "diff --git a/xnu/patches/0300-x.patch b/xnu/patches/0300-x.patch\nnew file mode 100644\n--- /dev/null\n+++ b/xnu/patches/0300-x.patch\n@@ -0,0 +1,%d @@\n%s\n" % (len(inner), "\n".join("+" + l for l in inner))
    head = ("diff --git a/k.c b/k.c", "--- a/k.c", "+++ b/k.c")
    want("r6 f1 stale flag: run, lone }, run, else", _c(" \tif (a) {", " \t\tfoo();", log, " \t}", "+\tif (dbg) {", '+\t\tprintf("d");', "+\t}", " \telse {", " \t\toff();", " \t}"), "hw-write")
    want("r6 f1 same run with no else after it", _c(" \tif (a) {", " \t\tfoo();", log, " \t}", "+\tif (dbg) {", '+\t\tprintf("d");', "+\t}", " \tbar();"), "log-only")
    want("r6 f1 the split }) still seen after the reset", _c(" \tv = ({", " \t\tfoo();", log, " \t}", " \t);"), "hw-write")
    want("r6 f3 \\ line inside a new patch's hunk", new(*head, "@@ -1,2 +1,3 @@", " a;", '+\tprintf("x");', "\\ No newline at end of file", " b;"), "hw-write")
    mid = lambda *lines: _p(*lines).replace("@@ -1,8 +1,8 @@", "@@ -10,8 +10,8 @@")   # line 1 would be the patch's free text; past it, inside the inner diff
    want("r6 f3 \\ line added to an existing patch", mid(" +\tfoo();", "+\\ No newline at end of file", " +\tbar();"), "hw-write")
    want("r6 f4 traditional inner new file", new("--- /dev/null", "+++ b/inc/cfg.h", "@@ -0,0 +1 @@", "+/* placeholder */"), "hw-write")
    want("r6 f4 traditional inner delete", new("--- a/inc/cfg.h", "+++ /dev/null", "@@ -1 +0,0 @@", "-/* placeholder */"), "hw-write")
    want("r6 f4 @@ -0,0 with a named target", new(*head, "@@ -0,0 +1 @@", "+/* placeholder */"), "hw-write")
    want("r6 f5 --- then +++ right after @@ are code", new(*head, "@@ -1,2 +1,2 @@", " int a;", "--- x.c ;", "+++ x.c ;"), "hw-write")
    want("r6 f5 lone +++ inside the promised lines", new(*head, "@@ -1 +1,2 @@", " a;", "+++ x.c ;"), "hw-write")
    want("r6 f5 second file after the counts ran out", new(*head, "@@ -1,2 +1,3 @@", " a;", '+\tprintf("x");', " b;", "diff --git a/m.c b/m.c", "--- a/m.c", "+++ b/m.c", "@@ -1,2 +1,3 @@", " c;", '+\tprintf("y");', " d;"), "log-only")
    want("r6 f6 empty diff", "", "hw-write")
    _self_test_r7(want, new, head, mid, log)
    _self_test_r8(want, new, head, log)
    want("r6 f7 a docs script", "diff --git a/docs/golden/x.sh b/docs/golden/x.sh\n--- a/docs/golden/x.sh\n+++ b/docs/golden/x.sh\n@@ -1 +1,2 @@\n a\n+fastboot erase modem_a\n", "hw-write")
    want("r6 f7 a docs .md file", "diff --git a/docs/x.md b/docs/x.md\n--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1,2 @@\n a\n+b\n", "log-only")
    want("r6 f8 existing patch, C++ target in view", _p(" +++ b/iokit/IOService.cpp", " @@ -1,2 +1,3 @@", " +\tfoo();", "++\tprintf(\"x\");", " +\tbar();"), "hw-write")
    want("r6 f8 existing patch, target out of view, count-neutral", mid(" +\tfoo();", '-+\tprintf("a");', '++\tprintf("b");', " +\tbar();"), "hw-write")
    want("r6 f8 existing patch, C target in view, count-neutral", mid(" +++ b/k.c", " @@ -1,2 +1,3 @@", " +\tfoo();", '-+\tprintf("a");', '++\tprintf("b");', " +\tbar();"), "log-only")
    want("r6 f11 /dev/null target with counts", new("--- /dev/null", "+++ b/k.c", "@@ -1,2 +1,3 @@", " a;", '+\tprintf("x");', " b;"), "hw-write")
    want("r6 f11 .cpp headers with no code line", new("--- a/x.cpp", "+++ b/x.cpp", "@@ -1 +1 @@", " a;"), "hw-write")
    want("r6 f8 new patch, target is an .s file", new("diff --git a/x.s b/x.s", "--- a/x.s", "+++ b/x.s", "@@ -1,2 +1,3 @@", " a", '+\tprintf("x");', " b"), "hw-write")
    want("r6 f9 parenthesised typedef extern", _c(" {", "+\textern foo_t (status);", " \tif (status == 1)", " \t\tg();", " }"), "hw-write")
    want("r6 f9 extern with an unknown parameter name", _c(" {", "+\textern int bar(status);", " \tg();", " }"), "hw-write")
    want("r6 f9 plain extern prototypes stay log-only", _c(" {", "+\textern void bar(void);", "+\textern int baz(const char *, uint32_t);", " \tg();", " }"), "log-only")
    want("r6 f10 submodule summary with a clean file", "Submodule ext 1111111..2222222:\n" + _c(" \tfoo();", log, " \tbar();"), "hw-write")
    want("r6 f11 generators of port input", "diff --git a/tools/boards.py b/tools/boards.py\n--- a/tools/boards.py\n+++ b/tools/boards.py\n@@ -1 +1,2 @@\n a\n+b\n", "hw-write")
    want("r6 f11 third_party boot image tool", "diff --git a/tools/third_party/mkbootimg.py b/tools/third_party/mkbootimg.py\n--- a/tools/third_party/mkbootimg.py\n+++ b/tools/third_party/mkbootimg.py\n@@ -1 +1,2 @@\n a\n+b\n", "hw-write")
    want("r6 f11 an ordinary tool stays tools", "diff --git a/tools/foo.py b/tools/foo.py\n--- a/tools/foo.py\n+++ b/tools/foo.py\n@@ -1 +1,2 @@\n a\n+b\n", "tools")


def _self_test_r5(want):
    """Opus r5 cases (docs/reviews/T-9862-change-class-r5.md, fixtures tools/testdata/change-class/r5)."""
    log = '+\tprintf("x");'
    want("r5 f1 #else hides an open if", _c(" #ifdef A", " \tif (x)", " #else", " \tfoo();", " #endif", log, " \twrite32(a, 1);"), "hw-write")
    want("r5 f1 only #endif in the hunk", _c(" \tfoo();", " #endif", log, " \twrite32(a, 1);"), "hw-write")
    want("r5 f1 inside the #else branch, code seen", _c(" #else", " \tfoo();", log, " #endif"), "log-only")
    want("r5 f1 code after #endif", _c(" #endif", " \tfoo();", log, " \tbar();"), "log-only")
    want("r5 f2 comment close then if", _c(" \t/* long", " \t   comment */ if (err) /* why */", log, " \twrite32(a, 1);"), "hw-write")
    want("r5 f2 comment close then else", _c(" \tif (a) foo();", "+\tif (dbg) {", "+\t}", " \t/* note", " \t   more */ else off();"), "hw-write")
    want("r5 f2 code then a // comment ending a statement", _c(" \tfoo(); // why", log, " \tbar();"), "log-only")
    want("r5 f2 // comment ending in \\", _c(" \tfoo(); // why \\", log, " \tbar();"), "hw-write")
    want("r5 f3 case line with a ternary", _c(" \tfoo();", " \tcase 1: v = flag ? a :", log, " \tslow(a);"), "hw-write")
    want("r5 f3 label after an open if", _c(" \tif (y)", " lbl:", log, " \tslow(a);"), "hw-write")
    want("r5 f3 column 0 name after a ?", _c(" \tv = fast ?", " cached:", log, " \tslow(a);"), "hw-write")
    want("r5 f3 column 0 label after a statement", _c(" \tfoo();", " out:", log, " \treturn;"), "log-only")
    want("r5 f4 (void) of a closed block's name", _c(" {", "+\tif (d) {", "+\t\tint s = 0;", "+\t}", "+\t(void)s;", " }"), "hw-write")
    want("r5 f4 &x of a closed block's name", _c(" {", "+\tif (d) {", "+\t\tuint32_t g = 0;", "+\t}", "+\tif (try_read32(b, &g)) {", "+\t}", " }"), "hw-write")
    want("r5 f5 raw string", _c(" {", '+\tprintf(u8R"(%d")", write32(a, 1), u8R"(")");', " }"), "hw-write")
    want("r5 f5 digit separator", _c(" {", "+\tprintf(\"%d %d %d\", 1'000, write32(a, 1), 2'000);", " }"), "hw-write")
    want("r5 f5 non-ASCII callee", _c(" {", '+\tprintf("%d", écrire32(a, 1));', " }"), "hw-write")
    want("r5 f5 non-ASCII in a literal and a comment is text", _c(" {", '+\tprintf("é"); /* é */', " }"), "log-only")
    for e in ("(int)*(base + 0x10)", "(char *const)0x1c500000", "(void *)0x1c500000", "(int)strlen((void *)base)"):
        want("r5 f6 %s" % e, _c(" {", '+\tprintf("%%x", %s);' % e, " }"), "hw-write")
    want("r5 f6 (void *) into a string logger", _c(" {", "+\tramlog_line((void *)base);", " }"), "hw-write")
    want("r5 f7 read split from its (", _c(" {", '+\tprintf("%x", read32', "+\t    (base));", " }"), "log-read")
    want("r5 f8 split }) tail", _c(" \t\tcompute(a);", log, " \t}", " \t);"), "hw-write")
    want("r5 f8 } then other code", _c(" \t\tcompute(a);", log, " \t}", " \tfoo();"), "log-only")
    want("r5 f9 new empty header", "diff --git a/shim/cfg.h b/shim/cfg.h\nnew file mode 100644\nindex 0000000..e69de29\n", "hw-write")
    want("r5 f9 deleted source", "diff --git a/shim/cfg.h b/shim/cfg.h\ndeleted file mode 100644\n--- a/shim/cfg.h\n+++ /dev/null\n@@ -1 +0,0 @@\n-/* x */\n", "hw-write")
    want("r5 f9 inner new file", _fx("xnu/patches/0300-sweet-x.patch", []).replace("+diff --git a/k.c b/k.c", "+diff --git a/k.h b/k.h\n+new file mode 100644"), "hw-write")
    want("r5 f10 inner +++ code line", _fx("xnu/patches/0300-sweet-x.patch", ["++ s.c ;"]), "hw-write")
    want("r5 f10 inner --- code line", _fx("xnu/patches/0300-sweet-x.patch", ["-- s.c ;"]).replace("++-- s.c", "+--- s.c"), "hw-write")
    want("r5 f12 text after the log call", _c(" {", '+\tprintf("x"), (*p);', " }"), "hw-write")
    want("r5 f13 extern with a starred parenthesised declarator", _c(" {", "+\tif (d) {", "+\t\textern int *(err);", "+\t}", " }"), "hw-write")
    want("r5 f14 a/ and b/ differ, no rename line", "diff --git a/shim/x.c b/docs/y.md\n@@ -1 +1,2 @@\n {\n+\twrite32(a, 1);\n", "hw-write")
    want("r5 f14 path with a space", "diff --git a/shim/x b/docs/y.c b/shim/x b/docs/y.c\n@@ -1 +1,2 @@\n {\n+\twrite32(a, 1);\n", "hw-write")
    want("r5 f14 diff --cc", "diff --git a/docs/a.md b/docs/a.md\ndiff --cc shim/x.c\n@@@ -1 -1 +1 @@\n++\twrite32(a, 1);\n", "hw-write")
    want("r5 f15 unit_select is a safety file", "diff --git a/tools/unit_select.py b/tools/unit_select.py\n@@ -1 +1,2 @@\n x\n+y\n", "hw-write")


def self_test(classify):
    ok, n = True, 0
    def want(name, diff, cls):
        nonlocal ok, n
        n += 1
        got, why, _, _, _ = classify(diff)
        if got != cls:
            ok = False; print("FAIL %s: expected %s got %s %s" % (name, cls, got, why[:2]))
    for path in ("shim/x.c", "xnu/patches/0300-sweet-x.patch"):
        want("witness %s" % path, _fx(path, WITNESS), "log-only")
        for name, line in MUTANTS:   # each mutant inserted inside the witness block must flip the class
            want("mutant %s in %s" % (name, path), _fx(path, WITNESS[:6] + [line] + WITNESS[6:]), "hw-write")
        want("read %s" % path, _fx(path, WITNESS[:6] + ["\t\tuint32_t r = readl(base + 0x10);"] + WITNESS[6:]), "log-read")
        want("try_read32 &local %s" % path, _fx(path, WITNESS[:6] + ["\t\tuint32_t r;", '\t\tprintf("%d", try_read32(base + 0x10, &r));'] + WITNESS[6:]), "log-read")
    want("removed log line", _c(" int f(void)", " {", "-\tprintf(\"x\\n\");", " }"), "log-only")
    want("removed store", _c(" int f(void)", " {", "-\twrite32(a, 1);", " }"), "hw-write")
    want("table", _fx("soc/google/gs101/soc.json", ['"x": 1']), "hw-write")
    want("audit file", _fx("tools/test-bluejay-boot.py", ["x = 1"]), "hw-write")
    want("classifier gate file", _fx("tools/gate_select.py", ["x = 1"]), "hw-write")
    want("tool", _fx("tools/runs", ["x = 1"]), "tools")
    want("tool + log", _fx("tools/runs", ["x = 1"]) + _fx("shim/x.c", ['\tprintf("x");']), "tools")
    want("tool + read", _fx("tools/runs", ["x = 1"]) + _fx("shim/x.c", ['\tuint32_t r = readl(b);']), "hw-write")
    want("docs", _fx("docs/a.md", ["x"]), "log-only")
    want("unknown path", _fx("Makefile", ["x"]), "hw-write")
    want("patch rename", "diff --git a/xnu/patches/0300-x.patch b/xnu/patches/0300-y.patch\nsimilarity index 100%\nrename from xnu/patches/0300-x.patch\n"
         "rename to xnu/patches/0300-y.patch\n", "hw-write")
    want("binary port file", "diff --git a/shim/x.c b/shim/x.c\nBinary files a/shim/x.c and b/shim/x.c differ\n", "hw-write")
    mid = "diff --git a/xnu/patches/0300-x.patch b/xnu/patches/0300-x.patch\n@@ -40,2 +40,3 @@\n+\t\tg = 1;\n \t\tprintf(\"x\");\n"   # edit deep inside an existing patch
    want("patch edit mid-file", mid.replace("+\t\tg", "++\t\tg").replace(" \t\tprintf", " +\t\tprintf"), "hw-write")
    # Opus r1 findings that need more than one inserted line:
    want("f2 comment open across context", _c(" {", "+\t/* start", " \twrite32(a,1);", "+\t end */", " }"), "hw-write")
    want("f2 comment opened after code", _c(" {", '+\tprintf("x"); /* start', " \twrite32(a,1);", "+\t*/", " }"), "hw-write")
    want("f2 code after close in comment", _c(" {", "+\t/* a", "+\t b */ write32(a,1);", " }"), "hw-write")
    want("cast chain", _c(" {", '+\tprintf("%d", (int)(uint32_t)(rv >> 8));', " }"), "log-only")
    want("f2 comment left open over existing code", _c(" {", "+\t/* start", " \twrite32(a,1);", " \t/* old */", " }"), "hw-write")
    want("stray close uncomments existing code", _c(" \t/* a", "+\t */", " \twrite32(a,1); /* b */", " }"), "hw-write")
    want("unterminated literal after a log", _c(" {", '+\tprintf("x"); "', " }"), "hw-write")
    want("read in a log continuation", _c(" {", '+\tprintf("%d",', "+\t    readl(b));", " }"), "log-read")
    want("f2 balanced multi-line comment", _c(" {", "+\t/* a", "+\t * b", "+\t */", " }"), "log-only")
    want("f3 star comma statement", _c(" {", "+\t*(volatile uint32_t *)REG = 1,", '+\tprintf("x");', " }"), "hw-write")
    want("f7 inner header edit", _p(" diff --git a/osfmk/a.c b/osfmk/a.c", " --- a/osfmk/a.c", "-+++ b/osfmk/a.c", "++++ b/osfmk/b.c",
                                    " @@ -100,3 +100,4 @@", "  ctx;", "-+\tprintf(\"a\");", "++\tprintf(\"x\");", "  ctx2;"), "hw-write")
    want("f7 inner hunk offset edit", _p(" diff --git a/osfmk/a.c b/osfmk/a.c", " --- a/osfmk/a.c", " +++ b/osfmk/a.c", "-@@ -100,3 +100,4 @@",
                                         "+@@ -900,3 +900,4 @@", "  ctx;", "-+\tprintf(\"a\");", "++\tprintf(\"x\");", "  ctx2;"), "hw-write")
    want("f7 inner context edit", _p(" diff --git a/osfmk/a.c b/osfmk/a.c", " --- a/osfmk/a.c", " +++ b/osfmk/a.c", " @@ -100,3 +100,4 @@",
                                     "- if (cond_a) {", "+ if (cond_b) {", " +\tg = 1;", "  }"), "hw-write")
    want("r2 f9a inner add, stale @@ counts", _p(" diff --git a/osfmk/a.c b/osfmk/a.c", " --- a/osfmk/a.c", " +++ b/osfmk/a.c", " @@ -100,3 +100,4 @@",
                                "  ctx;", "++\tprintf(\"x\");", "  ctx2;"), "hw-write")
    want("inner log line replaced", _p(" diff --git a/osfmk/a.c b/osfmk/a.c", " --- a/osfmk/a.c", " +++ b/osfmk/a.c", " @@ -100,3 +100,4 @@",
                                "  ctx;", "-+\tprintf(\"x\");", "++\tprintf(\"y\");", "  ctx2;"), "log-only")
    want("f7 patch description edit", _p("-old description", "+new description", " diff --git a/osfmk/a.c b/osfmk/a.c"), "log-only")
    want("f8 removed guard, added brace", _c(" {", "-\tif (x) {", "+\t}", " \twrite32(a, 1);"), "hw-write")
    want("f8 removed guard block", _c(" {", "-\tif (x) {", '-\t\tprintf("x");', "-\t}", " }"), "hw-write")
    want("f12 close then open brace", _c(" {", "+\t}", "+\tif (x) {", " }"), "hw-write")
    want("#if 0 + #endif", _c(" {", "+#if 0", "+#endif", " }"), "hw-write")
    want("log after unbraced if", _c(" \tif (x)", '+\t\tprintf("x");', " \twrite32(a, 1);"), "hw-write")
    want("removed log after unbraced if", _c(" \tif (x)", '-\t\tprintf("x");', " \twrite32(a, 1);"), "hw-write")
    want("log after else", _c(" \telse", '+\t\tprintf("x");', " \twrite32(a, 1);"), "hw-write")
    want("log in macro body", _c(" #define K() \\", '+\tprintf("x"); \\', " \twrite32(a, 1)"), "hw-write")
    want("log after comment close: code above unknown", _c(" \t * text", " \t */", '+\tprintf("x");', " \tg = 1;"), "hw-write")
    want("log after comment close: code above seen", _c(" \tfoo();", " \t/* text */", '+\tprintf("x");', " \tg = 1;"), "log-only")
    want("log after if with comment", _c(" \tif (x) /* c */", '+\tprintf("x");', " \tg = 1;"), "hw-write")
    want("no context before run", "diff --git a/shim/x.c b/shim/x.c\n@@ -10 +10,2 @@\n+\tprintf(\"x\");\n \tg = 1;\n", "hw-write")
    _self_test_r2(want)
    _self_test_r3(want)
    _self_test_r4(want)
    _self_test_r5(want)
    _self_test_r6(want)
    print("RESULT: %s change-class self-test cases=%d" % ("PASS" if ok else "FAIL", n))
    return 0 if ok else 1
