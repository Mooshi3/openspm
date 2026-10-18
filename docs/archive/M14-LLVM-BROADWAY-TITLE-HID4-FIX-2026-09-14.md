# M14 LLVM Broadway title/HID4 fix — 2026-09-14

## Symptom

The first successfully linked LLVM-native OpenSPM candidate entered the guest Program exception vector at `0x700` after 211,882 cycles. The first-fault dump reported:

- SRR0 `0x8026BBD0`
- Program cause `0x00080000` (`illegal`)
- raw PPC word `0x7C73FBA6`

The same generation run also printed:

`database/titles.txt is missing; using GameCube mode. Run dolrecomp --setup to download it.`

followed later by `cpu: Broadway (Wii)` because `--cpu broadway` was explicit.

## Root cause

There were two independent Wii/GC identity problems.

### 1. DolRecomp title classification

Pinned DolRecomp `40637c4683bd2820ac5b23607ee344720beb26df` forces `effective_gamecube_mode=1` and `titleless_mode=1` whenever `database/titles.txt` is absent, even if the caller explicitly selected `--cpu broadway`. That is why the DOL banner said `game: GameCube DOL` while the CPU banner said `Broadway (Wii)`.

OpenSPM now creates a one-entry private `database/titles.txt` under the local LLVM generation directory and runs DolRecomp with that directory as its cwd. It also exports `DOLRECOMP_GAMETDB_TITLES` to the same private file. No network/GameTDB download is required. Existing titleless `generated/` output is migrated once to `<gameid>_generated/` so exact stamped LLVM objects remain reusable.

### 2. Broadway HID4 missing from pinned runtime SPR access table

`0x7C73FBA6` decodes to `mtspr 1011,r3`. SPR 1011 is Broadway/750CL HID4. DolRecomp's generic runtime has an SPR callback path, but its static `ppc_spr_access[1024]` table omitted 1011. Therefore `ppc_mtspr()` raised `PPC_PROGRAM_ILLEGAL` before OpenSPM's Wii SPR callback could receive the write.

The LLVM module builder now patches only the staged private CPU runtime copy, adding `[1011] = SPR_RW`. No generated guest object changes are required. Reads/writes then flow through the already-installed OpenSPM SPR callbacks and are stored in the existing standalone Wii SPR array.

This patch does not mutate the pinned compiler checkout and does not alter the normal generated-C runtime.

## Expected next-run proof

Generation must no longer print the missing-database/GameCube-mode warning. It should identify the private Wii title and still report `cpu: Broadway (Wii)`.

Before linking, the module builder should print:

`[OpenSPM] Staged DolRecomp runtime admits Broadway HID4 SPR 1011; reads/writes delegate to the existing OpenSPM Wii SPR callback.`

At runtime the previous immediate Program/Illegal exception at SRR0 `0x8026BBD0` / raw `0x7C73FBA6` should disappear. If another first-fault dump occurs, treat its new SRR0/raw opcode as the next actual Broadway-runtime gap rather than reopening HID4 or GX seams.

## Commits

- `2bb88f8d00ba43a912d6ff6e17458c120b8bfd67` — staged Broadway HID4 runtime support
- `a6466def845651906f8d1017c0c01dcebc76335e` — deterministic private Wii title database and titleless-tree migration
