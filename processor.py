#!/usr/bin/env python3
"""
processor.py — Core extraction, deduplication, photo extraction and SQLite writing.
"""

import hashlib
import io
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

# ── optional OCR / doc deps ───────────────────────────────────────────────────
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pytesseract
    from PIL import Image as PILImage
except ImportError:
    pytesseract = None
    PILImage = None

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

# ── config ────────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

ANTHROPIC_BASE_URL   = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_AUTH_TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
ANTHROPIC_MODEL      = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
LOG_PATH             = Path(os.getenv("LOG_PATH", "./processing.log")).expanduser()
PROCESSED_FOLDER     = Path(os.getenv("PROCESSED_FOLDER", "./processed")).expanduser()
FAILED_FOLDER        = Path(os.getenv("FAILED_FOLDER", "")).expanduser() or (PROCESSED_FOLDER.parent / "failed")

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# Photo size constants (used only for resizing before saving to disk)
PHOTO_MAX_W = 240
PHOTO_MAX_H = 300


# ═════════════════════════════════════════════════════════════════════════════
# 1.  TEXT EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def extract_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(file_path)
    elif suffix == ".docx":
        return _extract_docx(file_path)
    elif suffix in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"):
        return _extract_image(file_path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


def _extract_pdf(path: Path) -> str:
    if fitz is None:
        raise ImportError("PyMuPDF not installed — run: pip install pymupdf")
    doc = fitz.open(str(path))
    parts = []
    for page in doc:
        text = page.get_text("text").strip()
        if text:
            parts.append(text)
        else:
            if pytesseract is None:
                log.warning("Page %s: no text layer and pytesseract not installed.", page.number)
                continue
            pix = page.get_pixmap(dpi=300)
            img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
            parts.append(pytesseract.image_to_string(img, lang="eng"))
    doc.close()
    return "\n".join(parts)


def _extract_docx(path: Path) -> str:
    if DocxDocument is None:
        raise ImportError("python-docx not installed — run: pip install python-docx")
    doc = DocxDocument(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_image(path: Path) -> str:
    if pytesseract is None or PILImage is None:
        raise ImportError("pytesseract/Pillow not installed — run: pip install pytesseract pillow")
    return pytesseract.image_to_string(PILImage.open(str(path)), lang="eng")


# ═════════════════════════════════════════════════════════════════════════════
# 2.  PHOTO EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def extract_photo(file_path: Path) -> Optional[bytes]:
    """
    Extract the candidate's portrait photo from a biodata file.

    Unified strategy for PDFs:
      For each page (up to 5), collect all embedded images.
      Classify each as either a "small portrait" (embedded separately, not full-page)
      or a "full-page scan" (the whole biodata stored as one image).

      - Small portraits: run through heuristic to find the best one directly.
      - Full-page scans: send to Claude Vision with crop coordinates; crop from
        the high-res embedded image (not a re-render) for best quality.

      Stop at the first page that yields a valid portrait.
    """
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return _extract_photo_from_pdf(file_path)
    elif suffix in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"):
        return _photo_from_image_vision(file_path)
    elif suffix == ".docx":
        return _photo_from_docx(file_path)
    return None


def _extract_photo_from_pdf(path: Path) -> Optional[bytes]:
    """
    Main PDF photo extraction. Tries all pages up to 5.
    For each page:
      1. Collect all embedded images, classify as small-portrait vs full-page-scan.
      2. If any small portraits exist, pick the best via heuristic scoring.
      3. If only full-page scans exist, use Claude Vision on the best (largest) one.
    Returns the first valid portrait found.
    """
    if fitz is None:
        return None

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        log.warning("Cannot open PDF %s: %s", path.name, exc)
        return None

    num_pages = len(doc)

    for page_index in range(min(num_pages, 5)):
        page = doc[page_index]
        rect = page.rect
        # Threshold: image wider than 50% of page = full-page scan
        page_w_px = rect.width * 150 / 72

        small_portraits = []   # (score, raw_bytes)
        full_page_scans = []   # (size_kb, raw_bytes)

        for img in page.get_images(full=True):
            xref = img[0]
            try:
                base = doc.extract_image(xref)
            except Exception:
                continue
            w, h    = base["width"], base["height"]
            size_kb = len(base["image"]) / 1024
            ratio   = h / w if w > 0 else 0

            # Detect full-page scans: image whose aspect ratio is close to the
            # page aspect ratio AND large enough to plausibly be the full page.
            # Real portrait photos embedded in a page have very different
            # aspect ratios from the A4/Letter page they're on.
            page_ratio = rect.height / rect.width if rect.width > 0 else 1.0
            ratio_match = abs(ratio - page_ratio) < 0.25   # within 25% of page ratio
            is_full_page = ratio_match and size_kb >= 10 and w >= 500

            if is_full_page:
                full_page_scans.append((size_kb, base["image"]))
            else:
                # Embedded portrait candidate: portrait ratio, reasonable size
                if ratio >= 0.75 and ratio <= 2.5 and size_kb >= 5 and w >= 50 and h >= 50:
                    score = size_kb * min(ratio, 1.5)
                    small_portraits.append((score, base["image"]))

        # --- Try small portraits first (fast, no API call) ---
        if small_portraits:
            small_portraits.sort(key=lambda x: x[0], reverse=True)
            best = _normalise_to_jpeg(small_portraits[0][1])
            if best:
                log.info("Heuristic portrait found on page %d of %s", page_index, path.name)
                doc.close()
                return best

        # --- Fall back to Vision on full-page scans ---
        if full_page_scans:
            # Try Vision on the largest embedded image first, then fall back
            # to a rendered version if the embedded image is too large for Vision
            full_page_scans.sort(key=lambda x: x[0], reverse=True)
            source_bytes = _normalise_to_jpeg(full_page_scans[0][1])
            if source_bytes:
                log.info("Full-page scan on page %d of %s — trying Claude Vision",
                         page_index, path.name)
                result = _photo_via_claude_vision(source_bytes,
                                                  f"{path.name}[p{page_index}]")
                if result:
                    doc.close()
                    return result

                # Embedded image was too large / Vision failed — try rendered page
                log.info("Retrying with rendered page for %s p%d", path.name, page_index)
                doc.close()
                rendered = _render_pdf_page(path, page_index=page_index, dpi=120)
                if rendered:
                    result = _photo_via_claude_vision(rendered,
                                                      f"{path.name}[p{page_index}-render]")
                    if result:
                        return result
                doc = fitz.open(str(path))  # reopen for next page iteration

    doc.close()
    return None


_VISION_PROMPT = """\
This is a page from an Indian matrimonial biodata document.

Find the BEST portrait/headshot photograph of the candidate on this page.

Rules for selecting the correct photo:
- PREFER: Close-up face and shoulders (passport-style or formal headshot)
- PREFER: The photo placed in the dedicated "photo" box of the biodata form
- ACCEPT: Upper-body photo (head + torso, no legs visible)
- REJECT: Full-body photos where legs are clearly visible
- REJECT: Casual outdoor/travel photos (if a formal one also exists)
- REJECT: Group photos with multiple people
- REJECT: Logos, icons, watermarks, or decorative elements
- REJECT: Horizontal banner images (width much greater than height)
- If multiple photos exist, pick the most formal/close-up one

If you find a suitable portrait, respond with ONLY a JSON object — no other text, no markdown fences:
{"found": true, "x": <left_px>, "y": <top_px>, "w": <width_px>, "h": <height_px>}

Coordinates are pixel positions in the image you received. Be generous — include full face, hair, and shoulders. Add ~5% padding on each side to avoid cutting edges.

If there is NO suitable portrait (placeholder/empty box, full-body only, truly no photo), respond ONLY:
{"found": false}
"""


def _render_pdf_page(path: Path, page_index: int = 0, dpi: int = 150) -> Optional[bytes]:
    """Render a PDF page to JPEG bytes at the given DPI."""
    if fitz is None or PILImage is None:
        return None
    try:
        doc  = fitz.open(str(path))
        page = doc[page_index]
        mat  = fitz.Matrix(dpi / 72, dpi / 72)
        pix  = page.get_pixmap(matrix=mat, alpha=False)
        img  = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
        doc.close()
        return _pil_to_jpeg(img)
    except Exception as exc:
        log.warning("PDF render failed for %s: %s", path.name, exc)
        return None


def _photo_via_claude_vision(image_bytes: bytes, source_name: str) -> Optional[bytes]:
    """
    Send an image to Claude Vision, get crop coordinates, crop from the
    original high-res image and return JPEG bytes.

    Downscales to max 1568px wide for sending (API limit / cost), but crops
    from the original full-resolution image for quality.
    """
    if not image_bytes or PILImage is None:
        return None

    import base64
    import json as _json

    # Load original image
    try:
        orig_img = PILImage.open(io.BytesIO(image_bytes))
        orig_w, orig_h = orig_img.size
    except Exception:
        return None

    # Downscale for sending to Claude (keeps cost low, 1568px is model max)
    MAX_SEND = 1200   # stay well under API limits; 1200px is sharp enough
    if orig_w > MAX_SEND:
        scale    = MAX_SEND / orig_w
        send_img = orig_img.resize((MAX_SEND, int(orig_h * scale)), PILImage.LANCZOS)
        send_bytes = _pil_to_jpeg(send_img, quality=82)
        send_w, send_h = send_img.size
    else:
        send_bytes = _pil_to_jpeg(orig_img, quality=82)
        send_w, send_h = orig_w, orig_h

    b64 = base64.standard_b64encode(send_bytes).decode("utf-8")

    try:
        client = anthropic.Anthropic(
            base_url=ANTHROPIC_BASE_URL,
            api_key=ANTHROPIC_AUTH_TOKEN,
        )
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=120,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": _VISION_PROMPT,
                    },
                ],
            }],
        )

        text = message.content[0].text.strip()

        # Strip markdown fences if present
        import re as _re
        text = _re.sub(r"^```[a-z]*\n?", "", text)
        text = _re.sub(r"\n?```$", "", text)

        result = _json.loads(text)

        if not result.get("found"):
            log.info("Claude Vision: no portrait in %s", source_name)
            return None

        # Crop coordinates are in the downscaled send image space
        x = int(result.get("x", 0))
        y = int(result.get("y", 0))
        w = int(result.get("w", 0))
        h = int(result.get("h", 0))

        if w < 20 or h < 20:
            log.warning("Claude Vision: implausible crop %s for %s", result, source_name)
            return None

        # Scale coordinates back to full-resolution original image
        if send_w != orig_w:
            sx = orig_w  / send_w
            sy = orig_h  / send_h
            x  = int(x * sx)
            y  = int(y * sy)
            w  = int(w * sx)
            h  = int(h * sy)

        # Clamp to image bounds
        x = max(0, min(x, orig_w - 1))
        y = max(0, min(y, orig_h - 1))
        w = max(1, min(w, orig_w - x))
        h = max(1, min(h, orig_h - y))

        cropped = orig_img.crop((x, y, x + w, y + h))
        log.info("Claude Vision: cropped %dx%d portrait from %s", w, h, source_name)
        return _pil_to_jpeg(cropped)

    except _json.JSONDecodeError:
        log.warning("Claude Vision non-JSON for %s: %s", source_name,
                    locals().get("text", "")[:120])
        return None
    except Exception as exc:
        log.warning("Claude Vision failed for %s: %s", source_name, exc)
        return None


