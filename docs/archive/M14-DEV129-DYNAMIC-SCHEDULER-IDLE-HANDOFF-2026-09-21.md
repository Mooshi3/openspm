# M14 dev129 — state-sensitive scheduler-idle side exit

Date: 2026-09-21
Branch: `m14-native-execution`

## Trigger: six-way pacing A/B

The player ran the current independent variants:

- baseline Immediate;
- dev126 final-idle spin;
- dev123 ABOVE_NORMAL producer priority;
- dev124 producer HighQoS;
- dev127 fast-core CPU Set preference;
- dev123 no-sticky pressed-edge transport.

Subjectively they were mostly close. Fast-core felt best but retained some
camera/world-scroll jitter. No-sticky was clearly the worst and sometimes felt
as though Mario himself stopped for a frame, so sticky pressed-edge retention
remains the baseline.

The important new evidence comes from dev128, which was present in every run.

Across every scheduling/input variant, the single dominant busy-deadline owner
is repeatedly:

```text
phase=3(done)
pc=0x8027551C
lr=0x8027551C
downcount=-258
```

`0x8027551C` is not ordinary gameplay/render work. It is the exact Revolution
OS `SelectThread` no-runnable-thread scheduler-idle continuation which OpenSPM
has treated as the host VI wait boundary since the original native boot.

Representative dev128 lines occur immediately before the familiar ~33 ms frame
and ~30 ms host-present tails.

## Historical context

This does **not** undo dev68 or dev90.

dev68 repaired a real opposite-state bug:

- persistent PC is scheduler idle;
- a device/finish event has made Nintendo `RunQueueBits != 0`;
- host must **not** sleep until VI;
- Nintendo `SelectThread` must execute immediately and select the runnable
  thread.

dev90 later made `0x8027551C` an exact compiler partition and unconditional
native-first public-dispatch entry. That was necessary to make LLVM capable of
re-entering the mid-function scheduler continuation after external callbacks.

The missing case was:

- native execution reaches `0x8027551C` *inside an already-running native
  slice*;
- live Nintendo `RunQueueBits == 0`;
- because dev90 is unconditional native-first, the public dispatcher enters
  `SelectThread` again instead of returning to the outer bridge;
- the bridge does not call `bridge_m14_service_retrace()` again until that
  native slice eventually yields;
- dev128 can therefore see a VI deadline arrive with the PC already sitting at
  the scheduler-idle continuation even though dev118 never observed the idle
  epoch before the deadline.

This explains the apparently contradictory combination of
`busy_due` + `pc=0x8027551C`.

## dev129 fix

Scheduler idle becomes **state-sensitive host-first** rather than permanently
native-first.

The generated LLVM public dispatcher still contains the exact compiler entry at
`0x8027551C`, but dev129 removes that address from dev90's unconditional
native-first table and forces the existing host-call prefilter bit on for that
exact PC.

At the host callback:

### RunQueueBits == 0

Request a private internal detail-34 side exit and leave PC exactly
`0x8027551C`.

`bridge_run_v7()` consumes that detail internally and converts it to an
ordinary safepoint. It never escapes to the backend as an unsupported fallback.

The next outer run immediately enters `bridge_m14_service_retrace()`, which
can use the already-proven true-idle VI wait path rather than burning a native
slice inside SelectThread's idle loop.

### RunQueueBits != 0

Return false from the host classifier. The generated dispatcher immediately
executes the exact existing native LLVM entry at `0x8027551C`.

Nintendo `SelectThread` remains authoritative and dev68's runnable-idle wake
repair is preserved.

### RunQueueBits unavailable / invalid

Fail open to Nintendo native execution. dev129 never invents an empty run queue.

## Lost-wakeup safety

An empty queue can become runnable after the side exit but before the next outer
run. That is safe:

1. the side exit preserves PC at `0x8027551C`;
2. the next outer run calls `bridge_m14_service_retrace()`;
3. before waiting, dev68 re-reads the same live Nintendo RunQueueBits word;
4. if nonzero, it returns without waiting and native SelectThread resumes.

No guest PC/LR/TCB/run-queue state is fabricated.

## Instrumentation

Startup marker:

```text
M14 LLVM dev129 scheduler-idle dynamic dispatch active:
```

Shutdown:

```text
M14 LLVM dev129 scheduler-idle dynamic stats
  empty_sideexits=...
  runnable_native=...
  unknown_native=...
```

dev90 statistics now cover only the two proven dev79 hot-BL structural entries.

dev128 remains active. The desired effect is for its residual
`busy-due owner ... pc=0x8027551C` population to collapse.

## Expected player result

Run the normal baseline first:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
```

Then walk the same Yold path.

Success is not merely a higher average FPS. The stronger criteria are:

- visibly smoother world/camera translation;
- fewer >25 ms host-present tails;
- fewer dev118 `busy_due` deliveries;
- specifically, a large reduction in dev128 busy-due events owned by
  `0x8027551C`;
- `runnable_native` remains nonzero when device events wake Nintendo threads;
- no boot/scheduler/input/graphics regression.

If idle-PC busy tails disappear but residual misses move to stable gameplay PCs,
dev129 has repaired the scheduler-boundary artifact and dev128 has exposed the
next genuine deterministic tail.
