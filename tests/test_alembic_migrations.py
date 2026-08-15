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
