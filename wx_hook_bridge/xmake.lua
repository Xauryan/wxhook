-- wx_hook_bridge xmake build script

set_xmakever("2.9.1")
set_project("wx_hook_bridge")

set_languages("c11")
set_runtimes("MT")
add_defines("WIN32_LEAN_AND_MEAN", "_CRT_SECURE_NO_WARNINGS")

if is_plat("windows") then
    set_toolchains("msvc")
    -- Source files contain UTF-8 Chinese text; prevent MSVC from using CP936.
    add_cflags("-Wall", "-O2", "/utf-8")
    add_ldflags("-nologo", "-DLL")
end

target("wx_hook_bridge")
    set_kind("shared")
    set_targetdir("dist")
    set_basename("wx_hook_bridge")
    add_includedirs("native/wx_hook_bridge/src/include", "native/wx_hook_bridge/src/modules", "native/wx_hook_bridge/src/third_party")
    add_files("native/wx_hook_bridge/src/wx_hook_bridge.c", "native/wx_hook_bridge/src/modules/*.c", "native/wx_hook_bridge/src/third_party/mongoose.c")
    if is_plat("windows") then
        add_links("kernel32", "user32", "winhttp", "ws2_32")
    end
