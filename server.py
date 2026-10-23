#!/usr/bin/env python3
"""
server.py — Biodata web server (Phase 2: multi-user with auth).

Usage:
    python3 server.py
Then open http://localhost:8080
"""

import io
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from flask import (
    Flask, abort, jsonify, redirect, render_template,
    request, send_file, url_for, flash,
)
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user, login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash

import database as db

load_dotenv(Path(__file__).parent / ".env")

PROCESSED_FOLDER = Path(os.getenv("PROCESSED_FOLDER", "./processed")).expanduser()

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

# ═════════════════════════════════════════════════════════════════════════════
# Flask-Login setup
# ═════════════════════════════════════════════════════════════════════════════

login_manager = LoginManager(app)
login_manager.login_view = "login"          # redirect here when @login_required fails
login_manager.login_message = "Please log in to access the biodata viewer."
login_manager.login_message_category = "info"


class User(UserMixin):
    """Thin wrapper so Flask-Login can work with our DB rows."""
    def __init__(self, row: dict):
        self.id       = row["id"]
        self.username = row["username"]
        self.role     = row["role"]
        self.active   = row["active"]

    def get_id(self) -> str:
        return str(self.id)

    @property
    def is_active(self) -> bool:
        return bool(self.active)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    row = db.get_user_by_id(int(user_id))
    return User(row) if row else None


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _photo_url(folder_stem: Optional[str]) -> str:
    if not folder_stem:
        return ""
    jpg = PROCESSED_FOLDER / folder_stem / f"{folder_stem}.jpg"
    return f"/candidate/{folder_stem}/photo" if jpg.exists() else ""


def _pdf_url(folder_stem: Optional[str], source_file: Optional[str]) -> str:
    if not folder_stem:
        return ""
    pdf = PROCESSED_FOLDER / folder_stem / f"{folder_stem}.pdf"
    if pdf.exists():
        return f"/candidate/{folder_stem}/pdf"
    folder = PROCESSED_FOLDER / folder_stem
    if folder.exists():
        pdfs = [p for p in folder.glob("*.pdf") if not p.name.startswith("duplicate_")]
        if pdfs:
            return f"/candidate/{folder_stem}/pdf"
    return ""


def _enrich(c: dict) -> dict:
    """Add photo_url and pdf_url to a candidate dict."""
    stem = c.get("folder_stem") or ""
    c["photo_url"] = _photo_url(stem)
    c["pdf_url"]   = _pdf_url(stem, c.get("source_file"))
    return c


