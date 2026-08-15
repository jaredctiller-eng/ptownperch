from datetime import date

from sqlalchemy import select

from app.ical.parse import IcalEvent
from app.models import Reservation
from app.sync.engine import apply_airbnb_events


def ev(uid="a@airbnb.com", start=date(2026, 9, 1), end=date(2026, 9, 5), summary="Reserved"):
    return IcalEvent(uid=uid, start=start, end=end, summary=summary)


async def test_new_event_inserted(session, prop):
    result = await apply_airbnb_events(session, prop.id, [ev()])
    assert [e.uid for e in result.new_events] == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.source == "airbnb_ical"
    assert stored.status == "confirmed"
    assert stored.check_in == date(2026, 9, 1)


async def test_second_run_is_idempotent(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    result = await apply_airbnb_events(session, prop.id, [ev()])
    assert result.has_changes is False


async def test_missing_uid_marks_canceled(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    result = await apply_airbnb_events(session, prop.id, [])
    assert result.canceled_uids == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.status == "canceled"


async def test_changed_dates_updated(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    moved = ev(start=date(2026, 9, 2), end=date(2026, 9, 6))
    result = await apply_airbnb_events(session, prop.id, [moved])
    assert [e.uid for e in result.changed_events] == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert (stored.check_in, stored.check_out) == (date(2026, 9, 2), date(2026, 9, 6))


async def test_collision_with_direct_reservation(session, prop):
    direct = Reservation(
        property_id=prop.id, source="direct", status="hold",
        check_in=date(2026, 9, 3), check_out=date(2026, 9, 7),
    )
    session.add(direct)
    await session.flush()
    result = await apply_airbnb_events(session, prop.id, [ev()])  # 9/1-9/5 overlaps 9/3-9/7
    assert len(result.collisions) == 1
    assert result.collisions[0].airbnb_uid == "a@airbnb.com"
    assert result.collisions[0].direct_reservation_id == direct.id


async def test_adjacent_dates_do_not_collide(session, prop):
    # checkout day == next check-in day is fine (end date is exclusive)
    direct = Reservation(
        property_id=prop.id, source="direct", status="confirmed",
        check_in=date(2026, 9, 5), check_out=date(2026, 9, 8),
    )
    session.add(direct)
    await session.flush()
    result = await apply_airbnb_events(session, prop.id, [ev()])  # ends 9/5
    assert result.collisions == []


async def test_resurrected_uid_reactivates_reservation(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    await apply_airbnb_events(session, prop.id, [])  # feed glitch: everything canceled
    result = await apply_airbnb_events(session, prop.id, [ev()])  # feed recovers
    assert [e.uid for e in result.new_events] == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.status == "confirmed"


async def test_already_canceled_uid_not_reported_again(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    await apply_airbnb_events(session, prop.id, [])
    result = await apply_airbnb_events(session, prop.id, [])  # still absent
    assert result.has_changes is False


async def test_airbnb_block_does_not_collide_with_direct(session, prop):
    # Airbnb "Not available" blocks mirror our own outbound feed; not a double-booking.
    direct = Reservation(
        property_id=prop.id, source="direct", status="confirmed",
        check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
    )
    session.add(direct)
    await session.flush()
    block = ev(uid="mirror@airbnb.com", summary="Airbnb (Not available)")
    result = await apply_airbnb_events(session, prop.id, [block])
    assert result.collisions == []


async def test_duplicate_uids_in_feed_do_not_crash(session, prop):
    result = await apply_airbnb_events(session, prop.id, [ev(), ev()])
    assert [e.uid for e in result.new_events] == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalars().all()
    assert len(stored) == 1


async def test_resurrection_with_new_dates_reports_once(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    await apply_airbnb_events(session, prop.id, [])
    moved = ev(start=date(2026, 9, 10), end=date(2026, 9, 14))
    result = await apply_airbnb_events(session, prop.id, [moved])
    assert [e.uid for e in result.new_events] == ["a@airbnb.com"]
    assert result.changed_events == []
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert (stored.check_in, stored.check_out) == (date(2026, 9, 10), date(2026, 9, 14))
