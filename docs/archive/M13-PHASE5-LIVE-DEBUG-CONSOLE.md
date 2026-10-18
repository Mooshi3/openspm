# M13 Phase 5 live debug console

Status: staged development tooling. Hosted Windows rebuilds remain intentionally paused while monthly GitHub Actions minutes are constrained.

## Why this exists

Startup presets are useful for repeatable benchmarks, but they are too slow for reverse-engineering experiments. If every hypothesis requires a source edit and full ModernGekko/Dolphin rebuild, small questions such as “which GSW value gates this cutscene?” become expensive.

The Phase 5 debug console provides a development-only command box in the OpenSPM launcher. It talks to the running in-process backend through a private shared-memory mailbox. The backend only consumes/mutates guest state at the verified R8PE01 CPU-thread `TICK_BEGIN` boundary.

This is deliberately **not** a public mod SDK service. Raw SPM script variables, map names and revision-specific state remain adapter/debug details.

## First command set

```text
help
seq
map
gsw <index>
gsw <index> <value>
gsw(<index>)
gsw(<index>)=<value>
story
story <value>
warp <map>
perf
```

Examples:

```text
gsw 0
story
gsw(3)
gsw 0 4
story 4
warp aa2_02
map
seq
perf
```

`GSW(0)` is the full-word sequence/story-position variable. `GSW(n>0)` is treated as the game's signed-byte GSW array.

## Threading / safety rule

The launcher never writes guest memory itself. It writes UTF-8 commands into a development mailbox named:

```text
Local\OpenSPM.Debug.<game-process-pid>
```

The R8PE01 adapter sees a new command generation during `ObserverTickBegin`, copies the command, executes it synchronously on the emulated CPU thread, and publishes a textual response generation. The GUI polls responses at low frequency.

Therefore a command such as:

```text
story 4
```

is not a random cross-thread memory poke. It becomes a safe game-thread write at the same semantic boundary intended for future game-service mutations.

## R8PE01 state used by the first implementation

Private adapter facts:

```text
seqWork              0x804D0D60
spmario::gp variable 0x8056CB78
SpmarioGlobals.mapName  +0x44
SpmarioGlobals.doorName +0x64
SpmarioGlobals.gsw0     +0x140
SpmarioGlobals.gsw[]    +0x544
```

Warp continues to use the already-proven private sequence transition:

```text
mapData          0x803C5098
next_seq         0x8056CABC
next_p0          0x8056D318
next_p1          0x8056D31C
SEQ_MAPCHANGE    3
```

These values must never become persistent content identity or public SDK constants.

## Bowser benchmark workflow once built

Instead of changing the startup preset for every experiment:

1. Launch a normal/debug session.
2. At any stable point, query `story` and `map`.
3. Set a candidate prologue sequence position with `story <n>`.
4. `warp aa2_02`.
5. Observe whether `aa2_02_init_evt` naturally selects the crowd/Bleck cutscene branch.
6. Once the exact value is known, encode that combination as the stable Bowser stress preset.

This turns the startup preset into a reproducible final recipe discovered through a flexible live tool.

## Build-budget policy

The full Phase 5 Windows workflow is currently guarded with `if: false` to avoid consuming the remaining monthly hosted Actions budget. The source-side debug console is staged now; it should be included in the next deliberately batched milestone-quality rebuild rather than triggering a hosted rebuild merely for this tool.
