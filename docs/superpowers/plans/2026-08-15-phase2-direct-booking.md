# Phase 2: Direct Booking Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the guest-facing direct-booking lifecycle — public inquiry, quote, Slack approve/decline, auto-emailed booking link, signed rental contract, manual-payment confirmation, and hold expiry — on top of the deployed Phase 1 calendar-sync backend.

**Architecture:** Extends the existing `Reservation`/`Property` models (no new tables) with nullable Phase 2 columns, introduces Alembic for schema migrations (replacing `create_all`, since production now holds real data), and adds three new integration surfaces: Slack Interactivity (signed request verification + Block Kit buttons), Gmail SMTP (the one automated guest email), and two guest-facing HTML pages served by this backend (no changes to the separate ptownperch.com codebase).

**Tech Stack:** Adds `alembic`, `jinja2`, `python-multipart` to the existing FastAPI + SQLAlchemy async + APScheduler + httpx stack. No new database.

**Spec:** `docs/superpowers/specs/2026-08-15-phase2-direct-booking-design.md`

**Design decisions made while writing this plan (not in the spec's decisions table, but consistent with it):**
- The public inquiry form (`/book`) collects only `check_in`, `check_out`, `guest_name`, `guest_email`, `guest_phone` — all pre-existing `Reservation` columns. `guest_home_address` and `occupant_names` (new columns) are collected later, on the signing page, alongside the signature — this keeps the first-touch form short and avoids a `party_size` column the data model never defined. Maximum occupancy is enforced by the contract's stated limit, not computationally.
- The public inquiry form includes a honeypot field (a hidden input real browsers leave empty; a filled value silently drops the submission) as cheap spam mitigation for an unauthenticated public endpoint. Full bot protection (CAPTCHA, rate limiting) is out of scope — noted as a limitation, not blocked on.

**Repo layout after this plan (additions only):**

```
ptown-perch-ops/
  alembic.ini
  migrations/
    env.py
    script.py.mako
    versions/
      0001_baseline.py
      0002_phase2_booking_fields.py
  app/
    booking/
      __init__.py
      availability.py     # shared overlap-check, extracted from admin.py
      quote.py             # compute_quote()
      contract.py          # render_contract(), has_unresolved_blanks()
      tokens.py             # generate_booking_token()
      hold_expiry.py        # expire_stale_holds()
      mark_paid.py           # shared hold+signed precondition, used by admin API and Slack
    email/
      __init__.py
      gmail.py               # send_email() via smtplib, sync
    slack/
      verify.py             # verify_slack_signature()
      blocks.py              # Block Kit message builders
    routes/
      booking_public.py      # GET/POST /book, GET/POST /book/{token}/sign
      slack_interactive.py    # POST /slack/interactive
    templates/
      book_request.html
      book_thanks.html
      book_guest.html
  tests/
    test_alembic_migrations.py
    test_availability.py
    test_quote.py
    test_contract.py
    test_slack_verify.py
    test_slack_blocks.py
    test_gmail.py
    test_admin_config.py
    test_booking_public.py
    test_slack_interactive.py
    test_hold_expiry.py
    test_mark_paid.py
```

---

### Task 1: Alembic setup + baseline migration

**Files:**
- Create: `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_baseline.py`
- Modify: `pyproject.toml`, `app/main.py`
- Test: `tests/test_alembic_migrations.py`

Production now holds real reservation data. From this task onward, schema changes are Alembic migrations, not `Base.metadata.create_all`. `create_all` stays in `tests/conftest.py`'s ephemeral per-test SQLite engine (unaffected — that's a throwaway DB, not app startup) but is removed from `app/main.py`'s lifespan.

- [ ] **Step 1: Add dependencies to `pyproject.toml`**

In the `dependencies` list, add `"alembic"`, `"jinja2"`, `"python-multipart"` (needed later in this plan for HTML templates and form parsing; adding now keeps one `pip install -e '.[dev]'` for the rest of the plan).

Run:
```bash
cd ~/Projects/ptown-perch-ops && .venv/bin/pip install -e '.[dev]'
```
Expected: alembic, jinja2, python-multipart install successfully.

- [ ] **Step 2: Initialize Alembic structure**

Run:
```bash
.venv/bin/alembic init migrations
```
Expected: creates `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/`.

- [ ] **Step 3: Configure `alembic.ini`**

Open `alembic.ini` and delete the `sqlalchemy.url = driver://user:pass@localhost/dbname` line entirely (env.py will supply the URL from `app.config.settings` instead — never hardcode a DB URL in a committed file).

- [ ] **Step 4: Replace `migrations/env.py`**

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = settings.sqlalchemy_url
    connectable = async_engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 5: Generate the baseline migration**

Run:
```bash
.venv/bin/alembic revision --autogenerate -m "baseline"
```
Expected: creates `migrations/versions/<hash>_baseline.py` containing `create_table` ops for `properties`, `reservations`, and `calendar_blocks` matching current `app/models.py` exactly (including the naming-convention constraint names).

Rename the generated file to `migrations/versions/0001_baseline.py` for a predictable path, and edit its `revision = "..."` value to the literal string `"0001"` (and `down_revision = None`). This makes later migrations easy to reference without hunting for autogenerated hashes.

- [ ] **Step 6: Write the failing test — `tests/test_alembic_migrations.py`**

```python
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_alembic_upgrade_head_succeeds_on_fresh_sqlite(tmp_path, monkeypatch):
    db_path = tmp_path / "migrate_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"},
    )
    assert result.returncode == 0, result.stderr
    assert db_path.exists()
```

- [ ] **Step 7: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_alembic_migrations.py -v`
Expected: FAIL — either the migration doesn't exist yet or the file naming from Step 5 isn't done. Fix Steps 1-5 until this is only failing because you haven't run it, then proceed.

Actually run the migration once yourself first to sanity-check outside the test:
```bash
DATABASE_URL="sqlite+aiosqlite:///$(pwd)/migrate_check.db" .venv/bin/alembic upgrade head
```
Expected: `Running upgrade  -> 0001, baseline`. Then `rm migrate_check.db`.

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_alembic_migrations.py -v`
Expected: PASS (1 passed)

- [ ] **Step 9: Remove `create_all` from `app/main.py`'s lifespan**

Modify `app/main.py` — remove these two lines from `lifespan`:
```python
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```
Remove the now-unused `from app.models import Base` and `engine` imports if `engine` isn't used elsewhere in the file (check: `SessionLocal` is still needed, `engine` is not). The lifespan's first statement becomes the existing `async with SessionLocal() as session: await upsert_configured_property(session)`.

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 66 passed (65 prior + 1 new). Existing tests are unaffected: `tests/conftest.py`'s `session` fixture builds its own throwaway engine with its own `create_all` call, independent of `app/main.py`.

- [ ] **Step 11: Commit**

```bash
.venv/bin/ruff check app/ scripts/ tests/ && git add -A && git commit -m "feat: introduce Alembic; remove create_all from app startup"
```

