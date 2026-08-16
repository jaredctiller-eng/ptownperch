from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyRate


async def resolve_nightly_rates(
    session: AsyncSession,
    property_id: int,
    check_in: date,
    check_out: date,
    default_rate_cents: int,
) -> list[int]:
    """One entry per night of the stay, in order. Uses the explicit
    DailyRate for a night if one exists, else default_rate_cents."""
    nights = [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]
    rows = (
        await session.execute(
            select(DailyRate).where(
                DailyRate.property_id == property_id,
                DailyRate.date >= check_in,
                DailyRate.date < check_out,
            )
        )
    ).scalars().all()
    by_date = {r.date: r.rate_cents for r in rows}
    return [by_date.get(night, default_rate_cents) for night in nights]
