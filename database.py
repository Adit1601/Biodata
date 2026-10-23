#!/usr/bin/env python3
"""
database.py — DB connection, schema, and all read/write helpers.

Backend
-------
  Set DATABASE_URL in .env to use PostgreSQL (production).
  If DATABASE_URL is absent, falls back to SQLite at DB_PATH (local dev).

  PostgreSQL:   DATABASE_URL=postgresql://user:pass@host:5432/dbname
  SQLite:       DB_PATH=./biodata.db  (default)
"""

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH      = Path(os.getenv("DB_PATH", "./biodata.db")).expanduser()

_USE_POSTGRES = bool(DATABASE_URL)

# ─────────────────────────────────────────────────────────────────────────────
# Thin compatibility shim
# ─────────────────────────────────────────────────────────────────────────────
# SQLite uses  ?   as placeholder; psycopg2 uses  %s
# SQLite returns sqlite3.Row (subscriptable by name).
# psycopg2 returns tuples unless we use RealDictCursor.
# We normalise both to plain dicts via _row_to_dict / DictCursor.

if _USE_POSTGRES:
    import psycopg2                                   # type: ignore
    import psycopg2.extras                            # type: ignore
    _PH = "%s"                                        # placeholder
else:
    _PH = "?"


def _ph(sql: str) -> str:
    """Replace ? with %s when using Postgres."""
    if _USE_POSTGRES:
        return sql.replace("?", "%s")
    return sql


# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    """
    Return an open DB connection.

    SQLite:   sqlite3.Connection  (row_factory = sqlite3.Row, WAL, FK ON)
    Postgres: psycopg2 connection with autocommit=False
    """
    if _USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    else:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn


@contextmanager
def _db() -> Generator:
    """Context manager: open connection, commit on success, rollback on error, close always."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fetchone(conn, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    """Execute, fetch one row, return as dict or None. Handles ? vs %s automatically."""
    sql = _ph(sql)
    if _USE_POSTGRES:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
    else:
        with _sqlite_cursor(conn) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def _fetchall(conn, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """Execute, fetch all rows, return list of dicts."""
    sql = _ph(sql)
    if _USE_POSTGRES:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    else:
        with _sqlite_cursor(conn) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def _execute(conn, sql: str, params: tuple = ()) -> Any:
    """Execute DML. For RETURNING queries returns the first column of the first row."""
    sql = _ph(sql)
    if _USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description:
                row = cur.fetchone()
                return row[0] if row else None
            return None
    else:
        with _sqlite_cursor(conn) as cur:
            cur.execute(sql, params)
            return cur.lastrowid


def _executemany(conn, sql: str, param_list) -> None:
    sql = _ph(sql)
    if _USE_POSTGRES:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, param_list, page_size=200)
    else:
        with _sqlite_cursor(conn) as cur:
            cur.executemany(sql, param_list)


# SQLite cursor as a context manager (Python 3.9's sqlite3.Cursor isn't one natively)
class _sqlite_cursor:
    def __init__(self, conn):
        self._conn = conn
        self._cur  = None

    def __enter__(self):
        self._cur = self._conn.cursor()
        return self._cur

    def __exit__(self, *_):
        if self._cur:
            self._cur.close()


# Monkey-patch the cursor() method so _fetchone/_fetchall/_execute work uniformly



# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS candidates (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT,
    date_of_birth        TEXT,
    birth_year           INTEGER,
    age                  INTEGER,
    gender               TEXT,
    height               TEXT,
    height_inches        INTEGER,
    weight               TEXT,
    weight_kg            REAL,
    caste                TEXT,
    sub_caste            TEXT,
    education            TEXT,
    occupation           TEXT,
    job_location         TEXT,
    residential_city     TEXT,
    state                TEXT,
    fathers_name         TEXT,
    fathers_contact      TEXT,
    mothers_name         TEXT,
    mothers_contact      TEXT,
    candidate_contact    TEXT,
    candidate_email      TEXT,
    marital_status       TEXT,
    blood_group          TEXT,
    manglik              TEXT,
    mother_tongue        TEXT,
    about_me             TEXT,
    hobbies              TEXT,
    salary               TEXT,
    gotra                TEXT,
    fathers_occupation   TEXT,
    mothers_occupation   TEXT,
    siblings             TEXT,
    source_file          TEXT,
    file_hash            TEXT UNIQUE,
    date_added           TEXT,
    folder_stem          TEXT UNIQUE,
    created_by_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_birth_year    ON candidates(birth_year);
CREATE INDEX IF NOT EXISTS idx_height_inches ON candidates(height_inches);
CREATE INDEX IF NOT EXISTS idx_weight_kg     ON candidates(weight_kg);
CREATE INDEX IF NOT EXISTS idx_folder_stem   ON candidates(folder_stem);
CREATE INDEX IF NOT EXISTS idx_created_by    ON candidates(created_by_user_id);

CREATE TABLE IF NOT EXISTS field_config (
    field_key   TEXT PRIMARY KEY,
    state       TEXT NOT NULL DEFAULT 'optional',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS labels (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name    TEXT    NOT NULL,
    color   TEXT    NOT NULL DEFAULT '#6b7280',
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_labels_user ON labels(user_id);

CREATE TABLE IF NOT EXISTS candidate_labels (
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    label_id     INTEGER NOT NULL REFERENCES labels(id)     ON DELETE CASCADE,
    PRIMARY KEY (candidate_id, label_id)
);

CREATE TABLE IF NOT EXISTS views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    filter_desc TEXT
);

CREATE INDEX IF NOT EXISTS idx_views_user ON views(user_id);

CREATE TABLE IF NOT EXISTS view_candidates (
    view_id      INTEGER NOT NULL REFERENCES views(id)      ON DELETE CASCADE,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    PRIMARY KEY (view_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS claims (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id  INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    status        TEXT    NOT NULL DEFAULT 'pending',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    reviewed_at   TEXT,
    admin_note    TEXT,
    UNIQUE (candidate_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_user   ON claims(user_id);
"""