def _photo_from_image_vision(path: Path) -> Optional[bytes]:
    """For image biodatas, use Claude Vision to crop the portrait."""
    if PILImage is None:
        return None
    try:
        img_bytes = path.read_bytes()
        result    = _photo_via_claude_vision(img_bytes, path.name)
        if result is None:
            # Vision found nothing — return the full image as fallback
            return _normalise_to_jpeg(img_bytes)
        return result
    except Exception:
        return None




def _photo_from_docx(path: Path) -> Optional[bytes]:
    if DocxDocument is None or PILImage is None:
        return None
    doc = DocxDocument(str(path))
    best_bytes = None
    best_score = 0.0

    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                blob = rel.target_part.blob
                img  = PILImage.open(io.BytesIO(blob))
                w, h = img.size
                size_kb = len(blob) / 1024
                ratio = h / w if w > 0 else 0
                if ratio >= 0.85 and size_kb >= 15 and w >= 60:
                    score = size_kb * max(ratio, 1.0)
                    if score > best_score:
                        best_score = score
                        best_bytes = blob
            except Exception:
                continue

    if best_bytes:
        return _normalise_to_jpeg(best_bytes)
    return None


def _normalise_to_jpeg(raw: bytes) -> bytes:
    if PILImage is None:
        return raw
    try:
        img = PILImage.open(io.BytesIO(raw))
        return _pil_to_jpeg(img)
    except Exception:
        return raw


