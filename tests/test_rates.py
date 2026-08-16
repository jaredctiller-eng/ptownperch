from datetime import date

from app.booking.rates import resolve_nightly_rates
from app.models import DailyRate


async def test_all_nights_covered_by_explicit_rates(session, prop):
    session.add_all(
        [
            DailyRate(property_id=prop.id, date=date(2026, 9, 1), rate_cents=45000),
            DailyRate(property_id=prop.id, date=date(2026, 9, 2), rate_cents=99400),
            DailyRate(property_id=prop.id, date=date(2026, 9, 3), rate_cents=99400),
        ]
    )
    await session.flush()

    rates = await resolve_nightly_rates(
        session, prop.id, date(2026, 9, 1), date(2026, 9, 4), default_rate_cents=50000
    )
    assert rates == [45000, 99400, 99400]


async def test_missing_nights_fall_back_to_default(session, prop):
    session.add(DailyRate(property_id=prop.id, date=date(2026, 9, 2), rate_cents=99400))
    await session.flush()

    rates = await resolve_nightly_rates(
        session, prop.id, date(2026, 9, 1), date(2026, 9, 4), default_rate_cents=50000
    )
    assert rates == [50000, 99400, 50000]


async def test_no_explicit_rates_uses_default_for_every_night(session, prop):
    rates = await resolve_nightly_rates(
        session, prop.id, date(2026, 9, 1), date(2026, 9, 4), default_rate_cents=50000
    )
    assert rates == [50000, 50000, 50000]


async def test_rates_outside_stay_range_excluded(session, prop):
    # one rate the day before check-in, one on the (exclusive) checkout day —
    # neither should appear in the result.
    session.add_all(
        [
            DailyRate(property_id=prop.id, date=date(2026, 8, 31), rate_cents=11111),
            DailyRate(property_id=prop.id, date=date(2026, 9, 1), rate_cents=45000),
            DailyRate(property_id=prop.id, date=date(2026, 9, 4), rate_cents=22222),
        ]
    )
    await session.flush()

    rates = await resolve_nightly_rates(
        session, prop.id, date(2026, 9, 1), date(2026, 9, 4), default_rate_cents=50000
    )
    assert rates == [45000, 50000, 50000]
