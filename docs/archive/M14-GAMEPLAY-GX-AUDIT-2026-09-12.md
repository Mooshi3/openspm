# M14 gameplay GX audit — current head 7eb2d12fbcee

Audited: `Mooshi3/spm-recomp`, `m14-native-execution`, **7eb2d12fbcee4511b7ccc2149a6220d1445933c4**.
Runtime evidence: player-provided **launch(20260912-182613).log**.
Aurora: **749d6ee7a22bdfab78c8ece9047bca5d79aa72ca**, with the current OpenSPM adapters.

This supersedes the earlier audit's prioritization, not its historical evidence. It is an architectural audit and implementation handoff; no runtime repair is claimed. Keep native execution, player-local game code/assets, 218 generated chunks, 34 hosted targets + 5 inline semantics, and existing saves. Do not use Actions minutes.

## Main conclusions

1. **The position-array crash has a concrete empty-draw explanation.** The raw path admits zero vertices, computes zero requirements, then requires a nonzero position prefix. An isolated C++ probe executing the actual extracted scanner/uploader loop reproduces the assertion. The log does not record the failing count, so this is the strongest source-supported hypothesis, not player-runtime proof.
2. **Raw vertex invalidation is still broken.** Opcode 0x48 is a no-op in the final patched parser. Cached prefixes and merge eligibility survive it. The per-frame content cache cannot repair this because its comparison is bypassed when the per-slot range already covers the draw.
3. **The array adapter and shader disagree about NBT3 bounds.** The scanner budgets one vector at each index, while the shader adds binormal/tangent component offsets. An actual extracted-scanner fixture produces 120 staged bytes for a shader access ending at byte 144.
4. **SU scaling has concrete relevance in this capture.** Many captured draws bind a 16x16 texture with SU 48x32. Ordinary sampling uses unscaled post-texgen UV. Missing binding is not the general explanation for these 512 draws.
5. **EFB/presentation and completion remain conflated.** This is especially relevant to disappearing layers and objects appearing during scene construction.
6. **Staging is expensive, but the measurement and capture were overstated.** The timing is elapsed wall time inside the helper; the capture belongs to an earlier ~7.9 MB frame, not the later ~31.6 MB frames or the crashing draw.

## What the latest log actually establishes

The log identifies the requested head on line 1. The native stack identifies `push_gx_draw` called from `draw_prim`, then recursive `process` calls. The fatal is the missing-prefix assertion, not the previous fixed-buffer-capacity abort.

Generation 4 capture:
- Arms at Aurora **frame 12539**, after 7,338,430 bytes have already been staged.
- Records **512 draws and 512 sampled texture records, all bind=1**.
- Hits the 512-draw cap at log line 14565; it is not a complete frame.
- Contains no EFB-copy records in that captured suffix. That does not establish that the whole frame had no copies.
- 368 texture records are 16x16; the final repeated material uses SU 48x32.
- The first ~31.6 MB timing sample belongs to **frame 12559**, 20 Aurora frames later.
- The fatal occurs after the later profile samples, with no failing-draw VCD/VAT/count snapshot.

Representative measurements:

| Aurora frame | Staged bytes | Entries / calls | Reuse hits | Helper elapsed time |
|---|---:|---:|---:|---:|
| 12539 | 7,893,982 | 542 / 558 | 16 | 3.534 ms |
| 12541 | 7,598,668 | 561 / 577 | 16 | 3.500 ms |
| 12559 | 31,809,240 | 856 / 929 | 73 | 22.234 ms |
| 12560 | 31,644,824 | 854 / 927 | 73 | 20.803 ms |
| 12586 | 31,644,824 | 854 / 927 | 73 | 22.949 ms |

These numbers are bytes: 31,644,824 bytes is **30.18 MiB**, not 31.6 MiB. Host/guest elapsed time remains approximately 1:1 while retraces fall, including later samples below 19/sec.

The `cpu_us` probe uses `std::chrono::steady_clock`, not a thread CPU clock. It measures time elapsed around cache search, comparisons, allocation, staging copy, snapshot copy and any intervening scheduling. It excludes `guest_array_requirements`, and frame-cache destruction occurs before its timer starts. It is strong evidence of a costly synchronous path, not an exclusive CPU profile or proof of the sole bottleneck.

