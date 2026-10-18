# M14 LLVM relF relocation handoff — 2026-09-15

## Current branch frontier

The last tested LLVM run escaped the v23 nonblocking frame-phase problem and made real forward progress: two frame presents occurred, retraces remained healthy, and the previous `0x801A7B58` phase-gate frontier disappeared.

The run then stopped at an explicit native migration boundary:

```text
reason=3 detail=0x0000000C source=0x80AF1DDC
```

`detail=0xC` is the local bridge's generic "no native function for this PC" boundary. This is not an MMIO/service failure and not another stationary native-region/cold-entry loop.

## Architectural diagnosis

`0x80AF1DDC` is inside the live `relF` module. The runtime module was linked at a different address than the old LLVM codegen-only REL base.

Known old codegen layout:

```text
relF module base   0x80C03F40
relF exec section  0x80C0401C
relF prolog        0x80C4205C
prolog offset      0x0003E11C
```

The observed live PC and the verified prolog offset imply:

```text
0x80AF1DDC - 0x0003E11C = 0x80AB3CC0
```

So this run loaded the live REL around `0x80AB3CC0`, not `0x80C03F40`.

This is expected Wii behavior: SPM's `relMain()` allocates the decompressed REL from `HEAP_MAIN`, then calls `OSLink`. `OSLink` relocates both code and embedded data/function pointers at the allocation address. Therefore simply translating a live `0x80AB...` PC to the old `0x80C0...` native code range is invalid: branch dispatch might work while embedded REL pointers remain wrong.

Older recovery notes explicitly warned that the previous `0x80C03F40` REL base was an observed live base, not a portable fixed ABI.

## Best current solution

Make only the relF allocation deterministic using the real Revolution expandable-heap semantics rather than inventing a runtime PC alias table.

Public RVL SDK/decomp behavior confirms:

```c
MEMAllocFromExpHeapEx(heap, size, align)
```

uses the sign of `align` as allocation direction:

- positive alignment: allocate from the head;
- negative alignment: allocate from the tail;
- absolute value remains the alignment.

Thus `align=-0x20` is an authentic tracked 32-byte-aligned tail allocation. It still creates a normal MEMExpHeap block and can be freed normally.

SPM's ordinary HEAP_MAIN allocations grow from the head. Moving only relF to the tail should make its address depend on the heap end instead of the exact earlier allocation schedule.

## New v24 proof bridge already landed

Commit:

```text
2fadcb13743b9a326b6b3b1d690f213b2e289ee0
M14 LLVM: prove deterministic relF tail allocation
```

Added:

```text
tools/native_bridge/legacy_c_adapter_v24.c.in
```

Important: at handoff time **v24 exists but CMake still selects v23**. Do not assume the proof bridge is active until `tools/native_bridge/CMakeLists.txt` is changed from `legacy_c_adapter_v23.c.in` to `legacy_c_adapter_v24.c.in` for LLVM.

v24 is LLVM-only and includes v23, so generated-C rollback remains untouched.

### v24 exact interception

The proof targets only the unique `relMain -> __memAlloc(HEAP_MAIN, relF_size)` call.

Verified constants in v24:

```text
__memAlloc entry              0x801A59A8
__memAlloc li r5,+0x20        0x801A59D4
__memAlloc MEMExpHeap call    0x801A59D8
MEMAllocFromExpHeapEx         0x80293A8C
relMain allocation call       0x8023C978
relMain allocation return     0x8023C97C
memory_wp                     0x8056CB68
relF decompressed size        0x0031BEC8
relF prolog offset            0x0003E11C
old codegen base              0x80C03F40
```

Exact R8PE01 instruction signatures:

```text
0x8023C978 = 0x4BF69031
0x801A59D4 = 0x38A00020
0x801A59D8 = 0x480EE0B5
```

At `__memAlloc` entry v24 also requires:

```text
r3 == 0              # HEAP_MAIN
r4 == 0x0031BEC8     # relF size
lr == 0x8023C97C     # exact relMain continuation
```

It reads the actual heap handle/start/end from `memory_wp`, validates the heap shape, then redirects execution into the game's real `MEMAllocFromExpHeapEx` with:

```text
r3 = real HEAP_MAIN handle
r4 = relF size (unchanged)
r5 = 0xFFFFFFE0      # -0x20
lr = 0x8023C97C
pc = 0x80293A8C
```

The actual SDK allocator owns all metadata updates. No clean DOL mutation and no manual heap surgery.

