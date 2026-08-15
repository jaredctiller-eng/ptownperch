from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    slack_webhook_url: str = ""
    sync_interval_minutes: int = 10

    # Declarative single-property config. When airbnb_ical_url is set, the app
    # upserts this property on startup, so a deploy needs no manual seeding and
    # the Airbnb token stays in the secret store. Local dev can still use
    # scripts/seed_property.py.
    property_slug: str = "perch"
    property_name: str = "Ptown Perch"
    airbnb_ical_url: str = ""

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
