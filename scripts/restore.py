#!/usr/bin/env python3
import argparse
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Restaura backup SQLite de forma atômica")
    parser.add_argument("backup", type=Path)
    parser.add_argument("--database", type=Path, default=Path("data/tasks.db"))
    parser.add_argument("--force", action="store_true", help="confirma substituição do banco")
    args = parser.parse_args()
    if not args.force:
        raise SystemExit("Use --force após parar a aplicação e confirmar a restauração")
    if not args.backup.is_file():
        raise SystemExit(f"Backup não encontrado: {args.backup}")
    with sqlite3.connect(f"file:{args.backup}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("Backup inválido")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=args.database.parent, prefix=".restore-")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(args.backup, temporary)
        temporary.chmod(0o600)
        os.replace(temporary, args.database)
    finally:
        temporary.unlink(missing_ok=True)
    print(args.database)


if __name__ == "__main__":
    main()
