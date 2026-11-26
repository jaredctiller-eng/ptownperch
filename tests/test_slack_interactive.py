import hashlib
import hmac
import json
import time
from datetime import UTC, date, datetime
from unittest.mock import patch

from app.config import settings
from app.models import Reservation


def _signed_headers(body: bytes, secret: str) -> dict:
    timestamp = str(int(time.time()))
    basestring = f"v0:{timestamp}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": sig}


async def _post_interaction(
    client, action_id: str, reservation_id: int, secret: str, agreed_total: str | None = "2000"
):
    payload = {"actions": [{"action_id": action_id, "value": str(reservation_id)}]}
    if agreed_total is not None:
        payload["state"] = {
            "values": {"agreed_total": {"value": {"value": agreed_total}}}
        }
    body = f"payload={json.dumps(payload)}".encode()
    headers = _signed_headers(body, secret)
    return await client.post(
        "/slack/interactive", content=body,
        headers={**headers, "content-type": "application/x-www-form-urlencoded"},
    )


async def test_rejects_unsigned_request(client):
    resp = await client.post("/slack/interactive", content=b"payload={}")
    assert resp.status_code == 401


async def test_approve_flips_to_hold_and_emails(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    prop.nightly_rate_cents = 20000
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                       guest_name="Alice", guest_email="alice@example.com",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    with patch("app.routes.slack_interactive.send_email") as mock_send:
        resp = await _post_interaction(client, "booking_approve", res.id, "shh")
    assert resp.status_code == 200
    mock_send.assert_called_once()

    await session.refresh(res)
    assert res.status == "hold"
    assert res.booking_token is not None


async def test_approve_email_failure_falls_back_gracefully(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    prop.nightly_rate_cents = 20000
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                       guest_name="Alice", guest_email="alice@example.com",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    from app.email.gmail import EmailSendError

    with patch("app.routes.slack_interactive.send_email", side_effect=EmailSendError("smtp down")):
        resp = await _post_interaction(client, "booking_approve", res.id, "shh")
    assert resp.status_code == 200  # never a 500 just because the email failed
    body = resp.json()
    assert "book/" in body["blocks"][0]["text"]["text"]  # link shown for manual send

    await session.refresh(res)
    assert res.status == "hold"  # still approved even though email failed


async def test_approve_rejects_when_now_overlapping(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    prop.nightly_rate_cents = 20000
    inquiry = Reservation(property_id=prop.id, source="direct", status="inquiry",
                           guest_name="Alice", guest_email="alice@example.com",
                           check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    blocker = Reservation(property_id=prop.id, source="direct", status="confirmed",
                           guest_name="Bob", check_in=date(2026, 9, 3), check_out=date(2026, 9, 7))
    session.add_all([inquiry, blocker])
    await session.flush()

    resp = await _post_interaction(client, "booking_approve", inquiry.id, "shh")
    assert resp.status_code == 200
    body = resp.json()
    assert "conflict" in body["blocks"][0]["text"]["text"].lower()

    await session.refresh(inquiry)
    assert inquiry.status == "inquiry"  # unchanged, not silently approved into a double-booking


async def test_approve_locks_property_row_before_overlap_check(client, session, prop, monkeypatch):
    # True row-lock concurrency isn't practically testable against SQLite —
    # it doesn't enforce FOR UPDATE the way Postgres does, so two "concurrent"
    # sessions in-process wouldn't actually block each other here. Instead
    # this test verifies the structural fix: _approve must re-fetch the
    # Property row through the shared locking helper (for_update=True)
    # before calling assert_no_overlap, closing the TOCTOU window. Logical
    # correctness of the overlap check itself is already covered by
    # test_approve_rejects_when_now_overlapping.
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    prop.nightly_rate_cents = 20000
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                       guest_name="Alice", guest_email="alice@example.com",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    import app.routes.slack_interactive as si_module

    original = si_module._get_property_or_404
    calls = []

    async def spy(session_arg, slug, *, for_update=False):
        calls.append(for_update)
        return await original(session_arg, slug, for_update=for_update)

    with (
        patch("app.routes.slack_interactive._get_property_or_404", side_effect=spy),
        patch("app.routes.slack_interactive.send_email"),
    ):
        resp = await _post_interaction(client, "booking_approve", res.id, "shh")

    assert resp.status_code == 200
    # Called exactly once, from inside _approve, with the lock requested.
    assert calls == [True]


async def test_decline_marks_canceled(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    resp = await _post_interaction(client, "booking_decline", res.id, "shh")
    assert resp.status_code == 200

    await session.refresh(res)
    assert res.status == "canceled"


async def test_unknown_action_id_returns_400(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    resp = await _post_interaction(client, "something_else", res.id, "shh")
    assert resp.status_code == 400


async def test_malformed_body_returns_401_not_500(client, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    resp = await client.post(
        "/slack/interactive",
        content=b"\xff\xfe\x00not-utf8",
        headers={"X-Slack-Request-Timestamp": str(int(time.time())), "X-Slack-Signature": "v0=irrelevant"},
    )
    assert resp.status_code == 401


async def test_malformed_payload_shape_returns_400(client, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    body = f"payload={json.dumps({'not_actions': []})}".encode()
    resp = await client.post(
        "/slack/interactive", content=body,
        headers={**_signed_headers(body, "shh"), "content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 400


async def test_mark_paid_action_confirms(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       signed_at=datetime(2026, 8, 15, tzinfo=UTC))
    session.add(res)
    await session.flush()

    resp = await _post_interaction(client, "booking_mark_paid", res.id, "shh")
    assert resp.status_code == 200
    await session.refresh(res)
    assert res.status == "confirmed"


async def test_mark_paid_action_rejects_unsigned(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    resp = await _post_interaction(client, "booking_mark_paid", res.id, "shh")
    assert resp.status_code == 200
    body = resp.json()
    assert "signed" in body["blocks"][0]["text"]["text"].lower()
    await session.refresh(res)
    assert res.status == "hold"


async def test_approve_without_total_changes_nothing(client, session, prop, monkeypatch):
    """A typo or an empty box must leave the reservation exactly as it was."""
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                      guest_name="Alice", guest_email="alice@example.com",
                      check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    resp = await _post_interaction(client, "booking_approve", res.id, "shh", agreed_total="")
    assert resp.status_code == 200
    assert "Not approved" in resp.json()["text"] or "Not approved" in str(resp.json())

    await session.refresh(res)
    assert res.status == "inquiry"
    assert res.agreed_total_cents is None
    assert res.booking_token is None


async def test_approve_rejects_a_nonsense_total(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                      guest_name="Alice", guest_email="alice@example.com",
                      check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    resp = await _post_interaction(client, "booking_approve", res.id, "shh", agreed_total="two grand")
    assert resp.status_code == 200
    await session.refresh(res)
    assert res.status == "inquiry"
    assert res.agreed_total_cents is None


async def test_approve_stores_the_agreed_total(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                      guest_name="Alice", guest_email="alice@example.com",
                      check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    with patch("app.routes.slack_interactive.send_email"):
        resp = await _post_interaction(
            client, "booking_approve", res.id, "shh", agreed_total="$2,450.00"
        )
    assert resp.status_code == 200
    await session.refresh(res)
    assert res.status == "hold"
    assert res.agreed_total_cents == 245000
