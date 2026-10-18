# Function hooks

Status: **version 2 built, no hook enabled yet.** Built: the registry
(`state/openspm-hooks.json`, `tools/native_bridge/openspm_hooks.py`), enabled
entries reaching every host-target consumer, the backend's boot check and
dispatcher (`include/openspm/hooks.h`, `backend/native` v9/v12), and the
`openspm.hooks` service: pre callbacks (change integer and floating-point
arguments, or replace the call with an r3 / f1 result) and post callbacks
(see and change the result) through a return address. Version 1 mods (no
`add_post`) still load. Not yet: a first in-game run: both registry entries
are `enabled: false`, so the build and the game are unchanged. The foundation
for characters v2 (Kirby: own movement, hitbox and damage types) and most
future content.

### Turning a hook on

1. Set `"enabled": true` on its entry in `state/openspm-hooks.json`.
2. `python3 tools/native_bridge/openspm_hooks.py --write` (regenerates
   `tools/native_bridge/openspm_hook_targets.inc` and
   `include/openspm/hooks_table.inc`; `test_openspm_hooks.py` fails while
   they are out of date). `--check-dol main.dol` checks every entry's first
   instruction.
3. The next launch rebuilds the native module (the address becomes a host
   exit); the log says `OpenSPM hooks: n of n registry hooks match this game`
   at the first hooked call, and `OpenSPM hooks: a mod hooks <name>` per
   registration.

Checked offline for `spsndSFXOn` against the player-generated code: with the
entry enabled, the call rewrite (`direct_calls.rewrite_tree`) turns all 348
direct calls into host exits (0 with it off); nothing tail-jumps into it; its
entry is a dispatch case (`case 0x8023950Cu`) and its body is
`li r4..r8` then `b 0x80238C34`, so a continued call runs the game's own body
through the bridge's one-call bypass (`bridge_m14_hook_bypassed`). Not yet run
in game.

## Why

Everything OpenSPM adds today is data or scripts: relocated tables, patched
constants, EVT scripts started between frames, input masking. That reaches a
ceiling where the game's behaviour is compiled C keyed on ids the game knows
(characters 0-3, Pixls 220-231). The character audit (CHARACTER-AUDIT.md)
counts 232 reads of the current character: 155 compares, 51 table indexes, 15
arguments. Data tricks cover the indexes; compares and argument uses need code
to run when the game asks "which character is this, and what does it do".

A hook lets OpenSPM (and through it a mod) run host code in place of, before
or after a named game function, and fall through to the game's own body for
everything the mod doesn't own.

## What exists

- **Host targets.** Guest addresses listed in `state/m14-host-targets.json`
  (mirrored in `tools/native_bridge/m14_host_targets.inc`, kept in sync by
  `test_host_target_sync.py`) become host exits in the generated module: static
  calls are rewritten and the dispatcher's entry check catches indirect calls.
  The backend's hostcall handler (`v9_handle_hostcall`) gets the full register
  state (`openspm_m14_hostcall_state`) and returns with the state it wants.
- **Continue original.** A handler returns `event_target =
  OPENSPM_M14_HOOK_CONTINUE_ORIGINAL` with `pc` at the function entry; the
  bridge lets exactly that call (same entry, return address and stack pointer)
  run the recompiled body. A new call to the same function from inside the
  body is hooked again.
- **LLVM path lists.** The LLVM build also keeps host-first sets outside the
  manifest (`EXTRA_HOST_PRIORITY` in apply_llvm_dispatch_priority.py,
  `EXTRA_MAY_HOST` in apply_llvm_hostcall_prefilter.py). A new host target has
  to reach both, or the LLVM path runs it as ordinary guest code.

## Design

### 1. One registry of hookable functions (build time)

`state/openspm-hooks.json`, next to the code patch recipes, owned by OpenSPM
per game revision:

