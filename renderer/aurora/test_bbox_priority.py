#!/usr/bin/env python3
"""Source migration and emitted queue/lifetime contract tests; no GPU required."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('bbox_priority', HERE / 'apply_bbox_priority_patch.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def test_transform():
    files = {
        'lib/gx/bbox.hpp': 'void initialize() noexcept;\n',
        'lib/gx/bbox.cpp': '#include "bbox.hpp"\nnamespace aurora::gx::bbox {\n'
        'void shutdown() noexcept {\n}\n'
        'bool read_sync(std::array<std::uint16_t, 4>& out) noexcept {\nreturn true;\n}\n'
        '} // namespace aurora::gx::bbox\n',
        'lib/aurora.cpp': '#include "gx/fifo.hpp"\n'
        'if (g_m14PresenterStop || !g_m14PresenterHaveImage) {\n}\n'
        'void m14_publish_presenter_slot(uint32_t slot) {\n}\n'
        'void m14_start_async_presenter_locked() {\n}\n',
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, value in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value)
        assert patch.transform(root, True) == 'applied'
        assert all((root / p).read_text() == v for p, v in files.items())
        assert patch.transform(root) == 'applied'
        assert patch.transform(root) == 'already-applied'
        # The predecessor canonicalizes the presenter loop each configure.
        p = root / 'lib/aurora.cpp'
        p.write_text(p.read_text().replace(' || gx::bbox::readback_pending()', ''))
        assert patch.transform(root) == 'applied'
        before = {p: (root / p).read_text() for p in files}
        p.write_text(p.read_text().replace(' || gx::bbox::readback_pending()', '') +
                     'if (g_m14PresenterStop || !g_m14PresenterHaveImage) {\n')
        before['lib/aurora.cpp'] = p.read_text()
        try:
            patch.transform(root)
        except RuntimeError:
            pass
        else:
            raise AssertionError('ambiguous source accepted')
        assert all((root / p).read_text() == v for p, v in before.items())


def test_wrapper():
    source = r'''
#include <array>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <functional>
struct { template<class... T> void info(T...) {} } Log;
namespace gfx::render_worker {
bool worker = false;
int queued = 0;
std::function<void()> pending;
bool is_worker_thread() { return worker; }
void enqueue_work(std::function<void()> fn) { ++queued; pending = fn; }
void synchronize() { worker = true; pending(); pending = {}; worker = false; }
}
int reads = 0;
bool m14_read_sync_worker(std::array<std::uint16_t, 4>& out) noexcept {
  out[0] = ++reads; return true;
}
''' + patch.STATE + patch.WRAPPER + r'''
int main(int argc, char**) {
  std::array<std::uint16_t, 4> out{};
  assert(!readback_pending());
  assert(read_sync(out) && out[0] == 1);
  assert(read_sync(out) && out[0] == 2); // Fresh results, never stale caching.
  if (argc > 1) {
    assert(!readback_pending() && gfx::render_worker::queued == 0);
  } else {
    assert(readback_pending() && gfx::render_worker::queued == 2);
    gfx::render_worker::worker = true;
    assert(read_sync(out) && out[0] == 3);
    assert(gfx::render_worker::queued == 2); // No recursive queue/deadlock.
    resume_presentation();
    assert(!readback_pending());
  }
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / 'test.cpp').write_text(source)
        subprocess.run(['g++', '-std=c++20', '-O2', str(p / 'test.cpp'), '-o', str(p / 'test')], check=True)
        import os
        env = dict(os.environ, OPENSPM_M14_BBOX_PRIORITY='1')
        subprocess.run([str(p / 'test')], env=env, check=True)
        env['OPENSPM_M14_BBOX_PRIORITY'] = '0'
        subprocess.run([str(p / 'test'), 'disabled'], env=env, check=True)


if __name__ == '__main__':
    test_transform()
    test_wrapper()
    print('PASS: bbox priority migration and emitted queue contracts')
