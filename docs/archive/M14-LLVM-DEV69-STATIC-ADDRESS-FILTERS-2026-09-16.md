# dev69: cheap rejection of absent immutable host addresses

Base: `d0e77177b78a0b783635c1bc1a6c07af41994a4c`.
User requested a short, high-impact pass with limited remaining usage.

## New dev68 runtime evidence

`launch(20260916-234042).log` confirms dev68 is materially better: the first
~12 cadence windows run around 60 real updates/s, versus the old 30. Over 141
windows, weighted cadence is 37.09 updates/s. Later heavy scenes remain around
30, with visible jitter/slow enemies reported by the player. These are different
scene mixes, so aggregate averages are not a controlled performance benchmark.

The run records 3,087 runnable-idle resumes, 57.55 s in idle sleeps across
144.39 s of measured slices, and no stationary 0x80065128 diagnostic. Keep dev68.
Presentation calls average 206 us; later DrawDone windows reach roughly 3-5 ms.
There is still real frame work and retrace quantization to investigate.

Ordinary routing performed 464.3 million seam searches / 4.044 billion
comparisons, plus 464.1 million host-target lower bounds / 2.555 billion
comparisons. This short pass removes most negative searches safely. Earlier
dev66's lack of FPS improvement happened before the confirmed scheduler ceiling
was removed; it does not prove this overhead is irrelevant now. Conversely,
these counts do not prove how much wall time the comparisons cost.

## Change

Two 2 KiB conservative address filters are built from immutable metadata:

- the generated const hosted-callsite table (v26);
- the static host-target membership set (v39; sorting changes order only).

If an address's bit is zero, it cannot be in that table, so the lookup returns
absent immediately. A set bit always falls through to the unchanged exact
binary lookup. Hash collisions cannot suppress a real host target. Initialization
is on the existing single native CPU/event thread, once per loaded module.

This never memoizes a host_call result, never skips the semantic router, and
never learns from CPU or device state. All dynamic v19/relF/HOME/DSP/control-tail
and direct-BL handling remains in the same order. Region range queries retain
exact lower-bound behavior. The dangerous dev66 optimization stays retired.

The module is `m14-dev69-llvm-static-address-filters`. No LLVM objects, generated-C
semantics, timer deadlines, guest state, animation rates or input behavior change.

## Validation

- Executable test of the actual production lookup functions agrees with reference
  membership on more than one million addresses, every synthetic seam, every
  real host target, forced hash collisions, unaligned PCs and uint32 boundaries.
- Existing runnable-idle executable regression still passes (120 old versus
  239 repaired synthetic frames over four seconds).
- Full LLVM v42 include-chain syntax check passes with pinned public CPU headers
  and synthetic generated metadata. No Windows link/gameplay run was available.
- No measured dev69 gameplay improvement is claimed yet.

## Next run

Run `M14-LLVM-UPDATE-RUN.cmd`; no cache/save wipe or LLVM regeneration needed.
Startup must include `M14 LLVM dev69 immutable address filters active:` in
addition to the retained dev68 wake marker.

At normal close, `M14 LLVM dev69 static filter stats seam_rejects=... host_rejects=...`
reports eliminated exact searches. Existing production stats report remaining
comparisons. Compare real spmarioMain cadence in the same heavy area; if it does
not improve, move to frame-phase/host CPU profiling rather than more table work.
The remaining 30 Hz may reflect >16.7 ms active frame work missing every other
VI opportunity. Do not speed enemies artificially or fabricate extra retraces.