On the exact return at `0x8023C97C`, v24 logs the returned base and compares it with the empty-tail prediction. If the signature/heap metadata changes, it fails closed and lets the original `__memAlloc` path execute. If the tail allocation returns NULL, it retries through the original wrapper so the game's normal assert/failure handling remains authoritative.

Expected activation marker:

```text
M14 LLVM relF deterministic-tail allocation active: verified relMain allocation redirected through guest MEMAllocFromExpHeapEx align=-0x20; heap=[...,...) empty_tail=....
```

Expected result marker:

```text
M14 LLVM relF tail allocation result: base=0x........ prolog=0x........ expected_empty_tail=0x........ match=yes|no; old_codegen_base=0x80C03F40 remains active for this proof run.
```

## Why the first v24 run is intentionally a proof run

Do **not** immediately change the LLVM codegen REL base based on a guessed heap boundary.

Activate v24 first while keeping codegen at the old `0x80C03F40` base. The game should allocate/link relF at the deterministic tail address, v24 should log that exact address, and then native execution is expected to stop when it tries to enter the newly placed REL because the LLVM objects are still compiled for the old base.

That intentional stop gives the exact deterministic base to compile against next.

After the proof log confirms a stable returned base:

1. change the local codegen-only relF base in `build_relf_codegen_dol.py` / related generation constants to the proven tail base;
2. regenerate the relF LLVM object set for that base;
3. keep v24's tail allocation behavior active so live `OSLink` and LLVM codegen agree;
4. rerun and verify the REL prolog enters natively instead of `detail=0xC`;
5. then continue to title/gameplay and performance A/B.

Because relF relocations encode absolute addresses, a new REL base necessarily invalidates the existing relF LLVM objects. The DOL objects may be reusable depending on how the generator stamps/manifests the augmented DOL, but do not pretend the old REL objects are address-independent.

## Next exact actions

1. Activate `legacy_c_adapter_v24.c.in` in `tools/native_bridge/CMakeLists.txt` for LLVM only. Update the nearby comment to say v24 includes v23 and adds deterministic relF tail-allocation proof.
2. Extend `test_llvm_native_pipeline.py`'s source-contract assertions so the active bridge must be v24 / contain the relF-tail marker. Keep generated-C assertions unchanged.
3. Run:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

4. In the new log, verify:
   - module version is the v24/dev34 variant;
   - v23 nonblocking frame-phase marker still appears;
   - v24 tail-allocation activation marker appears;
   - v24 result marker records heap start/end, returned base, prolog and `match=yes` if the free tail was pristine;
   - previous live `0x80AF1DDC` boundary is gone;
   - expected new stop is at the deterministic relF prolog/native-missing address because codegen is intentionally still old-base.
5. Only then move the codegen REL base to the logged deterministic base and rebuild the REL LLVM objects.

If v24 refuses the interception, do not weaken the checks. Determine which exact instruction/register/heap field differs and fix the model from evidence.

## Important prior LLVM fixes that must remain intact

- Broadway HID4 SPR compiler fix.
- FPU exception-restart entry-point compiler fix (`02cec7d8...`).
- post-MTMSR RVL cold-resume temporary recovery.
- CPU->DSP mailbox consume semantics (`2cce92c...`).
- first DSP task INIT semantic completion (v20).
- HOME/system-UI policy layer.
- bounded direct-BL migration rescue (v22).
- nonblocking frame-phase telemetry fix (v23).
- fixed 60 Hz retrace deadline grid; skipped elapsed deadlines, never burst delivery.

Do not reintroduce diagnostic frame-phase PCs as hard native-region barriers.

## Secondary issue still parked

`0x8028987C` is incorrectly classified in the shared generated-C host-target table as GX/WPAR setup. Exact R8PE01 proves it is `DSPCheckMailFromDSP`. Do not remove it from the shared generated-C rollback path until LLVM reaches known title/gameplay behavior and a controlled generated-C A/B proves removal is safe.

## Acceptance after REL alignment

Once the deterministic live REL base and LLVM codegen base match, the high-value checkpoint is not merely "no crash":

- reach the previous generated-C title/file-select/gameplay frontier;
- preserve v19 graphics behavior;
- verify input, NAND/save, resize, doors/spinners and HOME policy;
- then compare heavy-area frame/simulation timings against generated-C.

The reason for the LLVM migration remains performance: generated-C heavy scenes spend roughly 18–26 ms in `spmarioDisp + spmarioMain` before VI wait and collapse toward 30 Hz, while Dolphin demonstrates substantial CPU headroom on the same machine.