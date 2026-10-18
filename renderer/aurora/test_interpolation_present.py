#!/usr/bin/env python3
"""Exercise emitted scheduling and conservative resource preflight without a GPU."""
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent

def function(source, signature):
    start = source.index(signature)
    end = source.index('\n}\n', start) + 3
    return source[start:end]

def main():
    src = (HERE / 'interpolation_present.inl').read_text()
    scheduler = src[:src.index('// Only replay packets')].replace('std::chrono::steady_clock;', 'FakeClock;')
    preflight = function(src, 'const char* m14_preflight_visible(')
    publish = function(src, 'void m14_publish_interpolation_images()')
    code = r'''
#include <array>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <memory>
#include <vector>
struct FakeClock {
 using time_point = std::chrono::steady_clock::time_point;
 static inline time_point current{};
 static time_point now() { return current; }
};
struct { template<class... T> void info(T...) {} } Log;
struct M14PresenterImage { std::shared_ptr<int> texture; };
struct M14PresenterViewportAdjustment { bool active=false; };
namespace gx { struct DrawData { struct { uint32_t offset=0, size=0; } uniformRange; }; }
namespace gfx {
 using TextureHandle = std::shared_ptr<int>;
 namespace detail {
 enum class CommandType { Draw, Other };
 struct Command { CommandType type=CommandType::Draw; struct { struct {
  bool m14ReplayGX=true; std::array<char,sizeof(gx::DrawData)> payload{};
 } draw; } data; };
 struct RenderPass {
  bool hasDraws=true, m14ReplayEfb=true, discardable=false;
  gfx::TextureHandle resolveTarget;
  std::vector<int> paletteConvs;
  std::vector<Command> commands;
 };
 enum class FrameOpType { RenderPass, Other };
 struct FrameOp {
  FrameOpType type=FrameOpType::RenderPass;
  RenderPass* renderPass=nullptr; uint32_t index=0;
  std::vector<int> textureUploads;
 };
 struct FramePacket {
  std::vector<int> m14ReplayPhases{1}, encoderTasks, textureCopies;
  std::deque<RenderPass> renderPasses;
  std::vector<FrameOp> ops;
  uint32_t m14ReplayOriginalUniformBytes=64;
  uint32_t m14ReplayMatchedUniforms=0, m14ReplaySnappedUniforms=0;
 };
 }
}
''' + scheduler + preflight + publish + r'''
int main() {
 setenv("OPENSPM_M14_INTERP_PRESENT", "1", 1);
 using namespace std::chrono_literals;
 M14PresenterImage fallback{std::make_shared<int>(0)};
 auto a = std::make_shared<int>(1), b = std::make_shared<int>(2);
 g_m14InterpPending = {{{a},FakeClock::current+10ms},{{b},FakeClock::current+20ms}};
 m14_publish_interpolation_images();
 assert(g_m14InterpPending.empty());
 assert(*m14_choose_interpolation_image(fallback).texture == 0);
 FakeClock::current += 10ms;
 assert(*m14_choose_interpolation_image(fallback).texture == 1);
 FakeClock::current += 15ms;
 assert(*m14_choose_interpolation_image(fallback).texture == 2);
 assert(g_m14InterpQueue.empty());
 assert(*m14_choose_interpolation_image(fallback).texture == 2);
 for (int i=0;i<20;++i) g_m14InterpPending.push_back({{a},FakeClock::current+1ms});
 m14_publish_interpolation_images(); assert(g_m14InterpQueue.size()==8);
 m14_clear_interpolation_queue();
 assert(g_m14InterpQueue.empty() && !g_m14InterpLast.texture);
 assert(*m14_choose_interpolation_image(fallback).texture == 0);
 gfx::detail::FramePacket f;
 f.renderPasses.emplace_back();
 auto display = std::make_shared<int>(1);
 f.renderPasses[0].resolveTarget=display;
 f.ops.push_back({gfx::detail::FrameOpType::RenderPass,&f.renderPasses[0],0,{1}});
 uint32_t last=999;
 assert(!m14_preflight_visible(f,display,last) && last==0);
 f.encoderTasks.push_back(1);
 assert(m14_preflight_visible(f,display,last));f.encoderTasks.clear();
 f.textureCopies.push_back(1);
 assert(m14_preflight_visible(f,display,last));f.textureCopies.clear();
 f.renderPasses[0].paletteConvs.push_back(1);
 assert(m14_preflight_visible(f,display,last));f.renderPasses[0].paletteConvs.clear();
 f.renderPasses.emplace_back();f.renderPasses[1].hasDraws=false;
 f.ops.push_back({gfx::detail::FrameOpType::RenderPass,&f.renderPasses[1],1,{}});
 assert(!m14_preflight_visible(f,display,last) && last==0); // Stop before post-copy clear.
 f.ops[1].textureUploads.push_back(1);
 assert(m14_preflight_visible(f,display,last));f.ops[1].textureUploads.clear();
 f.renderPasses[1].resolveTarget=std::make_shared<int>(2);
 assert(m14_preflight_visible(f,display,last));f.renderPasses[1].resolveTarget={};
 f.renderPasses[0].commands.emplace_back();
 gx::DrawData data; data.uniformRange={60,8};
 std::memcpy(f.renderPasses[0].commands[0].data.draw.payload.data(),&data,sizeof(data));
 assert(m14_preflight_visible(f,display,last));
 data.uniformRange={0,64};
 std::memcpy(f.renderPasses[0].commands[0].data.draw.payload.data(),&data,sizeof(data));
 assert(!m14_preflight_visible(f,display,last));
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'test.cpp').write_text(code)
        subprocess.run(['g++','-std=c++20','-O2','-fsanitize=address,undefined',str(root/'test.cpp'),'-o',str(root/'test')],check=True)
        import os
        subprocess.run([str(root/'test')],env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'),check=True)
    print('PASS: visible replay queue and resource preflight')

if __name__ == '__main__': main()