**Note for the controller (not this task's subagent) — production migration sequencing:** production's `properties`/`reservations`/`calendar_blocks` tables already exist (created by the old `create_all`). Applying `0001_baseline` there must use `alembic stamp 0001` (records the revision without re-running `CREATE TABLE`, which would fail against existing tables), not `alembic upgrade head`. This only needs to happen once, right before Task 2's migration is deployed.

---

### Task 2: Phase 2 schema — extend `Property` and `Reservation`

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/0002_phase2_booking_fields.py`
- Test: `tests/test_models.py` (extend)

- [ ] **Step 1: Write the failing test — append to `tests/test_models.py`**

```python
from datetime import UTC, datetime


async def test_property_booking_config_defaults(session):
    prop = Property(slug="x", name="X")
    session.add(prop)
    await session.flush()
    assert prop.nightly_rate_cents is None
    assert prop.direct_booking_tax_enabled is False
    assert prop.direct_booking_tax_rate_percent == 14.45
    assert prop.contract_template is None


async def test_reservation_phase2_fields_roundtrip(session, prop):
    res = Reservation(
        property_id=prop.id,
        source="direct",
        status="hold",
        check_in=date(2026, 9, 1),
        check_out=date(2026, 9, 5),
        booking_token="tok123",
        guest_home_address="1 Main St",
        occupant_names="Alice, Bob",
        signed_name="Alice Guest",
        signature_image="data:image/png;base64,abc",
        signed_at=datetime(2026, 8, 15, tzinfo=UTC),
        signed_ip="1.2.3.4",
        contract_snapshot="rendered text",
    )
    session.add(res)
    await session.flush()
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.booking_token == "tok123"
    assert stored.signed_at == datetime(2026, 8, 15, tzinfo=UTC)
    assert stored.created_at is not None
    assert stored.hold_warned_at is None


async def test_booking_token_unique_but_multiple_nulls_allowed(session, prop):
    session.add_all(
        [
            Reservation(property_id=prop.id, source="direct", status="hold",
                        check_in=date(2026, 9, 1), check_out=date(2026, 9, 3),
                        booking_token=None),
            Reservation(property_id=prop.id, source="direct", status="hold",
                        check_in=date(2026, 10, 1), check_out=date(2026, 10, 3),
                        booking_token=None),
        ]
    )
    await session.flush()  # two NULL tokens must coexist
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: FAIL — `AttributeError` (fields don't exist yet).

- [ ] **Step 3: Extend `app/models.py`**

Change the imports at the top:
```python
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
```

Add to `Property` (after `airbnb_ical_url`):
```python
    nightly_rate_cents: Mapped[int | None] = mapped_column(Integer, default=None)
    direct_booking_tax_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    direct_booking_tax_rate_percent: Mapped[float] = mapped_column(Float, default=14.45)
    contract_template: Mapped[str | None] = mapped_column(Text, default=None)
```

Add to `Reservation` (after `check_out`):
```python
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    booking_token: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    guest_home_address: Mapped[str | None] = mapped_column(String(255), default=None)
    occupant_names: Mapped[str | None] = mapped_column(Text, default=None)
    signed_name: Mapped[str | None] = mapped_column(String(128), default=None)
    signature_image: Mapped[str | None] = mapped_column(Text, default=None)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    signed_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    contract_snapshot: Mapped[str | None] = mapped_column(Text, default=None)
    hold_warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: PASS (11 passed in this file)

- [ ] **Step 5: Generate and pin the migration**

Run:
```bash
.venv/bin/alembic revision --autogenerate -m "phase2 booking fields"
```
Rename the generated file to `migrations/versions/0002_phase2_booking_fields.py`, set `revision = "0002"`, `down_revision = "0001"`.

Verify the generated `upgrade()` only contains `op.add_column(...)` calls (no drops, no table recreation) — SQLite and Postgres both handle straightforward `ADD COLUMN` natively, no batch mode needed.

- [ ] **Step 6: Extend the migration test — append to `tests/test_alembic_migrations.py`**

```python
def test_alembic_upgrade_head_applies_phase2_columns(tmp_path):
    import os
    import sqlite3

    db_path = tmp_path / "migrate_test2.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reservations)")}
    conn.close()
    assert "booking_token" in columns
    assert "signature_image" in columns
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_alembic_migrations.py -v`
Expected: PASS (2 passed)

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 78 passed (66 prior + 11 in test_models.py's new tests + 1 new migration test = verify exact count from your run; if it differs from 78, trust the actual output, not this number).

- [ ] **Step 9: Commit**

```bash
.venv/bin/ruff check app/ scripts/ tests/ && git add -A && git commit -m "feat: extend Property/Reservation with Phase 2 booking fields"
```

---

### Task 3: Shared availability module (DRY refactor)

**Files:**
- Create: `app/booking/__init__.py`, `app/booking/availability.py`
- Modify: `app/routes/admin.py`
- Test: `tests/test_availability.py`

`app/routes/admin.py` currently has private `_get_property_or_404` and `_blocking_reservations` helpers. Task 9 (the public inquiry page) and Task 10 (Slack Approve re-check) need the same overlap logic. Extract it once, use it from both places, rather than duplicating the query.

- [ ] **Step 1: Write the failing test — `tests/test_availability.py`**

```python
from datetime import date

from app.booking.availability import assert_no_overlap, blocking_reservations
from app.models import Reservation
from fastapi import HTTPException
import pytest


async def test_blocking_reservations_excludes_inquiry_and_canceled(session, prop):
    session.add_all(
        [
            Reservation(property_id=prop.id, source="direct", status="hold",
                        check_in=date(2026, 9, 1), check_out=date(2026, 9, 3)),
            Reservation(property_id=prop.id, source="direct", status="inquiry",
                        check_in=date(2026, 10, 1), check_out=date(2026, 10, 3)),
            Reservation(property_id=prop.id, source="airbnb_ical", status="canceled",
                        external_uid="c@a", check_in=date(2026, 11, 1), check_out=date(2026, 11, 3)),
        ]
    )
    await session.flush()
    result = await blocking_reservations(session, prop.id)
    assert len(result) == 1
    assert result[0].status == "hold"


async def test_assert_no_overlap_raises_409(session, prop):
    session.add(
        Reservation(property_id=prop.id, source="direct", status="confirmed",
                    check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    )
    await session.flush()
    with pytest.raises(HTTPException) as exc_info:
        await assert_no_overlap(session, prop.id, date(2026, 9, 3), date(2026, 9, 7))
    assert exc_info.value.status_code == 409


async def test_assert_no_overlap_passes_when_clear(session, prop):
    await assert_no_overlap(session, prop.id, date(2026, 9, 1), date(2026, 9, 5))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_availability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.booking'`

- [ ] **Step 3: Implement `app/booking/availability.py`** (and empty `app/booking/__init__.py`)

```python
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DIRECT_BLOCKING_STATUSES, Reservation
from app.sync.engine import _overlaps


async def blocking_reservations(session: AsyncSession, property_id: int) -> list[Reservation]:
    return (
        (
            await session.execute(
                select(Reservation).where(
                    Reservation.property_id == property_id,
                    (
                        (Reservation.source == "direct")
                        & Reservation.status.in_(DIRECT_BLOCKING_STATUSES)
                    )
                    | ((Reservation.source == "airbnb_ical") & (Reservation.status != "canceled")),
                )
            )
        )
        .scalars()
        .all()
    )


async def assert_no_overlap(
    session: AsyncSession, property_id: int, check_in: date, check_out: date
) -> None:
    for existing in await blocking_reservations(session, property_id):
        if _overlaps(check_in, check_out, existing.check_in, existing.check_out):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"overlaps existing {existing.source} reservation "
                    f"#{existing.id} ({existing.check_in} → {existing.check_out})"
                ),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_availability.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Refactor `app/routes/admin.py` to use the shared module**

Delete `_blocking_reservations` (the whole function) from `app/routes/admin.py`. Change the import line:
```python
from app.models import DIRECT_BLOCKING_STATUSES, Property, Reservation
```
to:
```python
from app.booking.availability import assert_no_overlap
from app.models import DIRECT_BLOCKING_STATUSES, Property, Reservation
```

In `create_reservation`, replace:
```python
    if payload.status in DIRECT_BLOCKING_STATUSES:
        for existing in await _blocking_reservations(session, prop.id):
            if _overlaps(payload.check_in, payload.check_out, existing.check_in, existing.check_out):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"overlaps existing {existing.source} reservation "
                        f"#{existing.id} ({existing.check_in} → {existing.check_out})"
                    ),
                )
```
with:
```python
    if payload.status in DIRECT_BLOCKING_STATUSES:
        await assert_no_overlap(session, prop.id, payload.check_in, payload.check_out)
```

Remove the now-unused `from app.sync.engine import _overlaps` import line from `admin.py` (it's used only inside `app.booking.availability` now).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — all prior admin tests (`tests/test_admin.py`) still pass unchanged, since behavior is identical, just relocated. Expect 81 passed total.

- [ ] **Step 7: Commit**

```bash
.venv/bin/ruff check app/ scripts/ tests/ && git add -A && git commit -m "refactor: extract shared overlap-check into app.booking.availability"
```

---

### Task 4: Quote computation

**Files:**
- Create: `app/booking/quote.py`
- Test: `tests/test_quote.py`

- [ ] **Step 1: Write the failing test — `tests/test_quote.py`**

```python
from datetime import date

from app.booking.quote import compute_quote


def test_quote_applies_14_percent_discount_no_tax():
    q = compute_quote(
        nightly_rate_cents=20000, check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
        tax_enabled=False, tax_rate_percent=14.45,
    )
    assert q.nights == 4
    assert q.subtotal_cents == 80000
    assert q.discount_cents == 11200  # 14% of 80000
    assert q.tax_cents == 0
    assert q.total_cents == 68800  # 80000 - 11200


def test_quote_with_tax_enabled():
    q = compute_quote(
        nightly_rate_cents=10000, check_in=date(2026, 9, 1), check_out=date(2026, 9, 2),
        tax_enabled=True, tax_rate_percent=14.45,
    )
    assert q.subtotal_cents == 10000
    assert q.discount_cents == 1400
    taxable = 10000 - 1400
    assert q.tax_cents == round(taxable * 0.1445)
    assert q.total_cents == taxable + q.tax_cents


def test_quote_rounds_to_nearest_cent():
    q = compute_quote(
        nightly_rate_cents=333, check_in=date(2026, 9, 1), check_out=date(2026, 9, 4),
        tax_enabled=False, tax_rate_percent=14.45,
    )
    assert q.subtotal_cents == 999
    assert q.discount_cents == round(999 * 0.14)
    assert isinstance(q.total_cents, int)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_quote.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.booking.quote'`

- [ ] **Step 3: Implement `app/booking/quote.py`**

