from datetime import date

import pytest
from sqlalchemy import select

import app.routes.admin as admin_mod
from app.config import settings
from app.ical.parse import parse_airbnb_ical
from app.models import Reservation

AUTH = ("ops", "secret")


@pytest.fixture(autouse=True)
def admin_creds(monkeypatch):
    monkeypatch.setattr(settings, "admin_user", "ops")
    monkeypatch.setattr(settings, "admin_password", "secret")


def payload(**overrides):
    base = {
        "check_in": "2026-08-28",
        "check_out": "2026-09-07",
        "guest_name": "John Cena",
    }
    base.update(overrides)
    return base


# --- auth ---


async def test_503_when_admin_password_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "")
    resp = await client.post("/admin/reservations", json=payload())
    assert resp.status_code == 503


async def test_401_no_credentials(client):
    resp = await client.post("/admin/reservations", json=payload())
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Basic"


async def test_401_wrong_password(client):
    resp = await client.post("/admin/reservations", json=payload(), auth=("ops", "nope"))
    assert resp.status_code == 401


async def test_401_wrong_username(client):
    resp = await client.post("/admin/reservations", json=payload(), auth=("someone-else", "secret"))
    assert resp.status_code == 401


# --- create: happy path ---


async def test_create_happy_path_201(client, session, prop):
    resp = await client.post("/admin/reservations", json=payload(), auth=AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["guest_name"] == "John Cena"
    assert body["check_in"] == "2026-08-28"
    assert body["check_out"] == "2026-09-07"
    assert body["source"] == "direct"
    assert body["status"] == "confirmed"

    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.source == "direct"
    assert stored.status == "confirmed"
    assert stored.guest_name == "John Cena"
    assert stored.check_in == date(2026, 8, 28)
    assert stored.check_out == date(2026, 9, 7)
    assert stored.property_id == prop.id


async def test_create_unknown_property_slug_404(client, prop):
    resp = await client.post(
        "/admin/reservations", json=payload(property_slug="nope"), auth=AUTH
    )
    assert resp.status_code == 404


async def test_created_booking_appears_in_outbound_ical(client, session, prop):
    resp = await client.post("/admin/reservations", json=payload(), auth=AUTH)
    assert resp.status_code == 201

    ics_resp = await client.get("/ical/perch.ics")
    events = parse_airbnb_ical(ics_resp.text)
    assert (date(2026, 8, 28), date(2026, 9, 7)) in [(e.start, e.end) for e in events]
    # public feed must not leak guest identity
    assert "John Cena" not in ics_resp.text


# --- create: validation ---


@pytest.mark.parametrize(
    "check_in,check_out",
    [
        ("2026-09-07", "2026-08-28"),  # inverted
        ("2026-08-28", "2026-08-28"),  # zero-length
    ],
)
async def test_422_bad_date_range(client, prop, check_in, check_out):
    resp = await client.post(
        "/admin/reservations",
        json=payload(check_in=check_in, check_out=check_out),
        auth=AUTH,
    )
    assert resp.status_code == 422


async def test_422_bogus_status(client, prop):
    resp = await client.post(
        "/admin/reservations", json=payload(status="bogus"), auth=AUTH
    )
    assert resp.status_code == 422


# --- create: overlap conflicts ---


async def test_409_overlap_with_direct_confirmed(client, session, prop):
    session.add(
        Reservation(
            property_id=prop.id, source="direct", status="confirmed",
            guest_name="Existing Guest",
            check_in=date(2026, 8, 30), check_out=date(2026, 9, 3),
        )
    )
    await session.flush()

    resp = await client.post("/admin/reservations", json=payload(), auth=AUTH)
    assert resp.status_code == 409
    body = resp.json()
    assert "direct" in str(body)
    assert "2026-08-30" in str(body) or "2026-09-03" in str(body)


async def test_409_overlap_with_airbnb_ical_confirmed(client, session, prop):
    session.add(
        Reservation(
            property_id=prop.id, source="airbnb_ical", status="confirmed",
            external_uid="abc@airbnb.com",
            check_in=date(2026, 9, 1), check_out=date(2026, 9, 4),
        )
    )
    await session.flush()

    resp = await client.post("/admin/reservations", json=payload(), auth=AUTH)
    assert resp.status_code == 409
    body = resp.json()
    assert "airbnb_ical" in str(body)


async def test_no_conflict_with_canceled_airbnb_reservation(client, session, prop):
    session.add(
        Reservation(
            property_id=prop.id, source="airbnb_ical", status="canceled",
            external_uid="abc@airbnb.com",
            check_in=date(2026, 9, 1), check_out=date(2026, 9, 4),
        )
    )
    await session.flush()

    resp = await client.post("/admin/reservations", json=payload(), auth=AUTH)
    assert resp.status_code == 201


async def test_adjacent_dates_are_allowed(client, session, prop):
    # existing checkout day == new check-in day: not a conflict.
    session.add(
        Reservation(
            property_id=prop.id, source="direct", status="confirmed",
            check_in=date(2026, 8, 20), check_out=date(2026, 8, 28),
        )
    )
    await session.flush()

    resp = await client.post("/admin/reservations", json=payload(), auth=AUTH)
    assert resp.status_code == 201


async def test_inquiry_status_does_not_conflict_check(client, session, prop):
    session.add(
        Reservation(
            property_id=prop.id, source="direct", status="confirmed",
            check_in=date(2026, 8, 30), check_out=date(2026, 9, 3),
        )
    )
    await session.flush()

    resp = await client.post(
        "/admin/reservations", json=payload(status="inquiry"), auth=AUTH
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "inquiry"


# --- create: slack ---


async def test_slack_failure_does_not_fail_create(client, session, prop, monkeypatch):
    async def boom(text: str) -> None:
        raise RuntimeError("slack is down")

    monkeypatch.setattr(admin_mod, "post_slack", boom)
    resp = await client.post("/admin/reservations", json=payload(), auth=AUTH)
    assert resp.status_code == 201
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.guest_name == "John Cena"


# --- list ---


async def test_list_reservations_newest_check_in_first(client, session, prop):
    session.add_all(
        [
            Reservation(property_id=prop.id, source="direct", status="confirmed",
                        check_in=date(2026, 8, 1), check_out=date(2026, 8, 5)),
            Reservation(property_id=prop.id, source="direct", status="confirmed",
                        check_in=date(2026, 9, 1), check_out=date(2026, 9, 5)),
        ]
    )
    await session.flush()

    resp = await client.get("/admin/reservations", auth=AUTH)
    assert resp.status_code == 200
    dates = [r["check_in"] for r in resp.json()]
    assert dates == ["2026-09-01", "2026-08-01"]


async def test_list_reservations_filters_by_source_and_status(client, session, prop):
    session.add_all(
        [
            Reservation(property_id=prop.id, source="direct", status="confirmed",
                        check_in=date(2026, 8, 1), check_out=date(2026, 8, 5)),
            Reservation(property_id=prop.id, source="airbnb_ical", status="confirmed",
                        external_uid="a@airbnb.com",
                        check_in=date(2026, 9, 1), check_out=date(2026, 9, 5)),
            Reservation(property_id=prop.id, source="direct", status="canceled",
                        check_in=date(2026, 10, 1), check_out=date(2026, 10, 5)),
        ]
    )
    await session.flush()

    resp = await client.get("/admin/reservations?source=direct&status=confirmed", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["source"] == "direct"
    assert body[0]["status"] == "confirmed"


# --- cancel ---


async def test_cancel_flips_status_and_removes_from_feed(client, session, prop):
    create_resp = await client.post("/admin/reservations", json=payload(), auth=AUTH)
    res_id = create_resp.json()["id"]

    cancel_resp = await client.post(f"/admin/reservations/{res_id}/cancel", auth=AUTH)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "canceled"

    stored = await session.get(Reservation, res_id)
    await session.refresh(stored)
    assert stored.status == "canceled"

    ics_resp = await client.get("/ical/perch.ics")
    events = parse_airbnb_ical(ics_resp.text)
    assert (date(2026, 8, 28), date(2026, 9, 7)) not in [(e.start, e.end) for e in events]


async def test_cancel_unknown_id_404(client, prop):
    resp = await client.post("/admin/reservations/999999/cancel", auth=AUTH)
    assert resp.status_code == 404


# --- status ---


async def test_admin_status_does_not_leak_ical_url(client, session, prop):
    prop.airbnb_ical_url = "https://www.airbnb.com/calendar/ical/12345.ics?s=SUPERSECRETTOKEN"
    await session.flush()

    resp = await client.get("/admin/status", auth=AUTH)
    assert resp.status_code == 200
    assert "SUPERSECRETTOKEN" not in resp.text


async def test_admin_status_reports_configured_flag_and_counts(client, session, prop):
    prop.airbnb_ical_url = "https://example.com/cal.ics"
    session.add_all(
        [
            Reservation(property_id=prop.id, source="direct", status="confirmed",
                        check_in=date(2026, 8, 1), check_out=date(2026, 8, 5)),
            Reservation(property_id=prop.id, source="direct", status="confirmed",
                        check_in=date(2026, 9, 10), check_out=date(2026, 9, 12)),
            Reservation(property_id=prop.id, source="direct", status="canceled",
                        check_in=date(2026, 12, 1), check_out=date(2026, 12, 5)),
        ]
    )
    await session.flush()

    resp = await client.get("/admin/status", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["airbnb_ical_configured"] is True
    assert body["property"]["slug"] == "perch"
    counts = {(c["source"], c["status"]): c["count"] for c in body["reservation_counts"]}
    assert counts[("direct", "confirmed")] == 2
    assert counts[("direct", "canceled")] == 1
    assert body["earliest_check_in"] == "2026-08-01"
    assert body["latest_check_in"] == "2026-09-10"  # canceled excluded


async def test_admin_status_no_ical_url_configured(client, session, prop):
    resp = await client.get("/admin/status", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["airbnb_ical_configured"] is False