def _pil_to_jpeg(img: "PILImage.Image", quality: int = 95) -> bytes:
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def candidate_folder(stem: str) -> Path:
    """Return (and create) the per-candidate folder: processed/<stem>/"""
    folder = PROCESSED_FOLDER / stem
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_photo(photo_bytes: bytes, stem: str) -> Path:
    """Save photo into the candidate's folder as <stem>.jpg."""
    folder = candidate_folder(stem)
    dest   = folder / f"{stem}.jpg"
    dest.write_bytes(photo_bytes)
    return dest


# ═════════════════════════════════════════════════════════════════════════════
# 3.  FIELD EXTRACTION  (Claude)
# ═════════════════════════════════════════════════════════════════════════════

EXTRACTION_PROMPT = """\
You are a data-extraction assistant. Below is raw text from an Indian matrimonial biodata.

Extract the following fields and return ONLY a valid JSON object — no commentary, no markdown fences.
Use "Not Mentioned" for any field that is not found or unclear.

Fields to extract:
- name               : Full name of the candidate (string)
- date_of_birth      : In dd/mm/yyyy format (string)
- gender             : "Male" or "Female" — infer from name/context if not stated (string)
- height             : e.g. "5'4\\"" or "163 cm" (string)
- weight             : e.g. "55 kg" or "52 kgs" — use "Not Mentioned" if not in the biodata (string)
- caste              : Caste or sub-caste (string)
- education          : Highest qualification (string)
- occupation         : Job title / designation (string)
- job_location       : City (and country if abroad) where the candidate works (string)
- residential_city   : City where the candidate currently lives (string)
- fathers_name       : Father's full name (string)
- fathers_contact    : Father's phone/mobile number (string)
- mothers_name       : Mother's full name (string)
- mothers_contact    : Mother's phone/mobile number (string)
- candidate_contact  : Candidate's own phone/mobile number (string)

Biodata text:
\"\"\"
{text}
\"\"\"
"""


