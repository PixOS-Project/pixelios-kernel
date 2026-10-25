# 36: backboardd SIGSEGV inside libsweetgl (run 20261024T105116Z-gputest13)

Date 2026-10-24. Dylib analysed: `~/pixelios-artifacts/sweetgl-clear-test7/libsweetgl.dylib` (not stripped: 27630 local symbols; sweetgl code is -O0, Mesa is optimised).
Cache: iOS 16.7 (20H392) at `~/pixelios-artifacts/work/dsc/cache/dyld_shared_cache_arm64`. Tags: [obs-RE] read from disassembly (Python Mach-O reader + capstone, `ipsw dyld a2s/disass`), [src] read from source (file:line), [guess].
Offsets are dylib vm offsets (== file offsets, `__text` starts 0x4c20).

## 1. The two libsweetgl frames (confirmed in the binary)

Method: scanned every `lr_off` with `lr_off & 0x3fff == 0x0c3c` and `lr_off + 0x5b02c0` in `__text` with low bits 0x0efc, checked the instruction before lr. Two blr hits (irrelevant Mesa C++ code) and one structurally perfect hit:

| role | dylib offset | symbol | fact |
|---|---|---|---|
| lr (frame 1) | 0x8c3c | `_w_98 + 0x60` | `8c38 bl _mesa_GenTextures` then `8c3c ldp x29,x30` = return address [obs-RE]. `w_98` is the generated GLI wrapper of slot 98 = glGenTextures [src gli_slots.txt:41]; it loads `sweet_gli_ensure_current` (0x12fdcd0), calls it with rctx (x0) if non-NULL, then calls Mesa [obs-RE 0x8bf8-0x8c38] |
| pc (frame 0) | 0x5b8efc | `_create_textures + 0x34` | `ldr x25,[x0]` with x0 = ctx = NULL (far 0x0) [obs-RE] |

Why lr is the wrapper and not `_mesa_GenTextures`: `_mesa_GenTextures` (0x5b9074) is `GET_CURRENT_CONTEXT` (TLS descriptor 0x12bf618 = `_mesa_glapi_tls_Context`, `blr x8; ldr x0,[x0]`) followed by a TAIL CALL `b _create_textures` (0x5b90b4) [obs-RE]. So ctx = Mesa's thread-local current context = NULL, passed straight into create_textures, which dereferences it. Both crashes are the same code (pc-lr identical). Load bases: crash 1 base = lr - 0x8c3c = 0x106208c3c-0x8c3c = 0x106200000; crash 2 = 0x10168cc3c-0x8c3c = 0x101684000 (16 KB aligned, consistent) [obs-RE arithmetic].

## 2. Shared-cache frames (unslid = addr - 0x2ff0c000; `ipsw dyld a2s`, stdout only, xargs -P 8, per AP-18)

| bt | unslid | symbol [obs-RE] |
|---|---|---|
| 2 | 0x188407a48 | `CA::OGL::GLESContext::make_buffer_current(WindowServer::Surface*) + 200` |
| 3 | 0x1882e1cc4 | `CA::WindowServer::Display::render_display(OGL::Renderer&, Render::Update*) + 500` |
| 4 | 0x18836021c | `CA::WindowServer::EAGLServer::render_update(Render::Update*, unsigned&) + 64` |
| 5 | 0x1884f5d60 | `CA::WindowServer::Server::render_for_time(...) + 3952` |
| 6,7 | 0x1882ba438 / 0x1882bae38 | `IOMFBServer::vsync_callback + 588` / `display_timer_callback(__CFMachPort...) + 368` |
| 8-10 | 0x186e5cde4 .. | `__CFMachPortPerform`, `__CFRUNLOOP_IS_CALLING_OUT_TO_A_SOURCE1_PERFORM_FUNCTION__`, `__CFRunLoopDoSource1` |

make_buffer_current disassembly (`ipsw dyld disass --symbol`, 0x188407a30-0x188407a48) [obs-RE]: `x24 = [this+0x7f8]` (the cached getMacroContextPrivate table), `x8=[x24+0x318]`, `x0=[x24]` (rctx), `w1=1`, `x2=&tex`, `blr x8`; return address 0x188407a48. 0x318 = 8*(98+1) = slot 98 glGenTextures, matching the layout in gli_table.h:13. So this is the first GL call CA makes on the new context (glGenTextures(1,&tex) then glBindTexture, then attachImage, per research 33 s.2.1), made from the render thread (vsync_callback, a CFMachPort source), not from the thread that created the EAGLContext [obs-RE for the thread of the call chain; that the creating thread differs is [guess]].

