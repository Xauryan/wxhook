from collections import OrderedDict, deque
from threading import Lock
from time import monotonic

from starlette.responses import JSONResponse

from .errors import require


class Limiter:
    """Single-process admission guard; bounded keys and no stored IPs in logs."""
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = Lock()

    def check(self, key, count=60, window=60):
        with self.lock:
            current = monotonic()
            queue = self.entries.setdefault(key, deque())
            while queue and queue[0] <= current - window:
                queue.popleft()
            require(len(queue) < count, 'rate_limited', '操作过于频繁，请稍后再试。', 429)
            queue.append(current)
            self.entries.move_to_end(key)
            while len(self.entries) > 10000:
                self.entries.popitem(last=False)


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PATCH', 'PUT'):
            return await self.app(scope, receive, send)
        limit = 128 * 1024
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > limit:
                return await JSONResponse({'code': 'body_too_large', 'message': '请求内容过大。'}, status_code=413)(scope, receive, send)
            chunks.append(chunk)
            if not message.get('more_body'):
                break
        delivered = False

        async def limited_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()

        return await self.app(scope, limited_receive, send)
