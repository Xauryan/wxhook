# Weixin FTS Tokenizer

SQLite loadable extension for the original `MMFtsTokenizer` implementation in
Windows Weixin `4.1.9.23`. The extension loads the matching `Weixin.dll`, checks
the callback signatures, and registers the native tokenizer through the FTS5
API.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File native\weixin_fts_tokenizer\build.ps1
```

The build downloads the SQLite public extension headers into the ignored local
`build/` directory and writes `dist/weixin_fts_tokenizer.dll`.

MSVC is located through the Visual Studio Installer's `vswhere.exe`, including
Build Tools installations under `Program Files (x86)`. The existing Visual
Studio 2022 paths remain fallbacks.

If the search page reports `message_fts`, `contact_fts`, and `favorite_fts`
errors containing the extension path, check that this DLL exists and run the
build command above from `wx_hook_bridge`. Each search connection loads the
extension separately, so restoring a missing DLL does not require regenerating
the database snapshot. Confirm that `/api/search` returns `fts_used: true` and
an empty `errors` array; ordinary-query results alone do not verify FTS.

Set `WEIXIN_DLL_PATH` when Weixin is installed elsewhere:

```powershell
$env:WEIXIN_DLL_PATH = 'D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll'
```

## Python

```python
import sqlite3

connection = sqlite3.connect('runtime/db_decrypt/all/message/message_fts.db')
connection.enable_load_extension(True)
connection.load_extension('dist/weixin_fts_tokenizer.dll')

rows = connection.execute(
    "SELECT rowid, acontent FROM message_fts_v4_1 "
    "WHERE message_fts_v4_1 MATCH ? LIMIT 20",
    ('微信',),
).fetchall()
```

This extension is version-bound. It rejects a `Weixin.dll` whose callback byte
signatures do not match `4.1.9.23`.