## Old findings: closure audit

“Closed” below means the narrow original defect is addressed; it does not certify every feature of the subsystem.

| Earlier finding | Current status | Basis / remaining limit |
|---|---|---|
| TLUT patch-chain idempotence | Closed for tested chain | Fresh application and repeated final-chain application succeeded locally. |
| EFB format bit reconstruction | Closed narrowly | Correct rotated field expression is present. Copy stride, extent, lifecycle remain separate gaps. |
| MEM2 copy destination identity | Closed narrowly | Copy destination preserves the wider shifted address field. Allocation lifetime is not thereby solved. |
| Ordinary SU coordinate scaling | Open | Ordinary shader sampling still omits SU/image normalization; actual capture contains mismatches. |
| Raw 1024 dimensions | Closed narrowly | Corrected field-plus-one decode is present. Raw mip handling remains incomplete. |
| TEV RGB/alpha liveness | Implemented; source-level repair credible | Component tracking fixes the reported ordinary RGB/alpha dependency defect. Visual root cause unproven; not a complete TEV certification. |
| EFB copy / clear / completion / present | Open | Display copy records a boolean and ignores clear; host frame recycling introduces clears. |
| FIFO semantic framing | Open; synthetic counterexample reproduced | Raw DrawDone bytes inside XF payload cause false transport boundary. |
| Recursive display-list continuation | Open; source-confirmed loss path | Nested DrawDone returns through CALL_DL without retaining the nested remainder. |
| Hosted SDK display-list shadow semantics | Open | Full begin/end replacements redirect/count/pad only; SDK dirty/context effects absent. |
| Texture/vertex invalidation and resource lifetime | Partial; vertex invalidation demonstrably open | BP0x66 exists; CP0x48 does nothing; copy identity and TLUT snapshots remain unresolved. |
| Guest-time/retrace mismatch | Closed narrowly | Current log confirms host/guest time agreement under throughput loss. |
| Additional omissions noted earlier | Partial/open | Raw mip state, copy-resource reuse, retained TLUT guest pointers still matter. |

## P0: empty draws and array-9 assertion

Sources: `renderer/aurora/apply_guest_array_bounds_patch.py`; emitted Aurora `lib/gx/command_processor.cpp`, `draw_prim`, `guest_array_requirements`, `push_gx_draw`.

**9 is GX_VA_POS, the position attribute, not texture unit 9.** With an indexed position descriptor and vtxCount=0:
- totalVtxBytes=0;
- the scanner visits no vertices and returns zero requirements;
- an unmerged draw enters the uploader;
- the nonzero-prefix assertion fails.

For a nonempty ordinary draw with valid position format, the scanner and uploader use the same descriptor and raw-array state. Every indexed position supplies a positive element extent, even with stride zero or index zero. A missing prefix is therefore not good evidence, by itself, for a general index-scanner/state-lifetime failure.

The separate Aurora custom indexed command passes empty requirements too, but the symbolized `draw_prim` path and raw-console workload point first to ordinary empty draws.

**Repair direction:** treat an exactly zero-count primitive as a no-op before index generation/staging. Preserve the nonzero-prefix assertion for nonempty draws. Add bounded diagnostics carrying count, primitive, format, VCD/VAT, array base/stride and current DL location. Handling a proven empty draw is not equivalent to skipping an unexpectedly missing array.

Audit small primitive counts at the same time: fan/strip reserve arithmetic subtracts from unsigned counts below 2; quads generate groups of four without protecting incomplete groups; zero line-strip count underflows instance count. Consume the command's declared payload correctly even when it produces no complete primitive.

## P0: invalidation bypasses both content checking and merge safety

Sources: emitted `command_processor.cpp` CP_CMD_INVAL_VTX, `guest_array_cache_covers`, `push_gx_draw`; `apply_guest_array_frame_reuse_patch.py`; `apply_cp_array_cache_patch.py`.

