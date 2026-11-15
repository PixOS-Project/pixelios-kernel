# 34 - EAGLContext private ABI, GLI dispatch table and CA's GL slot use, reversed from the iOS 16.7 cache

Date 2026-10-23. Build: iOS 16.7 (20H392) dyld shared cache, unslid addresses (same cache as research 33). Read-only, no phone touched.
Tags: **[obs-RE]** = disassembled/decompiled by me from the cache (`ipsw dyld disass/macho/symaddr/a2s`), **[src]** = a source file,
**[guess]** = inference. Nothing here is "observed on hardware". Extends research 33 s.2 and corrects it where marked **CORRECTION**.
Images read: OpenGLES (whole image, 0.7 s), QuartzCore (whole image, 1.5 s). Apple code is quoted only as addresses, a few
instructions and symbol names.

## 0. Summary (what changes for the shim)

1. `-[EAGLContext getMacroContextPrivate]` returns `&impl->rctx` where `impl` is the `_EAGLContextPrivate` struct that the ivar `_private`
   (offset 8) points to. The returned block is **`{ rctx, disp[1036] }`**: word 0 = `rctx` (passed as x0 to every GL entry), word 1.. = the
   1036 function pointers; slot n is at byte offset `8*(n+1)`. Confirmed three independent ways (s.1.3). The README guess is right.
2. **All 408 slot numbers in `gli_slots.txt` that have an Apple-exported GL symbol match OpenGLES 16.7 exactly (0 mismatches).** The only
   leftovers: slot 385 (`glGetCompressedTexImage`, not an ES function, no export to check) and the name of slot 591 (Apple has
   `glDeleteProgram` and `glDeleteShader` on the **same** slot, our `glDeleteObjectARB` special is the right idea). (s.3)
3. **CORRECTION to the README/research 33:** CA's server path (`EAGLServer` -> `GLESContext`) **never calls `+[EAGLContext setCurrentContext:]`**
   (only `CARenderer` x2 and `CATextLayer` do) and the GL calls carry `rctx` explicitly. So nothing makes the Mesa context current for us:
   the GLI wrappers (or `-getMacroContextPrivate`/first use per thread) must do it. "Relies on CA having made the context current" is false. (s.1.2, s.5)
4. `attachImage:toCoreSurface:invertedRender:` is a thin wrapper over `texImageIOSurface:...:invert:` that derives (internalformat, format, type) from the
   IOSurface FourCC; its first argument is a GL **target** (both reference ports and the shim are right, research 33 s.2.1 was wrong). (s.1.1)
5. The APPLE fence slots 463-468 and the framebuffer-parameter slot 1024 are **never reached** with Mesa: the first only exist behind
   `GL_APPLE_fence`, the second behind `GL_APPLE_tile_order_rot`; both extension strings are absent in Mesa. CA's fences are the ES3 sync objects
   (slots 779/781/782). (s.4)
6. `-setParameter:to:` returns a **status code, 0 = success** (`uint32`, 10000 when the sharegroup is not initialised), not a BOOL. CA ignores the result.
   The shim returns YES (=1, which means "error" in Apple's convention): harmless for CA, wrong for any other caller. (s.2)

## 1. OpenGLES.framework: the private EAGLContext methods

### 1.0 Object layout [obs-RE: `ipsw dyld macho OpenGLES --objc` + code]

```
@interface EAGLContext : NSObject {
    _EAGLContextPrivate *_private;   // offset +0x08  (ivar type encoding ^{_EAGLContextPrivate=@@QI^{__GLIContextRec}{__GLIFunctionDispatchRec}{__GLIFunctionDispatchRec}})
    NSString *debugLabel;            // offset +0x10
}
```
The `_EAGLContextPrivate` struct is calloc'ed with **0x40e8** bytes in `commonInitWithAPI:properties:` (0x1d3f513f0: `mov w0,#1; mov w1,#0x40e8; bl calloc-ish 0x1d3f52bf4`). Field names are mine (the type
encoding has none); offsets are from the code:

