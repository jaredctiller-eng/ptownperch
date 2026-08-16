import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.booking.hold_expiry import expire_stale_holds_all_properties
from app.bootstrap import upsert_configured_property
from app.config import settings
from app.db import SessionLocal
from app.routes import admin, booking_public, dashboard, health, ical, slack_interactive
from app.sync.run import sync_all_properties

# httpx logs each request at INFO, including the full URL — and the Airbnb
# iCal / Slack webhook URLs both carry secret tokens. Pin this explicitly
# rather than relying on nobody ever raising the app's log level or adding
# --log-level to the Procfile.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with SessionLocal() as session:
        await upsert_configured_property(session)
    scheduler = AsyncIOScheduler()
    if settings.sync_interval_minutes > 0:
        scheduler.add_job(
            sync_all_properties,
            "interval",
            minutes=settings.sync_interval_minutes,
            # Run once at boot rather than waiting a full interval: a redeploy
            # would otherwise reset the clock and could postpone syncing forever.
            next_run_time=datetime.now(UTC),
        )
    scheduler.add_job(
        expire_stale_holds_all_properties, "interval", minutes=15,
        next_run_time=datetime.now(UTC),
    )
    scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Ptown Perch Ops", lifespan=lifespan)
app.include_router(health.router)
app.include_router(ical.router)
app.include_router(admin.router)
app.include_router(booking_public.router)
app.include_router(slack_interactive.router)
app.include_router(dashboard.router)
