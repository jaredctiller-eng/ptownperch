from datetime import date

from sqlalchemy import select

from app.config import settings
from app.models import CalendarBlock, DailyRate, Reservation

AUTH = ("ops", "secret")


async def test_dashboard_requires_auth(client, prop):
    resp = await client.get("/dashboard")
    assert resp.status_code in (401, 503)


async def test_dashboard_renders_calendar_and_excludes_inquiry(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")

    hold = Reservation(
        property_id=prop.id, source="direct", status="hold", guest_name="Jane Doe",
        check_in=date(2026, 10, 10), check_out=date(2026, 10, 13),
    )
    inquiry = Reservation(
        property_id=prop.id, source="direct", status="inquiry", guest_name="Secret Inquiry Person",
        check_in=date(2026, 10, 20), check_out=date(2026, 10, 22),
    )
    block = CalendarBlock(
        property_id=prop.id, start_date=date(2026, 10, 25), end_date=date(2026, 10, 27),
        reason="maintenance",
    )
    session.add_all([hold, inquiry, block])
    await session.flush()

    resp = await client.get("/dashboard?year=2026&month=10", auth=AUTH)
    assert resp.status_code == 200
    body = resp.text
    assert "Jane Doe" in body
    assert "Secret Inquiry Person" not in body


async def test_dashboard_post_config_updates_property(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")

    resp = await client.post(
        "/dashboard/config",
        data={
            "nightly_rate_dollars": "625",
            "direct_booking_tax_enabled": "on",
            "year": "2026",
            "month": "10",
        },
        auth=AUTH,
        follow_redirects=False,
    )
    assert resp.status_code == 303

    refreshed = (
        await session.execute(select(type(prop)).where(type(prop).id == prop.id))
    ).scalar_one()
    assert refreshed.nightly_rate_cents == 62500
    assert refreshed.direct_booking_tax_enabled is True


async def test_dashboard_post_contract_template_updates_property(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")

    resp = await client.post(
        "/dashboard/contract-template",
        data={"template": "Deposit: $500", "year": "2026", "month": "10"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert resp.status_code == 303

    await session.refresh(prop)
    assert prop.contract_template == "Deposit: $500"


async def test_dashboard_post_rates_valid_lines_creates_rows(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")

    resp = await client.post(
        "/dashboard/rates",
        data={
            "rates_text": "2026-10-01,650\n2026-10-02,700",
            "year": "2026",
            "month": "10",
        },
        auth=AUTH,
        follow_redirects=False,
    )
    assert resp.status_code == 303

    rows = (
        await session.execute(select(DailyRate).where(DailyRate.property_id == prop.id))
    ).scalars().all()
    assert len(rows) == 2
    by_date = {r.date: r.rate_cents for r in rows}
    assert by_date[date(2026, 10, 1)] == 65000
    assert by_date[date(2026, 10, 2)] == 70000


async def test_dashboard_post_rates_malformed_line_writes_nothing(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")

    resp = await client.post(
        "/dashboard/rates",
        data={
            "rates_text": "2026-10-01,650\nnot-a-valid-line",
            "year": "2026",
            "month": "10",
        },
        auth=AUTH,
        follow_redirects=False,
    )
    assert resp.status_code == 422

    rows = (
        await session.execute(select(DailyRate).where(DailyRate.property_id == prop.id))
    ).scalars().all()
    assert len(rows) == 0