```json
{ "schema_version": 1, "game_id": "R8PE01", "revision": 2,
  "hooks": [
    { "name": "marioSetCharacter", "address": "0x80146ED4",
      "expect": "0x9421FFF0", "args": ["s32 character"], "returns": "void",
      "why": "asserts 0 <= id < 4; characters 4+ are handled by OpenSPM" }
  ] }
```

- `expect` is the first instruction word, checked in the codegen DOL at build
  time and in guest RAM at boot, exactly like code patch recipes: fail closed
  (the hook is left out and logged, the game runs unhooked).
- The build merges these addresses into the host targets and into both LLVM
  host-first sets from this one file. No hand-edited mirrors: the .inc becomes
  generated (the sync test keeps guarding it).
- Mods can only hook functions in this registry. Adding one is an OpenSPM
  change plus one rebuild of the native module; installing a mod never
  rebuilds anything.

### 2. Runtime dispatch (backend)

The hostcall handler looks the address up in a small table (hook id ->
callbacks). Per call:

1. **pre** callbacks run in registration order. Each sees a `openspm_hook_call`
   (below) and may change arguments, or **replace** the call: set the return
   value and skip the original.
2. If nobody replaced it, continue into the original body (the existing
   marker).
3. **post** callbacks: before continuing, the handler saves the real return
   address and sets `lr` to an OpenSPM return trampoline (one reserved host
   target address that is not code). When the body returns there, the handler
   runs the post callbacks (they see and may change the return value) and
   jumps to the saved return address. Saved return addresses go on a small
   per-guest-thread stack keyed by the stack pointer, so recursion and thread
   switches inside the body stay correct.

With no callbacks registered for an address the handler continues at once:
the cost of an unused hook is one host exit per call. Hot functions (called
many times per frame) are measured before they go into the registry.

### 3. Mod API: `openspm.hooks` v2 (built: include/openspm/hooks.h)

```c
typedef struct openspm_hook_call {
    openspm_abi_header header;
    uint32_t hook_id;          /* from find() */
    uint32_t args[8];          /* r3..r10: writable in pre; in post, as at the call */
    uint32_t ret;              /* r3: set in pre to replace; readable/writable in post */
    uint32_t return_address;   /* the caller (read only) */
    int replace;               /* pre: 1 = skip the original, use ret/fret */
    /* version 2 */
    double fargs[8];           /* f1..f8, writable in pre */
    double fret;               /* f1, like ret */
    int has_fpr;               /* 0: the bridge carries no FPRs (fargs/fret unused) */
    int post;                  /* 1 in a post callback */
} openspm_hook_call;

typedef struct openspm_hook_service {
    openspm_abi_header header;
    void *context;
    /* By registry name; OPENSPM_ERROR_NOT_FOUND if this build lacks it. */
    int(OPENSPM_CALL *find)(void *context, const char *name, uint32_t *out_hook_id);
    int(OPENSPM_CALL *add_pre)(void *context, uint32_t hook_id, openspm_hook_fn fn, void *user);
    int(OPENSPM_CALL *add_post)(void *context, uint32_t hook_id, openspm_hook_fn fn, void *user); /* v2 */
} openspm_hook_service;
```

How the pieces work:

- **Floating-point registers.** The bridge's hostcall state carries f0..f31
  (appended to `openspm_m14_hostcall_state`, present when its `struct_size`
  covers them). A handler that changes one has it copied back into ps0 and
  ps1. Every handler still accepts the shorter state of an older bridge; hooks
  then see `has_fpr = 0`.
- **Post callbacks.** A continued call with post callbacks records the real
  return address, stack pointer and arguments in a frame and sets `lr` to
  `OPENSPM_HOOK_RETURN_ADDRESS` (0x80002FF0, below the game's code, never
  executed). The body returns there; that host exit finds the newest frame
  with the same stack pointer (nested calls and other threads have their own),
  runs the post callbacks and returns to the real caller. At most 128 calls
  can be under way at once; a call that finds the table full runs without its
  post callbacks (counted, never a crash). A replaced call runs its post
  callbacks at once. The return address is a host target only in builds with
  a hook enabled.

