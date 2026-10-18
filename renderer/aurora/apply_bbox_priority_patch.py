#!/usr/bin/env python3
"""Keep synchronous GX bbox reads ahead of optional retained-image presentation."""
from pathlib import Path
import argparse

HEADER = '''// M14 bbox priority: suppress repeated presentation until a new XFB arrives.
bool readback_pending() noexcept;
void resume_presentation() noexcept;
'''
STATE = '''namespace {
std::atomic_bool sM14BboxReadbackPending{false};
bool m14_bbox_priority_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_BBOX_PRIORITY");
    return value == nullptr || value[0] != '0';
  }();
  return enabled;
}
}
bool readback_pending() noexcept {
  return sM14BboxReadbackPending.load(std::memory_order_acquire);
}
void resume_presentation() noexcept {
  sM14BboxReadbackPending.store(false, std::memory_order_release);
}

'''
WRAPPER = '''bool read_sync(std::array<std::uint16_t, 4>& out) noexcept {
  if (!m14_bbox_priority_enabled()) return m14_read_sync_worker(out);
  static const bool reported = [] {
    Log.info("M14 bbox priority active: readback=render-worker repeat_present=pause-until-next-xfb fresh_bounds=on");
    return true;
  }();
  (void)reported;
  // Leave this set across a burst of CPU-dependent visibility queries. No new
  // game image exists to present until the producer publishes the next XFB.
  sM14BboxReadbackPending.store(true, std::memory_order_release);
  if (gfx::render_worker::is_worker_thread()) return m14_read_sync_worker(out);
  bool result = false;
  gfx::render_worker::enqueue_work([&out, &result] {
    result = m14_read_sync_worker(out);
  });
  // The queue fence keeps both references alive and publishes the result.
  gfx::render_worker::synchronize();
  return result;
}

'''

def replace(text, old, new):
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError('bbox priority anchor is not unique: ' + old[:100])
    return text.replace(old, new, 1)


def transform(root: Path, check_only=False):
    paths = ['lib/gx/bbox.hpp', 'lib/gx/bbox.cpp', 'lib/aurora.cpp']
    old = {p: (root / p).read_text() for p in paths}
    h, b, a = [old[p] for p in paths]
    if HEADER not in h:
        h = replace(h, 'void initialize() noexcept;', HEADER + '\nvoid initialize() noexcept;')
    b = replace(b, '#include "bbox.hpp"', '#include "bbox.hpp"\n#include "../gfx/render_worker.hpp"\n#include <atomic>\n#include <cstdlib>\n#include <memory>')
    b = replace(b, 'namespace aurora::gx::bbox {\n', 'namespace aurora::gx::bbox {\n' + STATE)
    signature = 'bool read_sync(std::array<std::uint16_t, 4>& out) noexcept {'
    if 'bool m14_read_sync_worker(' not in b:
        if b.count(signature) != 1:
            raise RuntimeError('bbox read implementation missing or duplicated')
        b = b.replace(signature, 'bool m14_read_sync_worker(std::array<std::uint16_t, 4>& out) noexcept {', 1)
    b = replace(b, '} // namespace aurora::gx::bbox', WRAPPER + '} // namespace aurora::gx::bbox')
    # WaitAny may time out; a pending callback must not retain a stack reference.
    b = b.replace('wgpu::MapAsyncStatus mapStatus = wgpu::MapAsyncStatus::Error;',
                  'auto mapStatus = std::make_shared<wgpu::MapAsyncStatus>(wgpu::MapAsyncStatus::Error);')
    b = b.replace('[&mapStatus]', '[mapStatus]').replace('    mapStatus = status;', '    *mapStatus = status;')
    b = b.replace('      mapStatus != wgpu::MapAsyncStatus::Success)', '      *mapStatus != wgpu::MapAsyncStatus::Success)')
    shutdown = b[b.index('void shutdown() noexcept {'):b.index('\n}', b.index('void shutdown() noexcept {'))]
    if 'resume_presentation();' not in shutdown:
        b = replace(b, 'void shutdown() noexcept {\n', 'void shutdown() noexcept {\n  resume_presentation();\n')
    a = replace(a, '#include "gx/fifo.hpp"', '#include "gx/fifo.hpp"\n#include "gx/bbox.hpp"')
    # Check again on the render worker: a present can already be queued when
    # the producer starts a bbox read. This precedes surface acquisition.
    guard = 'if (g_m14PresenterStop || !g_m14PresenterHaveImage) {'
    a = replace(a, guard, 'if (g_m14PresenterStop || !g_m14PresenterHaveImage || gx::bbox::readback_pending()) {')
    publish = 'void m14_publish_presenter_slot(uint32_t slot) {\n'
    a = replace(a, publish, publish + '  gx::bbox::resume_presentation();\n')
    marker = 'void m14_start_async_presenter_locked() {\n'
    a = replace(a, marker, marker + '  // M14 bbox priority v1: fresh readbacks; repeated XFB presents yield until publish.\n')
    for p, value in zip(paths, [h, b, a]):
        if not check_only and value != old[p]:
            (root / p).write_text(value)
    return 'already-applied' if [h, b, a] == [old[p] for p in paths] else 'applied'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    print('Aurora bbox readback priority ' + transform(args.source, args.check_only))
