#!/usr/bin/env python3
"""Opt-in copy-boundary replay presentation; conservative fallback on mutable resources."""
from pathlib import Path
import argparse

HERE = Path(__file__).resolve().parent
BBOX = r'''
namespace {
bool sM14ReplayBbox = false;
wgpu::Buffer sM14ReplayBboxStorage;
wgpu::BindGroup sM14ReplayBboxGroup;
}
void set_replay_target(bool enabled) {
  if (enabled && !sM14ReplayBboxGroup) {
    const wgpu::BufferDescriptor descriptor{
        .label = "OpenSPM private replay bbox", .usage = wgpu::BufferUsage::Storage, .size = 16,
    };
    sM14ReplayBboxStorage = webgpu::g_device.CreateBuffer(&descriptor);
    const wgpu::BindGroupEntry entry{.binding = 0, .buffer = sM14ReplayBboxStorage, .offset = 0, .size = 16};
    const wgpu::BindGroupDescriptor group{
        .label = "OpenSPM private replay bbox", .layout = sBindGroupLayout,
        .entryCount = 1, .entries = &entry,
    };
    sM14ReplayBboxGroup = webgpu::g_device.CreateBindGroup(&group);
  }
  sM14ReplayBbox = enabled;
}
'''

def once(text, old, new):
    if new in text: return text
    if text.count(old) != 1: raise RuntimeError('visible replay anchor not unique: ' + old[:90])
    return text.replace(old, new, 1)


