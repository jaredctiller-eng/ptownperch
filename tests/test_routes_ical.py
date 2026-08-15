from datetime import date

from app.ical.parse import parse_airbnb_ical
from app.models import CalendarBlock, Reservation


async def test_ical_includes_direct_holds_and_blocks_only(client, session, prop):
    session.add_all(
        [
            # direct hold + confirmed -> included
            Reservation(property_id=prop.id, source="direct", status="hold",
                        check_in=date(2026, 9, 1), check_out=date(2026, 9, 5)),
            Reservation(property_id=prop.id, source="direct", status="confirmed",
                        check_in=date(2026, 9, 10), check_out=date(2026, 9, 12)),
            # inquiry and canceled -> excluded
            Reservation(property_id=prop.id, source="direct", status="inquiry",
                        check_in=date(2026, 9, 20), check_out=date(2026, 9, 22)),
            Reservation(property_id=prop.id, source="direct", status="canceled",
                        check_in=date(2026, 9, 25), check_out=date(2026, 9, 27)),
            # airbnb-sourced -> excluded (Airbnb already knows its own bookings)
            Reservation(property_id=prop.id, source="airbnb_ical", status="confirmed",
                        external_uid="x@airbnb.com",
                        check_in=date(2026, 10, 1), check_out=date(2026, 10, 5)),
            # manual block -> included
            CalendarBlock(property_id=prop.id, start_date=date(2026, 11, 1),
                          end_date=date(2026, 11, 3), reason="maintenance"),
        ]
    )
    await session.flush()

    resp = await client.get("/ical/perch.ics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    events = parse_airbnb_ical(resp.text)
    assert [(e.start, e.end) for e in events] == [
        (date(2026, 9, 1), date(2026, 9, 5)),
        (date(2026, 9, 10), date(2026, 9, 12)),
        (date(2026, 11, 1), date(2026, 11, 3)),
    ]


async def test_unknown_slug_404(client):
    resp = await client.get("/ical/nope.ics")
    assert resp.status_code == 404
