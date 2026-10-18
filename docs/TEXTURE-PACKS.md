# HD texture packs (Dolphin format)

OpenSPM loads texture packs made for Dolphin's "Load Custom Textures", such as
the Super Paper Mario HD pack and character or outfit replacement packs. The
files work unchanged: OpenSPM names and hashes textures the way Dolphin does,
so a pack made for Dolphin matches the same textures here.

Packs are made by fans and are not part of OpenSPM. Only use packs you are
allowed to download.

## Installing a pack

1. Open the OpenSPM menu (F1), go to **Texture Packs** and click **Open
   texture packs folder**. This opens (and creates if needed) `texturepacks\`
   next to `OpenSPMNative.exe`.
2. Put each pack in its own subfolder. Any of these layouts works:

   ```
   texturepacks\
     SPM HD\                  loose tex1_*.png / .dds files, in any subfolders
       characters\tex1_64x64_m_1a2b3c4d5e6f7a8b_14.png
       ...
     Mario Outfit\
       Load\Textures\R8PE01\  a Dolphin "Load" folder, copied as it is
         ...
     Old Pack\
       R8P\                   the region-free game ID also works
         ...
   ```

   If a pack contains folders named `R8PE01` or `R8P` (or Dolphin's
   `R8PE01.txt`, `R8P.txt` or `all.txt` marker files), only those folders are
   read, so you can drop in a whole Dolphin `Load` folder that also holds
   packs for other games. Otherwise everything in the pack folder is read.
   Zipped packs must be extracted first. Texture files lying directly in
   `texturepacks\` (not in a subfolder) are ignored; the menu warns about them.
3. Press **Rescan** in the menu, then **Apply now**, or restart OpenSPM.

## Several packs and priority

The menu lists every pack with an on/off box, its texture count and up/down
arrows. When two packs replace the same texture, the one higher in the list
wins. Put a character pack above the full HD pack, for example, so its Mario
wins over the HD pack's Mario. Changes are saved to `OpenSPM.ini` at once.
**Apply now** re-applies them while you play (large packs take a moment), and
a restart applies them too.

Settings in `OpenSPM.ini` (the menu writes all of them):

```ini
[TexturePacks]
enabled = 1              ; 0: read no packs at all
folder =                 ; default texturepacks (relative to the exe, or absolute)
cache_mb = 2048          ; video memory for decoded pack textures: 512 | 1024 | 2048 | 4096
dump = 0                 ; 1: dump textures (see "Making a pack")
order = Mario Outfit|SPM HD   ; priority, highest first
pack.Old Pack = 0        ; per-pack switch (default on)
```

Pack folder names containing `= ; # [ ] |` cannot be turned off or moved in the
menu (they always load); rename the folder to fix that.

## Performance and memory

* At startup OpenSPM only reads the file names in each enabled pack. A texture
  is decoded the first time the game shows it, on background threads, so the
  original texture can show for a moment before the HD one appears (Dolphin
  without "Prefetch Custom Textures" behaves the same way).
* Decoded textures stay in video memory up to `cache_mb` (default 2 GB); beyond
  that the least recently used ones are dropped and read again when needed.
  Lower it on graphics cards with little memory.
* With no packs (or `enabled = 0`) nothing is read and textures are never
  hashed for replacement.
* The **Status** section of the menu shows how many textures are indexed, how
  many the game has replaced so far, the video memory in use, and files that
  failed to load (the log names them).

## Making a pack

Turn on **Dump textures** in the menu (applies on restart) and play. Every
texture the game shows that no pack replaces is written once to
`texturedumps\R8PE01\` next to the exe, as a PNG with exactly the name Dolphin
would dump it under. Upscale or edit it, keep the file name, and put it in a
pack folder. Dumping slows scene loads; turn it off afterwards. The dumped PNGs
are uncompressed; any image editor can re-save them smaller.

Replacements can be any size (a 4x upscale is typical) but should keep the
original's aspect ratio. Mipmaps: provide a `.dds` with mipmaps, or
`_mip1`, `_mip2`, ... files next to the base PNG; a PNG without them is used as
a single level, as in Dolphin.

## Compatibility details

A Dolphin texture name is

```
tex1_<width>x<height>[_m]_<texture hash>[_<palette hash>]_<format>[_arb][_mip<N>].png|.dds
```

* **texture hash**: XXH64 (seed 0) of the texture's base level exactly as the
  game stores it in memory (GX tiled format), with the size rounded up to whole
  4x4 / 8x4 / 8x8 blocks. Mip levels are not hashed. `$` matches any texture of
  that size and format.
* **palette hash** (only for the paletted formats C4 = 8, C8 = 9, C14X2 = 10):
  XXH64 of the palette entries the base level actually uses, from the lowest to
  the highest index it contains. `$` matches any palette.
* **width, height**: the texture's own size; **format**: the GX format number
  (0 I4, 1 I8, 2 IA4, 3 IA8, 4 RGB565, 5 RGB5A3, 6 RGBA8, 8 C4, 9 C8, 10 C14X2,
  14 CMPR).
* **_m**: Dolphin adds it when the game samples the texture with mipmaps.
  Dumps include it like Dolphin; when loading, OpenSPM accepts a name with or
  without it (Dolphin requires the exact variant).
* **_arb** (arbitrary mipmaps) is accepted, but OpenSPM does not apply
  Dolphin's special level-of-detail handling for such packs; their levels are
  used as ordinary mipmaps.

Not supported: the pre-2015 Dolphin names (`R8PE01_<crc>_<format>.png`),
EFB-copy textures (`efb1_`; Dolphin does not load those either), Dolphin
graphics mods and resource packs (`.zip`, `.json`), and replacing 3D models
(Dolphin cannot either; "model" or "character" packs replace textures, which
works here). A palette hash can only match when the game's palette covers every
index the texture uses; Dolphin hashes whatever is in texture memory there.

If a pack texture does not appear, turn on **Dump textures**, find the
texture's dump and compare its name with the pack file: the size and format
must match, and the hash must match exactly (or be `$`).
