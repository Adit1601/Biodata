# Biodata Viewer

A private, self-hosted matrimonial biodata management platform. The admin ingests biodata files (PDF, Word, images); Claude AI extracts structured fields automatically; all users browse, label, and shortlist candidates through a clean web interface.

> **Full feature documentation:** [FEATURES.md](FEATURES.md)  
> **Deployment guide:** [DEPLOY.md](DEPLOY.md)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9 · Flask · Flask-Login |
| Database | PostgreSQL (production) · SQLite (local dev fallback) |
| AI Extraction | Anthropic Claude (via IBM proxy) |
| File processing | PyMuPDF · Tesseract OCR · python-docx · Pillow |
| Frontend | Vanilla JS · Jinja2 templates · CSS custom properties |
| Server | Gunicorn (production) · Flask dev server (local) |
| Container | Docker + Docker Compose |

---

## Project Layout

```
biodata/
├── server.py              ← Flask web application (all routes)
├── database.py            ← DB schema, connection, all CRUD helpers
├── processor.py           ← PDF/DOCX/image extraction + Claude field extraction
├── watcher.py             ← Folder-watch daemon (real-time inbox processing)
├── run_once.py            ← Batch-process everything currently in inbox/
├── retry_photos.py        ← Re-run photo extraction for candidates without photos
├── retry_failed.py        ← Retry files that previously failed processing
├── migrate_phase2.py      ← One-time migration: adds auth tables, creates admin user
├── migrate_to_postgres.py ← One-time migration: copies SQLite data to PostgreSQL
├── backup.py              ← Dumps DB to a timestamped SQL file
├── requirements.txt
├── Dockerfile
├── Procfile               ← For Heroku / Render / Railway
├── .env                   ← Config (API keys, DB URL, paths) — never commit this
│
├── inbox/                 ← DROP biodata files here for processing
├── processed/             ← Processed candidates live here (one folder per person)
│   └── <stem>/
│       ├── <stem>.pdf     ← Original biodata (renamed)
│       ├── <stem>.jpg     ← Extracted portrait photo
│       └── <stem>.docx    ← Original if source was Word
├── failed/                ← Files that could not be processed
├── trash/                 ← Deleted candidate folders (soft delete)
└── templates/             ← Jinja2 HTML templates
```

Each candidate's folder stem follows the pattern `<birth_year>_<Full_Name>` (e.g. `1998_Priya_Shah`). The stem never changes after creation.

---

## Quick Start (Local)

### Prerequisites

- Python 3.9+
- PostgreSQL 14+ **or** leave `DATABASE_URL` blank to use SQLite
- Tesseract OCR (`brew install tesseract` on macOS)
- IBM proxy credentials for Claude API access

### 1. Clone and install

```bash
git clone <repo-url>
cd biodata
pip install -r requirements.txt
```

### 2. Configure `.env`

Copy the example and fill in your values:

```bash
cp .env.example .env   # or edit .env directly
```

Minimum required variables:

```env
SECRET_KEY=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
ANTHROPIC_BASE_URL=https://api.servicesessentials.ibm.com
ANTHROPIC_AUTH_TOKEN=sk-...
ANTHROPIC_MODEL=claude-sonnet-4-6

# Leave blank to use SQLite:
DATABASE_URL=postgresql://biodata:password@localhost:5432/biodata

PROCESSED_FOLDER=/absolute/path/to/biodata/processed
WATCH_FOLDER=/absolute/path/to/biodata/inbox
FAILED_FOLDER=/absolute/path/to/biodata/failed
```

### 3. Initialise the database

```bash
# Creates all tables + the admin user
python3 migrate_phase2.py
```

### 4. Run the server

```bash
python3 server.py
```

Open **http://localhost:8080** and sign in with the admin credentials from `.env`.

### 5. (Optional) Start the inbox watcher

In a separate terminal — drop any biodata file into `inbox/` and it is processed automatically:

```bash
python3 watcher.py
```

Or process everything already in `inbox/` in one batch:

```bash
python3 run_once.py
```

---

## How Biodata Processing Works

```
inbox/ file
    │
    ├─ Extract text ──── digital PDF (PyMuPDF) / scanned PDF or image (Tesseract OCR) / Word (python-docx)
    │
    ├─ Extract photo ─── embedded portrait (PyMuPDF) / full-page scan → Claude Vision crop
    │
    ├─ Extract fields ── Claude AI → structured JSON (name, DOB, height, caste, education, …)
    │
    ├─ Deduplication ─── file hash · folder stem · name + DOB match → skip if duplicate
    │
    ├─ Rename file ────── → <birth_year>_<Name>.pdf
    │
    └─ Insert DB row ──── move to processed/<stem>/
```

---

## Key URLs

| URL | Who | What |
|---|---|---|
| `/` | All users | Browse all candidates |
| `/view/<id>` | All users | Browse a saved view |
| `/profile/me` | All users | View own profile |
| `/profile/me/edit` | All users | Create / edit own profile |
| `/admin` | Admin | User management |
| `/admin/candidates` | Admin | All candidates table + merge |
| `/admin/candidate/<id>/edit` | Admin | Edit any candidate |
| `/admin/fields` | Admin | Field configuration |
| `/admin/completeness` | Admin | Missing-data audit |
| `/admin/claims` | Admin | Review profile ownership claims |
| `/admin/merge` | Admin | Merge duplicate profiles |

---

## Backups

```bash
# Dump the database (Postgres or SQLite) to ./backups/
python3 backup.py

# Custom output directory
python3 backup.py --out /Volumes/MyDrive/biodata-backups
```
