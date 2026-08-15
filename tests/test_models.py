from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import CalendarBlock, Property, Reservation


async def test_property_reservation_roundtrip(session, prop):
    session.add(
        Reservation(
            property_id=prop.id,
            source="airbnb_ical",
            status="confirmed",
            external_uid="abc123@airbnb.com",
            check_in=date(2026, 9, 1),
            check_out=date(2026, 9, 5),
        )
    )
    session.add(
        CalendarBlock(
            property_id=prop.id,
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 3),
            reason="maintenance",
        )
    )
    await session.flush()

    res = (await session.execute(select(Reservation))).scalar_one()
    assert res.external_uid == "abc123@airbnb.com"
    assert res.check_out == date(2026, 9, 5)
    block = (await session.execute(select(CalendarBlock))).scalar_one()
    assert block.reason == "maintenance"
    loaded_prop = (await session.execute(select(Property))).scalar_one()
    assert loaded_prop.slug == "perch"


async def test_duplicate_airbnb_uid_rejected(session, prop):
    for _ in range(2):
        session.add(
            Reservation(
                property_id=prop.id, source="airbnb_ical", status="confirmed",
                external_uid="dup@airbnb.com",
                check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
            )
        )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_multiple_direct_bookings_without_uid_allowed(session, prop):
    for start in (1, 10):
        session.add(
            Reservation(
                property_id=prop.id, source="direct", status="confirmed",
                check_in=date(2026, 9, start), check_out=date(2026, 9, start + 2),
            )
        )
    await session.flush()


async def test_inverted_date_range_rejected(session, prop):
    session.add(
        Reservation(
            property_id=prop.id, source="direct", status="hold",
            check_in=date(2026, 9, 5), check_out=date(2026, 9, 1),
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
