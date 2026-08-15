from sqlalchemy import select

from app.bootstrap import upsert_configured_property
from app.config import settings
from app.models import Property


def _configure(monkeypatch, *, slug="perch", name="Ptown Perch", url="https://a/cal.ics"):
    monkeypatch.setattr(settings, "property_slug", slug)
    monkeypatch.setattr(settings, "property_name", name)
    monkeypatch.setattr(settings, "airbnb_ical_url", url)


async def test_no_ical_url_creates_nothing(session, monkeypatch):
    _configure(monkeypatch, url="")
    assert await upsert_configured_property(session) is None
    assert (await session.execute(select(Property))).scalars().all() == []


async def test_creates_property_from_config(session, monkeypatch):
    _configure(monkeypatch)
    await upsert_configured_property(session)
    stored = (await session.execute(select(Property))).scalar_one()
    assert (stored.slug, stored.name, stored.airbnb_ical_url) == (
        "perch",
        "Ptown Perch",
        "https://a/cal.ics",
    )


async def test_second_boot_is_idempotent(session, monkeypatch):
    _configure(monkeypatch)
    await upsert_configured_property(session)
    await upsert_configured_property(session)
    assert len((await session.execute(select(Property))).scalars().all()) == 1


async def test_rotated_ical_url_updates_in_place(session, monkeypatch):
    _configure(monkeypatch)
    await upsert_configured_property(session)
    _configure(monkeypatch, url="https://a/rotated.ics")
    await upsert_configured_property(session)
    stored = (await session.execute(select(Property))).scalar_one()
    assert stored.airbnb_ical_url == "https://a/rotated.ics"