| Offset | Type | Meaning | Evidence |
|---|---|---|---|
| +0x00 | `EAGLContext*` | back pointer to the object (unretained) | `str x19,[x0]` at 0x1d3f514cc; `+currentContext` returns `[tls][0]` (0x1d3f51870) |
| +0x08 | `EAGLSharegroup*` | sharegroup | `stp x23,x22,[x0,#8]` 0x1d3f514c8; `-sharegroup` = `[[x0+8]+8]` 0x1d3f5188c |
| +0x10 | `NSUInteger` | API (1,2,3), 64-bit | `-API` 0x1d3f51880: `ldr x8,[x0,#8]; ldr x0,[x8,#0x10]; ret` |
| +0x18 | `uint32` | flags; bit 0 = `EAGLContextPropertyClientRetainRelease` | set at 0x1d3f51700 (`orr w9,w9,#1` after reading the property); tested by `+setCurrentContext:` 0x1d3f5182c |
| +0x20 | `__GLIContextRec*` | **rctx** (word 0 of the macro context) | `add x8,x0,#0x20` passed as `&rctx` to `gliCreateContextWithShared` 0x1d3f51514 |
| +0x28 | `disp[1036]` | GLI function table #1 = **the table CA calls** (0x2060 bytes = 1036*8) | `add x3,x0,#0x28` 0x1d3f5151c; `bzero(+0x28, 0x2060)` in dealloc 0x1d3f51778 |
| +0x2088 | `disp[1036]` | GLI function table #2 (second copy, unused by CA) | `mov w9,#0x2088; add x4,x0,x9` 0x1d3f51520 |

Check: 0x28 + 2*0x2060 = 0x40e8. Both dispatch records have exactly **1036** `^?` members (counted from the type string). [obs-RE]
`EAGLSharegroup._private` = `{Q, int state, BOOL, __GLDPixelFormatRec*, __GLISharedRec*}`; almost every method first checks `[[sharegroup ivar _private] + 8] == 1` ("shared state created", set at 0x1d3f52194)
and otherwise returns 0 / 10000. A shim object has `_private == NULL`, so **any Apple method we do not replace crashes on a shim object** (null deref at `[x0+8]`).
In the compositor path CA calls only the methods below (QuartzCore caller list, s.1.2), so this is safe today.

The GLI driver entry points are resolved by `eagl_init` (0x1d3f52444) into a table at `0x1e25b0da0`; `table[0]` = dlopen handle, then (name table 0x1d3f53350, stride 0x20) [obs-RE]:
`+0x08 gliChoosePixelFormat, 0x10 gliDestroyPixelFormat, 0x18 gliQueryRendererInfo, 0x20 gliDestroyRendererInfo, 0x28 gliCreateContext, 0x30 gliCreateContextWithShared,
0x38 gliDestroyContext, 0x40 gliAttachDrawable, 0x48 gliAttachDrawableWithOptions, 0x50 gliSetInteger, 0x58 gliGetInteger, 0x60 gliGetAttribute, 0x68 gliSetAttribute,
0x70 gliCopyAttributes, 0x78 gliGetMemoryAnnotationList, 0x80 gliGetVersion, 0x88 gliBindViewES, 0x90 gliPresentViewES`.

### 1.1 Method table (address, arguments, return, ivars)

All are ObjC IMPs: x0 = self (or Class), x1 = _cmd, arguments from x2. `msgSend$...` stubs are the linker's `objc_msgSend$<selector>` stubs (selref load + `br objc_msgSend`), identified by their selref.

