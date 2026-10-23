#!/usr/bin/env python3
"""
migrate_to_postgres.py — Copy all data from SQLite → PostgreSQL.

Run this ONCE when you are ready to switch from SQLite to Postgres:

  1.  Set DATABASE_URL in .env to your Postgres connection string.
      Keep DB_PATH pointing at the existing SQLite file.
  2.  Run:  python3 migrate_to_postgres.py
  3.  Verify the row counts printed at the end.
  4.  Restart the server — it will now use Postgres automatically.

The script is idempotent for the schema (CREATE IF NOT EXISTS) but
will fail on duplicate data if run twice.  Use --truncate to clear
Postgres tables before re-running.
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import psycopg2                    # type: ignore
import psycopg2.extras             # type: ignore
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

SQLITE_PATH  = Path(os.getenv("DB_PATH", "./biodata.db")).expanduser()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


# ─────────────────────────────────────────────────────────────────────────────

def sqlite_rows(cur, table: str):
    cur.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def pg_insert(pg_cur, table: str, cols, rows, returning_id: bool = False):
    if not rows:
        return
    ph    = ",".join(["%s"] * len(cols))
    sql   = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})"
    if returning_id:
        sql += " ON CONFLICT DO NOTHING"
    else:
        sql += " ON CONFLICT DO NOTHING"
    psycopg2.extras.execute_batch(pg_cur, sql, rows, page_size=200)


def reset_sequences(pg_cur):
    """Reset SERIAL sequences so new inserts don't collide with migrated IDs."""
    tables = ["users", "candidates", "labels", "views"]
    for t in tables:
        pg_cur.execute(f"""
            SELECT setval(pg_get_serial_sequence('{t}', 'id'),
                          COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)
        """)


# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--truncate", action="store_true",
                        help="Truncate all Postgres tables before inserting")
    args = parser.parse_args()

    if not SQLITE_PATH.exists():
        print(f"ERROR: SQLite DB not found at {SQLITE_PATH}")
        sys.exit(1)
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set in .env")
        sys.exit(1)

    print(f"\nSource : {SQLITE_PATH}")
    print(f"Target : {DATABASE_URL[:40]}…")

    # ── Open connections ──────────────────────────────────────────────────────
    sl  = sqlite3.connect(str(SQLITE_PATH))
    sl.row_factory = sqlite3.Row
    sl_cur = sl.cursor()

    pg  = psycopg2.connect(DATABASE_URL)
    pg.autocommit = False
    pg_cur = pg.cursor()

    # ── Create schema in Postgres ─────────────────────────────────────────────
    print("\nCreating schema …")
    # Import the Postgres schema string from database.py
    import importlib.util, types
    spec = importlib.util.spec_from_file_location("db", ROOT / "database.py")
    db_mod = types.ModuleType("db")
    # We only need the schema string, not the full module import chain
    schema_src = (ROOT / "database.py").read_text()
    schema_start = schema_src.find('_SCHEMA_POSTGRES = """') + len('_SCHEMA_POSTGRES = """')
    schema_end   = schema_src.find('"""', schema_start)
    pg_schema    = schema_src[schema_start:schema_end]
    pg_cur.execute(pg_schema)
    pg.commit()
    print("  Schema OK")

    # ── Optionally truncate ───────────────────────────────────────────────────
    if args.truncate:
        print("\nTruncating Postgres tables …")
        pg_cur.execute("""
            TRUNCATE view_candidates, candidate_labels, views, labels,
                     candidates, users RESTART IDENTITY CASCADE
        """)
        pg.commit()
        print("  Done")

    # ── Copy tables in FK-dependency order ───────────────────────────────────
    tables_ordered = [
        "users",
        "candidates",
        "labels",
        "candidate_labels",
        "views",
        "view_candidates",
    ]

    print("\nCopying data …")
    for table in tables_ordered:
        cols, rows = sqlite_rows(sl_cur, table)
        pg_insert(pg_cur, table, cols, rows)
        pg.commit()
        print(f"  {table:20s} {len(rows):5d} rows")

    # ── Reset sequences ───────────────────────────────────────────────────────
    print("\nResetting sequences …")
    reset_sequences(pg_cur)
    pg.commit()

    # ── Verify ────────────────────────────────────────────────────────────────
    print("\nVerification:")
    for table in tables_ordered:
        sl_cur.execute(f"SELECT COUNT(*) FROM {table}")
        sl_n = sl_cur.fetchone()[0]
        pg_cur.execute(f"SELECT COUNT(*) FROM {table}")
        pg_n = pg_cur.fetchone()[0]
        match = "✓" if sl_n == pg_n else "✗ MISMATCH"
        print(f"  {table:20s}  SQLite={sl_n:5d}  Postgres={pg_n:5d}  {match}")

    sl.close()
    pg_cur.close()
    pg.close()
    print("\nDone. Set DATABASE_URL in .env and restart the server.\n")


if __name__ == "__main__":
    main()
