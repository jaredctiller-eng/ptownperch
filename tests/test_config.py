from app.config import Settings


def test_sqlalchemy_url_passthrough_sqlite():
    s = Settings(database_url="sqlite+aiosqlite:///./dev.db", _env_file=None)
    assert s.sqlalchemy_url == "sqlite+aiosqlite:///./dev.db"


def test_sqlalchemy_url_rewrites_postgresql():
    s = Settings(database_url="postgresql://u:p@host:5432/db", _env_file=None)
    assert s.sqlalchemy_url == "postgresql+asyncpg://u:p@host:5432/db"


def test_sqlalchemy_url_rewrites_short_postgres_scheme():
    s = Settings(database_url="postgres://u:p@host:5432/db", _env_file=None)
    assert s.sqlalchemy_url == "postgresql+asyncpg://u:p@host:5432/db"