| Method | Address | Arguments | Return | Ivars/fields touched | Behaviour [obs-RE] |
|---|---|---|---|---|---|
| `-initWithAPI:properties:` | 0x1d3f51270 | x2 `NSUInteger` api, x3 `NSDictionary*` properties (may be nil) | `id` (self or nil) | none directly | reads the property dictionary (keys seen as CFString constants: Sharegroup, SharedWithCompute, ClientLabel, Accelerated, ClientRetainRelease, VisibleInDebugTools), wraps/copies it into a dictionary (initWithDictionary:copyItems:) and tail-calls `commonInitWithAPI:properties:` (stub 0x1d3f54da0). Unknown keys are ignored. |
| `-commonInitWithAPI:properties:` | 0x1d3f513f0 | x2 api (64-bit, `sub x8,x22,#1; cmp x8,#2` = 1..3), x3 properties | `id` | `_private`=alloc 0x40e8; impl+0,+8,+0x10,+0x18 | `gliCreateContextWithShared(&impl->rctx, sharegroup pixfmt, shared, &impl->disp1, &impl->disp2, flags[api-1])` through `table+0x30` (0x1d3f51530); nonzero = failure: release, return **nil** (0x1d3f5161c). Then `gliSetInteger(rctx, 0x139/0x2bd/0x3e3/0x3a0/0x3a1/0x39f/0x2bf, &v)`. ClientRetainRelease sets impl+0x18 bit 0. |
| `-API` | 0x1d3f51880 | - | `NSUInteger` (64-bit `x0`) | `_private` (+8), impl+0x10 | no validity check. CA compares only `w0` with 3. |
| `-setParameter:to:` | 0x1d3f52064 | x2 pname (`NSUInteger`/`GLenum`-like, passed on as x1), x3 `const int*` | `uint32` status: **0 = ok**, `0x2710` if sharegroup state != 1 | `_private`, sharegroup state, impl+0x20 | `gliSetInteger(rctx, pname, ptr)` (table+0x50), `mov w0,w0`. Proof that 0 = success: `0x1d3f52018` returns `result == 0` as BOOL. |
| `-getParameter:to:` | 0x1d3f520c0 | same | `uint32` | same | `gliGetInteger` (table+0x58). CA never calls it (0 call sites). |
| `-getMacroContextPrivate` | 0x1d3f5211c | - | `void*` = `impl+0x20` | `_private` | `ldr x8,[x0,#8]; add x0,x8,#0x20; ret` - **no validity check, no NULL check**; `-GetMacroContextPrivate` 0x1d3f52128 is a tail call to the same selector. |
| `+setCurrentContext:` | 0x1d3f517ec | x2 `EAGLContext*` or nil | `BOOL` (w0, 1 = ok, 0 = failure) | impl (via ivar +8), impl+0x18 bit 0, impl+0x08 sharegroup | if `ctx->impl == current impl` return YES; else `0x1d3f52764` = `pthread_setspecific(30, impl)` (TLS slot `tpidrro_el0+0xf0`) and `pthread_setspecific(g_key, impl)`; success -> YES. Retain/release of old/new context **only when impl+0x18 bit 0 is clear** (CA sets ClientRetainRelease, so no retain). nil is valid (clears the slot). |
| `+currentContext` | 0x1d3f51864 | - | `EAGLContext*` | TLS `tpidrro_el0+0xf0` = impl pointer | `[tls][0]`, or nil. Not retained/autoreleased. |
| `-attachImage:toCoreSurface:invertedRender:` | 0x1d3f51c60 | x2 `NSUInteger` **GL target** (0xde1 `GL_TEXTURE_2D` or 0x8d41 `GL_RENDERBUFFER`), x3 `IOSurfaceRef`, x4 `BOOL` invert (x4 is passed as `strb` byte) | `BOOL` (w0 1/0) | `_private`, sharegroup state | requires sharegroup state == 1; `IOSurfaceGetPixelFormat(surface)` FourCC -> (internalformat, format, type) table (below); then **`[self texImageIOSurface:s target:target internalFormat:ifmt width:IOSurfaceGetWidth(s) height:IOSurfaceGetHeight(s) format:fmt type:type plane:0 invert:invert]`** (stub 0x1d3f54fc0, selref 0x1dabbeba8). Unknown FourCC -> prints an error and returns 0 (0x1d3f51e88). |
| `-texImageIOSurface:...:plane:` (9 args) | 0x1d3f51ecc | like below minus `invert` | `BOOL` | - | wrapper: calls the `invert:` variant with invert=0 (stack copy `[sp]=type, [sp+8]=plane, [sp+0xc]=0`). CA never calls it. |
| `-texImageIOSurface:target:internalFormat:width:height:format:type:plane:invert:` | 0x1d3f51efc | see below | `BOOL` | `_private`, impl+0x20 | `gliSetInteger(rctx, 0x38e, {IOSurfaceGetID(s), target, ifmt, w, h, fmt, type, plane, 0})` (9 ints at sp+4, table+0x50) then `gliSetInteger(rctx, 0x399, {target, invert})`; either nonzero -> error message, return 0; else return 1. **Only the IOSurface ID (not the pointer) reaches the GL driver.** |

