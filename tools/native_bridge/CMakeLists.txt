cmake_minimum_required(VERSION 3.20)
project(OpenSPMLocalNativeBridge C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

set(OPENSPM_ROOT "" CACHE PATH "OpenSPM source root")
set(OPENSPM_GENERATED_DIR "" CACHE PATH "Local DolRecomp generated directory containing generated.h/chunks")
set(OPENSPM_CPU_RUNTIME_DIR "" CACHE PATH "Local compiler runtime root containing cpu/cpu.c and common/types.h")
set(OPENSPM_IDENTITY_DIR "" CACHE PATH "Local directory containing openspm_local_identity.h")
set(OPENSPM_GENERATED_BACKEND "c" CACHE STRING "Player-local generated backend: c or llvm")
set_property(CACHE OPENSPM_GENERATED_BACKEND PROPERTY STRINGS c llvm)
set(OPENSPM_LLVM_CHUNK_INSTRUCTIONS "256" CACHE STRING "LLVM generic chunk ceiling for reproducible A/Bs")
set_property(CACHE OPENSPM_LLVM_CHUNK_INSTRUCTIONS PROPERTY STRINGS 128 256 512)
set(OPENSPM_LLVM_STATE_IN_MEMORY "0" CACHE STRING "DolRecomp state-in-memory A/B: 0 or 1")
set_property(CACHE OPENSPM_LLVM_STATE_IN_MEMORY PROPERTY STRINGS 0 1)
set(OPENSPM_LLVM_HEAVY_DIAGNOSTICS "0" CACHE STRING "Heavy dev111/dev112/dev115 callback diagnostics: 0 or 1")
set_property(CACHE OPENSPM_LLVM_HEAVY_DIAGNOSTICS PROPERTY STRINGS 0 1)
# Player-local module toolchain (docs/RELEASE-PLAN.md Stage 1/2). "msvc" is the
# default and keeps the proven cl.exe /O2 /GL + link /LTCG build unchanged.
#   clang              clang-cl + lld-link from the pinned LLVM, ThinLTO over
#                      the bridge and cpu.c; still uses the MSVC CRT/SDK from
#                      the Visual Studio environment (Stage 1).
#   clang-freestanding clang-cl + lld-link with no CRT/SDK headers or libraries:
#                      m14_freestanding_crt.h/.c plus kernel32/ucrtbase import
#                      libraries made by llvm-dlltool from OpenSPM .def files
#                      (Stage 2 spike).
set(OPENSPM_LOCAL_TOOLCHAIN "msvc" CACHE STRING "Player-local module toolchain: msvc, clang or clang-freestanding")
set_property(CACHE OPENSPM_LOCAL_TOOLCHAIN PROPERTY STRINGS msvc clang clang-freestanding)
set(OPENSPM_LLVM_DLLTOOL "" CACHE FILEPATH "llvm-dlltool for OPENSPM_LOCAL_TOOLCHAIN=clang-freestanding")

foreach(required OPENSPM_ROOT OPENSPM_GENERATED_DIR OPENSPM_CPU_RUNTIME_DIR OPENSPM_IDENTITY_DIR)
    if(NOT ${required})
        message(FATAL_ERROR "${required} is required")
    endif()
endforeach()

if(NOT OPENSPM_GENERATED_BACKEND STREQUAL "c" AND
   NOT OPENSPM_GENERATED_BACKEND STREQUAL "llvm")
    message(FATAL_ERROR "OPENSPM_GENERATED_BACKEND must be c or llvm")
endif()
if(OPENSPM_GENERATED_BACKEND STREQUAL "llvm" AND
   NOT OPENSPM_LLVM_CHUNK_INSTRUCTIONS STREQUAL "128" AND
   NOT OPENSPM_LLVM_CHUNK_INSTRUCTIONS STREQUAL "256" AND
   NOT OPENSPM_LLVM_CHUNK_INSTRUCTIONS STREQUAL "512")
    message(FATAL_ERROR "OPENSPM_LLVM_CHUNK_INSTRUCTIONS must be 128, 256, or 512")
endif()
if(OPENSPM_GENERATED_BACKEND STREQUAL "llvm" AND
   NOT OPENSPM_LLVM_STATE_IN_MEMORY STREQUAL "0" AND
   NOT OPENSPM_LLVM_STATE_IN_MEMORY STREQUAL "1")
    message(FATAL_ERROR "OPENSPM_LLVM_STATE_IN_MEMORY must be 0 or 1")
endif()
if(OPENSPM_GENERATED_BACKEND STREQUAL "llvm" AND
   NOT OPENSPM_LLVM_HEAVY_DIAGNOSTICS STREQUAL "0" AND
   NOT OPENSPM_LLVM_HEAVY_DIAGNOSTICS STREQUAL "1")
    message(FATAL_ERROR "OPENSPM_LLVM_HEAVY_DIAGNOSTICS must be 0 or 1")
endif()
if(NOT OPENSPM_LOCAL_TOOLCHAIN STREQUAL "msvc" AND
   NOT OPENSPM_LOCAL_TOOLCHAIN STREQUAL "clang" AND
   NOT OPENSPM_LOCAL_TOOLCHAIN STREQUAL "clang-freestanding")
    message(FATAL_ERROR "OPENSPM_LOCAL_TOOLCHAIN must be msvc, clang or clang-freestanding")
endif()
if(OPENSPM_LOCAL_TOOLCHAIN MATCHES "^clang")
    if(NOT CMAKE_C_COMPILER_ID STREQUAL "Clang" OR
       NOT CMAKE_C_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
        message(FATAL_ERROR
            "OPENSPM_LOCAL_TOOLCHAIN=${OPENSPM_LOCAL_TOOLCHAIN} requires clang-cl "
            "(CMAKE_C_COMPILER=<pinned LLVM>/bin/clang-cl); got ${CMAKE_C_COMPILER_ID}/${CMAKE_C_COMPILER_FRONTEND_VARIANT}")
    endif()
    get_filename_component(OPENSPM_LINKER_NAME "${CMAKE_LINKER}" NAME_WE)
    if(NOT OPENSPM_LINKER_NAME STREQUAL "lld-link")
        message(FATAL_ERROR
            "OPENSPM_LOCAL_TOOLCHAIN=${OPENSPM_LOCAL_TOOLCHAIN} requires CMAKE_LINKER=<pinned LLVM>/bin/lld-link "
            "(ThinLTO bitcode cannot be linked by link.exe); got ${CMAKE_LINKER}")
    endif()
endif()
if(NOT EXISTS "${OPENSPM_ROOT}/include/openspm/native_api.h")
    message(FATAL_ERROR "OPENSPM_ROOT does not contain the M14 native ABI")
endif()
if(NOT EXISTS "${OPENSPM_GENERATED_DIR}/generated.h")
    message(FATAL_ERROR "OPENSPM_GENERATED_DIR is missing generated.h")
endif()
if(NOT EXISTS "${OPENSPM_CPU_RUNTIME_DIR}/cpu/cpu.c")
    message(FATAL_ERROR "OPENSPM_CPU_RUNTIME_DIR is missing cpu/cpu.c")
endif()
if(NOT EXISTS "${OPENSPM_IDENTITY_DIR}/openspm_local_identity.h")
    message(FATAL_ERROR "OPENSPM_IDENTITY_DIR is missing openspm_local_identity.h")
endif()

find_package(Python3 COMPONENTS Interpreter REQUIRED)

if(OPENSPM_GENERATED_BACKEND STREQUAL "llvm")
    # v42 includes the complete v41 -> v40 -> v39 -> ... LLVM bridge chain.
    # dev63 exposes dev58's positive-only region-admission cache read-only to
    # generated LLVM so hot safe entries can avoid a C transition. Misses keep
    # the exact v41 semantic-router path.
    # Historical source-contract anchors retained for proven predecessors:
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v25.c.in")
    # m14-dev35-llvm-relf-bss-tail-proof
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v26.c.in")
    # m14-dev36-llvm-hostcall-direct-parity
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v27.c.in")
    # m14-dev37-llvm-nand-completion-probe
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v28.c.in")
    # m14-dev38-llvm-scheduler-idle-reentry
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v29.c.in")
    # m14-dev39-llvm-stage1-scheduler-probe
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v30.c.in")
    # m14-dev40-llvm-scheduler-context-reentry
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v31.c.in")
    # m14-dev41-llvm-vi-scheduler-transfer-capture
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v32.c.in")
    # m14-dev42-llvm-vi-scratch-transfer-fix
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v33.c.in")
    # m14-dev43-llvm-vi-wait-queue-truth
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v34.c.in")
    # m14-dev44-llvm-postframe-idle-truth
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v35.c.in")
    # m14-dev45-llvm-vi-wake-tail-truth
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v36.c.in")
    # m14-dev46-llvm-main-thread-transition-truth
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v37.c.in")
    # m14-dev47-llvm-gx-finish-semantic-completion
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v38.c.in")
    # m14-dev48-llvm-pc-display-list
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v39.c.in")
    # m14-dev50-llvm-production-router
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v40.c.in")
    # m14-dev51-llvm-test-ready-gx-input
    # set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v41.c.in")
    # m14-dev58-llvm-admission-cache-v19-restore
    set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v42.c.in")
    if(NOT EXISTS "${OPENSPM_GENERATED_DIR}/openspm_local_v19_llvm_seams.h")
        message(FATAL_ERROR
            "LLVM player-local generation is missing openspm_local_v19_llvm_seams.h")
    endif()
    if(NOT EXISTS "${OPENSPM_GENERATED_DIR}/openspm_local_gx_dl_semantics.h")
        message(FATAL_ERROR
            "LLVM player-local generation is missing openspm_local_gx_dl_semantics.h")
    endif()

    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_LIST_DIR}/validate_llvm_generated.py"
                "${OPENSPM_GENERATED_DIR}" --require-openspm-wpar --print-cmake
        RESULT_VARIABLE OPENSPM_LLVM_VALIDATE_RC
        OUTPUT_VARIABLE OPENSPM_LLVM_OBJECT_LIST
        ERROR_VARIABLE OPENSPM_LLVM_VALIDATE_ERR
    )
    string(STRIP "${OPENSPM_LLVM_OBJECT_LIST}" OPENSPM_LLVM_OBJECT_LIST)
    string(STRIP "${OPENSPM_LLVM_VALIDATE_ERR}" OPENSPM_LLVM_VALIDATE_ERR)
    if(NOT OPENSPM_LLVM_VALIDATE_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM LLVM generated-object validation failed (${OPENSPM_LLVM_VALIDATE_RC}): "
            "${OPENSPM_LLVM_VALIDATE_ERR}")
    elseif(OPENSPM_LLVM_OBJECT_LIST STREQUAL "")
        message(FATAL_ERROR "OpenSPM LLVM validator returned an empty object manifest")
    endif()

    set(OPENSPM_LOCAL_OBJECTS ${OPENSPM_LLVM_OBJECT_LIST})
    list(LENGTH OPENSPM_LOCAL_OBJECTS OPENSPM_LOCAL_CHUNK_COUNT)
    set_source_files_properties(${OPENSPM_LOCAL_OBJECTS}
        PROPERTIES EXTERNAL_OBJECT TRUE GENERATED TRUE)
    set(OPENSPM_LOCAL_PAYLOAD ${OPENSPM_LOCAL_OBJECTS})
    message(STATUS
        "OpenSPM M14 validated LLVM manifest: ${OPENSPM_LOCAL_CHUNK_COUNT} native objects; inline WPAR symbols proven")