def extract_fields_with_claude(raw_text: str) -> dict:
    client = anthropic.Anthropic(
        base_url=ANTHROPIC_BASE_URL,
        api_key=ANTHROPIC_AUTH_TOKEN,
    )
    prompt = EXTRACTION_PROMPT.format(text=raw_text[:6000])
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("Claude returned non-JSON: %s", raw[:300])
        raise ValueError("Claude did not return valid JSON") from exc


# ═════════════════════════════════════════════════════════════════════════════
# 4.  FILE RENAMING
# ═════════════════════════════════════════════════════════════════════════════

def rename_file(file_path: Path, fields: dict) -> Path:
    """Rename to <birth_year>_<Name>.<ext>. Falls back to original stem if data missing."""
    name = fields.get("name") or ""
    dob  = fields.get("date_of_birth") or ""

    match = re.search(r"\d{1,2}/\d{1,2}/(\d{4})", dob)
    birth_year = match.group(1) if match else ""

    safe_name = re.sub(r"[^\w\s]", "", name).strip()
    safe_name = re.sub(r"\s+", "_", safe_name)

    if safe_name.lower() in ("not_mentioned", "not mentioned", ""):
        safe_name = ""

    if not safe_name and not birth_year:
        log.warning("Cannot rename %s — name and DOB both missing.", file_path.name)
        return file_path

    if not safe_name:
        original_stem = re.sub(r"[^\w\s]", "", file_path.stem).strip()
        original_stem = re.sub(r"\s+", "_", original_stem)
        safe_name = original_stem

    parts = [p for p in [birth_year, safe_name] if p]
    new_stem = "_".join(parts)
    new_name = new_stem + file_path.suffix.lower()
    new_path = file_path.parent / new_name

    if new_path.exists() and new_path != file_path:
        counter = 1
        while new_path.exists():
            new_path = file_path.parent / f"{new_stem}_{counter}{file_path.suffix.lower()}"
            counter += 1

    if new_path != file_path:
        file_path.rename(new_path)
        log.info("Renamed  %s  →  %s", file_path.name, new_path.name)

    return new_path


