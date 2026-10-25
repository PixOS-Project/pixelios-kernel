# 37 - SpringBoard aborts on `BoundingPathBitmap-1125x2436-375x812-3.00x`: the GPU worktree's ADT has no `/product` node

Date 2026-10-24. Read-only research, no phone touched. iOS 16.7 (20H392) dyld shared cache, unslid addresses (same cache as research 33/34).
Tags: **[obs]** = observed in a run log / file I opened, **[obs-RE]** = disassembly or string dump of the cache or of the IPSW dmg
(`ipsw dyld disass/dump/a2s/str`, `tools/mg-key`), **[src file:line]** = source file, **[guess]** = inference. Nothing here is verified on
hardware after the fix: the fix itself is a **[guess]** until one phone run.

## 0. Summary (read this)

* The asset exists, in the right place, in the Apple catalog and in our root image. It is NOT a root/plan problem.
  Asset = `/System/Library/PrivateFrameworks/UIKitCore.framework/BoundingPathData.bundle/Assets.car`, facet AND rendition
  `BoundingPathBitmap-1125x2436-375x812-3.00x` both present. [obs-RE s.2]
* The ADT that `tools/xrun` built in the GPU worktree has **no `/product` node** (`research/ipsw-x/d22ap.adt` missing there, so `tools/mkadt.py`
  warns and skips it). Every GPU run since `u1` has this ADT; the iPhone GUI milestone runs (demo1b, demo2) had it. [obs s.4]
* CoreUI (the asset-catalog library behind `-[_UIAssetManager dataNamed:]`) picks renditions by the device traits it asks MobileGestalt for
  (`ArtworkTraits` -> idiom, subtype, memory class, graphics class); MobileGestalt fills them from `IODeviceTree:/product`. [obs-RE s.3]
  With no `/product` they are empty/defaults, so the lookup of this data rendition returns nil -> the NSInternalInconsistencyException. [guess for the last step]
* Fix (rank 1): link `research` into the GPU kernel worktree (AP-11/AP-16 again), rebuild the ADT, reflash nothing, rerun. Cost: one `xrun`.
  No root change, no screen-size change, no defaults change.
* The user's note says the runs used `ios-root3-inj2.img`: gputest10 did not (it ran `ios-root3-gpu7.img`; inj1/inj2 came later). It does not
  matter: all are `ios-root3.img` + a launchd.plist edit + `/usr` files. [obs s.4]

## 1. Where the asset comes from, how the name is built (question 1)

All [obs-RE] from UIKitCore (`ipsw dyld disass CACHE --image UIKitCore`), symbols from `ipsw dyld symaddr`.

1. `+[_UIScreenBoundingPathUtilities boundingPathUtilitiesForScreen:]` 0x18908965c asks `+[_UIScreenComplexBoundingPathUtilities isScreenSupported:]`
   (0x189089b38) and builds the Complex or the Rectangular subclass. `isScreenSupported:` = `[screen _isEmbeddedScreen]` && `[screen _screenType] != -1`
   && `_UIScreenCornerRadiusForType(type) != 0` (0x189089b44-0x189089b6c). No defaults key, no boot-arg, no env in these three functions.
2. `-[_UIScreenComplexBoundingPathUtilities initWithScreen:]` 0x1890896c8 -> `_loadBitmapForScreen:type:` 0x1890897c4 (the method named in the abort).
3. Name: `stringWithFormat:` with the cfstring at 0x1d5c3e408 = `BoundingPathBitmap-%ldx%ld-%ldx%ld-%.2fx` (bytes at 0x189f20b13), arguments
   = `_UIScreenNativeDisplayBoundsSizeForType(type)` (pixels, here 1125x2436), `_UIScreenReferenceBoundsSizeForType(type)` (points, 375x812),
   `_UIScreenScaleForType(type)` (3.00) (0x189089874). The args come from the type table, not from the live display; `type` = `-[UIScreen _screenType]`
   (0x18970e344 -> `_UIScreenTypeForScreen` 0x188e80260, which matches the screen's native bounds / reference bounds / scale against the table).
   A `-A` suffix (cfstring "A" 0x1d5c3e428, format `%@-%@`) is added only for types 0x11..0x16 and 0x24 (0x189089884-0x1890898a8): our name has none,
   so our type is not in that set. The numeric `type` value itself I did not decode (not needed).
4. Loading: `___UIKitSharedBoundingPathDataManager` (0x1891f4644, block 0x18908d570) creates the `_UIAssetManager` once:
   `[[_UIAssetManager alloc] initWithName:@"UIKit_BoundingPathData" inBundle:[NSBundle bundleWithPath:_UIKitResourceBundlePath(@"BoundingPathData.bundle")]
   idiom:[[UIDevice currentDevice] userInterfaceIdiom]]` (strings at 0x189f6bfa6 and 0x189f6bfbd; selectors resolved with `ipsw dyld a2s`).
   `_UIKitResourceBundlePath` = UIKitCore's framework dir + component (0x1891eccfc; the exact base call 0x189a0c2bc I did not name; the path below
   is confirmed by the file list in s.2).