```python
from dataclasses import dataclass
from datetime import date

# Airbnb's guest service fee is roughly 14-16%; passing this back means a
# direct-booking guest pays noticeably less than booking on Airbnb, while the
# operator nets about the same as an Airbnb payout would have given them.
DIRECT_BOOKING_DISCOUNT = 0.14


@dataclass(frozen=True)
class Quote:
    nights: int
    nightly_rate_cents: int
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int


def compute_quote(
    *,
    nightly_rate_cents: int,
    check_in: date,
    check_out: date,
    tax_enabled: bool,
    tax_rate_percent: float,
) -> Quote:
    nights = (check_out - check_in).days
    subtotal_cents = nightly_rate_cents * nights
    discount_cents = round(subtotal_cents * DIRECT_BOOKING_DISCOUNT)
    taxable_cents = subtotal_cents - discount_cents
    tax_cents = round(taxable_cents * tax_rate_percent / 100) if tax_enabled else 0
    return Quote(
        nights=nights,
        nightly_rate_cents=nightly_rate_cents,
        subtotal_cents=subtotal_cents,
        discount_cents=discount_cents,
        tax_cents=tax_cents,
        total_cents=taxable_cents + tax_cents,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_quote.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: quote computation (Airbnb rate minus 14%, tax gated off by default)"
```

---

### Task 5: Contract template rendering

**Files:**
- Create: `app/booking/contract.py`
- Test: `tests/test_contract.py`

- [ ] **Step 1: Write the failing test — `tests/test_contract.py`**

```python
import pytest
from jinja2 import UndefinedError

from app.booking.contract import has_unresolved_blanks, render_contract


def test_detects_unresolved_blanks():
    assert has_unresolved_blanks("Deposit: $[____] due at booking.") is True
    assert has_unresolved_blanks("Guest: {{guest_name}}. All blanks filled.") is False


def test_renders_booking_specific_placeholders():
    template = "Guest {{guest_name}} stays {{check_in}} to {{check_out}} for {{total}}."
    rendered = render_contract(
        template,
        {"guest_name": "Alice", "check_in": "2026-09-01", "check_out": "2026-09-05", "total": "$688.00"},
    )
    assert rendered == "Guest Alice stays 2026-09-01 to 2026-09-05 for $688.00."


def test_missing_placeholder_raises_loudly():
    # A silently-blank field in a signed legal document is worse than a
    # crash the operator sees before the guest ever gets the link.
    with pytest.raises(UndefinedError):
        render_contract("Guest: {{guest_name}}, Total: {{missing_key}}", {"guest_name": "Alice"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.booking.contract'`

- [ ] **Step 3: Implement `app/booking/contract.py`**

```python
import re

from jinja2 import StrictUndefined, Template

# Matches the operator-facing "[____]"-style blanks in the legal template
# text (registration numbers, dollar amounts, etc). Distinct from the
# "{{...}}" booking-specific placeholders Jinja renders per-guest.
UNRESOLVED_BLANK_RE = re.compile(r"\[[^\]{}]*\]")


def has_unresolved_blanks(template: str) -> bool:
    return bool(UNRESOLVED_BLANK_RE.search(template))


def render_contract(template: str, context: dict[str, str]) -> str:
    # StrictUndefined: a missing template variable must be a loud error, not
    # a silently-blank field in a document a guest is about to sign.
    return Template(template, undefined=StrictUndefined).render(**context)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_contract.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: contract template rendering with blank-detection safety gate"
```

---

### Task 6: Slack interactivity support (signature verification + Block Kit builders)

**Files:**
- Create: `app/slack/verify.py`, `app/slack/blocks.py`
- Modify: `app/config.py`
- Test: `tests/test_slack_verify.py`, `tests/test_slack_blocks.py`

- [ ] **Step 1: Add config — modify `app/config.py`**

Add to the `Settings` class, after `admin_password`:
```python
    slack_signing_secret: str = ""
```

- [ ] **Step 2: Write the failing test — `tests/test_slack_verify.py`**

```python
import hashlib
import hmac
import time

import pytest

from app.config import settings
from app.slack.verify import SlackVerificationError, verify_slack_signature


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode()}"
    return "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_passes(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "test-secret")
    body = b"payload=hello"
    timestamp = str(int(time.time()))
    sig = _sign("test-secret", timestamp, body)
    verify_slack_signature(body=body, timestamp=timestamp, signature=sig)  # must not raise


def test_wrong_secret_rejected(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "test-secret")
    body = b"payload=hello"
    timestamp = str(int(time.time()))
    sig = _sign("wrong-secret", timestamp, body)
    with pytest.raises(SlackVerificationError):
        verify_slack_signature(body=body, timestamp=timestamp, signature=sig)


def test_stale_timestamp_rejected(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "test-secret")
    body = b"payload=hello"
    timestamp = str(int(time.time()) - 3600)  # 1 hour old
    sig = _sign("test-secret", timestamp, body)
    with pytest.raises(SlackVerificationError):
        verify_slack_signature(body=body, timestamp=timestamp, signature=sig)


def test_no_secret_configured_rejects(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "")
    with pytest.raises(SlackVerificationError):
        verify_slack_signature(body=b"x", timestamp=str(int(time.time())), signature="v0=x")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slack_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.slack.verify'`

- [ ] **Step 4: Implement `app/slack/verify.py`**

```python
import hashlib
import hmac
import time

from app.config import settings

REPLAY_WINDOW_SECONDS = 60 * 5


class SlackVerificationError(Exception):
    pass


def verify_slack_signature(*, body: bytes, timestamp: str, signature: str) -> None:
    if not settings.slack_signing_secret:
        raise SlackVerificationError("signing secret not configured")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise SlackVerificationError("invalid timestamp") from None
    if abs(time.time() - ts) > REPLAY_WINDOW_SECONDS:
        raise SlackVerificationError("stale request")

    basestring = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(), basestring.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(computed, signature):
        raise SlackVerificationError("signature mismatch")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_slack_verify.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Write the failing test — `tests/test_slack_blocks.py`**

```python
from datetime import date

from app.booking.quote import compute_quote
from app.slack.blocks import build_declined_blocks, build_inquiry_blocks, build_overlap_error_blocks


def _quote():
    return compute_quote(
        nightly_rate_cents=20000, check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
        tax_enabled=False, tax_rate_percent=14.45,
    )


def test_inquiry_blocks_carry_reservation_id_and_actions():
    payload = build_inquiry_blocks(
        property_name="Ptown Perch", reservation_id=42, guest_name="Alice",
        check_in=date(2026, 9, 1), check_out=date(2026, 9, 5), quote=_quote(),
    )
    actions = payload["blocks"][-1]["elements"]
    assert {a["action_id"] for a in actions} == {"booking_approve", "booking_decline"}
    assert all(a["value"] == "42" for a in actions)
    text = payload["blocks"][0]["text"]["text"]
    assert "Alice" in text and "$688.00" in text


def test_declined_blocks_have_no_actions():
    payload = build_declined_blocks(property_name="Ptown Perch", guest_name="Alice")
    assert all(b["type"] != "actions" for b in payload["blocks"])


def test_overlap_error_blocks_include_message():
    payload = build_overlap_error_blocks("dates now conflict with reservation #7")
    assert "dates now conflict" in payload["blocks"][0]["text"]["text"]
```

- [ ] **Step 7: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slack_blocks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.slack.blocks'`

- [ ] **Step 8: Implement `app/slack/blocks.py`**

```python
from datetime import date

from app.booking.quote import Quote


def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


def build_inquiry_blocks(
    *, property_name: str, reservation_id: int, guest_name: str,
    check_in: date, check_out: date, quote: Quote,
) -> dict:
    return {
        "text": f"New direct booking inquiry at {property_name} from {guest_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":email: *New direct booking inquiry* at {property_name}\n"
                        f"{guest_name} — {check_in} → {check_out} ({quote.nights} nights)\n"
                        f"Quote: {_money(quote.total_cents)} "
                        f"(rate {_money(quote.nightly_rate_cents)}/night, "
                        f"discount -{_money(quote.discount_cents)})"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "booking_approve",
                        "value": str(reservation_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Decline"},
                        "style": "danger",
                        "action_id": "booking_decline",
                        "value": str(reservation_id),
                    },
                ],
            },
        ],
    }


def build_approved_blocks(*, property_name: str, guest_name: str, link: str, email_sent: bool) -> dict:
    status_line = (
        f":white_check_mark: Emailed the booking link to {guest_name}."
        if email_sent
        else ":warning: Email send failed — send this link to the guest yourself:"
    )
    return {
        "text": f"Approved: {guest_name} at {property_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":white_check_mark: *Approved* — {guest_name} at {property_name}\n"
                        f"{status_line}\n{link}"
                    ),
                },
            }
        ],
    }


def build_declined_blocks(*, property_name: str, guest_name: str) -> dict:
    return {
        "text": f"Declined: {guest_name} at {property_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":x: *Declined* — {guest_name} at {property_name}",
                },
            }
        ],
    }


def build_overlap_error_blocks(message: str) -> dict:
    return {
        "text": f"Could not approve: {message}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":rotating_light: *Could not approve* — {message}. Decline this inquiry instead.",
                },
            }
        ],
    }
```

