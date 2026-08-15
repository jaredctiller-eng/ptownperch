from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    slack_webhook_url: str = ""
    sync_interval_minutes: int = 10

    @property
    def sqlalchemy_url(self) -> str:
        # Railway/Heroku provide postgresql:// or postgres://; async SQLAlchemy
        # needs the asyncpg driver spelled out.
        url = self.database_url
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url.removeprefix(prefix)
        return url


settings = Settings()