5. `-[_UIAssetManager dataNamed:]` (0x189089bf0) tail-calls the CoreUI `CUICatalog` data lookup; `_loadBitmapForScreen` then calls `-data`, then
   `+unarchivedObjectOfClass:fromData:error:` for `_UIBoundingPathBitmap`.
6. The abort in our logs is the assertion at line 326 (`mov w5,#0x146` at 0x189089ae0, taken when `-data` is nil, `cbz x23` at 0x189089908).
   Line 324 (`#0x144`) would be "the bounding path data manager should never be nil" (string at 0x189f20991): that is NOT what we get, so the bundle and
   catalog were opened fine; only the lookup of this name returned nothing. [obs-RE; log text: gputest10 usbnet.log:3334-3337]

## 2. The asset exists, in Apple's catalog and in our image (question 2)

* Catalog in the OS dmg: `~/pixelios-artifacts/ipsw/087-86698-044.dmg` (apfs-fuse, read only, unmounted again), file
  `System/Library/PrivateFrameworks/UIKitCore.framework/BoundingPathData.bundle/Assets.car`, 101768 bytes, sha256
  `0a53453f1e096f70ccb38618b0b8c32fa950f71dc3b559c4ce556825517aa04d`, built "Xcode 14.3 (14E6097d) via AssetCatalogSimulatorAgent", bundle
  `com.apple.uikit.BoundingPathData`, version 16.7. [obs-RE]
* I parsed the BOM/CAR with a 40-line script (BOM vars `FACETKEYS`, `RENDITIONS`, `KEYFORMAT`). 29 facets, 23 renditions. Facets include
  `BoundingPathBitmap-1125x2436-375x812-3.00x` and `...-1125x2436-320x693-3.00x`, `1080x2340-375x812`, `1170x2532-390x844` ... up to iPad sizes.
  There is **no 1080x2400 asset** (nor any name for our real panel). [obs-RE]
* The rendition for 1125x2436-375x812 exists (CSI of 3354 bytes, key `01000100 664d 5500 b500` = scale 1, idiom 1, identifier 0x4d66, element 0x55, part 0xb5).
  Its CSI name field reads `CoreStructuredImage` (and a dynamic UTI `dyn.ah62d4rv4ge80k2pyqe`), unlike the `.data`-named siblings; the same is true for
  1242x2688-375x812, 828x1792-375x812/414x896, 1242x2688-414x896 (the iPhone X/XR/XS Max/11 family). Real iPhone X hardware reads these, so this alone is
  not a failure cause; keep it in mind if the fix in s.5 does not help. [obs-RE]
