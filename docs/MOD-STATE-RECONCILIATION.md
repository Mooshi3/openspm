# OpenSPM mod state reconciliation

OpenSPM treats installed content, unlocked content, owned content, currently carried/active content, and deliverable/recoverable content as separate concepts.

## Hard invariants

- The extracted R8PE01 game root is immutable/read-only input. OpenSPM and supported mods never modify the dump on disk.
- Installing or enabling a mod does not automatically grant its content.
- Disabling a mod must not permanently destroy or rewrite vanilla state merely because the mod changed runtime behavior.
- Mod-owned persistent state is namespaced outside the vanilla save representation.
- Vanilla gameplay limits remain in force unless a mod/debug option explicitly changes them. Technical registry limits may be lifted; intentional balance limits are preserved.
- Reconciliation is transactional: either a coherent campaign/save revision is committed, or the previous coherent revision remains authoritative.
- Ownership is authoritative. A capacity failure, missing definition, disabled mod, or migration must never silently delete legitimately owned content.

## State layers

### 1. Vanilla persistent state

Nintendo-authored progression and player state: story flags, coins, vanilla inventory entries, etc. OpenSPM preserves the semantics of the original save.

### 2. Mod-owned persistent state

Each mod receives a stable namespace keyed by mod ID. This stores things the vanilla save cannot represent safely, including additive item ownership, custom-world progression, custom-character unlock flags, custom quest state, custom maps visited, and other mod-specific data.

Example namespace:

```text
slot1/
  vanilla-save
  mods/
    example.goomba_capsule/
      state
    example.world9/
      state
```

The physical on-disk save/container format is deliberately not frozen yet. The semantic ownership and isolation rules are.

### 3. Runtime/transient state

Effects that should disappear when a mod is disabled or the game is relaunched, unless the mod deliberately persists them through its own namespace. For example, a "delete every NPC" mod can remove actors from the loaded world without rewriting the base game data or vanilla save so that those NPCs remain gone forever.

## Registration is not unlocking

A mod registering a content definition only tells OpenSPM that the definition exists. It does not imply that the player can use it yet.

Example: a World 9 mod may register Waluigi at startup, but the character-select service should only include Waluigi when the mod's persisted unlock condition is satisfied, such as defeating the mod's Waluigi boss.

Conceptually:

```text
installed? -> definition can be registered
unlocked?  -> definition may appear in gameplay UI/services
owned?     -> player possesses the content where ownership applies
active/carried? -> content currently occupies an allowed gameplay slot/state
queued?    -> owned content is safely waiting for delivery/recovery
```

## Inventory, storage, and mail

OpenSPM may support an effectively unlimited number of registered item *types*, but default SPM gameplay keeps intentional capacity limits:

- **Carried inventory: 10 slots.** This is the combat/adventure loadout and should remain balance-relevant by default.
- **Shared Flipside/shop storage: 32 slots.** This is normal finite inventory management used around the shopping/cooking hub.
- **OpenSPM mail queue: unbounded for practical purposes.** Mail is delivery/recovery infrastructure, not extra combat inventory.

The mail queue is surfaced diegetically through a **second shopkeeper in the Flipside shop acting as a mailman**. The player can collect recoverable/delivered content there while already in the hub where cooking, shopping, and inventory management happen.

Mail must not act as a stealth extension of carried capacity during field gameplay. Items in mail are not usable until legitimately collected into carried inventory or storage. Its purpose is to guarantee that reconciliation, mod re-enables, rewards, migrations, and capacity conflicts always have a lossless destination.

This yields a deterministic destination policy for normal item reconciliation:

1. Preserve a legal carried position when possible.
2. Otherwise preserve/place the item in the 32-slot shared shop storage when possible.
3. Otherwise enqueue it in OpenSPM mail.
4. Never delete an owned item merely because all immediately usable capacity is full.

Example:

- The vanilla carried inventory is full at 10 items.
- Shared shop storage is also full at 32 items.
- Goomba Capsule Mod's namespace says the player owns 3 Goomba Capsules that should return after the mod is re-enabled.

OpenSPM does not expand combat capacity, overwrite vanilla items, or discard the capsules. It queues the 3 capsules as mail and can inform the player, e.g.:

```text
Inventory and storage are full. 3 Goomba Capsules were sent to the Flipside mailman.
```

When the player creates legal space and claims them, ownership moves transactionally from the mail queue to the selected usable destination.

## Item identity

OpenSPM needs stable namespaced *definitions* such as `mooshi.goomba_capsule`, but not every physical item instance needs a UUID. Fungible stack/count-style items can use definition identity plus authoritative quantities/locations. Per-instance identity should be reserved for content whose individual instance carries meaningful persistent state.

