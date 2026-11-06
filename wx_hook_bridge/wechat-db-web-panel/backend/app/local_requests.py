from fastapi import HTTPException, Request


def require_local(request: Request):
    allowed = {f'http://{host}:{port}' for host in ('localhost', '127.0.0.1')
               for port in (5173, 5174, 4173, 8765)}
    if not request.client or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
        raise HTTPException(403, '仅允许本机访问')
    if request.headers.get('origin', '').rstrip('/') not in allowed | {''}:
        raise HTTPException(403, '拒绝外部页面操作本机配置')