Argument/stack layout of the 10-argument `texImageIOSurface` (both the callee at 0x1d3f51efc and CA's callers at 0x188407058-0x188407080 and 0x188408e30-0x188408e58 agree) [obs-RE]:

| Position | Value | Width in the ABI |
|---|---|---|
| x0 / x1 | self / _cmd | |
| x2 | `IOSurfaceRef` | 8 |
| x3 | target (CA: `GL_TEXTURE_2D` 0xde1; `ldr w3` / `ldrh w3`, zero-extended) | 8 (callee only reads w3) |
| x4 | internalFormat (CA: `sxtw x4,w24`, i.e. sign-extended) | 8 |
| x5 | width (CA: `mov x5,x19`) | 8 (callee only reads w5) |
| x6 | height | 8 (callee only reads w6) |
| x7 | format | 8 (`sxtw`) |
| `[sp+0x00]` | type (CA stores 8 bytes: `str x8,[sp]`) | **8** |
| `[sp+0x08]` | plane (`str w..,[sp,#8]`) | **4** |
| `[sp+0x0c]` | invert (`strb wzr,[sp,#0xc]`) | **1** |

FourCC -> parameters used by `attachImage:` (0x1d3f51cb8-0x1d3f51e48; for a renderbuffer target the unsized internalformat is replaced by the sized one):

| FourCC | internalformat (tex / RB) | format | type |
|---|---|---|---|
| `BGRA` (0x42475241), `S32b` | 0x1908 / 0x8058 | 0x80e1 (BGRA) | 0x8367 (UNSIGNED_INT_8_8_8_8_REV) |
| `RGBA` (0x52474241) | 0x1908 / 0x8058 | 0x1908 | 0x8367 |
| `L555`, `5551`, `s551`, `S555` | 0x1908 / 0x8057 | 0x1908 | 0x8034 |
| `L565` | 0x1907 / 0x8d62 | 0x1907 | 0x8363 |
| `4444` | 0x1908 / 0x8056 | 0x80e1 | 0x8033 |
| `L008` | 0x1909 | 0x1909 | 0x1401 |
| `A008`/`A007` | 0x1906 | 0x1906 | 0x1401 |

Also `S32a`: same as BGRA. For the BGRA scanout buffers the CA-side `create_surface_from_iosurface` passes `0x1908, 0x80e1, 0x1401` (type UNSIGNED_BYTE) instead of 0x8367: the backend must derive the pixel layout from the IOSurface FourCC, not from `type`. [obs-RE; the decision is a guess]

### 1.2 Who calls what in QuartzCore (complete: every Obj-C send goes through `objc_msgSend$` stubs, no direct selref use) [obs-RE]

| Selector (stub in QuartzCore) | Call sites |
|---|---|
| `initWithAPI:properties:` (0x1885ca440) | `EAGLServer::renderer` 0x1882eb0b4 (API 3), 0x1882eb0e8 (API 2) |
| `setParameter:to:` (0x1885cec40) | `EAGLServer::renderer` 0x1882eb10c with (0x260, &0); result ignored (x0 overwritten by the next instruction) |
| `API` (0x1885c5f80) | `renderer` 0x1882eb11c (`cmp w0,#3`), `GLESContext::GLESContext(void*)` 0x188409a70, `-[CARenderer ...]` 0x1885495f4 |
| `getMacroContextPrivate` (0x1885c9f00) | **one site**: `GLESContext` ctor 0x188409690 |
| `attachImage:toCoreSurface:invertedRender:` (0x1885c7bc0) | `make_buffer_current` 0x188407ac8 (target `[x21+0x58]` = 0xde1, invert 0); `attach_surface_texture` 0x188409048 (target = `ldrh [image+0x34]`, invert 0). Result ignored. |
| `texImageIOSurface:...:invert:` (0x1885d0460) | `create_surface_from_iosurface` 0x188407080 (0xde1, 0x1908, w, h, 0x80e1, 0x1401, plane 0, invert 0); `attach_surface_texture` 0x188408e58. Result ignored. |
| `+setCurrentContext:` (0x1885cdd20) | `-[CARenderer ...]` 0x188549b48, 0x188549bf8; `-[CATextLayer ...]` 0x188299520. **Not in `EAGLServer`/`GLESContext`.** |
| `+currentContext` (0x1885c8b40) | `-[CARenderer ...]` 0x188549b30 |
| `getParameter:to:`, `sharegroup`, `isMultiThreaded`, `setMultiThreaded:`, `setDebugLabel:`, `renderbufferStorage:fromDrawable:`, `presentRenderbuffer:*`, `setBlockFence:onQueue:`, `texImageIOSurface:...:plane:` | **no call site** |

(The image `target` field at `image+0x34` is a 16-bit GL target used as `glBindTexture(target, name)`; no `0x84f5` literal exists in the image, so rectangle targets are not produced by CA's GLES path, answering research 33 open question 4 negatively. [obs-RE negative: `mov w,#0x84f5` count 0])

### 1.3 The macro context and how CA uses it [obs-RE]

* `GLESContext::GLESContext(void*)` 0x18840960c: `bl getMacroContextPrivate` (0x188409690) then `str x0,[x19,#0x7f8]` (0x188409698). **No NULL check**: the next use is `ldr x8,[x20,#0x3b0]` (`glGetString`), so a NULL return crashes backboardd.
* Every GL call is `ldr xA,[tbl,#8*(slot+1)]; ldr x0,[tbl]; (args) blr xA` (or `br xA` as a tail call). Example 0x188407a30-0x188407a44: `glGenTextures` = `ldr x8,[x24,#0x318]; ldr x0,[x24]; mov w1,#1; add x2,...; blr x8` (0x318/8-1 = slot 98).
* CA never reads any other word of the table and never dereferences `rctx`; it only forwards it.
* So the shim's struct `{ void *rctx; void *disp[1036]; }` is exactly Apple's block, and `rctx` can be any pointer (the shim uses its box pointer; fine).
* The exported `gl*` functions of OpenGLES.framework (what an app would call) read `tpidrro_el0+0xf0` (= `impl`) and call `impl+0x28+8n` with `x0 = impl+0x20`'s word. If `+setCurrentContext:` is replaced and does not fill that TLS slot, those exports silently return (`cbz x8 ... ret`). CA's compositor path never uses them. [obs-RE]

## 2. Review of `eagl_private.h` (right/wrong) and of the swizzles in `eagl_objc.m`

| Declaration in the shim | Verdict | Evidence / fix |
|---|---|---|
| `-initWithAPI:(NSUInteger) properties:(id)` -> `id` | **right** | x2 used as 64-bit (`sub x8,x22,#1`) 0x1d3f514d4; returns self/nil (0x1d3f51368, 0x1d3f5161c) |
| `-API` -> `NSUInteger` | **right** | `ldr x0,[x8,#0x10]` 64-bit |
| `-setParameter:(unsigned)pname to:(const int*)` | args **right** (width of pname is irrelevant, x2), **return type wrong**: Apple returns `uint32` status, **0 = success**, not `BOOL`/YES | 0x1d3f52064-0x1d3f520b0, `0x1d3f52018`. Fix: return `0` on success (declare `unsigned`). CA ignores it. `m_set_param` returns `1` on success today. |
| `-getMacroContextPrivate` -> `void*` to `{rctx, disp[1036]}` | **right** | s.1.3 |
| `-attachImage:(NSUInteger)target toCoreSurface:(IOSurfaceRef) invertedRender:(BOOL)` -> `BOOL` | **right** (target, not texture name) | 0x1d3f51c60: x2 compared with 0x8d41, passed as `target:`. `m_attach` accepts only 0xde1; Apple also accepts 0x8d41 (CA never sends it). |
| `-texImageIOSurface:target:internalFormat:width:height:format:type:plane:invert:` with `(IOSurfaceRef, NSUInteger, NSUInteger, uint32_t, uint32_t, NSUInteger, NSUInteger, uint32_t, BOOL)` -> `BOOL` | **right** and ABI-compatible: type = 8 bytes at `[sp]`, plane = 4 at `[sp+8]`, invert = 1 at `[sp+0xc]`; x5/x6 are 64-bit in the caller but only `w5/w6` are read by Apple and by `uint32_t` params | s.1.1 table. `internalFormat` is sign-extended by CA (`sxtw`): NSUInteger reads the same low 32 bits, fine for GL enums. |
| `+setCurrentContext:` -> `BOOL`, `+currentContext` | **right**; semantics note: CA's server path never calls them | s.1.2 |
| `dealloc` -> NSObject's | right (Apple's own dealloc calls `[self setDebugLabel:nil]` then `[super dealloc]`, 0x1d3f517b0-0x1d3f517d8) | |
| "no ivar of ours; Apple's methods must not run on these objects" | **right, and enforceable**: every other Apple method derefs `_private` (+8) which is NULL on a shim object -> crash (`sharegroup`, `isMultiThreaded`, `setMultiThreaded:`, `getParameter:to:`, ...). CA has no call site for them; if hardening is wanted either replace them or give the object a calloc'ed 0x40e8 `_private` (then Apple's `-API`/`-getMacroContextPrivate` would work unmodified). | s.1.0 |
| Comment "CA asks for ES2 then ..." / "initWithAPI:2 returns ES3 context" | consistent: CA only inspects `[API] == 3` after init (0x1882eb120) | R33 s.1.1 |