def _parse_filters() -> dict:
    """Parse filter query params from the current request."""
    def intv(k: str) -> Optional[int]:
        v = request.args.get(k, "").strip()
        try:
            return int(v) if v else None
        except ValueError:
            return None

    def floatv(k: str) -> Optional[float]:
        v = request.args.get(k, "").strip()
        try:
            return float(v) if v else None
        except ValueError:
            return None

    label_ids_raw = request.args.get("label_ids", "").strip()
    label_ids = []
    if label_ids_raw:
        for part in label_ids_raw.split(","):
            part = part.strip()
            if part.isdigit():
                label_ids.append(int(part))

    h_min = intv("height_min")
    h_max = intv("height_max")
    # B-4: if min > max, drop both so results aren't silently empty
    if h_min and h_max and h_min > h_max:
        h_min = h_max = None

    return {
        "height_min":       h_min,
        "height_max":       h_max,
        "birth_year_from":  intv("birth_year_from"),
        "birth_year_to":    intv("birth_year_to"),
        "weight_max":       floatv("weight_max"),
        "gender":           request.args.get("gender", "").strip() or None,
        "label_ids":        label_ids,
        "search":           request.args.get("search", "").strip() or None,
        "caste":            request.args.get("caste", "").strip() or None,
        "residential_city": request.args.get("residential_city", "").strip() or None,
        "job_location":     request.args.get("job_location", "").strip() or None,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Auth routes
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        remember = bool(request.form.get("remember"))

        row = db.get_user_by_username(username)
        if row and row["active"] and check_password_hash(row["password_hash"], password):
            user = User(row)
            login_user(user, remember=remember)
            next_page = request.args.get("next") or url_for("index")
            # Safety: only redirect to relative paths
            if next_page.startswith("http"):
                next_page = url_for("index")
            return redirect(next_page)
        elif row and not row["active"]:
            error = "Your account has been deactivated. Contact the admin."
        else:
            error = "Incorrect username or password."

    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm  = request.form.get("confirm", "").strip()

        if not username or not password:
            error = "Username and password are required."
        elif len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif db.username_exists(username):
            error = "That username is already taken."
        else:
            pw_hash = generate_password_hash(password, method="pbkdf2:sha256")
            user_id = db.create_user(username, pw_hash, role="user")
            row     = db.get_user_by_id(user_id)
            login_user(User(row), remember=True)
            return redirect(url_for("index"))

    return render_template("register.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ═════════════════════════════════════════════════════════════════════════════
# Page routes  (all require login)
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/")
@login_required
def index():
    uid           = current_user.id
    labels        = db.get_all_labels(user_id=uid)
    views         = db.get_views(user_id=uid)
    years         = db.get_distinct_years()
    total         = db.count_candidates({})
    has_profile   = db.get_candidate_by_user(uid) is not None
    pending_count = db.count_pending_claims() if current_user.is_admin else 0
    res_cities    = db.get_distinct_cities("residential_city")
    job_locations = db.get_distinct_cities("job_location")
    return render_template("index.html",
        labels=labels,
        views=views,
        years=years,
        total=total,
        has_profile=has_profile,
        pending_count=pending_count,
        res_cities=res_cities,
        job_locations=job_locations,
        current_user=current_user,
    )


@app.route("/settings")
@login_required
def settings():
    # Settings page has been removed — label management is now in the label dropdown
    return redirect(url_for("profile_me"))


@app.route("/view/<int:view_id>")
@login_required
def view_page(view_id: int):
    uid   = current_user.id
    views = db.get_views(user_id=uid)
    view  = next((v for v in views if v["id"] == view_id), None)
    if not view:
        abort(404)
    labels = db.get_all_labels(user_id=uid)
    return render_template("selected.html",
        view=view,
        labels=labels,
        all_views=views,
        current_user=current_user,
    )


# ═════════════════════════════════════════════════════════════════════════════
# File serving  (require login)
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/candidate/<stem>/photo", methods=["GET", "POST"])
@login_required
def serve_photo(stem):
    if request.method == "POST":
        return upload_photo(stem)
    path = PROCESSED_FOLDER / stem / f"{stem}.jpg"
    if not path.exists():
        abort(404)
    return send_file(str(path), mimetype="image/jpeg")


def upload_photo(stem: str):
    """Replace a candidate's photo with an uploaded file."""
    folder = PROCESSED_FOLDER / stem
    if not folder.exists():
        return jsonify({"error": "Candidate folder not found"}), 404

    # ── Ownership check ──────────────────────────────────────────────────────
    # Admin may upload to any profile.
    # A regular user may only upload to the profile they own.
    if not current_user.is_admin:
        candidate = db.get_candidate_by_stem(stem)
        if candidate is None:
            return jsonify({"error": "Candidate not found"}), 404
        if candidate.get("created_by_user_id") != current_user.id:
            return jsonify({"error": "Forbidden"}), 403

    if "photo" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["photo"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed:
        return jsonify({"error": f"Unsupported file type: {suffix}"}), 400

    try:
        from PIL import Image as PILImage
        img = PILImage.open(io.BytesIO(file.read()))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        dest = folder / f"{stem}.jpg"
        buf  = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        dest.write_bytes(buf.getvalue())
        return jsonify({"ok": True, "photo_url": f"/candidate/{stem}/photo"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/candidate/<stem>/pdf")
@login_required
def serve_pdf(stem):
    folder = PROCESSED_FOLDER / stem
    if not folder.exists():
        abort(404)
    pdf = folder / f"{stem}.pdf"
    if not pdf.exists():
        pdfs = [p for p in folder.glob("*.pdf") if not p.name.startswith("duplicate_")]
        if not pdfs:
            abort(404)
        pdf = pdfs[0]
    return send_file(str(pdf), mimetype="application/pdf")


# ═════════════════════════════════════════════════════════════════════════════
# API — Candidates  (require login)
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/api/candidates")
@login_required
def api_candidates():
    filters = _parse_filters()
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit  = max(1, min(200, int(request.args.get("limit", 50))))
    except ValueError:
        offset, limit = 0, 50

    uid        = current_user.id
    total      = db.count_candidates(filters)
    candidates = db.get_candidates(filters, offset=offset, limit=limit, user_id=uid)

    # Fetch this user's claims in one query and attach per-card
    with db._db() as conn:
        user_claims = db._fetchall(conn,
            "SELECT candidate_id, status FROM claims WHERE user_id=?", (uid,))
    claim_map = {r["candidate_id"]: r["status"] for r in user_claims}

    for c in candidates:
        _enrich(c)
        c["my_claim_status"] = claim_map.get(c["id"])  # None | pending | accepted | rejected

    return jsonify({
        "candidates": candidates,
        "total":      total,
        "offset":     offset,
        "limit":      limit,
        "has_more":   (offset + limit) < total,
        "current_user_id": uid,
    })


@app.route("/api/candidate/<int:cid>")
@login_required
def api_candidate(cid: int):
    c = db.get_candidate(cid)
    if not c:
        abort(404)
    _enrich(c)
    return jsonify(c)


@app.route("/api/filter-options/caste")
@login_required
def api_caste_options():
    """Return distinct caste values for the typeahead datalist."""
    with db._db() as conn:
        rows = db._fetchall(conn,
            "SELECT DISTINCT caste AS val FROM candidates "
            "WHERE caste IS NOT NULL "
            "AND LOWER(TRIM(caste)) NOT IN ('', 'not mentioned', 'none', 'null') "
            "ORDER BY caste"
        )
    return jsonify({"values": [r["val"] for r in rows if r["val"]]})


# ═════════════════════════════════════════════════════════════════════════════
# API — Labels on a candidate  (require login)
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/api/candidate/<int:cid>/views")
@login_required
def api_candidate_views(cid: int):
    """Return the IDs of the current user's views that contain this candidate."""
    uid = current_user.id
    with db._db() as conn:
        rows = db._fetchall(conn,
            "SELECT vc.view_id FROM view_candidates vc "
            "JOIN views v ON v.id = vc.view_id "
            "WHERE vc.candidate_id=? AND v.user_id=?",
            (cid, uid))
    return jsonify({"view_ids": [r["view_id"] for r in rows]})


@app.route("/api/candidate/<int:cid>/labels", methods=["POST"])
@login_required
def api_add_candidate_label(cid: int):
    data     = request.get_json(force=True) or {}
    label_id = data.get("label_id")
    if not label_id:
        return jsonify({"error": "label_id required"}), 400
    # Verify the label belongs to this user
    user_labels = db.get_all_labels(user_id=current_user.id)
    if not any(l["id"] == int(label_id) for l in user_labels):
        return jsonify({"error": "label not found"}), 404
    db.add_candidate_label(cid, int(label_id))
    c = db.get_candidate(cid)
    labels = [l for l in (c["labels"] if c else [])
              if any(ul["id"] == l["id"] for ul in user_labels)]
    return jsonify({"labels": labels})


@app.route("/api/candidate/<int:cid>/labels/<int:label_id>", methods=["DELETE"])
@login_required
def api_remove_candidate_label(cid: int, label_id: int):
    db.remove_candidate_label(cid, label_id)
    uid = current_user.id
    c   = db.get_candidate(cid)
    user_labels = db.get_all_labels(user_id=uid)
    labels = [l for l in (c["labels"] if c else [])
              if any(ul["id"] == l["id"] for ul in user_labels)]
    return jsonify({"labels": labels})


# ═════════════════════════════════════════════════════════════════════════════
# API — Labels CRUD  (require login, per-user)
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/api/labels", methods=["GET"])
@login_required
def api_get_labels():
    return jsonify(db.get_all_labels(user_id=current_user.id))


@app.route("/api/labels", methods=["POST"])
@login_required
def api_create_label():
    data  = request.get_json(force=True) or {}
    name  = (data.get("name") or "").strip()
    color = (data.get("color") or "#6b7280").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        new_id = db.add_label(name, color, user_id=current_user.id)
        return jsonify({"id": new_id, "name": name, "color": color}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/labels/<int:label_id>", methods=["PUT"])
@login_required
def api_update_label(label_id: int):
    data  = request.get_json(force=True) or {}
    name  = (data.get("name") or "").strip()
    color = (data.get("color") or "#6b7280").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        db.update_label(label_id, name, color, user_id=current_user.id)
        return jsonify({"id": label_id, "name": name, "color": color})
    except Exception as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/labels/<int:label_id>", methods=["DELETE"])
@login_required
def api_delete_label(label_id: int):
    db.delete_label(label_id, user_id=current_user.id)
    return jsonify({"ok": True})


# ═════════════════════════════════════════════════════════════════════════════
# API — Saved views  (require login, per-user)
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/api/views", methods=["GET"])
@login_required
def api_get_views():
    return jsonify(db.get_views(user_id=current_user.id))


@app.route("/api/views", methods=["POST"])
@login_required
def api_create_view():
    data          = request.get_json(force=True) or {}
    name          = (data.get("name") or "").strip()
    candidate_ids = data.get("candidate_ids") or []
    filter_desc   = (data.get("filter_desc") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    if not candidate_ids:
        return jsonify({"error": "candidate_ids required"}), 400
    new_id = db.create_view(name, filter_desc, [int(x) for x in candidate_ids],
                            user_id=current_user.id)
    views = db.get_views(user_id=current_user.id)
    view  = next((v for v in views if v["id"] == new_id), {"id": new_id})
    return jsonify(view), 201


@app.route("/api/views/<int:view_id>", methods=["PUT"])
@login_required
def api_rename_view(view_id: int):
    if not db.view_belongs_to_user(view_id, current_user.id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    db.rename_view(view_id, name, user_id=current_user.id)
    return jsonify({"ok": True, "name": name})


@app.route("/api/views/<int:view_id>", methods=["DELETE"])
@login_required
def api_delete_view(view_id: int):
    if not db.view_belongs_to_user(view_id, current_user.id):
        return jsonify({"error": "not found"}), 404
    db.delete_view(view_id, user_id=current_user.id)
    return jsonify({"ok": True})


@app.route("/api/views/<int:view_id>/candidates")
@login_required
def api_view_candidates(view_id: int):
    if not db.view_belongs_to_user(view_id, current_user.id):
        return jsonify({"error": "not found"}), 404
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit  = max(1, min(200, int(request.args.get("limit", 50))))
    except ValueError:
        offset, limit = 0, 50

    total      = db.count_view_candidates(view_id)
    candidates = db.get_view_candidates(view_id, offset=offset, limit=limit,
                                        user_id=current_user.id)
    for c in candidates:
        _enrich(c)

    return jsonify({
        "candidates": candidates,
        "total":      total,
        "offset":     offset,
        "limit":      limit,
        "has_more":   (offset + limit) < total,
    })


@app.route("/api/views/<int:view_id>/candidates", methods=["POST"])
@login_required
def api_add_to_view(view_id: int):
    """Add one or more candidates to an existing view.
    Returns added (newly inserted), already_in (already present), total.
    """
    if not db.view_belongs_to_user(view_id, current_user.id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True) or {}
    ids  = [int(x) for x in data.get("candidate_ids", [])]

    # Check which IDs are already in the view before inserting
    already_in_ids, added_ids = [], []
    for cid in ids:
        was_in = db.candidate_in_view(view_id, cid)
        try:
            db.add_to_view(view_id, cid)
        except Exception:
            pass
        if was_in:
            already_in_ids.append(cid)
        else:
            added_ids.append(cid)

    total = db.count_view_candidates(view_id)
    return jsonify({
        "ok":        True,
        "added":     len(added_ids),
        "already_in": len(already_in_ids),
        "total":     total,
    })


@app.route("/api/views/<int:view_id>/candidates/<int:cid>", methods=["DELETE"])
@login_required
def api_remove_from_view(view_id: int, cid: int):
    if not db.view_belongs_to_user(view_id, current_user.id):
        return jsonify({"error": "not found"}), 404
    db.remove_from_view(view_id, cid)
    total = db.count_view_candidates(view_id)
    return jsonify({"ok": True, "total": total})


# ═════════════════════════════════════════════════════════════════════════════
# Profile  (user creates / edits their own biodata)
# ═════════════════════════════════════════════════════════════════════════════

import re as _re
import hashlib as _hashlib
import uuid as _uuid


def _make_stem(name: str, dob: str, user_id: int) -> str:
    """
    Build a folder stem from name + DOB.
    Format: <birth_year>_<Safe_Name>
    Falls back to user_<id> if both are missing.
    If the stem already exists in the DB (another user's profile),
    appends _u<user_id> to make it unique.
    """
    match = _re.search(r"\d{1,2}/\d{1,2}/(\d{4})", dob or "")
    birth_year = match.group(1) if match else ""

    safe = _re.sub(r"[^\w\s]", "", name or "").strip()
    safe = _re.sub(r"\s+", "_", safe)
    if safe.lower() in ("not_mentioned", ""):
        safe = ""

    parts = [p for p in [birth_year, safe] if p]
    stem  = "_".join(parts) if parts else f"user_{user_id}"

    # Ensure uniqueness — check DB (excluding this user's own row)
    existing = db.get_user_by_id(user_id)   # just to confirm user exists
    base = stem
    counter = 1
    while True:
        row = db.get_candidate_by_stem(stem)
        if row is None:
            break                              # stem is free
        if row.get("created_by_user_id") == user_id:
            break                              # it's already this user's folder
        stem = f"{base}_{counter}"
        counter += 1
    return stem


@app.route("/profile/me")
@login_required
def profile_me():
    c = db.get_candidate_by_user(current_user.id)
    if c:
        _enrich(c)
    return render_template("profile.html",
        candidate=c,
        current_user=current_user,
        mode="view",
    )


@app.route("/profile/me/edit", methods=["GET", "POST"])
@login_required
def profile_edit():
    uid = current_user.id
    existing = db.get_candidate_by_user(uid)
    field_config = db.get_field_config()

    if request.method == "GET":
        if existing:
            _enrich(existing)
        return render_template("profile.html",
            candidate=existing,
            current_user=current_user,
            mode="edit",
            field_config=field_config,
            field_registry=db.FIELD_REGISTRY,
        )

    # ── POST: save the profile ───────────────────────────────────────────────
    f = request.form

    # ── Delete own profile ───────────────────────────────────────────────────
    if request.form.get("action") == "delete":
        if existing:
            stem = db.delete_candidate(existing["id"])
            if stem:
                import shutil as _shutil
                folder = PROCESSED_FOLDER / stem
                if folder.exists():
                    trash = PROCESSED_FOLDER.parent / "trash"
                    trash.mkdir(exist_ok=True)
                    _shutil.move(str(folder), str(trash / stem))
        return redirect(url_for("index"))

    def fv(k: str) -> Optional[str]:
        v = f.get(k, "").strip()
        return v if v else None

    fields = {
        "name":              fv("name"),
        "date_of_birth":     fv("date_of_birth"),
        "gender":            fv("gender"),
        "height":            fv("height"),
        "weight":            fv("weight"),
        "blood_group":       fv("blood_group"),
        "marital_status":    fv("marital_status"),
        "mother_tongue":     fv("mother_tongue"),
        "caste":             fv("caste"),
        "sub_caste":         fv("sub_caste"),
        "gotra":             fv("gotra"),
        "manglik":           fv("manglik"),
        "education":         fv("education"),
        "occupation":        fv("occupation"),
        "job_location":      fv("job_location"),
        "salary":            fv("salary"),
        "residential_city":  fv("residential_city"),
        "state":             fv("state"),
        "fathers_name":      fv("fathers_name"),
        "fathers_contact":   fv("fathers_contact"),
        "fathers_occupation":fv("fathers_occupation"),
        "mothers_name":      fv("mothers_name"),
        "mothers_contact":   fv("mothers_contact"),
        "mothers_occupation":fv("mothers_occupation"),
        "siblings":          fv("siblings"),
        "candidate_contact": fv("candidate_contact"),
        "candidate_email":   fv("candidate_email"),
        "hobbies":           fv("hobbies"),
        "about_me":          fv("about_me"),
    }

    # ── Mandatory field validation (reads live config) ────────────────────────
    field_config   = db.get_field_config()
    field_labels   = {fi["key"]: fi["label"] for fi in db.FIELD_REGISTRY}
    mandatory_keys = [k for k, s in field_config.items() if s == "mandatory"]
    errors = []

    for key in mandatory_keys:
        if not fields.get(key):
            errors.append(f"{field_labels.get(key, key)} is required.")

    # Cross-field: at-least-one-of rules
    for rule in db.FIELD_AT_LEAST_ONE:
        # Only enforce at-least-one when at least one key in the group is mandatory.
        # If all keys are optional (or hidden), the group is optional too.
        mandatory_in_group = [k for k in rule["keys"] if field_config.get(k) == "mandatory"]
        if mandatory_in_group:
            has_any = any(fields.get(k) for k in rule["keys"])
            if not has_any:
                errors.append(f"Please provide {rule['label']}.")

    error = " ".join(errors) if errors else None

    if error:
        if existing:
            _enrich(existing)
        return render_template("profile.html",
            candidate=existing,
            current_user=current_user,
            mode="edit",
            error=error,
            form_data=fields,
            field_config=field_config,
            field_registry=db.FIELD_REGISTRY,
        )

    if existing:
        # Update existing profile
        fields["folder_stem"] = existing["folder_stem"]   # don't rename on edit
        db.update_candidate(existing["id"], fields)
        cid  = existing["id"]
        stem = existing["folder_stem"]
    else:
        # Create new profile
        stem   = _make_stem(fields["name"], fields["date_of_birth"], uid)
        fhash  = f"user_{uid}_{_uuid.uuid4().hex[:8]}"    # synthetic hash
        fields["source_file"] = f"{stem}.pdf"
        # Create folder
        folder = PROCESSED_FOLDER / stem
        folder.mkdir(parents=True, exist_ok=True)
        cid = db.insert_candidate(fields, fhash, stem, created_by_user_id=uid)

    # ── Handle photo upload ──────────────────────────────────────────────────
    # Remove photo if requested (only when no new photo is being uploaded)
    if request.form.get("remove_photo") == "1" and not (request.files.get("photo") and request.files.get("photo").filename):
        import os as _os
        jpg = PROCESSED_FOLDER / stem / f"{stem}.jpg"
        if jpg.exists():
            _os.remove(jpg)

    photo_file = request.files.get("photo")
    if photo_file and photo_file.filename:
        allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
        suffix  = Path(photo_file.filename).suffix.lower()
        if suffix in allowed:
            try:
                from PIL import Image as PILImage
                img = PILImage.open(io.BytesIO(photo_file.read()))
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")
                folder = PROCESSED_FOLDER / stem
                folder.mkdir(parents=True, exist_ok=True)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=95)
                (folder / f"{stem}.jpg").write_bytes(buf.getvalue())
            except Exception:
                pass   # photo failure is non-fatal

    # ── Handle PDF upload ────────────────────────────────────────────────────
    pdf_file = request.files.get("pdf")
    if pdf_file and pdf_file.filename:
        if pdf_file.filename.lower().endswith(".pdf"):
            try:
                folder = PROCESSED_FOLDER / stem
                folder.mkdir(parents=True, exist_ok=True)
                pdf_bytes = pdf_file.read()
                (folder / f"{stem}.pdf").write_bytes(pdf_bytes)
            except Exception:
                pass   # pdf failure is non-fatal

    return redirect(url_for("profile_me"))


# ═════════════════════════════════════════════════════════════════════════════
# Admin panel
# ═════════════════════════════════════════════════════════════════════════════

from functools import wraps

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


@app.route("/admin")
@login_required
@admin_required
def admin_users():
    users         = db.list_users()
    pending_count = db.count_pending_claims()
    return render_template("admin_users.html",
        users=users,
        pending_count=pending_count,
        current_user=current_user,
    )


@app.route("/admin/user/<int:uid>", methods=["GET", "POST"])
@login_required
@admin_required
def admin_user_detail(uid: int):
    row = db.get_user_by_id(uid)
    if not row:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "toggle_active":
            if uid != current_user.id:   # can't deactivate yourself
                new_active = 0 if row["active"] else 1
                db.update_user(uid, active=new_active)
        elif action == "toggle_role":
            new_role = "admin" if row["role"] == "user" else "user"
            # Prevent self-demotion
            if uid != current_user.id:
                db.update_user(uid, role=new_role)
        elif action == "reset_password":
            new_pw  = request.form.get("new_password", "").strip()
            conf_pw = request.form.get("confirm_password", "").strip()
            pw_message = None
            pw_error   = None
            if len(new_pw) < 8:
                pw_error = "Password must be at least 8 characters."
            elif new_pw != conf_pw:
                pw_error = "Passwords do not match."
            else:
                pw_hash = generate_password_hash(new_pw, method="pbkdf2:sha256")
                db.update_user_password(uid, pw_hash)
                pw_message = f"Password for \"{row['username']}\" has been updated."
            candidate = db.get_candidate_by_user(uid)
            if candidate:
                _enrich(candidate)
            row = db.get_user_by_id(uid)
            return render_template("admin_user_detail.html",
                u=row,
                candidate=candidate,
                current_user=current_user,
                pw_message=pw_message,
                pw_error=pw_error,
            )
        elif action == "delete_user":
            if uid != current_user.id:  # can't delete yourself
                # Delete candidate profile if exists
                c = db.get_candidate_by_user(uid)
                if c:
                    stem = db.delete_candidate(c["id"])
                    if stem:
                        import shutil as _shutil
                        folder = PROCESSED_FOLDER / stem
                        if folder.exists():
                            trash = PROCESSED_FOLDER.parent / "trash"
                            trash.mkdir(exist_ok=True)
                            _shutil.move(str(folder), str(trash / stem))
                # Delete user (cascades labels + views)
                with db._db() as conn:
                    db._execute(conn, "DELETE FROM users WHERE id=?", (uid,))
                return redirect(url_for("admin_users"))
        return redirect(url_for("admin_user_detail", uid=uid))

    candidate = db.get_candidate_by_user(uid)
    if candidate:
        _enrich(candidate)
    return render_template("admin_user_detail.html",
        u=row,
        candidate=candidate,
        current_user=current_user,
        pw_message=None,
        pw_error=None,
    )


@app.route("/admin/candidates")
@login_required
@admin_required
def admin_candidates():
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit  = 50
    except ValueError:
        offset = 0
        limit  = 50

    search  = request.args.get("search", "").strip() or None
    filters = {"search": search} if search else {}
    total      = db.count_candidates(filters)
    candidates = db.get_candidates(filters, offset=offset, limit=limit)
    for c in candidates:
        _enrich(c)

    return render_template("admin_candidates.html",
        candidates=candidates,
        total=total,
        offset=offset,
        limit=limit,
        search=search or "",
        current_user=current_user,
    )


@app.route("/admin/candidate/<int:cid>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def admin_candidate_edit(cid: int):
    c = db.get_candidate(cid)
    if not c:
        abort(404)
    _enrich(c)

    error = None
    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "delete":
            stem = db.delete_candidate(cid)
            if stem:
                import shutil as _shutil
                folder = PROCESSED_FOLDER / stem
                if folder.exists():
                    trash = PROCESSED_FOLDER.parent / "trash"
                    trash.mkdir(exist_ok=True)
                    _shutil.move(str(folder), str(trash / stem))
            return redirect(url_for("admin_candidates"))

        f = request.form
        def fv(k: str) -> Optional[str]:
            v = f.get(k, "").strip()
            return v if v else None

        fields = {
            "name":              fv("name"),
            "date_of_birth":     fv("date_of_birth"),
            "gender":            fv("gender"),
            "height":            fv("height"),
            "weight":            fv("weight"),
            "blood_group":       fv("blood_group"),
            "marital_status":    fv("marital_status"),
            "mother_tongue":     fv("mother_tongue"),
            "caste":             fv("caste"),
            "sub_caste":         fv("sub_caste"),
            "gotra":             fv("gotra"),
            "manglik":           fv("manglik"),
            "education":         fv("education"),
            "occupation":        fv("occupation"),
            "job_location":      fv("job_location"),
            "salary":            fv("salary"),
            "residential_city":  fv("residential_city"),
            "state":             fv("state"),
            "candidate_contact": fv("candidate_contact"),
            "candidate_email":   fv("candidate_email"),
            "fathers_name":      fv("fathers_name"),
            "fathers_contact":   fv("fathers_contact"),
            "fathers_occupation":fv("fathers_occupation"),
            "mothers_name":      fv("mothers_name"),
            "mothers_contact":   fv("mothers_contact"),
            "mothers_occupation":fv("mothers_occupation"),
            "siblings":          fv("siblings"),
            "hobbies":           fv("hobbies"),
            "about_me":          fv("about_me"),
            "folder_stem":       c["folder_stem"],   # never change stem from admin edit
        }

        if not fields["name"]:
            error = "Name is required."
        else:
            db.update_candidate(cid, fields)

            # Remove photo if requested
            if request.form.get("remove_photo") == "1":
                import os as _os
                jpg = PROCESSED_FOLDER / c["folder_stem"] / f"{c['folder_stem']}.jpg"
                if jpg.exists():
                    _os.remove(jpg)

            # Photo upload
            photo_file = request.files.get("photo")
            if photo_file and photo_file.filename:
                allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
                if Path(photo_file.filename).suffix.lower() in allowed:
                    try:
                        from PIL import Image as PILImage
                        img = PILImage.open(io.BytesIO(photo_file.read()))
                        if img.mode in ("RGBA", "P", "LA"):
                            img = img.convert("RGB")
                        stem   = c["folder_stem"]
                        folder = PROCESSED_FOLDER / stem
                        folder.mkdir(parents=True, exist_ok=True)
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=95)
                        (folder / f"{stem}.jpg").write_bytes(buf.getvalue())
                    except Exception:
                        pass

            # PDF upload
            pdf_file = request.files.get("pdf")
            if pdf_file and pdf_file.filename and pdf_file.filename.lower().endswith(".pdf"):
                try:
                    stem   = c["folder_stem"]
                    folder = PROCESSED_FOLDER / stem
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / f"{stem}.pdf").write_bytes(pdf_file.read())
                except Exception:
                    pass

            if not error:
                return redirect(url_for("admin_candidates"))

    return render_template("admin_candidate_edit.html",
        c=c,
        error=error,
        current_user=current_user,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Claims — user submits, admin reviews
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/api/candidate/<int:cid>/claim", methods=["POST"])
@login_required
def api_claim(cid: int):
    """User submits a claim on an admin-managed profile."""
    # Admins manage claims — they don't submit them
    if current_user.is_admin:
        return jsonify({"error": "Admins cannot claim profiles"}), 403
    candidate = db.get_candidate(cid)
    if not candidate:
        return jsonify({"error": "Not found"}), 404
    # Only allow claiming admin-managed profiles (created_by_user_id is NULL)
    if candidate.get("created_by_user_id") is not None:
        return jsonify({"error": "This profile is already owned by a user"}), 400
    result = db.create_claim(cid, current_user.id)
    if result is None:
        existing = db.get_claim_for_user(cid, current_user.id)
        status = existing["status"] if existing else "unknown"
        return jsonify({"error": "already_claimed", "status": status}), 409
    return jsonify({"ok": True, "claim_id": result}), 201


@app.route("/admin/claims")
@login_required
@admin_required
def admin_claims():
    tab    = request.args.get("tab", "pending")
    claims = db.list_claims(status=tab if tab != "all" else None)
    pending_count = db.count_pending_claims()
    for cl in claims:
        cl["photo_url"] = _photo_url(cl.get("folder_stem"))
    return render_template("admin_claims.html",
        claims=claims,
        tab=tab,
        pending_count=pending_count,
        current_user=current_user,
    )


@app.route("/admin/claims/<int:claim_id>", methods=["POST"])
@login_required
@admin_required
def admin_review_claim(claim_id: int):
    action     = request.form.get("action", "")
    admin_note = request.form.get("admin_note", "").strip()
    if action == "accept":
        db.review_claim(claim_id, "accepted", admin_note)
    elif action == "reject":
        db.review_claim(claim_id, "rejected", admin_note)
    return redirect(url_for("admin_claims", tab=request.form.get("tab", "pending")))


# ═════════════════════════════════════════════════════════════════════════════
# Profile merge
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/admin/merge", methods=["POST"])
@login_required
@admin_required
def admin_merge_preview():
    """Receive selected IDs, show the field-picker merge UI."""
    ids_raw = request.form.getlist("ids")
    ids = []
    for v in ids_raw:
        try:
            ids.append(int(v))
        except ValueError:
            pass

    if len(ids) < 2:
        return redirect(url_for("admin_candidates"))

    # Deduplicate while preserving order
    seen = set()
    unique_ids = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            unique_ids.append(i)
    ids = unique_ids

    candidates = []
    for cid in ids:
        c = db.get_candidate(cid)
        if c:
            _enrich(c)
            candidates.append(c)

    if len(candidates) < 2:
        return redirect(url_for("admin_candidates"))

    return render_template("admin_merge.html",
        candidates=candidates,
        registry=db.FIELD_REGISTRY,
        current_user=current_user,
        error=None,
    )


@app.route("/admin/merge/confirm", methods=["POST"])
@login_required
@admin_required
def admin_merge_confirm():
    """Execute the merge: build the merged fields dict, update survivor, delete others."""
    import shutil as _shutil

    ids_raw = request.form.getlist("ids")
    ids = []
    for v in ids_raw:
        try:
            ids.append(int(v))
        except ValueError:
            pass

    if len(ids) < 2:
        return redirect(url_for("admin_candidates"))

    try:
        survivor_id = int(request.form.get("survivor_id", ids[0]))
    except (ValueError, TypeError):
        survivor_id = ids[0]

    if survivor_id not in ids:
        survivor_id = ids[0]

    # Load all candidates
    candidates = {}
    for cid in ids:
        c = db.get_candidate(cid)
        if c:
            _enrich(c)
            candidates[cid] = c

    if len(candidates) < 2 or survivor_id not in candidates:
        return redirect(url_for("admin_candidates"))

    survivor = candidates[survivor_id]

    # Build the merged fields dict — for each field, use the value from whichever
    # candidate the admin selected (form name: field_<key>, value: candidate_id)
    merged = {}
    for field in db.FIELD_REGISTRY:
        key = field["key"]
        try:
            chosen_id = int(request.form.get(f"field_{key}", survivor_id))
        except (ValueError, TypeError):
            chosen_id = survivor_id
        if chosen_id not in candidates:
            chosen_id = survivor_id
        merged[key] = candidates[chosen_id].get(key)

    # Keep survivor's folder_stem always
    merged["folder_stem"] = survivor["folder_stem"]

    # ── Photo: copy chosen candidate's photo into survivor folder ──
    try:
        photo_from_id = int(request.form.get("photo_from", survivor_id))
    except (ValueError, TypeError):
        photo_from_id = survivor_id
    if photo_from_id not in candidates:
        photo_from_id = survivor_id

    surv_folder = PROCESSED_FOLDER / survivor["folder_stem"]
    surv_folder.mkdir(parents=True, exist_ok=True)

    if photo_from_id != survivor_id:
        src_photo = PROCESSED_FOLDER / candidates[photo_from_id]["folder_stem"] \
                    / f"{candidates[photo_from_id]['folder_stem']}.jpg"
        dst_photo = surv_folder / f"{survivor['folder_stem']}.jpg"
        if src_photo.exists():
            import shutil as _sh
            _sh.copy2(str(src_photo), str(dst_photo))
        else:
            # chosen profile had no photo — remove survivor's photo too if it exists
            if dst_photo.exists():
                dst_photo.unlink()

    # ── PDFs: copy all non-survivor PDFs into survivor folder ──
    for cid, c in candidates.items():
        if cid == survivor_id:
            continue
        src_folder = PROCESSED_FOLDER / c["folder_stem"]
        if not src_folder.exists():
            continue
        for pdf in src_folder.glob("*.pdf"):
            if pdf.name.startswith("duplicate_"):
                continue
            dst = surv_folder / pdf.name
            # Avoid overwriting — rename if collision
            counter = 1
            while dst.exists():
                dst = surv_folder / f"merged_{counter}_{pdf.name}"
                counter += 1
            try:
                _shutil.copy2(str(pdf), str(dst))
            except Exception:
                pass

    # ── Update survivor in DB ──
    db.update_candidate(survivor_id, merged)

    # ── Delete non-survivors: hard delete from DB, move folder to trash ──
    trash = PROCESSED_FOLDER.parent / "trash"
    trash.mkdir(exist_ok=True)

    for cid in ids:
        if cid == survivor_id:
            continue
        c = candidates.get(cid)
        if not c:
            continue
        db.delete_candidate(cid)
        src_folder = PROCESSED_FOLDER / c["folder_stem"]
        if src_folder.exists():
            dst_trash = trash / c["folder_stem"]
            # Avoid collision in trash
            counter = 1
            while dst_trash.exists():
                dst_trash = trash / f"{c['folder_stem']}_{counter}"
                counter += 1
            try:
                _shutil.move(str(src_folder), str(dst_trash))
            except Exception:
                pass

    return redirect(url_for("admin_candidate_edit", cid=survivor_id))


# ═════════════════════════════════════════════════════════════════════════════
# Field config + Completeness dashboard
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/admin/fields", methods=["GET", "POST"])
@login_required
@admin_required
def admin_fields():
    saved   = False
    changes = []          # list of (label, old_state, new_state)
    if request.method == "POST":
        old_config = db.get_field_config()
        field_labels = {f["key"]: f["label"] for f in db.FIELD_REGISTRY}
        for field in db.FIELD_REGISTRY:
            key   = field["key"]
            if key not in request.form:
                continue
            state = request.form[key]
            if state in ("mandatory", "optional", "hidden"):
                old = old_config.get(key, field["default"])
                if old != state:
                    changes.append((field_labels[key], old, state))
                db.set_field_state(key, state)
        saved = True
    config = db.get_field_config()
    return render_template("admin_fields.html",
        registry=db.FIELD_REGISTRY,
        config=config,
        saved=saved,
        changes=changes,
        current_user=current_user,
    )


@app.route("/admin/completeness")
@login_required
@admin_required
def admin_completeness():
    mandatory_keys = db.get_mandatory_fields()
    field_labels   = {f["key"]: f["label"] for f in db.FIELD_REGISTRY}
    rule_labels    = [r["label"] for r in db.FIELD_AT_LEAST_ONE]
    field_config   = db.get_field_config()

    # All non-hidden fields are auditable (for chip filters)
    auditable_fields = [
        f for f in db.FIELD_REGISTRY
        if field_config.get(f["key"]) != "hidden"
    ]

    # Fetch all candidates (no limit — this is an audit page)
    with db._db() as conn:
        all_candidates = db._fetchall(conn,
            "SELECT * FROM candidates ORDER BY name")

    _EMPTY = {"", "none", "null", "not mentioned"}

    def _missing_val(c, key):
        v = c.get(key)
        return not v or str(v).strip().lower() in _EMPTY

    rows = []
    for c in all_candidates:
        missing_mandatory = db.audit_candidate(c, mandatory_keys)
        # Build full missing-field set across ALL non-hidden fields
        missing_all = set()
        for f in auditable_fields:
            if _missing_val(c, f["key"]):
                missing_all.add(f["key"])
        rows.append({
            "id":              c["id"],
            "name":            c["name"],
            "missing":         set(missing_mandatory),          # for ✓/✗ columns
            "missing_all":     missing_all,                     # for chip filtering (data attr)
            "missing_count":   len(missing_mandatory),
        })

    # Per-field missing counts (for chip badges) — count across ALL candidates
    field_missing_counts = {}
    for f in auditable_fields:
        key = f["key"]
        field_missing_counts[key] = sum(1 for r in rows if key in r["missing_all"])

    complete_count   = sum(1 for r in rows if r["missing_count"] == 0)
    incomplete_count = len(rows) - complete_count
    filter_param     = request.args.get("filter", "all")

    return render_template("admin_completeness.html",
        rows=rows,
        mandatory_fields=mandatory_keys,
        rule_labels=rule_labels,
        field_labels=field_labels,
        complete_count=complete_count,
        incomplete_count=incomplete_count,
        total_count=len(rows),
        filter=filter_param,
        auditable_fields=auditable_fields,
        field_missing_counts=field_missing_counts,
        current_user=current_user,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "─" * 50)
    print("  Biodata Viewer")
    print("  http://localhost:8080")
    print("  Press Ctrl+C to stop")
    print("─" * 50 + "\n")
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
