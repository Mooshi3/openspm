# M13 Phase 5 item/save bridge research

Status: evidence-gathering for the first additive item vertical slice. This document records what is verified against the project's exact generated R8PE01 module versus what remains provisional. It does **not** freeze the public item SDK.

## Sources and revision discipline

The authoritative executable evidence for this project is the generated C module in the preserved `spm-m10-portable-source.zip` lineage used to build the working runtime. Public symbol/documentation sources are used for names and structure hypotheses, then checked against that exact generated module.

Public references used:

- SeekyCt `spm-decomp` commit `4952a692aed3410ba7a9621aabde4596aaa87fc9`
- `spm-headers/linker/spm.us2.lst` as the working US revision-2 symbol map
- `spm-headers/include/spm/mario_pouch.h`
- `spm-headers/include/spm/nandmgr.h`
- `spm-headers/include/spm/item_data.h`
- `spm-headers/include/spm/evt_shop.h`

The US2 map agrees with already-proven exact-module anchors such as `strcmp = 0x8025C9C8` and `GXSetProjection = 0x80292164`, but public addresses are still treated as hypotheses until the exact generated module contains matching code/semantics.

## Verified live pouch layout

Public headers describe `MarioPouchWork` as a `0x6A0`-byte structure copied in full to/from the save file, with:

```text
+0x020  keyItem[32]   u16
+0x060  useItem[10]  u16
+0x074  shopItem[32] u16
+0x0B4  characters[4]
+0x0C4  pixls[16]
...
size    0x6A0
```

The exact generated US2 module independently confirms the inventory offsets and capacities:

- `pouchAddItem` scans/stores the usable-item array at `pouch + 0x60` and explicitly uses a ten-slot loop for item IDs 65..216.
- `pouchAddShopItem` scans/stores the shop array at `pouch + 0x74` and explicitly bounds the scan at 32 slots.

Therefore the OpenSPM gameplay rule is grounded in the actual executable, not merely documentation:

> vanilla carried capacity = 10, vanilla shared shop storage capacity = 32.

OpenSPM may make the definition registry additive, but these active gameplay capacities remain unchanged unless a mod intentionally changes the ruleset. Returning mod items that cannot legally fit go to OpenSPM mail rather than evicting existing content or increasing these arrays.

## Verified US2 pouch functions

The following entries are present in the exact generated module and match the US2 symbol map/semantics:

| Function | R8PE01 US2 address | Evidence |
| --- | ---: | --- |
| `pouchGetPtr` | `0x8014B9EC` | exact body returns `0x804D0428` |
| `pouchAddItem` | `0x8014CF5C` | exact body dispatches by item class; usable branch scans ten slots at `+0x60` |
| `pouchCheckHaveItem` | `0x8014D558` | exact-module entry + US2 map |
| `pouchRemoveItem` | `0x8014D8B0` | exact-module entry + US2 map |
| `pouchAddShopItem` | `0x8014E39C` | not named in current US2 list; exact body verifies usable-item range, scans 32 entries at `+0x74`, stores item and returns success/failure |
| `pouchRemoveShopItem` | `0x8014E4CC` | exact body + US2 map; scans the `+0x74` array |
| `pouchCountUseItems` | `0x8014F384` | exact-module entry + US2 map |
| `pouchCountShopItems` | `0x8014F4DC` | exact-module entry + US2 map |
| `pouchCheckFreeUseItem` | `0x8014F714` | exact body semantically verified: tests the ten `+0x60` entries and returns true on an empty slot |

### Exact live pouch address

`pouchGetPtr` is only three guest instructions on this revision:

```text
lis  r3, 0x804D
addi r3, r3, 0x0428
blr
```

So the current R8PE01 live `MarioPouchWork` is:

```text
0x804D0428
```

This address is revision-specific implementation detail and must **not** become a public OpenSPM ABI value. A future game service should resolve it internally and expose semantic inventory operations.

## Verified save boundary

Public `nandmgr.h` describes `SaveFile` as:

```text
+0x0000 flags
+0x0008 SpmarioGlobals
+0x1B10 MarioPouchWork pouch
+0x25B0 checksum
+0x25B4 checksumNOT
size    0x25B8
```

The US2 symbol map and exact generated module provide:

```text
nandUpdateSave = 0x8023D0CC
nandLoadSave   = 0x8023D2E0
```

The exact translated code proves the pouch copy in both directions:

### Save/update direction

`nandUpdateSave`:

1. resolves the selected `SaveFile` (`slot * 0x25B8`),
2. calls `pouchGetPtr`,
3. copies exactly `1696` bytes (`0x6A0`) from live pouch,
4. destination is `SaveFile + 6928` (`0x1B10`).

Conceptually:

```c
memcpy(&save[slot].pouch, pouchGetPtr(), 0x6A0);
```

### Load direction

`nandLoadSave`:

1. resolves selected `SaveFile`,
2. calls `pouchGetPtr`,
3. copies exactly `1696` bytes (`0x6A0`),
4. source is `SaveFile + 6928` (`0x1B10`).

