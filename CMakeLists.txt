cmake_minimum_required(VERSION 3.20)
project(OpenSPMPlatform VERSION 13.0 LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(OPENSPM_BUILD_INPROCESS_BACKEND "Build the in-process ModernGekko backend" OFF)
set(OPENSPM_MODERNGEKKO_SOURCE_DIR "" CACHE PATH "Path to a pinned ModernGekko source checkout")
option(OPENSPM_BUILD_AURORA_RENDERER "Build the M14 native Aurora GX renderer plugin" OFF)
set(OPENSPM_AURORA_SOURCE_DIR "" CACHE PATH "Path to the pinned, OpenSPM-adapted Aurora source checkout")

# These scripts mutate the pinned player-local Aurora checkout at configure time.
# Make Ninja/CMake notice adapter changes so a normal M14 update-run cannot keep
# using stale generated build rules or uninstrumented cached Aurora objects.
file(GLOB OPENSPM_AURORA_PATCH_SCRIPTS CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_*patch*.py")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    ${OPENSPM_AURORA_PATCH_SCRIPTS})

if(OPENSPM_BUILD_INPROCESS_BACKEND)
    if(NOT EXISTS "${OPENSPM_MODERNGEKKO_SOURCE_DIR}/CMakeLists.txt")
        message(FATAL_ERROR "OPENSPM_BUILD_INPROCESS_BACKEND requires OPENSPM_MODERNGEKKO_SOURCE_DIR")
    endif()
    add_subdirectory("${OPENSPM_MODERNGEKKO_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/moderngekko" EXCLUDE_FROM_ALL)
endif()

if(OPENSPM_BUILD_AURORA_RENDERER)
    if(NOT EXISTS "${OPENSPM_AURORA_SOURCE_DIR}/CMakeLists.txt" OR
       NOT EXISTS "${OPENSPM_AURORA_SOURCE_DIR}/include/aurora/gx_guest_memory.hpp" OR
       NOT EXISTS "${OPENSPM_AURORA_SOURCE_DIR}/include/aurora/gx_raw.hpp")
        message(FATAL_ERROR
            "OPENSPM_BUILD_AURORA_RENDERER requires the pinned Aurora checkout after "
            "renderer/aurora/apply_patches.py and apply_raster_patch.py")
    endif()

    # The raw FIFO parser can itself generate ordered renderer work (currently
    # PE bounding-box writes). Aurora's public encoder-task API drains the FIFO
    # before recording a task, which recursively re-enters fifo::drain() when
    # called from that parser. Apply OpenSPM's exact pinned-source adaptation on
    # every configure so both fresh and already-cached home-test checkouts gain
    # the no-drain internal path without asking the player to reset .openspm.
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_fifo_reentrancy_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_REENTRANCY_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_REENTRANCY_OUT
        ERROR_VARIABLE OPENSPM_AURORA_REENTRANCY_ERR
    )
    string(STRIP "${OPENSPM_AURORA_REENTRANCY_OUT}" OPENSPM_AURORA_REENTRANCY_OUT)
    string(STRIP "${OPENSPM_AURORA_REENTRANCY_ERR}" OPENSPM_AURORA_REENTRANCY_ERR)
    if(NOT OPENSPM_AURORA_REENTRANCY_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora FIFO reentrancy adaptation failed (${OPENSPM_AURORA_REENTRANCY_RC}): "
            "${OPENSPM_AURORA_REENTRANCY_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_REENTRANCY_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_REENTRANCY_OUT}")
    endif()

    # dev120 pacing A/B: keep the failed asynchronous worker architecture
    # retired, but allow Aurora's safe same-thread ProcessingMode::Inline at
    # runtime. The command-aware backend publishes only complete GX prefixes;
    # Inline consumes them synchronously before Broadway can reuse mutable guest
    # RAM. Drain remains the default/rollback mode.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_fifo_inline_mode_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_FIFO_INLINE_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_FIFO_INLINE_OUT
        ERROR_VARIABLE OPENSPM_AURORA_FIFO_INLINE_ERR
    )
    string(STRIP "${OPENSPM_AURORA_FIFO_INLINE_OUT}" OPENSPM_AURORA_FIFO_INLINE_OUT)
    string(STRIP "${OPENSPM_AURORA_FIFO_INLINE_ERR}" OPENSPM_AURORA_FIFO_INLINE_ERR)
    if(NOT OPENSPM_AURORA_FIFO_INLINE_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora FIFO Inline-mode adaptation failed "
            "(${OPENSPM_AURORA_FIFO_INLINE_RC}): "
            "${OPENSPM_AURORA_FIFO_INLINE_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_FIFO_INLINE_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_FIFO_INLINE_OUT}")
    endif()

    # dev88: the bounded DrawDone substage probe has completed its job.  The
    # dev87 player run still processed thousands of draws per frame, so keeping
    # multiple steady_clock reads around every draw/scan/stage contaminates the
    # narrow 60 Hz deadline margin we are now measuring.  Apply the exact
    # idempotent retirement to the cached pinned Aurora checkout on configure;
    # this changes diagnostics only, never GX/FIFO/render semantics.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_drawdone_profile_retirement_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_DRAWDONE_RETIRE_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_DRAWDONE_RETIRE_OUT
        ERROR_VARIABLE OPENSPM_AURORA_DRAWDONE_RETIRE_ERR
    )
    string(STRIP "${OPENSPM_AURORA_DRAWDONE_RETIRE_OUT}" OPENSPM_AURORA_DRAWDONE_RETIRE_OUT)
    string(STRIP "${OPENSPM_AURORA_DRAWDONE_RETIRE_ERR}" OPENSPM_AURORA_DRAWDONE_RETIRE_ERR)
    if(NOT OPENSPM_AURORA_DRAWDONE_RETIRE_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora DrawDone-profiler retirement failed "
            "(${OPENSPM_AURORA_DRAWDONE_RETIRE_RC}): "
            "${OPENSPM_AURORA_DRAWDONE_RETIRE_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_DRAWDONE_RETIRE_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_DRAWDONE_RETIRE_OUT}")
    endif()

    # dev89: keep the exact/correct raw guest-array semantics, but remove two
    # host-side costs measured at the current deadline frontier: per-stage
    # unordered_multimap node allocation and repeated per-vertex descriptor
    # decoding in the mandatory index scan.  The transform is exact/idempotent
    # and fails closed if the runtime-proven Aurora source shape has drifted.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_guest_array_hotpath_v2_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_GUEST_HOTPATH_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_GUEST_HOTPATH_OUT
        ERROR_VARIABLE OPENSPM_AURORA_GUEST_HOTPATH_ERR
    )
    string(STRIP "${OPENSPM_AURORA_GUEST_HOTPATH_OUT}" OPENSPM_AURORA_GUEST_HOTPATH_OUT)
    string(STRIP "${OPENSPM_AURORA_GUEST_HOTPATH_ERR}" OPENSPM_AURORA_GUEST_HOTPATH_ERR)
    if(NOT OPENSPM_AURORA_GUEST_HOTPATH_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora guest-array hotpath optimization failed "
            "(${OPENSPM_AURORA_GUEST_HOTPATH_RC}): "
            "${OPENSPM_AURORA_GUEST_HOTPATH_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_GUEST_HOTPATH_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_GUEST_HOTPATH_OUT}")
    endif()

    # dev94: player A/B showed dev91's mapped-GPU-staging memcmp path is
    # CPU-expensive on D3D12. Restore the proven CPU-cached exact snapshot pool
    # while retaining dev89's fixed-bucket/scan-plan and dev92's timing
    # retirement. This migration also repairs already-mutated player caches.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_guest_array_staging_snapshot_rollback_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_OUT
        ERROR_VARIABLE OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_ERR
    )
    string(STRIP "${OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_OUT}" OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_OUT)
    string(STRIP "${OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_ERR}" OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_ERR)
    if(NOT OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora CPU-cached guest snapshot restore failed "
            "(${OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_RC}): "
            "${OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_GUEST_CPU_SNAPSHOT_OUT}")
    endif()

    # dev92: the historical guest-array stage timing probe still performs
    # steady_clock reads and aggregate accounting inside m14_stage_guest_array.
    # Its measurements already produced dev89/dev91, so remove that diagnostic
    # tax from normal play while leaving all staging/reuse semantics unchanged.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_guest_array_timing_probe_retirement_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_GUEST_TIMING_RETIRE_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_GUEST_TIMING_RETIRE_OUT
        ERROR_VARIABLE OPENSPM_AURORA_GUEST_TIMING_RETIRE_ERR
    )
    string(STRIP "${OPENSPM_AURORA_GUEST_TIMING_RETIRE_OUT}" OPENSPM_AURORA_GUEST_TIMING_RETIRE_OUT)
    string(STRIP "${OPENSPM_AURORA_GUEST_TIMING_RETIRE_ERR}" OPENSPM_AURORA_GUEST_TIMING_RETIRE_ERR)
    if(NOT OPENSPM_AURORA_GUEST_TIMING_RETIRE_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora guest-array timing retirement failed "
            "(${OPENSPM_AURORA_GUEST_TIMING_RETIRE_RC}): "
            "${OPENSPM_AURORA_GUEST_TIMING_RETIRE_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_GUEST_TIMING_RETIRE_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_GUEST_TIMING_RETIRE_OUT}")
    endif()

    # dev119: allow one rebuilt Aurora binary to A/B Mailbox vs Immediate via
    # OPENSPM_M14_PRESENT_MODE. Unset retains pinned Aurora's historical policy.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_present_mode_override_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_PRESENT_MODE_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_PRESENT_MODE_OUT
        ERROR_VARIABLE OPENSPM_AURORA_PRESENT_MODE_ERR
    )
    string(STRIP "${OPENSPM_AURORA_PRESENT_MODE_OUT}" OPENSPM_AURORA_PRESENT_MODE_OUT)
    string(STRIP "${OPENSPM_AURORA_PRESENT_MODE_ERR}" OPENSPM_AURORA_PRESENT_MODE_ERR)
    if(NOT OPENSPM_AURORA_PRESENT_MODE_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora present-mode override adaptation failed "
            "(${OPENSPM_AURORA_PRESENT_MODE_RC}): "
            "${OPENSPM_AURORA_PRESENT_MODE_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_PRESENT_MODE_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_PRESENT_MODE_OUT}")
    endif()

    # dev122: augment the already-active low-volume Present pacing probe with
    # tail-only correlation. No new clock reads are added; only intervals the
    # existing probe already measured above 25 ms produce one log line.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_present_tail_diag_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_PRESENT_TAIL_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_PRESENT_TAIL_OUT
        ERROR_VARIABLE OPENSPM_AURORA_PRESENT_TAIL_ERR
    )
    string(STRIP "${OPENSPM_AURORA_PRESENT_TAIL_OUT}" OPENSPM_AURORA_PRESENT_TAIL_OUT)
    string(STRIP "${OPENSPM_AURORA_PRESENT_TAIL_ERR}" OPENSPM_AURORA_PRESENT_TAIL_ERR)
    if(NOT OPENSPM_AURORA_PRESENT_TAIL_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora present-tail diagnostic adaptation failed "
            "(${OPENSPM_AURORA_PRESENT_TAIL_RC}): "
            "${OPENSPM_AURORA_PRESENT_TAIL_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_PRESENT_TAIL_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_PRESENT_TAIL_OUT}")
    endif()

    # dev131: observation-only display-cadence diagnostic. Log the SDL display
    # mode actually associated with Aurora's window and compare its refresh
    # against integer multiples of the authoritative 60 Hz Wii simulation.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_display_cadence_diag_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_DISPLAY_CADENCE_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_DISPLAY_CADENCE_OUT
        ERROR_VARIABLE OPENSPM_AURORA_DISPLAY_CADENCE_ERR
    )
    string(STRIP "${OPENSPM_AURORA_DISPLAY_CADENCE_OUT}" OPENSPM_AURORA_DISPLAY_CADENCE_OUT)
    string(STRIP "${OPENSPM_AURORA_DISPLAY_CADENCE_ERR}" OPENSPM_AURORA_DISPLAY_CADENCE_ERR)
    if(NOT OPENSPM_AURORA_DISPLAY_CADENCE_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora display-cadence diagnostic adaptation failed "
            "(${OPENSPM_AURORA_DISPLAY_CADENCE_RC}): "
            "${OPENSPM_AURORA_DISPLAY_CADENCE_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_DISPLAY_CADENCE_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_DISPLAY_CADENCE_OUT}")
    endif()

    # dev136: first OpenPete/WiiCompiled-style interpolation scaffold. At normal
    # 60 Hz simulation, record renderer-visible GX transform history per draw
    # and validate cross-frame pairing at the exact GXCopyDisp boundary. This is
    # observation-only until retained-scene replay is enabled by a successor.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_transform_interpolation_probe_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_INTERP_PROBE_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_INTERP_PROBE_OUT
        ERROR_VARIABLE OPENSPM_AURORA_INTERP_PROBE_ERR
    )
    string(STRIP "${OPENSPM_AURORA_INTERP_PROBE_OUT}" OPENSPM_AURORA_INTERP_PROBE_OUT)
    string(STRIP "${OPENSPM_AURORA_INTERP_PROBE_ERR}" OPENSPM_AURORA_INTERP_PROBE_ERR)
    if(NOT OPENSPM_AURORA_INTERP_PROBE_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora dev136 interpolation-probe adaptation failed "
            "(${OPENSPM_AURORA_INTERP_PROBE_RC}): "
            "${OPENSPM_AURORA_INTERP_PROBE_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_INTERP_PROBE_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_INTERP_PROBE_OUT}")
    endif()

    # dev138: directly supersede the rejected dev137 visual experiment and
    # validate a stronger replay identity without changing pixels. Hash final staged geometry for
    # each logical GX draw, including merged ranges, and match only at the
    # GXCopyDisp frame boundary. No second build_uniform call is permitted.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_transform_interpolation_identity_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_INTERP_IDENTITY_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_INTERP_IDENTITY_OUT
        ERROR_VARIABLE OPENSPM_AURORA_INTERP_IDENTITY_ERR
    )
    string(STRIP "${OPENSPM_AURORA_INTERP_IDENTITY_OUT}" OPENSPM_AURORA_INTERP_IDENTITY_OUT)
    string(STRIP "${OPENSPM_AURORA_INTERP_IDENTITY_ERR}" OPENSPM_AURORA_INTERP_IDENTITY_ERR)
    if(NOT OPENSPM_AURORA_INTERP_IDENTITY_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora dev138 interpolation-identity adaptation failed "
            "(${OPENSPM_AURORA_INTERP_IDENTITY_RC}): "
            "${OPENSPM_AURORA_INTERP_IDENTITY_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_INTERP_IDENTITY_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_INTERP_IDENTITY_OUT}")
    endif()

    # dev139: cadence/lifetime proof for decoupled presentation. Retain the
    # completed Wii XFB into a two-slot host ring and let an optional presenter
    # thread own swapchain Present() independently of the 60 Hz producer.
    # Geometry interpolation remains off in this proof.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_async_presenter_probe_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_ASYNC_PRESENTER_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_ASYNC_PRESENTER_OUT
        ERROR_VARIABLE OPENSPM_AURORA_ASYNC_PRESENTER_ERR
    )
    string(STRIP "${OPENSPM_AURORA_ASYNC_PRESENTER_OUT}" OPENSPM_AURORA_ASYNC_PRESENTER_OUT)
    string(STRIP "${OPENSPM_AURORA_ASYNC_PRESENTER_ERR}" OPENSPM_AURORA_ASYNC_PRESENTER_ERR)
    if(NOT OPENSPM_AURORA_ASYNC_PRESENTER_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora dev139 async-presenter adaptation failed "
            "(${OPENSPM_AURORA_ASYNC_PRESENTER_RC}): "
            "${OPENSPM_AURORA_ASYNC_PRESENTER_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_ASYNC_PRESENTER_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_ASYNC_PRESENTER_OUT}")
    endif()

    # Readback bursts must not compete with repeated retained-XFB presents.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_bbox_priority_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_BBOX_PRIORITY_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_BBOX_PRIORITY_OUT
        ERROR_VARIABLE OPENSPM_AURORA_BBOX_PRIORITY_ERR
    )
    if(NOT OPENSPM_AURORA_BBOX_PRIORITY_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM bbox priority adaptation failed: ${OPENSPM_AURORA_BBOX_PRIORITY_ERR}")
    endif()
    message(STATUS "${OPENSPM_AURORA_BBOX_PRIORITY_OUT}")

    # Report PE bounding boxes in SPM's logical GX coordinates. Aurora's EFB
    # follows the window resolution, so raw EFB pixels made the game capture the
    # wrong screen rectangle for flip/door/pipe copy effects in large windows.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_bbox_logical_scale_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_BBOX_LOGICAL_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_BBOX_LOGICAL_OUT
        ERROR_VARIABLE OPENSPM_AURORA_BBOX_LOGICAL_ERR
    )
    if(NOT OPENSPM_AURORA_BBOX_LOGICAL_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM bbox logical-coordinate adaptation failed: ${OPENSPM_AURORA_BBOX_LOGICAL_ERR}")
    endif()
    message(STATUS "${OPENSPM_AURORA_BBOX_LOGICAL_OUT}")

    # Opt-in (OPENSPM_M14_COPY_TRACE=1) EFB texture-copy correspondence trace for
    # capture effects that are still missing (flip card, door swing).
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_copy_trace_diag_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_COPY_TRACE_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_COPY_TRACE_OUT
        ERROR_VARIABLE OPENSPM_AURORA_COPY_TRACE_ERR
    )
    if(NOT OPENSPM_AURORA_COPY_TRACE_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM EFB copy trace adaptation failed: ${OPENSPM_AURORA_COPY_TRACE_ERR}")
    endif()
    message(STATUS "${OPENSPM_AURORA_COPY_TRACE_OUT}")

    # Opt-in (OPENSPM_M14_EFB_COPY_RAM=1) write-back of EFB texture copies into
    # guest RAM, for effects whose CPU code consumes copied pixels.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_efb_copy_ram_writeback_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_EFB_RAM_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_EFB_RAM_OUT
        ERROR_VARIABLE OPENSPM_AURORA_EFB_RAM_ERR
    )
    if(NOT OPENSPM_AURORA_EFB_RAM_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM EFB copy RAM write-back adaptation failed: ${OPENSPM_AURORA_EFB_RAM_ERR}")
    endif()
    message(STATUS "${OPENSPM_AURORA_EFB_RAM_OUT}")

    # Dolphin HD texture packs ([TexturePacks], docs/TEXTURE-PACKS.md): stats,
    # cache budget and Dolphin-named PNG dumps on Aurora's replacement cache,
    # which texture.cpp consults after the EFB copy tag lookup above. The patch
    # embeds openspm_texture_pack_names.hpp, so re-configure when it changes.
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/openspm_texture_pack_names.hpp")
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_texture_pack_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_TEXPACK_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_TEXPACK_OUT
        ERROR_VARIABLE OPENSPM_AURORA_TEXPACK_ERR
    )
    if(NOT OPENSPM_AURORA_TEXPACK_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM texture pack adaptation failed: ${OPENSPM_AURORA_TEXPACK_ERR}")
    endif()
    message(STATUS "${OPENSPM_AURORA_TEXPACK_OUT}")

    # Pixel-exact source rect for unscaled EFB texture copies and the GX viewport
    # origin (342, as the game encodes it): star-door capture alignment.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_copy_pixel_exact_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_COPY_EXACT_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_COPY_EXACT_OUT
        ERROR_VARIABLE OPENSPM_AURORA_COPY_EXACT_ERR
    )
    if(NOT OPENSPM_AURORA_COPY_EXACT_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM pixel-exact EFB copy adaptation failed: ${OPENSPM_AURORA_COPY_EXACT_ERR}")
    endif()
    message(STATUS "${OPENSPM_AURORA_COPY_EXACT_OUT}")

    # dev140a: visual-neutral retained-draw replay preparation. While the
    # authoritative frame staging arena is still mapped, pair adjacent GX
    # draws, clone the existing uniform arena for the exact 100:60 phase
    # schedule, patch transform fields only, and retain FramePacket state
    # through the render-worker end-frame callback. No extra scene replay or
    # interpolated pixels are emitted by this milestone.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_interpolation_replay_prep_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_INTERP_REPLAY_PREP_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_INTERP_REPLAY_PREP_OUT
        ERROR_VARIABLE OPENSPM_AURORA_INTERP_REPLAY_PREP_ERR
    )
    string(STRIP "${OPENSPM_AURORA_INTERP_REPLAY_PREP_OUT}" OPENSPM_AURORA_INTERP_REPLAY_PREP_OUT)
    string(STRIP "${OPENSPM_AURORA_INTERP_REPLAY_PREP_ERR}" OPENSPM_AURORA_INTERP_REPLAY_PREP_ERR)
    if(NOT OPENSPM_AURORA_INTERP_REPLAY_PREP_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora dev140 replay-prep adaptation failed "
            "(${OPENSPM_AURORA_INTERP_REPLAY_PREP_RC}): "
            "${OPENSPM_AURORA_INTERP_REPLAY_PREP_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_INTERP_REPLAY_PREP_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_INTERP_REPLAY_PREP_OUT}")
    endif()

    # dev140b: offscreen scene-replay encode proof. This consumes dev140a's
    # prepared transform-uniform arenas on Aurora's render worker and replays
    # EFB GX draws into isolated color/depth targets. It never publishes those
    # targets to the presenter, so the authoritative EFB and visible dev139
    # output remain unchanged while replay safety/performance is measured.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_interpolation_replay_encode_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_AURORA_INTERP_REPLAY_ENCODE_RC
        OUTPUT_VARIABLE OPENSPM_AURORA_INTERP_REPLAY_ENCODE_OUT
        ERROR_VARIABLE OPENSPM_AURORA_INTERP_REPLAY_ENCODE_ERR
    )
    string(STRIP "${OPENSPM_AURORA_INTERP_REPLAY_ENCODE_OUT}" OPENSPM_AURORA_INTERP_REPLAY_ENCODE_OUT)
    string(STRIP "${OPENSPM_AURORA_INTERP_REPLAY_ENCODE_ERR}" OPENSPM_AURORA_INTERP_REPLAY_ENCODE_ERR)
    if(NOT OPENSPM_AURORA_INTERP_REPLAY_ENCODE_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 Aurora dev140b replay-encode adaptation failed "
            "(${OPENSPM_AURORA_INTERP_REPLAY_ENCODE_RC}): "
            "${OPENSPM_AURORA_INTERP_REPLAY_ENCODE_ERR}")
    endif()
    if(NOT OPENSPM_AURORA_INTERP_REPLAY_ENCODE_OUT STREQUAL "")
        message(STATUS "OpenSPM M14: ${OPENSPM_AURORA_INTERP_REPLAY_ENCODE_OUT}")
    endif()

    # OpenSPM supplies disc/filesystem/save/input itself. Only Aurora's native
    # window/WebGPU/GX stack is needed for the M14 renderer plugin.
    set(AURORA_ENABLE_GX ON CACHE BOOL "" FORCE)
    set(AURORA_ENABLE_DVD OFF CACHE BOOL "" FORCE)
    set(AURORA_ENABLE_CARD OFF CACHE BOOL "" FORCE)
    set(AURORA_ENABLE_THP OFF CACHE BOOL "" FORCE)
    set(AURORA_ENABLE_RMLUI OFF CACHE BOOL "" FORCE)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/interpolation_present.inl")
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_interpolation_present_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_INTERP_PRESENT_RC
        OUTPUT_VARIABLE OPENSPM_INTERP_PRESENT_OUT
        ERROR_VARIABLE OPENSPM_INTERP_PRESENT_ERR)
    if(NOT OPENSPM_INTERP_PRESENT_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM visible interpolation adaptation failed: ${OPENSPM_INTERP_PRESENT_ERR}")
    endif()
    message(STATUS "${OPENSPM_INTERP_PRESENT_OUT}")

    # Stage A present timeline (opt-in OPENSPM_M14_PRESENT_TIMELINE=1 with the
    # async presenter): vblank-driven presents chosen by the pure pacing model
    # in present_timeline.hpp. Must run after every other presenter patch.
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/present_timeline.hpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/present_timeline_presenter.inl")
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_present_timeline_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_PRESENT_TIMELINE_RC
        OUTPUT_VARIABLE OPENSPM_PRESENT_TIMELINE_OUT
        ERROR_VARIABLE OPENSPM_PRESENT_TIMELINE_ERR)
    string(STRIP "${OPENSPM_PRESENT_TIMELINE_OUT}" OPENSPM_PRESENT_TIMELINE_OUT)
    string(STRIP "${OPENSPM_PRESENT_TIMELINE_ERR}" OPENSPM_PRESENT_TIMELINE_ERR)
    if(NOT OPENSPM_PRESENT_TIMELINE_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM present timeline adaptation failed "
                "(${OPENSPM_PRESENT_TIMELINE_RC}): ${OPENSPM_PRESENT_TIMELINE_ERR}")
    endif()
    message(STATUS "${OPENSPM_PRESENT_TIMELINE_OUT}")

    # Stage B draw tags (opt-in OPENSPM_M14_DRAW_TAGS=1): latch the dispDraw
    # owner markers the LLVM bridge writes into the FIFO (BP 0x7E/0x7F) and
    # attach them to every recorded GX draw. Last in the chain.
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/draw_tags.hpp")
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_draw_tags_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_DRAW_TAGS_RC
        OUTPUT_VARIABLE OPENSPM_DRAW_TAGS_OUT
        ERROR_VARIABLE OPENSPM_DRAW_TAGS_ERR)
    string(STRIP "${OPENSPM_DRAW_TAGS_OUT}" OPENSPM_DRAW_TAGS_OUT)
    string(STRIP "${OPENSPM_DRAW_TAGS_ERR}" OPENSPM_DRAW_TAGS_ERR)
    if(NOT OPENSPM_DRAW_TAGS_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM draw tags adaptation failed "
                "(${OPENSPM_DRAW_TAGS_RC}): ${OPENSPM_DRAW_TAGS_ERR}")
    endif()
    message(STATUS "${OPENSPM_DRAW_TAGS_OUT}")

    # Stage C frame interpolation (opt-in OPENSPM_M14_INTERP=1 with the Stage A
    # timeline and Stage B draw tags): retain the last finished frames and
    # replay B with owner-matched matrices interpolated for each vblank's
    # alpha. Last in the chain.
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/frame_interp.hpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/frame_interp_presenter.inl")
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_frame_interp_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_FRAME_INTERP_RC
        OUTPUT_VARIABLE OPENSPM_FRAME_INTERP_OUT
        ERROR_VARIABLE OPENSPM_FRAME_INTERP_ERR)
    string(STRIP "${OPENSPM_FRAME_INTERP_OUT}" OPENSPM_FRAME_INTERP_OUT)
    string(STRIP "${OPENSPM_FRAME_INTERP_ERR}" OPENSPM_FRAME_INTERP_ERR)
    if(NOT OPENSPM_FRAME_INTERP_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM frame interp adaptation failed "
                "(${OPENSPM_FRAME_INTERP_RC}): ${OPENSPM_FRAME_INTERP_ERR}")
    endif()
    message(STATUS "${OPENSPM_FRAME_INTERP_OUT}")

    # bbox fast path (opt-in OPENSPM_M14_BBOX_FAST=1, or =verify to check each
    # CPU answer against the GPU): a PE bbox read after only provably empty
    # draws (none, offscreen, degenerate) returns the written box without a GPU
    # sync (star door start hitch). Last in the chain.
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/bbox_fast.hpp")
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_bbox_fast_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_BBOX_FAST_RC
        OUTPUT_VARIABLE OPENSPM_BBOX_FAST_OUT
        ERROR_VARIABLE OPENSPM_BBOX_FAST_ERR)
    string(STRIP "${OPENSPM_BBOX_FAST_OUT}" OPENSPM_BBOX_FAST_OUT)
    string(STRIP "${OPENSPM_BBOX_FAST_ERR}" OPENSPM_BBOX_FAST_ERR)
    if(NOT OPENSPM_BBOX_FAST_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM bbox fast adaptation failed "
                "(${OPENSPM_BBOX_FAST_RC}): ${OPENSPM_BBOX_FAST_ERR}")
    endif()
    message(STATUS "${OPENSPM_BBOX_FAST_OUT}")

    # Widescreen ([Display] widescreen = 1): AuroraSetStretchAspect fits the
    # stretched EFB to 16:9 inside the window (pillar/letterbox) so the game's
    # anamorphic 16:9 image is shown at 16:9 from a 16:9 host EFB. Last in the chain.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_widescreen_fit_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_WIDESCREEN_FIT_RC
        OUTPUT_VARIABLE OPENSPM_WIDESCREEN_FIT_OUT
        ERROR_VARIABLE OPENSPM_WIDESCREEN_FIT_ERR)
    string(STRIP "${OPENSPM_WIDESCREEN_FIT_OUT}" OPENSPM_WIDESCREEN_FIT_OUT)
    string(STRIP "${OPENSPM_WIDESCREEN_FIT_ERR}" OPENSPM_WIDESCREEN_FIT_ERR)
    if(NOT OPENSPM_WIDESCREEN_FIT_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM widescreen fit adaptation failed "
                "(${OPENSPM_WIDESCREEN_FIT_RC}): ${OPENSPM_WIDESCREEN_FIT_ERR}")
    endif()
    message(STATUS "${OPENSPM_WIDESCREEN_FIT_OUT}")

    # Fast-forward ("turbo", docs/PLAYING.md): aurora_m14_set_turbo lets the
    # renderer submit a game frame without presenting it (at most one image per
    # display refresh, so FIFO vsync never throttles N x speed) and makes the
    # async presenter / present timeline show the newest image, never
    # interpolated, while turbo is engaged. Inert while the flags are 0.
    # Last in the chain.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_turbo_present_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_TURBO_PRESENT_RC
        OUTPUT_VARIABLE OPENSPM_TURBO_PRESENT_OUT
        ERROR_VARIABLE OPENSPM_TURBO_PRESENT_ERR)
    string(STRIP "${OPENSPM_TURBO_PRESENT_OUT}" OPENSPM_TURBO_PRESENT_OUT)
    string(STRIP "${OPENSPM_TURBO_PRESENT_ERR}" OPENSPM_TURBO_PRESENT_ERR)
    if(NOT OPENSPM_TURBO_PRESENT_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM turbo presentation adaptation failed "
                "(${OPENSPM_TURBO_PRESENT_RC}): ${OPENSPM_TURBO_PRESENT_OUT} ${OPENSPM_TURBO_PRESENT_ERR}")
    endif()
    message(STATUS "${OPENSPM_TURBO_PRESENT_OUT}")

    # Present timeline resize crash fix: one mutex serializes the render
    # worker's timeline presents with surface reconfiguration (window resize,
    # refresh, release) on the main thread. Aurora's SurfaceLock is Android-only.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_presenter_surface_lock_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_SURFACE_LOCK_RC
        OUTPUT_VARIABLE OPENSPM_SURFACE_LOCK_OUT
        ERROR_VARIABLE OPENSPM_SURFACE_LOCK_ERR)
    string(STRIP "${OPENSPM_SURFACE_LOCK_OUT}" OPENSPM_SURFACE_LOCK_OUT)
    string(STRIP "${OPENSPM_SURFACE_LOCK_ERR}" OPENSPM_SURFACE_LOCK_ERR)
    if(NOT OPENSPM_SURFACE_LOCK_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM presenter surface lock adaptation failed "
                "(${OPENSPM_SURFACE_LOCK_RC}): ${OPENSPM_SURFACE_LOCK_OUT} ${OPENSPM_SURFACE_LOCK_ERR}")
    endif()
    message(STATUS "${OPENSPM_SURFACE_LOCK_OUT}")

    # GX capture diagnostics: EFB copy log lines also carry the guest
    # destination address (analyze_gx_capture.py --passes). Capture-only.
    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/renderer/aurora/apply_gx_audit_copy_guest_patch.py"
                "${OPENSPM_AURORA_SOURCE_DIR}"
        RESULT_VARIABLE OPENSPM_COPY_GUEST_RC
        OUTPUT_VARIABLE OPENSPM_COPY_GUEST_OUT
        ERROR_VARIABLE OPENSPM_COPY_GUEST_ERR)
    string(STRIP "${OPENSPM_COPY_GUEST_OUT}" OPENSPM_COPY_GUEST_OUT)
    string(STRIP "${OPENSPM_COPY_GUEST_ERR}" OPENSPM_COPY_GUEST_ERR)
    if(NOT OPENSPM_COPY_GUEST_RC EQUAL 0)
        message(FATAL_ERROR "OpenSPM GX capture copy guest address adaptation failed "
                "(${OPENSPM_COPY_GUEST_RC}): ${OPENSPM_COPY_GUEST_OUT} ${OPENSPM_COPY_GUEST_ERR}")
    endif()
    message(STATUS "${OPENSPM_COPY_GUEST_OUT}")

    add_subdirectory("${OPENSPM_AURORA_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/aurora" EXCLUDE_FROM_ALL)

    add_library(openspm_renderer SHARED renderer/aurora/openspm_renderer_aurora_v6.cpp)
    target_include_directories(openspm_renderer PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    # Aurora core/GX query the VI-configured framebuffer size even though
    # OpenSPM owns the guest-side VI service, so the native renderer must carry
    # Aurora's small host-side VI state implementation too.
    target_link_libraries(openspm_renderer PRIVATE aurora::core aurora::gx aurora::vi)
    set_target_properties(openspm_renderer PROPERTIES
        PREFIX ""
        OUTPUT_NAME "openspm_renderer"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/runtime"
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/runtime")

    if(MSVC)
        # openspm_renderer.map: names for the sampling profiler (OPENSPM_PROFILE=1).
        target_link_options(openspm_renderer PRIVATE /MAP)
    endif()
    if(WIN32)
        include("${OPENSPM_AURORA_SOURCE_DIR}/cmake/AuroraCopyRuntimeDLLs.cmake")
        aurora_copy_runtime_dlls(openspm_renderer)
    endif()
endif()

add_library(openspm_abi STATIC
    core/abi.c
    core/native_runtime.c
    core/native_loader.c
    core/dol_image.c
    core/disc_boot.c
    core/wii_platform.c
    core/wii_boot_platform.c
    core/gx_platform.c
    core/sha256.c)
target_include_directories(openspm_abi PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
if(NOT WIN32)
    target_link_libraries(openspm_abi PUBLIC ${CMAKE_DL_LIBS})
endif()

add_executable(m13_abi_test tests/m13_abi_test.c)
target_link_libraries(m13_abi_test PRIVATE openspm_abi)

# M14 standalone-native contract tests. They deliberately build without Dolphin,
# ModernGekko, RecompCore, game data, or proprietary symbols.
add_executable(m14_native_runtime_test tests/m14_native_runtime_test.c)
target_link_libraries(m14_native_runtime_test PRIVATE openspm_abi)

add_library(m14_synthetic_native_module SHARED tests/m14_synthetic_native_module.c)
target_include_directories(m14_synthetic_native_module PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
set_target_properties(m14_synthetic_native_module PROPERTIES PREFIX "")

add_executable(m14_native_loader_test tests/m14_native_loader_test.c)
target_link_libraries(m14_native_loader_test PRIVATE openspm_abi)

add_executable(m14_dol_image_test tests/m14_dol_image_test.c)
target_link_libraries(m14_dol_image_test PRIVATE openspm_abi)

add_executable(m14_disc_boot_test tests/m14_disc_boot_test.c)
target_link_libraries(m14_disc_boot_test PRIVATE openspm_abi)

add_executable(m14_wii_platform_test tests/m14_wii_platform_test.c)
target_link_libraries(m14_wii_platform_test PRIVATE openspm_abi)

add_executable(m14_wii_boot_platform_test tests/m14_wii_boot_platform_test.c)
target_link_libraries(m14_wii_boot_platform_test PRIVATE openspm_abi)

add_executable(m14_gx_platform_test tests/m14_gx_platform_test.c)
target_link_libraries(m14_gx_platform_test PRIVATE openspm_abi)

add_executable(m14_gx_display_list_test tests/m14_gx_display_list_test.c)
target_link_libraries(m14_gx_display_list_test PRIVATE openspm_abi)

add_executable(m14_sha256_test tests/m14_sha256_test.c)
target_link_libraries(m14_sha256_test PRIVATE openspm_abi)

add_executable(m14_native_settings_test tests/m14_native_settings_test.c)
add_executable(m14_pad_map_test tests/m14_pad_map_test.c)
add_executable(m14_sysconf_test tests/m14_sysconf_test.c)
add_executable(m14_native_game_test tests/m14_native_game_test.c)
add_executable(m14_ini_edit_test tests/m14_ini_edit_test.cpp)
target_include_directories(m14_ini_edit_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_cheats_test tests/m14_cheats_test.c)
target_include_directories(m14_cheats_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_shell_crouch_mod_test tests/m14_shell_crouch_mod_test.c)
target_include_directories(m14_shell_crouch_mod_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_code_patches_test tests/m14_code_patches_test.c)
target_include_directories(m14_code_patches_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_game_state_test tests/m14_game_state_test.c)
target_include_directories(m14_game_state_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_mod_manifest_test tests/m14_mod_manifest_test.c)
target_include_directories(m14_mod_manifest_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_definitions(m14_mod_manifest_test PRIVATE OPENSPM_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}")
add_executable(m14_sample_profile_test tests/m14_sample_profile_test.c)
target_include_directories(m14_sample_profile_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_hooks_test tests/m14_hooks_test.c)
target_include_directories(m14_hooks_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_sfx_log_mod_test tests/m14_sfx_log_mod_test.c)
target_include_directories(m14_sfx_log_mod_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_content_pixls_test tests/m14_content_pixls_test.c)
target_include_directories(m14_content_pixls_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_content_roster_test tests/m14_content_roster_test.c)
target_include_directories(m14_content_roster_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_content_followers_test tests/m14_content_followers_test.c)
target_include_directories(m14_content_followers_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
if(NOT MSVC)
    target_link_libraries(m14_content_followers_test PRIVATE m)
endif()
add_executable(m14_content_messages_test tests/m14_content_messages_test.c)
target_include_directories(m14_content_messages_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_content_maps_test tests/m14_content_maps_test.c)
target_include_directories(m14_content_maps_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
if(NOT MSVC)
    target_link_libraries(m14_content_maps_test PRIVATE m)
endif()
add_executable(m14_content_enemies_test tests/m14_content_enemies_test.c)
target_include_directories(m14_content_enemies_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_fst_overlay_test tests/m14_fst_overlay_test.c)
target_include_directories(m14_fst_overlay_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_content_ids_test tests/m14_content_ids_test.c)
target_include_directories(m14_content_ids_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_striker_mod_test tests/m14_striker_mod_test.c)
target_include_directories(m14_striker_mod_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
if(NOT MSVC)
    target_link_libraries(m14_striker_mod_test PRIVATE m)
endif()
add_executable(m14_sample_level_mod_test tests/m14_sample_level_mod_test.c)
target_include_directories(m14_sample_level_mod_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_francis_mod_test tests/m14_francis_mod_test.c)
target_include_directories(m14_francis_mod_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
if(NOT MSVC)
    target_link_libraries(m14_francis_mod_test PRIVATE m)
endif()
add_executable(m14_hitbox_view_mod_test tests/m14_hitbox_view_mod_test.c)
target_include_directories(m14_hitbox_view_mod_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
if(NOT MSVC)
    target_link_libraries(m14_hitbox_view_mod_test PRIVATE m)
endif()
add_executable(m14_glock_mod_test tests/m14_glock_mod_test.c)
target_include_directories(m14_glock_mod_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
if(NOT MSVC)
    target_link_libraries(m14_glock_mod_test PRIVATE m)
endif()
add_executable(m14_keymap_test tests/m14_keymap_test.c)
target_include_directories(m14_keymap_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_pointer_input_test tests/m14_pointer_input_test.c)
target_include_directories(m14_pointer_input_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
if(NOT MSVC)
    target_link_libraries(m14_pointer_input_test PRIVATE m)
endif()
add_executable(m14_guest_clock_test tests/m14_guest_clock_test.c)
target_include_directories(m14_guest_clock_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
add_executable(m14_mod_tick_test tests/m14_mod_tick_test.c)
target_include_directories(m14_mod_tick_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)

if(WIN32)
    add_executable(OpenSPM launcher/main.c launcher/mod_loader.c launcher/config.c launcher/gui.c launcher/renderer_loader.c)
    target_include_directories(OpenSPM PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/launcher)
    target_link_libraries(OpenSPM PRIVATE openspm_abi comctl32 shell32 ole32)
    target_compile_definitions(OpenSPM PRIVATE UNICODE _UNICODE)
    if(MSVC)
        target_compile_options(OpenSPM PRIVATE /FIwindowsx.h)
    endif()

    add_executable(OpenSPMNative launcher/native_main.c launcher/renderer_loader.c launcher/mod_loader.c launcher/config.c
                                 launcher/sample_profiler.c)
    target_include_directories(OpenSPMNative PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/launcher)
    target_link_libraries(OpenSPMNative PRIVATE openspm_abi comdlg32 shell32)
    target_compile_definitions(OpenSPMNative PRIVATE UNICODE _UNICODE)
    if(MSVC)
        target_link_options(OpenSPMNative PRIVATE /MAP) # names for the sampling profiler
    endif()

    # dev25 wraps runtime-proven dev24 and replaces SPM's physical Wii NAND
    # completion/storage boundary with persistent files below user_root.
    add_library(openspm_native_backend SHARED backend/native/openspm_native_backend_v11.c)
    target_include_directories(openspm_native_backend PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(openspm_native_backend PRIVATE openspm_abi)
    if(MSVC)
        target_link_options(openspm_native_backend PRIVATE /MAP) # names for the sampling profiler
    endif()
    target_compile_definitions(openspm_native_backend PRIVATE UNICODE _UNICODE)
    set_target_properties(openspm_native_backend PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/runtime")

    add_library(moderngekko_backend SHARED backend/legacy/moderngekko_backend.c)
    target_include_directories(moderngekko_backend PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_compile_definitions(moderngekko_backend PRIVATE UNICODE _UNICODE)
    set_target_properties(moderngekko_backend PROPERTIES PREFIX "")

    if(OPENSPM_BUILD_INPROCESS_BACKEND)
        add_library(moderngekko_inprocess_backend SHARED backend/inprocess/moderngekko_backend.cpp)
        target_include_directories(moderngekko_inprocess_backend PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
        target_link_libraries(moderngekko_inprocess_backend PRIVATE ModernGekko::Runtime)
        target_compile_definitions(moderngekko_inprocess_backend PRIVATE UNICODE _UNICODE)
        set_target_properties(moderngekko_inprocess_backend PROPERTIES
            PREFIX ""
            OUTPUT_NAME "moderngekko_backend_inprocess")
    endif()

    add_library(openspm_debug SHARED mods/debug/openspm_debug.c)
    target_include_directories(openspm_debug PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(openspm_debug PROPERTIES PREFIX "")

    # Sample mods (docs/MODDING.md) build straight into the mods folder next to
    # OpenSPMNative.exe; they load only with [Mods] enabled = 1.
    add_library(openspm_shell_crouch SHARED mods/shell_crouch/openspm_shell_crouch.c)
    target_include_directories(openspm_shell_crouch PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(openspm_shell_crouch PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mods$<0:>")
    add_library(openspm_striker SHARED mods/striker/openspm_striker.c)
    target_include_directories(openspm_striker PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(openspm_striker PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mods$<0:>")
    add_library(openspm_glock SHARED mods/glock/openspm_glock.c)
    target_include_directories(openspm_glock PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(openspm_glock PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mods$<0:>")
    add_library(openspm_francis SHARED mods/francis/openspm_francis.c)
    target_include_directories(openspm_francis PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(openspm_francis PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mods$<0:>")
    add_library(openspm_flint_follower SHARED mods/flint_follower/openspm_flint_follower.c)
    target_include_directories(openspm_flint_follower PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(openspm_flint_follower PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mods$<0:>")
    add_library(openspm_hitbox_view SHARED mods/hitbox_view/openspm_hitbox_view.c)
    target_include_directories(openspm_hitbox_view PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(openspm_hitbox_view PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mods$<0:>")
    add_library(openspm_sample_level SHARED mods/sample_level/openspm_sample_level.c)
    target_include_directories(openspm_sample_level PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(openspm_sample_level PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mods$<0:>")
    # The sample level's map, built from tools/content/sample_level.py (made
    # here, nothing from the game) into the mod's files folder.
    find_package(Python3 COMPONENTS Interpreter)
    if(Python3_Interpreter_FOUND)
        set(OPENSPM_SAMPLE_LEVEL_FILES "${CMAKE_BINARY_DIR}/mods/openspm_sample_level/files")
        add_custom_command(
            OUTPUT "${OPENSPM_SAMPLE_LEVEL_FILES}/map/os1_01.bin"
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/content/sample_level.py"
                    "${OPENSPM_SAMPLE_LEVEL_FILES}"
            DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tools/content/sample_level.py"
                    "${CMAKE_CURRENT_SOURCE_DIR}/tools/content/level_build.py"
                    "${CMAKE_CURRENT_SOURCE_DIR}/tools/content/map_build.py"
            COMMENT "OpenSPM: building the sample level's map (os1_01)")
        add_custom_target(openspm_sample_level_files ALL DEPENDS "${OPENSPM_SAMPLE_LEVEL_FILES}/map/os1_01.bin")
        add_dependencies(openspm_sample_level openspm_sample_level_files)
    else()
        message(WARNING "OpenSPM: no Python 3, so the sample level's map is not built (its mod stays off)")
    endif()
    add_library(openspm_sfx_log SHARED mods/sfx_log/openspm_sfx_log.c)
    target_include_directories(openspm_sfx_log PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(openspm_sfx_log PROPERTIES
        PREFIX ""
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mods$<0:>")

    # Mod info files next to the DLLs (include/openspm/mod_manifest.h).
    foreach(openspm_mod IN ITEMS debug shell_crouch striker glock sfx_log hitbox_view francis flint_follower sample_level)
        configure_file(mods/${openspm_mod}/openspm_${openspm_mod}.toml
                       ${CMAKE_BINARY_DIR}/mods/openspm_${openspm_mod}.toml COPYONLY)
    endforeach()

    add_library(m14_synthetic_renderer SHARED tests/m14_synthetic_renderer.c)
    target_include_directories(m14_synthetic_renderer PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    set_target_properties(m14_synthetic_renderer PROPERTIES PREFIX "")

    add_executable(m13_backend_query_test tests/m13_backend_query_test.c backend/legacy/moderngekko_backend.c)
    target_include_directories(m13_backend_query_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_compile_definitions(m13_backend_query_test PRIVATE UNICODE _UNICODE)

    add_executable(m13_dll_query_test tests/m13_dll_query_test.c)
    target_link_libraries(m13_dll_query_test PRIVATE openspm_abi)
    target_compile_definitions(m13_dll_query_test PRIVATE UNICODE _UNICODE)

    add_executable(m14_gx_renderer_service_test tests/m14_gx_renderer_service_test.c)
    target_link_libraries(m14_gx_renderer_service_test PRIVATE openspm_abi)
    target_compile_definitions(m14_gx_renderer_service_test PRIVATE UNICODE _UNICODE)

    add_executable(m14_exi_diagnostic_test tests/m14_exi_diagnostic_test.c)
    target_link_libraries(m14_exi_diagnostic_test PRIVATE openspm_abi)
    target_compile_definitions(m14_exi_diagnostic_test PRIVATE UNICODE _UNICODE)

    add_executable(m14_renderer_loader_test tests/m14_renderer_loader_test.c launcher/renderer_loader.c)
    target_include_directories(m14_renderer_loader_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/launcher)
    target_link_libraries(m14_renderer_loader_test PRIVATE openspm_abi)
    target_compile_definitions(m14_renderer_loader_test PRIVATE UNICODE _UNICODE)

    add_executable(m13_mod_loader_test tests/m13_mod_loader_test.c launcher/mod_loader.c)
    target_include_directories(m13_mod_loader_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include ${CMAKE_CURRENT_SOURCE_DIR}/launcher)
    target_link_libraries(m13_mod_loader_test PRIVATE openspm_abi)
    target_compile_definitions(m13_mod_loader_test PRIVATE UNICODE _UNICODE)

    add_executable(m13_config_test tests/m13_config_test.c launcher/config.c)
    target_include_directories(m13_config_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/launcher)
    target_compile_definitions(m13_config_test PRIVATE UNICODE _UNICODE)
endif()

enable_testing()
add_test(NAME m13_abi COMMAND m13_abi_test)
add_test(NAME m14_native_runtime COMMAND m14_native_runtime_test)
add_test(NAME m14_native_loader COMMAND m14_native_loader_test $<TARGET_FILE:m14_synthetic_native_module>)
add_test(NAME m14_dol_image COMMAND m14_dol_image_test)
add_test(NAME m14_disc_boot COMMAND m14_disc_boot_test)
add_test(NAME m14_wii_platform COMMAND m14_wii_platform_test)
add_test(NAME m14_wii_boot_platform COMMAND m14_wii_boot_platform_test)
add_test(NAME m14_gx_platform COMMAND m14_gx_platform_test)
add_test(NAME m14_gx_display_list COMMAND m14_gx_display_list_test)
add_test(NAME m14_sha256 COMMAND m14_sha256_test)
add_test(NAME m14_cheats COMMAND m14_cheats_test)
add_test(NAME m14_shell_crouch_mod COMMAND m14_shell_crouch_mod_test)
add_test(NAME m14_glock_mod COMMAND m14_glock_mod_test)
add_test(NAME m14_hitbox_view_mod COMMAND m14_hitbox_view_mod_test)
add_test(NAME m14_francis_mod COMMAND m14_francis_mod_test)
add_test(NAME m14_sample_level_mod COMMAND m14_sample_level_mod_test)
add_test(NAME m14_code_patches COMMAND m14_code_patches_test)
add_test(NAME m14_hooks COMMAND m14_hooks_test)
add_test(NAME m14_mod_manifest COMMAND m14_mod_manifest_test)
add_test(NAME m14_sample_profile COMMAND m14_sample_profile_test)
add_test(NAME m14_game_state COMMAND m14_game_state_test)
add_test(NAME m14_sfx_log_mod COMMAND m14_sfx_log_mod_test)
add_test(NAME m14_content_roster COMMAND m14_content_roster_test)
add_test(NAME m14_content_ids COMMAND m14_content_ids_test)
add_test(NAME m14_content_followers COMMAND m14_content_followers_test)
add_test(NAME m14_fst_overlay COMMAND m14_fst_overlay_test)
add_test(NAME m14_content_maps COMMAND m14_content_maps_test)
add_test(NAME m14_content_messages COMMAND m14_content_messages_test)
add_test(NAME m14_content_enemies COMMAND m14_content_enemies_test)
add_test(NAME m14_content_pixls COMMAND m14_content_pixls_test)
add_test(NAME m14_striker_mod COMMAND m14_striker_mod_test)
add_test(NAME m14_keymap COMMAND m14_keymap_test)
add_test(NAME m14_guest_clock COMMAND m14_guest_clock_test)
add_test(NAME m14_mod_tick COMMAND m14_mod_tick_test)
add_test(NAME m14_pointer_input COMMAND m14_pointer_input_test)
add_test(NAME m14_native_settings COMMAND m14_native_settings_test)
add_test(NAME m14_pad_map COMMAND m14_pad_map_test)
add_test(NAME m14_sysconf COMMAND m14_sysconf_test)
add_test(NAME m14_native_game COMMAND m14_native_game_test)
add_test(NAME m14_ini_edit COMMAND m14_ini_edit_test)
if(WIN32)
    add_test(NAME m13_backend_query COMMAND m13_backend_query_test)
    add_test(NAME m13_dll_query COMMAND m13_dll_query_test $<TARGET_FILE:moderngekko_backend>)
    add_test(NAME m14_native_backend_query COMMAND m13_dll_query_test $<TARGET_FILE:openspm_native_backend> openspm.native)
    add_test(NAME m14_native_launcher_version COMMAND OpenSPMNative --version)
    add_test(NAME m14_gx_renderer_service COMMAND m14_gx_renderer_service_test $<TARGET_FILE:openspm_native_backend>)
    add_test(NAME m14_exi_diagnostic COMMAND m14_exi_diagnostic_test $<TARGET_FILE:openspm_native_backend>)
    add_test(NAME m14_renderer_loader COMMAND m14_renderer_loader_test $<TARGET_FILE:m14_synthetic_renderer>)
    if(OPENSPM_BUILD_INPROCESS_BACKEND)
        add_test(NAME m13_inprocess_backend_query COMMAND m13_dll_query_test $<TARGET_FILE:moderngekko_inprocess_backend> moderngekko.inprocess)
    endif()
    add_test(NAME m13_mod_loader COMMAND m13_mod_loader_test)
    add_test(NAME m13_config COMMAND m13_config_test)
endif()

# The tests check with assert(); a Release build defines NDEBUG, which would
# compile every check out (CI builds Release). Keep assert() on in every
# *_test target whatever the build type.
get_property(openspm_targets DIRECTORY PROPERTY BUILDSYSTEM_TARGETS)
foreach(openspm_target IN LISTS openspm_targets)
    if(openspm_target MATCHES "_test$")
        if(MSVC)
            target_compile_options(${openspm_target} PRIVATE /UNDEBUG)
        else()
            target_compile_options(${openspm_target} PRIVATE -UNDEBUG)
        endif()
    endif()
endforeach()