This keeps ordinary item mods simple while still allowing future unique equipment, generated objects, or quest artifacts to opt into stable instance identities.

## Load-time reconciliation

When a save loads, OpenSPM should build a logical state from the vanilla save plus enabled mod namespaces, then reconcile that state against gameplay constraints before entering normal play.

Suggested order:

1. Load and validate the untouched vanilla save state.
2. Load the last coherent OpenSPM campaign revision and namespaced ownership records.
3. Discover enabled OpenSPM packages and register their definitions.
4. Load each enabled mod's namespaced persistent state.
5. Evaluate unlock predicates and progression dependencies.
6. Reconcile carried/active state against current gameplay limits.
7. Route excess/recoverable item ownership through carried -> 32-slot storage -> mail without loss.
8. Validate the entire proposed merged revision.
9. Commit the revision atomically; on failure retain the previous coherent revision.
10. Report material reconciliation to the player.
11. Enter gameplay with a coherent merged state.

A disabled or missing mod definition does not erase its dormant namespaced ownership. It merely removes that content from the active merged game until a compatible definition becomes available again or an explicit migration/fallback is performed.

## Fallback and migration intent

A mod author may declare how owned content should migrate when a definition is replaced, renamed, or intentionally retired. The engine validates that declaration before applying it. The runtime must not invent lossy semantic substitutions merely because two items look mechanically similar.

Examples of valid future author-declared intent could include:

- namespaced definition rename with preserved quantity/state;
- replacement by a specific successor definition;
- conversion into another explicitly declared resource;
- return to mail pending a compatible version.

If the declared migration is invalid or unavailable, preservation beats convenience: keep the ownership dormant/queued rather than destroy it.

## Character example

Scenario:

- A custom World 9 package registers Waluigi.
- The package defines Waluigi's unlock as completion of its boss encounter.
- The mod is installed but the boss has not been defeated.

Result: Waluigi exists in the registry but does not appear in character select.

After the boss is defeated and the mod namespace records the unlock, save reload evaluates the predicate and character select includes Waluigi.

Disabling the mod removes Waluigi from the merged registry/UI without rewriting unrelated vanilla progress. Re-enabling it later restores the character if the namespaced unlock state is still present.

## Removal/disable behavior

Disabling a mod hides its registered definitions and stops applying its runtime behavior. Its namespaced persistent state remains dormant by default so reinstalling/re-enabling the same mod ID can restore the player's mod-specific progress.

Mod disable must not be used as an inventory exploit. If disabling an item mod frees carried slots and the player fills those slots with vanilla items, re-enabling the mod does not create extra carried capacity. Returning mod-owned items are reconciled into the 32-slot shop storage and then mail until legitimate inventory room exists.

For v1, native DLL hot-unload is not required. Disabling a native package may take effect on the next clean launch. That is an implementation limitation, not a permanent SDK rule; the persistence semantics above must remain correct either way.

## API direction

The eventual OpenSPM SDK should expose semantic services rather than encouraging direct save-memory edits, for example:

```c
openspm_register_item(...);
openspm_register_character(...);
openspm_mod_state_get(...);
openspm_mod_state_set(...);
openspm_inventory_try_add(...);
openspm_storage_deposit(...);
openspm_mail_enqueue(...);
```

Direct mutation of vanilla persistent structures should be an advanced/explicit capability, not the default path for additive mods.

Creator ergonomics is a platform requirement: the common path for adding an item/character/actor should use stable namespaced definitions and semantic services, not guest addresses, Wii save layouts, manual slot replacement, or a full OpenSPM rebuild.

## Acceptance tests

1. **Delete Every NPC:** enable -> NPCs disappear at runtime -> save -> exit -> disable -> load -> vanilla NPCs are present again.
2. **Goomba Capsule full inventory:** mod owns 3 capsules -> carried inventory is full (10) -> storage is full (32) -> load -> both limits remain enforced -> capsules are queued to mail and preserved -> create space -> claim from Flipside mailman -> ownership moves without duplication/loss.
3. **Disable/re-enable item mod:** disable Goomba Capsule while owned -> vanilla state remains coherent -> use freed capacity for vanilla items -> re-enable -> capsules return through storage/mail rather than overwriting vanilla items or increasing carried capacity.
4. **Waluigi unlock:** mod installed before unlock -> Waluigi absent from character select -> defeat custom boss/save -> reload -> Waluigi present -> disable mod -> absent -> re-enable -> present again without touching unrelated vanilla progression.
5. **Transactional failure:** interrupt/reject a reconciliation or migration before commit -> previous coherent campaign revision remains loadable and authoritative.

These tests are part of the long-term definition of non-destructive OpenSPM modding.