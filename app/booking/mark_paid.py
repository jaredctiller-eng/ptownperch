from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reservation


class MarkPaidError(Exception):
    pass


async def mark_paid(session: AsyncSession, reservation: Reservation) -> None:
    if reservation.status != "hold":
        raise MarkPaidError("reservation is not in hold status")
    if reservation.signed_at is None:
        raise MarkPaidError("reservation has no signed contract on file")
    reservation.status = "confirmed"
    await session.commit()
