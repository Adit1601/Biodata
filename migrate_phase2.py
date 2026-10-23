#!/usr/bin/env python3
"""
migrate_phase2.py — One-shot migration from Phase 1 to Phase 2 schema.

NOTE: This script operates directly on the SQLite DB file.
      Run it BEFORE switching DATABASE_URL to PostgreSQL.
      To migrate to Postgres, run this on SQLite first, then use
      migrate_to_postgres.py to copy data across.
"""
1.  Adds new columns to the existing tables (if they don't exist yet):
      candidates.created_by_user_id
      candidates.sub_caste
      candidates.state
      candidates.candidate_email
      labels.user_id
      views.user_id

2.  Creates the new `users` table (if it doesn't exist).

3.  Creates the admin user from ADMIN_USERNAME / ADMIN_PASSWORD in .env,
    or prompts interactively if those vars are not set.

4.  Assigns all existing labels and views to the admin user.

5.  Verifies the result and prints a summary.

Safe to re-run: every step is idempotent.
"""

import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.getenv("DB_PATH", "./biodata.db")).expanduser()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")   # off during migration
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Helper: check if a column already exists
# ─────────────────────────────────────────────────────────────────────────────

def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Add new columns
# ─────────────────────────────────────────────────────────────────────────────

def add_columns(conn: sqlite3.Connection) -> None:
    new_cols = [
        # (table, column, definition)
        ("candidates", "created_by_user_id", "INTEGER"),
        ("candidates", "sub_caste",          "TEXT"),
        ("candidates", "state",              "TEXT"),
        ("candidates", "candidate_email",    "TEXT"),
        ("labels",     "user_id",            "INTEGER"),
        ("views",      "user_id",            "INTEGER"),
    ]
    for table, col, defn in new_cols:
        if not column_exists(conn, table, col):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            print(f"  + Added column {table}.{col}")
        else:
            print(f"  ✓ Column {table}.{col} already exists")
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Create users table
# ─────────────────────────────────────────────────────────────────────────────

USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

USERS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_labels_user ON labels(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_views_user  ON views(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_created_by  ON candidates(created_by_user_id);",
]


def create_users_table(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "users"):
        conn.execute(USERS_DDL)
        print("  + Created users table")
    else:
        print("  ✓ users table already exists")
    for idx_sql in USERS_INDEXES:
        conn.execute(idx_sql)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Create admin user
# ─────────────────────────────────────────────────────────────────────────────

def create_admin(conn: sqlite3.Connection) -> int:
    """Insert admin user (if not exists) and return their id."""
    from werkzeug.security import generate_password_hash

    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()

    if not username:
        username = input("Admin username: ").strip()
    if not password:
        import getpass
        password = getpass.getpass("Admin password: ").strip()

    if len(password) < 6:
        print("ERROR: Password must be at least 6 characters.")
        sys.exit(1)

    # Check if this username already exists
    existing = conn.execute(
        "SELECT id, role FROM users WHERE lower(username)=lower(?)", (username,)
    ).fetchone()

    if existing:
        # Always update the password (so re-running after a .env change takes effect)
        # and promote to admin if needed
        conn.execute(
            "UPDATE users SET password_hash=?, role='admin' WHERE id=?",
            (pw_hash, existing["id"]),
        )
        conn.commit()
        print(f"  ✓ Admin user '{username}' updated (id={existing['id']})")
        return existing["id"]

    pw_hash = generate_password_hash(password, method="pbkdf2:sha256")
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?,?,'admin')",
        (username, pw_hash),
    )
    conn.commit()
    print(f"  + Created admin user '{username}' (id={cur.lastrowid})")
    return cur.lastrowid


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Assign existing labels and views to admin
# ─────────────────────────────────────────────────────────────────────────────

def assign_existing_to_admin(conn: sqlite3.Connection, admin_id: int) -> None:
    # Labels without a user_id → assign to admin
    label_count = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE user_id IS NULL"
    ).fetchone()[0]
    if label_count:
        conn.execute(
            "UPDATE labels SET user_id=? WHERE user_id IS NULL", (admin_id,)
        )
        print(f"  + Assigned {label_count} existing label(s) to admin")
    else:
        print("  ✓ All labels already have a user_id")

    # Views without a user_id → assign to admin
    view_count = conn.execute(
        "SELECT COUNT(*) FROM views WHERE user_id IS NULL"
    ).fetchone()[0]
    if view_count:
        conn.execute(
            "UPDATE views SET user_id=? WHERE user_id IS NULL", (admin_id,)
        )
        print(f"  + Assigned {view_count} existing view(s) to admin")
    else:
        print("  ✓ All views already have a user_id")

    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Verify
# ─────────────────────────────────────────────────────────────────────────────

def verify(conn: sqlite3.Connection) -> None:
    counts = {
        "users":            conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "candidates":       conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],
        "labels":           conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0],
        "views":            conn.execute("SELECT COUNT(*) FROM views").fetchone()[0],
        "labels_no_user":   conn.execute("SELECT COUNT(*) FROM labels WHERE user_id IS NULL").fetchone()[0],
        "views_no_user":    conn.execute("SELECT COUNT(*) FROM views  WHERE user_id IS NULL").fetchone()[0],
    }
    print()
    print("  DB summary:")
    print(f"    users:          {counts['users']}")
    print(f"    candidates:     {counts['candidates']}")
    print(f"    labels:         {counts['labels']}  (orphaned: {counts['labels_no_user']})")
    print(f"    views:          {counts['views']}  (orphaned: {counts['views_no_user']})")
    if counts["labels_no_user"] or counts["views_no_user"]:
        print("  WARNING: orphaned labels/views found — re-run migration.")
    else:
        print("  All labels and views have a user_id. ✓")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    print(f"\nMigrating: {DB_PATH}\n")
    conn = get_db()

    print("Step 1 — Adding new columns ...")
    add_columns(conn)

    print("\nStep 2 — Creating users table ...")
    create_users_table(conn)

    print("\nStep 3 — Creating admin user ...")
    admin_id = create_admin(conn)

    print("\nStep 4 — Assigning existing data to admin ...")
    assign_existing_to_admin(conn, admin_id)

    print("\nStep 5 — Verifying ...")
    verify(conn)

    conn.close()
    print("\nMigration complete.\n")


if __name__ == "__main__":
    main()
