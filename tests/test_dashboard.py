from datetime import date

from sqlalchemy import select

from app.config import settings
from app.models import CalendarBlock, DailyRate, Reservation

AUTH = ("ops", "secret")


async def test_dashboard_requires_auth(client, prop):
    resp = await client.get("/dashboard")
    assert resp.status_code in (401, 503)


async def test_post_handlers_require_auth(client):
    for path in ("/dashboard/config", "/dashboard/contract-template", "/dashboard/rates",
                 "/dashboard/airbnb-payout"):
        resp = await client.post(path, data={})
        assert resp.status_code in (401, 503)


async def test_dashboard_rejects_invalid_month(client, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    resp = await client.get("/dashboard?year=2026&month=13", auth=AUTH)
    assert resp.status_code == 422


async def test_dashboard_month_zero_rejected_not_silently_substituted(client, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    resp = await client.get("/dashboard?year=2026&month=0", auth=AUTH)
    assert resp.status_code == 422


async def test_config_form_rejects_non_numeric_rate(client, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    resp = await client.post(
        "/dashboard/config",
        data={"nightly_rate_dollars": "not-a-number", "year": "2026", "month": "10"},
        auth=AUTH,
    )
    assert resp.status_code == 422


async def test_dashboard_renders_calendar_hold_and_lists_inquiry(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")

    hold = Reservation(
        property_id=prop.id, source="direct", status="hold", guest_name="Jane Doe",
        check_in=date(2026, 10, 10), check_out=date(2026, 10, 13),
    )
    inquiry = Reservation(
        property_id=prop.id, source="direct", status="inquiry", guest_name="Pending Inquiry Person",
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

    # calendar grid: a direct hold shows the guest name against a
    # direct_hold day, and the inquiry's dates are correctly ignored
    # (unit-covered exhaustively in test_calendar_view.py; here we just
    # confirm the route wires the same behavior through end to end).
    calendar_section, _, reservation_section = body.partition("<h2>Reservations</h2>")
    assert "Jane Doe" in calendar_section
    assert "direct_hold" in calendar_section

    # the inquiry's dates must appear as "available" days in the calendar
    # grid, not marked with any blocking status.
    calendar_lines = calendar_section.splitlines()
    for day_num in (20, 21):
        marker = f"2026-10-{day_num}"
        idx = next(i for i, line in enumerate(calendar_lines) if marker in line)
        status_line = calendar_lines[idx + 1]
        assert "available" in status_line
        for blocking_status in ("direct_hold", "direct_confirmed", "airbnb", "blocked"):
            assert blocking_status not in status_line

    # reservation list: pending inquiries ARE shown so an operator can see
    # them without checking Slack — this system routes everything through
    # human review, and the inquiry list is that surface on this page.
    assert "Pending Inquiry Person" in reservation_section
    assert "inquiry" in reservation_section


async def test_dashboard_reservation_list_is_not_capped_at_30(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")

    for i in range(35):
        session.add(Reservation(
            property_id=prop.id, source="direct", status="hold", guest_name=f"Guest {i}",
            check_in=date(2020, 1, 1 + (i % 27)), check_out=date(2020, 1, 2 + (i % 27)),
        ))
    await session.flush()

    resp = await client.get("/dashboard?year=2020&month=1", auth=AUTH)
    assert resp.status_code == 200
    reservation_section = resp.text.partition("<h2>Reservations</h2>")[2]
    for i in range(35):
        assert f"Guest {i}" in reservation_section


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


async def test_contract_page_shows_and_saves_template(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    prop.contract_template = "Hello {{ guest_name }}"
    await session.flush()

    page = await client.get("/dashboard/contract", auth=AUTH)
    assert page.status_code == 200
    assert "Hello {{ guest_name }}" in page.text

    resp = await client.post(
        "/dashboard/contract-template",
        data={"template": "Updated {{ guest_name }}", "return_to": "contract"},
        auth=AUTH, follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/contract"
    await session.refresh(prop)
    assert prop.contract_template == "Updated {{ guest_name }}"


async def test_contract_page_requires_auth(client, prop):
    assert (await client.get("/dashboard/contract")).status_code in (401, 503)


async def test_dashboard_has_no_settings_section_but_links_contract(client, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    body = (await client.get("/dashboard", auth=AUTH)).text
    assert "<h2>Settings</h2>" not in body
    assert 'href="/dashboard/contract"' in body
