#!/usr/bin/env python3
"""
retry_failed.py — Move all files from /failed back to /inbox and reprocess them.

Usage:
    python3 retry_failed.py
"""

from pathlib import Path
from dotenv import load_dotenv
import os, shutil

load_dotenv(Path(__file__).parent / ".env")

FAILED_FOLDER    = Path(os.getenv("FAILED_FOLDER", "./failed")).expanduser()
WATCH_FOLDER     = Path(os.getenv("WATCH_FOLDER",  "./inbox")).expanduser()
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}

from processor import process_file, log

def main():
    if not FAILED_FOLDER.exists():
        print("No failed/ folder found — nothing to retry.")
        return

    files = [f for f in FAILED_FOLDER.iterdir()
             if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]

    if not files:
        print("No files in failed/ — nothing to retry.")
        return

    print(f"Retrying {len(files)} failed file(s)...\n")
    ok = errors = 0

    for f in files:
        # Move back to inbox first, then process in place from failed/
        # (process directly from failed/ so we don't need to move twice)
        print(f"  → {f.name}")
        try:
            process_file(f)
            ok += 1
        except Exception as exc:
            log.exception("Still failing: %s: %s", f.name, exc)
            errors += 1

    print(f"\nDone.  ✓ {ok} succeeded  |  ✗ {errors} still failing")

if __name__ == "__main__":
    main()
