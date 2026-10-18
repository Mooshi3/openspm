# OpenSPM Aurora renderer adapter

This directory contains the optional Stage-A native GX renderer. It is deliberately outside the CPU/backend runtime: OpenSPM's recompiled game talks only to `openspm_gx_renderer_sink`, and the launcher loads the renderer as an ordinary PC-native component.

## Dependency policy

The audited foundation is upstream `encounter/aurora` at the revision recorded in `aurora.lock.json`. That upstream project is MIT licensed. Do not vendor Nintendo-derived game code or assets here. Do not copy WiiCompiled runtime/HLE code into OpenSPM; WiiCompiled is useful as a behavior reference, but its top-level runtime is GPL-3.0.

Aurora's `include/dolphin/...` paths are SDK-compatible API names from Aurora itself; they are not Dolphin Emulator source. OpenSPM nevertheless keeps all Aurora headers and types behind this adapter so neither the launcher ABI nor native SPM backend exposes them.

## Why upstream Aurora is not drop-in for original SPM FIFO

Aurora is primarily designed for source ports whose GX calls execute on the host. Super Paper Mario instead emits the original console command stream. At the pinned revision, OpenSPM must adapt these raw-console contracts:

1. **CP array bases (`0xA0..0xAF`)** — raw values are Wii guest/physical addresses. Aurora normally expects its custom `GX_AURORA_LOAD_ARRAYBASE` command containing a host pointer.
2. **Display lists (`GX_CMD_CALL_DL`)** — upstream skips nested raw display-list calls. OpenSPM resolves the guest address through the bounded memory mapper and recursively consumes the referenced list with a bounded nesting depth.
3. **Texture Image3 registers** — raw BP Image3 values encode texture source addresses; OpenSPM resolves them through guest memory and supplies the mapped host span to Aurora's texture path.
4. **TLUT source register (`0x64`)** — palette source data is likewise resolved through bounded guest memory before the `0x65` load consumes it.
5. **EFB texture-copy state (`0x49`, `0x4A`, `0x4B`, `0x52`)** — source coordinates/size, the shifted physical destination address, and the original hardware copy-format encoding must be reconstructed because Aurora's source-port path normally receives equivalent information through Aurora-only metadata.
6. **Display copy / presentation** — upstream raw BP display-copy handling is stubbed because source ports call Aurora frame APIs explicitly. OpenSPM converts the original game's display-copy event into a native presentation boundary without recreating a Wii VI device.
7. **Bounding box** — upstream `GXClearBoundingBox` and `GXReadBoundingBox` are TODO. OpenSPM adds renderer-owned raster coverage; the old diagnostic 64x64 value is forbidden in production.

## Guest-memory rule

The adapter never receives raw backend allocations. Each FIFO submission receives `openspm_gx_guest_memory`, whose `map` callback returns a bounded contiguous span. Every original-console address must go through that callback. The renderer rejects or loudly diagnoses an operation when the requested bytes do not fit the returned span.

The same resolver covers MEM1/MEM2 physical addresses and cached/uncached aliases. This is an OpenSPM platform boundary, not general-purpose Wii MMU emulation.

## Current raw EFB-copy adaptation

`apply_patches.py` installs handlers for original BP copy source (`0x49`/`0x4A`) and destination (`0x4B`). The physical destination is shifted back to a byte address and passed through `openspm_gx_guest_memory`. This is important because a later raw Image3 texture load maps the same guest address to the same host pointer, allowing Aurora's copy-texture cache to recognize that the texture came from an earlier EFB copy.

`apply_raster_patch.py` decodes the original `0x52` texture-copy control into Aurora's logical `GXTexFmt`. Common color/intensity/component formats and the primary depth-copy forms are handled. Unknown/unsupported combinations deliberately log and abort that copy rather than silently rendering with the wrong format.

One Stage-A caveat remains intentionally documented: raw hardware exposes copy stride/storage layout rather than the high-level destination rectangle Aurora's source-port API normally receives. The current adapter uses the exact EFB source-copy extent as the native copy texture size. That is the right first correctness model for ordinary copy-texture reuse, but a real SPM run must validate whether any effect relies on copying a smaller rectangle into a larger/padded backing allocation. If such a case exists, implement the storage/subrectangle semantics explicitly instead of guessing.

## Bounding-box implementation

The raster layer maintains four GPU-visible integer values (min X, max X, min Y, max Y), updates them from surviving fragment coverage after alpha-test discard, supports ordered PE clear/write operations, and synchronously reads them back at the game's observable bbox boundary. This is intended to replace the private 64x64 diagnostic fallback with renderer-owned raster feedback.

The implementation being present is not the same thing as runtime proof. Until a built persistent candidate reaches natural title/load using these values, `state/m14.json` keeps real-raster-bounds runtime proof false.

## Presentation rule

Keep SPM's simulation/retrace ordering intact. Stage A may conservatively close/submit frames at correctness synchronization boundaries while bring-up is ongoing. The stable presentation boundary should be the renderer-recognized original display-copy event. Later high-refresh work must interpolate renderer snapshots between completed simulation ticks rather than retiming gameplay or writing interpolated state back into game memory.

## Windows candidate packaging

The OpenSPM renderer target is built as `runtime/openspm_renderer.dll`. On Windows, CMake uses Aurora's own runtime-DLL staging helper so SDL/Dawn runtime DLLs and Dawn's dynamically loaded DXC components are copied beside the renderer. The strict candidate launcher and native backend are staged separately by OpenSPM.

The public candidate is audited before upload by `tools/release/audit_public_candidate.py`. The audit rejects disc images, DOL/REL/WAD payloads, extracted-disc/private-cache directories, and `openspm_local_native.dll`. The player-local generated native module is never part of the public renderer/runtime artifact.

## Completion gate for this adapter

The adapter is not considered ready merely because it compiles or opens a window. It must, from the persistent M14 runtime:

- consume fragmented original GX FIFO writes;
- resolve all guest-backed draw, display-list, texture, TLUT and copy-destination data through `openspm_gx_guest_memory`;
- execute SPM's observed EFB texture-copy cases correctly;
- render the natural boot/title frames;
- return real raster bounds;
- preserve draw-sync and delayed draw-done behavior already owned by `openspm_gx_platform`;
- present on a correct GX-aware frame boundary;
- advance through title input to `SEQ_LOAD` without the 64x64 fallback;
- contain no Nintendo-derived files and no Dolphin Emulator dependency.
