/* Build Super Paper Mario EVT scripts from a mod (portable C).
 *
 * EVT is the game's own script bytecode (maps, cutscenes, items all use it).
 * A mod builds a script into a u32 buffer with these helpers, copies it into
 * guest memory it got from openspm.guest_call scratch() (big-endian words,
 * write32 does that), and starts it with a guest call to evtEntry
 * (SPM_R8PE01_EVT_ENTRY) - the game then runs it like any of its scripts.
 *
 * Encoding (the game's evtmgr): each instruction is a word
 * (argument count << 16) | opcode, followed by its arguments. Variables and
 * floats are encoded as large negative numbers: LW(n) = n - 30000000,
 * GW(n) = n - 50000000, LF(n) = n - 70000000, float f = f * 1024 - 240000000.
 * A script ends with END_EVT then END_SCRIPT. Opcode numbers follow the
 * public spm-decomp EvtOpcode enum. */
#ifndef OPENSPM_SPM_EVT_H
#define OPENSPM_SPM_EVT_H

#include <stdint.h>

enum {
    SPM_EVT_OP_END_SCRIPT = 1,
    SPM_EVT_OP_END_EVT = 2,
    SPM_EVT_OP_LBL = 3,
    SPM_EVT_OP_GOTO = 4,
    SPM_EVT_OP_WAIT_FRM = 9,
    SPM_EVT_OP_WAIT_MSEC = 10,
    SPM_EVT_OP_SET = 50,
    SPM_EVT_OP_SETF = 52,
    SPM_EVT_OP_ADD = 53,
    SPM_EVT_OP_USER_FUNC = 92,
    SPM_EVT_OP_RUN_EVT = 93,
    SPM_EVT_OP_RUN_CHILD_EVT = 95
};

#define SPM_EVT_LW(n) ((uint32_t)((int32_t)(n) - 30000000))
#define SPM_EVT_GW(n) ((uint32_t)((int32_t)(n) - 50000000))
#define SPM_EVT_LF(n) ((uint32_t)((int32_t)(n) - 70000000))
#define SPM_EVT_FLOAT(f) ((uint32_t)((int32_t)((f) * 1024.0f) - 240000000))
#define SPM_EVT_NULLPTR ((uint32_t)(int32_t)-270000000) /* "no output" for pointer/name arguments */

typedef struct spm_evt_builder {
    uint32_t *words;
    uint32_t capacity, count;
    int overflow;
} spm_evt_builder;

static inline void spm_evt_init(spm_evt_builder *b, uint32_t *words, uint32_t capacity) {
    b->words = words;
    b->capacity = capacity;
    b->count = 0u;
    b->overflow = 0;
}

static inline void spm_evt_word(spm_evt_builder *b, uint32_t w) {
    if (b->count < b->capacity) b->words[b->count++] = w;
    else b->overflow = 1;
}

static inline void spm_evt_cmd(spm_evt_builder *b, uint32_t opcode, uint32_t argc, const uint32_t *args) {
    uint32_t i;
    spm_evt_word(b, (argc << 16) | opcode);
    for (i = 0u; i < argc; ++i) spm_evt_word(b, args[i]);
}

static inline void spm_evt_set(spm_evt_builder *b, uint32_t var, uint32_t value) {
    const uint32_t a[2] = {var, value};
    spm_evt_cmd(b, SPM_EVT_OP_SET, 2u, a);
}

static inline void spm_evt_run_child(spm_evt_builder *b, uint32_t script) {
    spm_evt_cmd(b, SPM_EVT_OP_RUN_CHILD_EVT, 1u, &script);
}

/* USER_FUNC function, args... (up to 15 arguments). */
static inline void spm_evt_user_func(spm_evt_builder *b, uint32_t function, uint32_t argc, const uint32_t *args) {
    uint32_t i;
    spm_evt_word(b, ((argc + 1u) << 16) | SPM_EVT_OP_USER_FUNC);
    spm_evt_word(b, function);
    for (i = 0u; i < argc; ++i) spm_evt_word(b, args[i]);
}

static inline void spm_evt_add(spm_evt_builder *b, uint32_t var, uint32_t value) {
    const uint32_t a[2] = {var, value};
    spm_evt_cmd(b, SPM_EVT_OP_ADD, 2u, a);
}

static inline void spm_evt_wait_frames(spm_evt_builder *b, uint32_t frames) {
    spm_evt_cmd(b, SPM_EVT_OP_WAIT_FRM, 1u, &frames);
}

/* END_EVT + END_SCRIPT. Returns the word count, 0 if the buffer overflowed. */
static inline uint32_t spm_evt_finish(spm_evt_builder *b) {
    spm_evt_word(b, SPM_EVT_OP_END_EVT);
    spm_evt_word(b, SPM_EVT_OP_END_SCRIPT);
    return b->overflow ? 0u : b->count;
}

#endif
