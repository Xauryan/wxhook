"""One-off report computation, isolated from the realtime API/native bridge."""
import json
from pathlib import Path
import sys

from .analysis_snapshots import atomic_json
from .archive_insights import ArchiveInsights
from .config import Settings
from .repository import WeChatRepository


def main(path):
    path = Path(path)
    request = json.loads(path.read_text(encoding='utf-8'))
    paths = {'panel_root','repository_root','database_root','report_path','cache_path','fts_extension','weixin_dll'}
    settings = Settings(**{k:Path(v) if k in paths else v for k,v in request['settings'].items()})
    service = ArchiveInsights(WeChatRepository(settings))
    identity = request['identity']
    service.check(identity['account'],identity['scope'])
    method = request['method']
    if method not in {'report','catalog'}:
        raise ValueError('unsupported local report')
    result = getattr(service,method)(**request['arguments'])
    service.check(identity['account'],identity['scope'])
    output = path.with_name(path.name.replace('.request.json','.result.json'))
    atomic_json(output,result)


if __name__ == '__main__':
    try:
        main(sys.argv[1])
    except Exception:
        # No chat content, local paths or native exception text in process logs.
        sys.exit(1)