else()
    set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v13.c.in")
    file(GLOB OPENSPM_LOCAL_CHUNKS CONFIGURE_DEPENDS
        "${OPENSPM_GENERATED_DIR}/chunks/*.c")
    list(LENGTH OPENSPM_LOCAL_CHUNKS OPENSPM_LOCAL_CHUNK_COUNT)
    if(OPENSPM_LOCAL_CHUNK_COUNT EQUAL 0)
        message(FATAL_ERROR "No local generated C chunks were found")
    endif()
    set(OPENSPM_LOCAL_PAYLOAD ${OPENSPM_LOCAL_CHUNKS})

    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_LIST_DIR}/instrument_spmario_main_v20.py"
                "${OPENSPM_GENERATED_DIR}"
        RESULT_VARIABLE OPENSPM_SIM_HOOK_RC
        OUTPUT_VARIABLE OPENSPM_SIM_HOOK_OUT
        ERROR_VARIABLE OPENSPM_SIM_HOOK_ERR
    )
    string(STRIP "${OPENSPM_SIM_HOOK_OUT}" OPENSPM_SIM_HOOK_OUT)
    string(STRIP "${OPENSPM_SIM_HOOK_ERR}" OPENSPM_SIM_HOOK_ERR)
    if(NOT OPENSPM_SIM_HOOK_RC EQUAL 0)
        message(FATAL_ERROR
            "OpenSPM M14 real main-loop phase adaptation failed (${OPENSPM_SIM_HOOK_RC}): ${OPENSPM_SIM_HOOK_ERR}")
    elseif(NOT OPENSPM_SIM_HOOK_OUT STREQUAL "")
        message(STATUS "${OPENSPM_SIM_HOOK_OUT}")
    endif()

    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_LIST_DIR}/inspect_memory_helper_calls.py"
                "${OPENSPM_GENERATED_DIR}"
        RESULT_VARIABLE OPENSPM_MEM_CENSUS_RC
        OUTPUT_VARIABLE OPENSPM_MEM_CENSUS_OUT
        ERROR_VARIABLE OPENSPM_MEM_CENSUS_ERR
    )
    string(STRIP "${OPENSPM_MEM_CENSUS_OUT}" OPENSPM_MEM_CENSUS_OUT)
    string(STRIP "${OPENSPM_MEM_CENSUS_ERR}" OPENSPM_MEM_CENSUS_ERR)
    if(NOT OPENSPM_MEM_CENSUS_RC EQUAL 0)
        message(WARNING
            "OpenSPM M14 generated memory-helper census unavailable (${OPENSPM_MEM_CENSUS_RC}): "
            "${OPENSPM_MEM_CENSUS_ERR}")
    elseif(NOT OPENSPM_MEM_CENSUS_OUT STREQUAL "")
        message(STATUS "${OPENSPM_MEM_CENSUS_OUT}")
    endif()

    execute_process(
        COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_LIST_DIR}/inspect_display_list_sdk_calls.py"
                "${OPENSPM_GENERATED_DIR}"
        RESULT_VARIABLE OPENSPM_DL_GRAPH_RC
        OUTPUT_VARIABLE OPENSPM_DL_GRAPH_OUT
        ERROR_VARIABLE OPENSPM_DL_GRAPH_ERR
    )
    string(STRIP "${OPENSPM_DL_GRAPH_OUT}" OPENSPM_DL_GRAPH_OUT)
    string(STRIP "${OPENSPM_DL_GRAPH_ERR}" OPENSPM_DL_GRAPH_ERR)
    if(NOT OPENSPM_DL_GRAPH_RC EQUAL 0)
        message(WARNING
            "OpenSPM M14 native GX display-list call-graph inspection unavailable (${OPENSPM_DL_GRAPH_RC}): "
            "${OPENSPM_DL_GRAPH_ERR}")
    elseif(NOT OPENSPM_DL_GRAPH_OUT STREQUAL "")
        message(STATUS "${OPENSPM_DL_GRAPH_OUT}")
    endif()
