#!/usr/bin/env python3
import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria backup consistente do SQLite")
    parser.add_argument("--database", type=Path, default=Path("data/tasks.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("backups"))
    args = parser.parse_args()
    if not args.database.is_file():
        raise SystemExit(f"Banco não encontrado: {args.database}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = args.output_dir / f"tasks-{timestamp}.db"
    with sqlite3.connect(args.database) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            target.unlink(missing_ok=True)
            raise SystemExit("Backup falhou na verificação de integridade")
    target.chmod(0o600)
    print(target)


if __name__ == "__main__":
    main()
