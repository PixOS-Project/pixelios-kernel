# 33 - Which renderer CoreAnimation picks (B8) and what its GLES renderer needs from EAGL/GLES (B9)

Date 2026-10-23. Build: iOS 16.7 (20H392) dyld shared cache, unslid addresses (same build as sailfish: its hard-coded
`0x188441a58`, `0x1d5b44a10` match). Read-only research, no phone touched. Extends research 18 s.1.2, s.3.3, s.4 (not repeated).
Tags: **[obs-RE sym/addr]** = read from the disassembly/strings of the cache (`ipsw dyld disass/macho/symaddr`), **[src file:line]** =
read from a source file, **[guess]** = inference. Nothing here is "observed on hardware" except where a prior port is cited.
Method note: whole-image disassembly of QuartzCore takes 1.5 s (`ipsw dyld disass CACHE --image QuartzCore --quiet`); addresses below are
vm addresses of that cache.

## 0. Summary

* The main display server is `CA::WindowServer::EAGLServer` (created by `AppleDisplay::new_server`, the only server type that
  runs on `AppleCLCDDisplay`). Its virtual `renderer()` is the whole selection logic. Order: **Metal -> GLES (ES 3 only) -> CPU `CA::OGL::SW`**.
  [obs-RE `AppleDisplay::new_server` 0x1883abea4 calls ctor 0x1883da11c (sets EAGLServer vtable 0x1d5b39ee0 at 0x1883da824); `EAGLServer::renderer` 0x1882eaf3c]
* Our phone ends on the **software renderer** because (a) there is no Metal device and (b) Apple's `EAGLContext initWithAPI:` cannot
  succeed: its GL plugin loader needs `IOGLESBundleName` on the IOAccelerator, which our empty `IOAcceleratorES` stub (patch 0084) lacks.
  (a) is [obs-RE], (b) is [obs-RE] for the code path and [guess] for the exact failing step (never sampled on hardware).
* Minimal change to get CA onto GLES: make `[EAGLContext initWithAPI:3 properties:]` return a context whose `API` is 3 and whose
  `getMacroContextPrivate` returns a GLI dispatch table. That is exactly what sailfish's `eagl.c` swizzle does [src ios-sailfish/tools/sweetgl/eagl.c:2205-2232].
  No boot-arg or defaults key chooses GLES; `CA_ENABLE_OGL=0` can only turn it off. Confidence: high (code path), medium (nothing else needed).

## 1. Renderer selection (B8)

### 1.1 Decision tree in `EAGLServer::renderer()` (0x1882eaf3c)

```
renderer():
  if cached renderer (this+0x320) and its context (this+0x318) is alive: return it
  dev = display->+0x108                       # cached Metal device
  if dev == NULL: dev = CAMetalContextCreate()   # may block 30 s, see 1.2
  if dev != NULL:                              # METAL
      ctx = CA::OGL::new_metal_context(dev,0,0,0,0,priority 4, label)   -> Renderer(ctx)
  else:                                        # GLES attempt
      if g_ogl_enabled (byte 0x1dcdf81b9) == 0: return NULL        -> SW
      eagl = cached display->+0x100
      if eagl == NULL:
          props = {kEAGLContextPropertyAccelerated: @YES, kEAGLContextPropertyClientRetainRelease: @YES}
          eagl = [[EAGLContext alloc] initWithAPI:3 properties:props]
          if eagl == nil: eagl = [[EAGLContext alloc] initWithAPI:2 properties:props]
          if eagl == nil: return NULL                              -> SW
          [eagl setParameter:0x260 to:&0]
      if [eagl API] != 3: return NULL                              -> SW   (ES2 is rejected)
      ctx = new CA::OGL::GLESContext(eagl); Renderer(ctx)          # GLES
```
Evidence: [obs-RE 0x1882eaf90-0x1882eb18c; stubs resolved with `ipsw dyld disass --vaddr`; selectors from `ipsw dyld macho QuartzCore --objc --objc-refs`:
0x1885ca440 = `initWithAPI:properties:` (selref 0x1d5b72a80), 0x1885cec40 = `setParameter:to:` (0x1d5b74b70), 0x1885c5f80 = `API` (0x1d5b71280);
the two property keys are `_ptr._kEAGLContextPropertyAccelerated` / `_ptr._kEAGLContextPropertyClientRetainRelease` resolved with `ipsw dyld a2s` at GOT slots 0x1dacc5be8/0x1dacc5bf0;
GLESContext ctor = `__ZN2CA3OGL11GLESContextC1EPv` 0x18840960c, Renderer ctor `__ZN2CA3OGL8RendererC1ERNS0_7ContextE` 0x1883cc7e8].
The GLES floor is **API == 3**: [obs-RE `cmp w0,#3; b.ne` at 0x1882eb120; the same check is in `gles_new_context` 0x188440b4c `ldr w8,[x0]; cmp w8,#3`].

