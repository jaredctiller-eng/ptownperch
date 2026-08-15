import asyncio
import logging

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.ical.parse import parse_airbnb_ical
from app.models import Property
from app.slack.notify import format_sync_messages, post_slack
from app.sync.engine import apply_airbnb_events

logger = logging.getLogger(__name__)


async def fetch_ical(url: str) -> str:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def sync_property(session, prop: Property) -> None:
    text = await fetch_ical(prop.airbnb_ical_url)
    events = parse_airbnb_ical(text)
    result = await apply_airbnb_events(session, prop.id, events)
    await session.commit()
    for msg in format_sync_messages(prop.name, result):
        await post_slack(msg)


async def sync_all_properties() -> None:
    async with SessionLocal() as session:
        props = (
            (await session.execute(select(Property).where(Property.airbnb_ical_url != "")))
            .scalars()
            .all()
        )
    for prop in props:
        try:
            async with SessionLocal() as session:
                await sync_property(session, prop)
        except Exception:
            logger.exception("sync failed for property %s", prop.slug)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(sync_all_properties())
