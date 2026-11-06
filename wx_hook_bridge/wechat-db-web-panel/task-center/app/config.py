from dataclasses import dataclass
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str
    public_url: str
    title: str = '协作任务中心'
    local_http: bool = False

    def __post_init__(self):
        url = urlsplit(self.public_url)
        if not url.hostname or url.path not in ('', '/') or url.query or url.fragment or url.username:
            raise ValueError('TASK_CENTER_PUBLIC_URL must be an origin')
        if url.scheme != 'https' and not (self.local_http and url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1')):
            raise ValueError('HTTPS required except explicitly configured loopback development')
        if not self.database_url.startswith('postgresql+psycopg://'):
            raise ValueError('A dedicated PostgreSQL database is required')

    @property
    def origin(self):
        return self.public_url.rstrip('/')

    @property
    def cookie(self):
        return 'tc_session' if self.local_http else '__Host-tc_session'

    def local_client_origin(self, origin):
        if not isinstance(origin, str):
            return False
        try:
            url = urlsplit(origin)
            return (url.scheme in ('http', 'https') and
                    url.hostname in ('localhost', '127.0.0.1', '::1') and
                    url.username is None and url.password is None and
                    origin == f'{url.scheme}://{url.netloc}' and
                    (url.port is None or 0 < url.port <= 65535))
        except ValueError:
            return False

    def allows_origin(self, origin):
        return origin == self.origin or (not self.local_http and self.local_client_origin(origin))

    def session_cookie(self, origin):
        if not self.local_http and self.local_client_origin(origin):
            return {'key': 'tc_remote_session', 'path': '/api/task-center',
                    'secure': origin.startswith('https://')}
        return {'key': self.cookie, 'path': '/', 'secure': not self.local_http}

    @classmethod
    def from_env(cls):
        return cls(database_url=os.environ['TASK_CENTER_DATABASE_URL'],
                   public_url=os.environ['TASK_CENTER_PUBLIC_URL'],
                   title=os.getenv('TASK_CENTER_TITLE', '协作任务中心'),
                   local_http=os.getenv('TASK_CENTER_LOCAL_HTTP') == '1')
