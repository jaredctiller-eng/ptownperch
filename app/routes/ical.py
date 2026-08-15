from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.ical.generate import build_calendar
from app.models import DIRECT_BLOCKING_STATUSES, CalendarBlock, Property, Reservation

router = APIRouter()

ANCHOR_START = date(2020, 1, 1)
ANCHOR_END = date(2020, 1, 2)


@router.get("/ical/{slug}.ics")
async def property_ical(slug: str, session: AsyncSession = Depends(get_session)):
    prop = (
        await session.execute(select(Property).where(Property.slug == slug))
    ).scalar_one_or_none()
    if prop is None:
        raise HTTPException(status_code=404, detail="unknown property")

    reservations = (
        (
            await session.execute(
                select(Reservation)
                .where(
                    Reservation.property_id == prop.id,
                    Reservation.source == "direct",
                    Reservation.status.in_(DIRECT_BLOCKING_STATUSES),
                )
                .order_by(Reservation.check_in)
            )
        )
        .scalars()
        .all()
    )
    blocks = (
        (
            await session.execute(
                select(CalendarBlock)
                .where(CalendarBlock.property_id == prop.id)
                .order_by(CalendarBlock.start_date)
            )
        )
        .scalars()
        .all()
    )
    entries = [(f"res-{r.id}@ptownperch", r.check_in, r.check_out) for r in reservations]
    entries += [(f"block-{b.id}@ptownperch", b.start_date, b.end_date) for b in blocks]
    if not entries:
        # Airbnb's calendar importer rejects a feed containing no events, which
        # is exactly the state of a property with no direct bookings yet. Publish
        # a long-past anchor event so the feed is never empty; past dates block
        # nothing on any consumer's calendar.
        entries = [(f"anchor-{prop.id}@ptownperch", ANCHOR_START, ANCHOR_END)]
    return Response(content=build_calendar(entries), media_type="text/calendar")