## 3. What is NULL, and why (src + obs-RE)

Confirmed: the *Mesa thread-local current context is NULL* when CA's glGenTextures wrapper runs. Not a NULL table slot (the call reached `w_98` through `[x24+0x318]`; a NULL table would fault at far 0x318), not the `_private`/rctx (the wrapper was entered), not a backend vtable entry.

Facts that narrow it:
* The hook is installed: `sweet_eagl_install` stores 0x5a74 (`sgl_ensure_current`) into 0x12fdcd0 at 0x6c08-0x6c18 before the swizzles [obs-RE]; the log line "swizzles installed" proves install returned 0 [obs run].
* `sgl_ensure_current` (0x5a74): if `tl_cur == rctx` return, else tail-call `sgl_set_current` [obs-RE; src eagl_core.c:116-120]. It IGNORES the return value. `sgl_set_current` returns -EINVAL (not live), -EBUSY (bound on another thread), -EIO (`be.make_current` failed) and in every case Mesa keeps no context [src eagl_core.c:98-113, comment at 119]. Nothing in the wrappers guards a NULL Mesa context, so any such silent failure becomes this SIGSEGV [obs-RE w_98 has no check after the hook].
* `be_make_current` = `st_api_make_current(bctx, NULL, NULL) ? 0 : -EIO` [src eagl_mesa.c:238-241]. In the binary (0x62ada8) NULL/NULL takes the `_mesa_get_incomplete_framebuffer` path then `_mesa_make_current(ctx, incomplete, incomplete)` (0x62af8c-0x62afa0), and `_mesa_make_current` (0x457c58) sets the TLS via `_mesa_glapi_set_context` (0x3cc8a8, same TLS 0x12bf618) and has no later path that clears it [obs-RE]. So IF it runs and returns true, Mesa's current is set; the segfault then means it did NOT run to success.

## 4. Root cause (confidence: crash mechanism HIGH; which failure branch LOW-MEDIUM)

Crash mechanism [obs-RE, high]: glGenTextures on a thread whose Mesa TLS context is NULL -> `create_textures(NULL,...)`.
Most likely cause [guess, ~50%]: `sgl_ensure_current(rctx)` returned an error that was swallowed. Candidates in order: (a) `be.make_current` -> `st_api_make_current` false -> -EIO (e.g. `_mesa_make_current` refusing with the incomplete framebuffer, or a Mesa state not yet initialised for a surfaceless ES3 context); (b) -EBUSY/-EINVAL; (c) the hook returned success without binding because `tl_cur == rctx` on this thread while Mesa's TLS is NULL (needs another writer of Mesa's TLS; nothing found, ~5%).
Not supported by evidence: hook NULL (disproved), table NULL (disproved), wrong dylib (offsets match a unique structure).

## 5. Smallest fix, alternatives (do not apply before the logging below says which)

1. Make the failure visible and non-fatal where all callers route (gen_gli.py wrapper template + the specials): after `SWEET_GLI_ENSURE(rctx)`, `if (!_mesa_glapi_get_context()) return 0;` (needs the same arity-safe default as traps). This turns the crash into a logged "no context" and lets CA proceed with texture 0. It is a guard, not the root fix.
2. Root fix in the failing branch once known: if -EIO, fix `be_make_current`/context creation (e.g. pass a real surfaceless drawable or call `st_api_make_current` right after `st_api_create_context` on the creating thread); if -EBUSY, the context was bound on the creating thread and the render thread cannot take it: unbind (set_current(NULL)) on the creator after create.
3. Alternative: make the context current inside `m_init` / `m_macro` (creating thread) and have `sgl_ensure_current` migrate it (drop bound on the old thread) instead of failing with -EBUSY.

## 6. Logging that discriminates (each line via open("/dev/null")+write like `slog` in dylib_main.m:38-39 / gl_clear_test.c)

