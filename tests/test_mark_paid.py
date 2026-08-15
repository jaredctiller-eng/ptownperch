from datetime import UTC, date, datetime

import pytest

from app.booking.mark_paid import MarkPaidError, mark_paid
from app.models import Reservation


async def test_mark_paid_requires_hold_status(session, prop):
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()
    with pytest.raises(MarkPaidError):
        await mark_paid(session, res)


async def test_mark_paid_requires_signature(session, prop):
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()
    with pytest.raises(MarkPaidError):
        await mark_paid(session, res)


async def test_mark_paid_success(session, prop):
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       signed_at=datetime(2026, 8, 15, tzinfo=UTC))
    session.add(res)
    await session.flush()
    await mark_paid(session, res)
    assert res.status == "confirmed"