## 3. GLI slot numbers vs OpenGLES 16.7 (task 2)

Method [obs-RE]: OpenGLES.framework exports one wrapper per GL function (`_glBindTexture` etc.). Each reads the current `impl` from TLS and does `ldr xN,[impl,#off]; br xN` with `x0 = [impl+0x20]`, so **slot = (off - 0x28) / 8**
(e.g. `_glAlphaFunc` 0x1d3f4c980: `ldr x2,[x8,#0x30]` -> slot 1; `_glTexImage2D` 0x1d3f4dd48: `ldr x11,[x9,#0x990]` -> slot 301). Functions with more than 7 GL arguments shift the register
arguments by one and re-pack the stack arguments (`str w7,[sp]; str x10,[sp,#8]`), which is exactly a C call `entry(rctx, args...)`. 457 exported names resolved (all of them; the first scan had 12 `None` for these multi-argument wrappers, fixed by accepting `x9` as the TLS register).
Script used (no Apple code in it):

```python
# python3 -I slots_gl.py   (input: ipsw dyld disass OpenGLES --quiet --no-color > gl.dis ; ipsw dyld symaddr ... > gl.sym)
import re
L=open('gl.dis').read().split('\n'); funcs={}; cur=None
for l in L:
    if l.startswith('sub_'): cur=int(l[4:-1],16); funcs[cur]=[]
    else:
        m=re.match(r'0x([0-9a-f]+):\s+(?:[0-9a-f]{2} ){4}\s*(.*)',l)
        if m and cur is not None: funcs[cur].append((int(m.group(1),16),m.group(2)))
for l in open('gl.sym'):
    m=re.match(r'(0x[0-9a-f]+):\s+\(external[^)]*\)\s+_(gl\w+)',l)
    if not m: continue
    ld={}
    for ad,t in funcs.get(int(m.group(1),16),[])[:30]:
        x=re.match(r'ldr\s+(x\d+), \[(x8|x9)(?:, #(0x[0-9a-f]+))?\]',t)
        if x: ld[x.group(1)]=int(x.group(3) or '0',16)
        x=re.match(r'(?:br|blr)\s+(x\d+)',t)
        if x:
            if ld.get(x.group(1),0)>=0x28: print((ld[x.group(1)]-0x28)//8, m.group(2))
            break
```

