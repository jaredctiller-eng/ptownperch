from datetime import date

from sqlalchemy import select

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