The final raw opcode 0x48 handler is only a comment and break. Comments elsewhere promise invalidation that does not exist.

Reproducer sequence:
1. Draw from array A, creating a cached range.
2. Update A in guest memory.
3. Issue GX vertex-cache invalidation.
4. Draw using indices within the same previous prefix.

The per-slot range still passes the size-only test, so `m14_stage_guest_array` and its memcmp never run. If state is otherwise clean, the new draw can merge into a draw whose staged bytes predate invalidation.

**Repair direction:** implement invalidation as an ordered cache epoch: clear/invalidate raw per-slot ranges and break merge eligibility. The secondary snapshot cache can still reuse an immutable range after comparing the newly visible bytes. Base/stride/content identity, visibility epoch and staging-buffer lifetime must be explicit. Do not merely add memcmp inside a helper that callers bypass.

Frame-end code already clears per-slot ranges. The defect is particularly same-frame mutation/invalidation, not an assertion that every frame reuses freed storage.

## P1: NBT3 shader accesses exceed the staged prefix

Sources: `guest_array_element_size` and scanner in `apply_guest_array_bounds_patch.py`; emitted `lib/gx/shader.cpp`, `attr_load_nbt_slice` and `attr_address`.

For NBT3, the adapter uses 3*componentSize for every independent index. The shader selects the independent B/T index **and adds** sliceIndex*3*componentSize inside the selected record.

Synthetic F32 example, stride=36, N/B/T indices=1/2/3:
- scanner maximum: 3*36+12 = **120**;
- tangent shader access end: 3*36+24+12 = **144**.

Whatever final hardware interpretation is adopted, the current CPU bound is not a bound on its own shader's accesses. This can read unrelated bytes elsewhere in the shared GPU storage buffer, so a mapped-RAM bounds check alone is insufficient.

Use one attribute-access description to drive scanning and shader fetches, including per-slice offsets. Test indexed NBT and NBT3 independently, and measure whether affected gameplay actually uses them. This is not an explanation for a missing position prefix.

## P1 performance: prefix amplification, duplicate copies and redundant pipeline work

The prefix formula `maxIndex*stride+elementSize` is sufficient for ordinary array addressing but can copy large unreferenced leading regions. Growing a prefix restages its entire beginning. Each miss then copies the same prefix into both Aurora storage and a newly allocated snapshot.

A synthetic sequence of 100 increasing prefixes of 1,000 through 100,000 bytes stages **5,050,000 bytes** although the final prefix is 100,000 bytes. This demonstrates amplification; the log lacks enough per-array ranges to attribute the exact Tower total to this mechanism.

The cache linearly searches accumulated entries, so N misses can incur O(N²) metadata comparisons. At 854 misses this can approach 364,231 candidate checks before considering hits. This is another cost to measure, not proof it dominates the copies.

A further source-confirmed inefficiency: CP array-base handlers are marked alwaysHandle + DirtyPipeline. `handle_cp` sets DirtyPipeline **before** `cp_arraybase` notices an unchanged base and returns. Thus “duplicate base preserved staged cache” does not preserve pipeline analysis or draw merging. The pipeline key includes stride/endianness, not the base address itself. Ordinary base changes need new immediate array offsets; only actual shader-layout changes need pipeline invalidation.

**Recommended sequence:**
1. Restore invalidation and immutable draw-snapshot correctness first.
2. Instrument min/max/unique indices, element size, prefix growth, bytes actually referenced, allocation/copy/lookup time, and dirty-pipeline causes.
3. Remove unconditional DirtyPipeline for base-only changes while preserving endianness/layout transitions.
4. Index reusable spans by canonical address/visibility generation; pool snapshots. Avoid full linear scans.
5. Stage intervals with a correctly accounted base offset, or gather referenced elements and rewrite the index stream. NBT3 must use the same access model.
6. Introduce longer-lived GPU mesh resources only with content/visibility generation tracking and in-flight resource lifetime. Do not reuse transient frame offsets across frames.

Do not repeatedly enlarge the buffer as the performance plan. Keep 128 MiB as temporary headroom. Do not retime simulation.

