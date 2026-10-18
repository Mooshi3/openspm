/* OpenSPM content service for mods ("openspm.content", docs/CONTENT-API.md).
 *
 * Mods describe new content; OpenSPM assigns ids, validates it, puts it into
 * the game and takes it out again. No mod ever picks a game id itself, so two
 * mods adding characters never collide.
 *
 * Characters (version 1): register_character() during OPENSPM_MOD_EVENT_STARTUP
 * (and before the first in-game frame). OpenSPM gives the character a roster
 * id (4, 5, ...: 0-3 stay Mario, Peach, Bowser, Luigi) and lists it in the
 * pause menu's character tab after the game's own characters. Picking it
 * makes the game switch to the character's base character (0-3: abilities,
 * flip, voice, save data all stay that character's) and shows the
 * character's own model; picking another character, leaving the game or
 * disabling the mod restores everything. Nothing is written to the save.
 * The mod reads character_active() each frame to run the character's own
 * ability (see mods/striker).
 *
 * With the rebuilt native module (item table relocation) each character also
 * gets his own item after the added Pixls: his name, description and icon in
 * the character tab. Limits of version 1: at most
 * OPENSPM_CONTENT_MAX_CHARACTERS characters in total; the character list
 * needs OpenSPM's build patch (a rebuilt native module) for more than one.
 *
 * Portable C; version 1; later versions only append fields. */
#ifndef OPENSPM_CONTENT_SERVICE_H
#define OPENSPM_CONTENT_SERVICE_H

#include <stddef.h>
#include <stdint.h>

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_CONTENT_SERVICE_ID "openspm.content"
#define OPENSPM_CONTENT_SERVICE_VERSION 1u
#define OPENSPM_CONTENT_MAX_CHARACTERS 60u
#define OPENSPM_CONTENT_FIRST_CHARACTER_ID 4u
#define OPENSPM_CONTENT_MAX_PIXLS 32u
#define OPENSPM_CONTENT_FIRST_ITEM_ID 538u /* added items follow the game's 538 */

typedef struct openspm_character_def {
    openspm_abi_header header;
    /* Stable, unique: "<mod>:<name>", 3..47 characters of [a-z0-9_.:-]. */
    const char *key;
    /* Shown in the menu: 1..31 printable ASCII characters. */
    const char *name;
    /* Menu description: 0..255 printable ASCII characters (NULL = none). */
    const char *description;
    /* The game model (animation pose file) to show, e.g. "e_k_shooter":
       1..31 characters of [a-z0-9_]. Animations the model lacks show its
       first animation instead (the game's own fallback). */
    const char *model;
    /* 0 Mario, 1 Peach, 2 Bowser, 3 Luigi: what the game plays underneath. */
    uint32_t base_character;
    uint32_t flags; /* OPENSPM_CHARACTER_* below; other bits 0 */
    /* Appended (read when header.struct_size covers it): the game item whose
       menu icon the character tab shows for him (0: the base character's). */
    uint32_t icon_item;
    /* Appended: which of the model's animations plays when the game asks for
       one of the base character's, "<game name>=<model name>" pairs split by
       ',' (e.g. "J_1B=J_1_a,J_1C=J_1_b"; NULL = none). The game asks Mario's
       and Luigi's models for "mario_W_1", "mario_W_1R" (seen from behind),
       "Big_mario_W_1" ...; OpenSPM adds those names to the character's model,
       each playing the model's animation of the same name, the mapped one, or
       else "S_1". 0..255 characters of [A-Za-z0-9_=,]. */
    const char *anim_map;
    /* Appended: the character's voice instead of the base character's, per
       kind: "damage=<sound>,acro=<sound>,attack=<sound>" (hurt, stylish
       moves, attacks); <sound> is a game sound name ("SFX_E_KICK_KOURA1") or
       "none" (silent); a kind left out keeps the base character's voice.
       NULL = the base character's voice. [A-Za-z0-9_=,], 0..255. */
    const char *voice;
    /* Appended: the character's own size and speed instead of the base
       character's, "<property>=<number>" pairs split by ',':
       width (hitbox, 4..80), height (hitbox, 8..100), walk (0.2..6) and dash
       (0.2..10) walking and running speed, in the game's units (for scale:
       Mario is about 20 wide and 40 tall). A property left out keeps the base
       character's. NULL = all the base's. [a-z0-9.=,], 0..127. */
    const char *properties;
} openspm_character_def;

/* The character can not flip to 3D (OpenSPM holds back the flip button, A,
   while he is played); flipping stays the base character's otherwise. */
#define OPENSPM_CHARACTER_NO_FLIP 0x1u
#define OPENSPM_CHARACTER_FLAGS_KNOWN 0x1u
#define OPENSPM_CHARACTER_DEF_V1_SIZE (offsetof(openspm_character_def, flags) + sizeof(uint32_t))

