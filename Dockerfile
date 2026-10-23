# ── Build stage: install Python deps ─────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# System deps for psycopg2-binary, Pillow, pytesseract, pymupdf
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Folders that need to exist at runtime
RUN mkdir -p processed inbox failed trash

# Gunicorn listens on $PORT (Render/Railway inject this; default 8080 for local)
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "gunicorn server:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile -"]
