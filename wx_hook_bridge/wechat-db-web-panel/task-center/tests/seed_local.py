"""Provision only an isolated local test owner; normal app and auth routes are used."""
from urllib.parse import urlsplit

from sqlalchemy import select

from app.auth import Auth
from app.config import Settings
from app.db import Database, Project

if __name__ == '__main__':
    settings = Settings.from_env()
    url = urlsplit(settings.database_url)
    assert url.hostname in ('127.0.0.1','localhost') and url.port == 55439
    assert url.path == '/task_center_browser_test' and settings.local_http
    database = Database(settings.database_url)
    database.initialize(settings)
    with database.transaction() as db:
        owner = db.scalar(select(Project.owner_user_id))
    if owner is None:
        Auth(database,settings).bootstrap('owner','项目所有者','Local-test-only-1234')
    print('Local owner ready. This fixture database is not for real accounts.')
