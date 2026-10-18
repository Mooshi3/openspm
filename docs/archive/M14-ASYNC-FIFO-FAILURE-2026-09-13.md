# M14 async FIFO experiment — failed runtime frontier (2026-09-13)

This note records the **failed** first attempt to move Aurora GX command processing off the Broadway/runtime thread. It is intentionally preserved so future work does not repeat the same unsafe architecture.

## Tested candidate

Player run: `launch(20260913-173002).log`

Repo candidate: `f0a0aa1e3b0858fe1b25fd2318a7b43ec7ce453a`

Candidate changes restored Aurora `ProcessingMode::Thread`, added a nonblocking raw-FIFO publish seam, and had backend dev27 publish command-complete prefixes to the Aurora FIFO worker at roughly 16 KiB intervals. v19 display-list context handling and compact guest-array staging remained active.

The experiment was retired by forward commit `06905a7537d73fce9adc37058f90b67ad73f4439`, whose tree is exactly the last graphics-correct v19 + compact-staging runtime (`3bde0dbdcabeee405f2e59670cfbb2d205bc8dc9`). Do not rewind the branch to remove the experiment; its history is useful evidence.

## What runtime proved

The worker really ran. The log contained the Aurora FIFO worker marker, dev27 startup marker, nonblocking renderer publish marker, and repeated backend `async GX FIFO publish` markers. This was not a no-op configuration test.

The result was unacceptable:

- some scenes became smoother, but other scenes became slower;
- enemies visibly moved in slow motion while Mario could appear comparatively fast;
- the Chapter 1-2 rotating transport/spinning devices made Mario disappear again;
- Mario became a black square during the 2D/3D flip animation;
- DrawDone stalls still reached roughly 79 ms and later roughly 172 ms;
- bad periods fell to roughly 17–30 delivered retraces/sec;
- resizing the window ended in a host access violation (`0xC0000005`, read address `0x9`) after a ~163 ms begin-frame/FIFO-submit stall.

v19 itself remained active and its display-list hooks continued returning valid 192/224-byte lists. Therefore these regressions are **not evidence that v19 is wrong**. They were introduced by the new asynchronous consumption model.

## Root architectural mistake

OpenSPM's raw Wii GX FIFO is not a self-contained immutable command stream.

Commands can reference **mutable guest RAM** that is resolved later by Aurora:

- `GX_CMD_CALL_DL` references display-list memory;
- indexed vertex arrays reference guest array memory;
- textures and other GX resources can reference guest memory;
- the game may reuse or rewrite those addresses after issuing GX work.

In the synchronous Drain runtime, Broadway is stopped while Aurora parses the submitted GX stream and resolves those guest-memory references. The renderer therefore observes the bytes associated with that command epoch.

The dev27 worker candidate let Broadway continue while the worker parsed older GX work. Broadway could then rewrite/reuse the guest buffers before the worker dereferenced them. The runtime log supports this hazard directly: the same display-list address is repeatedly regenerated with different hashes during normal play.

This explains why asynchronous parsing could produce transient black squares, invisible Mario, transform/material corruption, and timing-dependent behavior despite v19 being correct.

The resize crash is also consistent with the candidate exposing host-side lifetime/race behavior that does not exist in the synchronous runtime. It should be treated as a renderer-threading/lifetime failure until disproved, not as a guest crash.

## Do not repeat

Do **not** restore Aurora Thread mode while `guest_memory::map` gives the worker live mutable MEM1/MEM2 and raw commands still carry unresolved guest pointers.

Publishing only at complete GX command boundaries is necessary but **not sufficient**. A command can be syntactically complete while still referring to mutable memory whose content is not immutable for an asynchronous consumer.

## Correct direction for future renderer separation

The goal remains valid: SPM simulation should stay logically single-threaded while renderer CPU work runs concurrently. The separation boundary must move **after GPU-visible data is made immutable**.

Two viable architectural families:

1. **GPU-visible shadow / Wii cache semantics.** Maintain renderer-owned shadow memory representing bytes made visible to GX by the guest's cache/DMA semantics. Mirror relevant `DCFlushRange` / `DCStoreRange` / uncached or DMA-visible writes and completed display-list bytes into that shadow. The renderer worker maps the immutable/versioned shadow rather than live Broadway RAM.
2. **Producer-side dependency snapshots.** Before publishing raw GX work, resolve every guest-memory dependency used by that work and attach immutable snapshots/versioned handles. This must cover display lists, indexed arrays, textures and other referenced resource state; inlining only CALL_DL is not enough.

A copy-on-write/versioned resource scheme can optimize either design later. Correctness comes before reducing copies.

## Current safe runtime

Keep:

- continuation-safe display-list bridge v19;
- compact raw guest-array interval staging;
- command-aware FIFO framing and nested display-list continuation;
- Aurora `ProcessingMode::Drain` until immutable GPU-visible resource semantics exist.

The current performance problem is therefore no longer "just turn on Aurora's worker." The next safe performance step is to profile synchronous `fifo::process()` / draw preparation / pipeline / texture work and remove the expensive component without changing memory-observation timing.
