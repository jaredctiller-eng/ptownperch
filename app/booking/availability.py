from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DIRECT_BLOCKING_STATUSES, Reservation
from app.sync.engine import _overlaps


async def blocking_reservations(session: AsyncSession, property_id: int) -> list[Reservation]:
    return (
        (
            await session.execute(
                select(Reservation).where(
                    Reservation.property_id == property_id,
                    (
                        (Reservation.source == "direct")
                        & Reservation.status.in_(DIRECT_BLOCKING_STATUSES)
                    )
                    | ((Reservation.source == "airbnb_ical") & (Reservation.status != "canceled")),
                )
            )
        )
        .scalars()
        .all()
    )


async def assert_no_overlap(
    session: AsyncSession, property_id: int, check_in: date, check_out: date
) -> None:
    for existing in await blocking_reservations(session, property_id):
        if _overlaps(check_in, check_out, existing.check_in, existing.check_out):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"overlaps existing {existing.source} reservation "
                    f"#{existing.id} ({existing.check_in} → {existing.check_out})"
                ),
            )
