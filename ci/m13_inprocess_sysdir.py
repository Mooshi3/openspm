from pathlib import Path

p = Path('ModernGekko/vendor/dolphin/Source/Core/Common/FileUtil.cpp')
s = p.read_text()
anchor = '''#elif defined(_WIN32) || defined(LINUX_LOCAL_DEV)\n  const std::string sys_directory = GetExeDirectory() + DIR_SEP SYSDATA_DIR DIR_SEP;\n'''
replacement = '''#elif defined(_WIN32)\n  std::wstring openspm_sys_buffer(32768, L'\\0');\n  const DWORD openspm_sys_size = GetEnvironmentVariableW(\n      L"OPENSPM_SYS_DIR", openspm_sys_buffer.data(),\n      static_cast<DWORD>(openspm_sys_buffer.size()));\n  const std::string openspm_sys_directory =\n      openspm_sys_size > 0 && openspm_sys_size < openspm_sys_buffer.size()\n          ? WStringToUTF8(std::wstring(openspm_sys_buffer.data(), openspm_sys_size))\n          : std::string{};\n  const std::string sys_directory =\n      !openspm_sys_directory.empty()\n          ? openspm_sys_directory + DIR_SEP\n          : GetExeDirectory() + DIR_SEP SYSDATA_DIR DIR_SEP;\n#elif defined(LINUX_LOCAL_DEV)\n  const char* openspm_sys_dir = std::getenv("OPENSPM_SYS_DIR");\n  const std::string sys_directory =\n      openspm_sys_dir && *openspm_sys_dir\n          ? std::string(openspm_sys_dir) + DIR_SEP\n          : GetExeDirectory() + DIR_SEP SYSDATA_DIR DIR_SEP;\n'''
assert s.count(anchor) == 1, 'expected RecompCore Sys directory anchor exactly once'
s = s.replace(anchor, replacement, 1)
p.write_text(s)
print('patched RecompCore Sys directory resolution for in-process OpenSPM backend')