## P1 graphics: SU scaling, material data and depth ordering

Ordinary sampling in `lib/gx/shader.cpp` uses `texN_uv` directly. SU uniforms already contain decoded scale+1.

For the captured 16x16 / SU48x32 material, the expected ordinary coordinate conversion includes **3x horizontally and 2x vertically** relative to the current normalized sample. Use logical image dimensions, not replacement-texture resolution. A fix must also cover the indirect “simple coordinates” shortcut without applying scaling twice to paths already in texel space.

This is a concrete wrong-coordinate defect. It does not prove which white object it explains: decoded texels, resolved indexed UVs, wrap mode and post-texgen coordinates must be associated with the object.

The deep integer-vertex logger frequently reports pos_f32=0 / tex_f32=0 for INDEX16 attributes. Its printed zeros in those cases are not decoded array values. Extend capture to resolve actual referenced positions/normals/colors/UVs, and log K selectors/values, raster channel state, swap tables, entering TEV values, intermediate stage values and final alpha for a selected draw.

**New depth omission:** `bp_pe_ctrl` decodes `zCompLocBeforeTex`, but no shader/pipeline code consumes it. BP0x43 also has no dirty flag for a future depth-location key. Early depth update versus alpha rejection can change which later objects survive. This belongs in the wrong-layer investigation alongside EFB preservation. Capture it before assuming the game's sort order is wrong. Also inventory unhandled depth-texture BP state before claiming complete depth semantics.

## P1/P2: EFB lifecycle and resource ownership

Sources: `apply_raster_patch.py`; emitted `regs.cpp::bp_efb_copy`; `openspm_renderer_aurora_v4.cpp`; Aurora `lib/gfx/recording.cpp`.

A display copy calls notify_display_copy and ignores clear. DrawDone drains and ends/presents the Aurora frame; scheduler safepoints can also close a display-copy frame. New recording starts with attachments cleared. These are not equivalent to ordered guest EFB operations.

Implement:
- persistent guest EFB color/depth state;
- an immutable display snapshot at the display-copy command;
- ordered clear with correct masks/rectangle after copy;
- submission/completion distinct from presentation;
- load/preserve across submission-only boundaries.

Synthetic acceptance: red draw -> display-copy+clear -> blue draw -> DrawDone must present red, while EFB ends blue. DrawDone without copy between two draws must not erase the first draw.

Copy textures are resolved by destination pointer before ordinary RAM textures. BP0x66 invalidates bindings, but does not prove that a later CPU allocation at the same pointer supersedes an old GPU copy. Add allocation/content ownership generations or a valid write/visibility contract. Do not blindly evict every GPU copy on texture-cache invalidation: games may invalidate before sampling a valid copy whose RAM representation was never materialized.

TLUT loads still retain guest-memory pointers rather than a TMEM-like snapshot; palette-byte mutation after load is another independent lifetime test. EFB destination stride and half-size/filtering behavior also remain incomplete after the narrow format/address fixes.

## P1/P2: FIFO semantics and hosted lists

Sources: `backend/native/openspm_native_backend_v9.c::v9_submit_gx_fifo`; `core/gx_platform.c`; `apply_patches.py` recursive CALL_DL handling.

**False boundary demonstrated:** synthetic command bytes
`10 00011040 61450000 02000000`
are one XF load with two payload words and no BP command. The rolling transport detector nevertheless recognizes DrawDone ending at byte offset 9 and can drain a partial XF command. This is now a reproduced counterexample, not a theoretical collision.

Use command-aware incremental framing with CP/VAT-derived vertex lengths. Do not add more raw-byte exceptions. The decoder should own completion events, or framing must share its command-length/state model. Preserve parser state across 32-byte gathers.

**Nested remainder loss:** recursive `process` returns on nested DrawDone, then its caller returns the parent position after CALL_DL without saving the nested offset. Bytes following DrawDone in that list are lost. Use an explicit stream stack/continuation, preserve exact completion order, and test nested state -> DrawDone -> state -> parent continuation.

