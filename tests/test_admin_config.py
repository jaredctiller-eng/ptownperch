from datetime import UTC, date, datetime

from app.models import Reservation


async def test_patch_config_sets_rate_and_tax(client, prop, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_password", "secret")
    resp = await client.patch(
        "/admin/config",
        json={"nightly_rate_cents": 25000, "direct_booking_tax_enabled": True},
        auth=("ops", "secret"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["nightly_rate_cents"] == 25000
    assert body["direct_booking_tax_enabled"] is True


async def test_put_contract_template_rejects_when_blanks_remain(client, prop, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_password", "secret")
    resp = await client.put(
        "/admin/contract-template",
        json={"template": "Deposit: $[____]"},
        auth=("ops", "secret"),
    )
    assert resp.status_code == 200  # storing a draft with blanks is allowed
    body = resp.json()
    assert body["has_unresolved_blanks"] is True


async def test_mark_paid_requires_signature(client, session, prop, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_password", "secret")
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()

    resp = await client.post(f"/admin/reservations/{res.id}/mark-paid", auth=("ops", "secret"))
    assert resp.status_code == 422

    res.signed_at = datetime(2026, 8, 15, tzinfo=UTC)
    res.signed_name = "Alice"
    await session.flush()
    resp = await client.post(f"/admin/reservations/{res.id}/mark-paid", auth=("ops", "secret"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
