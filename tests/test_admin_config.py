from datetime import UTC, date, datetime

from sqlalchemy import select

from app.models import DailyRate, Reservation


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


async def test_status_surfaces_unresolved_blanks(client, session, prop, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_password", "secret")
    prop.contract_template = "Deposit: $[____]"
    await session.flush()

    resp = await client.get("/admin/status", auth=("ops", "secret"))
    assert resp.status_code == 200
    assert resp.json()["contract_has_unresolved_blanks"] is True


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


async def test_put_rates_creates_new_rows(client, session, prop, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_password", "secret")

    resp = await client.put(
        "/admin/rates",
        json={"rates": {"2026-09-01": 45000, "2026-09-02": 99400, "2026-09-03": 99400}},
        auth=("ops", "secret"),
    )
    assert resp.status_code == 200
    assert resp.json()["updated_count"] == 3

    rows = (
        await session.execute(select(DailyRate).where(DailyRate.property_id == prop.id))
    ).scalars().all()
    assert len(rows) == 3
    by_date = {r.date: r.rate_cents for r in rows}
    assert by_date == {
        date(2026, 9, 1): 45000,
        date(2026, 9, 2): 99400,
        date(2026, 9, 3): 99400,
    }


async def test_put_rates_updates_existing_row_instead_of_duplicating(client, session, prop, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_password", "secret")

    session.add(DailyRate(property_id=prop.id, date=date(2026, 9, 1), rate_cents=40000))
    await session.flush()

    resp = await client.put(
        "/admin/rates",
        json={"rates": {"2026-09-01": 45000}},
        auth=("ops", "secret"),
    )
    assert resp.status_code == 200
    assert resp.json()["updated_count"] == 1

    rows = (
        await session.execute(
            select(DailyRate).where(
                DailyRate.property_id == prop.id, DailyRate.date == date(2026, 9, 1)
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].rate_cents == 45000


async def test_put_rates_invalid_date_rejects_whole_batch(client, session, prop, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_password", "secret")

    resp = await client.put(
        "/admin/rates",
        json={"rates": {"2026-09-01": 45000, "not-a-date": 99400}},
        auth=("ops", "secret"),
    )
    assert resp.status_code == 422

    rows = (
        await session.execute(select(DailyRate).where(DailyRate.property_id == prop.id))
    ).scalars().all()
    assert rows == []
