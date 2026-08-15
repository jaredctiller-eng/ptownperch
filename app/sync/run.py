import asyncio
import logging
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.ical.parse import parse_airbnb_ical
from app.models import Property
from app.slack.notify import format_sync_messages, post_slack
from app.sync.engine import apply_airbnb_events

logger = logging.getLogger(__name__)


async def fetch_ical(url: str) -> str:
    # The Airbnb iCal URL carries a secret token in its query string. We check
    # resp.is_error directly instead of resp.raise_for_status(): the latter
    # constructs an httpx.HTTPStatusError whose message embeds the full URL,
    # and that URL is reachable via the exception's __context__ even from a
    # `raise ... from None` wrapper (from None only suppresses the *default*
    # traceback printer, it doesn't clear __context__) — so anything that
    # walks the exception chain directly (e.g. an error-tracking SDK) could
    # still leak it. Never constructing that exception avoids the class of bug.
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.is_error:
            safe_url = urlsplit(url)._replace(query="").geturl()
            logger.error("ical fetch failed (%s): %s", resp.status_code, safe_url)
            raise RuntimeError(f"ical fetch failed ({resp.status_code}): {safe_url}")
        return resp.text


async def sync_property(session: AsyncSession, prop: Property) -> None:
    text = await fetch_ical(prop.airbnb_ical_url)
    events = parse_airbnb_ical(text)
    result = await apply_airbnb_events(session, prop.id, events)
    await session.commit()
    for msg in format_sync_messages(prop.name, result):
        try:
            await post_slack(msg)
        except Exception:
            logger.exception("slack post failed for property %s", prop.slug)


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
