import sqlite3
import sys
from pathlib import Path

import pytest

from app.database import Database
from scripts import backup, restore


def test_backup_and_restore_preserve_tasks_and_idempotency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    output_dir = tmp_path / "backups"
    database = Database(source)
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO tasks (title, completed) VALUES (?, ?)", ("backup", False)
        )
        task_id = cursor.lastrowid
        connection.execute(
            """INSERT INTO idempotency_keys (key, request_hash, response_json)
            VALUES (?, ?, ?)""",
            ("backup-key", "hash", f'{{"id":{task_id}}}'),
        )

    monkeypatch.setattr(
        sys,
        "argv",
        ["backup.py", "--database", str(source), "--output-dir", str(output_dir)],
    )
    backup.main()
    backup_path = Path(capsys.readouterr().out.strip())

    monkeypatch.setattr(
        sys,
        "argv",
        ["restore.py", str(backup_path), "--database", str(restored), "--force"],
    )
    restore.main()
    capsys.readouterr()

    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT title FROM tasks").fetchone() == ("backup",)
        assert connection.execute("SELECT key FROM idempotency_keys").fetchone() == (
            "backup-key",
        )