def transform(root: Path, check_only=False):
    paths = ['lib/gfx/frame_packet.hpp', 'lib/gfx/encoding.cpp', 'lib/gx/bbox.hpp', 'lib/gx/bbox.cpp', 'lib/aurora.cpp', 'lib/gfx/recording.cpp']
    original = {p: (root / p).read_text() for p in paths}
    packet, encoding, header, bbox, aurora, recording = [original[p] for p in paths]
    if "struct M14PresenterViewportAdjustment {" not in aurora or \
       "m14_presenter_viewport_adjustment(" not in aurora:
        raise RuntimeError("dev143 async-presenter viewport hook prerequisite missing")
    packet = once(packet, '  uint32_t m14ReplayOriginalUniformBytes = 0;',
                  '  uint32_t m14ReplayLastPass = UINT32_MAX;\n  uint32_t m14ReplayOriginalUniformBytes = 0;')
    header = once(header, 'void initialize() noexcept;',
        'void set_replay_target(bool enabled);\nstruct ReplayScope {\n  ReplayScope() { set_replay_target(true); }\n  ~ReplayScope() { set_replay_target(false); }\n  ReplayScope(const ReplayScope&) = delete;\n};\nvoid initialize() noexcept;')
    bbox = once(bbox, 'void shutdown() noexcept {', BBOX + '\nvoid shutdown() noexcept {')
    bbox = once(bbox, 'void shutdown() noexcept {\n',
        'void shutdown() noexcept {\n  sM14ReplayBbox = false;\n  sM14ReplayBboxGroup = {};\n  sM14ReplayBboxStorage = {};\n')
    bbox = once(bbox, 'const wgpu::BindGroup& bind_group() noexcept { return sBindGroup; }',
        'const wgpu::BindGroup& bind_group() noexcept { return sM14ReplayBbox ? sM14ReplayBboxGroup : sBindGroup; }')
    encoding = once(encoding, '#include "encoding.hpp"', '#include "encoding.hpp"\n#include "../gx/bbox.hpp"')
    begin = encoding.index('bool m14_encode_interpolation_replay(')
    end = encoding.index('\n}\n', begin) + 3
    body = encoding[begin:end]
    body = once(body, '  stats = {};', '  gx::bbox::ReplayScope privateBbox;\n  stats = {};')
    for old in ['for (const auto& passInfo : frame.renderPasses) {', 'for (auto& passInfo : frame.renderPasses) {']:
        if old in body:
            body = body.replace(old, 'for (size_t passIndex = 0; passIndex < frame.renderPasses.size() && passIndex <= frame.m14ReplayLastPass; ++passIndex) {\n    auto& passInfo = frame.renderPasses[passIndex];', 1)
    anchor = '    for (const auto& command : passInfo.commands) {\n'
    preflight = '''      if (command.type == CommandType::Draw && command.data.draw.m14ReplayGX) {
        gx::DrawData data{};
        std::memcpy(&data, command.data.draw.payload.data(), sizeof(data));
        const uint64_t end = uint64_t(data.uniformRange.offset) + data.uniformRange.size;
        if (end > frame.m14ReplayOriginalUniformBytes ||
            uint64_t(uniformBase) + end > frame.uniforms.size()) {
          ++stats.rejectedPasses; ++stats.rangeRejects; return false;
        }
      }
'''
    body = once(body, anchor, anchor + preflight)
    encoding = encoding[:begin] + body + encoding[end:]
    for include in ('#include "gfx/tex_copy_conv.hpp"', '#include "gx/pipeline.hpp"', '#include <deque>'):
        lines = aurora.splitlines(keepends=True)
        found = False
        kept = []
        for line in lines:
            if line.strip() == include:
                if found: continue
                found = True
            kept.append(line)
        aurora = ''.join(kept)
        if not found:
            aurora = once(aurora, '#include "gfx/encoding.hpp"', '#include "gfx/encoding.hpp"\n' + include)
    declarations = '''bool m14_visible_interpolation();
void m14_clear_interpolation_queue();
M14PresenterImage m14_choose_interpolation_image(M14PresenterImage fallback);
'''
    aurora = once(aurora, 'void m14_presenter_main() {', declarations + '\nvoid m14_presenter_main() {')
    aurora = once(aurora, '        (void)m14_present_retained_image(image);',
                  '        image = m14_choose_interpolation_image(std::move(image));\n        (void)m14_present_retained_image(image);')
    # Helpers live outside the dev139 and dev140 generated regions.
    marker = '// M14 visible interpolation helpers BEGIN\n'
    end_marker = '// M14 visible interpolation helpers END\n'
    helpers = marker + (HERE / 'interpolation_present.inl').read_text() + end_marker
    if marker in aurora:
        start = aurora.index(marker); end = aurora.index(end_marker, start) + len(end_marker)
        aurora = aurora[:start] + helpers + aurora[end:]
    else:
        start = aurora.index('void m14_encode_interpolation_replay_proof(')
        end = aurora.index('\n}\n', start) + 3
        aurora = aurora[:end] + '\n' + helpers + aurora[end:]
    aurora = once(aurora, '      m14_encode_interpolation_replay_proof(encoder);',
        '      if (m14_visible_interpolation()) m14_encode_visible_interpolation(encoder, openspmDisplayCopySnapshot);\n      else m14_encode_interpolation_replay_proof(encoder);')
    aurora = once(aurora, '      m14_publish_presenter_slot(m14PresenterSlot);',
        '      m14_publish_interpolation_images();\n      m14_publish_presenter_slot(m14PresenterSlot);')
    # The stop path runs after the presenter thread joins and the worker drains.
    # Reset with replay targets (the existing shutdown callback owns this path).
    aurora = once(aurora, 'void m14_reset_replay_encode_targets() noexcept {\n',
        'void m14_reset_replay_encode_targets() noexcept {\n  m14_clear_interpolation_queue();\n')
    mirror = r'''    // M14 cacheable replay mirrors: copy the original CPU input, never mapped upload reads.
    if (m14_replay_enabled()) {
      auto& frame = g_recorder.frame();
      auto* mirror = &target == &frame.uniforms ? &frame.m14UniformRead :
                     &target == &frame.verts ? &frame.m14VertexRead :
                     &target == &frame.indices ? &frame.m14IndexRead : nullptr;
      if (mirror) {
        mirror->resize(begin + length, 0);
        std::memcpy(mirror->data() + begin, data, length);
      }
    }
'''
    recording = once(recording, '    target.append(data, length);',
                     mirror + '    target.append(data, length);')
    output = [packet, encoding, header, bbox, aurora, recording]
    for name, value in zip(paths, output):
        if not check_only and value != original[name]: (root / name).write_text(value)
    return 'already-applied' if output == [original[p] for p in paths] else 'applied'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    print('Aurora visible interpolation ' + transform(args.source, args.check_only))