endif()

if(NOT EXISTS "${OPENSPM_ACTIVE_BRIDGE}")
    message(FATAL_ERROR "OpenSPM M14 selected native bridge is missing")
endif()
file(READ "${OPENSPM_ACTIVE_BRIDGE}" OPENSPM_ACTIVE_BRIDGE_SOURCE)
string(FIND "${OPENSPM_ACTIVE_BRIDGE_SOURCE}"
    "M14 native retrace phase-lock active: fixed 60 Hz deadline grid; elapsed deadlines are skipped, never burst-delivered."
    OPENSPM_PHASE_LOCK_MARKER)
if(OPENSPM_PHASE_LOCK_MARKER EQUAL -1)
    message(FATAL_ERROR "OpenSPM M14 selected bridge lacks the fixed-phase retrace contract")
endif()
if(OPENSPM_GENERATED_BACKEND STREQUAL "llvm")
    string(FIND "${OPENSPM_ACTIVE_BRIDGE_SOURCE}"
        "m14-dev49-llvm-pc-display-list-return"
        OPENSPM_PC_DISPLAY_LIST_RETURN_MARKER)
    if(OPENSPM_PC_DISPLAY_LIST_RETURN_MARKER EQUAL -1)
        message(FATAL_ERROR "OpenSPM M14 LLVM bridge lacks the dev49 PC-native display-list return contract")
    endif()
    string(FIND "${OPENSPM_ACTIVE_BRIDGE_SOURCE}"
        "m14-dev51-llvm-test-ready-gx-input"
        OPENSPM_TEST_READY_MARKER)
    if(OPENSPM_TEST_READY_MARKER EQUAL -1)
        message(FATAL_ERROR "OpenSPM M14 LLVM bridge lacks the dev51 test-ready GX/input contract")
    endif()
    string(FIND "${OPENSPM_ACTIVE_BRIDGE_SOURCE}"
        "m14-dev52-llvm-frame-phase-continuation"
        OPENSPM_PHASE_CONTINUATION_MARKER)
    if(OPENSPM_PHASE_CONTINUATION_MARKER EQUAL -1)
        message(FATAL_ERROR "OpenSPM M14 LLVM bridge lacks the dev52 frame-phase continuation repair")
    endif()
