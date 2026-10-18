# M14 dev125 — cache invariant QPC frequency

Date: 2026-09-21
Branch: `m14-native-execution`

## Why this is a real residual-tail target

The current residual camera/world-scroll hitch is no longer a broad throughput
problem:

- dev114 measured a 13.514 ms median first-retrace critical path;
- dev118 proved most VI deliveries reach true Nintendo scheduler idle before the
  deadline;
- dev119 Immediate presentation removed roughly three quarters of the visible
  jitter;
- dev122 then showed the remaining >25 ms presentation tails correlate tightly
  with the minority of `busy_due` deliveries.

The dev118 precise final-2-ms wait is intentionally aggressive. In steady Yold
logs it commonly reports roughly 150k–190k `Sleep(0)` yields per host second.

Inspection of the exact wait path found an accidental host-clock cost:

1. `bridge_m14_service_retrace()` calls `bridge_m14_now_ns()` before deciding
   whether the VI deadline is due;
2. after every `Sleep(0)` / `Sleep(1)`, it calls `bridge_m14_now_ns()` again
   to measure overshoot;
3. the Windows launcher's `host_monotonic_ns()` previously called both
   `QueryPerformanceCounter()` **and** `QueryPerformanceFrequency()` on every
   timestamp.

Therefore a 150k–190k/sec fine-wait window can cause roughly 300k–380k redundant
`QueryPerformanceFrequency()` calls/sec from this path alone.

Windows defines QPC frequency as invariant for the running system/boot session;
it only needs to be queried during initialization and can be cached.

## dev125 change

`launcher/native_main.c` now:

- queries `QueryPerformanceFrequency` once after command-line validation and
  before exposing the host API;
- stores the validated frequency in `g_host_qpc_frequency`;
- fails cleanly before launch if no usable QPC frequency exists;
- `host_monotonic_ns()` calls only `QueryPerformanceCounter()`;
- preserves the exact prior whole-seconds/remainder integer conversion.

Expected startup marker:

```text
M14 dev125 cached QPC frequency active: frequency=... Hz; host timestamp calls query only the counter.
```

Source contracts require exactly one `QueryPerformanceFrequency(` occurrence
in the launcher and forbid it inside `host_monotonic_ns()`.

## Non-changes

dev125 does **not** change:

- the 60 Hz fixed VI grid;
- precise-wait 2 ms threshold;
- Sleep(0)/Sleep(1) policy;
- guest timebase;
- renderer or FIFO ordering;
- presentation mode;
- thread priority/QoS;
- input transport.

This is permanent host-side hygiene rather than a risky semantic A/B.

## Player validation

Any current LLVM launcher includes dev125 after pulling the branch. For a clean
one-axis subjective comparison, first use the ordinary best baseline:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
```

Then scheduling A/Bs remain available separately:

```bat
M14-LLVM-PRIORITY-RUN.cmd
M14-LLVM-HIGHQOS-RUN.cmd
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

The main numbers to watch are dev118 `busy_due`, subjective camera scrolling,
and the >25 ms host-present tail count.

## If dev125 is neutral

Do not revert the cache; it removes objectively redundant work. Continue with
the independent dev123/dev124 scheduling tests and then, if needed, a soft
Windows CPU-set fast-core preference based on reported EfficiencyClass rather
than hardcoded logical CPU numbers.
