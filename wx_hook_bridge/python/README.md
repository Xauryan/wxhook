# python/ — Python 层

Python 辅助层，按职责分子目录。  
功能列表见 [docs/features.md](../docs/features.md) 第八章。

```text
python/
├── inject/     进程、注入、更新锁定
├── message/    消息规范化与载荷
└── tools/      可直接运行的 CLI
```

| 目录 | 主要能力 |
|------|----------|
| [inject/](inject/) | 找微信进程、编译 DLL、远程注入 |
| [message/](message/) | 规范化消息、引用 XML、Finder 载荷 |
| [tools/](tools/) | 文件 outbox / HTTP 收发 CLI |

```powershell
# 仓库根目录
python python\tools\wx_message_bridge.py --help
```