* `KEYFORMAT` = tokens 12 (scale), 15 (idiom), 17 (identifier), 1 (element), 2 (part): every rendition is idiom 1 (phone) except one `-A` iPad entry
  with idiom 0. So a lookup that carries a wrong/empty idiom or subtype cannot match. [obs-RE; mapping of token numbers to names is from my memory of
  CoreUI's `kCRThemeXxxName` enum: **guess**]
* In OUR images: `docs`-recorded listing `<tank>/pixelios/cmp2/LISTING.0` (image `ios-root-rootless2.img`, same tools/plan as ios-root3) lists
  `./System/Library/PrivateFrameworks/UIKitCore.framework/BoundingPathData.bundle/{Assets.car,BoundingPathData,Info.plist,_CodeSignature/...}`
  (mode 644 root). [obs] The root plan does not prune anything: `pixelios-userland/tools/ios-root-plan.py` only adds cryptex links, the dyld cache link and the
  sweet tweaks (header docstring lines 1-20); grep for `UIKitCore|PrivateFrameworks|exclude|prune` finds no filter. [src]
  `overlay-ios-root.sh` (used for gpu4..inj2) only rewrites `System/Library/xpc/launchd.plist` and adds `/usr/local/bin/*` or `/usr/lib/libsweetgl.dylib`
  (`pixelios-userland/tools/overlay-ios-root.sh:42-79`). [src]
* Direct check of `ios-root3-inj2.img` itself: NOT done. I started `tools/rootless/vm.sh run list` on it (one qemu guest) but the guest and its scratch dir (/tmp/r37g) had vanished when I came back, with no report. Presence in ios-root3-inj2.img is therefore **inferred** (same build tools and plan as the listed image, plan has no filter, overlay touches only launchd.plist and /usr), not measured. To settle: rerun `vm.sh run list -f keys.txt=KEYS -m 1024 ro:IMG report.bin` with the Assets.car path in KEYS and compare its sha256 with `0a53453f...`; ~20+ min under TCG, one heavy job.

## 3. What CoreUI asks the device (the link to the ADT)

* `-[CUICatalog dataWithName:appearanceName:]` (0x19ee98458) passes process-wide defaults (globals at 0x1e1e03ce8..d00) to
  `_dataWithName:deviceIdiom:deviceSubtype:memoryClass:graphicsClass:appearanceIdentifier:graphicsFallBackOrder:deviceSubtypeFallbacks:` (0x19ee985f4). [obs-RE]
* The `dispatch_once` block that fills them (0x19eea1064) calls MobileGestalt `ArtworkTraits` (obfuscated key `oPeik/9e8lQWMszEjbPzng`, present in CoreUI at 0x19ef45097)
  and reads the dictionary keys `ArtworkDeviceIdiom`, `ArtworkDeviceSubType`, `DevicePerformanceMemoryClass`, `GraphicsFeatureSetClass`, plus `ArtworkDisplayGamut`.
  The error strings in the same block: "CUICatalog: couldn't get 'kMGArtworkDeviceIdiom' from MGCopyAnswer got '%@'", "... 'kMGArtworkDisplayGamut' ...",
  "Failed to parse value of 'kMGArtworkGraphics...". [obs-RE: strings 0x19ef450a0..0x19ef45160, block disassembly]
* MobileGestalt side, `tools/mg-key ArtworkTraits` (run 2026-10-24, report `/tmp/r37mg/report.txt`): the compute function 0x19c57013c reads, from
  **`IODeviceTree:/product`**, the properties `product-description`, `artwork-device-idiom`, `artwork-device-subtype`, `artwork-scale-factor`,
  `artwork-dynamic-displaymode`, `device-perf-memory-class`, `graphics-featureset-class`, `graphics-featureset-fallbacks`, `compatible-device-fallback`.
  [obs-RE] (The two literal fallbacks `APPLE4` / `APPLE3:APPLE3v1:...` are in the same function; what it returns when the node is absent I did not trace.)
* Values the real D22AP (iPhone X) tree holds, read from `research/ipsw-x/d22ap.adt` with `tools/adt.py`: artwork-device-idiom `phone`, artwork-device-subtype
  `0x984` (=2436), artwork-scale-factor 3, device-perf-memory-class 4, graphics-featureset-class `APPLE4`, artwork-display-gamut `P3`. [obs]
* `mkadt.py` copies this node into the ADT (`tools/mkadt.py:46-59` `d22ap_product()`, `:244-247` builds the node and overrides `product-description` with
  "Pixel 6a"). Its `display-scale 3` (`:112`) and the `disp0` canvas 1125x2436 (`:205-206`) are separate and are present in both setups. [src]

## 4. Evidence that the GPU runs lost `/product` and the milestone runs had it

* [obs] `logs/runs/20261024T085342Z-gputest10/xrun.log:2`: `warning: .../kernel/tools/../research/ipsw-x/d22ap.adt missing, no /product node`.
  The GPU worktree has no `research/` (`ls research` fails; `tools/third_party` is a symlink, `research` is not). `.gitignore:15` ignores `/research/`.
* [obs] SpringBoard's own IORegistry probe: gputest10 `usbnet.log:1277` `IOK SpringBoard: path IODeviceTree:/product -> none`
  (12 such lines; backboardd 38) versus demo2 (iPhone GUI milestone, `20261022T203316Z-demo2/usbnet.log:1759`) `IOK SpringBoard: path IODeviceTree:/product -> IOPlatformDevice`
  (12 lines; backboardd 25).
* [obs] ADT size: all 22 GPU runs `A.adt` = 12852 bytes, none contains `artwork-device-subtype`; demo2's `A.adt` = 15404 bytes and contains the 70+ `/product` property names
  (`strings -a` diff: `product-name iPhone X`, `artwork-device-subtype`, `graphics-featureset-class APPLE4`, ... `product-description Pixel 6a`).
* [obs] Which runs hit the abort: gputest10 (534 hits), gputest16 (20), gputest17 (1364). Runs 1-9 and 11-15 either ended before SpringBoard finished starting
  (restart 600 s at cpus=1; SpringBoard needs ~800-1000 s at cpus=1 and ~340 s at cpus=8, see `t_boot` and the abort time) or lost backboardd first (12-15:
  `CRASH backboardd sig 11`, an injection problem, not this one). 8 and 9 failed to mount the root (AP-28). So the abort is present in every long run since the ADT lost `/product`; there is
  no long GPU run with a `/product` ADT to compare against. [obs]
* [obs] Roots: `~/pixelios-artifacts/flashed-userdata.txt:9-10` shows gputest10 (08:53:42Z) booted `ios-root3-gpu7.img`; inj1 from 09:31Z, inj2 from 11:38Z. All three are ios-root3.img + overlay (s.2).
* [obs] The abort is not related to the screen mode: the heap scan in the same log shows `currentMode = 375x812@3x (1125x2436/3)`, `pixelSize = {1125, 2436}`,
  `bounds = {{0,0},{375,812}}` (gputest10 usbnet.log after line 3334). The type table row is found; only the data lookup fails.

## 5. Fixes, ranked (question 3)

| # | Fix | Risk | Needs phone? |
|---|---|---|---|
| 1 | Make the GPU worktree's ADT include `/product`: `ln -s ~/.t3/projects/pixelios/pixos-project/pixelios-kernel/research ~/.t3/worktrees/pixelios-gpu/kernel/research`, check `python3 tools/mkadt.py ...` (or `xrun --dry-run`) no longer prints the warning and the ADT is ~15 kB with `artwork-device-subtype`, then `tools/xrun` as before. | none (same ADT the iPhone GUI milestone used; no root change, no reflash, kernel unchanged) | one xrun (~28 min at cpus=1, ~6 min of SpringBoard at cpus=8) to see SpringBoard survive past ~345 s (cpus 8) without `Failed to load bounding path` |
| 2 | Same as 1 plus a hard stop in `tools/mkadt.py`/`tools/xrun`: for `--board bluejay`/sweet, a missing `d22ap.adt` is an ERROR, not a warning (the cause of this loss was a silent `warning:` line at the top of a 28 min run). Propose as a tool change (TP) and an AP-11/AP-16 append. | low | none |
| 3 | Only if 1 does not fix it: give CoreUI the answer directly, i.e. nothing new; instead look at the `CoreStructuredImage` rendition (s.2) - for example compare `dataWithName:` behaviour with the 1080x2340 sibling by overriding the screen type. Needs a repro first; do not implement now. | medium | yes, several runs |
| rejected | (a) put the catalog into the root: it is already there. | - | - |
| rejected | (b) change the presented screen size or scale to another asset (`1080x2340` exists): the project presents 1125x2436 on purpose (patch 0061, `tools/mkadt.py:205-206`, research 03 H19) and touch/geometry code depends on it; no 1080x2400 name exists in the catalog at all. Even if tried, the lookup would face the same empty traits. | high | yes |
| rejected | (c) disable the complex bounding path: `isScreenSupported:`/`boundingPathUtilitiesForScreen:` read no default, boot-arg or env (s.1 item 1), only the screen type and its corner radius. Forcing `_screenType = -1` would need a code patch in UIKitCore (the cache is not ours to change) or an injected swizzle. | high | yes |

Consistency with the milestone "iPhone GUI on the Pixel 6a": the milestone was reached with `ios-root3.img` and the full ADT (demo1b / demo2, kernel 7b988aa,
`cpus=8`, `sweetfb_hz=30`, touch args; HANDOFF.md session of 2026-10-22 lines 51-75 in `~/.t3/projects/pixelios/docs/HANDOFF.md`). Fix 1 restores that ADT;
it contradicts nothing there. [obs: demo2 usbnet shows `SBCoverSheetPrimarySlidingViewController` transitions and SpringBoard `exec=1`, no CRASH]

## 6. Unknown (and what would settle it)

* That the missing `/product` is THE cause: it fits (CoreUI keys its lookup on those MG answers, the node is absent, SpringBoard probes it and gets "none", the milestone ADT
  had it and SpringBoard did not abort), but I did not trace what `ArtworkTraits` returns without the node, nor CoreUI's key matching. Settles it: fix 1 + one run, grep
  `Failed to load bounding path` = 0 and SpringBoard `exec` count 1 for the whole run (like demo2). A host-only partial check: none exists (CoreUI cannot run on the host).
* gputest10 vs gputest17 `A.adt`: same size, 64 bytes differ (probably the random seed; not checked).
* The numeric `_screenType` of our screen and why `-A` is not appended: not decoded; irrelevant for the fix.
* Whether `UISDeviceContext` (UIKitServices) supplies a second idiom to UIKit (`-[UIDevice userInterfaceIdiom]` goes through `initialDeviceContext` first, then `MGCopyAnswer(DeviceClassNumber)`,
  0x1891c46b0 / 0x18920e920): it does not reach CoreUI's lookup, which asks MobileGestalt itself, so I did not follow it. If fix 1 fails, this is the next thing to read.

## 7. Steps for the implementer (no code here)

1. In the GPU kernel worktree (`~/.t3/worktrees/pixelios-gpu/kernel`) create the symlink `research` -> the main kernel clone's `research` (gitignored; AP-11/AP-16). Also check `.env`, `tools/third_party`.
2. Dry-run the ADT build the way `tools/xrun` does (or `tools/xrun --dry-run`) and confirm: no `d22ap.adt missing` warning; the ADT is ~15 kB; `strings -a` shows `artwork-device-subtype`,
   `graphics-featureset-class`, `product-description` followed by `Pixel 6a`.
3. Hardware: one `tools/xrun` of the same kind as gputest17 (`--cpus 8 --restart 1500`, same boot args, root `ios-root3-inj2.img` already on userdata unless a hard reset happened: check
   `flashed-userdata.txt`/AP-28; reflash if the last run ended by a hard reset). Expect in `usbnet.log`: `IOK SpringBoard: path IODeviceTree:/product -> IOPlatformDevice`, zero
   `Failed to load bounding path`, one `exec SpringBoard`.
4. If that holds, record it in the GPU handover with run label and ADT size; add the AP entry (AP-11/AP-16 appended with 2026-10-24: a long GPU run lost ~30 hardware minutes x 3 runs to a
   `warning:` line) and write the TP: `tools/mkadt.py` exits 2 when `d22ap.adt` is missing for a board that needs the userland GUI.
5. If it does NOT hold: read `-[CUICatalog _dataWithName:deviceIdiom:...]` (0x19ee985f4) and the CSI `CoreStructuredImage` rendition path (s.2, s.5 row 3) before touching anything else.

## Method (to repeat)

`I=~/pixelios-artifacts/tools/ipsw; C=~/pixelios-artifacts/work/dsc/cache/dyld_shared_cache_arm64`
`$I dyld disass $C --image UIKitCore --quiet --no-color > uik.dis` (7 s); `$I dyld symaddr $C --image UIKitCore`; cfstring pointers: low 36 bits of the first quadword at +8
(`$I dyld dump $C ADDR --size N`); stubs: `$I dyld a2s $C ADDR`. Catalog: mount `087-86698-044.dmg` with `~/pixelios-artifacts/apfs-src/build/apfs-fuse` (a symlink named `*.dmg`),
copy the `.car`, unmount. CAR parser: BOM header (big endian), vars table, tree nodes (leaf = u16 isLeaf,u16 count,u32 fwd,u32 back, then (value,key) u32 pairs from +12), CSI name at +0x28.
`tools/mg-key ArtworkTraits --out DIR` for the MobileGestalt side.
