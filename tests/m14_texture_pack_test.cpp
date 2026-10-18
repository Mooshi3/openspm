// Host test: Dolphin custom-texture naming (renderer/aurora/openspm_texture_pack_names.hpp).
// Synthetic textures only. XXH64 here is an independent reference implementation
// (the renderer uses Aurora's xxHash); its vectors were cross-checked against
// xxHash v0.8.3, the version Aurora pins.
#include "../renderer/aurora/openspm_texture_pack_names.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

int g_failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                                        \
        }                                                                        \
    } while (0)

constexpr std::uint64_t P1 = 0x9E3779B185EBCA87ull, P2 = 0xC2B2AE3D27D4EB4Full, P3 = 0x165667B19E3779F9ull,
                        P4 = 0x85EBCA77C2B2AE63ull, P5 = 0x27D4EB2F165667C5ull;
std::uint64_t rotl(std::uint64_t v, int r) { return (v << r) | (v >> (64 - r)); }
std::uint64_t le64(const std::uint8_t *p) {
    std::uint64_t v = 0;
    for (int i = 7; i >= 0; --i) v = (v << 8) | p[i];
    return v;
}
std::uint32_t le32(const std::uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | (std::uint32_t(p[3]) << 24); }
std::uint64_t round1(std::uint64_t acc, std::uint64_t in) { return rotl(acc + in * P2, 31) * P1; }
std::uint64_t merge(std::uint64_t acc, std::uint64_t v) { return (acc ^ round1(0, v)) * P1 + P4; }

std::uint64_t xxh64(const void *data, std::size_t len, std::uint64_t seed = 0) {
    const auto *p = static_cast<const std::uint8_t *>(data);
    const std::uint8_t *end = p + len;
    std::uint64_t h;
    if (len >= 32) {
        std::uint64_t v1 = seed + P1 + P2, v2 = seed + P2, v3 = seed, v4 = seed - P1;
        for (; p + 32 <= end; p += 32) {
            v1 = round1(v1, le64(p));
            v2 = round1(v2, le64(p + 8));
            v3 = round1(v3, le64(p + 16));
            v4 = round1(v4, le64(p + 24));
        }
        h = rotl(v1, 1) + rotl(v2, 7) + rotl(v3, 12) + rotl(v4, 18);
        h = merge(merge(merge(merge(h, v1), v2), v3), v4);
    } else {
        h = seed + P5;
    }
    h += len;
    for (; p + 8 <= end; p += 8) h = rotl(h ^ round1(0, le64(p)), 27) * P1 + P4;
    if (p + 4 <= end) {
        h = rotl(h ^ (std::uint64_t(le32(p)) * P1), 23) * P2 + P3;
        p += 4;
    }
    for (; p < end; ++p) h = rotl(h ^ (*p * P5), 11) * P1;
    h ^= h >> 33;
    h *= P2;
    h ^= h >> 29;
    h *= P3;
    h ^= h >> 32;
    return h;
}

void test_xxh64() {
    CHECK(xxh64("", 0) == 0xEF46DB3751D8E999ull);
    CHECK(xxh64("a", 1) == 0xD24EC4F1A98C6E5Bull);
    CHECK(xxh64("abc", 3) == 0x44BC2CF5AD770999ull);
    std::vector<std::uint8_t> ramp(100);
    for (std::size_t i = 0; i < ramp.size(); ++i) ramp[i] = static_cast<std::uint8_t>(i);
    CHECK(xxh64(ramp.data(), ramp.size()) == 0x6AC1E58032166597ull);
}

void test_sizes() {
    using namespace openspm_texpack;
    CHECK(base_level_bytes(kI4, 8, 8) == 32);
    CHECK(base_level_bytes(kI4, 9, 8) == 64);  // 4-bit formats: 8x8 blocks
    CHECK(base_level_bytes(kCMPR, 64, 64) == 2048);
    CHECK(base_level_bytes(kI8, 8, 4) == 32);  // 8-bit formats: 8x4 blocks
    CHECK(base_level_bytes(kC8, 10, 5) == 128);
    CHECK(base_level_bytes(kRGB5A3, 4, 4) == 32);
    CHECK(base_level_bytes(kRGBA8, 4, 4) == 64);
    CHECK(base_level_bytes(kRGBA8, 640, 480) == 640u * 480u * 4u);
    CHECK(base_level_bytes(kC14X2, 3, 3) == 32);
    CHECK(base_level_bytes(7, 4, 4) == 0);
}

// A 16x8 I8 texture (4 blocks of 8x4) whose bytes are 0..127; mipmapped sampler.
void test_i8_name() {
    using namespace openspm_texpack;
    std::vector<std::uint8_t> tex(base_level_bytes(kI8, 16, 8));
    CHECK(tex.size() == 128);
    for (std::size_t i = 0; i < tex.size(); ++i) tex[i] = static_cast<std::uint8_t>(i);
    const std::uint64_t h = xxh64(tex.data(), tex.size());
    CHECK(name(16, 8, true, h, false, 0, kI8) == "tex1_16x8_m_7a7fe14647b9ab92_1");
    CHECK(name(16, 8, false, h, false, 0, kI8) == "tex1_16x8_7a7fe14647b9ab92_1");
    CHECK(!referenced_tlut(kI8, tex.data(), tex.size()).paletted);
}

