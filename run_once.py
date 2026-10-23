#!/usr/bin/env python3
"""
run_once.py — Batch-process all files in /inbox with parallel workers.

Optimisations vs the naive approach:
  1. Hash-dedup in the inbox BEFORE calling Claude — skips identical files instantly
     (no OCR, no Claude call, no cost).  With 757 files where 534 are duplicates
     this alone cuts ~70% of Claude API calls.
  2. Commit-as-you-go — each worker commits immediately after it finishes,
     so processed/ folders and DB rows appear in real time.
  3. Higher default worker count — Claude + IBM proxy handles 15 concurrent
     requests comfortably; OCR is CPU-bound but fast.

Usage:
    python3 run_once.py                # default 15 workers
    python3 run_once.py --workers 20
"""

import argparse
import hashlib
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

WATCH_FOLDER         = Path(os.getenv("WATCH_FOLDER", "./inbox")).expanduser()
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
DEFAULT_WORKERS      = 15

from processor import prepare_file, commit_result, log


# ── progress ──────────────────────────────────────────────────────────────────
_lock     = threading.Lock()
_counters = {"done": 0, "total": 0, "added": 0, "skipped": 0, "errors": 0}

def _tick(status: str, filename: str) -> None:
    with _lock:
        _counters["done"]   += 1
        _counters[status]    = _counters.get(status, 0) + 1
        done  = _counters["done"]
        total = _counters["total"]
        added = _counters["added"]
        filled = int(done / total * 28) if total else 0
        bar   = "█" * filled + "░" * (28 - filled)
        label = filename[:34]
        print(f"\r[{bar}] {done}/{total}  +{added} added  {label:<34}", end="", flush=True)


# ── inbox dedup ───────────────────────────────────────────────────────────────
def _fast_hash(path: Path) -> str:
    """MD5 of first 256 KB — fast enough to dedup large PDFs without reading all."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(262144))   # 256 KB
    return h.hexdigest()


def dedup_inbox(files: list) -> tuple:
    """
    Among all files in the inbox, keep only one representative per unique content.
    Returns (to_process, skipped_count).
    Picks the file with the shortest name as the canonical one (usually the
    original, non-_1/_2 suffixed version).
    """
    print("Scanning inbox for duplicates…", end=" ", flush=True)
    groups = defaultdict(list)
    for f in files:
        try:
            groups[_fast_hash(f)].append(f)
        except OSError:
            pass

    to_process = []
    skipped    = 0
    for group in groups.values():
        # Pick shortest name as canonical (e.g. "Priya.pdf" over "Priya_1.pdf")
        canonical = min(group, key=lambda p: len(p.name))
        to_process.append(canonical)
        skipped += len(group) - 1

    print(f"found {len(files)} files → {len(to_process)} unique, {skipped} duplicates skipped.")
    return to_process, skipped


# ── worker ────────────────────────────────────────────────────────────────────
_commit_lock = threading.Lock()

def _prepare_and_commit(file_path: Path) -> str:
    """Prepare (parallel) then commit immediately under the serial lock."""
    try:
        result = prepare_file(file_path)
    except Exception as exc:
        log.exception("Unexpected error preparing %s: %s", file_path.name, exc)
        result = {"status": "failed", "file_path": file_path.resolve(), "reason": str(exc)}

    if result is None:
        return "skipped"

    with _commit_lock:
        try:
            commit_result(result)
            if result["status"] == "ready":
                with _lock:
                    _counters["added"] += 1
                return "added"
            else:
                return "errors"
        except Exception as exc:
            log.exception("Error committing %s: %s", file_path.name, exc)
            return "errors"


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Batch-process biodata files in parallel.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Parallel workers (default: {DEFAULT_WORKERS})")
    parser.add_argument("--no-dedup", action="store_true",
                        help="Skip inbox dedup and process every file")
    args = parser.parse_args()

    all_files = [
        f for f in WATCH_FOLDER.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not all_files:
        print("No files found in", WATCH_FOLDER)
        return

    # ── Step 1: dedup inbox (free — no Claude calls) ──────────────────────────
    inbox_skipped = 0
    if args.no_dedup:
        files = all_files
    else:
        files, inbox_skipped = dedup_inbox(all_files)

    total   = len(files)
    workers = min(args.workers, total)
    _counters["total"]   = total
    _counters["skipped"] = inbox_skipped

    print(f"\nProcessing {total} unique file(s) with {workers} workers.\n")

    # ── Step 2: parallel prepare + immediate commit ────────────────────────────
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_file = {pool.submit(_prepare_and_commit, f): f for f in files}

        for future in as_completed(future_to_file):
            src_file = future_to_file[future]
            try:
                status = future.result()
            except Exception as exc:
                log.exception("Worker crashed on %s: %s", src_file.name, exc)
                status = "errors"
            _tick(status, src_file.name)

    print()   # newline after progress bar

    c = _counters
    total_skipped = c["skipped"]   # inbox dups + DB dups
    print(f"\nDone.  ✓ {c['added']} added  |  ⟳ {total_skipped} skipped  |  ✗ {c['errors']} errors")


if __name__ == "__main__":
    main()