* L1 `sgl_ensure_current`: `ens rctx=%p tl_cur=%p tid=%p` before; after `sgl_set_current`: `ens r=%d mesa_ctx=%p` (`_mesa_glapi_get_context()`). r=-5 -> (a); -16 -> (b EBUSY); -22 -> EINVAL; r=0 with mesa_ctx NULL -> (c) / TLS issue. No L1 line at all -> hook not reached.
* L2 `be_make_current`: `mk bctx=%p ret=%d mesa_ctx=%p`.
* L3 `m_init`: `init c=%p b=%p tid=%p`; `m_macro`: `macro table=%p rctx=%p`. Compare tid of init vs ens (thread difference supports (b)).
* L4 in `be_create_context`: `create st=%p err=%d`.
Run once on the phone, read /dev/null writes from the usbnet log; one run discriminates.

## Unknowns
Actual return of `sgl_set_current` at runtime (not observable offline); the thread that created the EAGLContext; why gl_clear_test (CLI, creates and makes current on one thread) worked in CI-link only, never on hardware [src eagl_mesa.c:93 comment].
Helper scripts were throw-away in /tmp/rv36 (not kept); AP-18/AP-19 followed, no new antipattern.

---

# Second crash (gputest14)

Run `logs/runs/20261024T113802Z-gputest14/usbnet.log` lines 1242-1245. Same method and cache as above; AP-18/AP-19 followed (whole-image disassembly of IOSurface once, `ipsw dyld a2s/symaddr/disass`, bottom-up only from the faulting function). Slide 0x167c0000, so unslid = addr - 0x167c0000 (note: different slide from the first crash, 0x2ff0c000). Tags: [obs] run log, [obs-RE] read from the cache disassembly, [src] source file:line, [guess].

## Verdict (short)

The first crash (NULL Mesa context) is gone. This one is NOT in libsweetgl or the EAGL shim: it is in IOSurface.framework, reading an `IOSurfaceClient` that was built from an all-zero reply of **IOSurfaceRootUserClient selector 6 (WrapClientImage)**, which our kernel (xnu patch 0046) answers with success + zeroed output because it falls into the `default:` "accept and ignore" branch. Root cause confidence: high (~85%). The one soft link is which caller issues selector 6 (not traced, see Unknowns).

## 1. Symbolicated frames [obs-RE]

| frame | unslid | symbol |
|---|---|---|
| pc | 0x1d1b01e70 | `__platform_memmove + 176` (libsystem_platform) |
| lr | 0x1a3983ffc | `_IOSurfaceClientGetBulkAttachments + 68`, return address of `bl memmove` at 0x1a3983ff8 |
| bt1 | 0x18833765c | `CA::Render::iosurface_get_edr_factor(__IOSurface*) + 72` (return of `bl IOSurfaceGetBulkAttachments` stub 0x18dbcc5bc -> 0x1a3983030) |
| bt2 | 0x18835538c | `CA::WindowServer::IOSurface::wrap_buffer(Display*, __IOSurface*, bool, bool) + 236` |
| bt3 | 0x1884088cc | `CA::OGL::GLESContext::retain_image(Image*, Render::Texture*, bool) + 312` |
| bt4 | 0x188406078 | `CA::OGL::GLESContext::bind_image_impl(...) + 424` |
| bt5 | 0x188327448 | `CA::OGL::Context::bind_image(...) + 260` |
| bt6 | 0x1882efafc | `CA::OGL::render_contents_background(Renderer&, Layer const*) + 4380` |
| bt7,9 | 0x1882d8bf8 / 0x1882d8c48 | `CA::OGL::LayerNode::apply(float, Surface**, float*) + 80 / +160` |
| bt8 | 0x1882d593c | `CA::OGL::ImagingNode::render(RenderClosure*, unsigned) + 664` |
| bt9 | 0x1882e8344 | `CA::OGL::render_layers(Renderer&, Layer*) + 332` |

So the render thread is now past `make_buffer_current` (glGenTextures etc. worked) and is drawing the first layer: `bind_image` -> `retain_image` -> `wrap_buffer` (which wraps an IOSurface for the texture) -> `iosurface_get_edr_factor` -> `IOSurfaceGetBulkAttachments` -> memmove. No libsweetgl frame is on the stack at all.

## 2. What is NULL at 0x30 [obs-RE]