endif()

#[[
add_library(openspm_local_native SHARED
    "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v11.c.in"
]]
add_library(openspm_local_native SHARED
    "${OPENSPM_ACTIVE_BRIDGE}"
    "${OPENSPM_CPU_RUNTIME_DIR}/cpu/cpu.c"
    ${OPENSPM_LOCAL_PAYLOAD}
)
# Opt-in audio (OPENSPM_AUDIO=1, docs/AUDIO-PLAN.md): the portable AI/DSP
# device model and AX HLE mixer, used by the LLVM bridge's m14_audio_bridge.h.
# Plain OpenSPM C sources; no game code, no codegen change.
if(OPENSPM_GENERATED_BACKEND STREQUAL "llvm")
    target_sources(openspm_local_native PRIVATE
        "${OPENSPM_ROOT}/backend/audio/openspm_audio_device.c"
        "${OPENSPM_ROOT}/backend/audio/openspm_ax_hle.c")
endif()
set_source_files_properties("${OPENSPM_ACTIVE_BRIDGE}"
    PROPERTIES LANGUAGE C)

target_include_directories(openspm_local_native PRIVATE
    "${OPENSPM_ROOT}/include"
    "${CMAKE_CURRENT_LIST_DIR}"
    "${OPENSPM_GENERATED_DIR}"
    "${OPENSPM_CPU_RUNTIME_DIR}"
    "${OPENSPM_IDENTITY_DIR}"
)

