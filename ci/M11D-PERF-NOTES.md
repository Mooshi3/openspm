# M11D performance follow-up

This note records the next performance experiment discovered from the first full M11B perf run. It does **not** change the M11C build.

## Measured evidence

The M11B run completed with:

- 10,855,784,605 native dispatches
- 87,151,815 native bursts
- 0 forced interpreter fallback steps
- 0 SMC-failed chunks
- 656,825,936 instruction-hook fallbacks

Top sampled dispatch PCs included:

- `0x8025C9C8` (`strcmp`) — 488,446 samples
- `0x8004DD98` (return from a hot `strcmp` call in animation code) — 451,188 samples
- `0x8027551C` — 307,185 samples

Sampling occurs every 4096 native dispatches, so `0x8027551C` represents roughly 1.258 billion sampled-equivalent dispatch entries in that run.

## 0x8027551C is Nintendo OS scheduler idle

Static inspection of exact R8PE01 generated code identifies the containing routine as the Nintendo OS `SelectThread` scheduler path.

Immediately before the loop:

1. `SelectThread` calls `OSEnableInterrupts` at `0x80271E3C`.
2. It repeatedly reads `RunQueueBits` from SDA (`lwz r0,-29552(r13)`).
3. It loops at `0x8027551C` while `RunQueueBits == 0`.
4. Once nonzero, it calls `OSDisableInterrupts` at `0x80271E28` and resumes scheduler selection.

This is a canonical emulated-CPU idle loop: with no runnable guest thread, the PPC intentionally burns cycles waiting for an interrupt/timed event to make one runnable.

## Existing RecompCore mechanism

Pinned RecompCore already has a purpose-built `MAIN_STATICRECOMP_IDLE_PC` (`u32`) setting. `StaticRecompCore::Init()` loads it into `m_idle_pc`; the run loop calls `CoreTiming::Idle()` when PPC PC equals that address.

Therefore the safe first experiment is simply:

`StaticRecompIdlePC = 0x8027551C`

for R8PE01/OpenSPM.

This preserves guest scheduler behavior while allowing Dolphin CoreTiming to skip the useless spin until the next timing slice/event. It does not alter generated SPM instructions, REL resolution, SMC validation, or the OpenSPM public feature API.

## Build discipline

Do not combine this with native libc replacements or unsafe cross-chunk direct calls in the same measurement build. Measure M11C first; if more performance is needed, M11D should isolate the scheduler-idle setting so its effect is attributable.

## Other researched follow-ups (not yet enabled)

### `strcmp` native replacement

DolRecomp supports module-local replacements through `DOLRECOMP_ENABLE_REPLACEMENTS` / `dolrecomp_dispatch_replacement`. This is a promising later optimization because `0x8025C9C8` alone accounts for about 18% of sampled dispatch entries. A replacement can remain behind normal RecompCore chunk verification, unlike direct cross-chunk calls.

### Cross-chunk direct calls

DolRecomp upstream PR #13 added C-backend cross-chunk direct calls and measured a 10.6% win, but the maintainer deliberately made them opt-in via `DOLRECOMP_UNSAFE_DIRECT_CALLS=1`: they bypass runtimes such as RecompCore that validate mutable guest code at chassis dispatch boundaries. Do **not** enable this in a production OpenSPM build without adding an SMC-safe verification gate.
