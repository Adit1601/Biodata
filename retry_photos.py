#!/usr/bin/env python3
"""
retry_photos.py — Re-extract photos for candidates who are missing one.

Uses the improved Claude Vision photo extraction.
Does NOT re-run OCR or Claude field extraction — only the photo step.

Usage:
    python3 retry_photos.py              # all candidates missing a photo
    python3 retry_photos.py --all        # re-extract ALL photos (replace existing)
    python3 retry_photos.py --workers 5
"""

import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import os
PROCESSED_FOLDER = Path(os.getenv("PROCESSED_FOLDER", "./processed")).expanduser()

from processor import extract_photo, save_photo, log
import database as db

_lock     = threading.Lock()
_counters = {"done": 0, "total": 0, "found": 0, "not_found": 0, "errors": 0}

def _tick(status: str, name: str) -> None:
    with _lock:
        _counters["done"] += 1
        _counters[status]  = _counters.get(status, 0) + 1
        done  = _counters["done"]
        total = _counters["total"]
        found = _counters["found"]
        filled = int(done / total * 28) if total else 0
        bar   = "█" * filled + "░" * (28 - filled)
        label = name[:36]
        print(f"\r[{bar}] {done}/{total}  ✓{found} photos  {label:<36}", end="", flush=True)


def retry_one(candidate: dict) -> str:
    stem   = candidate["folder_stem"]
    folder = PROCESSED_FOLDER / stem

    # Find the PDF or image in the candidate folder
    file_path = None
    for ext in (".pdf", ".jpg", ".jpeg", ".png", ".docx"):
        p = folder / f"{stem}{ext}"
        if p.exists():
            file_path = p
            break
    if file_path is None:
        # Search for any supported file
        for ext in (".pdf", ".jpg", ".jpeg", ".png", ".docx"):
            matches = [f for f in folder.iterdir()
                       if f.suffix.lower() == ext and not f.name.startswith("duplicate_")]
            if matches:
                file_path = matches[0]
                break

    if file_path is None:
        log.warning("No source file found for %s", stem)
        return "errors"

    try:
        photo_bytes = extract_photo(file_path)
        if photo_bytes:
            save_photo(photo_bytes, stem)
            log.info("Photo saved for %s (%d KB)", stem, len(photo_bytes) // 1024)
            return "found"
        else:
            log.info("No photo found for %s", stem)
            return "not_found"
    except Exception as exc:
        log.error("Error extracting photo for %s: %s", stem, exc)
        return "errors"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all",     action="store_true", help="Re-extract all photos, not just missing ones")
    parser.add_argument("--workers", type=int, default=5, help="Parallel workers (default: 5)")
    args = parser.parse_args()

    candidates = db.get_candidates({}, offset=0, limit=10000, user_id=None)

    if args.all:
        targets = candidates
        print(f"Re-extracting photos for all {len(targets)} candidates.")
    else:
        targets = [
            c for c in candidates
            if not (PROCESSED_FOLDER / c["folder_stem"] / f"{c['folder_stem']}.jpg").exists()
        ]
        print(f"Found {len(targets)} candidates missing a photo.")

    if not targets:
        print("Nothing to do.")
        return

    _counters["total"] = len(targets)
    workers = min(args.workers, len(targets))
    print(f"Running with {workers} workers (Claude Vision for failures).\n")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_c = {pool.submit(retry_one, c): c for c in targets}
        for future in as_completed(future_to_c):
            c = future_to_c[future]
            try:
                status = future.result()
            except Exception as exc:
                log.exception("Worker crashed on %s: %s", c["folder_stem"], exc)
                status = "errors"
            _tick(status, c["name"] or c["folder_stem"])

    print()
    c = _counters
    print(f"\nDone.  ✓ {c['found']} photos found  |  ✗ {c['not_found']} still missing  |  ⚠ {c['errors']} errors")


if __name__ == "__main__":
    main()
