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
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert db_path.exists()


def test_alembic_upgrade_head_applies_phase2_columns(tmp_path):
    import os
    import sqlite3

    db_path = tmp_path / "migrate_test2.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reservations)")}
    conn.close()
    assert "booking_token" in columns
    assert "signature_image" in columns


def test_alembic_upgrade_head_creates_daily_rates_table(tmp_path):
    import os
    import sqlite3

    db_path = tmp_path / "migrate_test3.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_rates)")}
    conn.close()
    assert columns == {"id", "property_id", "date", "rate_cents"}