Result against `gli_slots.txt` (410 slots):

| Result | Count | Detail |
|---|---|---|
| name and slot match | 408 | includes glFlush 90, glFinish 89, glBindTexture 5, glClear 10, glGetString 117, glGetIntegerv 104, glTexImage2D 301, glFramebufferTexture2D 677, glBindFramebuffer 672, glGenFramebuffers 674, glFenceSync 779 / glDeleteSync 781 / glClientWaitSync 782, and 463-468 = `glGenFencesAPPLE glDeleteFencesAPPLE glSetFenceAPPLE glIsFenceAPPLE glTestFenceAPPLE glFinishFenceAPPLE`, 469 `glTestObjectAPPLE`, 470 `glFinishObjectAPPLE`, 1011-1014 `glCurrentPaletteMatrixOES glLoadPaletteFromModelViewMatrixOES glMatrixIndexPointerOES glWeightPointerOES`, 1024 `glFramebufferParameteriAPPLE`, 1025 `glGetFramebufferParameteriAPPLE`, 1026 `glResolveMultisampleFramebufferAPPLE`, 1027 `glCopyTextureLevelsAPPLE` |
| **slot mismatch** | **0** | |
| not checkable | 2 | slot 385 `glGetCompressedTexImage`: no Apple export (it is not an ES function); slot 591: Apple exports `glDeleteProgram` **and** `glDeleteShader` both at slot 591 (not `glDeleteObjectARB`). The `sgli_DeleteObjectARB` special that picks program vs shader is the right shape. |
| in Apple's table, absent from `gli_slots.txt` | 0 | every Apple-exported slot is already listed |
| table slots with no Apple export | 627 of 1036 | desktop-GL leftovers or driver-only slots (e.g. 0, 13, 19, 22, 27-29, 35). Not nameable from OpenGLES; GLEngine fills them. The shim traps them (log once). |

Slot count: **1036** per dispatch record (type encoding), maximum exported slot 1032. `glGetAttachedObjectsARB` / `glGetHandleARB` do not exist in this table (no export names them); the sailfish names for them are for an older build. [obs-RE]
`glFramebufferParameteriAPPLE`'s wrapper (0x1d3f51120) passes three arguments after rctx (`mov x3,x2; mov x2,x1; mov x1,x0`); see s.4 for the real call.

## 4. Which GL slots QuartzCore actually calls (task 3)

Method [obs-RE]: whole-QuartzCore scan with per-function register tracking. A register becomes "table" when loaded from `[ctx,#0x7f8]` (the `GLESContext` field) or copied from one;
a call is `ldr xA,[table,#off]` followed by `blr xA` **or `br xA`** (tail call, which research 33's `ldr+blr` scan missed: that is why `glFlush`, `glBlendFunc`, `glGetError`, the fence slots and the marker slots were absent
from its list); plus a second scan on the pattern `ldr x0,[xT]; ldr xA,[xT,#off]; blr xA` anywhere in the image. Slots only reached through other structures (vtables, `CALayer` ivars; slots 0, 1, 13, 19, 22, 27-29, 35, 191, 236, 57) were checked by hand and are **not** GL (false positives, listed so nobody chases them).
Still a lower bound (calls through a register that is spilled and reloaded can be missed), but every call site found is a genuine GL call.

Slots called by CA, with the CA function (all `CA::OGL::GLContext` / `GLESContext`) [obs-RE]:

