from datetime import UTC, date, datetime

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


async def test_property_booking_config_defaults(session):
    prop = Property(slug="x", name="X")
    session.add(prop)
    await session.flush()
    assert prop.nightly_rate_cents is None
    assert prop.direct_booking_tax_enabled is False
    assert prop.direct_booking_tax_rate_percent == 14.45
    assert prop.contract_template is None


async def test_reservation_phase2_fields_roundtrip(session, prop):
    res = Reservation(
        property_id=prop.id,
        source="direct",
        status="hold",
        check_in=date(2026, 9, 1),
        check_out=date(2026, 9, 5),
        booking_token="tok123",
        guest_home_address="1 Main St",
        occupant_names="Alice, Bob",
        signed_name="Alice Guest",
        signature_image="data:image/png;base64,abc",
        signed_at=datetime(2026, 8, 15, tzinfo=UTC),
        signed_ip="1.2.3.4",
        contract_snapshot="rendered text",
    )
    session.add(res)
    await session.flush()
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.booking_token == "tok123"
    assert stored.signed_at == datetime(2026, 8, 15, tzinfo=UTC)
    assert stored.created_at is not None
    assert stored.hold_warned_at is None


async def test_booking_token_unique_but_multiple_nulls_allowed(session, prop):
    session.add_all(
        [
            Reservation(property_id=prop.id, source="direct", status="hold",
                        check_in=date(2026, 9, 1), check_out=date(2026, 9, 3),
                        booking_token=None),
            Reservation(property_id=prop.id, source="direct", status="hold",
                        check_in=date(2026, 10, 1), check_out=date(2026, 10, 3),
                        booking_token=None),
        ]
    )
    await session.flush()  # two NULL tokens must coexist
