from datetime import date

import pytest
from fastapi import HTTPException

from app.booking.availability import assert_no_overlap, blocking_reservations
from app.models import Reservation


async def test_blocking_reservations_excludes_inquiry_and_canceled(session, prop):
    session.add_all(
        [
            Reservation(property_id=prop.id, source="direct", status="hold",
                        check_in=date(2026, 9, 1), check_out=date(2026, 9, 3)),
            Reservation(property_id=prop.id, source="direct", status="inquiry",
                        check_in=date(2026, 10, 1), check_out=date(2026, 10, 3)),
            Reservation(property_id=prop.id, source="airbnb_ical", status="canceled",
                        external_uid="c@a", check_in=date(2026, 11, 1), check_out=date(2026, 11, 3)),
        ]
    )
    await session.flush()
    result = await blocking_reservations(session, prop.id)
    assert len(result) == 1
    assert result[0].status == "hold"


async def test_assert_no_overlap_raises_409(session, prop):
    session.add(
        Reservation(property_id=prop.id, source="direct", status="confirmed",
                    check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    )
    await session.flush()
    with pytest.raises(HTTPException) as exc_info:
        await assert_no_overlap(session, prop.id, date(2026, 9, 3), date(2026, 9, 7))
    assert exc_info.value.status_code == 409


async def test_assert_no_overlap_passes_when_clear(session, prop):
    await assert_no_overlap(session, prop.id, date(2026, 9, 1), date(2026, 9, 5))