- [ ] **Step 9: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_slack_blocks.py -v`
Expected: PASS (3 passed)

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 94 passed (81 prior + 3 quote + 3 contract + 4 verify + 3 blocks).

- [ ] **Step 11: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: Slack request signature verification and Block Kit message builders"
```

---

### Task 7: Gmail SMTP sending

**Files:**
- Create: `app/email/__init__.py`, `app/email/gmail.py`
- Modify: `app/config.py`
- Test: `tests/test_gmail.py`

- [ ] **Step 1: Add config — modify `app/config.py`**

Add to `Settings`, after `slack_signing_secret`:
```python
    gmail_address: str = ""
    gmail_app_password: str = ""
    public_base_url: str = "http://localhost:8000"
```

- [ ] **Step 2: Write the failing test — `tests/test_gmail.py`**

```python
import smtplib
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.email.gmail import EmailSendError, send_email


def test_raises_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "gmail_address", "")
    monkeypatch.setattr(settings, "gmail_app_password", "")
    with pytest.raises(EmailSendError):
        send_email(to="guest@example.com", subject="Hi", body="Body")


def test_sends_via_smtp_with_starttls_and_login(monkeypatch):
    monkeypatch.setattr(settings, "gmail_address", "host@gmail.com")
    monkeypatch.setattr(settings, "gmail_app_password", "app-password")

    mock_smtp = MagicMock()
    mock_smtp.__enter__.return_value = mock_smtp
    with patch("app.email.gmail.smtplib.SMTP", return_value=mock_smtp) as smtp_cls:
        send_email(to="guest@example.com", subject="Hi", body="Body text")

    smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("host@gmail.com", "app-password")
    sent_message = mock_smtp.send_message.call_args[0][0]
    assert sent_message["To"] == "guest@example.com"
    assert sent_message["From"] == "host@gmail.com"
    assert sent_message["Subject"] == "Hi"


def test_smtp_failure_raises_sanitized_error(monkeypatch):
    monkeypatch.setattr(settings, "gmail_address", "host@gmail.com")
    monkeypatch.setattr(settings, "gmail_app_password", "app-password")

    mock_smtp = MagicMock()
    mock_smtp.__enter__.return_value = mock_smtp
    mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
    with patch("app.email.gmail.smtplib.SMTP", return_value=mock_smtp):
        with pytest.raises(EmailSendError):
            send_email(to="guest@example.com", subject="Hi", body="Body")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_gmail.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.email'`

- [ ] **Step 4: Implement `app/email/gmail.py`** (and empty `app/email/__init__.py`)

```python
import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    pass


def send_email(*, to: str, subject: str, body: str) -> None:
    if not (settings.gmail_address and settings.gmail_app_password):
        raise EmailSendError("gmail not configured")

    message = EmailMessage()
    message["From"] = settings.gmail_address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(settings.gmail_address, settings.gmail_app_password)
            smtp.send_message(message)
    except smtplib.SMTPException as exc:
        logger.error("gmail send failed: %s", exc.__class__.__name__)
        raise EmailSendError(f"gmail send failed: {exc.__class__.__name__}") from None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_gmail.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: Gmail SMTP sender for the one automated guest email"
```

---

### Task 8: Admin config endpoints + mark-paid

**Files:**
- Modify: `app/routes/admin.py`
- Test: `tests/test_admin_config.py`

- [ ] **Step 1: Write the failing test — `tests/test_admin_config.py`**

