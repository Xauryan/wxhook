# native/ — 原生 C 层

原生 C 工程与编译脚本，默认产物输出到仓库根 `dist/`。

- `wx_hook_bridge/`：微信 Hook 与消息桥 DLL
- `weixin_fts_tokenizer/`：复用微信 `MMFtsTokenizer` 的 SQLite FTS5 扩展

- 功能说明：[docs/features.md](../docs/features.md)
- 模块与构建：[docs/native-architecture.md](../docs/native-architecture.md)
- 本地实时消息传输：[docs/realtime-transport.md](../docs/realtime-transport.md)

**推荐新编译脚本**（xmake）：

```lua
-- xmake.lua （放在仓库根目录）
set_xmakever("2.9.1")
set_project("wx_hook_bridge")

set_kind("shared")
set_languages("c11")
set_runtimes("MT")
add_defines("WIN32_LEAN_AND_MEAN", "_CRT_SECURE_NO_WARNINGS")

if is_host("windows") then
    set_toolchains("msvc")
    add_cflags("-Wall", "-O2")
    add_ldflags("-nologo", "-DLL")
end

add_includedirs({
    "./wx_hook_bridge/native/wx_hook_bridge/src/include",
    "./wx_hook_bridge/native/wx_hook_bridge/src/modules",
    "./wx_hook_bridge/native/wx_hook_bridge/src/third_party"
})

add_files({
    "./wx_hook_bridge/native/wx_hook_bridge/src/wx_hook_bridge.c",
    "./wx_hook_bridge/native/wx_hook_bridge/src/modules/*.c",
    "./wx_hook_bridge/native/wx_hook_bridge/src/third_party/mongoose.c"
})

target("wx_hook_bridge")
    set_kind("shared")
    set_outputdir("dist")
    set_basename("wx_hook_bridge")
end

build_target("wx_hook_bridge")
```

使用方法：
```powershell
xmake          # 首次运行会生成 xmake.exe
xmake build    # 默认输出到 dist\wx_hook_bridge.dll
xmake -r       # 清除缓存
```

旧的 PowerShell build.ps1 已保留备用。
