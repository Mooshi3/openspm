#!/usr/bin/env python3
"""Repair raw-Wii TLUT hardware routing in the pinned Aurora checkout.

Aurora's source-port GX path uses logical GXTlut indices (0..19) and emits
GX_AURORA metadata that carries those indices directly. An original Wii FIFO
does not: BP LOADTLUT1 (0x65) and the per-texture TLUT registers (0x98-0x9B,
0xB8-0xBB) carry TMEM-region offsets. OpenSPM's initial raw adapter treated the
LOADTLUT1 offset as a logical array index and ignored the per-texture selector.
With Nintendo's default GX regions, ordinary offsets are 0x200..0x2f0 and
0x300..0x3c0, so the old `idx < MaxTluts` guard discarded every normal palette
load.

This transform preserves Aurora's source-port behavior when no guest-memory
mapper is active. For an original-console stream it translates the Revolution
SDK default TLUT TMEM regions into Aurora's existing 20 logical slots, maps the
palette source from guest RAM, decodes hardware GXTlutSize in 16-entry units,
and decodes each texture's TLUT selector/format register. Unknown TMEM layouts
fail closed and log instead of guessing.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

OLD_TLUT = '''// TLUT load trigger (0x65)
void bp_tlut_load(u8, u32 value) noexcept {
  const auto idx = reg_get(value, 10, 0);
  if (idx < MaxTluts) {
    auto& slot = g_gxState.loadedTluts[idx];
    slot.loadTlut0 = g_gxState.bpRegCache[0x64];
    // BP LOADTLUT1 stores GXTlutSize in 16-entry units. Nintendo's SDK
    // writes this as an 11-bit field (GX_TLUT_16=1 ... GX_TLUT_16K=1024),
    // not as an entry-count-minus-one value.
    const u32 encodedTlutSize = reg_get(value, 11, 10);
    slot.numEntries = static_cast<u16>(encodedTlutSize * 16u);
    if (guest_memory::has_mapper()) {
      guest_memory::Span span;
      const u32 guestAddress = (slot.loadTlut0 & 0x00FFFFFFu) << 5;
      const u32 sourceBytes = static_cast<u32>(slot.numEntries) * sizeof(u16);
      slot.data = guest_memory::map(guestAddress, sourceBytes, &span)
                      ? static_cast<const void*>(span.data)
                      : nullptr;
    }
  }
}
'''

NEW_TLUT = '''// Original SDK default TLUT regions. LOADTLUT1 and texture TLUT
// selectors carry (TMEM address - 0x80000) >> 9, not Aurora's logical
// GXTlut enum. GXInit creates 16 256-entry regions at 0xC0000+i*0x2000 and
// four 1K-entry regions at 0xE0000+i*0x8000.
u32 raw_tlut_slot(u32 tmemOffset) noexcept {
  if (tmemOffset >= 0x200u && tmemOffset <= 0x2F0u &&
      ((tmemOffset - 0x200u) & 0xFu) == 0u) {
    return (tmemOffset - 0x200u) >> 4;
  }
  if (tmemOffset >= 0x300u && tmemOffset <= 0x3C0u &&
      ((tmemOffset - 0x300u) & 0x3Fu) == 0u) {
    return 16u + ((tmemOffset - 0x300u) >> 6);
  }
  return MaxTluts;
}

// TLUT load trigger (0x65)
void bp_tlut_load(u8, u32 value) noexcept {
  if (!guest_memory::has_mapper()) {
    // Preserve Aurora source-port semantics. Its GXLoadTlut writes a logical
    // slot index and an entry-count-minus-one field, then supplies host-pointer
    // metadata through GX_AURORA_LOAD_TLUT.
    const auto idx = reg_get(value, 10, 0);
    if (idx < MaxTluts) {
      auto& slot = g_gxState.loadedTluts[idx];
      slot.loadTlut0 = g_gxState.bpRegCache[0x64];
      slot.numEntries = static_cast<u16>(reg_get(value, 10, 10) + 1u);
    }
    return;
  }

  const u32 tmemOffset = reg_get(value, 10, 0);
  const u32 idx = raw_tlut_slot(tmemOffset);
  if (idx >= MaxTluts) {
    Log.error("Unsupported original GX TLUT TMEM offset 0x{:03X}", tmemOffset);
    return;
  }

  auto& slot = g_gxState.loadedTluts[idx];
  slot.loadTlut0 = g_gxState.bpRegCache[0x64];
  // Real LOADTLUT1 stores GXTlutSize in 16-entry units in an 11-bit field.
  const u32 encodedTlutSize = reg_get(value, 11, 10);
  slot.numEntries = static_cast<u16>(encodedTlutSize * 16u);

  guest_memory::Span span;
  const u32 guestAddress = (slot.loadTlut0 & 0x00FFFFFFu) << 5;
  const u32 sourceBytes = static_cast<u32>(slot.numEntries) * sizeof(u16);
  slot.data = sourceBytes != 0u && guest_memory::map(guestAddress, sourceBytes, &span)
                  ? static_cast<const void*>(span.data)
                  : nullptr;
  if (slot.data == nullptr) {
    Log.error("Failed to map original GX TLUT source 0x{:08X} ({} bytes, TMEM 0x{:03X})",
              guestAddress, sourceBytes, tmemOffset);
  }
}
'''

OLD_IMAGE3 = '''  case 5: // Image3
    slot.image3 = value;
    if (guest_memory::has_mapper()) {
      guest_memory::Span span;
      const u32 guestAddress = (value & 0x00FFFFFFu) << 5;
      const size_t wanted = texture::texture_source_size(
          slot.format(), slot.width(), slot.height(), slot.mip_count());
      const u32 minSize = wanted != 0 && wanted <= UINT32_MAX
                              ? static_cast<u32>(wanted)
                              : 1u;
      slot.data = guest_memory::map(guestAddress, minSize, &span)
                      ? static_cast<const void*>(span.data)
                      : nullptr;
    }
    break;
'''

NEW_IMAGE3 = OLD_IMAGE3 + '''  case 6: // TLUT selector / format
    if (guest_memory::has_mapper()) {
      const u32 tmemOffset = reg_get(value, 10, 0);
      const u32 tlutIdx = raw_tlut_slot(tmemOffset);
      if (tlutIdx >= MaxTluts) {
        // Keep an invalid logical index so palette resolution fails closed.
        slot.tlut = static_cast<GXTlut>(MaxTluts);
        Log.error("Unsupported original GX texture TLUT offset 0x{:03X} for map {}",
                  tmemOffset, texMapId);
      } else {
        slot.tlut = static_cast<GXTlut>(tlutIdx);
        g_gxState.loadedTluts[tlutIdx].format =
            static_cast<GXTlutFmt>(reg_get(value, 2, 10));
      }
    }
    break;
'''

OLD_REG = '      regs[base + 0x18 + i] = {};                      // TLUT region TMEM offset\n'
NEW_REG = '      regs[base + 0x18 + i] = {bp_tex, DirtyTextures}; // TLUT region TMEM offset + format\n'

SIGNATURE = 'u32 raw_tlut_slot(u32 tmemOffset) noexcept {'


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / 'lib/gx/regs.cpp'
    text = path.read_text(encoding='utf-8')
    if SIGNATURE in text:
        if NEW_REG not in text or NEW_IMAGE3 not in text or NEW_TLUT not in text:
            raise RuntimeError('lib/gx/regs.cpp: raw TLUT routing signature is present but transform is incomplete')
        return 'already-applied'

    text = replace_once(text, OLD_TLUT, NEW_TLUT, 'raw TLUT load handler')
    text = replace_once(text, OLD_IMAGE3, NEW_IMAGE3, 'raw texture Image3/TLUT handler')
    text = replace_once(text, OLD_REG, NEW_REG, 'raw texture TLUT register table')

    if not check_only:
        path.write_text(text, encoding='utf-8', newline='\n')
    return 'verified' if check_only else 'applied'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=pathlib.Path, help='pinned Aurora checkout')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()

    source = args.source.resolve()
    expected = json.loads(LOCK.read_text(encoding='utf-8'))['commit']
    try:
        actual = run('git', 'rev-parse', 'HEAD', cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f'error: not a usable git checkout: {source}: {exc}', file=sys.stderr)
        return 2
    if actual != expected:
        print(f'error: Aurora revision mismatch: expected {expected}, got {actual}', file=sys.stderr)
        return 3

    try:
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f'error: Aurora raw TLUT routing adaptation failed: {exc}', file=sys.stderr)
        return 4

    if result == 'already-applied':
        print('Aurora raw TLUT routing adaptation already applied')
    elif result == 'verified':
        print('Aurora raw TLUT routing adaptation verified')
    else:
        print('Aurora raw TLUT routing adaptation applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