```python
from datetime import UTC, date, datetime

from app.models import Reservation


async def test_patch_config_sets_rate_and_tax(client, monkeypatch):
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


async def test_put_contract_template_rejects_when_blanks_remain(client, monkeypatch):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_admin_config.py -v`
Expected: FAIL — 404s (routes don't exist).

- [ ] **Step 3: Add the endpoints to `app/routes/admin.py`**

Add these imports at the top (extend the existing `from pydantic import BaseModel, Field` line and add):
```python
from app.booking.contract import has_unresolved_blanks
```

Add these models and routes at the end of the file:
```python
class ConfigUpdate(BaseModel):
    nightly_rate_cents: int | None = None
    direct_booking_tax_enabled: bool | None = None
    direct_booking_tax_rate_percent: float | None = None


class ConfigOut(BaseModel):
    nightly_rate_cents: int | None
    direct_booking_tax_enabled: bool
    direct_booking_tax_rate_percent: float


@router.patch("/config", response_model=ConfigOut)
async def update_config(
    payload: ConfigUpdate,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    if payload.nightly_rate_cents is not None:
        prop.nightly_rate_cents = payload.nightly_rate_cents
    if payload.direct_booking_tax_enabled is not None:
        prop.direct_booking_tax_enabled = payload.direct_booking_tax_enabled
    if payload.direct_booking_tax_rate_percent is not None:
        prop.direct_booking_tax_rate_percent = payload.direct_booking_tax_rate_percent

    await session.commit()
    await session.refresh(prop)
    return prop


class ContractTemplateUpdate(BaseModel):
    template: str


@router.put("/contract-template")
async def update_contract_template(
    payload: ContractTemplateUpdate,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)
    prop.contract_template = payload.template
    await session.commit()
    return {"has_unresolved_blanks": has_unresolved_blanks(payload.template)}


@router.post("/reservations/{reservation_id}/mark-paid", response_model=ReservationOut)
async def mark_paid(
    reservation_id: int,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    reservation = (
        await session.execute(
            select(Reservation).where(
                Reservation.id == reservation_id,
                Reservation.property_id == prop.id,
                Reservation.source == "direct",
            )
        )
    ).scalar_one_or_none()
    if reservation is None:
        raise HTTPException(status_code=404, detail="unknown reservation")
    if reservation.status != "hold":
        raise HTTPException(status_code=422, detail="reservation is not in hold status")
    if reservation.signed_at is None:
        raise HTTPException(status_code=422, detail="reservation has no signed contract on file")

    reservation.status = "confirmed"
    await session.commit()
    await session.refresh(reservation)

    try:
        await post_slack(
            f":moneybag: Marked paid & confirmed at *{prop.name}*: {reservation.guest_name} "
            f"{reservation.check_in} → {reservation.check_out} (#{reservation.id})"
        )
    except Exception:
        logger.exception("slack post failed for mark-paid #%s", reservation.id)

    return reservation
```

`ReservationOut`'s existing `model_config = {"from_attributes": True}` already lets it serialize a `Property` for `ConfigOut`'s purposes too — but `ConfigOut` is defined as its own model above, so no cross-dependency; FastAPI will serialize the returned `Property` ORM object against `ConfigOut`'s field set automatically since pydantic models validate from attributes when `response_model` is set on a route returning an ORM instance... actually `ConfigOut` also needs `model_config = {"from_attributes": True}` for that to work. Add it:
```python
class ConfigOut(BaseModel):
    model_config = {"from_attributes": True}

    nightly_rate_cents: int | None
    direct_booking_tax_enabled: bool
    direct_booking_tax_rate_percent: float
```
(Replace the `ConfigOut` class defined above with this corrected version — same fields, added `model_config`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_admin_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 97 passed (94 prior + 3 new).

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: admin endpoints for rate/tax config, contract template, mark-paid"
```

---

### Task 9: Public guest inquiry page

**Files:**
- Create: `app/routes/booking_public.py`, `app/templates/book_request.html`, `app/templates/book_thanks.html`
- Modify: `app/main.py`
- Test: `tests/test_booking_public.py`

- [ ] **Step 1: Write the failing test — `tests/test_booking_public.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_booking_public.py -v`
Expected: FAIL — 404s, route not registered.

- [ ] **Step 3: Create `app/templates/book_request.html`**

```html
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Book Ptown Perch Direct</title></head>
<body style="font-family: sans-serif; max-width: 480px; margin: 2rem auto;">
  <h1>Request to Book — Ptown Perch</h1>
  <p>Booking direct saves you the platform fee. We'll review and get back to you shortly.</p>
  <form method="post" action="/book">
    <p><label>Check-in <input type="date" name="check_in" required></label></p>
    <p><label>Check-out <input type="date" name="check_out" required></label></p>
    <p><label>Name <input type="text" name="guest_name" required></label></p>
    <p><label>Email <input type="email" name="guest_email" required></label></p>
    <p><label>Phone <input type="tel" name="guest_phone"></label></p>
    <input type="text" name="honeypot" style="display:none" tabindex="-1" autocomplete="off">
    <p><button type="submit">Request to Book</button></p>
  </form>
</body>
</html>
```

- [ ] **Step 4: Create `app/templates/book_thanks.html`**

```html
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Request received</title></head>
<body style="font-family: sans-serif; max-width: 480px; margin: 2rem auto;">
  <h1>Thanks!</h1>
  <p>We've received your request and will follow up by email shortly.</p>
</body>
</html>
```

- [ ] **Step 5: Implement `app/routes/booking_public.py`**

```python
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.booking.availability import assert_no_overlap
from app.booking.quote import compute_quote
from app.config import settings
from app.db import get_session
from app.models import Property, Reservation
from app.routes.admin import _get_property_or_404
from app.slack.blocks import build_inquiry_blocks
from app.slack.notify import post_slack_blocks

router = APIRouter(tags=["booking"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/book", response_class=HTMLResponse)
async def book_request_form(request: Request):
    return templates.TemplateResponse(request, "book_request.html")


@router.post("/book")
async def book_request_submit(
    check_in: date = Form(...),
    check_out: date = Form(...),
    guest_name: str = Form(...),
    guest_email: str = Form(...),
    guest_phone: str = Form(""),
    honeypot: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    if honeypot:
        # Silently pretend success so a bot can't tell its submission was
        # dropped; a human never sees or fills this hidden field.
        return RedirectResponse(url="/book/thanks", status_code=303)

    prop = await _get_property_or_404(session, settings.property_slug, for_update=True)
    if prop.nightly_rate_cents is None:
        raise HTTPException(status_code=503, detail="booking not configured yet")
    if check_out <= check_in:
        raise HTTPException(status_code=422, detail="check_out must be after check_in")

    await assert_no_overlap(session, prop.id, check_in, check_out)

    quote = compute_quote(
        nightly_rate_cents=prop.nightly_rate_cents,
        check_in=check_in,
        check_out=check_out,
        tax_enabled=prop.direct_booking_tax_enabled,
        tax_rate_percent=prop.direct_booking_tax_rate_percent,
    )

    reservation = Reservation(
        property_id=prop.id,
        source="direct",
        status="inquiry",
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone or None,
        check_in=check_in,
        check_out=check_out,
    )
    session.add(reservation)
    await session.commit()
    await session.refresh(reservation)

    await post_slack_blocks(
        build_inquiry_blocks(
            property_name=prop.name, reservation_id=reservation.id, guest_name=guest_name,
            check_in=check_in, check_out=check_out, quote=quote,
        )
    )

    return RedirectResponse(url="/book/thanks", status_code=303)


@router.get("/book/thanks", response_class=HTMLResponse)
async def book_thanks(request: Request):
    return templates.TemplateResponse(request, "book_thanks.html")
```

Two things this step depends on that don't exist yet:
- `app.routes.admin._get_property_or_404` must be importable — it currently has no leading-underscore-module-private restriction beyond Python convention, so this works, but note it as a cross-module dependency (acceptable: it's the same pattern already used for `_overlaps` before Task 3's refactor).
- `app.slack.notify.post_slack_blocks` doesn't exist yet — add it now.

Modify `app/slack/notify.py` — add this function after the existing `post_slack`:
```python
async def post_slack_blocks(payload: dict) -> None:
    if not settings.slack_webhook_url:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(settings.slack_webhook_url, json=payload)
        if resp.is_error:
            safe_host = urlsplit(settings.slack_webhook_url).netloc
            logger.error("slack blocks post failed (%s): %s", resp.status_code, safe_host)
            raise RuntimeError(f"slack post failed ({resp.status_code}) to {safe_host}")
```

- [ ] **Step 6: Register the router — modify `app/main.py`**

Change:
```python
from app.routes import admin, health, ical
```
to:
```python
from app.routes import admin, booking_public, health, ical
```
and add, after `app.include_router(admin.router)`:
```python
app.include_router(booking_public.router)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_booking_public.py -v`
Expected: PASS (5 passed)

- [ ] **Step 8: Add a test for `post_slack_blocks` — append to `tests/test_slack_notify.py`**

```python
async def test_post_slack_blocks_noop_without_webhook(monkeypatch):
    from app.config import settings
    from app.slack.notify import post_slack_blocks
    monkeypatch.setattr(settings, "slack_webhook_url", "")
    await post_slack_blocks({"blocks": []})
```

Run: `.venv/bin/pytest tests/test_slack_notify.py -v`
Expected: PASS

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 103 passed (97 prior + 5 booking_public + 1 slack_notify).

- [ ] **Step 10: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: public guest inquiry page (GET/POST /book)"
```

---

### Task 10: Slack interactive endpoint (Approve/Decline)

**Files:**
- Create: `app/routes/slack_interactive.py`
- Modify: `app/main.py`
- Test: `tests/test_slack_interactive.py`

- [ ] **Step 1: Write the failing test — `tests/test_slack_interactive.py`**

```python
import hashlib
import hmac
import json
import time
from datetime import date
from unittest.mock import patch

from sqlalchemy import select

from app.config import settings
from app.models import Reservation


def _signed_headers(body: bytes, secret: str) -> dict:
    timestamp = str(int(time.time()))
    basestring = f"v0:{timestamp}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": sig}


async def _post_interaction(client, action_id: str, reservation_id: int, secret: str):
    payload = {"actions": [{"action_id": action_id, "value": str(reservation_id)}]}
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slack_interactive.py -v`
Expected: FAIL — 404s, route not registered.

- [ ] **Step 3: Implement `app/routes/slack_interactive.py`**

```python
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.booking.availability import assert_no_overlap
from app.booking.tokens import generate_booking_token
from app.config import settings
from app.db import get_session
from app.email.gmail import EmailSendError, send_email
from app.models import Property, Reservation
from app.slack.blocks import build_approved_blocks, build_declined_blocks, build_overlap_error_blocks
from app.slack.verify import SlackVerificationError, verify_slack_signature

logger = logging.getLogger(__name__)

router = APIRouter(tags=["slack"])


@router.post("/slack/interactive")
async def slack_interactive(request: Request, session: AsyncSession = Depends(get_session)):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    try:
        verify_slack_signature(body=body, timestamp=timestamp, signature=signature)
    except SlackVerificationError:
        raise HTTPException(status_code=401, detail="invalid slack signature") from None

    form = dict(pair.split("=", 1) for pair in body.decode().split("&") if "=" in pair)
    from urllib.parse import unquote_plus

    payload = json.loads(unquote_plus(form.get("payload", "{}")))
    action = payload["actions"][0]
    action_id = action["action_id"]
    reservation_id = int(action["value"])

    reservation = (
        await session.execute(
            select(Reservation).where(Reservation.id == reservation_id)
        )
    ).scalar_one_or_none()
    if reservation is None:
        raise HTTPException(status_code=404, detail="unknown reservation")
    prop = (
        await session.execute(select(Property).where(Property.id == reservation.property_id))
    ).scalar_one()

    if action_id == "booking_approve":
        return await _approve(session, reservation, prop)
    if action_id == "booking_decline":
        reservation.status = "canceled"
        await session.commit()
        return build_declined_blocks(property_name=prop.name, guest_name=reservation.guest_name or "guest")

    raise HTTPException(status_code=400, detail="unknown action_id")


async def _approve(session: AsyncSession, reservation: Reservation, prop) -> dict:
    try:
        # Time has passed since the inquiry was created — re-check for a
        # conflict (another approval, a fresh Airbnb sync) before committing
        # to a hold, rather than trusting the state at inquiry time.
        await assert_no_overlap(session, prop.id, reservation.check_in, reservation.check_out)
    except HTTPException as exc:
        return build_overlap_error_blocks(exc.detail)

    reservation.status = "hold"
    reservation.booking_token = generate_booking_token()
    await session.commit()

    link = f"{settings.public_base_url}/book/{reservation.booking_token}"
    subject = f"Your booking request at {prop.name}"
    body_text = (
        f"Hi {reservation.guest_name},\n\n"
        f"Your dates ({reservation.check_in} to {reservation.check_out}) are on hold. "
        f"Please review and sign the rental agreement, and see payment instructions, here:\n\n"
        f"{link}\n\nThanks!"
    )

    email_sent = True
    try:
        # smtplib is blocking; run off the event loop so a slow SMTP
        # handshake doesn't stall other requests (including Slack's own
        # 3-second interactivity timeout on unrelated in-flight requests).
        await asyncio.to_thread(
            send_email, to=reservation.guest_email, subject=subject, body=body_text
        )
    except EmailSendError:
        logger.exception("booking email failed for reservation #%s", reservation.id)
        email_sent = False

    return build_approved_blocks(
        property_name=prop.name, guest_name=reservation.guest_name or "guest",
        link=link, email_sent=email_sent,
    )
```

- [ ] **Step 4: Create `app/booking/tokens.py`**

```python
import secrets


def generate_booking_token() -> str:
    return secrets.token_urlsafe(24)
```

- [ ] **Step 5: Register the router — modify `app/main.py`**

Change:
```python
from app.routes import admin, booking_public, health, ical
```
to:
```python
from app.routes import admin, booking_public, health, ical, slack_interactive
```
and add, after `app.include_router(booking_public.router)`:
```python
app.include_router(slack_interactive.router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_slack_interactive.py -v`
Expected: PASS (6 passed)

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 109 passed (103 prior + 6 new).

- [ ] **Step 8: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: Slack interactive Approve/Decline endpoint with signature verification"
```

---

### Task 11: Guest signing page

**Files:**
- Create: `app/templates/book_guest.html`
- Modify: `app/routes/booking_public.py`
- Test: `tests/test_booking_public.py` (extend)

- [ ] **Step 1: Write the failing test — append to `tests/test_booking_public.py`**

```python
from datetime import UTC, datetime


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_booking_public.py -v`
Expected: FAIL — 404s, routes don't exist.

- [ ] **Step 3: Create `app/templates/book_guest.html`**

```html
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Your Booking — Ptown Perch</title></head>
<body style="font-family: sans-serif; max-width: 640px; margin: 2rem auto;">
  <h1>Your Booking</h1>
  <p>{{ check_in }} → {{ check_out }} — Total due: {{ total }}</p>
  <p>Send payment via Venmo (@ptownperch) or Zelle (bookings@ptownperch.com) for the total above.</p>

  {% if signed %}
    <h2>Signed rental agreement</h2>
    <pre style="white-space: pre-wrap; border: 1px solid #ccc; padding: 1rem;">{{ contract_text }}</pre>
    <p>Signed by {{ signed_name }} on {{ signed_at }}. Thank you!</p>
  {% else %}
    <h2>Rental agreement — please review and sign</h2>
    <pre style="white-space: pre-wrap; border: 1px solid #ccc; padding: 1rem;">{{ contract_text }}</pre>
    <form method="post" action="/book/{{ token }}/sign" id="sign-form">
      <p><label>Full legal name <input type="text" name="signed_name" required></label></p>
      <p><label>Home address <input type="text" name="guest_home_address" required></label></p>
      <p><label>Occupant names (comma-separated) <input type="text" name="occupant_names" required></label></p>
      <p>Signature:<br>
        <canvas id="sig-pad" width="500" height="200" style="border:1px solid #333; touch-action:none;"></canvas><br>
        <button type="button" id="sig-clear">Clear</button>
      </p>
      <input type="hidden" name="signature_image" id="signature_image">
      <p><button type="submit">Sign and Submit</button></p>
    </form>
    <script>
    (function () {
      var canvas = document.getElementById('sig-pad');
      var ctx = canvas.getContext('2d');
      ctx.lineWidth = 2;
      ctx.lineCap = 'round';
      var drawing = false;

      function pos(e) {
        var rect = canvas.getBoundingClientRect();
        var point = e.touches ? e.touches[0] : e;
        return { x: point.clientX - rect.left, y: point.clientY - rect.top };
      }
      function start(e) { drawing = true; var p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); e.preventDefault(); }
      function move(e) { if (!drawing) return; var p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke(); e.preventDefault(); }
      function end() { drawing = false; }

      canvas.addEventListener('mousedown', start);
      canvas.addEventListener('mousemove', move);
      window.addEventListener('mouseup', end);
      canvas.addEventListener('touchstart', start);
      canvas.addEventListener('touchmove', move);
      canvas.addEventListener('touchend', end);
      document.getElementById('sig-clear').addEventListener('click', function () {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      });
      document.getElementById('sign-form').addEventListener('submit', function () {
        document.getElementById('signature_image').value = canvas.toDataURL('image/png');
      });
    })();
    </script>
  {% endif %}
</body>
</html>
```

- [ ] **Step 4: Extend `app/routes/booking_public.py`**

Add these imports at the top:
```python
from datetime import UTC, datetime

from app.booking.contract import has_unresolved_blanks, render_contract
from app.booking.quote import compute_quote
```
(merge with existing imports rather than duplicating; the file already imports `compute_quote` from Task 9 — just add `has_unresolved_blanks`, `render_contract`, and the `datetime`/`UTC` names.)

Append these routes to `app/routes/booking_public.py`:
```python
def _quote_context(prop: Property, reservation: Reservation) -> dict:
    quote = compute_quote(
        nightly_rate_cents=prop.nightly_rate_cents or 0,
        check_in=reservation.check_in,
        check_out=reservation.check_out,
        tax_enabled=prop.direct_booking_tax_enabled,
        tax_rate_percent=prop.direct_booking_tax_rate_percent,
    )
    return {
        "guest_name": reservation.guest_name or "",
        "check_in": str(reservation.check_in),
        "check_out": str(reservation.check_out),
        "total": f"${quote.total_cents / 100:.2f}",
    }


async def _get_reservation_by_token_or_404(session: AsyncSession, token: str) -> Reservation:
    reservation = (
        await session.execute(select(Reservation).where(Reservation.booking_token == token))
    ).scalar_one_or_none()
    if reservation is None:
        raise HTTPException(status_code=404, detail="unknown booking link")
    return reservation


@router.get("/book/{token}", response_class=HTMLResponse)
async def book_guest_page(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
):
    reservation = await _get_reservation_by_token_or_404(session, token)
    prop = (
        await session.execute(select(Property).where(Property.id == reservation.property_id))
    ).scalar_one()

    if reservation.signed_at is not None:
        # contract_snapshot is a frozen copy taken at signing time — render it
        # verbatim regardless of what the live template looks like now. A
        # later template edit (even one that reintroduces a blank) must never
        # change what an already-signed guest sees they agreed to.
        return templates.TemplateResponse(
            request, "book_guest.html",
            {
                "signed": True,
                "contract_text": reservation.contract_snapshot,
                "signed_name": reservation.signed_name,
                "signed_at": reservation.signed_at,
                **_quote_context(prop, reservation),
            },
        )

    # Hard stop: never let a guest reach a signable contract that still has
    # an operator-facing "[____]" blank in it (registration number, deposit
    # amount, etc). This must be enforced here, not just at the admin
    # template-editing endpoint, since the template can be edited at any time
    # after a booking link has already been sent out.
    template = prop.contract_template or ""
    if has_unresolved_blanks(template):
        raise HTTPException(
            status_code=503,
            detail="contract template is incomplete — this booking cannot be signed yet",
        )

    contract_text = render_contract(template, _quote_context(prop, reservation))
    return templates.TemplateResponse(
        request, "book_guest.html",
        {"signed": False, "contract_text": contract_text, "token": token, **_quote_context(prop, reservation)},
    )


@router.post("/book/{token}/sign")
async def book_guest_sign(
    token: str,
    request: Request,
    signed_name: str = Form(...),
    guest_home_address: str = Form(...),
    occupant_names: str = Form(...),
    signature_image: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    reservation = await _get_reservation_by_token_or_404(session, token)
    if reservation.signed_at is not None:
        raise HTTPException(status_code=409, detail="already signed")

    prop = (
        await session.execute(select(Property).where(Property.id == reservation.property_id))
    ).scalar_one()
    template = prop.contract_template or ""
    if has_unresolved_blanks(template):
        # Same hard stop as the GET page — a direct POST must not be able to
        # bypass the blank-check just because it skipped the form render.
        raise HTTPException(
            status_code=503,
            detail="contract template is incomplete — this booking cannot be signed yet",
        )
    contract_text = render_contract(template, _quote_context(prop, reservation))

    reservation.signed_name = signed_name
    reservation.guest_home_address = guest_home_address
    reservation.occupant_names = occupant_names
    reservation.signature_image = signature_image
    reservation.signed_at = datetime.now(UTC)
    reservation.signed_ip = request.client.host if request.client else None
    reservation.contract_snapshot = contract_text
    await session.commit()

    return RedirectResponse(url=f"/book/{token}", status_code=303)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_booking_public.py -v`
Expected: PASS (12 passed in this file)

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 116 passed (109 prior + 7 new: 404, unsigned form, signed confirmation, sign success, already-signed rejection, blank-template refused on GET+POST, already-signed page unaffected by a later blank).

- [ ] **Step 7: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: guest signing page with draw-pad signature capture"
```

---

### Task 12: Hold expiry scheduler job

**Files:**
- Create: `app/booking/hold_expiry.py`
- Modify: `app/config.py`, `app/main.py`
- Test: `tests/test_hold_expiry.py`

- [ ] **Step 1: Add config — modify `app/config.py`**

Add to `Settings`, after `public_base_url`:
```python
    hold_expiry_hours: int = 48
    hold_warning_hours: int = 36
```

- [ ] **Step 2: Write the failing test — `tests/test_hold_expiry.py`**

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.booking.hold_expiry import expire_stale_holds
from app.config import settings
from app.models import Reservation


def _hold(prop, hours_old, **kwargs):
    return Reservation(
        property_id=prop.id, source="direct", status="hold",
        guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
        created_at=datetime.now(UTC) - timedelta(hours=hours_old),
        **kwargs,
    )


from datetime import date


async def test_fresh_hold_untouched(session, prop):
    res = _hold(prop, hours_old=1)
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "hold"
    assert res.hold_warned_at is None


async def test_hold_past_warning_threshold_gets_warned_once(session, prop, monkeypatch):
    sent = []
    monkeypatch.setattr("app.booking.hold_expiry.post_slack", lambda text: sent.append(text))
    res = _hold(prop, hours_old=settings.hold_warning_hours + 1)
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "hold"
    assert res.hold_warned_at is not None
    assert len(sent) == 1

    # second run within the same window must not warn again
    await expire_stale_holds(session)
    assert len(sent) == 1


async def test_hold_past_expiry_threshold_released(session, prop, monkeypatch):
    monkeypatch.setattr("app.booking.hold_expiry.post_slack", lambda text: None)
    res = _hold(
        prop, hours_old=settings.hold_expiry_hours + 1,
        hold_warned_at=datetime.now(UTC) - timedelta(hours=13),
    )
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "canceled"


async def test_signed_hold_still_expires_if_unpaid(session, prop, monkeypatch):
    # Signing doesn't confirm — an unpaid signed hold should still expire.
    monkeypatch.setattr("app.booking.hold_expiry.post_slack", lambda text: None)
    res = _hold(
        prop, hours_old=settings.hold_expiry_hours + 1, signed_at=datetime.now(UTC),
        hold_warned_at=datetime.now(UTC) - timedelta(hours=13),
    )
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "canceled"


async def test_confirmed_reservation_never_touched(session, prop, monkeypatch):
    monkeypatch.setattr("app.booking.hold_expiry.post_slack", lambda text: None)
    res = Reservation(
        property_id=prop.id, source="direct", status="confirmed",
        guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
        created_at=datetime.now(UTC) - timedelta(hours=1000),
    )
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "confirmed"
```

Note: `post_slack` in the real module takes `text: str` and is `async def`; monkeypatching it with a plain (non-async) lambda here works only if `hold_expiry.py` calls it with `await`, which would fail against a lambda. Use an async-compatible patch instead — replace each `monkeypatch.setattr("app.booking.hold_expiry.post_slack", lambda text: ...)` line with:
```python
    async def fake_post_slack(text):
        sent.append(text)
    monkeypatch.setattr("app.booking.hold_expiry.post_slack", fake_post_slack)
```
(and the no-op variants with `async def fake_post_slack(text): pass`). Apply this correction to all four tests above before running them.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_hold_expiry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.booking.hold_expiry'`

- [ ] **Step 4: Implement `app/booking/hold_expiry.py`**

```python
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Reservation
from app.slack.notify import post_slack

logger = logging.getLogger(__name__)


async def expire_stale_holds(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    warn_cutoff = now - timedelta(hours=settings.hold_warning_hours)
    expire_cutoff = now - timedelta(hours=settings.hold_expiry_hours)

    holds = (
        (await session.execute(select(Reservation).where(Reservation.status == "hold")))
        .scalars()
        .all()
    )

    for res in holds:
        if res.created_at <= expire_cutoff:
            res.status = "canceled"
            await post_slack(
                f":alarm_clock: Hold expired and released: {res.guest_name} "
                f"{res.check_in} → {res.check_out} (#{res.id})"
            )
        elif res.created_at <= warn_cutoff and res.hold_warned_at is None:
            res.hold_warned_at = now
            await post_slack(
                f":hourglass_flowing_sand: Hold will expire soon unless paid: "
                f"{res.guest_name} {res.check_in} → {res.check_out} (#{res.id})"
            )

    await session.commit()


async def expire_stale_holds_all_properties() -> None:
    async with SessionLocal() as session:
        try:
            await expire_stale_holds(session)
        except Exception:
            logger.exception("hold expiry sweep failed")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_hold_expiry.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Wire the scheduler job — modify `app/main.py`**

Change:
```python
from app.routes import admin, booking_public, health, ical, slack_interactive
from app.sync.run import sync_all_properties
```
to:
```python
from app.booking.hold_expiry import expire_stale_holds_all_properties
from app.routes import admin, booking_public, health, ical, slack_interactive
from app.sync.run import sync_all_properties
```

In `lifespan`, after the existing `sync_all_properties` `scheduler.add_job(...)` block (still inside `if settings.sync_interval_minutes > 0:`), add a second, unconditional job registration:
```python
    scheduler.add_job(
        expire_stale_holds_all_properties, "interval", minutes=15,
        next_run_time=datetime.now(UTC),
    )
```
Place this line right before `scheduler.start()`, after the existing `if settings.sync_interval_minutes > 0:` block closes (so hold expiry runs regardless of whether Airbnb sync is enabled — they're independent concerns).

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 121 passed (116 prior + 5 new).

- [ ] **Step 8: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: hold-expiry scheduler (warn at 36h, release at 48h)"
```

---

### Task 14: Slack Mark Paid button

**Files:**
- Create: `app/booking/mark_paid.py`
- Modify: `app/routes/admin.py`, `app/slack/blocks.py`, `app/routes/booking_public.py`, `app/routes/slack_interactive.py`
- Test: `tests/test_mark_paid.py`, extend `tests/test_slack_blocks.py`, `tests/test_booking_public.py`, `tests/test_slack_interactive.py`

The spec's lifecycle says the operator triggers Mark Paid via "Slack button or admin API." Task 8 only built the admin API half. This task adds the Slack button — posted once the guest signs, since Mark Paid only becomes meaningful after signing — and factors the hold+signed precondition into one shared function so the two entry points can't drift apart.

- [ ] **Step 1: Write the failing test — `tests/test_mark_paid.py`**

```python
from datetime import UTC, date, datetime

import pytest

from app.booking.mark_paid import MarkPaidError, mark_paid
from app.models import Reservation


async def test_mark_paid_requires_hold_status(session, prop):
    res = Reservation(property_id=prop.id, source="direct", status="inquiry",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()
    with pytest.raises(MarkPaidError):
        await mark_paid(session, res)


async def test_mark_paid_requires_signature(session, prop):
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5))
    session.add(res)
    await session.flush()
    with pytest.raises(MarkPaidError):
        await mark_paid(session, res)


async def test_mark_paid_success(session, prop):
    res = Reservation(property_id=prop.id, source="direct", status="hold",
                       check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
                       signed_at=datetime(2026, 8, 15, tzinfo=UTC))
    session.add(res)
    await session.flush()
    await mark_paid(session, res)
    assert res.status == "confirmed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mark_paid.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.booking.mark_paid'`

- [ ] **Step 3: Implement `app/booking/mark_paid.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reservation


class MarkPaidError(Exception):
    pass


async def mark_paid(session: AsyncSession, reservation: Reservation) -> None:
    if reservation.status != "hold":
        raise MarkPaidError("reservation is not in hold status")
    if reservation.signed_at is None:
        raise MarkPaidError("reservation has no signed contract on file")
    reservation.status = "confirmed"
    await session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mark_paid.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Refactor `app/routes/admin.py`'s mark-paid endpoint to use the shared helper**

Add to the imports:
```python
from app.booking.mark_paid import MarkPaidError
from app.booking.mark_paid import mark_paid as apply_mark_paid
```

Replace the entire `mark_paid` route function (added in Task 8) with:
```python
@router.post("/reservations/{reservation_id}/mark-paid", response_model=ReservationOut)
async def mark_paid_endpoint(
    reservation_id: int,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    reservation = (
        await session.execute(
            select(Reservation).where(
                Reservation.id == reservation_id,
                Reservation.property_id == prop.id,
                Reservation.source == "direct",
            )
        )
    ).scalar_one_or_none()
    if reservation is None:
        raise HTTPException(status_code=404, detail="unknown reservation")

    try:
        await apply_mark_paid(session, reservation)
    except MarkPaidError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await session.refresh(reservation)

    try:
        await post_slack(
            f":moneybag: Marked paid & confirmed at *{prop.name}*: {reservation.guest_name} "
            f"{reservation.check_in} → {reservation.check_out} (#{reservation.id})"
        )
    except Exception:
        logger.exception("slack post failed for mark-paid #%s", reservation.id)

    return reservation
```
(The route function is renamed from `mark_paid` to `mark_paid_endpoint` to avoid shadowing the imported `mark_paid` helper — the URL path `/admin/reservations/{id}/mark-paid` is unchanged, so Task 8's `tests/test_admin_config.py::test_mark_paid_requires_signature` still passes unmodified.)

- [ ] **Step 6: Run test to verify the existing admin test still passes**

Run: `.venv/bin/pytest tests/test_admin_config.py -v`
Expected: PASS (3 passed, unchanged from Task 8)

- [ ] **Step 7: Write the failing test — extend `tests/test_slack_blocks.py`**

```python
from app.slack.blocks import build_mark_paid_confirmed_blocks, build_mark_paid_error_blocks, build_signed_blocks


def test_signed_blocks_have_mark_paid_button():
    payload = build_signed_blocks(
        property_name="Ptown Perch", guest_name="Alice", reservation_id=9, total="$688.00"
    )
    actions = payload["blocks"][-1]["elements"]
    assert actions[0]["action_id"] == "booking_mark_paid"
    assert actions[0]["value"] == "9"


def test_mark_paid_confirmed_and_error_blocks():
    ok = build_mark_paid_confirmed_blocks(property_name="Ptown Perch", guest_name="Alice")
    assert "Confirmed" in ok["blocks"][0]["text"]["text"]
    err = build_mark_paid_error_blocks("reservation has no signed contract on file")
    assert "no signed contract" in err["blocks"][0]["text"]["text"]
```

- [ ] **Step 8: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slack_blocks.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_signed_blocks'`

- [ ] **Step 9: Add to `app/slack/blocks.py`**

Append:
```python
def build_signed_blocks(*, property_name: str, guest_name: str, reservation_id: int, total: str) -> dict:
    return {
        "text": f"{guest_name} signed the agreement at {property_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":pencil2: *{guest_name} signed the rental agreement* at {property_name}\n"
                        f"Total due: {total}. Tap Mark Paid once you see the Venmo/Zelle payment land."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Mark Paid"},
                        "style": "primary",
                        "action_id": "booking_mark_paid",
                        "value": str(reservation_id),
                    }
                ],
            },
        ],
    }


def build_mark_paid_confirmed_blocks(*, property_name: str, guest_name: str) -> dict:
    return {
        "text": f"Confirmed: {guest_name} at {property_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":tada: *Confirmed* — {guest_name} at {property_name} is paid and confirmed.",
                },
            }
        ],
    }


def build_mark_paid_error_blocks(message: str) -> dict:
    return {
        "text": f"Could not mark paid: {message}",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":warning: *Could not mark paid* — {message}"},
            }
        ],
    }
```

- [ ] **Step 10: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_slack_blocks.py -v`
Expected: PASS (5 passed in this file)

- [ ] **Step 11: Write the failing test — extend `tests/test_booking_public.py`**

```python
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
```

- [ ] **Step 12: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_booking_public.py -v`
Expected: FAIL — no Slack post happens yet (`sent` stays empty, assertion fails).

- [ ] **Step 13: Modify `app/routes/booking_public.py`**

Add `import logging` near the top and `logger = logging.getLogger(__name__)` right after the existing `router = APIRouter(...)` line.

Change the `from app.slack.blocks import build_inquiry_blocks` import to:
```python
from app.slack.blocks import build_inquiry_blocks, build_signed_blocks
```

In `book_guest_sign`, insert this block after `await session.commit()` and before `return RedirectResponse(...)`:
```python
    try:
        await post_slack_blocks(
            build_signed_blocks(
                property_name=prop.name,
                guest_name=reservation.guest_name or "guest",
                reservation_id=reservation.id,
                total=_quote_context(prop, reservation)["total"],
            )
        )
    except Exception:
        logger.exception("slack post failed for signed reservation #%s", reservation.id)
```

- [ ] **Step 14: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_booking_public.py -v`
Expected: PASS (13 passed in this file)

- [ ] **Step 15: Write the failing test — extend `tests/test_slack_interactive.py`**

Change the top import from `from datetime import date` to `from datetime import UTC, date, datetime`, then append:
```python
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
```

- [ ] **Step 16: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slack_interactive.py -v`
Expected: FAIL — `HTTPException 400 unknown action_id` (handler doesn't exist yet).

- [ ] **Step 17: Modify `app/routes/slack_interactive.py`**

Add to the imports:
```python
from app.booking.mark_paid import MarkPaidError
from app.booking.mark_paid import mark_paid as apply_mark_paid
```
Change the `from app.slack.blocks import ...` line to:
```python
from app.slack.blocks import (
    build_approved_blocks,
    build_declined_blocks,
    build_mark_paid_confirmed_blocks,
    build_mark_paid_error_blocks,
    build_overlap_error_blocks,
)
```

In `slack_interactive`, add a third branch before the final `raise HTTPException(status_code=400, ...)` line:
```python
    if action_id == "booking_mark_paid":
        try:
            await apply_mark_paid(session, reservation)
        except MarkPaidError as exc:
            return build_mark_paid_error_blocks(str(exc))
        return build_mark_paid_confirmed_blocks(
            property_name=prop.name, guest_name=reservation.guest_name or "guest"
        )
```

- [ ] **Step 18: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_slack_interactive.py -v`
Expected: PASS (8 passed in this file)

- [ ] **Step 19: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 129 passed (121 prior + 3 mark_paid + 2 slack_blocks + 1 booking_public + 2 slack_interactive).

- [ ] **Step 20: Commit**

```bash
.venv/bin/ruff check app/ tests/ && git add -A && git commit -m "feat: Slack Mark Paid button, posted when the guest signs"
```

---

### Task 15: README, env vars, and final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update `README.md`**

Replace the "## Phase 1 (current)" section header and content with:
```markdown
## Phase 1
- Pulls the Airbnb iCal every 10 min; new/changed/canceled bookings and
  double-booking collisions are posted to Slack.
- Serves `/ical/{slug}.ics` for Airbnb to import (blocks direct-booking dates).
- Admin API (`/admin/*`, HTTP Basic auth) for manually recording, listing, and
  canceling direct bookings — no signed contract, used for overrides.

## Phase 2 (current)
- Public guest inquiry page (`/book`) quotes a price (Airbnb rate minus 14%,
  tax gated off by default) and posts an approval request to Slack.
- Approving in Slack auto-emails the guest a link (Gmail SMTP) to a page
  where they sign the rental agreement (draw-pad signature) and see payment
  instructions.
- `mark-paid` (Slack or admin API) requires a signature on file before
  confirming — never confirms an unsigned booking.
- Unpaid holds auto-expire after 48h (warned at 36h).
- Schema is Alembic-managed from this phase onward — see "Deploy" below.
```

Add to the "## Environment" list:
```markdown
- `SLACK_SIGNING_SECRET` — Perch Ops Slack app's signing secret, for verifying
  interactive button payloads
- `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` — sends the one automated guest email
  (booking link); needs 2-Step Verification enabled on the Gmail account to
  generate an app password
- `PUBLIC_BASE_URL` — this service's public URL, used to build guest booking
  links (e.g. `https://app-production-b8f3.up.railway.app`)
- `HOLD_EXPIRY_HOURS` / `HOLD_WARNING_HOURS` — default 48 / 36
```

Replace the "## Deploy (Railway, dedicated project)" section with:
```markdown
## Deploy (Railway, dedicated project)
Deployed via Procfile (uvicorn web process; scheduler runs in-process).

**Migrations (from Phase 2 onward):** this repo uses Alembic. After merging a
migration, apply it to production yourself — it does not auto-run on deploy:
    export DATABASE_URL=<Postgres service's public proxy URL, not the
      .railway.internal one — alembic runs from your laptop, outside
      Railway's private network>
    .venv/bin/alembic upgrade head
The very first Alembic migration (`0001_baseline`) must be applied with
`alembic stamp 0001` instead of `upgrade head`, since production's tables
already existed (created by the old `create_all`) before Alembic was
introduced — `stamp` records the revision without re-running `CREATE TABLE`.
Every migration after that uses the normal `upgrade head`.

**Before real guests use `/book`:** set `nightly_rate_cents` via
`PATCH /admin/config`, and set the contract template via
`PUT /admin/contract-template` with every `[____]`-style blank filled in —
the guest page refuses to render while any remain (checked via
`GET /admin/status` conceptually; the contract-template endpoint's response
reports `has_unresolved_blanks`).
```

- [ ] **Step 2: Run the full suite one final time**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — 129 passed, 0 failed. (If the actual count differs from this or any other task's stated number, trust the real `pytest` output — these counts are a sanity check, not a hard gate.)

Run: `.venv/bin/ruff check app/ scripts/ tests/`
Expected: All checks passed.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "docs: Phase 2 README updates (env vars, deploy/migration sequencing)"
```

---

## Out of scope for this plan (controller steps, after all tasks land)

- Applying `0001_baseline` (via `stamp`) and `0002_phase2_booking_fields` (via `upgrade head`) to the production Railway Postgres.
- Creating the Slack app's Interactivity configuration (Request URL = `https://<domain>/slack/interactive`) and fetching the signing secret into `SLACK_SIGNING_SECRET`.
- Enabling 2-Step Verification on the sending Gmail account and generating the app password for `GMAIL_APP_PASSWORD`.
- Setting `PUBLIC_BASE_URL`, `HOLD_EXPIRY_HOURS`/`HOLD_WARNING_HOURS` (if overriding defaults) on Railway.
- Filling in the real contract template values (registration numbers, deposit amount, cancellation tiers, pet/parking policy) via `PUT /admin/contract-template`, and setting the real `nightly_rate_cents` via `PATCH /admin/config` — required before `/book` can be used by a real guest.
- Manual browser QA of the signature pad (canvas drawing is not something pytest can verify) once deployed.
- A Massachusetts attorney's review of the contract text — independent of when the code ships.
