# Third-party software

OpenSPM is MIT licensed (LICENSE). It builds on the projects below; their
licenses apply to their code, and a release carries each one's license text.

## Shipped in the release (linked into OpenSPMNative / the renderer)

| Project | Use | License |
| --- | --- | --- |
| [Aurora](https://github.com/encounter/aurora) (pinned 749d6ee, with OpenSPM's patches in renderer/aurora/patches) | GX renderer | MIT |
| [Dawn](https://dawn.googlesource.com/dawn) | WebGPU implementation under Aurora | BSD-3-Clause |
| [SDL 3](https://libsdl.org) | window, input, audio output | Zlib |
| [Dear ImGui](https://github.com/ocornut/imgui) | the F1 menu and overlays | MIT |
| [Abseil](https://abseil.io) | Dawn dependency | Apache-2.0 |
| [{fmt}](https://github.com/fmtlib/fmt) | Aurora dependency | MIT |
| [xxHash](https://github.com/Cyan4973/xxHash) | Aurora dependency | BSD-2-Clause |
| [Tracy](https://github.com/wolfpld/tracy) (client headers) | Aurora dependency | BSD-3-Clause |

## Used to prepare the game on the player's PC

| Project | Use | License |
| --- | --- | --- |
| [DolRecomp](https://github.com/ExpansionPak/DolRecomp) (pinned 40637c4) | static recompiler (PowerPC to native objects); its CPU runtime (`cpu.c`) is compiled into the player's native module | GPL-3.0 |
| [LLVM](https://llvm.org) 20.1.8 (clang-cl, lld-link, llvm-dlltool) | compiles and links the player's native module | Apache-2.0 WITH LLVM-exception |

DolRecomp is GPL-3.0: a release that ships `dolrecomp.exe` ships it with its
license and the exact source it was built from (the pinned commit plus
OpenSPM's GPL-3.0 adaptation scripts in tools/native_bridge, see LICENSE). The
native module the player builds combines OpenSPM's MIT code with DolRecomp's
GPL-3.0 runtime; it stays on the player's PC and is never distributed.

## Not shipped

- The game. Bring your own copy of Super Paper Mario (R8PE01, revision 2).
- [Wiimms ISO Tools](https://wit.wiimm.de) (`wit`), used to extract the disc
  image; the player installs it.