| Slot | Name (Apple export) | Where |
|---|---|---|
| 5 | glBindTexture | `make_buffer_current`, `update_state`, `restore_state`, `update_texture` |
| 7 / 336 / 338 | glBlendFunc / glBlendFuncSeparate / glBlendEquation | `load_blend_func` |
| 10, 12, 63, 72, 251, 335 | glClear, glClearColor, glDisable, glEnable, glScissor, glViewport | `clear_region`, `init_state`, `restore_state`, `set_surface` |
| 56, 59, 98, 195, 301, 302, 304, 307, 380, 681 | glCopyTexSubImage2D, glDeleteTextures, glGenTextures, glPixelStorei, glTexImage2D, glTexParameterf, glTexParameteri, glTexSubImage2D, glCompressedTexImage2D, glGenerateMipmap | texture management in `GLESContext` |
| 65, 67 | glDrawArrays, glDrawElements (`mode,count,0x1403,indices`) | `draw_lines/points/elements` |
| 89, 90 | **glFinish**, **glFlush** | `finish_image` (no-APPLE_fence branch), `flush_texture_cache` |
| 102, 104, 117 | glGetError, glGetIntegerv, glGetString | `get`, `update`, `GLESContext` ctor, `init_state` |
| 342 | glActiveTexture | state |
| 511, 512, 513 | glVertexAttribPointer, glEnableVertexAttribArray, glDisableVertexAttribArray | draw paths |
| 591 | glDeleteProgram/glDeleteShader (one slot, both used) | `purge`, `gl_delete_shader` |
| 594-600 | glCreateShader glShaderSource glCompileShader glCreateProgram glAttachShader glLinkProgram glUseProgram | `create_shader`, `update_shader` |
| 602, 605, 606, 612, 613, 619, 620 | glUniform1f, 4f, 1i, 3fv, 4fv, glUniformMatrix3fv, 4fv | `update_shader` |
| 625, 630 | glGetUniformLocation (30 call sites), glBindAttribLocation | `create_shader` |
| 672, 673, 674, 677 | glBindFramebuffer, glDeleteFramebuffers, glGenFramebuffers, glFramebufferTexture2D | `set_surface`, `make_buffer_current`, `finalize_surface` |
| 779, 781, 782 | **glFenceSync** (`0x9117, 0`), **glDeleteSync**, **glClientWaitSync** (`sync, 1, 0`) | `GLESContext::set_fence` 0x1884058b8, `delete_fence` 0x18840590c, `test_fence` 0x1884058d0 |
| 814 | glLabelObjectEXT (`GL_TEXTURE 0x1702, name, len, label`) | `attach_surface_texture` |
| 816, 817, 818 | glInsertEventMarkerEXT, glPushGroupMarkerEXT, glPopGroupMarkerEXT | `GLContext::insert_event_marker/push_group_marker/pop_group_marker` |
| 1024 | glFramebufferParameteriAPPLE | `set_surface` 0x18840773c, **gated** (below) |

