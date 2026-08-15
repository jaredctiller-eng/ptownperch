from datetime import UTC, date, datetime

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


async def test_guest_page_404_for_unknown_token(client):
    resp = await client.get("/book/nope")
    assert resp.status_code == 404


async def test_guest_page_shows_unsigned_form(client, session, prop):
    prop.nightly_rate_cents = 20000
    prop.contract_template = "Guest: {{guest_name}}, Total: {{total}}"
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", guest_email="alice@example.com",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       booking_token="tok-abc")
    session.add(res)
    await session.flush()

    resp = await client.get("/book/tok-abc")
    assert resp.status_code == 200
    assert b"Guest: Alice" in resp.content
    assert b"sig-pad" in resp.content  # signature pad present, not yet signed


async def test_guest_page_shows_signed_confirmation(client, session, prop):
    prop.contract_template = "x"
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       booking_token="tok-signed", signed_at=datetime(2026, 8, 1, tzinfo=UTC),
                       contract_snapshot="Signed text")
    session.add(res)
    await session.flush()

    resp = await client.get("/book/tok-signed")
    assert resp.status_code == 200
    assert b"Signed text" in resp.content
    assert b"sig-pad" not in resp.content  # no signature widget once already signed


async def test_sign_stores_signature_and_redirects(client, session, prop):
    prop.contract_template = "Guest: {{guest_name}}, Total: {{total}}"
    prop.nightly_rate_cents = 20000
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       booking_token="tok-sign")
    session.add(res)
    await session.flush()

    resp = await client.post(
        "/book/tok-sign/sign",
        data={
            "signed_name": "Alice Guest", "guest_home_address": "1 Main St",
            "occupant_names": "Alice, Bob",
            "signature_image": "data:image/png;base64,abc123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    await session.refresh(res)
    assert res.signed_name == "Alice Guest"
    assert res.signature_image == "data:image/png;base64,abc123"
    assert res.contract_snapshot == "Guest: Alice, Total: $688.00"
    assert res.signed_at is not None
    assert res.status == "hold"  # signing alone doesn't confirm — mark-paid does


async def test_sign_already_signed_reservation_rejected(client, session, prop):
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       booking_token="tok-done", signed_at=datetime(2026, 8, 1, tzinfo=UTC))
    session.add(res)
    await session.flush()

    resp = await client.post(
        "/book/tok-done/sign",
        data={"signed_name": "Alice", "guest_home_address": "x", "occupant_names": "x",
              "signature_image": "data:image/png;base64,x"},
        follow_redirects=False,
    )
    assert resp.status_code == 409


async def test_guest_page_refuses_to_render_while_template_has_blanks(client, session, prop):
    # This is the spec's hard safety stop: a guest must never be able to sign
    # a contract that still contains an operator-facing "[____]" placeholder.
    prop.nightly_rate_cents = 20000
    prop.contract_template = "Deposit: $[____]. Guest: {{guest_name}}"
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       booking_token="tok-blank")
    session.add(res)
    await session.flush()

    resp = await client.get("/book/tok-blank")
    assert resp.status_code == 503

    # Signing must be refused too, not just the page render — a direct POST
    # (bypassing the UI) must not be able to sign an incomplete contract.
    resp = await client.post(
        "/book/tok-blank/sign",
        data={"signed_name": "Alice", "guest_home_address": "x", "occupant_names": "x",
              "signature_image": "data:image/png;base64,x"},
        follow_redirects=False,
    )
    assert resp.status_code == 503


async def test_already_signed_page_still_renders_even_if_template_later_gains_blanks(client, session, prop):
    # contract_snapshot is a frozen copy taken at signing time — a template
    # edited afterward (even to reintroduce a blank) must never affect an
    # already-signed guest's view of what they agreed to.
    prop.contract_template = "Deposit: $[____]"  # edited after this guest signed
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       booking_token="tok-already-signed", signed_at=datetime(2026, 8, 1, tzinfo=UTC),
                       contract_snapshot="Deposit: $500 (frozen at signing)")
    session.add(res)
    await session.flush()

    resp = await client.get("/book/tok-already-signed")
    assert resp.status_code == 200
    assert b"frozen at signing" in resp.content


async def test_sign_posts_slack_notification_with_mark_paid_button(client, session, prop, monkeypatch):
    prop.contract_template = "Guest: {{guest_name}}, Total: {{total}}"
    prop.nightly_rate_cents = 20000
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       booking_token="tok-notify")
    session.add(res)
    await session.flush()

    sent = []

    async def fake_post_blocks(payload):
        sent.append(payload)

    monkeypatch.setattr("app.routes.booking_public.post_slack_blocks", fake_post_blocks)

    resp = await client.post(
        "/book/tok-notify/sign",
        data={"signed_name": "Alice Guest", "guest_home_address": "1 Main St",
              "occupant_names": "Alice", "signature_image": "data:image/png;base64,abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(sent) == 1
    actions = sent[0]["blocks"][-1]["elements"]
    assert actions[0]["action_id"] == "booking_mark_paid"


async def test_guest_page_undefined_template_variable_returns_503_not_500(client, session, prop):
    # render_contract raises jinja2.UndefinedError if the template references
    # a key not provided — this is an operator template bug, and it must
    # surface as a clean 503, never a raw stack trace to a guest.
    prop.nightly_rate_cents = 20000
    prop.contract_template = "Guest: {{guest_name}}, Mystery: {{undefined_key}}"
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       booking_token="tok-undefined")
    session.add(res)
    await session.flush()

    resp = await client.get("/book/tok-undefined")
    assert resp.status_code == 503
