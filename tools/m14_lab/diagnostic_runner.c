#define _GNU_SOURCE
#include "generated.h"
#include "openspm/wii_boot_platform.h"
#include "openspm/wii_platform.h"

#include <errno.h>
#include <inttypes.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Persistent M14 whole-game diagnostic harness.
 *
 * This intentionally contains no game code/assets. It links against a
 * player-local generated tree and the pinned local CPU helper runtime. Keep
 * bring-up fixes here (or promote them into core/) instead of leaving them in
 * a chat-only /mnt/data harness.
 *
 * The current harness preserves the reconstructed CPU/control semantics and
 * diagnostic logging. Higher-level GameFS, frame scheduling, graphics-finish,
 * input and audio host services should be added as explicit semantic services
 * here or promoted to OpenSPM core -- never as unrecorded one-off hacks.
 */

typedef struct {
    unsigned ext_reads;
    unsigned ext_writes;
    unsigned fallbacks;
    unsigned host_calls;
    unsigned spr_reads;
    unsigned spr_writes;
    unsigned cache_ops;
    u32 last_ext_addr;
    u32 last_fallback_cia;
    u32 last_host;
    u32 spr[1024];
    int quiet;
    openspm_wii_platform platform;
    openspm_wii_boot_platform boot_platform;
} Host;

typedef struct {
    const char *main_dol;
    unsigned long long max_yields;
    unsigned stuck_limit;
    unsigned progress_every;
    int quiet;
} Options;

static u32 be32(const unsigned char *p) {
    return ((u32)p[0] << 24) | ((u32)p[1] << 16) | ((u32)p[2] << 8) | p[3];
}

static void *guest_ptr(CPUState *cpu, u32 address, u32 size) {
    if (address >= 0x80000000u && address + size >= address &&
        address + size <= 0x80000000u + cpu->ram_size) {
        return cpu->ram + (address - 0x80000000u);
    }
    if (address >= 0x90000000u && address + size >= address &&
        address + size <= 0x90000000u + cpu->mem2_size) {
        return cpu->mem2 + (address - 0x90000000u);
    }
    return NULL;
}

static int load_dol(CPUState *cpu, const char *path, u32 *entry) {
    FILE *file = fopen(path, "rb");
    unsigned char header[0x100];
    u32 bss;
    u32 bss_size;
    int i;

    if (!file) {
        perror(path);
        return 0;
    }
    if (fread(header, 1, sizeof(header), file) != sizeof(header)) {
        fprintf(stderr, "short DOL header: %s\n", path);
        fclose(file);
        return 0;
    }

    /*
     * Critical regression guard: R8PE01's broad BSS envelope overlaps some
     * initialized DOL ranges. Zero BSS FIRST, then copy file-backed sections.
     * Reversing these two operations silently erases valid game data and can
     * turn later callbacks into bogus pointers.
     */
    bss = be32(header + 0xD8);
    bss_size = be32(header + 0xDC);
    if (bss_size) {
        void *pointer = guest_ptr(cpu, bss, bss_size);
        if (!pointer) {
            fprintf(stderr, "BSS unmapped address=%08x size=%x\n", bss, bss_size);
            fclose(file);
            return 0;
        }
        memset(pointer, 0, bss_size);
    }

    for (i = 0; i < 18; ++i) {
        u32 offset = be32(header + (i < 7 ? 0x00 + i * 4 : 0x1C + (i - 7) * 4));
        u32 address = be32(header + (i < 7 ? 0x48 + i * 4 : 0x64 + (i - 7) * 4));
        u32 size = be32(header + (i < 7 ? 0x90 + i * 4 : 0xAC + (i - 7) * 4));
        void *pointer;
        if (!size)
            continue;
        pointer = guest_ptr(cpu, address, size);
        if (!pointer) {
            fprintf(stderr, "DOL section unmapped address=%08x size=%x\n", address, size);
            fclose(file);
            return 0;
        }
        if (fseek(file, (long)offset, SEEK_SET) != 0 || fread(pointer, 1, size, file) != size) {
            fprintf(stderr, "DOL section read failed address=%08x size=%x\n", address, size);
            fclose(file);
            return 0;
        }
    }

    *entry = be32(header + 0xE0);
    fclose(file);
    return 1;
}