# ═════════════════════════════════════════════════════════════════════════════
# 5.  HASH
# ═════════════════════════════════════════════════════════════════════════════

def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ═════════════════════════════════════════════════════════════════════════════
# 6.  MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def process_file(file_path: Path) -> None:
    """Full pipeline for a single file (serial — wraps prepare + commit)."""
    result = prepare_file(file_path)
    if result:
        commit_result(result)


# ── split pipeline: parallel-safe prepare, then serial commit ────────────────

def prepare_file(file_path: Path) -> Optional[dict]:
    """
    Phase 1 — PARALLEL-SAFE.
    Does all the slow work (OCR, photo extraction, Claude API call).
    Returns a result dict for commit_result(), or None if the file should be skipped.
    Does NOT touch the DB, move, or rename anything.
    """
    file_path = file_path.resolve()

    if not file_path.exists():
        log.info("Skipping %s — file no longer exists.", file_path.name)
        return None

    log.info("━━━ Preparing  %s", file_path.name)

    fhash = file_hash(file_path)

    # Extract text
    try:
        raw_text = extract_text(file_path)
    except Exception as exc:
        log.error("Text extraction failed for %s: %s", file_path.name, exc)
        return {"status": "failed", "file_path": file_path, "reason": str(exc)}

    if not raw_text.strip():
        log.warning("No text found in %s.", file_path.name)
        return {"status": "failed", "file_path": file_path, "reason": "no text extracted"}

    # Extract photo
    photo_bytes = None
    try:
        photo_bytes = extract_photo(file_path)
        if photo_bytes:
            log.info("Photo extracted (%d KB) from %s", len(photo_bytes) // 1024, file_path.name)
        else:
            log.info("No photo found in %s", file_path.name)
    except Exception as exc:
        log.warning("Photo extraction failed for %s: %s", file_path.name, exc)

    # Claude field extraction
    try:
        fields = extract_fields_with_claude(raw_text)
    except Exception as exc:
        log.error("Claude extraction failed for %s: %s", file_path.name, exc)
        return {"status": "failed", "file_path": file_path, "reason": str(exc)}

    log.info("Fields ready for %s: %s", file_path.name, json.dumps(fields, ensure_ascii=False))

    return {
        "status":      "ready",
        "file_path":   file_path,
        "fhash":       fhash,
        "fields":      fields,
        "photo_bytes": photo_bytes,
    }


def commit_result(result: dict) -> None:
    """
    Phase 2 — SERIAL (call from one thread only).
    Handles dedup check, rename, SQLite insert, photo save, file move.
    """
    # Import here to avoid circular issues if database imports processor
    from database import (
        folder_stem_exists, hash_exists, name_dob_exists, insert_candidate
    )

    file_path = result["file_path"]

    # Failed during prepare — just move to failed/
    if result["status"] == "failed":
        if file_path.exists():
            _move_to_failed(file_path, reason=result.get("reason", ""))
        return

    fhash       = result["fhash"]
    fields      = result["fields"]
    photo_bytes = result["photo_bytes"]

    # ── Compute the target stem (same logic as rename_file) ──────────────────
    name  = fields.get("name") or ""
    dob   = fields.get("date_of_birth") or ""

    match = re.search(r"\d{1,2}/\d{1,2}/(\d{4})", dob)
    birth_year = match.group(1) if match else ""
    safe_name  = re.sub(r"[^\w\s]", "", name).strip()
    safe_name  = re.sub(r"\s+", "_", safe_name)
    if safe_name.lower() in ("not_mentioned", "not mentioned", ""):
        safe_name = re.sub(r"[^\w\s]", "", file_path.stem).strip()
        safe_name = re.sub(r"\s+", "_", safe_name)
    parts       = [p for p in [birth_year, safe_name] if p]
    target_stem = "_".join(parts) if parts else file_path.stem

    # ── Dedup check 1: candidate folder already exists ───────────────────────
    target_folder = PROCESSED_FOLDER / target_stem
    if target_folder.exists():
        log.info("DUPLICATE (folder exists): %s → %s already in processed/ — skipped.",
                 file_path.name, target_stem)
        _move_to_processed_as_duplicate(file_path, target_stem)
        return

    # ── Dedup check 2: hash already in DB ────────────────────────────────────
    if hash_exists(fhash):
        log.info("DUPLICATE (hash match): %s — skipped.", file_path.name)
        _move_to_processed_as_duplicate(file_path, target_stem)
        return

    # ── Dedup check 3: Name + DOB already in DB ──────────────────────────────
    if name and dob:
        if name_dob_exists(name, dob):
            log.info("DUPLICATE (Name+DOB match): %s / %s — skipped.", name, dob)
            _move_to_processed_as_duplicate(file_path, target_stem)
            return

    # ── Rename file ───────────────────────────────────────────────────────────
    file_path = rename_file(file_path, fields)

    # ── Save photo ────────────────────────────────────────────────────────────
    if photo_bytes:
        photo_path = save_photo(photo_bytes, file_path.stem)
        log.info("Photo saved → %s", photo_path.name)

    # ── Insert into SQLite ────────────────────────────────────────────────────
    fields["source_file"] = file_path.name
    candidate_id = insert_candidate(fields, fhash, file_path.stem)
    log.info("✓ Inserted candidate id=%d for  %s", candidate_id, file_path.name)

    # ── Move file to processed/<stem>/ ───────────────────────────────────────
    _move_to_processed(file_path)


# ═════════════════════════════════════════════════════════════════════════════
# 7.  MOVE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _move_to_processed(file_path: Path) -> None:
    """Move file into its per-candidate folder: processed/<stem>/<stem>.ext"""
    stem   = file_path.stem
    folder = candidate_folder(stem)
    dest   = folder / file_path.name
    if dest.exists():
        counter = 1
        while dest.exists():
            dest = folder / f"{stem}_{counter}{file_path.suffix}"
            counter += 1
    shutil.move(str(file_path), str(dest))
    log.info("Moved  %s  →  processed/%s/", file_path.name, stem)


def _move_to_processed_as_duplicate(file_path: Path, target_stem: str) -> None:
    """
    Move a duplicate into the existing candidate's folder prefixed with 'duplicate_'.
    """
    if not file_path.exists():
        return
    folder = PROCESSED_FOLDER / target_stem
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"duplicate_{file_path.name}"
    counter = 1
    while dest.exists():
        dest = folder / f"duplicate_{file_path.stem}_{counter}{file_path.suffix}"
        counter += 1
    shutil.move(str(file_path), str(dest))
    log.info("Moved duplicate  %s  →  processed/%s/duplicate_*", file_path.name, target_stem)


def _move_to_failed(file_path: Path, reason: str = "") -> None:
    FAILED_FOLDER.mkdir(parents=True, exist_ok=True)
    dest = FAILED_FOLDER / file_path.name
    if dest.exists():
        counter = 1
        while dest.exists():
            dest = FAILED_FOLDER / f"{file_path.stem}_{counter}{file_path.suffix}"
            counter += 1
    shutil.move(str(file_path), str(dest))
    log.warning("⚠ Moved  %s  →  failed/   reason: %s", file_path.name, reason)