`_IOSurfaceClientGetBulkAttachments` (0x1a3983fb8-0x1a3984008): `x0` = client, `x1` = out buffer, `x2` = &size; size must be <= 0x70 else error 0xe00002bc. Then `ldr x8,[x0,#0x70]; add x8,x8,#0x30; mov x0,x1; mov x1,x8; bl memmove(out, [client+0x70]+0x30, 0x70)` (memmove length is the in-out size, 0x70 from iosurface_get_edr_factor at 0x188337648). `far 0x30` therefore means **`[IOSurfaceClient + 0x70] == NULL`**: the pointer to the shared "state page" (where the bulk attachments live at +0x30..+0xa0) is NULL. The client itself is non-NULL (a NULL client would fault at 0x70). Stack layout makes pc read the source, so the source pointer is NULL+0x30.

Where `client+0x70` comes from (only one place builds a client): `_ioSurfaceClientCreateWithLockResult` (0x1a3980bc8) callocs `0xad0 - X` bytes (X = 0x980 if reply[0x38] == 0 planes else 0xa00 - planes*0x80) and does `memcpy(client+0x68, reply, 0xa68 - X)` (0x1a3980c30-0x1a3980c3c). There is no other store to `[xN,#0x68/#0x70]` of a client in the whole image (grep of the full disassembly). So `client+0x70 = reply+8`, i.e. the lock-result field our kernel fills with `ref->state->getAddress()` [src xnu/patches/0046-sweet-iosurface-iomfb.patch sweet_surface_fill_result: `put64(out, 0x08, ref->state->getAddress())`]. Other accessors confirm the layout: GetWidth reads `client+0x88` (= reply+0x20 = width), GetHeight `+0x8c`, GetPixelFormat `+0x98` [obs-RE 0x1a3984c8c, 0x1a3985004, 0x1a3984ba4], which is why `wrap_buffer` ran its width/height/format calls (0x1883552e8-0x188355300) without faulting: they read plain ints, and only the bulk-attachment read follows `+0x70`.

For surfaces made by selectors 0/4/34 our kernel fills +8 (state page, mapped read-only), so those clients are fine (and the state page is zero so edr factor = 1.0, no crash). The remaining way to get `+0x70 == 0` is a reply that is all zeros.

## 3. Which reply is all zeros: selector 6 [obs-RE + src + obs]

The framework has five callers of `_ioSurfaceClientCreateWithLockResult`: 0x1a3981190 (create, sel 0), 0x1a3982bb4 (lookup), 0x1a3982de8 (lookup from port, sel 0x22 = 34), **0x1a3986794 (sel 6)**, **0x1a3986900 (sel 7)**.
* Sel 6 is `_IOSurfaceClientWrapClientImage` (0x1a3986580): `IOConnectCallMethod(conn, 6, scalars=NULL/0, struct in = 0x20 bytes, struct out 0xa68)`; the input struct (built at fp-0x48) is `{u64 ptr = x5; u32 w0; u32 w1; u32 w2 = pixelFormat (only a whitelist of formats accepted: BGRA 0x42475241, 'L555'/'L565'-class 16 bit, 420 etc. compared at 0x1a39865f0-0x1a3986728); u32 2; u32 w3; u32 w4}` [obs-RE 0x1a3986730-0x1a3986778]. The argument roles (w0/w1 = width/height, w3/w4 = element size / row bytes) are [guess]; ptr is a client-memory address (the name says wrap CLIENT image).
* Sel 7 is `_IOSurfaceClientWrapClientMemory` (0x1a3986834): 2 scalars (x0 = address, x1 = size), struct out 0xa68 [obs-RE 0x1a39868a8-0x1a39868f0].
* Kernel: patch 0046 only implements 0, 4, 13, 34, 35. Everything else goes to `default:` which zeroes the scalar and struct outputs and **returns success**, logging `sel N` once [src patch 0046 externalMethod `default:` branch]. A success status plus a zero reply makes `WrapClientImage` hand back a client with base 0, +8 = 0, width/height/format 0 (`ldr w19,[sp,#0x38]` error path is only taken when the create-from-result returns NULL, which it does not: it callocs and copies).
* [obs] the log shows `SWEET SURF: backboardd sel 6` as the very last kernel line (run log line 1240) before the CRASH lines; the "once per selector" log means this is the first sel-6 call in the boot, and sels 9, 27, 5 (also default-branch, harmless so far) precede it. No `sel 7` line exists. All 16+ identical crashes follow from CA retrying.