Not found, although research 33 listed them: `glClearStencil` (15), `glEnableClientState` (73), `glReadBuffer` (236, that hit was a false positive), and the sailfish-style `...ARB` names (they are Apple's `glCreateShader`... above). Not found and listed as trapped in the README: **463-468, 469 (TestObject), 1011-1014, 1025-1027: no call site at all.** Slot 470 (`glFinishObjectAPPLE`) is called only under the extension gate below.

### 4.1 APPLE fence slots, framebuffer parameters: the gates [obs-RE]

* The context flag word is `GLESContext+0x824`, filled in the ctor from `check_extension` results in the order (bit): `GL_APPLE_fence` (bits 0 and 1), `GL_APPLE_texture_filter_cubic` 2, `ycbcr_basic` 3, `ycbcr_extended` 4, `GL_APPLE_texture_xr` 5,
  **`GL_APPLE_tile_order_rot` 6**, `GL_EXT_shader_framebuffer_fetch` 7, `anisotropic` 8, `lod_bias` 9, ... (ctor 0x188409754-0x188409a58; strings at 0x18858204e-0x1885821f7).
* `finish_image` 0x1884068e4: `tbnz w8,#1` (GL_APPLE_fence): **set** -> loop `glFinishObjectAPPLE(rctx, 0x1702, name)` (slot 470); **clear** (Mesa) -> `IOSurfaceLock/Unlock` on the image's surface, and `glFinish` (slot 89) when the dirty bit `+0x868 bit 2` is set. So with Mesa the fence slots 463-470 are unreachable, `glFinish` is required.
* `set_surface` 0x188407708: `tbz w8,#6` (GL_APPLE_tile_order_rot) skips the whole block that calls **slot 1024** `(rctx, GL_FRAMEBUFFER 0x8d40, 0x8a79, int* -> {0x8a7a | 0x8a7d})`; `make_buffer_current` 0x188407be8 has a second `tbz w8,#6` block. With Mesa (extension absent) neither runs.
  The value selecting 0x8a7a/0x8a7d is `(surface flag byte bit 0x10000) ? 0x8a7a : 0x8a7d`; the third argument is a **pointer** (`add x3,sp,#4`), i.e. the call is `glFramebufferParameterivAPPLE`-shaped although Apple's export is named `...Parameteri...` (its wrapper just forwards x3). **Answers research 33 open question 3 for 0x8a79**: it is an Apple tile-order/rotation setting, irrelevant while we do not advertise `GL_APPLE_tile_order_rot`.
* ES3 sync (slots 779/781/782) is the real fence path: `supports_fences` (0x1884058b0) is a constant `mov w0,#1`; `test_fence` = `glClientWaitSync(sync, 1 /*SYNC_FLUSH_COMMANDS_BIT*/, 0)` and reports "signalled" for `0x911a` (ALREADY_SIGNALED) and `0x911c` (CONDITION_SATISFIED) only (`w0-0x911a < 4` and even). Mesa supports all three.
* `glGetString(0x8a1e)` and `GL_EXT_shader_framebuffer_fetch` as in research 33 s.2.3 (unchanged).

## 5. Consequences for the shim (for the driver; I did not edit the shim)

1. **Current-context hole (new, important):** CA does not call `+setCurrentContext:` in the compositor path. `gli_table.c` wrappers must ensure the Mesa context of `rctx` is current on the calling thread before every call (cheap thread-local compare, switch on mismatch), or the first Mesa call on a render thread runs with no current context. Alternatively make `-getMacroContextPrivate` bind it, but CA's render threads may differ from the constructing thread.
2. `m_set_param` should return `0` on success (declare as `unsigned`/`NSUInteger`); CA ignores it.
3. `m_attach`: accept target 0x8d41 as Apple does (optional), and derive the format from the IOSurface FourCC (table in s.1.1) instead of trusting `type`; CA passes type 0x1401 on the other path, 0x8367 on Apple's own attach path.
4. The trapped 13 slots are never called by CA (s.4): the README "13 slots trap" risk can be downgraded; `glFramebufferParameteriAPPLE` as `sgli_nop` is right if it is ever reached. `glFinishObjectAPPLE`/`TestObject` are unreachable without `GL_APPLE_fence`.
5. Add (already listed or trapped, now known to be called): `glFlush`->Finish (90), `glGetError` (102), `glBlendFunc` (7), `glBlendFuncSeparate` (336), debug markers 816-818 (specials `sgli_nop` exist), `glLabelObjectEXT` 814 (listed, wired).
6. If anything besides CA's listed methods can ever message a shim `EAGLContext` (`sharegroup`, `isMultiThreaded`, `setMultiThreaded:`, `getParameter:to:`), either swizzle them or allocate a zeroed 0x40e8 `_private` so Apple's own accessors do not dereference NULL.

## 6. Unresolved / [guess]

* What pname `0x260` means to GLEngine (`setParameter:0x260 to:&0`, CA sends it once): GLI driver-internal [guess: a GLI context option]; the shim stores and ignores it, CA ignores the return.
* pnames 0x38e/0x399 (`gliSetInteger` pnames of `texImageIOSurface`) are driver-internal; only relevant if one ever wanted to call Apple's GLEngine.
* The meaning of the fields at `IOSurface` dirty bit `GLESContext+0x868 bit 2` in `finish_image` (set by `update_image`?) was not followed. [guess: "GPU wrote since last finish"]
* Whether the swap path ever waits on `test_fence` (research 33 open question 6): not followed here.
* Unnamed table slots (0, 13, 19, 22, 27-29, 35, 191, 236) came out of the scan as false positives; if a real trap log ever shows one of them, re-check.
* No on-device evidence. The ABI is [obs-RE] from the same binaries that run on the phone; the cache is 20H392 (16.7) and the README says the reference ports use an iOS 16.5 table: **the 16.7 binary agrees with that table on every named slot**.

## 7. Reproduce

```
C=~/pixelios-artifacts/work/dsc/cache/dyld_shared_cache_arm64; I=~/pixelios-artifacts/tools/ipsw
$I dyld disass $C --image OpenGLES  --quiet --no-color > gl.dis     # 0.7 s
$I dyld disass $C --image QuartzCore --quiet --no-color > qc.dis    # 1.5 s
$I dyld symaddr $C --image OpenGLES > gl.sym ; $I dyld symaddr $C --image QuartzCore > qc.sym
$I dyld macho $C OpenGLES --objc            # ivar layout, 1036 pointers per dispatch record
$I dyld macho $C OpenGLES --objc --objc-refs   # selref -> selector name (needs --objc too)
$I dyld disass $C --vaddr 0x1d3f54fc0 --count 6 --no-color --quiet   # an objc_msgSend$ stub; raw bytes in column 2 (also how the gli name table at 0x1d3f53350 was read)
printf '%s\n' ADDR... | xargs -P 8 -I{} sh -c "$I dyld a2s $C {} --cache ~/pixelios-artifacts/work/dsc/a2s.cache 2>/dev/null | grep '^0x'"
```
Work files were in /tmp/ca34 (not kept). AP-18/AP-19 obeyed (one whole-image disassembly, parallel `a2s` on a handful of stubs, no pointer-pattern searches).
