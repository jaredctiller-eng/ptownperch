from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    slack_webhook_url: str = ""
    sync_interval_minutes: int = 10

    @property
    def sqlalchemy_url(self) -> str:
        # Railway provides postgresql://; SQLAlchemy async needs the asyncpg driver.
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
