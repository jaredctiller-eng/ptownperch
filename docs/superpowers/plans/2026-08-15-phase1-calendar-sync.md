# Phase 1: Calendar Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the ptown-perch-ops backend and ship the one autonomous loop: pull Airbnb's iCal every 10 minutes, detect new/changed/canceled bookings and double-booking collisions, notify Slack, and serve an outbound iCal that Airbnb imports to block direct-booking dates.

**Architecture:** Single FastAPI + SQLAlchemy(async) service; APScheduler runs the sync loop in-process. Airbnb integration is iCal-only behind small parse/generate modules so a channel-manager adapter can replace it later. Slack is notify-only in this phase (incoming webhook — interactive buttons arrive with Phase 2). Schema is created via `Base.metadata.create_all` in this phase; Alembic is introduced at the start of Phase 2, before any schema *changes* hit a database with real data.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async (aiosqlite for dev/tests, asyncpg for Railway Postgres), APScheduler, httpx, icalendar, pydantic-settings, pytest + pytest-asyncio, ruff. Deployed to a **dedicated Railway project**.

**Spec:** `docs/superpowers/specs/2026-08-15-str-ops-backend-design.md`

**Repo layout after this plan:**

```
ptown-perch-ops/
  pyproject.toml
  app/
    __init__.py
    config.py            # settings (env-driven)
    db.py                # engine, session factory, get_session dependency
    models.py            # Property, Reservation, CalendarBlock
    main.py              # FastAPI app + scheduler lifespan
    ical/
      __init__.py
      parse.py           # Airbnb .ics -> IcalEvent list
      generate.py        # entries -> outbound .ics bytes
    sync/
      __init__.py
      engine.py          # diff/upsert/collision logic (pure DB, no network)
      run.py             # fetch + apply + notify per property; CLI entry
    slack/
      __init__.py
      notify.py          # webhook post + message formatting
    routes/
      __init__.py
      health.py
      ical.py            # GET /ical/{slug}.ics
  scripts/
    seed_property.py
  tests/
    conftest.py
    test_health.py
    test_models.py
    test_ical_parse.py
    test_ical_generate.py
    test_routes_ical.py
    test_sync_engine.py
    test_slack_notify.py
```

---

### Task 1: Project scaffold + health endpoint

**Files:**
- Create: `pyproject.toml`, `app/__init__.py`, `app/config.py`, `app/routes/__init__.py`, `app/routes/health.py`, `app/main.py` (minimal — no scheduler yet), `tests/conftest.py` (empty for now), `tests/test_health.py`, `.gitignore`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "ptown-perch-ops"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite",
    "asyncpg",
    "pydantic-settings",
    "httpx",
    "icalendar",
    "apscheduler",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "ruff"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*", "scripts*"]
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
dev.db
.env
.pytest_cache/
.ruff_cache/
```

- [ ] **Step 3: Create venv and install**

Run:
```bash
cd ~/Projects/ptown-perch-ops && python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```
Expected: installs succeed. (All later `pytest`/`ruff` commands in this plan mean `.venv/bin/pytest` / `.venv/bin/ruff` from the repo root.)

- [ ] **Step 4: Write the failing test — `tests/test_health.py`**

```python
import httpx
import pytest_asyncio
from httpx import ASGITransport

from app.main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

Also create empty `tests/conftest.py`, `app/__init__.py`, `app/routes/__init__.py`.

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 6: Implement**

`app/config.py`:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    slack_webhook_url: str = ""
    sync_interval_minutes: int = 10

    @property
    def sqlalchemy_url(self) -> str:
        # Railway provides postgresql://; SQLAlchemy async needs the asyncpg driver.
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
```

`app/routes/health.py`:
```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}
```

`app/main.py` (minimal for now; Task 8 adds the scheduler lifespan):
```python
from fastapi import FastAPI

from app.routes import health

app = FastAPI(title="Ptown Perch Ops")
app.include_router(health.router)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_health.py -v`
Expected: PASS (1 passed)

- [ ] **Step 8: Lint and commit**

```bash
ruff check app/ tests/ && git add -A && git commit -m "feat: scaffold FastAPI app with health endpoint"
```

---

### Task 2: Data models + test session fixture

**Files:**
- Create: `app/models.py`, `app/db.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test — `tests/test_models.py`**