Conceptually:

```c
memcpy(pouchGetPtr(), &save[slot].pouch, 0x6A0);
```

This is a high-value OpenSPM seam. Rather than making every mod understand Nintendo save layout, OpenSPM can eventually own a save transaction/reconciliation layer around these semantic save/load boundaries.

### Architectural implication

A future save operation should conceptually become:

```text
SPM requests save
    -> snapshot vanilla live state
    -> OpenSPM canonical/mod state transaction
    -> reconcile active projections
    -> commit one campaign revision
    -> permit/write vanilla-compatible save representation
```

Load can mirror that process:

```text
load vanilla representation
    -> discover enabled packages
    -> load canonical mod state for same committed revision
    -> resolve definitions/progression/ownership
    -> project legal carried/storage state
    -> overflow returning mod content to mail
    -> enter play
```

The physical transaction format remains deliberately undecided. The requirement is atomic/recoverable campaign revisions, not necessarily one monolithic file or SQLite database.

## Item definitions: the central additive challenge

US2 maps:

```text
itemDataTable = 0x803B7E78
```

Public headers define each `ItemData` as `0x2C` bytes and the vanilla table as 538 entries. It contains presentation/economy metadata such as icon, name/description message keys, buy/sell price, HP/XP values and sort value.

The exact generated module also makes the additive problem concrete:

- vanilla `pouchAddItem` has hard-coded vanilla item-class numeric ranges;
- many translated call sites construct/use the vanilla `itemDataTable` base directly rather than going through one obvious universal accessor;
- the generated source contains many references to the table base, so simply allocating entry 539 and changing one pointer is unlikely to be sufficient.

Therefore the first additive implementation should **not** expose guest numeric IDs as persistent mod identity.

Required identity layering remains:

```text
persistent key:  mooshi.goomba_capsule
        -> OpenSPM session handle / definition
        -> private guest-visible representation chosen by the R8PE01 adapter
```

The adapter may use an extended/shadow item-data mechanism, translated lookups, generated-code hooks, or another implementation after instrumentation identifies the smallest robust seam. That choice stays private.

## Shop insertion seam

US2 maps and the exact module contain:

```text
evt_shop_set_defs = 0x80111708
```

Public `evt_shop.h` describes:

```c
typedef struct {
    s32 type;
    const char *npcName;
    s32 *items;        // [itemId, price, arg] repeated, terminated by -1
    const char *case1Name;
    const char *case2Name;
} EvtShopDef;

// evt_shop_set_defs(EvtShopDef *defs, s32 count)
```

This is promising for the Goomba Capsule vertical slice because OpenSPM can instrument one semantic shop-definition boundary instead of permanently rewriting a REL or extracted game file. The next research step is to observe which definitions are supplied when the Flipside shop initializes and build an adapter-owned merged definition list.

Target behavior remains declarative for mod authors, e.g. conceptually:

```toml
[[shops.flipside]]
item = "mooshi.goomba_capsule"
price = 0
```

The R8PE01 adapter, not the mod, should handle `evt_shop_set_defs`/guest representation details.

## Immediate implementation plan from this evidence

1. Land backend-neutral runtime lifecycle/present/perf services first.
2. Add an **internal R8PE01 research service**, not public SDK, capable of safe CPU-thread reads/observations and adapter-specific function interception.
3. Instrument `evt_shop_set_defs (0x80111708)` and the pouch/save boundaries above.
4. Use the Flipside Shop debug target/test save to record actual shop definitions and item-use flow.
5. Implement a private additive-item projection sufficient for one namespaced item without replacing a vanilla definition.
6. Only after the vertical slice works, freeze the high-level item/content API.

## Acceptance implications for Goomba Capsule

The first real item proof must show all of these simultaneously:

- persistent identity is namespaced (`mooshi.goomba_capsule`), not a vanilla numeric ID;
- no vanilla item definition is overwritten;
- Flipside shop presents the new item for 0 coins;
- obtaining it consumes one of the real ten carried slots;
- use routes to native/OpenSPM behavior and spawns a Goomba;
- vanilla save remains valid/compatible as designed;
- mod-owned state is canonical outside Nintendo's fixed item representation;
- disable removes custom items from active pouch projection without corrupting vanilla state;
- re-enable reconciles returning ownership into carried/storage where legal;
- anything that cannot fit the 10 + 32 legitimate capacities goes to the unlimited Flipside mail queue;
- existing vanilla carried/storage content is never evicted to make returning mod content fit.

## Things still deliberately unknown

- The final guest-visible additive item ID/projection strategy.
- The complete stock inventory-menu item-use dispatch seam.
- The exact Flipside shop definition object/REL source at runtime.
- The final transactional campaign storage format.
- Whether standard fungible item ownership needs persistent per-instance IDs; do not impose that until a real requirement appears.

Those are now bounded research questions rather than vague architecture problems.