### 1.2 What happens when no renderer comes back

`EAGLServer::render_update` (0x1883601dc) calls virtual slot 0x1a0 (= `renderer()`); a NULL result tail-calls `Server::render_update` (0x1884f4b58),
which calls `0x1884f4c54`: that function **creates the CPU renderer on first use** (calloc 0x4fe0, vtable `SWContext` at 0x1d5b317c8 = the same object
`sw_new_context` 0x1884409d0 builds, then `Renderer(ctx)`). [obs-RE 0x1884f4c54-0x1884f4cd8; default `Server::renderer` 0x1884f4c4c is `mov x0,#0; ret`]
So "SW" is not chosen by a flag: it is the fall-through when `renderer()` returns NULL. [obs-RE]

`CAMetalContextCreate` (0x18838421c): `dispatch_once(block 0x18853e5f8)` then `MTLCreateSystemDefaultDevice()`. The block calls `IOMainPort`, then up to
0x12c (300) times `IOServiceGetMatchingServices(IOServiceMatching("IOAcceleratorES"))`, `usleep(100000)` between tries: up to 30 s once per process when
no `IOAcceleratorES` service exists. [obs-RE 0x18853e5f8-0x18853e6d4, imports resolved by `ipsw dyld a2s`: `_IOServiceMatching_stub`, `_IOServiceGetMatchingServices_stub`, `_usleep_stub`]
Matches the topaz/sailfish comment [src ios-topaz/xnu/patches/0138-topaz-empty-ioacceleratores.patch:21-26] and our patch 0084 [src xnu/patches/0084-sweet-iokit-quiet-accelerator.patch:40-62]
(empty `IOAcceleratorES`, boot-arg `sweetaccel`, default on). With that stub the 30 s wait is gone and `MTLCreateSystemDefaultDevice` returns nil
[obs on sailfish/topaz hardware per their patch comments: "Metal passes over it"; [guess] that the same holds on bluejay, same cache].

### 1.3 Other users of Metal / GLES (so we know the boundary)

* Only `EAGLServer` has a GLES branch. `AsynchronousDispatcher::renderer` (0x188303040) and `VirtualServer::renderer` (0x1883cebb8) are
  `CAMetalContextCreate` -> `new_metal_context` or NULL. [obs-RE]
* `IOMFBDisplay::get_ctx` block (0x18853bfe8) builds the static Metal context for scaling/copies; with no Metal it stays NULL. [obs-RE]
* `CARenderer _initWithEAGLContext:options:` (0x1885495b4) is the public app API and calls `-[EAGLContext API]`; app-driven, not the compositor. [obs-RE]
* `CA_NO_ACCEL` is read in `CA::CG::AccelQueue::AccelQueue` (0x1883c5364+0xd8): CoreGraphics acceleration, **not** the display renderer. [obs-RE]

### 1.4 Knobs that do / do not matter