/* Pixls (appended to version 1): register_pixl() at STARTUP. OpenSPM gives
   the Pixl its own item id (538, 539, ...) in the relocated item table (name,
   description, menu icon and model are its own), lists it in the pause menu's
   Pixl tab after the game's Pixls, and when the player picks it, puts away
   the game's Pixl (none selected in the save) so the mod's Pixl is the one in
   play: pixl_active() says so, and the mod runs its follower and ability
   (mods/glock). Needs the rebuilt native module (item table relocation). */
typedef struct openspm_pixl_def {
    openspm_abi_header header;
    const char *key;         /* "<mod>:<name>", as for characters */
    const char *name;        /* 1..31 printable ASCII */
    const char *description; /* 0..255 printable ASCII */
    const char *model;       /* game animation pose shown in the Pixl menu, e.g. "e_kozun" */
    const char *menu_anim;   /* animation of that pose for the menu (NULL: "S_1") */
    uint32_t icon_item;      /* use this game item's menu icon (0: a Pixl-like default) */
    uint32_t flags;          /* reserved: 0 */
} openspm_pixl_def;

/* Followers (appended to version 1): an NPC who walks behind the player through
   the whole game, doors, pipes and cutscenes included (the way Luvbi or a
   party member trails along). register_follower() at STARTUP; OpenSPM makes
   him as a game NPC with the model and moves him along the player's trail
   (include/openspm/content_followers.h). set_follower() shows or hides him
   later (a follower who joins after a story event). */
typedef struct openspm_follower_def {
    openspm_abi_header header;
    const char *key;        /* "<mod>:<name>", as for characters */
    const char *model;      /* the game model (animation pose file), e.g. "n_stg5_hiroshi" */
    const char *stand_anim; /* the model's animations: standing (NULL: "S_1"), */
    const char *walk_anim;  /* walking (NULL: "W_1"), */
    const char *run_anim;   /* running, when he falls behind (NULL: "R_1"); [A-Za-z0-9_], 1..23 */
    float distance;         /* how far behind the player he walks, along the trail (0: 45; at most 400) */
    uint32_t flags;         /* OPENSPM_FOLLOWER_* below */
} openspm_follower_def;

/* He floats at the player's height (a jump lifts him too) instead of keeping
   to the ground. */
#define OPENSPM_FOLLOWER_FLOATING 0x1u
/* He is hidden until set_follower(id, 1). */
#define OPENSPM_FOLLOWER_START_HIDDEN 0x2u
#define OPENSPM_FOLLOWER_FLAGS_KNOWN 0x3u
#define OPENSPM_FOLLOWER_DEF_V1_SIZE (offsetof(openspm_follower_def, flags) + sizeof(uint32_t))

/* A new map (docs/CUSTOM-LEVELS.md): registered from a mod's tick (the
   init script is guest memory the mod built). Its files come from the mod's
   files folder: map/<file>.bin (and setup/<name>.dat for placed enemies). */
typedef struct openspm_map_def {
    openspm_abi_header header;
    const char *key;            /* "mod.id:level" (logs) */
    const char *name;           /* the map's name, [a-z0-9_], under 32 characters; its first three letters are its
                                   area. Not a game map's name. */
    const char *file;           /* map/<file>.bin; NULL = name */
    const char *fallback_door;  /* entered with no door name: this door (NULL: the spawn position) */
    float spawn[3];             /* entered with no door name and no fallback door: here */
    uint32_t init_script;       /* guest address of the map's init script (EVT), 0 for none */
    /* Appended: the camera. A map without a camera road (camera_road.bin, the
       game's per-map camera paths) leaves the game's camera where it was;
       with camera_distance set OpenSPM moves it after the player instead:
       side on in 2D, from behind (the player's left) in 3D, swinging between
       the two on a flip. */
    float camera_distance;      /* from the player to the camera, 100-5000; 0 = leave the camera alone */
    float camera_height;        /* the camera above the player's feet */
    float camera_look_height;   /* the point it looks at, above the player's feet */
    float camera_x_range[2];    /* the point it looks at stays in this x range; both 0 = anywhere */
} openspm_map_def;

#define OPENSPM_MAP_DEF_V1_SIZE (offsetof(openspm_map_def, init_script) + sizeof(uint32_t))
#define OPENSPM_MAP_DEF_V2_SIZE (offsetof(openspm_map_def, camera_x_range) + 2u * sizeof(float))

/* A new enemy (docs/CUSTOM-LEVELS.md "Enemies"): a new template that behaves
   like a game template (its AI scripts and flags) and a new tribe that looks
   like a game tribe (model, animations, parts, drops) with its own numbers.
   Registered from a mod tick; the template number it gets is what a map's
   script (evt_npc_entry_from_template) or setup file spawns it with. */
