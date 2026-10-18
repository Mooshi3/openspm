/*
 * Compatibility build entry retained for the existing M14 CMake target.
 *
 * dev25's task-level NandWork poller is intentionally retired: it intercepted
 * nandWriteBannerLoadAllSaves() and raced the game's real NAND SDK callbacks.
 * dev26 hosts the physical NAND SDK boundary and KPAD held-repeat/released
 * fidelity. v13 is a narrow dev26 hotfix that makes existing banner.bin create
 * idempotent so nandmgr does not loop forever on NAND_CODE_EXISTS.
 */
#include "openspm_native_backend_v13.c"
