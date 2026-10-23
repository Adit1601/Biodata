#!/usr/bin/env python3
"""
backup.py — Dump the PostgreSQL database (or SQLite) to a timestamped file.

Usage:
    python3 backup.py                  # dumps to ./backups/YYYY-MM-DDTHH-MM-SS.sql (Postgres)
                                       #                    YYYY-MM-DDTHH-MM-SS.db  (SQLite)
    python3 backup.py --out /some/dir  # custom output directory
"""

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH      = Path(os.getenv("DB_PATH", "./biodata.db")).expanduser()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "backups"),
                        help="Output directory (default: ./backups)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

    if DATABASE_URL:
        # ── PostgreSQL: use pg_dump ───────────────────────────────────────────
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            print("ERROR: pg_dump not found. Install postgresql-client.")
            sys.exit(1)

        out_file = out_dir / f"{ts}.sql"
        env = os.environ.copy()
        env["DATABASE_URL"] = DATABASE_URL

        result = subprocess.run(
            [pg_dump, DATABASE_URL, "--no-password", "--format=plain"],
            capture_output=True, env=env
        )
        if result.returncode != 0:
            print("ERROR: pg_dump failed:")
            print(result.stderr.decode())
            sys.exit(1)

        out_file.write_bytes(result.stdout)
        print(f"PostgreSQL backup → {out_file}  ({out_file.stat().st_size // 1024} KB)")

    else:
        # ── SQLite: plain file copy ───────────────────────────────────────────
        if not DB_PATH.exists():
            print(f"ERROR: SQLite DB not found at {DB_PATH}")
            sys.exit(1)

        out_file = out_dir / f"{ts}.db"
        shutil.copy2(DB_PATH, out_file)
        print(f"SQLite backup → {out_file}  ({out_file.stat().st_size // 1024} KB)")

    print("Done.")


if __name__ == "__main__":
    main()
