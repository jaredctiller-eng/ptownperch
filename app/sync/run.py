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
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # The Airbnb iCal URL carries a secret token in its query string;
            # httpx's default exception message embeds the full URL, and this
            # gets written to Railway logs on every 429/5xx. Log only the
            # sanitized host+path, and raise a sanitized exception rather
            # than re-raising exc directly: sync_all_properties's outer
            # `except Exception: logger.exception(...)` would otherwise
            # print the original exception's message (with the raw URL) via
            # exc_info. `from None` suppresses the implicit __context__
            # chain so the original exception's URL-bearing message never
            # reaches a traceback either.
            safe_url = urlsplit(url)._replace(query="").geturl()
            logger.error("ical fetch failed (%s): %s", exc.response.status_code, safe_url)
            raise RuntimeError(
                f"ical fetch failed ({exc.response.status_code}): {safe_url}"
            ) from None
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
