from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.ical.generate import build_calendar
from app.models import CalendarBlock, Property, Reservation

router = APIRouter()

BLOCKING_STATUSES = ("hold", "confirmed")


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
                    Reservation.status.in_(BLOCKING_STATUSES),
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
    return Response(content=build_calendar(entries), media_type="text/calendar")