static u64 ext_read(CPUState *cpu, u32 address, u8 size) {
    Host *host = (Host *)cpu->external_user_data;
    uint64_t value = 0;
    int result;
    host->ext_reads++;

    result = openspm_wii_boot_platform_bus_read(&host->boot_platform, address, size, &value);
    if (result == OPENSPM_OK)
        return (u64)value;

    if (!host->quiet && (host->ext_reads < 80 || address != host->last_ext_addr)) {
        fprintf(stderr, "[ext-r-unhandled] pc=%08x a=%08x n=%u (#%u)\n",
                cpu->pc, address, size, host->ext_reads);
    }
    host->last_ext_addr = address;

    /* Unknown platform reads deliberately fail soft to zero in this bring-up
       harness so the next boundary can be observed. Production services must
       be explicit and semantic; do not grow an address-value magic table. */
    return 0;
}

static void ext_write(CPUState *cpu, u32 address, u64 value, u8 size) {
    Host *host = (Host *)cpu->external_user_data;
    int result;
    host->ext_writes++;

    result = openspm_wii_boot_platform_bus_write(&host->boot_platform, address, size, value);
    if (result == OPENSPM_OK)
        return;

    if (!host->quiet && (host->ext_writes < 120 || address != host->last_ext_addr)) {
        fprintf(stderr, "[ext-w-unhandled] pc=%08x a=%08x n=%u v=%llx (#%u)\n",
                cpu->pc, address, size, (unsigned long long)value, host->ext_writes);
    }
    host->last_ext_addr = address;
}

static void fallback(CPUState *cpu, u32 raw, u32 cia) {
    Host *host = (Host *)cpu->external_user_data;
    host->fallbacks++;
    if (!host->quiet && (host->fallbacks < 120 || cia != host->last_fallback_cia)) {
        fprintf(stderr, "[fallback] cia=%08x raw=%08x (#%u)\n",
                cia, raw, host->fallbacks);
    }
    host->last_fallback_cia = cia;

    /* Remaining startup/control instructions. Keep policy out of generated
       game code; the local CPU helper runtime owns their semantics. */
    if ((raw >> 26) == 31u) {
        u32 xo = (raw >> 1) & 0x3FFu;
        u32 reg = (raw >> 21) & 31u;
        u32 spr = ((raw >> 16) & 31u) | ((raw >> 6) & 0x3E0u);
        if (xo == 339u) {
            cpu->gpr[reg] = ppc_mfspr(cpu, (u16)spr, cia);
            cpu->pc = cia + 4u;
            return;
        }
        if (xo == 467u) {
            ppc_mtspr(cpu, (u16)spr, cpu->gpr[reg], cia);
            cpu->pc = cia + 4u;
            return;
        }
        {
            u32 ra = (raw >> 16) & 31u;
            u32 rb = (raw >> 11) & 31u;
            u32 ea = (ra ? cpu->gpr[ra] : 0u) + cpu->gpr[rb];
            if (xo == 54u) {
                ppc_cache_control(cpu, PPC_CACHE_DCBST, ea, cia);
                cpu->pc = cia + 4u;
                return;
            }
            if (xo == 86u) {
                ppc_cache_control(cpu, PPC_CACHE_DCBF, ea, cia);
                cpu->pc = cia + 4u;
                return;
            }
            if (xo == 470u) {
                ppc_cache_control(cpu, PPC_CACHE_DCBI, ea, cia);
                cpu->pc = cia + 4u;
                return;
            }
            if (xo == 982u) {
                ppc_cache_control(cpu, PPC_CACHE_ICBI, ea, cia);
                cpu->pc = cia + 4u;
                return;
            }
        }
    }

    fprintf(stderr, "[fallback-unhandled] cia=%08x raw=%08x\n", cia, raw);
}