```python
from datetime import date

from sqlalchemy import select

from app.models import CalendarBlock, Property, Reservation


async def test_property_reservation_roundtrip(session, prop):
    session.add(
        Reservation(
            property_id=prop.id,
            source="airbnb_ical",
            status="confirmed",
            external_uid="abc123@airbnb.com",
            check_in=date(2026, 9, 1),
            check_out=date(2026, 9, 5),
        )
    )
    session.add(
        CalendarBlock(
            property_id=prop.id,
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 3),
            reason="maintenance",
        )
    )
    await session.flush()

    res = (await session.execute(select(Reservation))).scalar_one()
    assert res.external_uid == "abc123@airbnb.com"
    assert res.check_out == date(2026, 9, 5)
    block = (await session.execute(select(CalendarBlock))).scalar_one()
    assert block.reason == "maintenance"
    loaded_prop = (await session.execute(select(Property))).scalar_one()
    assert loaded_prop.slug == "perch"
```

Replace `tests/conftest.py` with:
```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Property


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def prop(session):
    p = Property(slug="perch", name="Ptown Perch")
    session.add(p)
    await session.flush()
    return p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Implement `app/models.py`**

```python
from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    airbnb_ical_url: Mapped[str] = mapped_column(String(512), default="")


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (UniqueConstraint("property_id", "external_uid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    source: Mapped[str] = mapped_column(String(16))  # 'airbnb_ical' | 'direct'
    status: Mapped[str] = mapped_column(String(16))  # inquiry|hold|confirmed|checked_out|canceled
    external_uid: Mapped[str | None] = mapped_column(String(255), default=None)
    guest_name: Mapped[str | None] = mapped_column(String(128), default=None)
    guest_email: Mapped[str | None] = mapped_column(String(255), default=None)
    guest_phone: Mapped[str | None] = mapped_column(String(32), default=None)
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)  # exclusive (checkout day)


class CalendarBlock(Base):
    __tablename__ = "calendar_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)  # exclusive
    reason: Mapped[str] = mapped_column(String(128), default="")
```

Create `app/db.py`:
```python
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.sqlalchemy_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
ruff check app/ tests/ && git add -A && git commit -m "feat: add Property/Reservation/CalendarBlock models and async session"
```

---

### Task 3: Airbnb iCal parsing

**Files:**
- Create: `app/ical/__init__.py`, `app/ical/parse.py`
- Test: `tests/test_ical_parse.py`

- [ ] **Step 1: Write the failing test — `tests/test_ical_parse.py`**

```python
from datetime import date

from app.ical.parse import parse_airbnb_ical

AIRBNB_ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Airbnb Inc//Hosting Calendar 1.0//EN\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20260901\r
DTEND;VALUE=DATE:20260905\r
UID:1418fb9d5d4d-8b47c0a@airbnb.com\r
SUMMARY:Reserved\r
DESCRIPTION:Reservation URL: https://www.airbnb.com/hosting/reservations/de\r
 tails/HMABCDE\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20261010\r
DTEND;VALUE=DATE:20261012\r
UID:5f2b1c-blocked@airbnb.com\r
SUMMARY:Airbnb (Not available)\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_parses_events_sorted_by_start():
    events = parse_airbnb_ical(AIRBNB_ICS)
    assert len(events) == 2
    first, second = events
    assert first.uid == "1418fb9d5d4d-8b47c0a@airbnb.com"
    assert first.start == date(2026, 9, 1)
    assert first.end == date(2026, 9, 5)  # exclusive checkout day
    assert first.summary == "Reserved"
    assert first.is_reservation is True
    assert second.is_reservation is False


def test_empty_calendar():
    empty = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\nEND:VCALENDAR\r\n"
    assert parse_airbnb_ical(empty) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ical_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ical'`

- [ ] **Step 3: Implement `app/ical/parse.py`** (and empty `app/ical/__init__.py`)

```python
from dataclasses import dataclass
from datetime import date, datetime

from icalendar import Calendar


@dataclass(frozen=True)
class IcalEvent:
    uid: str
    start: date  # check-in
    end: date  # check-out (exclusive, per iCal DTEND semantics)
    summary: str

    @property
    def is_reservation(self) -> bool:
        # Airbnb uses SUMMARY "Reserved" for bookings and
        # "Airbnb (Not available)" for host/system blocks.
        return self.summary.lower().startswith("reserved")


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def parse_airbnb_ical(text: str) -> list[IcalEvent]:
    cal = Calendar.from_ical(text)
    events = [
        IcalEvent(
            uid=str(component["UID"]),
            start=_as_date(component.decoded("DTSTART")),
            end=_as_date(component.decoded("DTEND")),
            summary=str(component.get("SUMMARY", "")),
        )
        for component in cal.walk("VEVENT")
    ]
    return sorted(events, key=lambda e: (e.start, e.uid))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ical_parse.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
ruff check app/ tests/ && git add -A && git commit -m "feat: parse Airbnb iCal feeds into IcalEvent records"
```

---

### Task 4: Outbound iCal generation

**Files:**
- Create: `app/ical/generate.py`
- Test: `tests/test_ical_generate.py`

- [ ] **Step 1: Write the failing test — `tests/test_ical_generate.py`**

```python
from datetime import date

from app.ical.generate import build_calendar
from app.ical.parse import parse_airbnb_ical


def test_roundtrips_through_parser():
    ics = build_calendar(
        [
            ("res-1@ptownperch", date(2026, 9, 1), date(2026, 9, 5)),
            ("block-2@ptownperch", date(2026, 10, 1), date(2026, 10, 3)),
        ]
    )
    events = parse_airbnb_ical(ics.decode())
    assert [e.uid for e in events] == ["res-1@ptownperch", "block-2@ptownperch"]
    assert events[0].start == date(2026, 9, 1)
    assert events[0].end == date(2026, 9, 5)


def test_no_guest_details_leak():
    # This feed is imported by Airbnb — summaries must be generic.
    ics = build_calendar([("res-1@ptownperch", date(2026, 9, 1), date(2026, 9, 5))])
    assert b"SUMMARY:Unavailable" in ics


def test_empty_calendar_is_valid():
    events = parse_airbnb_ical(build_calendar([]).decode())
    assert events == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ical_generate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ical.generate'`

- [ ] **Step 3: Implement `app/ical/generate.py`**

```python
from datetime import date

from icalendar import Calendar, Event


def build_calendar(entries: list[tuple[str, date, date]]) -> bytes:
    """Build an all-day-busy .ics from (uid, start, end_exclusive) entries.

    Summaries are intentionally generic: Airbnb imports this feed, so it
    must never contain guest names or contact details.
    """
    cal = Calendar()
    cal.add("prodid", "-//Ptown Perch Ops//EN")
    cal.add("version", "2.0")
    for uid, start, end in entries:
        event = Event()
        event.add("uid", uid)
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("summary", "Unavailable")
        cal.add_component(event)
    return cal.to_ical()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ical_generate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
ruff check app/ tests/ && git add -A && git commit -m "feat: generate outbound availability iCal"
```

---

### Task 5: Outbound iCal route

**Files:**
- Create: `app/routes/ical.py`
- Modify: `app/main.py`, `tests/conftest.py`
- Test: `tests/test_routes_ical.py`

- [ ] **Step 1: Add an authed client fixture to `tests/conftest.py`** (append)

```python
import httpx
from httpx import ASGITransport

from app.db import get_session
from app.main import app


@pytest_asyncio.fixture
async def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

(Also delete the local `client` fixture from `tests/test_health.py` — the conftest one replaces it; `test_health` itself is unchanged.)

- [ ] **Step 2: Write the failing test — `tests/test_routes_ical.py`**

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_routes_ical.py -v`
Expected: FAIL — 404 for `/ical/perch.ics` (route not registered)

- [ ] **Step 4: Implement `app/routes/ical.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.ical.generate import build_calendar
from app.models import CalendarBlock, Property, Reservation

router = APIRouter()

BLOCKING_STATUSES = ("hold", "confirmed")


@router.get("/ical/{slug}.ics")
async def property_ical(slug: str, session: AsyncSession = Depends(get_session)):
    prop = (
        await session.execute(select(Property).where(Property.slug == slug))
    ).scalar_one_or_none()
    if prop is None:
        raise HTTPException(status_code=404, detail="unknown property")

    reservations = (
        (
            await session.execute(
                select(Reservation)
                .where(
                    Reservation.property_id == prop.id,
                    Reservation.source == "direct",
                    Reservation.status.in_(BLOCKING_STATUSES),
                )
                .order_by(Reservation.check_in)
            )
        )
        .scalars()
        .all()
    )
    blocks = (
        (
            await session.execute(
                select(CalendarBlock)
                .where(CalendarBlock.property_id == prop.id)
                .order_by(CalendarBlock.start_date)
            )
        )
        .scalars()
        .all()
    )
    entries = [(f"res-{r.id}@ptownperch", r.check_in, r.check_out) for r in reservations]
    entries += [(f"block-{b.id}@ptownperch", b.start_date, b.end_date) for b in blocks]
    return Response(content=build_calendar(entries), media_type="text/calendar")
```

Modify `app/main.py` to register the router:
```python
from fastapi import FastAPI

from app.routes import health, ical

app = FastAPI(title="Ptown Perch Ops")
app.include_router(health.router)
app.include_router(ical.router)
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `pytest tests/ -v`
Expected: PASS (test_routes_ical 2 passed; full suite green)

- [ ] **Step 6: Commit**

```bash
ruff check app/ tests/ && git add -A && git commit -m "feat: serve outbound availability iCal per property"
```

---

### Task 6: Sync engine (diff, upsert, collisions)

**Files:**
- Create: `app/sync/__init__.py`, `app/sync/engine.py`
- Test: `tests/test_sync_engine.py`

- [ ] **Step 1: Write the failing test — `tests/test_sync_engine.py`**

```python
from datetime import date

from sqlalchemy import select

from app.ical.parse import IcalEvent
from app.models import Reservation
from app.sync.engine import apply_airbnb_events


def ev(uid="a@airbnb.com", start=date(2026, 9, 1), end=date(2026, 9, 5), summary="Reserved"):
    return IcalEvent(uid=uid, start=start, end=end, summary=summary)


async def test_new_event_inserted(session, prop):
    result = await apply_airbnb_events(session, prop.id, [ev()])
    assert [e.uid for e in result.new_events] == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.source == "airbnb_ical"
    assert stored.status == "confirmed"
    assert stored.check_in == date(2026, 9, 1)


async def test_second_run_is_idempotent(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    result = await apply_airbnb_events(session, prop.id, [ev()])
    assert result.has_changes is False


async def test_missing_uid_marks_canceled(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    result = await apply_airbnb_events(session, prop.id, [])
    assert result.canceled_uids == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.status == "canceled"


async def test_changed_dates_updated(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    moved = ev(start=date(2026, 9, 2), end=date(2026, 9, 6))
    result = await apply_airbnb_events(session, prop.id, [moved])
    assert [e.uid for e in result.changed_events] == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert (stored.check_in, stored.check_out) == (date(2026, 9, 2), date(2026, 9, 6))


async def test_collision_with_direct_reservation(session, prop):
    direct = Reservation(
        property_id=prop.id, source="direct", status="hold",
        check_in=date(2026, 9, 3), check_out=date(2026, 9, 7),
    )
    session.add(direct)
    await session.flush()
    result = await apply_airbnb_events(session, prop.id, [ev()])  # 9/1-9/5 overlaps 9/3-9/7
    assert len(result.collisions) == 1
    assert result.collisions[0].airbnb_uid == "a@airbnb.com"
    assert result.collisions[0].direct_reservation_id == direct.id


async def test_adjacent_dates_do_not_collide(session, prop):
    # checkout day == next check-in day is fine (end date is exclusive)
    direct = Reservation(
        property_id=prop.id, source="direct", status="confirmed",
        check_in=date(2026, 9, 5), check_out=date(2026, 9, 8),
    )
    session.add(direct)
    await session.flush()
    result = await apply_airbnb_events(session, prop.id, [ev()])  # ends 9/5
    assert result.collisions == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sync_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sync'`

- [ ] **Step 3: Implement `app/sync/engine.py`** (and empty `app/sync/__init__.py`)

```python
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ical.parse import IcalEvent
from app.models import Reservation


@dataclass(frozen=True)
class Collision:
    airbnb_uid: str
    direct_reservation_id: int


@dataclass
class SyncResult:
    new_events: list[IcalEvent] = field(default_factory=list)
    changed_events: list[IcalEvent] = field(default_factory=list)
    canceled_uids: list[str] = field(default_factory=list)
    collisions: list[Collision] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.new_events or self.changed_events or self.canceled_uids or self.collisions)


def _overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    # End dates are exclusive, so back-to-back stays don't overlap.
    return a_start < b_end and b_start < a_end


async def apply_airbnb_events(
    session: AsyncSession, property_id: int, events: list[IcalEvent]
) -> SyncResult:
    result = SyncResult()

    existing = (
        (
            await session.execute(
                select(Reservation).where(
                    Reservation.property_id == property_id,
                    Reservation.source == "airbnb_ical",
                    Reservation.status != "canceled",
                )
            )
        )
        .scalars()
        .all()
    )
    by_uid = {r.external_uid: r for r in existing}
    feed_uids = {e.uid for e in events}

    for event in events:
        if event.uid in by_uid:
            reservation = by_uid[event.uid]
            if (reservation.check_in, reservation.check_out) != (event.start, event.end):
                reservation.check_in = event.start
                reservation.check_out = event.end
                result.changed_events.append(event)
        else:
            session.add(
                Reservation(
                    property_id=property_id,
                    source="airbnb_ical",
                    status="confirmed",
                    external_uid=event.uid,
                    check_in=event.start,
                    check_out=event.end,
                )
            )
            result.new_events.append(event)

    for uid, reservation in by_uid.items():
        if uid not in feed_uids:
            reservation.status = "canceled"
            result.canceled_uids.append(uid)

    direct = (
        (
            await session.execute(
                select(Reservation).where(
                    Reservation.property_id == property_id,
                    Reservation.source == "direct",
                    Reservation.status.in_(("hold", "confirmed")),
                )
            )
        )
        .scalars()
        .all()
    )
    for event in events:
        for d in direct:
            if _overlaps(event.start, event.end, d.check_in, d.check_out):
                result.collisions.append(Collision(event.uid, d.id))

    await session.flush()
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sync_engine.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
ruff check app/ tests/ && git add -A && git commit -m "feat: sync engine — upsert airbnb events, detect changes and collisions"
```

---

### Task 7: Slack notifier

**Files:**
- Create: `app/slack/__init__.py`, `app/slack/notify.py`
- Test: `tests/test_slack_notify.py`

- [ ] **Step 1: Write the failing test — `tests/test_slack_notify.py`**

```python
from datetime import date

from app.ical.parse import IcalEvent
from app.slack.notify import format_sync_messages, post_slack
from app.sync.engine import Collision, SyncResult


def test_formats_all_change_kinds():
    result = SyncResult(
        new_events=[
            IcalEvent(uid="n@airbnb.com", start=date(2026, 9, 1), end=date(2026, 9, 5),
                      summary="Reserved"),
            IcalEvent(uid="b@airbnb.com", start=date(2026, 9, 8), end=date(2026, 9, 9),
                      summary="Airbnb (Not available)"),
        ],
        changed_events=[
            IcalEvent(uid="c@airbnb.com", start=date(2026, 9, 10), end=date(2026, 9, 12),
                      summary="Reserved"),
        ],
        canceled_uids=["gone@airbnb.com"],
        collisions=[Collision(airbnb_uid="n@airbnb.com", direct_reservation_id=7)],
    )
    msgs = format_sync_messages("Ptown Perch", result)
    assert len(msgs) == 5
    assert "New Airbnb booking" in msgs[0]
    assert "New Airbnb block" in msgs[1]
    assert "dates changed" in msgs[2]
    assert "canceled" in msgs[3]
    assert "DOUBLE-BOOKING RISK" in msgs[4]
    assert "#7" in msgs[4]


def test_no_changes_no_messages():
    assert format_sync_messages("Ptown Perch", SyncResult()) == []


async def test_post_slack_noop_without_webhook():
    # settings.slack_webhook_url defaults to "" in tests — must not raise or call out.
    await post_slack("hello")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_slack_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.slack'`

- [ ] **Step 3: Implement `app/slack/notify.py`** (and empty `app/slack/__init__.py`)

```python
import httpx

from app.config import settings
from app.sync.engine import SyncResult


async def post_slack(text: str) -> None:
    if not settings.slack_webhook_url:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(settings.slack_webhook_url, json={"text": text})
        resp.raise_for_status()


def format_sync_messages(property_name: str, result: SyncResult) -> list[str]:
    msgs = []
    for e in result.new_events:
        kind = "New Airbnb booking" if e.is_reservation else "New Airbnb block"
        msgs.append(f":tada: {kind} at *{property_name}*: {e.start} → {e.end}")
    for e in result.changed_events:
        msgs.append(
            f":calendar: Airbnb dates changed at *{property_name}*: "
            f"now {e.start} → {e.end} ({e.uid})"
        )
    for uid in result.canceled_uids:
        msgs.append(f":x: Airbnb event canceled at *{property_name}* ({uid})")
    for c in result.collisions:
        msgs.append(
            f":rotating_light: *DOUBLE-BOOKING RISK* at {property_name}: Airbnb event "
            f"{c.airbnb_uid} overlaps direct reservation #{c.direct_reservation_id}. "
            f"Resolve immediately."
        )
    return msgs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_slack_notify.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
ruff check app/ tests/ && git add -A && git commit -m "feat: slack webhook notifier with sync-result formatting"
```

---

### Task 8: Sync runner + scheduler wiring

**Files:**
- Create: `app/sync/run.py`
- Modify: `app/main.py`
- Test: extend `tests/test_sync_engine.py` (runner test with a fake fetcher)

- [ ] **Step 1: Write the failing test — append to `tests/test_sync_engine.py`**

```python
from app.sync.run import sync_property


async def test_sync_property_fetches_parses_applies(session, prop, monkeypatch):
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
        "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260901\r\nDTEND;VALUE=DATE:20260905\r\n"
        "UID:r1@airbnb.com\r\nSUMMARY:Reserved\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )

    async def fake_fetch(url: str) -> str:
        assert url == "https://airbnb.example/cal.ics"
        return ics

    prop.airbnb_ical_url = "https://airbnb.example/cal.ics"
    sent = []

    async def fake_post(text: str) -> None:
        sent.append(text)

    import app.sync.run as run_mod

    monkeypatch.setattr(run_mod, "fetch_ical", fake_fetch)
    monkeypatch.setattr(run_mod, "post_slack", fake_post)

    await sync_property(session, prop)

    stored = (await session.execute(select(Reservation).where(
        Reservation.external_uid == "r1@airbnb.com"))).scalar_one()
    assert stored.status == "confirmed"
    assert len(sent) == 1 and "New Airbnb booking" in sent[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sync_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sync.run'`

- [ ] **Step 3: Implement `app/sync/run.py`**

```python
import asyncio
import logging

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.ical.parse import parse_airbnb_ical
from app.models import Property
from app.slack.notify import format_sync_messages, post_slack
from app.sync.engine import apply_airbnb_events

logger = logging.getLogger(__name__)


async def fetch_ical(url: str) -> str:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def sync_property(session, prop: Property) -> None:
    text = await fetch_ical(prop.airbnb_ical_url)
    events = parse_airbnb_ical(text)
    result = await apply_airbnb_events(session, prop.id, events)
    await session.commit()
    for msg in format_sync_messages(prop.name, result):
        await post_slack(msg)


async def sync_all_properties() -> None:
    async with SessionLocal() as session:
        props = (
            (await session.execute(select(Property).where(Property.airbnb_ical_url != "")))
            .scalars()
            .all()
        )
    for prop in props:
        try:
            async with SessionLocal() as session:
                await sync_property(session, prop)
        except Exception:
            logger.exception("sync failed for property %s", prop.slug)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(sync_all_properties())
```

- [ ] **Step 4: Wire the scheduler — replace `app/main.py`**

```python
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.config import settings
from app.db import engine
from app.models import Base
from app.routes import health, ical
from app.sync.run import sync_all_properties


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    scheduler = AsyncIOScheduler()
    if settings.sync_interval_minutes > 0:
        scheduler.add_job(
            sync_all_properties, "interval", minutes=settings.sync_interval_minutes
        )
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Ptown Perch Ops", lifespan=lifespan)
app.include_router(health.router)
app.include_router(ical.router)
```

(Tests are unaffected: `ASGITransport` does not run the lifespan, so no scheduler or `create_all` fires during tests.)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS (all tests green)

- [ ] **Step 6: Commit**

```bash
ruff check app/ tests/ && git add -A && git commit -m "feat: sync runner with 10-min scheduler; manual run via python -m app.sync.run"
```

---

### Task 9: Seed script, README, Railway deployment

**Files:**
- Create: `scripts/__init__.py`, `scripts/seed_property.py`, `README.md`, `Procfile`

- [ ] **Step 1: Create `scripts/seed_property.py`** (and empty `scripts/__init__.py`)

```python
"""Seed a property.

Usage: python -m scripts.seed_property <slug> <name> <airbnb_ical_url>
Get the iCal URL from Airbnb: Calendar -> Availability -> Connect another calendar
-> "Export calendar" link for the listing.
"""
import asyncio
import sys

from app.db import SessionLocal, engine
from app.models import Base, Property


async def main(slug: str, name: str, ical_url: str) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        session.add(Property(slug=slug, name=name, airbnb_ical_url=ical_url))
        await session.commit()
    print(f"seeded property '{slug}'")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
```

- [ ] **Step 2: Verify seed + manual sync work locally**

Run:
```bash
.venv/bin/python -m scripts.seed_property perch "Ptown Perch" ""
.venv/bin/python -m app.sync.run
```
Expected: `seeded property 'perch'`; sync run exits cleanly (no properties with an iCal URL yet, so it does nothing). Delete `dev.db` afterward if you want a clean slate.

- [ ] **Step 3: Create `Procfile`**

```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

- [ ] **Step 4: Create `README.md`**

```markdown
# ptown-perch-ops

Ops backend for Ptown Perch (ptownperch.com). Spec:
`docs/superpowers/specs/2026-08-15-str-ops-backend-design.md`.

## Phase 1 (this deploy)
- Pulls the Airbnb iCal every 10 min; new/changed/canceled bookings and
  double-booking collisions are posted to Slack.
- Serves `/ical/{slug}.ics` for Airbnb to import (blocks direct-booking dates).

## Local dev
    python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
    .venv/bin/pytest tests/ -q
    .venv/bin/python -m scripts.seed_property perch "Ptown Perch" "<airbnb ical url>"
    .venv/bin/python -m app.sync.run           # one-shot sync
    .venv/bin/uvicorn app.main:app --reload    # API + scheduler

## Environment
- `DATABASE_URL` — Postgres on Railway (auto-rewritten to +asyncpg), sqlite locally
- `SLACK_WEBHOOK_URL` — incoming-webhook URL for the ops channel
- `SYNC_INTERVAL_MINUTES` — default 10; 0 disables the scheduler
```

- [ ] **Step 5: Commit**

```bash
ruff check app/ scripts/ tests/ && git add -A && git commit -m "feat: seed script, Procfile, README"
```

- [ ] **Step 6: Create the dedicated Railway project and deploy**

Run from the repo root (each `railway` command is interactive-ish; confirm prompts as they come):
```bash
railway init --name ptown-perch-ops
railway add --database postgres
railway up
```
Then in the Railway dashboard (or CLI): on the app service set
- `DATABASE_URL` = reference to the Postgres service's `DATABASE_URL`
- `SLACK_WEBHOOK_URL` = the incoming-webhook URL. **First create the dedicated Slack workspace** (free tier, slack.com/create) with both hosts in it and a `#perch-ops` channel; then create the webhook at api.slack.com/apps → Incoming Webhooks → point it at `#perch-ops`. Phase 2's interactive app installs into this same workspace.
- `SYNC_INTERVAL_MINUTES` = `10`

Expected: deploy succeeds; `GET https://<app-domain>/health` returns `{"status":"ok"}`.

- [ ] **Step 7: Seed production and connect Airbnb both ways**

```bash
railway run python -m scripts.seed_property perch "Ptown Perch" "<airbnb export ical url>"
```
(If `railway run` hits the internal-hostname problem, run the seed locally with `DATABASE_URL` set to the Postgres service's **public** URL instead.)

Then in Airbnb (Calendar → Availability → Connect another calendar):
1. Copy the listing's **Export calendar** URL → that's the seed value above.
2. **Import calendar** → paste `https://<app-domain>/ical/perch.ics`.

Expected within ~15 minutes: Slack messages for each existing Airbnb booking (initial sync treats them all as new — expected on first run), and Airbnb shows the imported calendar as connected.

- [ ] **Step 8: Verify end-to-end and commit any fixups**

- `GET https://<app-domain>/ical/perch.ics` returns a valid empty calendar (no direct bookings yet).
- Slack channel received the initial-sync burst.
- `railway logs` shows the sync job firing every 10 minutes without errors.

```bash
git add -A && git commit -m "chore: deployment fixups" --allow-empty
```

---

## Out of scope for this plan (later phases)

- Phase 2: Alembic baseline, direct-booking inquiry API + quote engine, Slack interactive app (Approve/Decline/Mark-paid buttons), guest emails, hold expiry, suggestions table.
- Phase 3: cleaning/turnover tasks. Phase 4: messaging templates/sequences. Phase 5: pricing suggestions. Phase 6: dashboard.
- The spec's formal `ChannelAdapter` interface is deferred until a second channel actually exists (YAGNI); for now the iCal integration is isolated in `app/ical/` + `app/sync/`, which is the seam a future adapter would replace.