typedef struct openspm_enemy_def {
    openspm_abi_header header;
    const char *key;            /* "mod.id:enemy" (logs) */
    uint32_t base_template;     /* behaves like this game template, 1-434 */
    int32_t look_tribe;         /* looks like this game tribe, 0-534; -1 = the base template's */
    const char *model;          /* NULL = the look tribe's; else a game model with the same animations */
    uint32_t max_hp;            /* 1-255; 0 = the look tribe's */
    uint32_t attack;            /* the attack the tattle shows, 1-255; 0 = the look tribe's */
    uint32_t contact_damage;    /* damage it does on touch, 1-99; 0 = what the base's spawn script sets */
    int32_t score;              /* points for defeating it, 0-32767; -1 = the look tribe's */
    uint32_t catch_card;        /* 0 = none; OPENSPM_ENEMY_CARD_LOOK = the look tribe's; else a game card item id */
    uint8_t color[4];           /* tint, RGBA; all 0 = none */
    float scale;                /* size, 0.1-10; 0 = as is */
    uint32_t spawn_script;      /* guest EVT run when one spawns, in place of the base's; 0 = the base's */
    uint32_t flags;             /* 0 */
} openspm_enemy_def;

#define OPENSPM_ENEMY_CARD_LOOK UINT32_C(0xFFFFFFFF)
#define OPENSPM_ENEMY_DEF_V1_SIZE (offsetof(openspm_enemy_def, flags) + sizeof(uint32_t))

typedef int(OPENSPM_CALL *openspm_content_register_character_fn)(void *context, const openspm_character_def *def,
                                                                 uint32_t *out_id);
typedef int(OPENSPM_CALL *openspm_content_character_active_fn)(void *context, uint32_t id);

typedef struct openspm_content_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    /* OPENSPM_OK and *out_id = roster id; OPENSPM_ERROR_INVALID_ARGUMENT for a
       bad definition (logged with the reason), OPENSPM_ERROR_RUNTIME when the
       roster is full, the key is taken or registration is closed. */
    openspm_content_register_character_fn register_character;
    /* 1 while the player is playing this character, else 0. Any thread. */
    openspm_content_character_active_fn character_active;
    /* Appended: present when header.struct_size covers them. */
    int(OPENSPM_CALL *register_pixl)(void *context, const openspm_pixl_def *def, uint32_t *out_item_id);
    int(OPENSPM_CALL *pixl_active)(void *context, uint32_t item_id);
    /* Appended: followers. OPENSPM_OK and *out_id (0, 1, ...); errors as
       register_character. set_follower: 1 shows him, 0 hides him. */
    int(OPENSPM_CALL *register_follower)(void *context, const openspm_follower_def *def, uint32_t *out_id);
    int(OPENSPM_CALL *set_follower)(void *context, uint32_t id, int shown);
    /* Appended: maps. register_map (from a mod tick): OPENSPM_OK and
       *out_slot (the registry slot), OPENSPM_ERROR_INVALID_ARGUMENT for a bad
       definition, OPENSPM_ERROR_UNSUPPORTED when this build has no map
       registry (rebuild the native module), OPENSPM_ERROR_RUNTIME when full.
       go_to_map (from a mod tick): the game's map change to `name`, entered
       by `door` (NULL: the map's fallback). */
    int(OPENSPM_CALL *register_map)(void *context, const openspm_map_def *def, uint32_t *out_slot);
    int(OPENSPM_CALL *go_to_map)(void *context, const char *name, const char *door);
    /* Appended: messages (include/openspm/content_messages.h). A key the
       game's message tables have keeps the game's text: prefix keys with
       something of the mod's own. Any time, any thread; registering a key
       again replaces its text. */
    int(OPENSPM_CALL *register_message)(void *context, const char *key, const char *text);
    /* Appended: enemies (include/openspm/content_enemies.h). register_enemy
       (from a mod tick): OPENSPM_OK and *out_template (435 and up, the number
       to spawn it with); errors as register_map. */
    int(OPENSPM_CALL *register_enemy)(void *context, const openspm_enemy_def *def, uint32_t *out_template);
} openspm_content_service;

#define OPENSPM_CONTENT_SERVICE_V1_SIZE (offsetof(openspm_content_service, character_active) + sizeof(void *))

static inline int openspm_content_accept(const openspm_content_service *out, uint32_t requested_version) {
    if (out == 0) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (requested_version != OPENSPM_CONTENT_SERVICE_VERSION || out->header.struct_size < OPENSPM_CONTENT_SERVICE_V1_SIZE)
        return OPENSPM_ERROR_ABI_MISMATCH;
    return OPENSPM_OK;
}

#ifdef __cplusplus
}
#endif

#endif
