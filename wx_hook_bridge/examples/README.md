# examples/

可复制运行的示例。

| 文件 | 用途 |
|------|------|
| `send_text.ps1` | 写 outbox 发文本，并等待 `result_*.json` |
| `send_text_line.txt` | outbox 单行格式说明（勿整文件拷进 next.txt） |

完整步骤见 [docs/quick-start.md](../docs/quick-start.md)。

```powershell
# 仓库根目录，且 inject 已运行
powershell -ExecutionPolicy Bypass -File examples\send_text.ps1 -To filehelper -Content "hello"
```
