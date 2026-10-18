// OpenSPM texture packs: Dolphin "Load Custom Textures" naming rules.
//
// Dependency-free (no Aurora, SDL or xxHash) so it is host unit-tested
// (tests/m14_texture_pack_test.cpp). apply_texture_pack_patch.py embeds this
// file into Aurora's lib/gfx/texture_replacement.cpp (Dolphin-named PNG dumps),
// and the F1 menu uses parse() to count the textures in a pack.
//
// Dolphin (VideoCommon/TextureInfo.cpp, CalculateTextureName) names a texture
//   tex1_<w>x<h>[_m]_<texhash>[_<tluthash>]_<fmt>
//   w, h      the texture's own size (texImage0 width/height + 1), decimal
//   _m        present when the sampler enables mipmaps (TEX_MODE0 bits 5-6,
//             "mipmap_filter", non-zero), whatever the level count
//   texhash   XXH64, seed 0, of the base level only: the GX-tiled source bytes
//             as stored in RAM, size = the base level rounded up to whole
//             blocks (base_level_bytes below), 16 lower-case hex digits
//   tluthash  paletted formats only (C4, C8, C14X2): XXH64, seed 0, of the
//             palette entries the base level actually uses, i.e. 2 bytes per
//             entry from the smallest to the largest index found in the base
//             level's bytes (block padding included). Big-endian entries as
//             loaded, so a palette swap of the same image gets its own name.
//   fmt       the GX texture format number, decimal (0 I4 ... 14 CMPR)
// Pack files add an extension (.png or .dds) and optionally:
//   "$" for the texture hash (any texture of that size/format) or the TLUT
//   hash (any palette), "_arb" after the format (arbitrary mipmaps: the pack
//   supplies deliberately different mip levels), and "_mip<N>" sidecar files
//   holding level N of a base file.
#ifndef OPENSPM_TEXTURE_PACK_NAMES_HPP
#define OPENSPM_TEXTURE_PACK_NAMES_HPP

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace openspm_texpack {

enum GxFormat : std::uint32_t {
    kI4 = 0, kI8 = 1, kIA4 = 2, kIA8 = 3, kRGB565 = 4, kRGB5A3 = 5, kRGBA8 = 6,
    kC4 = 8, kC8 = 9, kC14X2 = 10, kCMPR = 14,
};

inline bool is_paletted(std::uint32_t fmt) { return fmt == kC4 || fmt == kC8 || fmt == kC14X2; }

// Dolphin TexDecoder_GetTextureSizeInBytes for one level: the size expanded to
// whole blocks (8x8 for 4-bit formats, 8x4 for 8-bit, 4x4 otherwise; 32-byte
// blocks, RGBA8 64). 0 for an unknown format.
inline std::size_t base_level_bytes(std::uint32_t fmt, std::uint32_t w, std::uint32_t h) {
    std::uint32_t bw = 4, bh = 4, block = 32;
    switch (fmt) {
    case kI4: case kC4: case kCMPR: bw = 8; bh = 8; break;
    case kI8: case kIA4: case kC8: bw = 8; bh = 4; break;
    case kIA8: case kRGB565: case kRGB5A3: case kC14X2: break;
    case kRGBA8: block = 64; break;
    default: return 0;
    }
    return static_cast<std::size_t>((w + bw - 1) / bw) * ((h + bh - 1) / bh) * block;
}

// The palette entries a paletted base level references: [first, first + count).
struct TlutRange {
    bool paletted = false;
    std::uint32_t first = 0;
    std::uint32_t count = 0;
    std::size_t byte_offset() const { return static_cast<std::size_t>(first) * 2u; }
    std::size_t byte_size() const { return static_cast<std::size_t>(count) * 2u; }
};

inline TlutRange referenced_tlut(std::uint32_t fmt, const std::uint8_t *base, std::size_t bytes) {
    TlutRange range;
    if (!is_paletted(fmt) || base == nullptr || bytes == 0) return range;
    std::uint32_t lo = 0xFFFFu, hi = 0;
    auto see = [&](std::uint32_t index) {
        if (index < lo) lo = index;
        if (index > hi) hi = index;
    };
    if (fmt == kC4) {
        for (std::size_t i = 0; i < bytes; ++i) { see(base[i] & 0xFu); see(base[i] >> 4); }
    } else if (fmt == kC8) {
        for (std::size_t i = 0; i < bytes; ++i) see(base[i]);
    } else {
        for (std::size_t i = 0; i + 2 <= bytes; i += 2) see(((std::uint32_t(base[i]) << 8) | base[i + 1]) & 0x3FFFu);
    }
    range.paletted = true;
    range.first = lo;
    range.count = hi + 1 - lo;
    return range;
}

// Dolphin's name without extension, e.g. "tex1_64x32_m_0123456789abcdef_14".
inline std::string name(std::uint32_t w, std::uint32_t h, bool mipmapped, std::uint64_t tex_hash, bool paletted,
                        std::uint64_t tlut_hash, std::uint32_t fmt) {
    char text[96];
    if (paletted)
        std::snprintf(text, sizeof text, "tex1_%ux%u%s_%016llx_%016llx_%u", w, h, mipmapped ? "_m" : "",
                      static_cast<unsigned long long>(tex_hash), static_cast<unsigned long long>(tlut_hash), fmt);
    else
        std::snprintf(text, sizeof text, "tex1_%ux%u%s_%016llx_%u", w, h, mipmapped ? "_m" : "",
                      static_cast<unsigned long long>(tex_hash), fmt);
    return text;
}

struct ParsedName {
    std::uint32_t width = 0, height = 0, format = 0;
    bool mipmapped = false;     // "_m"
    bool any_texture = false;   // texture hash "$"
    bool has_tlut = false;
    bool any_tlut = false;      // TLUT hash "$"
    bool arbitrary_mips = false; // "_arb"
    std::uint64_t tex_hash = 0, tlut_hash = 0;
    std::uint32_t mip_level = 0; // N of a "_mip<N>" sidecar, 0 for a base file
};

namespace detail {
inline bool iends_with(std::string_view s, std::string_view suffix) {
    if (s.size() < suffix.size()) return false;
    for (std::size_t i = 0; i < suffix.size(); ++i) {
        char c = s[s.size() - suffix.size() + i];
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
        if (c != suffix[i]) return false;
    }
    return true;
}
inline bool dec(std::string_view s, std::uint32_t &out) {
    if (s.empty() || s.size() > 9) return false;
    std::uint32_t v = 0;
    for (char c : s) {
        if (c < '0' || c > '9') return false;
        v = v * 10u + static_cast<std::uint32_t>(c - '0');
    }
    out = v;
    return true;
}
inline bool hex(std::string_view s, std::uint64_t &out) {
    if (s.empty() || s.size() > 16) return false;
    std::uint64_t v = 0;
    for (char c : s) {
        v <<= 4;
        if (c >= '0' && c <= '9') v |= static_cast<std::uint64_t>(c - '0');
        else if (c >= 'a' && c <= 'f') v |= static_cast<std::uint64_t>(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= static_cast<std::uint64_t>(c - 'A' + 10);
        else return false;
    }
    out = v;
    return true;
}
} // namespace detail

// Parses a pack file name (.png / .dds, any case). nullopt for anything else.
inline std::optional<ParsedName> parse(std::string_view filename) {
    std::string_view stem;
    if (detail::iends_with(filename, ".png") || detail::iends_with(filename, ".dds"))
        stem = filename.substr(0, filename.size() - 4);
    else
        return std::nullopt;
    if (stem.substr(0, 5) != "tex1_") return std::nullopt;
    std::vector<std::string_view> parts;
    for (std::size_t pos = 0;;) {
        const std::size_t next = stem.find('_', pos);
        parts.push_back(stem.substr(pos, next == std::string_view::npos ? std::string_view::npos : next - pos));
        if (next == std::string_view::npos) break;
        pos = next + 1;
    }
    ParsedName out;
    // Trailing "_mip<N>" (sidecar level) and "_arb" (arbitrary mipmaps).
    if (parts.size() > 1 && parts.back().size() > 3 && parts.back().substr(0, 3) == "mip") {
        if (!detail::dec(parts.back().substr(3), out.mip_level) || out.mip_level == 0) return std::nullopt;
        parts.pop_back();
    }
    if (parts.size() > 1 && parts.back() == "arb") {
        out.arbitrary_mips = true;
        parts.pop_back();
    }
    // tex1, WxH, [m], texhash, [tluthash], fmt
    if (parts.size() < 4 || parts.size() > 6) return std::nullopt;
    const std::string_view dims = parts[1];
    const std::size_t x = dims.find('x');
    if (x == std::string_view::npos || !detail::dec(dims.substr(0, x), out.width) ||
        !detail::dec(dims.substr(x + 1), out.height) || out.width == 0 || out.height == 0)
        return std::nullopt;
    std::size_t i = 2;
    if (parts[i] == "m") {
        out.mipmapped = true;
        ++i;
    }
    const std::size_t rest = parts.size() - i;
    if (rest != 2 && rest != 3) return std::nullopt;
    if (parts[i] == "$") out.any_texture = true;
    else if (!detail::hex(parts[i], out.tex_hash)) return std::nullopt;
    if (rest == 3) {
        out.has_tlut = true;
        if (parts[i + 1] == "$") out.any_tlut = true;
        else if (!detail::hex(parts[i + 1], out.tlut_hash)) return std::nullopt;
    }
    if (!detail::dec(parts.back(), out.format)) return std::nullopt;
    return out;
}

// A minimal RGBA8 PNG (stored deflate blocks, no compression) for texture
// dumps: every PNG reader and editor loads it; recompress it for a pack.
namespace detail {
inline std::uint32_t crc32(const std::uint8_t *data, std::size_t size, std::uint32_t crc = 0) {
    crc = ~crc;
    for (std::size_t i = 0; i < size; ++i) {
        crc ^= data[i];
        for (int k = 0; k < 8; ++k) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
inline void put_be32(std::vector<std::uint8_t> &out, std::uint32_t v) {
    out.push_back(static_cast<std::uint8_t>(v >> 24));
    out.push_back(static_cast<std::uint8_t>(v >> 16));
    out.push_back(static_cast<std::uint8_t>(v >> 8));
    out.push_back(static_cast<std::uint8_t>(v));
}
inline void chunk(std::vector<std::uint8_t> &png, const char type[4], const std::vector<std::uint8_t> &body) {
    put_be32(png, static_cast<std::uint32_t>(body.size()));
    const std::size_t start = png.size();
    png.insert(png.end(), type, type + 4);
    png.insert(png.end(), body.begin(), body.end());
    put_be32(png, crc32(png.data() + start, png.size() - start));
}
} // namespace detail

inline std::vector<std::uint8_t> encode_png_rgba8(std::uint32_t w, std::uint32_t h, const std::uint8_t *rgba) {
    std::vector<std::uint8_t> png;
    if (w == 0 || h == 0 || rgba == nullptr) return png;
    const std::size_t row = std::size_t(w) * 4u;
    std::vector<std::uint8_t> raw;
    raw.reserve((row + 1u) * h);
    for (std::uint32_t y = 0; y < h; ++y) {
        raw.push_back(0); // filter: none
        raw.insert(raw.end(), rgba + y * row, rgba + (y + 1) * row);
    }
    std::vector<std::uint8_t> z = {0x78, 0x01};
    std::uint32_t a = 1, b = 0; // Adler-32
    for (std::uint8_t v : raw) {
        a = (a + v) % 65521u;
        b = (b + a) % 65521u;
    }
    for (std::size_t pos = 0; pos < raw.size();) {
        const std::size_t n = std::min<std::size_t>(raw.size() - pos, 65535u);
        z.push_back(pos + n == raw.size() ? 1 : 0);
        z.push_back(static_cast<std::uint8_t>(n));
        z.push_back(static_cast<std::uint8_t>(n >> 8));
        z.push_back(static_cast<std::uint8_t>(~n));
        z.push_back(static_cast<std::uint8_t>(~n >> 8));
        z.insert(z.end(), raw.begin() + static_cast<std::ptrdiff_t>(pos), raw.begin() + static_cast<std::ptrdiff_t>(pos + n));
        pos += n;
    }
    detail::put_be32(z, (b << 16) | a);
    static const std::uint8_t kSignature[8] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
    png.assign(kSignature, kSignature + 8);
    std::vector<std::uint8_t> ihdr;
    detail::put_be32(ihdr, w);
    detail::put_be32(ihdr, h);
    ihdr.insert(ihdr.end(), {8u, 6u, 0u, 0u, 0u}); // 8-bit RGBA
    detail::chunk(png, "IHDR", ihdr);
    detail::chunk(png, "IDAT", z);
    detail::chunk(png, "IEND", {});
    return png;
}

} // namespace openspm_texpack

#endif