Chain: CA hands the GL path a CPU-backed image (client memory); IOSurface (CoreSurface) wraps it with sel 6; our kernel returns "success, zeros"; `wrap_buffer` reads the IOSurface's width/height (0) then the bulk attachments through the NULL state pointer -> SIGSEGV. (Even with the guard added, a zero-size surface would be useless: the wrap has to produce a real surface.)

## 4. What must be provided; smallest fix

Nothing in libsweetgl / eagl_objc.m / eagl_private.h / gli_*.c is involved [obs-RE: no sweetgl frame; src: eagl_objc.m import_surface only runs for surfaces CA already holds]. The fix is kernel side, in `SweetSurface.cpp` (patch 0046 + 0066 base; a NEW patch, next free number after 0111 must be chosen by the driver, patches are contiguous):

1. **Minimum to remove the crash, not the cause:** make selectors 6 and 7 return an error (`kIOReturnUnsupported`) instead of success-with-zeros. The framework then takes `IOConnectCallMethod != 0` -> `WrapClientImage` returns NULL (error paths 0x1a39867f0 / 0x1a398695c, log via `os_log` code 5) and the CA caller sees a NULL surface. Whether CA then falls back or crashes elsewhere is unknown [guess, ~50% it falls back, since callers of IOSurfaceCreate-like APIs check NULL].
2. **Real fix (recommended):** implement sel 6 as "create a SweetSurface of the given size/format and fill it from the client's memory": `copyin` `h*bytesPerRow` bytes from `ptr` in the caller task into a new kernel-owned surface (the same path as `create()`, i.e. `sweet_surface_layout` equivalent + `IOBufferMemoryDescriptor::inTaskWithOptions` + `sweet_surface_publish` + `returnSurface(s, args)`), so the lock result has base, state page (+8), id, size/format. Snapshot semantics (later client writes are not seen) is acceptable first; true aliasing needs a descriptor over user pages and `addRef` support for `memory == NULL` (not recommended now). Same for sel 7 (address, size -> a linear surface of `size` bytes, with CA-supplied geometry unknown [guess]). The input struct for sel 6 must be decoded as above; field roles w0..w4 are [guess] and MUST be verified before implementing (AGENTS.md rule 7): disassemble the three callers of `_IOSurfaceClientWrapClientImage` / `IOSurfaceCreateWithClientImage`-style public wrappers (symbols near 0x1a3986580), or log the 0x20 input bytes on one run.
3. Defence in depth (cheap, correct for all selectors): in the `default:` branch do not return success for any selector that carries a 0xa68-byte struct output (`args->structureOutputSize == 0xa68`): return `kIOReturnUnsupported` so a zero lock result can never be turned into a client again.

## 5. Discriminating log lines

* Kernel (the cheap one-run check, `IOLog`): in `default:` branch log `sel=%u scIn=%u stIn=%u stOut=%u` and hexdump the first 0x20 input bytes for selectors 6 and 7 (this gives ptr/w0..w4 and settles the roles). Expect `stIn=32 stOut=2664` for sel 6.
* Userland (`write()` to /dev/null style like `slog`): in `eagl_objc.m` `import_surface` log `id=%u w=%u h=%u fmt=0x%x bpr=%u alloc=%lu base=%p` before use. For the crash path it never runs (the fault is earlier, in CA), but for the next layer it will show whether surfaces are real (w/h non-zero). A zero-sized `w=0 h=0` line would confirm the sel-6 zero-reply theory had the guard been applied without the real fix.
* Alternative offline check: CA's own log is not available; but the crash register dump `far 0x30` already matches [client+0x70] == NULL exactly (pc/lr/far all consistent with 0x1a3983fe8-0x1a3983ff8).

## Unknowns

* Which CA code path issues selector 6 (CA::Render image from client memory, e.g. a backing store `CGImage`/`CAImageQueue` style) and with which dimensions/format: not traced (the sel-6 call happens before `retain_image` returns; the wrap caller is above wrap_buffer's image creation, `bl 0x1884740a8` in retain_image, not resolved).
* Roles of w0..w4 in the sel-6 input struct and whether the format is BGRA or an L555-class one (a whitelist is applied at 0x1a39865f0-0x1a3986728).
* Whether the next failure after fixing this is in libsweetgl (`import_surface` -> SweetMali selector 5 for a surface with copied pixels) rather than CA.
* Why the earlier-tagged `attachImage` first-call chain from the first crash did not reach `wrap_buffer`: the first crash happened before any layer was drawn.
