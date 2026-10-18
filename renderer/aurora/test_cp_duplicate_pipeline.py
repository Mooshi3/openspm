#!/usr/bin/env python3
"""Compile and exercise the actual patched CP decoder from a local Aurora tree.

Requires a C++20 compiler and the pinned, adapted Aurora checkout. No game bytes
or GPU are needed. Extracts production functions verbatim; only their surrounding
state/map/log dependencies are replaced with a small deterministic fixture.
"""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def function(text, signature):
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 1
    end = brace + 1
    # These exact pinned functions have balanced braces in strings/comments.
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


PREAMBLE = r'''
#include <array>
#include <bitset>
#include <cassert>
#include <cstdint>
#include <cstdio>
using u8 = uint8_t;
using u32 = uint32_t;
using u64 = uint64_t;
constexpr u8 DirtyPipeline=1, DirtyImmediates=8;
constexpr u32 GX_VA_POS=9, GX_VA_MAX_ATTR=26;
struct Range { u32 offset=0, size=0; };
struct AttrArray {
  const void* data=nullptr;
  u32 size=0;
  bool le=false, guestMapped=false;
  u8 stride=12;
  Range cachedRange{};
};
struct State {
  u8 dirty=0;
  std::array<AttrArray,26> arrays{};
  std::bitset<256> cpRegValid;
  std::array<u32,256> cpRegCache{};
} g_gxState;
struct Logger {
  template<class... T> void info(T...) const {}
  template<class... T> void error(T...) const {}
} Log;
namespace guest_memory {
struct Span { const void* data=nullptr; u32 size=0; };
bool enabled=true, success=true;
u32 maps=0;
std::array<u8,128> bytes{};
Span next{bytes.data(),64};
bool has_mapper() { return enabled; }
bool map(u32, u32, Span* out) { ++maps; *out=next; return success; }
}
struct RegHandler {
  using Fn=void(*)(u8,u32) noexcept;
  Fn decode=nullptr;
  u8 dirty=0;
  bool alwaysHandle=false;
};
'''

TEST = r'''
int main() {
  unsigned cases=0;
  for (unsigned prior=0; prior<256; ++prior) {
    for (unsigned scenario=0; scenario<9; ++scenario) {
      g_gxState={};
      g_gxState.dirty=static_cast<u8>(prior);
      auto& a=g_gxState.arrays[GX_VA_POS];
      a.data=guest_memory::bytes.data(); a.size=64;
      a.guestMapped=true; a.cachedRange={128,64};
      guest_memory::enabled=true; guest_memory::success=true;
      guest_memory::maps=0; guest_memory::next={a.data,64};
      switch(scenario) {
      case 0: break; // Exact duplicate.
      case 1: a.cachedRange={}; break; // Explicit invalidation stays invalid.
      case 2: guest_memory::next.data=guest_memory::bytes.data()+16; break;
      case 3: guest_memory::next.size=48; break;
      case 4: a.le=true; break; // Endian transition must invalidate pipeline.
      case 5: a.guestMapped=false; break;
      case 6: guest_memory::success=false; break;
      case 7: guest_memory::enabled=false; break;
      case 8: ++guest_memory::bytes[0]; break; // Duplicate base is not a cache invalidate.
      }
      const auto before=a;
      handle_cp(0xA0,0x80000000);
      assert(guest_memory::maps==(scenario==7 ? 0u : 1u));
      const bool duplicate=scenario==0 || scenario==1 || scenario==8;
      if (duplicate) {
        assert(g_gxState.dirty==prior);
        assert(a.data==before.data && a.size==before.size);
        assert(a.cachedRange.offset==before.cachedRange.offset);
        assert(a.cachedRange.size==before.cachedRange.size);
      } else {
        const unsigned extra=scenario>=2 && scenario<=5 ? DirtyImmediates : 0;
        assert(g_gxState.dirty==(prior | DirtyPipeline | extra));
        if(scenario>=2 && scenario<=5) {
          assert(a.data==guest_memory::next.data && a.size==guest_memory::next.size);
          assert(!a.le && a.guestMapped && a.cachedRange.size==0);
        }
        if(scenario==6) assert(!a.data && !a.size && a.cachedRange.size==0);
        if(scenario==7) assert(a.data==before.data && a.cachedRange.size==64);
      }
      ++cases;
    }
  }
  std::printf("PASS: %u actual CP decoder cases; all dirty masks, duplicate, invalidation, mutation, mapping changes/failures and endian transitions\n",cases);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--cxx", default="c++")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("patch", HERE / "apply_cp_duplicate_pipeline_patch.py")
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    assert patch.transform(args.source, check_only=True) == "already-applied"
    text = (args.source / "lib/gx/regs.cpp").read_text()
    body = function(text, "void cp_arraybase(")
    dispatch = function(text, "void handle_cp(")
    # Use the production array-base row and dispatch, including alwaysHandle.
    row = next(line for line in text.splitlines() if "regs[0xA0 + i] =" in line)
    table = "constexpr auto kCpRegs=[] { std::array<RegHandler,256> regs{}; for(u8 i=0;i<16;++i) {\n" + row + "\n} return regs; }();\n"
    with tempfile.TemporaryDirectory(prefix="openspm-cp-pipeline-") as name:
        root = Path(name)
        cpp = root / "test.cpp"
        exe = root / "test"
        cpp.write_text(PREAMBLE + body + "\n" + table + dispatch + TEST)
        subprocess.run([args.cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
        # Cached migration must be idempotent and fail closed on a partial edit.
        fixture = root / "fixture/lib/gx"
        fixture.mkdir(parents=True)
        target = fixture / "regs.cpp"
        target.write_text(text)
        assert patch.transform(root / "fixture") == "already-applied"
        assert target.read_text() == text
        baseline = text.replace(patch.TABLE_NEW, patch.TABLE_OLD, 1).replace(patch.ENTRY_NEW, patch.ENTRY_OLD, 1).replace(patch.DUPLICATE_NEW, patch.DUPLICATE_OLD, 1)
        target.write_text(baseline)
        assert patch.transform(root / "fixture", True) == "verified"
        assert target.read_text() == baseline
        assert patch.transform(root / "fixture") == "applied"
        assert target.read_text() == text
        target.write_text(text.replace("g_gxState.dirty = dirtyBeforeBase;", ""))
        try:
            patch.transform(root / "fixture")
        except RuntimeError:
            pass
        else:
            raise AssertionError("partial patch accepted")
    print("PASS: fresh/check-only/cached/idempotent/partial-source migration checks")

    spec = importlib.util.spec_from_file_location("retire", HERE / "apply_drawdone_profile_retirement_patch.py")
    retire = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(retire)
    with tempfile.TemporaryDirectory(prefix="openspm-quiet-retirement-") as name:
        root = Path(name)
        path = root / "lib/gx/command_processor.cpp"
        path.parent.mkdir(parents=True)
        path.write_text(retire.QUIET_HANDLE)
        assert retire.transform(root, True) == "verified"
        assert path.read_text() == retire.QUIET_HANDLE
        assert retire.transform(root) == "retired"
        assert retire.RETIRED_SIGNATURE in path.read_text()
        assert retire.transform(root) == "already-retired"
        path.write_text(retire.QUIET_HANDLE + "\nu64 sM14DrawDoneProfile;\n")
        try:
            retire.transform(root)
        except RuntimeError:
            pass
        else:
            raise AssertionError("quiet source with profiler residue accepted")
    print("PASS: quiet renderer migration satisfies retirement marker contract")


if __name__ == "__main__":
    main()