_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS candidates (
    id                   SERIAL PRIMARY KEY,
    name                 TEXT,
    date_of_birth        TEXT,
    birth_year           INTEGER,
    age                  INTEGER,
    gender               TEXT,
    height               TEXT,
    height_inches        INTEGER,
    weight               TEXT,
    weight_kg            REAL,
    caste                TEXT,
    sub_caste            TEXT,
    education            TEXT,
    occupation           TEXT,
    job_location         TEXT,
    residential_city     TEXT,
    state                TEXT,
    fathers_name         TEXT,
    fathers_contact      TEXT,
    mothers_name         TEXT,
    mothers_contact      TEXT,
    candidate_contact    TEXT,
    candidate_email      TEXT,
    marital_status       TEXT,
    blood_group          TEXT,
    manglik              TEXT,
    mother_tongue        TEXT,
    about_me             TEXT,
    hobbies              TEXT,
    salary               TEXT,
    gotra                TEXT,
    fathers_occupation   TEXT,
    mothers_occupation   TEXT,
    siblings             TEXT,
    source_file          TEXT,
    file_hash            TEXT UNIQUE,
    date_added           TEXT,
    folder_stem          TEXT UNIQUE,
    created_by_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_birth_year    ON candidates(birth_year);
CREATE INDEX IF NOT EXISTS idx_height_inches ON candidates(height_inches);
CREATE INDEX IF NOT EXISTS idx_weight_kg     ON candidates(weight_kg);
CREATE INDEX IF NOT EXISTS idx_folder_stem   ON candidates(folder_stem);
CREATE INDEX IF NOT EXISTS idx_created_by    ON candidates(created_by_user_id);

CREATE TABLE IF NOT EXISTS field_config (
    field_key   TEXT PRIMARY KEY,
    state       TEXT NOT NULL DEFAULT 'optional',
    updated_at  TEXT NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS labels (
    id      SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name    TEXT    NOT NULL,
    color   TEXT    NOT NULL DEFAULT '#6b7280',
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_labels_user ON labels(user_id);

CREATE TABLE IF NOT EXISTS candidate_labels (
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    label_id     INTEGER NOT NULL REFERENCES labels(id)     ON DELETE CASCADE,
    PRIMARY KEY (candidate_id, label_id)
);

CREATE TABLE IF NOT EXISTS views (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    filter_desc TEXT
);

CREATE INDEX IF NOT EXISTS idx_views_user ON views(user_id);

CREATE TABLE IF NOT EXISTS view_candidates (
    view_id      INTEGER NOT NULL REFERENCES views(id)      ON DELETE CASCADE,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    PRIMARY KEY (view_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS claims (
    id            SERIAL PRIMARY KEY,
    candidate_id  INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    status        TEXT    NOT NULL DEFAULT 'pending',
    created_at    TEXT    NOT NULL DEFAULT to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS'),
    reviewed_at   TEXT,
    admin_note    TEXT,
    UNIQUE (candidate_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_user   ON claims(user_id);
"""


def init_db() -> None:
    """Create all tables and indexes if they don't already exist."""
    if not _USE_POSTGRES:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        if _USE_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_POSTGRES)
        else:
            conn.executescript(_SCHEMA_SQLITE)


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_height_inches(h: Optional[str]) -> Optional[int]:
    if not h or str(h).strip().lower() in ("not mentioned", "none", ""):
        return None
    h = str(h).strip()
    m = re.search(r"(\d)\s*['\u2019\u02bc]\s*(\d{1,2})", h)
    if m:
        return int(m.group(1)) * 12 + int(m.group(2))
    m = re.search(r"(\d)\s*f(?:t|eet)\s*(\d{1,2})", h, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 12 + int(m.group(2))
    m = re.search(r"(\d)\s*['\u2019]", h)
    if m:
        return int(m.group(1)) * 12
    m = re.search(r"(\d{2,3})\s*cm", h, re.IGNORECASE)
    if m:
        return round(int(m.group(1)) / 2.54)
    return None


def parse_birth_year(dob: Optional[str]) -> Optional[int]:
    if not dob:
        return None
    m = re.search(r"\d{1,2}/\d{1,2}/(\d{4})", str(dob))
    return int(m.group(1)) if m else None


def parse_weight_kg(w: Optional[str]) -> Optional[float]:
    if not w or str(w).strip().lower() in ("not mentioned", "none", ""):
        return None
    w = str(w).strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*k(?:g|gs)?", w, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)", w)
    if m:
        return float(m.group(1))
    return None


def calc_age(dob: Optional[str]) -> Optional[int]:
    if not dob:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(dob))
    if not m:
        return None
    try:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        today = datetime.today()
        age = today.year - year - ((today.month, today.day) < (month, day))
        return age if 0 < age < 120 else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────────────────────────

def create_user(username: str, password_hash: str, role: str = "user") -> int:
    with _db() as conn:
        if _USE_POSTGRES:
            row = _fetchone(conn,
                "INSERT INTO users (username, password_hash, role) VALUES (?,?,?) RETURNING id",
                (username.strip(), password_hash, role))
            return row["id"]
        else:
            return _execute(conn,
                "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                (username.strip(), password_hash, role))


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        return _fetchone(conn, "SELECT * FROM users WHERE id=?", (user_id,))


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        return _fetchone(conn,
            "SELECT * FROM users WHERE lower(username)=lower(?)", (username.strip(),))


def list_users() -> List[Dict[str, Any]]:
    with _db() as conn:
        return _fetchall(conn, """
            SELECT u.id, u.username, u.role, u.active, u.created_at,
                   COUNT(c.id) AS profile_count
            FROM users u
            LEFT JOIN candidates c ON c.created_by_user_id = u.id
            GROUP BY u.id, u.username, u.role, u.active, u.created_at
            ORDER BY u.created_at DESC
        """)


def update_user(user_id: int, role: Optional[str] = None, active: Optional[int] = None) -> None:
    with _db() as conn:
        if role is not None:
            _execute(conn, "UPDATE users SET role=? WHERE id=?", (role, user_id))
        if active is not None:
            _execute(conn, "UPDATE users SET active=? WHERE id=?", (active, user_id))


def update_user_password(user_id: int, password_hash: str) -> None:
    with _db() as conn:
        _execute(conn, "UPDATE users SET password_hash=? WHERE id=?",
                 (password_hash, user_id))


def username_exists(username: str) -> bool:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT 1 FROM users WHERE lower(username)=lower(?)", (username.strip(),))
        return row is not None


# ─────────────────────────────────────────────────────────────────────────────
# Dedup checks
# ─────────────────────────────────────────────────────────────────────────────

def folder_stem_exists(stem: str) -> bool:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT 1 FROM candidates WHERE folder_stem = ? LIMIT 1", (stem,))
        return row is not None


def hash_exists(fhash: str) -> bool:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT 1 FROM candidates WHERE file_hash = ? LIMIT 1", (fhash,))
        return row is not None


def name_dob_exists(name: str, dob: str) -> bool:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT 1 FROM candidates WHERE lower(trim(name)) = lower(trim(?)) "
            "AND lower(trim(date_of_birth)) = lower(trim(?)) LIMIT 1",
            (name, dob))
        return row is not None


# ─────────────────────────────────────────────────────────────────────────────
# Candidate CRUD
# ─────────────────────────────────────────────────────────────────────────────

def insert_candidate(
    fields: Dict[str, Any],
    fhash: str,
    folder_stem: str,
    created_by_user_id: Optional[int] = None,
) -> int:
    def v(key: str) -> Optional[str]:
        val = fields.get(key)
        if not val or str(val).strip().lower() in ("null", "none", "", "not mentioned"):
            return None
        return str(val).strip()

    dob           = v("date_of_birth")
    height_raw    = v("height")
    weight_raw    = v("weight")
    source_file   = v("source_file") or f"{folder_stem}.pdf"
    birth_year    = parse_birth_year(dob)
    age           = calc_age(dob)
    height_inches = parse_height_inches(height_raw)
    weight_kg     = parse_weight_kg(weight_raw)

    sql = """
        INSERT INTO candidates
          (name, date_of_birth, birth_year, age, gender,
           height, height_inches, weight, weight_kg,
           caste, sub_caste, education, occupation, job_location,
           residential_city, state,
           fathers_name, fathers_contact, mothers_name, mothers_contact,
           candidate_contact, candidate_email,
           marital_status, blood_group, manglik, mother_tongue,
           about_me, hobbies, salary, gotra,
           fathers_occupation, mothers_occupation, siblings,
           source_file, file_hash, date_added, folder_stem,
           created_by_user_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    params = (
        v("name"), dob, birth_year, age, v("gender"),
        height_raw, height_inches, weight_raw, weight_kg,
        v("caste"), v("sub_caste"), v("education"), v("occupation"),
        v("job_location"), v("residential_city"), v("state"),
        v("fathers_name"), v("fathers_contact"),
        v("mothers_name"), v("mothers_contact"),
        v("candidate_contact"), v("candidate_email"),
        v("marital_status"), v("blood_group"), v("manglik"), v("mother_tongue"),
        v("about_me"), v("hobbies"), v("salary"), v("gotra"),
        v("fathers_occupation"), v("mothers_occupation"), v("siblings"),
        source_file, fhash, datetime.now().isoformat(), folder_stem,
        created_by_user_id,
    )

    with _db() as conn:
        if _USE_POSTGRES:
            row = _fetchone(conn, sql + " RETURNING id", params)
            return row["id"]
        else:
            return _execute(conn, sql, params)


def get_candidate(candidate_id: int) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT * FROM candidates WHERE id = ?", (candidate_id,))
        if not row:
            return None
        row["labels"] = _get_labels_for(conn, candidate_id)
        return row


def get_candidate_by_user(user_id: int) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT * FROM candidates WHERE created_by_user_id=? LIMIT 1", (user_id,))
        if not row:
            return None
        row["labels"] = _get_labels_for(conn, row["id"])
        return row


def get_candidate_by_stem(stem: str) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        return _fetchone(conn,
            "SELECT * FROM candidates WHERE folder_stem=? LIMIT 1", (stem,))


def update_candidate(candidate_id: int, fields: Dict[str, Any]) -> None:
    def v(key: str) -> Optional[str]:
        val = fields.get(key)
        if val is None:
            return None
        s = str(val).strip()
        return None if s.lower() in ("null", "none", "", "not mentioned") else s

    dob           = v("date_of_birth")
    height_raw    = v("height")
    weight_raw    = v("weight")
    birth_year    = parse_birth_year(dob)
    age           = calc_age(dob)
    height_inches = parse_height_inches(height_raw)
    weight_kg     = parse_weight_kg(weight_raw)

    with _db() as conn:
        _execute(conn, """
            UPDATE candidates SET
              name=?, date_of_birth=?, birth_year=?, age=?, gender=?,
              height=?, height_inches=?, weight=?, weight_kg=?,
              caste=?, sub_caste=?, education=?, occupation=?, job_location=?,
              residential_city=?, state=?,
              fathers_name=?, fathers_contact=?,
              mothers_name=?, mothers_contact=?,
              candidate_contact=?, candidate_email=?,
              marital_status=?, blood_group=?, manglik=?, mother_tongue=?,
              about_me=?, hobbies=?, salary=?, gotra=?,
              fathers_occupation=?, mothers_occupation=?, siblings=?,
              folder_stem=?
            WHERE id=?
        """, (
            v("name"), dob, birth_year, age, v("gender"),
            height_raw, height_inches, weight_raw, weight_kg,
            v("caste"), v("sub_caste"), v("education"), v("occupation"),
            v("job_location"), v("residential_city"), v("state"),
            v("fathers_name"), v("fathers_contact"),
            v("mothers_name"), v("mothers_contact"),
            v("candidate_contact"), v("candidate_email"),
            v("marital_status"), v("blood_group"), v("manglik"), v("mother_tongue"),
            v("about_me"), v("hobbies"), v("salary"), v("gotra"),
            v("fathers_occupation"), v("mothers_occupation"), v("siblings"),
            v("folder_stem"),
            candidate_id,
        ))


def delete_candidate(candidate_id: int) -> Optional[str]:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT folder_stem FROM candidates WHERE id=?", (candidate_id,))
        stem = row["folder_stem"] if row else None
        _execute(conn, "DELETE FROM candidates WHERE id=?", (candidate_id,))
    return stem


# ─────────────────────────────────────────────────────────────────────────────
# Candidate list + filters
# ─────────────────────────────────────────────────────────────────────────────

def _build_filter_query(
    filters: Dict[str, Any],
    count_only: bool = False,
    user_id: Optional[int] = None,
) -> tuple:
    label_ids = filters.get("label_ids") or []
    select = "SELECT COUNT(DISTINCT c.id)" if count_only else "SELECT DISTINCT c.*"

    clauses: List[str] = ["1=1"]
    params:  List[Any] = []

    if filters.get("height_min"):
        clauses.append("c.height_inches >= ?"); params.append(int(filters["height_min"]))
    if filters.get("height_max"):
        clauses.append("c.height_inches <= ?"); params.append(int(filters["height_max"]))
    if filters.get("birth_year_from"):
        clauses.append("c.birth_year >= ?"); params.append(int(filters["birth_year_from"]))
    if filters.get("birth_year_to"):
        clauses.append("c.birth_year <= ?"); params.append(int(filters["birth_year_to"]))
    if filters.get("weight_max"):
        clauses.append("c.weight_kg <= ?"); params.append(float(filters["weight_max"]))
    if filters.get("gender"):
        clauses.append("LOWER(c.gender) = LOWER(?)"); params.append(filters["gender"])
    if filters.get("search"):
        op = "ILIKE" if _USE_POSTGRES else "LIKE"
        clauses.append(f"c.name {op} ?"); params.append(f"%{filters['search']}%")

    if filters.get("caste"):
        op = "ILIKE" if _USE_POSTGRES else "LIKE"
        clauses.append(f"c.caste {op} ?"); params.append(f"%{filters['caste']}%")

    if filters.get("residential_city"):
        op = "ILIKE" if _USE_POSTGRES else "LIKE"
        clauses.append(f"c.residential_city {op} ?"); params.append(f"%{filters['residential_city']}%")

    if filters.get("job_location"):
        op = "ILIKE" if _USE_POSTGRES else "LIKE"
        clauses.append(f"c.job_location {op} ?"); params.append(f"%{filters['job_location']}%")

    if label_ids:
        phs = ",".join(["?"] * len(label_ids))
        clauses.append(
            f"c.id IN ("
            f"  SELECT candidate_id FROM candidate_labels "
            f"  WHERE label_id IN ({phs}) "
            f"  GROUP BY candidate_id HAVING COUNT(DISTINCT label_id) = ?"
            f")"
        )
        params.extend(label_ids)
        params.append(len(label_ids))

    where = " AND ".join(clauses)
    sql   = f"{select} FROM candidates c WHERE {where}"
    return _ph(sql), params


def count_candidates(filters: Dict[str, Any], user_id: Optional[int] = None) -> int:
    sql, params = _build_filter_query(filters, count_only=True, user_id=user_id)
    with _db() as conn:
        row = _fetchone(conn, sql, tuple(params))
        return list(row.values())[0] if row else 0


def get_candidates(
    filters: Dict[str, Any],
    offset: int = 0,
    limit: int = 50,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    sql, params = _build_filter_query(filters, user_id=user_id)
    sql += " ORDER BY c.date_added DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with _db() as conn:
        rows = _fetchall(conn, sql, tuple(params))
        for row in rows:
            row["labels"] = _get_labels_for(conn, row["id"], user_id=user_id)
        return rows


def get_distinct_years() -> List[int]:
    """Return a continuous year range from min to max birth year in the DB."""
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT MIN(birth_year) AS mn, MAX(birth_year) AS mx "
            "FROM candidates WHERE birth_year IS NOT NULL")
    if not row or not row["mn"]:
        return []
    return list(range(row["mn"], row["mx"] + 1))


_EMPTY_VALS = ("", "not mentioned", "none", "null")


def get_distinct_cities(column: str) -> List[str]:
    """Return distinct non-empty values for a city-like column, sorted by frequency desc."""
    allowed = {"residential_city", "job_location"}
    if column not in allowed:
        raise ValueError(f"column must be one of {allowed}")
    _EMPTY_LOWER = "('', 'not mentioned', 'none', 'null')"
    if _USE_POSTGRES:
        sql = (
            f"SELECT {column} AS val, COUNT(*) AS c FROM candidates "
            f"WHERE {column} IS NOT NULL "
            f"AND LOWER(TRIM({column})) NOT IN {_EMPTY_LOWER} "
            f"GROUP BY {column} ORDER BY c DESC, {column}"
        )
    else:
        sql = (
            f"SELECT {column} AS val, COUNT(*) AS c FROM candidates "
            f"WHERE {column} IS NOT NULL "
            f"AND LOWER(TRIM({column})) NOT IN {_EMPTY_LOWER} "
            f"GROUP BY {column} ORDER BY c DESC, {column}"
        )
    with _db() as conn:
        rows = _fetchall(conn, sql)
    return [r["val"] for r in rows if r["val"]]


# ─────────────────────────────────────────────────────────────────────────────
# Labels
# ─────────────────────────────────────────────────────────────────────────────

def _get_labels_for(
    conn,
    candidate_id: int,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if user_id is not None:
        return _fetchall(conn, """
            SELECT l.id, l.name, l.color
            FROM labels l
            JOIN candidate_labels cl ON l.id = cl.label_id
            WHERE cl.candidate_id = ? AND l.user_id = ?
            ORDER BY l.name
        """, (candidate_id, user_id))
    return _fetchall(conn, """
        SELECT l.id, l.name, l.color
        FROM labels l
        JOIN candidate_labels cl ON l.id = cl.label_id
        WHERE cl.candidate_id = ?
        ORDER BY l.name
    """, (candidate_id,))


def get_all_labels(user_id: int) -> List[Dict[str, Any]]:
    with _db() as conn:
        return _fetchall(conn,
            "SELECT * FROM labels WHERE user_id=? ORDER BY name", (user_id,))


def add_label(name: str, color: str, user_id: int) -> int:
    with _db() as conn:
        if _USE_POSTGRES:
            row = _fetchone(conn,
                "INSERT INTO labels (name, color, user_id) VALUES (?,?,?) RETURNING id",
                (name.strip(), color.strip(), user_id))
            return row["id"]
        else:
            return _execute(conn,
                "INSERT INTO labels (name, color, user_id) VALUES (?,?,?)",
                (name.strip(), color.strip(), user_id))


def update_label(label_id: int, name: str, color: str, user_id: int) -> None:
    with _db() as conn:
        _execute(conn,
            "UPDATE labels SET name=?, color=? WHERE id=? AND user_id=?",
            (name.strip(), color.strip(), label_id, user_id))


def delete_label(label_id: int, user_id: int) -> None:
    with _db() as conn:
        _execute(conn,
            "DELETE FROM labels WHERE id=? AND user_id=?", (label_id, user_id))


def add_candidate_label(candidate_id: int, label_id: int) -> None:
    with _db() as conn:
        if _USE_POSTGRES:
            _execute(conn,
                "INSERT INTO candidate_labels (candidate_id, label_id) VALUES (?,?) "
                "ON CONFLICT DO NOTHING",
                (candidate_id, label_id))
        else:
            _execute(conn,
                "INSERT OR IGNORE INTO candidate_labels (candidate_id, label_id) VALUES (?,?)",
                (candidate_id, label_id))


def remove_candidate_label(candidate_id: int, label_id: int) -> None:
    with _db() as conn:
        _execute(conn,
            "DELETE FROM candidate_labels WHERE candidate_id=? AND label_id=?",
            (candidate_id, label_id))


# ─────────────────────────────────────────────────────────────────────────────
# Saved views
# ─────────────────────────────────────────────────────────────────────────────

def get_views(user_id: int) -> List[Dict[str, Any]]:
    with _db() as conn:
        return _fetchall(conn, """
            SELECT v.id, v.name, v.created_at, v.filter_desc,
                   COUNT(vc.candidate_id) AS count
            FROM views v
            LEFT JOIN view_candidates vc ON v.id = vc.view_id
            WHERE v.user_id = ?
            GROUP BY v.id, v.name, v.created_at, v.filter_desc
            ORDER BY v.created_at DESC
        """, (user_id,))


def create_view(
    name: str,
    filter_desc: str,
    candidate_ids: List[int],
    user_id: int,
) -> int:
    now = datetime.now().isoformat()
    with _db() as conn:
        if _USE_POSTGRES:
            row = _fetchone(conn,
                "INSERT INTO views (name, created_at, filter_desc, user_id) "
                "VALUES (?,?,?,?) RETURNING id",
                (name.strip(), now, filter_desc or "", user_id))
            view_id = row["id"]
        else:
            view_id = _execute(conn,
                "INSERT INTO views (name, created_at, filter_desc, user_id) VALUES (?,?,?,?)",
                (name.strip(), now, filter_desc or "", user_id))
        if candidate_ids:
            if _USE_POSTGRES:
                _executemany(conn,
                    "INSERT INTO view_candidates (view_id, candidate_id) VALUES (?,?) "
                    "ON CONFLICT DO NOTHING",
                    [(view_id, cid) for cid in candidate_ids])
            else:
                _executemany(conn,
                    "INSERT OR IGNORE INTO view_candidates (view_id, candidate_id) VALUES (?,?)",
                    [(view_id, cid) for cid in candidate_ids])
        return view_id


def rename_view(view_id: int, name: str, user_id: int) -> None:
    with _db() as conn:
        _execute(conn,
            "UPDATE views SET name=? WHERE id=? AND user_id=?",
            (name.strip(), view_id, user_id))


def delete_view(view_id: int, user_id: int) -> None:
    with _db() as conn:
        _execute(conn,
            "DELETE FROM views WHERE id=? AND user_id=?", (view_id, user_id))


def view_belongs_to_user(view_id: int, user_id: int) -> bool:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT 1 FROM views WHERE id=? AND user_id=?", (view_id, user_id))
        return row is not None


def get_view_candidates(
    view_id: int,
    offset: int = 0,
    limit: int = 50,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    with _db() as conn:
        rows = _fetchall(conn, """
            SELECT c.*
            FROM candidates c
            JOIN view_candidates vc ON c.id = vc.candidate_id
            WHERE vc.view_id = ?
            ORDER BY c.name
            LIMIT ? OFFSET ?
        """, (view_id, limit, offset))
        for row in rows:
            row["labels"] = _get_labels_for(conn, row["id"], user_id=user_id)
        return rows


def count_view_candidates(view_id: int) -> int:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) FROM view_candidates WHERE view_id=?", (view_id,))
        return list(row.values())[0] if row else 0


def remove_from_view(view_id: int, candidate_id: int) -> None:
    with _db() as conn:
        _execute(conn,
            "DELETE FROM view_candidates WHERE view_id=? AND candidate_id=?",
            (view_id, candidate_id))


def candidate_in_view(view_id: int, candidate_id: int) -> bool:
    """Return True if the candidate is already in the view."""
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT 1 FROM view_candidates WHERE view_id=? AND candidate_id=?",
            (view_id, candidate_id))
    return row is not None


def add_to_view(view_id: int, candidate_id: int) -> None:
    with _db() as conn:
        if _USE_POSTGRES:
            _execute(conn,
                "INSERT INTO view_candidates (view_id, candidate_id) VALUES (?,?) "
                "ON CONFLICT DO NOTHING",
                (view_id, candidate_id))
        else:
            _execute(conn,
                "INSERT OR IGNORE INTO view_candidates (view_id, candidate_id) VALUES (?,?)",
                (view_id, candidate_id))


# ─────────────────────────────────────────────────────────────────────────────
# Init on import
# ─────────────────────────────────────────────────────────────────────────────

init_db()


# ─────────────────────────────────────────────────────────────────────────────
# Claims
# ─────────────────────────────────────────────────────────────────────────────

def create_claim(candidate_id: int, user_id: int) -> Optional[int]:
    """Insert or re-open a claim. Returns the claim id, or None if already pending/accepted."""
    with _db() as conn:
        existing = _fetchone(conn,
            "SELECT id, status FROM claims WHERE candidate_id=? AND user_id=?",
            (candidate_id, user_id))
        if existing:
            # Allow re-claim only when the previous attempt was rejected
            if existing["status"] == "rejected":
                _execute(conn,
                    "UPDATE claims SET status='pending', admin_note=NULL, reviewed_at=NULL WHERE id=?",
                    (existing["id"],))
                return existing["id"]
            # Already pending or accepted — do nothing
            return None
        if _USE_POSTGRES:
            row = _fetchone(conn,
                "INSERT INTO claims (candidate_id, user_id) VALUES (?,?) RETURNING id",
                (candidate_id, user_id))
            return row["id"] if row else None
        else:
            return _execute(conn,
                "INSERT INTO claims (candidate_id, user_id) VALUES (?,?)",
                (candidate_id, user_id))


def get_claim_for_user(candidate_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        return _fetchone(conn,
            "SELECT * FROM claims WHERE candidate_id=? AND user_id=?",
            (candidate_id, user_id))


def list_claims(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return claims joined with user and candidate info for admin review."""
    with _db() as conn:
        if status:
            return _fetchall(conn, """
                SELECT cl.*, u.username,
                       c.name AS candidate_name, c.folder_stem, c.date_of_birth
                FROM claims cl
                JOIN users      u ON u.id = cl.user_id
                JOIN candidates c ON c.id = cl.candidate_id
                WHERE cl.status = ?
                ORDER BY cl.created_at DESC
            """, (status,))
        return _fetchall(conn, """
            SELECT cl.*, u.username,
                   c.name AS candidate_name, c.folder_stem, c.date_of_birth
            FROM claims cl
            JOIN users      u ON u.id = cl.user_id
            JOIN candidates c ON c.id = cl.candidate_id
            ORDER BY cl.created_at DESC
        """)


def count_pending_claims() -> int:
    with _db() as conn:
        row = _fetchone(conn,
            "SELECT COUNT(*) FROM claims WHERE status='pending'")
        return list(row.values())[0] if row else 0


def review_claim(claim_id: int, status: str, admin_note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Accept or reject a claim. Returns the claim row after update."""
    with _db() as conn:
        cl = _fetchone(conn, "SELECT * FROM claims WHERE id=?", (claim_id,))
        if not cl:
            return None
        now = datetime.now().isoformat()
        _execute(conn,
            "UPDATE claims SET status=?, reviewed_at=?, admin_note=? WHERE id=?",
            (status, now, admin_note or "", claim_id))
        if status == "accepted":
            _execute(conn,
                "UPDATE candidates SET created_by_user_id=? WHERE id=?",
                (cl["user_id"], cl["candidate_id"]))
        return _fetchone(conn, "SELECT * FROM claims WHERE id=?", (claim_id,))


# ─────────────────────────────────────────────────────────────────────────────
# Field registry  (single source of truth for every candidate field)
# ─────────────────────────────────────────────────────────────────────────────

# input_type: text | textarea | select | date
# default_state: mandatory | optional | hidden
# options: list of strings for <select> inputs

FIELD_REGISTRY: List[Dict[str, Any]] = [
    # ── Personal ─────────────────────────────────────────────────────────────
    {"key": "name",           "label": "Full name",        "group": "Personal", "input": "text",     "default": "mandatory"},
    {"key": "date_of_birth",  "label": "Date of birth",    "group": "Personal", "input": "text",     "default": "mandatory", "placeholder": "DD/MM/YYYY"},
    {"key": "gender",         "label": "Gender",           "group": "Personal", "input": "select",   "default": "mandatory", "options": ["Female", "Male", "Other"]},
    {"key": "height",         "label": "Height",           "group": "Personal", "input": "text",     "default": "mandatory", "placeholder": "5'4\" or 163 cm"},
    {"key": "weight",         "label": "Weight",           "group": "Personal", "input": "text",     "default": "optional",  "placeholder": "55 kg"},
    {"key": "blood_group",    "label": "Blood group",      "group": "Personal", "input": "select",   "default": "optional",  "options": ["A+","A-","B+","B-","O+","O-","AB+","AB-","Unknown"]},
    {"key": "marital_status", "label": "Marital status",   "group": "Personal", "input": "select",   "default": "optional",  "options": ["Never Married", "Divorced", "Widowed", "Awaiting Divorce"]},
    {"key": "mother_tongue",  "label": "Mother tongue",    "group": "Personal", "input": "text",     "default": "optional"},
    # ── Caste & Religion ─────────────────────────────────────────────────────
    {"key": "caste",          "label": "Caste",            "group": "Caste & Religion", "input": "text", "default": "mandatory"},
    {"key": "sub_caste",      "label": "Sub-caste",        "group": "Caste & Religion", "input": "text", "default": "optional"},
    {"key": "gotra",          "label": "Gotra",            "group": "Caste & Religion", "input": "text", "default": "optional"},
    {"key": "manglik",        "label": "Manglik",          "group": "Caste & Religion", "input": "select", "default": "optional", "options": ["Yes", "No", "Don't know"]},
    # ── Education & Career ────────────────────────────────────────────────────
    {"key": "education",      "label": "Education",        "group": "Education & Career", "input": "text", "default": "mandatory"},
    {"key": "occupation",     "label": "Occupation",       "group": "Education & Career", "input": "text", "default": "optional"},
    {"key": "job_location",   "label": "Current location", "group": "Education & Career", "input": "text", "default": "mandatory"},
    {"key": "salary",         "label": "Annual salary",    "group": "Education & Career", "input": "text", "default": "optional", "placeholder": "e.g. 12 LPA  (per annum)"},
    # ── Location ─────────────────────────────────────────────────────────────
    {"key": "residential_city","label": "Family city",     "group": "Location", "input": "text",     "default": "mandatory"},
    {"key": "state",          "label": "State",            "group": "Location", "input": "text",     "default": "optional"},
    # ── Family ───────────────────────────────────────────────────────────────
    {"key": "fathers_name",   "label": "Father's name",    "group": "Family",   "input": "text",     "default": "optional"},
    {"key": "fathers_contact","label": "Father's contact", "group": "Family",   "input": "text",     "default": "optional"},
    {"key": "fathers_occupation","label": "Father's occupation","group": "Family","input": "text",   "default": "optional"},
    {"key": "mothers_name",   "label": "Mother's name",    "group": "Family",   "input": "text",     "default": "optional"},
    {"key": "mothers_contact","label": "Mother's contact", "group": "Family",   "input": "text",     "default": "optional"},
    {"key": "mothers_occupation","label": "Mother's occupation","group": "Family","input": "text",   "default": "optional"},
    {"key": "siblings",       "label": "Siblings",         "group": "Family",   "input": "text",     "default": "optional", "placeholder": "e.g. 1 brother (married), 1 sister"},
    {"key": "candidate_contact","label": "Candidate phone","group": "Family",   "input": "text",     "default": "optional"},
    {"key": "candidate_email","label": "Candidate email",  "group": "Family",   "input": "text",     "default": "optional"},
    # ── About ─────────────────────────────────────────────────────────────────
    {"key": "hobbies",        "label": "Hobbies",          "group": "About",    "input": "text",     "default": "optional"},
    {"key": "about_me",       "label": "About me",         "group": "About",    "input": "textarea", "default": "optional"},
]

# Convenient lookup by key
FIELD_MAP: Dict[str, Dict[str, Any]] = {f["key"]: f for f in FIELD_REGISTRY}

# Special cross-field rules (at-least-one-of)
FIELD_AT_LEAST_ONE: List[Dict[str, Any]] = [
    {"keys": ["fathers_name", "mothers_name"],    "label": "at least one parent name"},
    {"keys": ["fathers_contact", "mothers_contact"], "label": "at least one parent contact"},
]


# ─────────────────────────────────────────────────────────────────────────────
# Field config CRUD
# ─────────────────────────────────────────────────────────────────────────────

def get_field_config() -> Dict[str, str]:
    """Return {field_key: state} for all configured fields.
    Falls back to the field registry default for any unconfigured key."""
    with _db() as conn:
        rows = _fetchall(conn, "SELECT field_key, state FROM field_config")
    db_config = {r["field_key"]: r["state"] for r in rows}
    result = {}
    for f in FIELD_REGISTRY:
        result[f["key"]] = db_config.get(f["key"], f["default"])
    return result


def set_field_state(field_key: str, state: str) -> None:
    """Upsert a single field's state (mandatory | optional | hidden)."""
    assert state in ("mandatory", "optional", "hidden")
    now = datetime.now().isoformat()
    with _db() as conn:
        if _USE_POSTGRES:
            _execute(conn, """
                INSERT INTO field_config (field_key, state, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (field_key) DO UPDATE SET state=EXCLUDED.state, updated_at=EXCLUDED.updated_at
            """, (field_key, state, now))
        else:
            _execute(conn, """
                INSERT OR REPLACE INTO field_config (field_key, state, updated_at)
                VALUES (?, ?, ?)
            """, (field_key, state, now))


def get_mandatory_fields() -> List[str]:
    """Return list of field keys currently marked mandatory."""
    cfg = get_field_config()
    return [k for k, v in cfg.items() if v == "mandatory"]


def audit_candidate(candidate: Dict[str, Any], mandatory_keys: List[str]) -> List[str]:
    """Return list of mandatory field keys that are missing/empty for this candidate."""
    missing = []
    for key in mandatory_keys:
        val = candidate.get(key)
        if not val or str(val).strip().lower() in ("", "none", "null", "not mentioned"):
            missing.append(key)
    # Cross-field: at-least-one-of rules
    cfg = get_field_config()
    for rule in FIELD_AT_LEAST_ONE:
        # Only enforce if at least one key in the group is mandatory or optional (not hidden)
        active_keys = [k for k in rule["keys"] if cfg.get(k) in ("mandatory", "optional")]
        if not active_keys:
            continue
        # Check if any of the group keys has a value
        has_any = any(
            candidate.get(k) and str(candidate.get(k)).strip().lower()
            not in ("", "none", "null", "not mentioned")
            for k in rule["keys"]
        )
        if not has_any:
            # Only add the rule label once, not each individual key
            missing.append(f"__rule__{rule['label']}")
    return missing
