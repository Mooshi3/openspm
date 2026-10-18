cmake_minimum_required(VERSION 3.20)
project(openspm_m14_lab C)

get_filename_component(OPENSPM_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)

set(OPENSPM_GENERATED_DIR "" CACHE PATH "Player-local direct-call generated source tree")
set(OPENSPM_CPU_RUNTIME_DIR "" CACHE PATH "Pinned local CPU runtime root containing cpu/cpu.c")

if(NOT EXISTS "${OPENSPM_GENERATED_DIR}/generated.h")
    message(FATAL_ERROR "OPENSPM_GENERATED_DIR must contain generated.h")
endif()
if(NOT IS_DIRECTORY "${OPENSPM_GENERATED_DIR}/chunks")
    message(FATAL_ERROR "OPENSPM_GENERATED_DIR must contain chunks/")
endif()
if(EXISTS "${OPENSPM_CPU_RUNTIME_DIR}/src/cpu/cpu.c")
    set(OPENSPM_CPU_RUNTIME_DIR "${OPENSPM_CPU_RUNTIME_DIR}/src")
endif()
if(NOT EXISTS "${OPENSPM_CPU_RUNTIME_DIR}/cpu/cpu.c")
    message(FATAL_ERROR "OPENSPM_CPU_RUNTIME_DIR must contain cpu/cpu.c or src/cpu/cpu.c")
endif()

file(GLOB OPENSPM_M14_CHUNKS CONFIGURE_DEPENDS
    "${OPENSPM_GENERATED_DIR}/chunks/chunk_*.c")
list(LENGTH OPENSPM_M14_CHUNKS OPENSPM_M14_CHUNK_COUNT)
if(OPENSPM_M14_CHUNK_COUNT EQUAL 0)
    message(FATAL_ERROR "No chunk_*.c files found in generated tree")
endif()

add_executable(openspm_m14_lab
    diagnostic_runner.c
    "${OPENSPM_ROOT}/core/wii_platform.c"
    "${OPENSPM_ROOT}/core/wii_boot_platform.c"
    "${OPENSPM_CPU_RUNTIME_DIR}/cpu/cpu.c"
    ${OPENSPM_M14_CHUNKS})

target_include_directories(openspm_m14_lab PRIVATE
    "${OPENSPM_ROOT}/include"
    "${OPENSPM_GENERATED_DIR}"
    "${OPENSPM_CPU_RUNTIME_DIR}")

target_compile_features(openspm_m14_lab PRIVATE c_std_11)
if(MSVC)
    target_compile_options(openspm_m14_lab PRIVATE /O2)
else()
    target_compile_options(openspm_m14_lab PRIVATE -O2 -fno-strict-aliasing)
    target_link_libraries(openspm_m14_lab PRIVATE m)
endif()

message(STATUS "OpenSPM M14 lab generated chunks: ${OPENSPM_M14_CHUNK_COUNT}")