static bool host_call(CPUState *cpu, u32 address) {
    Host *host = (Host *)cpu->external_user_data;
    host->host_calls++;
    if (address == PPC_HOST_CALL_NATIVE_REGION_QUERY) {
        cpu->external_rid = PPC_NATIVE_REGION_QUERY_HANDLED;
        return false;
    }
    if (!host->quiet && (host->host_calls < 100 || address != host->last_host)) {
        fprintf(stderr, "[host] pc=%08x call=%08x (#%u)\n",
                cpu->pc, address, host->host_calls);
    }
    host->last_host = address;
    return false;
}

static u32 spr_read(CPUState *cpu, u16 spr, u32 cia) {
    Host *host = (Host *)cpu->external_user_data;
    host->spr_reads++;
    if (!host->quiet && host->spr_reads < 120)
        fprintf(stderr, "[spr-r] cia=%08x spr=%u\n", cia, spr);
    if (spr == 287)
        return PPC_GEKKO_PVR;
    return spr < 1024 ? host->spr[spr] : 0;
}

static void spr_write(CPUState *cpu, u16 spr, u32 value, u32 cia) {
    Host *host = (Host *)cpu->external_user_data;
    host->spr_writes++;
    if (!host->quiet && host->spr_writes < 120)
        fprintf(stderr, "[spr-w] cia=%08x spr=%u v=%08x\n", cia, spr, value);
    if (spr < 1024 && spr != 287)
        host->spr[spr] = value;
}

static void cache_control(CPUState *cpu, u8 operation, u32 ea, u32 cia) {
    Host *host = (Host *)cpu->external_user_data;
    (void)cpu;
    host->cache_ops++;
    if (!host->quiet && host->cache_ops < 80)
        fprintf(stderr, "[cache] cia=%08x op=%u ea=%08x\n", cia, operation, ea);
}

static int parse_u64(const char *text, unsigned long long *out) {
    char *end = NULL;
    unsigned long long value;
    errno = 0;
    value = strtoull(text, &end, 0);
    if (errno || !end || *end)
        return 0;
    *out = value;
    return 1;
}

static int parse_u32_arg(const char *text, unsigned *out) {
    unsigned long long value;
    if (!parse_u64(text, &value) || value > 0xFFFFFFFFull)
        return 0;
    *out = (unsigned)value;
    return 1;
}

static void usage(const char *argv0) {
    fprintf(stderr,
            "usage: %s --main-dol PATH [--max-yields N] [--stuck-limit N] "
            "[--progress-every N] [--quiet]\n",
            argv0);
}

static int parse_options(int argc, char **argv, Options *options) {
    int i;
    memset(options, 0, sizeof(*options));
    options->max_yields = 5000000ull;
    options->stuck_limit = 100000u;
    options->progress_every = 10000u;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--main-dol") == 0 && i + 1 < argc) {
            options->main_dol = argv[++i];
        } else if (strcmp(argv[i], "--max-yields") == 0 && i + 1 < argc) {
            if (!parse_u64(argv[++i], &options->max_yields))
                return 0;
        } else if (strcmp(argv[i], "--stuck-limit") == 0 && i + 1 < argc) {
            if (!parse_u32_arg(argv[++i], &options->stuck_limit))
                return 0;
        } else if (strcmp(argv[i], "--progress-every") == 0 && i + 1 < argc) {
            if (!parse_u32_arg(argv[++i], &options->progress_every))
                return 0;
        } else if (strcmp(argv[i], "--quiet") == 0) {
            options->quiet = 1;
        } else {
            return 0;
        }
    }
    return options->main_dol != NULL && options->max_yields > 0;
}

