#!/usr/bin/env python3
"""
watcher.py — Folder watcher daemon.
Drop any biodata file into /inbox and it is processed automatically.

Usage:
    python watcher.py
"""

import sys
import time
import logging
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent / ".env")

WATCH_FOLDER = Path(os.getenv("WATCH_FOLDER", "./inbox")).expanduser()
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}

# ── watchdog ─────────────────────────────────────────────────────────────────
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("watchdog is not installed. Run:  pip install watchdog")
    sys.exit(1)

from processor import process_file

log = logging.getLogger(__name__)


class BiodataHandler(FileSystemEventHandler):
    """Triggered whenever a file is created/moved into the watch folder."""

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.src_path))

    def on_moved(self, event):
        # Covers files moved/copied INTO the folder
        if event.is_directory:
            return
        self._handle(Path(event.dest_path))

    def _handle(self, path: Path):
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            log.info("Ignored (unsupported type): %s", path.name)
            return

        # Wait until the file has fully landed (size stable for 2 consecutive checks)
        self._wait_for_stable(path)

        if not path.exists():
            return  # file was already moved away

        try:
            process_file(path)
        except Exception as exc:
            log.exception("Unhandled error processing %s: %s", path.name, exc)

    @staticmethod
    def _wait_for_stable(path: Path, interval: float = 1.0, stable_rounds: int = 2):
        """Block until the file size stops changing for `stable_rounds` consecutive checks."""
        last_size = -1
        stable = 0
        while stable < stable_rounds:
            time.sleep(interval)
            if not path.exists():
                return
            try:
                size = path.stat().st_size
            except OSError:
                return
            if size == last_size:
                stable += 1
            else:
                stable = 0
            last_size = size


def main():
    WATCH_FOLDER.mkdir(parents=True, exist_ok=True)
    print(f"\n👀  Watching  {WATCH_FOLDER}  for new biodata files …")
    print("    Drop a PDF, DOCX, or image file into that folder.")
    print("    Press  Ctrl+C  to stop.\n")

    observer = Observer()
    observer.schedule(BiodataHandler(), str(WATCH_FOLDER), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopping watcher …")
        observer.stop()

    observer.join()
    print("Watcher stopped.")


if __name__ == "__main__":
    main()
