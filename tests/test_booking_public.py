from datetime import date

from sqlalchemy import select

from app.models import Reservation


async def test_get_book_renders_form(client):
    resp = await client.get("/book")
    assert resp.status_code == 200
    assert b'name="check_in"' in resp.content
    assert b'name="honeypot"' in resp.content


async def test_post_book_creates_inquiry_with_quote(client, session, prop):
    prop.nightly_rate_cents = 20000
    await session.flush()

    resp = await client.post(
        "/book",
        data={
            "check_in": "2026-09-01", "check_out": "2026-09-05",
            "guest_name": "Alice Guest", "guest_email": "alice@example.com",
            "guest_phone": "555-1234", "honeypot": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.status == "inquiry"
    assert stored.source == "direct"
    assert stored.guest_name == "Alice Guest"


async def test_post_book_honeypot_filled_is_silently_ignored(client, session, prop):
    prop.nightly_rate_cents = 20000
    await session.flush()

    resp = await client.post(
        "/book",
        data={
            "check_in": "2026-09-01", "check_out": "2026-09-05",
            "guest_name": "Bot", "guest_email": "bot@example.com",
            "guest_phone": "", "honeypot": "im-a-bot",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303  # still redirects, so a bot can't tell it was dropped
    assert (await session.execute(select(Reservation))).scalars().all() == []


async def test_post_book_rejects_overlap(client, session, prop):
    prop.nightly_rate_cents = 20000
    session.add(
        Reservation(property_id=prop.id, source="direct", status="confirmed",
                    check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    )
    await session.flush()

    resp = await client.post(
        "/book",
        data={
            "check_in": "2026-09-03", "check_out": "2026-09-06",
            "guest_name": "Alice", "guest_email": "alice@example.com",
            "guest_phone": "", "honeypot": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 409


async def test_post_book_succeeds_even_if_slack_post_fails(client, session, prop, monkeypatch):
    prop.nightly_rate_cents = 20000
    await session.flush()

    async def failing_post_blocks(payload):
        raise RuntimeError("slack down")

    monkeypatch.setattr("app.routes.booking_public.post_slack_blocks", failing_post_blocks)

    resp = await client.post(
        "/book",
        data={
            "check_in": "2026-09-01", "check_out": "2026-09-05",
            "guest_name": "Alice", "guest_email": "alice@example.com",
            "guest_phone": "", "honeypot": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303  # inquiry still recorded despite Slack failure

    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.status == "inquiry"


async def test_post_book_requires_configured_rate(client, session, prop):
    resp = await client.post(
        "/book",
        data={
            "check_in": "2026-09-01", "check_out": "2026-09-05",
            "guest_name": "Alice", "guest_email": "alice@example.com",
            "guest_phone": "", "honeypot": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 503