// An 8x8 C4 texture using palette entries 3..9 of a 16-entry IA8 TLUT.
void test_c4_name() {
    using namespace openspm_texpack;
    std::vector<std::uint8_t> tex(base_level_bytes(kC4, 8, 8), 0x55);
    CHECK(tex.size() == 32);
    tex[0] = 0x39; // indices 3 and 9
    tex[31] = 0x74;
    const TlutRange range = referenced_tlut(kC4, tex.data(), tex.size());
    CHECK(range.paletted && range.first == 3 && range.count == 7);
    CHECK(range.byte_offset() == 6 && range.byte_size() == 14);
    std::vector<std::uint8_t> tlut(32);
    for (std::size_t i = 0; i < tlut.size(); ++i) tlut[i] = static_cast<std::uint8_t>(0xF0 - i);
    const std::uint64_t th = xxh64(tex.data(), tex.size());
    const std::uint64_t lh = xxh64(tlut.data() + range.byte_offset(), range.byte_size());
    CHECK(name(8, 8, false, th, true, lh, kC4) == "tex1_8x8_46ddbf0416f86334_038b20d5ef79bee4_8");
    // C8 and C14X2 index ranges; C14X2 indices are big-endian, low 14 bits.
    const std::uint8_t c8[] = {7, 200, 9, 7};
    const TlutRange r8 = referenced_tlut(kC8, c8, sizeof c8);
    CHECK(r8.first == 7 && r8.count == 194);
    const std::uint8_t c14[] = {0xC0, 0x05, 0x01, 0x00, 0x3F, 0xFF, 0x00, 0x02};
    const TlutRange r14 = referenced_tlut(kC14X2, c14, sizeof c14);
    CHECK(r14.first == 2 && r14.count == 0x3FFE);
}

void test_parse() {
    using openspm_texpack::parse;
    auto a = parse("tex1_64x32_m_0123456789abcdef_14.png");
    CHECK(a && a->width == 64 && a->height == 32 && a->mipmapped && a->tex_hash == 0x0123456789abcdefull &&
          !a->has_tlut && a->format == 14 && a->mip_level == 0 && !a->arbitrary_mips);
    auto b = parse("tex1_128x128_00000000deadbeef_$_9.DDS");
    CHECK(b && !b->mipmapped && b->has_tlut && b->any_tlut && b->tex_hash == 0xdeadbeefull && b->format == 9);
    auto c = parse("tex1_32x32_$_5_arb.png");
    CHECK(c && c->any_texture && c->arbitrary_mips && c->format == 5 && !c->has_tlut);
    auto d = parse("tex1_256x256_m_0123456789abcdef_fedcba9876543210_8_mip3.png");
    CHECK(d && d->mip_level == 3 && d->has_tlut && d->tlut_hash == 0xfedcba9876543210ull && d->format == 8);
    auto e = parse("tex1_16x16_m_0123456789abcdef_1_arb_mip1.png");
    CHECK(e && e->arbitrary_mips && e->mip_level == 1);
    const std::string built =
        openspm_texpack::name(40, 20, true, 0xabcull, true, 0x12ull, openspm_texpack::kC8) + ".png";
    auto f = parse(built);
    CHECK(built == "tex1_40x20_m_0000000000000abc_0000000000000012_9.png");
    CHECK(f && f->width == 40 && f->tex_hash == 0xabc && f->tlut_hash == 0x12 && f->format == 9);
    for (const char *bad : {"tex1_64x32_0123456789abcdef_14.jpg", "tex2_64x32_0123456789abcdef_14.png",
                            "tex1_64_0123456789abcdef_14.png", "tex1_0x32_0123456789abcdef_14.png",
                            "tex1_64x32_0123456789abcdeg_14.png", "tex1_64x32_14.png",
                            "tex1_64x32_0123456789abcdef_x.png", "tex1_64x32_0123456789abcdef_14_mip0.png",
                            "tex1_64x32_1_2_3_4.png", "efb1_64x32_0123456789abcdef_14.png",
                            "tex1_64x32_00112233445566778_14.png", "readme.txt"}) {
        if (parse(bad)) {
            std::fprintf(stderr, "accepted invalid name %s\n", bad);
            ++g_failures;
        }
    }
}

void test_png() {
    const std::uint8_t rgba[] = {255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12};
    const auto png = openspm_texpack::encode_png_rgba8(3, 2, rgba);
    CHECK(png.size() > 8 + 25 + 12 + 12);
    CHECK(std::memcmp(png.data(), "\x89PNG\r\n\x1a\n", 8) == 0);
    CHECK(std::memcmp(png.data() + 12, "IHDR", 4) == 0 && png[16 + 7] == 2 && png[16 + 8] == 8 && png[16 + 9] == 6);
    const std::uint8_t iend[] = {0, 0, 0, 0, 'I', 'E', 'N', 'D', 0xAE, 0x42, 0x60, 0x82};
    CHECK(std::memcmp(png.data() + png.size() - 12, iend, 12) == 0);
    // IDAT: zlib header, one final stored block of 2 rows x (1 + 12) bytes, Adler-32.
    const std::size_t idat = 8 + 25;
    CHECK(std::memcmp(png.data() + idat + 4, "IDAT", 4) == 0);
    const std::uint8_t *z = png.data() + idat + 8;
    CHECK(z[0] == 0x78 && z[2] == 1 && z[3] == 26 && z[4] == 0 && z[5] == 0xE5 && z[6] == 0xFF);
    CHECK(z[7] == 0 && std::memcmp(z + 8, rgba, 12) == 0 && z[20] == 0 && std::memcmp(z + 21, rgba + 12, 12) == 0);
    CHECK(openspm_texpack::encode_png_rgba8(0, 2, rgba).empty());
}

} // namespace

int main() {
    test_xxh64();
    test_sizes();
    test_i8_name();
    test_c4_name();
    test_parse();
    test_png();
    if (g_failures != 0) {
        std::fprintf(stderr, "%d texture pack check(s) failed\n", g_failures);
        return 1;
    }
    std::puts("PASS: texture pack naming (Dolphin tex1_ names, XXH64, TLUT range, PNG dump)");
    return 0;
}
