# M14 runtime 052222: resize-triggered correctness frontier

Player log: `launch(20260914-052222).log`

Tested repo revision: `fd80b77612cc`

## Player discovery

This run did not intentionally reach the usual heaviest performance area, but it produced a more important correctness discovery.

The rotating/spinning device initially rendered Mario correctly. The player then resized the host window while Mario was in the device; after the resize Mario's sprite became corrupted. A later door interaction softlocked. The player believes both failures are resize-correlated and likely would not have happened without resizing.

Treat this as a new causal hypothesis with strong architectural support. Do **not** continue using spinner corruption or door softlock by themselves as proof that an unrelated renderer experiment regressed correctness unless the A/B also controls window resize.

Do not yet resurrect previously retired worker/no-guest-index experiments. Their old A/B evidence is now confounded, not disproven. First fix/measure resize behavior on the current safe synchronous architecture.

## Runtime evidence: host event processing spikes exactly in the resize-era cluster

The existing frame-boundary event deferral is active:

`M14 Aurora host events deferred while a recording frame is open; resize/surface mutation runs only at host-frame boundaries.`

That protects against mutating the swapchain in the middle of an actively recording host frame, but it does not make the resize operation semantically transparent to the emulated Wii.

Before the first late event spike the run repeatedly delivers 60 retraces/sec. Then the log records:

- `update-events wait_us=201383`, followed by a 48-retrace second;
- `update-events wait_us=252634`, followed by a 45-retrace second;
- later `update-events wait_us=398987`, followed by a 34-retrace second;
- subsequent resize-era event waits of ~245 ms, ~256 ms, ~270 ms, ~101 ms and ~253 ms.

Those are real host-window/event stalls and explain severe transient hitches while resizing. They are separate from ordinary scene-dependent DrawDone cost.

## Architectural bug: Aurora resize clears state that is guest-visible in raw-Wii mode

Pinned Aurora's SDL window path handles `SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED` by calling its resize path.

Pinned WebGPU resize first executes `gfx::gpu_synchronize()`. When the render target size changes it then calls:

```cpp
gx::clear_copy_texture_cache();
gfx::clear_caches();
```

`gx::clear_copy_texture_cache()` clears both:

```cpp
g_gxState.copyTextures.clear();
g_gxState.copyTextureCache.clear();
```

That is reasonable for a normal Aurora source port, where those textures are host-side implementation cache.

It is not semantically correct for OpenSPM's original-Wii path. Our raw GX adapter uses these host copy textures to represent results of Wii EFB `GXCopyTex` operations whose destinations are guest-memory identities. From the game's point of view those results live in RAM. Resizing a PC window is not a Wii memory invalidation and must not erase them.

This is a plausible direct explanation for resize -> captured/effect sprite corruption. It may also explain door/transition failures if those sequences depend on an EFB-copy-backed effect or texture state. The door causal link remains runtime-unproven; do not overclaim it yet.

## Why preserving raw-Wii copy textures across resize is safe enough to A/B

Aurora's `CopyTextureKey` contains:

- destination identity;
- scaled copy width;
- scaled copy height;
- format.

A future `GXCopyTex` after a host-size change therefore naturally uses a different key when scaled dimensions change, allocates an appropriately sized new host copy texture, and updates `copyTextures[dest]` to the newest result.

We do not need to erase the previous guest-visible contents merely to permit a correctly sized future copy.

## Candidate repair

Added:

`renderer/aurora/apply_raw_wii_resize_copy_persistence_patch.py`

Wired into the ordinary cached-Aurora configure chain after render-worker retirement verification and the producer-only DrawDone profiler.

Behavior:

- if the raw-Wii guest-memory mapper is **not** active, keep upstream Aurora behavior and clear copy textures on resize;
- if the guest-memory mapper **is** active, preserve `copyTextures` / `copyTextureCache` across host resize;
- still keep `gfx::gpu_synchronize()`;
- still clear ordinary gfx/offscreen/bind caches;
- do not change FIFO ordering, parser boundaries, worker behavior, guest-array lifetime, or presentation policy;
- future EFB copies still re-key by scaled dimensions.

Sparse runtime marker, first eight resizes and powers of two:

```text
M14 raw-Wii resize preserves guest-visible EFB copy textures n=... render=...x... surface=...x... copies=... cache_entries=...
```

## Performance profiler side-result from this run

The new producer-only DrawDone profiler is active and useful even though the player did not reach the intended heaviest area.

In established `seq=2 stage=1` gameplay, representative 256-frame windows show roughly:

- draw CPU ~658-745 ms/window (~2.6-2.9 ms/frame);
- indexed-array staging ~225-258 ms/window, the largest measured substage;
- mandatory guest-index scan ~168-190 ms/window, second largest;
- pipeline ~48-59 ms/window;
- bind ~43-60 ms/window;
- uniforms ~15-19 ms/window;
- vertex push ~15-18 ms/window.

So the current synchronous draw path still spends most of its measured substage time in exact raw indexed-array handling, even after the proven pooled snapshot optimizations. Preserve this evidence for the performance track, but do not mix it with the resize correctness fix.

## Next controlled player test

Run normal `M14-UPDATE-RUN.cmd`; do not wipe `.openspm` or saves.

For correctness, a short controlled A/B is more valuable than reaching the heavy room:

1. Enter/use the known rotating device **without resizing** and verify Mario remains correct.
2. Use it again and deliberately resize the window while Mario is inside.
3. Confirm the new `M14 raw-Wii resize preserves...` marker appears.
4. Check whether Mario stays correct through/after the resize.
5. Immediately try the same door path that previously softlocked.
6. Report whether the resize still causes corruption/softlock.

If the fix prevents both failures, resize was a shared trigger and old spinner/door regression attribution must be revisited. If sprite corruption remains, instrument which host/GX cache identity changes across resize next. If sprite is fixed but door still softlocks, split door event/animation progression from graphics and trace the guest animation/event functions independently.

## Performance track remains separate

Manual window dragging can account for the 100-399 ms `update-events` spikes and associated missed retraces, but it does **not** explain the persistent scene-dependent 30-35 ms presentation tail observed in non-resize gameplay. Continue the DrawDone/producer performance investigation after the resize correctness A/B.
