# Biodata — Deployment Guide

This guide covers every way to run the application: local development, a Linux VM / VPS, and Docker. Read the section that matches your target environment.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Environment Variables Reference](#2-environment-variables-reference)
3. [Local Development (macOS / Linux)](#3-local-development-macos--linux)
4. [Linux VM / VPS (Production)](#4-linux-vm--vps-production)
5. [Docker (Single Machine)](#5-docker-single-machine)
6. [Docker Compose (Recommended for VMs)](#6-docker-compose-recommended-for-vms)
7. [First-Run Setup](#7-first-run-setup)
8. [Ingesting Biodata Files](#8-ingesting-biodata-files)
9. [Backups](#9-backups)
10. [Keeping the App Running](#10-keeping-the-app-running)
11. [Upgrading](#11-upgrading)

---

## 1. Prerequisites

### For running directly on a machine (no Docker)

| Requirement | Version | Install |
|---|---|---|
| Python | 3.9 or 3.11 | `apt install python3 python3-pip` |
| PostgreSQL | 14+ | `apt install postgresql postgresql-contrib` |
| Tesseract OCR | 4.x or 5.x | `apt install tesseract-ocr tesseract-ocr-eng` |
| libpq (Postgres C client) | any | `apt install libpq-dev` |
| gcc (to compile psycopg2) | any | `apt install gcc` |
| git | any | `apt install git` |

> **macOS only:** Also install `brew install tesseract` and ensure `libpq` is present (`brew install libpq`).

### For Docker deployment

| Requirement | Version |
|---|---|
| Docker Engine | 24+ |
| Docker Compose | v2 (`docker compose`) |

No Python, Postgres, or Tesseract needed on the host — everything runs inside the containers.

### IBM Proxy / Claude API

You need:
- `ANTHROPIC_BASE_URL` — the IBM proxy endpoint
- `ANTHROPIC_AUTH_TOKEN` — your API key (starts with `sk-`)
- `ANTHROPIC_MODEL` — e.g. `claude-sonnet-4-6`

These are required only when **processing new biodata files** (running `watcher.py` or `run_once.py`). The web server itself does not call Claude.

---

## 2. Environment Variables Reference

Create a `.env` file in the project root. **Never commit this file.**

```env
# ── Flask ─────────────────────────────────────────────────────────────────
# Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=change-this-to-a-long-random-string

# ── Database ───────────────────────────────────────────────────────────────
# Leave blank to fall back to SQLite (local dev only, not for production)
DATABASE_URL=postgresql://biodata:yourpassword@localhost:5432/biodata

# ── Claude AI (IBM proxy) ──────────────────────────────────────────────────
ANTHROPIC_BASE_URL=https://api.servicesessentials.ibm.com
ANTHROPIC_AUTH_TOKEN=sk-...
ANTHROPIC_MODEL=claude-sonnet-4-6

# ── Folder paths (use absolute paths in production) ────────────────────────
PROCESSED_FOLDER=/app/processed
WATCH_FOLDER=/app/inbox
FAILED_FOLDER=/app/failed

# ── Admin account (used by migrate_phase2.py) ──────────────────────────────
ADMIN_USERNAME=admin
ADMIN_PASSWORD=choose-a-strong-password

# ── Server port (Render/Railway set this automatically) ────────────────────
PORT=8080
```

| Variable | Required | Default | Notes |
|---|---|---|---|
| `SECRET_KEY` | **Yes** | `dev-secret-key-…` | Must be a long random string in production. Flask uses it to sign session cookies. |
| `DATABASE_URL` | **Yes** (prod) | *(blank → SQLite)* | PostgreSQL connection string |
| `ANTHROPIC_BASE_URL` | For processing | — | IBM proxy URL |
| `ANTHROPIC_AUTH_TOKEN` | For processing | — | API key |
| `ANTHROPIC_MODEL` | For processing | — | Claude model name |
| `PROCESSED_FOLDER` | **Yes** | `./processed` | Absolute path to processed candidate folders |
| `WATCH_FOLDER` | For watcher | `./inbox` | Folder monitored for new biodata files |
| `FAILED_FOLDER` | For processing | `./failed` | Failed files land here |
| `ADMIN_USERNAME` | Setup only | — | Used once by `migrate_phase2.py` |
| `ADMIN_PASSWORD` | Setup only | — | Min 8 chars |
| `PORT` | No | `8080` | Web server port |

---

## 3. Local Development (macOS / Linux)

### macOS — install system deps

```bash
brew install tesseract postgresql@16
```

### Ubuntu / Debian — install system deps

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv \
    postgresql postgresql-contrib libpq-dev gcc \
    tesseract-ocr tesseract-ocr-eng git
```

### Set up the app

```bash
git clone <repo-url>
cd biodata

# Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

### Set up PostgreSQL (local)

```bash
# macOS (Homebrew)
brew services start postgresql@16
createdb biodata

# Ubuntu
sudo systemctl start postgresql
sudo -u postgres psql -c "CREATE USER biodata WITH PASSWORD 'yourpassword';"
sudo -u postgres psql -c "CREATE DATABASE biodata OWNER biodata;"
```

### Configure and initialise

```bash
# Edit .env (at minimum set SECRET_KEY and DATABASE_URL)
cp .env.example .env
nano .env

# Create all tables and the admin user
python3 migrate_phase2.py
```

### Run

```bash
python3 server.py
# Open http://localhost:8080
```

---

## 4. Linux VM / VPS (Production)

This is the recommended approach for running on a cloud VM (AWS EC2, DigitalOcean Droplet, Hetzner, etc.).

### Step 1 — Provision the VM

Minimum specs:
- **OS:** Ubuntu 22.04 LTS (or 24.04)
- **RAM:** 1 GB (2 GB recommended for smooth Claude processing)
- **Disk:** 20 GB+ (photos and PDFs grow over time)
- **CPU:** 1 vCPU minimum

### Step 2 — Install system dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    python3 python3-pip python3-venv \
    postgresql postgresql-contrib libpq-dev \
    tesseract-ocr tesseract-ocr-eng \
    gcc git nginx
```

### Step 3 — Create a Postgres database

```bash
sudo systemctl enable --now postgresql

sudo -u postgres psql <<SQL
CREATE USER biodata WITH PASSWORD 'choose-a-strong-password';
CREATE DATABASE biodata OWNER biodata;
\q
SQL
```

### Step 4 — Deploy the application

```bash
# As your deploy user (not root)
git clone <repo-url> /opt/biodata
cd /opt/biodata

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create the data folders
mkdir -p processed inbox failed trash

# Configure environment
cp .env.example .env
nano .env   # fill in all values — especially SECRET_KEY and DATABASE_URL

# Initialise tables and admin user
python3 migrate_phase2.py
```

### Step 5 — Run with Gunicorn (WSGI server)

```bash
# Test it works
gunicorn server:app --bind 0.0.0.0:8080 --workers 2 --threads 4

# Press Ctrl+C to stop
```

### Step 6 — Create a systemd service (runs on boot, auto-restarts)

```bash
sudo nano /etc/systemd/system/biodata.service
```

Paste:

```ini
[Unit]
Description=Biodata Web Application
After=network.target postgresql.service

[Service]
Type=simple
User=<your-deploy-user>
WorkingDirectory=/opt/biodata
EnvironmentFile=/opt/biodata/.env
ExecStart=/opt/biodata/venv/bin/gunicorn server:app \
    --bind 127.0.0.1:8080 \
    --workers 2 \
    --threads 4 \
    --timeout 120 \
    --access-logfile /var/log/biodata/access.log \
    --error-logfile /var/log/biodata/error.log
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/log/biodata
sudo chown <your-deploy-user>: /var/log/biodata

sudo systemctl daemon-reload
sudo systemctl enable --now biodata

# Check status
sudo systemctl status biodata
```

### Step 7 — Set up Nginx as a reverse proxy

```bash
sudo nano /etc/nginx/sites-available/biodata
```

Paste:

```nginx
server {
    listen 80;
    server_name your-domain.com;   # or the VM's IP address

    # Increase upload size limit for PDF/photo uploads
    client_max_body_size 25M;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/biodata /etc/nginx/sites-enabled/
sudo nginx -t          # verify config
sudo systemctl reload nginx
```

### Step 8 — (Optional) Add HTTPS with Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

Certbot automatically updates your Nginx config and sets up auto-renewal.

---

## 5. Docker (Single Machine)

Use this if you want to run the app as a container alongside a Postgres container, on any machine with Docker installed.

### Prerequisites

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in
```

### Start a Postgres container

```bash
docker run -d \
  --name biodata-postgres \
  -e POSTGRES_USER=biodata \
  -e POSTGRES_PASSWORD=yourpassword \
  -e POSTGRES_DB=biodata \
  -p 5432:5432 \
  -v biodata-pgdata:/var/lib/postgresql/data \
  --restart unless-stopped \
  postgres:16-alpine
```

### Build and run the app container

```bash
cd /path/to/biodata

# Build
docker build -t biodata .

# Run (mounts the processed/ folder so files survive container restarts)
docker run -d \
  --name biodata-app \
  -p 8080:8080 \
  --link biodata-postgres:postgres \
  -v /host/path/to/processed:/app/processed \
  -v /host/path/to/inbox:/app/inbox \
  -e DATABASE_URL=postgresql://biodata:yourpassword@postgres/biodata \
  -e SECRET_KEY=<random-string> \
  -e ANTHROPIC_BASE_URL=https://api.servicesessentials.ibm.com \
  -e ANTHROPIC_AUTH_TOKEN=sk-... \
  -e ANTHROPIC_MODEL=claude-sonnet-4-6 \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=<your-pw> \
  --restart unless-stopped \
  biodata
```

> **Important:** The `processed/` folder must be on a **host-mounted volume** (`-v`), not inside the container. Otherwise photos and PDFs are lost when the container is rebuilt or restarted.

### First-run: initialise the database

```bash
docker exec biodata-app python3 migrate_phase2.py
```

---

## 6. Docker Compose (Recommended for VMs)

The cleanest way to manage both the app and Postgres together. Create a `docker-compose.yml` in the project root:

```yaml
version: "3.9"

services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: biodata
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: biodata
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U biodata"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    build: .
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8080:8080"
    environment:
      DATABASE_URL: postgresql://biodata:${DB_PASSWORD}@db/biodata
      SECRET_KEY: ${SECRET_KEY}
      ANTHROPIC_BASE_URL: ${ANTHROPIC_BASE_URL}
      ANTHROPIC_AUTH_TOKEN: ${ANTHROPIC_AUTH_TOKEN}
      ANTHROPIC_MODEL: ${ANTHROPIC_MODEL}
      PROCESSED_FOLDER: /app/processed
      WATCH_FOLDER: /app/inbox
      FAILED_FOLDER: /app/failed
    volumes:
      - ./processed:/app/processed
      - ./inbox:/app/inbox
      - ./failed:/app/failed
      - ./trash:/app/trash

volumes:
  pgdata:
```

Create a `.env` file alongside it (Docker Compose reads it automatically):

```env
DB_PASSWORD=choose-a-strong-password
SECRET_KEY=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
ANTHROPIC_BASE_URL=https://api.servicesessentials.ibm.com
ANTHROPIC_AUTH_TOKEN=sk-...
ANTHROPIC_MODEL=claude-sonnet-4-6
```

### Deploy

```bash
# Start everything
docker compose up -d

# First run only — initialise tables and admin user
docker compose exec app python3 migrate_phase2.py

# View logs
docker compose logs -f app

# Stop everything
docker compose down
```

---

## 7. First-Run Setup

These steps apply regardless of deployment method, and only need to be done **once**.

### Create tables and the admin user

```bash
# Direct (non-Docker)
ADMIN_USERNAME=admin ADMIN_PASSWORD=yourpassword python3 migrate_phase2.py

# Docker Compose
docker compose exec app python3 migrate_phase2.py

# Docker (standalone)
docker exec biodata-app python3 migrate_phase2.py
```

### Migrate existing data from SQLite (if you have it)

If you have an existing `biodata.db` SQLite file and want to move it to Postgres:

```bash
# Make sure DATABASE_URL points at the target Postgres instance
DATABASE_URL=postgresql://... python3 migrate_to_postgres.py
```

---

## 8. Ingesting Biodata Files

### Real-time (watcher)

Drop files into `inbox/` and they are processed automatically within seconds:

```bash
python3 watcher.py
```

To run as a background service, add a second systemd unit:

```ini
[Unit]
Description=Biodata Inbox Watcher
After=biodata.service

[Service]
Type=simple
User=<your-deploy-user>
WorkingDirectory=/opt/biodata
EnvironmentFile=/opt/biodata/.env
ExecStart=/opt/biodata/venv/bin/python3 watcher.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### Batch (run once)

Process everything currently sitting in `inbox/`:

```bash
python3 run_once.py
```

### Retry failed files

```bash
python3 retry_failed.py
```

### Re-run photo extraction

For candidates that were processed but have no portrait photo:

```bash
python3 retry_photos.py
```

### Convert DOCX to PDF

If some candidates' source files are Word documents and you want PDF download support, convert them using Microsoft Word (macOS) or LibreOffice (Linux):

```bash
# macOS (requires Microsoft Word installed) — uses docx2pdf
pip install docx2pdf
python3 -c "
from docx2pdf import convert
from pathlib import Path
for docx in Path('processed').glob('**/*.docx'):
    stem = docx.parent.name
    pdf  = docx.parent / f'{stem}.pdf'
    if not pdf.exists():
        print(f'Converting {docx.name}...')
        convert(str(docx), str(docx.parent))
"

# Linux (requires LibreOffice installed)
sudo apt install -y libreoffice
for docx in processed/**/*.docx; do
  libreoffice --headless --convert-to pdf --outdir "$(dirname "$docx")" "$docx"
done
```

---

## 9. Backups

```bash
# Dump the database to ./backups/biodata_<timestamp>.sql
python3 backup.py

# Custom output directory
python3 backup.py --out /mnt/backups

# Docker Compose
docker compose exec app python3 backup.py --out /app/backups
```

Set up a daily cron job on the VM:

```bash
crontab -e
```

Add:

```cron
0 3 * * * cd /opt/biodata && venv/bin/python3 backup.py --out /mnt/backups >> /var/log/biodata/backup.log 2>&1
```

---

## 10. Keeping the App Running

### Check service status

```bash
# systemd
sudo systemctl status biodata

# Docker Compose
docker compose ps
docker compose logs -f app
```

### Restart after a crash

With `systemd` and `Restart=on-failure`, the app restarts automatically. Same for Docker Compose's `restart: unless-stopped`.

### Keep Postgres running across reboots

Both systemd (`--now enable`) and Docker Compose (`restart: unless-stopped`) handle this automatically.

If you started Postgres manually with `docker run --restart unless-stopped`, it will also restart with Docker daemon on reboot.

---

## 11. Upgrading

```bash
# Pull latest code
git pull

# Install any new Python dependencies
pip install -r requirements.txt   # (activate venv first if using one)

# Restart the app
sudo systemctl restart biodata

# Docker Compose
docker compose build
docker compose up -d
```

> **Schema changes:** The app runs `init_db()` on startup which creates missing tables but does not alter existing ones. If a new column is added to `database.py`, run the corresponding `ALTER TABLE` statement manually on the Postgres DB before restarting.
