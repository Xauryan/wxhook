"""Initialize one owner through an operator terminal, never through a public route."""
import getpass

from .auth import Auth
from .config import Settings
from .db import Database


if __name__ == '__main__':
    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.initialize(settings)
    login = input('Owner login: ').strip()
    name = input('Display name: ').strip()
    password = getpass.getpass('Password (12+ characters): ')
    if password != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords do not match')
    Auth(database, settings).bootstrap(login, name, password)
    print('Owner initialized. Use the owner entry on the task page.')
