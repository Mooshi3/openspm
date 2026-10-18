# M14 generated-source provenance

This note corrects a historical naming ambiguity around the private M14 generated source.

## Proven migration input

The source tree used by the private standalone-native M14 title/load proof contains **218 generated C chunks**:

```text
main DOL text0:    1 chunk   (starts 0x80004000)
main DOL text1:  192 chunks  (starts 0x8001D940)
live relF text:   25 chunks  (starts 0x80C0401C)
                 ----------
total:           218 chunks
```

The live REL mapping previously observed for `relF.rel` uses base `0x80C03F40`; its generated text begins at `0x80C0401C`. The 25 REL chunks occupy generated filenames `chunk_0193_text2_80C0401C.c` through `chunk_0217_text2_80C6401C.c`.

The historical private archive is named:

```text
SPM-M14-whole-DOL-generated-direct-218chunks.zip
```

The `whole-DOL` wording is misleading. The archive is a **combined main-DOL + one live-REL tree**, postprocessed by the M14 direct-call transform. It is player-derived private data and must not be committed or redistributed as part of OpenSPM.

## Fresh pinned DolRecomp result

Running the pinned C backend against only the verified R8PE01 `sys/main.dol` currently yields **193 chunks**:

```text
text0:   1
text1: 192
total: 193
```

That result is internally consistent with DolRecomp's current 4096-instruction C chunk size. It does **not** reproduce the 218-chunk proof input because it contains no generated live REL text.

Therefore:

- do not change the M14 proof expectation from 218 to 193;
- do not use `--allow-chunk-count-mismatch` to make the current title/load proof accept DOL-only source;
- use the known 218-chunk already-direct private tree through `-GeneratedDirect` for current player-PC proof work;
- treat fresh 193-chunk main-DOL generation as a separate development/codegen path until deterministic REL extraction, decompression, relocation and merge are implemented in the bring-your-own-WBFS pipeline.

## Why the pinned DolRecomp commit did not prove 218-chunk regeneration

The older Windows M10/M12 build flow unpacked an already-generated portable SPM source payload and then checked out `ExpansionPak/DolRecomp@1bec3554ecc4817cf78319ca3d8a0669477f29fa` for build/runtime headers and CPU support. It did not regenerate the 218 chunks from the retail DOL during that workflow.

So the DolRecomp source revision remains important toolchain provenance, but it must not be mistaken for complete provenance of the historical 218-chunk combined generated tree.

## Productization work still required

The final bring-your-own-WBFS flow should reproduce the combined native input without any saved private rescue archive. That means OpenSPM-owned tooling must eventually:

1. verify/extract the supported disc image;
2. generate the main DOL;
3. recover/decompress the required REL from player-owned extracted files;
4. apply the validated load/relocation model (including the proven `relF` mapping for this revision);
5. generate the REL source and merge it into the player-local native module;
6. run direct-call/host-boundary transforms and identity checks;
7. keep every game-derived byte under the player-local ignored cache.

Until that exists, the rescue tree is a development checkpoint, not the final installer architecture.
