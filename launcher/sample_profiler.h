/* The opt-in sampling profiler of the game thread (launcher/sample_profiler.c,
 * include/openspm/sample_profile.h). */
#ifndef OPENSPM_LAUNCHER_SAMPLE_PROFILER_H
#define OPENSPM_LAUNCHER_SAMPLE_PROFILER_H

typedef struct openspm_sample_profiler openspm_sample_profiler;

/* Starts sampling the calling thread when OPENSPM_PROFILE=1; NULL (nothing
   running) otherwise or on failure. */
openspm_sample_profiler *openspm_sample_profiler_start(void);

/* Logs the whole-run report and stops; NULL is a no-op. */
void openspm_sample_profiler_stop(openspm_sample_profiler *profiler);

#endif