int main(int argc, char **argv) {
    Options options;
    CPUState cpu;
    Host host;
    u32 entry = 0;
    u32 previous = 0;
    unsigned same = 0;
    unsigned long long i;
    const char *stop_reason = "max_yields";

    if (!parse_options(argc, argv, &options)) {
        usage(argv[0]);
        return 2;
    }

    memset(&host, 0, sizeof(host));
    host.quiet = options.quiet;
    if (!cpu_init(&cpu) || !cpu_alloc_mem2(&cpu, WII_MEM2_SIZE)) {
        fprintf(stderr, "CPU/RAM allocation failed\n");
        return 3;
    }

    openspm_wii_platform_reset(&host.platform);
    openspm_wii_platform_set_memory(&host.platform, cpu.ram, cpu.ram_size, cpu.mem2, cpu.mem2_size);
    openspm_wii_boot_platform_reset(&host.boot_platform, &host.platform);
    openspm_wii_boot_platform_set_memory(&host.boot_platform, cpu.ram, cpu.ram_size, cpu.mem2, cpu.mem2_size);

    cpu.external_user_data = &host;
    cpu.external_read = ext_read;
    cpu.external_write = ext_write;
    cpu.instruction_fallback = fallback;
    cpu.host_call = host_call;
    cpu.spr_read = spr_read;
    cpu.spr_write = spr_write;
    cpu.cache_control = cache_control;

    if (!load_dol(&cpu, options.main_dol, &entry)) {
        cpu_free(&cpu);
        return 4;
    }
    fprintf(stderr, "entry=%08x state=%zu\n", entry, sizeof(cpu));

    cpu.pc = entry;
    cpu.gpr[1] = 0x816FFFF0u;
    cpu.msr = 0x00002032u;
    cpu.hid2 = 0xE0000000u;
    cpu.downcount = INT64_MAX / 4;

    for (i = 0; i < options.max_yields; ++i) {
        previous = cpu.pc;
        if (!dolrecomp_call(&cpu, cpu.pc)) {
            fprintf(stderr, "NO DISPATCH pc=%08x i=%llu\n", cpu.pc, i);
            stop_reason = "no_dispatch";
            break;
        }
        cpu.timebase += 128;

        if (cpu.exception) {
            fprintf(stderr, "EXCEPTION pc=%08x exc=%08x prog=%08x i=%llu\n",
                    cpu.pc, cpu.exception, cpu.program_exception, i);
            stop_reason = "exception";
            break;
        }
        if (cpu.pc == previous) {
            if (++same == options.stuck_limit) {
                fprintf(stderr, "STUCK pc=%08x i=%llu\n", cpu.pc, i);
                stop_reason = "stuck";
                break;
            }
        } else {
            same = 0;
        }
        if (!options.quiet && options.progress_every && (i % options.progress_every) == 0) {
            fprintf(stderr,
                    "[run] i=%llu pc=%08x lr=%08x r1=%08x ext=%u/%u fb=%u host=%u\n",
                    i, cpu.pc, cpu.lr, cpu.gpr[1], host.ext_reads, host.ext_writes,
                    host.fallbacks, host.host_calls);
        }
    }

    fprintf(stderr,
            "DONE pc=%08x lr=%08x ext=%u/%u fb=%u host=%u spr=%u/%u cache=%u reason=%s\n",
            cpu.pc, cpu.lr, host.ext_reads, host.ext_writes, host.fallbacks,
            host.host_calls, host.spr_reads, host.spr_writes, host.cache_ops, stop_reason);
    printf("OPENSPM_RUN_RESULT {\"reason\":\"%s\",\"pc\":\"0x%08X\","
           "\"lr\":\"0x%08X\",\"ext_reads\":%u,\"ext_writes\":%u,"
           "\"fallbacks\":%u,\"host_calls\":%u}\n",
           stop_reason, cpu.pc, cpu.lr, host.ext_reads, host.ext_writes,
           host.fallbacks, host.host_calls);

    cpu_free(&cpu);
    return strcmp(stop_reason, "exception") == 0 ? 5 : 0;
}
