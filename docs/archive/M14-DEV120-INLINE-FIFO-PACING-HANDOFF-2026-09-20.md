# M14 dev120 — same-thread FIFO Inline pacing A/B

Date: 2026-09-20
Branch: `m14-native-execution`

## Why this candidate exists

dev119 forced Aurora/Dawn Immediate presentation and the player reported that the
remaining whole-frame jitter dropped to roughly one quarter of its former
severity. That confirms Mailbox was amplifying visible hold/catch-up, but the
producer still occasionally misses a fresh frame.

The Immediate Yold capture remains close to 60 Hz most of the time:

- retraces remain ~60/s;
- ordinary VI delivery is mostly from true scheduler idle;
- `busy_due` is commonly 0-2/60 in the known Yold scene;
- display and main are normally ~6 ms each;
- DrawDone/parser synchronization is still ~2.5 ms average, with ~3-4+ ms tail
  windows and occasional larger outliers.

The old async FIFO experiment from 2026-09-13 must **not** be restored. It moved
raw GX parsing to a worker thread while commands still referenced mutable
MEM1/MEM2. Broadway could reuse those buffers before the worker dereferenced
them, producing black/invisible/corrupt objects and timing-dependent failures.

## Safe alternative: ProcessingMode::Inline

Pinned Aurora also has `ProcessingMode::Inline`:

> process FIFO synchronously at publish

dev120 uses that mode, not Thread mode.

A command-complete prefix is parsed synchronously on the same Broadway/runtime
thread before the host call returns. Therefore referenced guest memory cannot be
rewritten by Broadway between publication and parser dereference.

This preserves the safety property of Drain mode while moving parser work out of
one large DrawDone burst and into smaller complete-command chunks.

## Exact changes

### Aurora runtime mode selector

`renderer/aurora/apply_fifo_inline_mode_patch.py` replaces the old build-time
forced Drain rewrite with:

```text
OPENSPM_M14_FIFO_MODE=drain   # default / rollback
OPENSPM_M14_FIFO_MODE=inline  # candidate
```

Thread mode is intentionally not exposed.

The same transform adds `aurora::gx::raw::publish()`, which delegates to
Aurora's existing FIFO publish primitive.

Expected candidate startup marker:

```text
OpenSPM M14 Aurora FIFO processing mode: Inline
```

### Neutral complete-prefix sync reason

`OPENSPM_GX_SYNC_FIFO_PUBLISH = 3` is restored as a weak semantic boundary.

It does **not**:

- present;
- recycle a host frame;
- report GX completion to the guest;
- change DrawDone;
- change display-copy selection.

It only allows already-submitted bytes ending at a complete GX command to be
parsed.

### Backend command-aware publication

Backend dev29 keeps the runtime-proven v9 GX framer. When Inline mode is
selected, v12 publishes roughly every 16 KiB only when:

```text
g_v9_gx_framer.phase == V9_GX_FRAME_OPCODE
```

so no partial BP/CP/XF/draw/CALL_DL command is exposed.

DrawDone remains an exact boundary and resets the incremental byte counter.
PRESENT_SAFEPOINT also resets it after draining any remaining tail.

Expected markers:

```text
M14 dev120 same-thread GX Inline pacing active...
M14 inline GX FIFO publish n=1 bytes=...; complete prefix parsed synchronously before Broadway resumes.
M14 Aurora command-complete same-thread FIFO publish active...
```

### Presentation and VI settings retained

The candidate keeps the two already-positive pacing changes:

- dev118 precise final-2-ms VI wait;
- dev119 Immediate presentation.

No chunk/state-memory/callback changes are part of this experiment.

## Player A/B

Candidate:

```bat
M14-LLVM-INLINE-RUN.cmd
```

Exact rollback/same-source baseline:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
```

The baseline now explicitly sets:

```text
OPENSPM_M14_FIFO_MODE=drain
```

After the first renderer rebuild, switching Inline/Drain is runtime-only.

## What success looks like

The primary target is the remaining occasional hitch, not raw average FPS.

Good result:

- subjective scrolling becomes closer to perfectly even;
- Yold `busy_due` falls toward zero;
- DrawDone `producer sync window` shrinks materially because much parser work
  was already consumed;
- no graphics difference from dev119;
- no new crash/hang.

Possible neutral result:

- DrawDone shrinks, but display/main rise by approximately the same total and
  subjective jitter is unchanged. That would prove redistribution alone is not
  enough; the next step would be eliminating or overlapping actual parser cost
  only after immutable renderer dependencies exist.

Reject immediately if:

- any visual corruption reappears;
- command framer reports an error;
- gameplay slows materially;
- a parser recursion/assertion appears.

The old asynchronous worker remains retired regardless of this A/B result.