if(OPENSPM_GENERATED_BACKEND STREQUAL "llvm")
    target_compile_definitions(openspm_local_native PRIVATE
        OPENSPM_M14_LLVM_CHUNK_INSTRUCTIONS=${OPENSPM_LLVM_CHUNK_INSTRUCTIONS}
        OPENSPM_M14_LLVM_STATE_IN_MEMORY=${OPENSPM_LLVM_STATE_IN_MEMORY}
        OPENSPM_M14_HEAVY_DIAGNOSTICS=${OPENSPM_LLVM_HEAVY_DIAGNOSTICS})
    message(STATUS
        "OpenSPM LLVM identity: chunk=${OPENSPM_LLVM_CHUNK_INSTRUCTIONS} state_mem=${OPENSPM_LLVM_STATE_IN_MEMORY} heavy_diag=${OPENSPM_LLVM_HEAVY_DIAGNOSTICS}")
endif()

if(MSVC)
    # openspm_local_native.map: the names the sampling profiler
    # (OPENSPM_PROFILE=1) reports, for every toolchain.
    target_link_options(openspm_local_native PRIVATE /MAP)
endif()

if(OPENSPM_LOCAL_TOOLCHAIN MATCHES "^clang")
    # clang-cl is MSVC-compatible, so the MSVC branch below must not see it:
    # /experimental:c11atomics is cl-only (clang has <stdatomic.h>), and /GL is
    # replaced by ThinLTO. Only the bridge and cpu.c are bitcode; the DolRecomp
    # payload objects are final COFF code either way, exactly as under /LTCG.
    # clang-cl already defaults to -fno-strict-aliasing for MSVC targets (cl
    # has no type-based aliasing); it is spelled out because the bridge relies
    # on it.
    set(OPENSPM_LOCAL_LTO_SOURCES
        "${OPENSPM_ACTIVE_BRIDGE}"
        "${OPENSPM_CPU_RUNTIME_DIR}/cpu/cpu.c")
    target_compile_options(openspm_local_native PRIVATE
        $<$<CONFIG:Release>:/O2>
        /clang:-fno-strict-aliasing)
    set_property(SOURCE ${OPENSPM_LOCAL_LTO_SOURCES}
        APPEND PROPERTY COMPILE_OPTIONS -flto=thin)
    if(OPENSPM_LOCAL_TOOLCHAIN STREQUAL "clang-freestanding")
        # No MSVC CRT and no Windows SDK: clang's builtin headers plus the
        # OpenSPM wrappers in freestanding/include (all resolving to
        # m14_freestanding_crt.h). /X ignores %INCLUDE%, /Zl drops the
        # msvcrt/oldnames default-library directives, /GS- drops the
        # __security_cookie dependency, and /NODEFAULTLIB leaves only the two
        # import libraries made here from OpenSPM-authored .def files.
        if(NOT OPENSPM_LLVM_DLLTOOL)
            get_filename_component(OPENSPM_LLVM_BIN "${CMAKE_LINKER}" DIRECTORY)
            find_program(OPENSPM_LLVM_DLLTOOL llvm-dlltool HINTS "${OPENSPM_LLVM_BIN}" NO_DEFAULT_PATH)
        endif()
        if(NOT OPENSPM_LLVM_DLLTOOL)
            message(FATAL_ERROR "clang-freestanding needs llvm-dlltool next to lld-link (OPENSPM_LLVM_DLLTOOL)")
        endif()
        set(OPENSPM_FREESTANDING_IMPORT_LIBS)
        foreach(dll kernel32 ucrtbase)
            set(def "${CMAKE_CURRENT_LIST_DIR}/m14_freestanding_${dll}.def")
            set(lib "${CMAKE_CURRENT_BINARY_DIR}/openspm_freestanding_${dll}.lib")
            add_custom_command(OUTPUT "${lib}"
                COMMAND "${OPENSPM_LLVM_DLLTOOL}" -m i386:x86-64 -d "${def}" -l "${lib}"
                DEPENDS "${def}"
                COMMENT "OpenSPM freestanding import library ${dll}.dll"
                VERBATIM)
            list(APPEND OPENSPM_FREESTANDING_IMPORT_LIBS "${lib}")
        endforeach()
        add_custom_target(openspm_freestanding_import_libs
            DEPENDS ${OPENSPM_FREESTANDING_IMPORT_LIBS})
        add_dependencies(openspm_local_native openspm_freestanding_import_libs)
        # The runtime shim stays a native object: the code generator can emit
        # memset/memcpy/__chkstk calls after LTO has dropped bitcode copies.
        target_sources(openspm_local_native PRIVATE
            "${CMAKE_CURRENT_LIST_DIR}/m14_freestanding_crt.c")
        target_include_directories(openspm_local_native SYSTEM PRIVATE
            "${CMAKE_CURRENT_LIST_DIR}/freestanding/include")
        target_compile_options(openspm_local_native PRIVATE /X /Zl /GS-)
        target_link_options(openspm_local_native PRIVATE /NODEFAULTLIB)
        target_link_libraries(openspm_local_native PRIVATE
            ${OPENSPM_FREESTANDING_IMPORT_LIBS})
        set(CMAKE_C_STANDARD_LIBRARIES "")
        message(STATUS
            "OpenSPM M14 native module toolchain: clang-cl ThinLTO + lld-link, freestanding "
            "(no MSVC CRT/SDK; kernel32 + ucrtbase import libs from OpenSPM .def files)")
    else()
        message(STATUS
            "OpenSPM M14 native module toolchain: clang-cl ThinLTO + lld-link (MSVC CRT/SDK)")
    endif()