Rules:

- Registration at STARTUP only (like content); a disabled mod registers
  nothing, so vanilla is untouched.
- Callbacks run on the guest thread, inside the frame, at the moment the game
  calls the function. `openspm.guest_memory` is open during a callback; guest
  calls (`openspm.guest_call`) are not (they would re-enter the game's
  thread), and a callback must be quick (no file I/O, no waits).
- Stack arguments beyond r10 and structs by value are out of scope for v1:
  registry entries say `args` explicitly and only register-passed signatures
  are accepted.
- OpenSPM's own content code (the roster) uses the same service: the
  characters and Pixls built so far move onto hooks where that is cleaner
  (button injection and placeholder items go away).

### 4. Testing

- Registry: a host test checks every entry's `expect` word against a DOL
  given on the command line (like `test_table_refs.py`), and that the build
  merges entries into the target set and both LLVM sets.
- Dispatch: a host test drives the handler with a fake register state:
  pre/replace, continue, post through the trampoline, nested calls, two
  threads.
- In game: a first hook with a visible, harmless effect before any content
  depends on it (see the order below), with a log line per hook the first time
  it fires ("OpenSPM hooks: marioSetCharacter fired, 1 callback").

## First hooks, in order

1. **Proof hook**: `spsndSFXOn` (0x8023950C, a plain `(const char *name)`):
   a debug option logs every sound effect the game starts. Harmless, easy to
   see, and useful for the missing-sound-effects investigation.
2. **Characters v2 core** (all in the registry, off; expect words checked
   against the DOL, every call site reroutes or meets the entry guard):
   - `marioSetCharacter` (0x80146ED4): the `0 <= id < 4` assert; a pre hook
     takes ids 4+.
   - `_marioChangeCharacter` (0x80146F68): stores the id, reloads the model,
     applies the properties.
   - `marioLoadCharacterModel` (0x80121CE0, variant 0 normal / 1 big): the
     model names come from 0x803E8148[id * 2 + variant]; a pre hook points a
     new id at its own model.
   - `marioApplyCharacterProperties` (0x801220C4, 13 callers): copies
     `characterProperties[id]` (0x803E8228, 20 bytes: hitbox width, height,
     walk, dash, one more) into MarioWork (+0x194/+0x19C width,
     +0x198/+0x1A0 height, +0x14C walk, +0x150 dash, +0x158). A post hook
     writes a character's own values: the simplest path to "own hitbox and
     speed".
   - `marioGetCharacterWidth` / `marioGetCharacterHeight` (0x80122300,
     0x80122324): float accessors of the same table (f1 results, hooks v2).
     Measure their call rate before enabling.
   The id compares (155 in the audit) mostly test for Peach (1), Bowser (2)
   or Luigi (3): an id 4+ fails them all and gets Mario's plain behaviour;
   abilities come from the mod.
3. **Ability dispatch**: the compare sites the audit lists per function, taken
   one function at a time with the mod supplying the behaviour.
4. **Pixls**: `marioFairyUse` (0x80130B70, asserts `220 <= id < 232`) and the
   follower setup (0x8011CF60), replacing the "no game Pixl selected" trick.

## Open questions

- Some same-chunk calls are plain `goto label_<entry>` without the cycle
  budget check (e.g. marioSetCharacter's call to `_marioChangeCharacter`);
  `direct_calls` leaves those, so they reach the hook through the entry
  guard instead of a side exit. Same result: the guard's host check runs the
  hook, and the continued call re-enters through the dispatcher (two checks).
- Recompiled bodies that were inlined into their callers are invisible to a
  hook on the callee. The registry build step should scan for known inlined
  copies (same first instructions elsewhere) and warn.
- Save data: characters 4+ must never reach the game's save; a hook on the
  save writer (or the sidecar planned in CONTENT-API.md) maps them back to
  their base character.