**Deferred source lifetime:** the draw stream, DL storage, indexed arrays and TLUT memory have separate visibility points. Drain mode avoids the prohibited threaded FIFO path, but does not automatically snapshot mutable source RAM when commands are submitted. Record producer and consumer hashes with matching list identities/epochs before asserting actual corruption.

**Hosted SDK semantics:** native begin/end still replace the entire SPM functions with bounded redirection/count/padding. They do not restore SDK dirty-state flushing or optional context save/restore. Prefer recompiled SDK non-device logic with only physical FIFO redirection hosted. Verify the exact player-local SDK implementation rather than guessing shadow offsets from another revision.

## TEV repair assessment

The component-aware patch correctly separates explicit RGB/alpha dependencies for ordinary combiner arguments. Its implicit preservation reads may conservatively load an otherwise unnecessary component; that costs a uniform but is not itself a wrong-color defect. I found no reason to revert it.

The captured three-stage material includes actual texel/raster multiplication, a KONST stage, then blending with TEV register 0. Merely observing that register loads now exist does not establish correct inputs or outputs. In many captured records register-0 alpha is zero, which makes its RGB contribution to that particular interpolation vanish; it is not evidence that the register is missing.

Keep “liveness repaired” separate from “TEV subsystem correct.” Synthetic coverage should include split destinations, alpha used as a color argument, preserved initial components, cross-stage reads, final non-PREV outputs and comparison operations. The shared alpha comparison generator also deserves review: non-A8 comparison opcodes reach vector-swizzle expressions with scalar alpha arguments. No such compare-op trigger is established in this capture.

A separate initialization workaround remains provisional: sampled-TCG sentinel recovery changes only src at shader consumption. It does not reconstruct all missing type/matrix/postmatrix state, and the log still records it during transitions. Trace why a sampled slot was omitted by numTexGens or never initialized before promoting this fallback to an exact fix.

## Implementation order and proof requirements

1. Add exactly-zero-draw handling and a failing-draw metadata ring. Keep nonempty assertions. Test small-count primitive arithmetic.
2. Implement raw vertex invalidation/merge barriers and reconcile NBT3 scanner/shader access ranges.
3. Remove redundant base-only pipeline invalidation; measure and replace amplified staging with a bounded interval/gather design.
4. Correct SU conversion with synthetic checkerboards, then compare a named affected object.
5. Repair EFB snapshot/clear/submission lifecycle and exercise alpha/depth ordering.
6. Replace raw completion matching and preserve nested DL continuations; restore exact SDK shadow semantics.
7. Add copy/TLUT/resource-generation coverage and follow the selected failing material through the pipeline.

A cheap rolling metadata buffer should retain the last commands/draws and dump at fatal, independent of a once-only 512-draw capture. Capture before the scanner/uploader assertion. Rearm a complete stable frame after the heavy scene becomes active. Prefer controlled per-feature comparisons over another large bundle of unseparated visual changes.

## Validation performed and limitations

- Retrieved current OpenSPM renderer/backend source and regenerated pinned Aurora with the full Python adaptation chain.
- Fresh and repeated chain application succeeded. Inspected the separate CMake Drain-mode override.
- Compiled isolated C++ probes using the actual extracted attribute-size functions, requirement scanner, upload loop and invalidation body:
  - ordinary INDEX16 position draw: prefix 48 bytes, passes;
  - zero vertices with indexed positions: exact missing-prefix assertion reproduced;
  - NBT3 fixture: scanner 120 bytes versus shader extent 144;
  - invalidation fixture: cached range remains 48 bytes and coverage still returns true.
- Reproduced false DrawDone matching in the synthetic XF payload above.
- Parsed the supplied log to distinguish the truncated capture, later staging-heavy frames and crash.
- No full Windows build, full GPU fixture, WBFS execution or player-visible fix was performed. The isolated probes establish local source defects, not the exact contents of the crashing draw.

The earlier architectural audit remains useful, but “roughly half closed” hides the most consequential fact: the raw-console adapter still lacks several ordinary command, visibility and lifetime semantics. These are bounded renderer engineering tasks; no evidence here requires abandoning the native port.