elseif(MSVC)
    target_compile_options(openspm_local_native PRIVATE
        /experimental:c11atomics
        $<$<CONFIG:Release>:/O2>)

    include(CheckIPOSupported)
    check_ipo_supported(RESULT OPENSPM_LOCAL_IPO_SUPPORTED
                        OUTPUT OPENSPM_LOCAL_IPO_ERROR)
    if(NOT OPENSPM_LOCAL_IPO_SUPPORTED)
        message(FATAL_ERROR
            "OpenSPM M14 native CPU whole-program optimization is unsupported: ${OPENSPM_LOCAL_IPO_ERROR}")
    endif()
    set_property(TARGET openspm_local_native
        PROPERTY INTERPROCEDURAL_OPTIMIZATION_RELEASE TRUE)
    message(STATUS
        "OpenSPM M14 native CPU whole-program optimization active (MSVC /GL + /LTCG)")
else()
    target_compile_options(openspm_local_native PRIVATE
        $<$<CONFIG:Release>:-O2>
        -fno-strict-aliasing)
    if(NOT APPLE)
        target_link_libraries(openspm_local_native PRIVATE m)
    endif()
endif()

set_target_properties(openspm_local_native PROPERTIES
    PREFIX ""
    OUTPUT_NAME "openspm_local_native"
)

message(STATUS
    "OpenSPM local native bridge backend=${OPENSPM_GENERATED_BACKEND}: ${OPENSPM_LOCAL_CHUNK_COUNT} generated native chunks/objects")