| Knob | Effect | Evidence |
|---|---|---|
| `CA_ENABLE_OGL` | read once in the `EAGLServer` ctor: unset -> 1; set -> `atoi(v) != 0`. Only "0" disables GLES (forces SW). Default is already "try GLES". | [obs-RE 0x1883da844-0x1883da874: `getenv`, `atoi`, `strb w8,[0x1dcdf81b9]`] |
| `CA_DISABLE_WORKAROUNDS`, `CA_MAX_TEXTURE_UNITS`, `CA_MAX_TEXTURE_SIZE`, `CA_DISABLED_EXTENSIONS` | GLES-context tuning, read in `GLESContext` ctor / `check_extension` | [obs-RE 0x1884096c4-0x188409710; 0x1884b66b4] |
| Preference keys | none found for the choice (only the env/IOKit above) | [obs-RE: no CFPreferences use in `renderer()`] |
| `MTL_*` env | irrelevant while no device exists | [guess] |
| boot-arg | none; env must come through `sweetenv` (research 18 s.4.3 #2) | [src docs/research/18-gpu-userland.md s.4.3] |

### 1.5 Why Apple's `EAGLContext` fails on our phone (the part not yet seen on hardware)

`+[EAGLContext initWithAPI:properties:]` -> `commonInitWithAPI:properties:` (OpenGLES 0x1d3f513f0) -> `eagl_init` (0x1d3f52444). `eagl_init`:
1. `CFBundle com.apple.opengles` -> `<path>/GLEngine.bundle/GLEngine`, `dlopen`; `dlsym gliInitializeLibrary`; env `GL_BORON`. [obs-RE 0x1d3f524e8-0x1d3f52588]
2. `IOServiceGetMatchingService("IOAcceleratorES")`, else `"IOAcceleratorESLegacy"`; if both are 0 it still calls `gliInitializeLibrary(ports=0,...)`. [obs-RE 0x1d3f525d4-0x1d3f52628]
3. `dlsym` of 18 more `gli*` entry points (name table at 0x1d3f53350, stride 0x20), then a probe call through table slot 16 that must return non-zero; any failure -> `eagl_init` returns 0 -> `initWithAPI:` returns nil. [obs-RE 0x1d3f52634-0x1d3f526dc]
4. Inside `gliInitializeLibrary` -> `gfxInitializeLibrary` (libGFXShared 0x1d3f6e2f0): per accelerator port it reads the registry properties `IOGLESBundleName`, `IOGLESMetalBundleName`,
   defaults `UseMetal` (domain `com.apple.opengl`) / `IOGLESDefaultUseMetal`, and loads the driver `"/System/Library/Extensions/%s.bundle"` (the AGX GLD plugin). With none of the properties the per-port code is skipped (`cbz x28, 0x1d3f6e70c`). [obs-RE 0x1d3f6e494-0x1d3f6e5fc and strings at 0x1d3f73f56-0x1d3f73fad]
Our `IOAcceleratorES` stub has no such properties, and no AGX bundle exists, so no GLD plugin loads and `eagl_init` fails. **[guess]** for the precise step; [obs-RE] that
the vendor driver is the closed GLD plugin selected by those properties. Providing the properties would only make Apple's loader look for a bundle we cannot supply, so do not go that way.

**Cheapest confirmation (one boot, no GPU):** `sweetenv`-inject a dylib that logs the return of `-[EAGLContext initWithAPI:properties:]` in backboardd, or sample backboardd for `CA::OGL::SW::*` frames (research 18 s.4.3 #5). Until then the SW claim is [guess] backed by [obs-RE] code.

## 2. EAGL API and GL surface that CA's GLES renderer uses (B9)

### 2.1 EAGL methods (all of CA's use, compositor side)

Selectors CA sends to `EAGLContext` in the render server (from the objc-refs of QuartzCore, callers resolved with `bl` targets): [obs-RE]

| Selector | Caller in QuartzCore | Shim must |
|---|---|---|
| `+alloc` / `initWithAPI:properties:` (API 3, then 2) | `EAGLServer::renderer` 0x1882eaf3c | accept `{Accelerated, ClientRetainRelease}`; return non-nil with API 3 |
| `-API` | `renderer`, `GLESContext ctor` 0x18840960c (+0x464), `gles_new_context`, `CARenderer` | return 3 |
| `-setParameter:to:` (pname 0x260, value 0) | `renderer` 0x1882eb10c | return YES (sailfish ignores) [src eagl.c:1784-1790] |
| `-getMacroContextPrivate` | `GLESContext ctor` +0x84 (0x188409690) | return `{rctx, disp[1036]}`; stored at `GLESContext+0x7f8` |
| `+setCurrentContext:` / `+currentContext` | `CARenderer render` 0x188549abc, `CATextLayer drawInContext:` | per-thread current |
| `-attachImage:toCoreSurface:invertedRender:` | `GLESContext::make_buffer_current` 0x188407980 (+0x148), `attach_surface_texture` 0x1884088e4 (+0x764) | bind IOSurface as texture storage (render target and sampled) |
| `-texImageIOSurface:target:internalFormat:width:height:format:type:plane:invert:` | `create_surface_from_iosurface` 0x188406e90 (+0x1f0), `attach_surface_texture` (+0x574) | same, for sampling |

Not used by the compositor (only apps / `CAEAGLLayer`): `renderbufferStorage:fromDrawable:`, `presentRenderbuffer:*`, `sharegroup`, `setBlockFence:onQueue:`, `isMultiThreaded`. [obs-RE: no stub call sites for them in QuartzCore text outside `CAEAGLLayer`/`CARenderer`]
`EAGLSharegroup` is not touched by CA's server path. [obs-RE]
Exact arguments: `make_buffer_current(WindowServer::Surface*)` gets the `IOSurfaceRef` from the surface's virtual `+0x98`, does `glGenTextures`, `glBindTexture(GL_TEXTURE_2D)`,
4x `glTexParameteri` (NEAREST, CLAMP_TO_EDGE), then `[eagl attachImage:tex toCoreSurface:iosurface invertedRender:NO]`, then `glGenFramebuffers/glBindFramebuffer(0x8d40)/glFramebufferTexture2D(0x8ce0)`. [obs-RE 0x188407a30-0x188407b20]
`create_surface_from_iosurface`: `texImageIOSurface:s target:GL_TEXTURE_2D(0xde1) internalFormat:GL_RGBA(0x1908) w h format:GL_BGRA(0x80e1) type:GL_UNSIGNED_BYTE(0x1401) plane:0 invert:NO`, after checking w,h <= `GL_MAX_TEXTURE_SIZE` (ctx+0x820). [obs-RE 0x188406f04-0x188407080]
`attach_surface_texture` also maps IOSurface formats to (internalformat, format, type): RGBA/BGRA8, 4444 (0x8033), 5551 (0x8034), 565 (0x8363), L/A/LA (0x1909/0x1906), RG8/R8 (0x822b/0x8229), RGBA16F (0x881a/0x140b/0x8d61), RGBA32F (0x8814/0x1406), Apple xr/ycbcr (0x8a65-0x8a68, YCbCr matrix via `Render::iosurface_ycbcr_matrix`). [obs-RE 0x188408b60-0x188408f88]
The framebuffer for the scanout buffer additionally gets an Apple-only call `glFramebufferParameteriv(GL_FRAMEBUFFER, 0x8a79, {0x8a7a | 0x8a7d})` chosen by a surface flag bit (0x10000), slot 1024 `glFramebufferParameterivAPPLE`. [obs-RE `GLESContext::set_surface` 0x18840771c-0x188407750]; meaning [guess] (colour-space/XR mode of the buffer).

### 2.2 How GL calls are made

CA never links GL. `GLESContext` calls through the GLI table: `ldr x8,[tbl,#8*(slot+1)]; ldr x0,[tbl]; blr x8` (`tbl[0]` = rctx; slot numbering = sailfish `ios-dispatch.txt`, e.g. slot 117 `glGetString` at +0x3b0, 104 `glGetIntegerv` at +0x348). [obs-RE 0x188409720-0x188409728, 0x188409aa4; src ios-sailfish/tools/sweetgl/ios-dispatch.txt:32-36]
Slots whose call sites I found in `GLContext`/`GLESContext` code (scan of `ldr+blr` through the `+0x7f8` table; **lower bound**, not exhaustive): [obs-RE slot scan of QuartzCore text]
`glBindTexture glBlendEquation glBlendFunc glClear glClearColor glClearStencil glCompressedTexImage2D glCopyTexSubImage2D glDeleteTextures glDisable glDrawArrays glDrawElements glEnable
glEnableClientState glFinish glGenTextures glGetError glGetIntegerv glGetString glPixelStorei glReadBuffer glScissor glTexImage2D glTexParameterf glTexParameteri glTexSubImage2D glViewport glActiveTexture
glVertexAttribPointerARB glEnable/DisableVertexAttribArrayARB glCreateShaderObjectARB glShaderSourceARB glCompileShaderARB glCreateProgramObjectARB glAttachObjectARB glLinkProgramARB glUseProgramObjectARB glDeleteObjectARB
glBindAttribLocationARB glGetUniformLocationARB glUniform1f/1i/4f/3fv/4fv glUniformMatrix3fv/4fv glBindFramebufferEXT glGenFramebuffersEXT glDeleteFramebuffersEXT glFramebufferTexture2DEXT glGenerateMipmapEXT
glFinishObjectAPPLE glLabelObjectEXT glClientWaitSync glFramebufferParameterivAPPLE` plus fence slots 779/781 (`glFenceSync(0x9117, 0)`, `glDeleteSync`) in `GLESContext::set_fence/delete_fence` (0x1884058b8/0x18840590c).
Also needed per sailfish (it had to hand-write them) [src ios-sailfish/tools/sweetgl/gen-dispatch.py:19-26]: `glGetObjectParameterivARB glGetInfoLogARB glGetAttachedObjectsARB glGetHandleARB glDiscardFramebufferEXT glGetShaderPrecisionFormatOES glReleaseShaderCompilerOES glShaderBinaryOES glGetIntegerIndexedv glTestObjectAPPLE glFlush`.
Fences: `supports_fences`=1, `test_fence` = `glClientWaitSync(sync, SYNC_FLUSH_COMMANDS_BIT, 0)` result mapped to signalled/not. [obs-RE 0x1884058b0-0x188405900] ES 3.0 sync objects: Mesa has them.

### 2.3 Queries, extension strings, limits CA performs at context creation

`GLESContext` ctor (0x18840960c): `glGetString(GL_EXTENSIONS 0x1f03)`, `glGetString(0x8a1e)` (second extension list; **not a standard enum**, Mesa answers GL_INVALID_ENUM/NULL, and `CA::OGL::search_extension_list` is NULL-safe, 0x1884b67dc `cbz x0`), `glGetString(GL_RENDERER 0x1f01)`;
`glGetIntegerv`: `GL_MAX_TEXTURE_IMAGE_UNITS 0x8872`, `GL_MAX_TEXTURE_SIZE 0xd33`, `GL_MAX_VIEWPORT_DIMS 0xd3a`, `GL_SUBPIXEL_BITS 0xd50`; `IOSurfaceGetPropertyMaximum` x3 (clamps max size). [obs-RE 0x18840971c-0x188409c80]
Hard failure only if `GL_MAX_TEXTURE_IMAGE_UNITS <= 2` ("CoreAnimation: unsupported graphics hardware; need three or more texture units", `GLESContext::update` 0x188405544). Everything else below degrades. [obs-RE 0x188405588-0x188405608]
`[ctx API] > 2` sets ES3 feature bits (0xe3800) in the flag word at `GLESContext+0x824`. [obs-RE 0x188409a70-0x188409a8c]
`check_extension(exts, exts2, name)` = whole-word match in list 1 else list 2, minus `CA_DISABLED_EXTENSIONS`. [obs-RE `GLContext::check_extension` 0x1884b6664]

| Extension CA checks | CA uses it for (flag bit at ctx+0x824) | Mesa panfrost (v9, GLES 3.1) | Evidence for Mesa column |
|---|---|---|---|
| `GL_APPLE_fence` | bit 0/1, finish_image | **no** (core ES3 sync instead) | not in `extensions_table.h` |
| `GL_APPLE_texture_filter_cubic` | bit 2 | no | not in table |
| `GL_APPLE_texture_ycbcr_basic_formats` / `_extended_formats` | bits 3,4 | no | not in table |
| `GL_APPLE_texture_xr` | bit 5 (wide-gamut buffers) | no | not in table |
| `GL_APPLE_tile_order_rot` | bit 6 | no | not in table |
| `GL_EXT_shader_framebuffer_fetch` | bit 7; generated shaders `#extension ... : require` + `gl_LastFragData` | **yes** (native) | [src mesa pan_screen.c:700-702 `caps->fbfetch = max_cbufs`, `fbfetch_coherent=true`; st_extensions.c:1634-1640 sets the extension when fbfetch >= max_render_targets] |
| `GL_EXT_texture_filter_anisotropic` | bit 8 | yes | [src pan_screen.c:918 `max_texture_anisotropy=16`] |
| `GL_EXT_texture_lod_bias` | bit 9 | yes (dummy_true on ES1 only per table; ES2 col `x`) -> **unknown on ES3** | [src extensions_table.h:EXT_texture_lod_bias `GLL, x, ES1, x`] |
| `GL_OES_standard_derivatives` | `fwidth()` in generated shaders, `#extension ... : require` | yes (core in ES3 shaders; extension string exposed) | [src extensions_table.h OES_standard_derivatives ES2] |
| `GL_OES_texture_half_float` / `_linear` | float image formats | yes | [src st_extensions.c:840-843] |
| `GL_OES_texture_float` / `_linear` | float formats | yes | [src st_extensions.c:840] |
| `GL_IMG_texture_compression_pvrtc` | PVRTC layer contents | **no** | not in table |
| `GL_KHR_texture_compression_astc_ldr` | ASTC contents | yes if format support (v9 has ASTC) | [src st_extensions.c:881-889]; hardware format support [guess] |
| `GL_TEXTURE_RECTANGLE` / `sampler2DRect` / `texture2DRect` | blur/filter shaders (text in QuartzCore) | **no** on ES | [src eagl.c:265-300, 468-469, 539-580] (sailfish emulates) |

Mesa glGetString must also report ES >= 3.0: [src docs/drivers/panfrost.rst:29] lists Valhall v9 (G57) at ES 3.1; G78 is not named there but is the same v9 arch [guess on G78 model support: see research 32].

### 2.4 Shader dialect CA generates

GLSL ES **1.00** text (`attribute/varying`, `gl_FragColor`, `texture2D`, `texture2DRect`, `precision mediump/lowp/highp`, `gl_LastFragData`, `fwidth`, `#define NOISE_SIZE %d`).
[obs-RE: `strings QuartzCore`: "attribute highp vec4 vertex_position;", "lowp vec4 d%d = gl_LastFragData[%d];", "#extension GL_EXT_shader_framebuffer_fetch : require", "#extension GL_OES_standard_derivatives : require", "q0 = texture2DRect(texture0, ...)"]
ES 1.00 shaders are accepted by an ES 3.1 context in Mesa; the `sampler2DRect`/`texture2DRect` ones are not, and four texture-function expressions ship without GLSL text (patched by sailfish at 20H cache addresses [src eagl.c:2150-2203]).
Sailfish also needed `FD_MESA_DEBUG=nofp16` because CA's mediump shaders broke on freedreno [src sweetgl.c:106-113]; whether panfrost needs a similar switch is open.

## 3. How the rendered surface reaches the display

* CA renders into IOSurface-backed `WindowServer::Surface` buffers created by `IOMFBDisplay::create_surface` (0x18837b104) -> `WindowServer::IOSurface::allocate_iosurface` (0x188384e74). GLES attaches them via `attachImage:toCoreSurface:` (s.2.1); the SW renderer writes them with the CPU. The display side is identical for both. [obs-RE]
* Present: `IOMFBDisplay::finish_update` -> `IOMobileFramebufferSwapBegin / SwapSetLayer / SwapEnd` (also `present_surface`, `set_all_layers_to_null`, `IOMFBFlipBook::swap_frame`), i.e. the IOMFB user client selectors our kernel `SweetFramebuffer` already implements for the CPU path. [obs-RE callers of the `_IOMobileFramebufferSwap*_stub` imports, via `ipsw dyld a2s` + `bl` scan]
* Therefore switching to GLES changes **nothing in the display path**; it changes who writes the pixels. Research 18 s.1.3 (import by IOSurface id, `SweetGPU` selector 12, LRU of imported resources, copy when pitch is misaligned, `glFlush == glFinish`, kernel-side copy/scale on SwapEnd) applies unchanged. [src docs/research/18-gpu-userland.md:93-108]
* Synchronisation: CA exposes ES3 fences (`set_fence/test_fence/delete_fence`, EAGLServer vtable wrappers 0x1883d9fd0-0x1883da0a4) but I did not find the swap path waiting on them. Without a fence the kernel swap could read a half-rendered buffer. Sailfish forces `glFlush`=`glFinish` for this reason [src eagl.c:1296-1340]. [guess] that the same is needed on Mali.
* Design consequences for SweetMali BO/IOSurface import (research 32 s.2.4):
  1. Each IOSurface used by CA (scanout buffers from `allocate_iosurface`, plus sampled layer surfaces) must be importable as a Mali BO by IOSurface id: wired, physically described pages mapped into the GPU VA space (no dma-buf).
  2. Both roles: render target (`attachImage`) and sampler (`texImageIOSurface`); one imported resource per (surface id, format, pitch) so writer and reader share it (sailfish LRU).
  3. Linear layout only for the scanout buffers (the kernel `SweetFramebuffer` swap copies/scans linear BGRA). Panfrost must render to a `DRM_FORMAT_MOD_LINEAR` resource; pitch alignment is a Mali requirement to be checked (Adreno needed 64 B) [guess].
  4. SwapEnd must not read the buffer before the GPU finished: either `glFinish` per frame in the shim (simple) or an explicit GPU fence the kernel waits on.
  5. The surface seed/purgeability protocol (sailfish patches 0131-0139) is part of what `SweetSurface` must keep answering; unchanged by GLES.

## 4. Prioritised TODO for the EAGL shim (first light)

P0 (nothing renders on GL without these)
1. Interpose `EAGLContext` (swizzle as sailfish: `initWithAPI:properties:`, `dealloc`, `API`, `setParameter:to:`, `getMacroContextPrivate`, `texImageIOSurface:...invert:`, `attachImage:toCoreSurface:invertedRender:`, `+setCurrentContext:`, `+currentContext`). API 3 must succeed; `API` returns 3. [obs-RE s.2.1; src eagl.c:2205-2232]
2. GLI table `{rctx, disp[1036]}` with the s.2.2 slots wired to Mesa `gl*`; unimplemented slots log once and return 0. Reuse `ios-dispatch.txt` + `gen-dispatch.py` as an idea only (no code copy; sibling ports are references [memory ios-bluejay rule]).
3. `glGetString(GL_EXTENSIONS)` must contain `GL_EXT_shader_framebuffer_fetch` (Mesa panfrost already does) and should not set a GL error for `glGetString(0x8a1e)` (return NULL silently). `GL_MAX_TEXTURE_IMAGE_UNITS >= 3` (Mesa: 16).
4. IOSurface import (render target + sampler), BGRA8888 first. Other formats from s.2.1 later.
5. `glFlush`/`glFinish` coherent with SwapEnd (s.3). Fences: Mesa `glFenceSync/glClientWaitSync` just work.
6. Make CA see no Metal and not wait: keep patch 0084 (`IOAcceleratorES` without `MetalPluginName`) [src xnu/patches/0084]. Keep `CA_ENABLE_OGL` unset.

P1 (visible correctness)
7. `GL_TEXTURE_RECTANGLE`/`sampler2DRect` emulation (rewrite or lower to 2D with scale) for the filter shaders. [src eagl.c:265-300]
8. Four texture-function expressions CA ships without GLSL text (needs a cache-address patch; refuse when bytes differ). [src eagl.c:2150-2203]
9. `glFramebufferParameteriv(0x8a79, 0x8a7a/0x8a7d)` accepted as a no-op; decide colour handling for the 10-bit "w30r" surface (open question 3).
10. `glFinishObjectAPPLE`, `glTestObjectAPPLE` -> `glFinish` / sync; `GL_APPLE_*` flags simply stay off.

P2
11. PVRTC (no Mesa support) and ASTC availability; mediump/fp16 behaviour on Mali; `glLabelObjectEXT` (Mesa has `EXT_debug_label`) can be forwarded or dropped.
12. App GL (`renderbufferStorage:fromDrawable:`, `presentRenderbuffer:`, `EAGLSharegroup`, `CAEAGLLayer` native window) - out of scope for first light.

## 5. Open questions (not settled)

1. What exactly returns nil in Apple's `EAGLContext` on our phone (s.1.5 step 3 vs 4)? Needs the hook/sample in s.1.5. [guess]
2. Who consumes the `{sw,null,gles,metal}_new_context` function-pointer table (slots 0x1d5b37c68-c78, 0x1d5b296d0; referenced from a 4-entry table at 0x1dcdfa4f0 that has no direct code references)? I did not find a direct caller; `renderer()` above does not use it, so it is probably the `CARenderer`/`CAContext` generic path. Unused by the compositor path as far as I traced. [guess]
3. Meaning of `glFramebufferParameteriv(0x8a79, 0x8a7a|0x8a7d)` and of `glGetString(0x8a1e)` (Apple private). [guess]
4. Which `texImageIOSurface` target does CA pass for rect textures? No `0x84f5` literal exists in QuartzCore text or data; sailfish still emulated rect, so some path reaches it (maybe via the IOSurface format table). [obs-RE negative result]
5. Complete list of GL entry points: the scan in s.2.2 is a lower bound (indirect calls through other registers are missed). Cross-check by tracing at the shim (`SWEETGL_TRACE`-style) on first boot. [obs-RE limitation]
6. Does the swap path wait on CA's fences (s.3)? Not found; needs a read of `IOMFBDisplay::finish_update` (0x1882d0f88 / `finish_update_` 0x1882ed310) beyond the call list.
7. Is `MTLCreateSystemDefaultDevice` nil with the 0084 stub on bluejay? Observed only on sailfish/topaz per their patch comments. [guess]
8. Panfrost on Mali-G78 (model id) in the pinned Mesa: research 32; `EXT_texture_lod_bias` on ES3 and ASTC format support not confirmed from source here.

## 6. Reproduce (all offline, no phone)

```
C=~/pixelios-artifacts/work/dsc/cache/dyld_shared_cache_arm64; I=~/pixelios-artifacts/tools/ipsw
$I dyld disass $C --image QuartzCore --quiet --no-color > qc.dis        # 1.5 s
$I dyld symaddr $C --image QuartzCore > qc.sym; $I dyld macho $C QuartzCore --strings --objc --objc-refs
$I dyld extract $C QuartzCore --output DIR                              # chained pointers: match low 5 bytes only
$I dyld a2s $C ADDR --cache ~/pixelios-artifacts/work/dsc/a2s.cache      # stub names (run with xargs -P 8, stdout only)
# images read: QuartzCore, OpenGLES, GLEngine (strings/imports only), libGFXShared, Metal (strings)
```
Work files were in /tmp/ca33 (not kept). Apple binaries and cache contents are not copied; only symbol names, short strings and conclusions are quoted.
