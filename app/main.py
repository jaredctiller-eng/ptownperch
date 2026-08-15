from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.bootstrap import upsert_configured_property
from app.config import settings
from app.db import SessionLocal, engine
from app.models import Base
from app.routes import health, ical
from app.sync.run import sync_all_properties


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await upsert_configured_property(session)
    scheduler = AsyncIOScheduler()
    if settings.sync_interval_minutes > 0:
        scheduler.add_job(
            sync_all_properties, "interval", minutes=settings.sync_interval_minutes
        )
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Ptown Perch Ops", lifespan=lifespan)
app.include_router(health.router)
app.include_router(ical.router)
