# Biodata Viewer — Feature Documentation

**Version:** Phase 3 (multi-user · PostgreSQL · AI extraction · admin tooling)  
**Stack:** Python 3.9 · Flask · Flask-Login · SQLite (dev) / PostgreSQL (prod) · Jinja2 · Vanilla JS  
**Default port:** `http://localhost:8080`

---

## Table of Contents

1. [Authentication](#1-authentication)
2. [Candidate Browsing](#2-candidate-browsing)
3. [Filters & Search](#3-filters--search)
4. [Detail Panel](#4-detail-panel)
5. [Labels](#5-labels)
6. [Saved Views](#6-saved-views)
7. [My Profile](#7-my-profile)
8. [Profile Claims](#8-profile-claims)
9. [Admin — User Management](#9-admin--user-management)
10. [Admin — Candidate Management](#10-admin--candidate-management)
11. [Admin — Merge Profiles](#11-admin--merge-profiles)
12. [Admin — Field Configuration](#12-admin--field-configuration)
13. [Admin — Completeness Dashboard](#13-admin--completeness-dashboard)
14. [Admin — Claims Review](#14-admin--claims-review)
15. [PDF & Photo Pipeline](#15-pdf--photo-pipeline)
16. [All Fields Reference](#16-all-fields-reference)
17. [Known Limitations](#17-known-limitations)

---

## 1. Authentication

### Registration (`/register`)
- Open signup — anyone with the URL can create an account
- Fields: username (min 3 chars), password (min 8 chars), confirm password
- Username uniqueness is checked case-insensitively
- New accounts default to `role = user`
- Auto logged-in with a persistent cookie after registration

### Login (`/login`)
- Case-insensitive username lookup
- Deactivated accounts are rejected with a clear message
- **"Stay signed in for 30 days"** checkbox — sets a long-lived persistent cookie
- Redirects to the originally requested page after login

### Logout (`/logout`)
- Clears session, redirects to `/login`

### Access Control
- Every page and API endpoint requires login; unauthenticated requests redirect to `/login?next=<url>`
- Admin-only routes return **403** if the logged-in user is not an admin
- A `User` object wraps every DB row with `.is_admin` and `.is_active` properties

---

## 2. Candidate Browsing

### Main Grid (`/`)
- Card grid showing all candidates, newest first
- **Infinite scroll** — loads 50 cards at a time; next batch fetches automatically as you reach the bottom (via `IntersectionObserver`)
- Each card shows:
  - Portrait photo (or grey avatar placeholder)
  - Full name, gender chip, birth year · age · height
  - City / location metadata line
  - The current user's own label chips
  - **📄 PDF** button (if a PDF exists on disk)
  - **"This is me"** claim button (admin-managed profiles only, non-admin users only)
- **Total count** badge in the header updates as filters change
- **Profile banner** — if the logged-in user has no profile yet, a dismissible blue banner offers "Create my profile"
- **Stale-response protection** — a version counter discards late-arriving API responses when the user changes filters quickly

### View Page (`/view/<id>`)
- Same card grid scoped to one saved view's candidates
- Sorted alphabetically by name
- No filter bar
- **✕ Remove** button on each card removes it from the view without deleting the candidate
- Inline view rename in the page heading
- **Delete view** button removes the entire view

---

## 3. Filters & Search

All filters are additive (AND logic). Applied immediately; search has a 280 ms debounce.

| Filter | Type | Behaviour |
|--------|------|-----------|
| **Search by name** | Text input | Substring match — `ILIKE` on Postgres, `LIKE` on SQLite |
| **Height min / max** | Numeric (inches) | Filters on the stored `height_inches` computed column |
| **Birth year from / to** | Numeric (year) | Filters on the stored `birth_year` computed column |
| **Weight max** | Numeric (kg) | Filters on the stored `weight_kg` computed column |
| **Gender** | Dropdown | Exact match, case-insensitive |
| **Caste** | Text input + datalist | Partial substring match; autocomplete suggestions from DB |
| **Family city** | Text input + datalist | Partial match on `residential_city` |
| **Current location** | Text input + datalist | Partial match on `job_location` |
| **Labels** | Multi-checkbox dropdown | AND logic — candidate must carry every selected label |

**Height guard:** if min > max, both values are silently dropped on the server so results are never unexpectedly empty.

**Reset:** "Reset" button clears all active filters and reloads from the start.

---

## 4. Detail Panel

Clicking any card opens a slide-up bottom sheet showing the full profile.

### Content Sections

| Section | Fields |
|---------|--------|
| **Personal** | DOB, Age, Height, Weight, Gender, Marital Status, Blood Group, Mother Tongue |
| **Location** | Family city, State, Current location |
| **Caste & Religion** | Caste, Sub-caste, Gotra, Manglik |
| **Education & Career** | Education, Occupation, Annual Salary |
| **Family** | Father's Name + Contact + Occupation; Mother's Name + Contact + Occupation; Siblings; Candidate Phone; Candidate Email |
| **Hobbies** | Shown only when filled |
| **About** | Shown only when filled |

Empty fields are hidden from the panel — only filled values are displayed.

### Action Bar (top of panel)
- **✎ Edit** — links to the admin edit page (admin only)
- **📷 Photo** — opens file picker to upload / replace the portrait (admin, or profile owner)
- **📄 PDF** — opens the biodata PDF in a new tab (shown only when a PDF exists)
- **+ View** — add this candidate to an existing saved view (opens a per-view membership popup)
- **"🏷 This is me"** — claim button for unowned profiles (non-admin users only)

### Label Management in Panel
- Same "+ Label" dropdown as on the card (see [Labels](#5-labels))
- Label chips shown inline; click ✕ on a chip to remove it

### Behaviour
- Close with the **✕** button, the **Escape** key, or by tapping outside the panel
- Photo upload is instant — the card photo updates in memory without a page reload

---

## 5. Labels

Labels are **private per user** — each user creates, sees, and manages only their own labels.

### Creating Labels
- Click **"+ Label"** on any card or in the detail panel to open the dropdown
- At the bottom of the dropdown: **"＋ New label"** — click to expand an inline form
- Fill in a name (up to 40 chars) and pick a colour from 10 preset swatches
- Press **Create** (or Enter) — the label is saved, appears in the list immediately
- You can then click it to apply it to the candidate

### Applying Labels
- Open the dropdown, click any label name
- Already-applied labels are greyed out
- Changes are instant — no page reload

### Removing Labels from a Candidate
- Click **✕** on any label chip on a card or in the detail panel

### Deleting Labels
- Hover over any label name in the dropdown — a faint **✕** appears on the right
- Clicking it opens a confirmation popup: *"Are you sure you want to delete [name]? It will be removed from all candidates."*
- Confirm deletes the label everywhere (cascades via the DB) and removes its chips from all visible cards instantly

> **Note:** There is no separate Settings page for labels — all label management happens directly inside the dropdown.

---

## 6. Saved Views

A saved view is a named, static list of candidates.

### Creating a View
1. On the main grid, tick the checkbox on any cards you want to include
2. A sticky **"N selected"** bar appears at the bottom
3. Click **"💾 Save as View"**
4. An auto-generated name is shown (based on active filters, e.g. *"Female 5′4″–5′6″, 1998–2002 · Oct 2026"*) — edit it if you like
5. Click **Save**

### Adding Candidates to an Existing View
- From the main grid or a view page, open any card's detail panel
- Click **"+ View"** in the panel action bar
- A popup lists all your views with a ✓ / + indicator per view
- Click any view to add the candidate — membership updates immediately

### Managing Views
- All views appear in the **📁 Views** dropdown in the header with a candidate count badge
- **Rename**: click the pencil icon → inline text input, confirm on Enter or blur
- **Delete**: click ✕ in the dropdown → confirmation required

### View Page (`/view/<id>`)
- Alphabetical list of all candidates in the view
- Remove individual candidates with **"✕ Remove"**
- Rename or delete the view from the page heading
- Same detail panel and label management as the main grid

---

## 7. My Profile

### Creating a Profile (`/profile/me/edit`)
- Any logged-in user can create one biodata profile for themselves
- The form shows only fields that are not set to **Hidden** in the admin's field configuration
- Fields marked **Mandatory** show a red ✱ and block saving if empty
- The folder stem is auto-generated as `<birth_year>_<Full_Name>` (e.g. `1998_Priya_Shah`); name collisions get a numeric suffix

### Editing a Profile
- Navigate to **"👤 My Profile"** → **"✎ Edit"**, or go directly to `/profile/me/edit`
- All saved values are pre-populated
- The folder stem is **never renamed** on edit — it stays fixed forever

### Photo Upload
- Accepts JPG, JPEG, PNG, WEBP, BMP, TIFF
- PIL converts all formats to JPEG at quality 95
- **Remove photo**: tick the checkbox on the edit form to delete the current photo on save

### PDF Upload
- Upload your biodata PDF from the edit form
- Visible to all logged-in users via the PDF button and detail panel
- Re-uploading replaces the existing file

### Deleting a Profile
- **"🗑 Delete my profile"** button on the edit form, with a two-step confirmation
- Moves the entire `processed/<stem>/` folder to `trash/`
- Removes the DB row (cascades label associations)

### View Mode (`/profile/me`)
- Read-only view of your own profile across all field sections
- **"✎ Edit"** button in the header

---

## 8. Profile Claims

Allows a regular user to link their account identity to an admin-uploaded profile.

### Claiming (User Side)
- The **"🏷 This is me"** button appears on any card that was uploaded by the admin (no `created_by_user_id`)
- Clicking submits a claim — the button immediately changes to **"⏳ Claim pending"**
- Status badges shown on the card: **Pending** (amber) · **Accepted** (green) · **Rejected** (red)
- A rejected claim can be resubmitted — the existing row is reset to `pending`
- Admin users never see the claim button

### Reviewing Claims (Admin — `/admin/claims`)
- Tabbed view: **Pending** · **Accepted** · **Rejected** · **All**
- Pending count shown as a red badge on the Claims link in the admin nav (visible on all admin pages and the main browse page)
- Each claim card shows: candidate photo, name, DOB, claimant username (links to user page), submission date, status
- Admin can add an optional note before acting
- **Accept** — sets `candidates.created_by_user_id = user_id`; the profile is now owned by that user, the claim button disappears everywhere
- **Reject** — marks as rejected; the profile stays admin-managed

---

## 9. Admin — User Management

### User List (`/admin`)
All users listed with: username, role badge (Admin / User), active/deactivated status, number of linked profiles, join date.

Top navigation: **Claims** (with pending badge) · **Completeness** · **⚙ Fields** · **Candidates →**

### User Detail (`/admin/user/<id>`)

| Action | Restriction |
|--------|-------------|
| Toggle active / deactivate | Cannot deactivate yourself |
| Promote to Admin / Demote to User | Cannot demote yourself |
| Reset password (admin sets directly, no current password needed) | New password min 8 chars |
| Delete user | Cannot delete yourself; cascades labels, views, claims; moves profile folder to `trash/` |

**Linked profile:** if the user has a self-created profile, it's shown with a View / Edit link.

---

## 10. Admin — Candidate Management

### Candidates Table (`/admin/candidates`)
- Infinite scroll table — 50 rows per load
- Columns: checkbox, photo thumbnail, name, DOB / age, height · weight, city, owner (user # or "admin"), Edit link
- **Search** by name (Enter or Search button)
- **Select all loaded** checkbox in the header row

### Merge Selection Bar
- Appears when ≥1 checkbox is ticked
- **Merge selected** button active only when ≥2 profiles are ticked — opens the merge flow
- **Clear** resets selection
- Selection persists across infinite-scroll page loads

### Candidate Edit (`/admin/candidate/<id>/edit`)
- Shows **all 29 fields** regardless of field configuration (field config only gates the user profile form)
- Current photo with Remove checkbox, folder stem (read-only), owner info, PDF link
- Photo upload and PDF upload both available
- Only `name` is required; everything else is optional for admin
- **Delete** (two-step confirm) moves folder to `trash/` and removes the DB row

---

## 11. Admin — Merge Profiles

Combine two or more duplicate or related profiles into one clean record.

### Step 1 — Select
On the candidates table, tick ≥2 checkboxes, then click **"⚡ Merge selected"** in the selection bar.

### Step 2 — Configure (`/admin/merge`)
A field-picker table opens with one column per selected profile and one row per field.

- **Survivor picker** — choose which profile's ID and folder stem to keep; all others are permanently deleted after the merge
- **Quick-fill buttons** — "All from: [Name]" sets every radio to that candidate in one click
- **Photo row** — thumbnails side-by-side; pick which photo the merged profile will use
- **Per-field radios** — for each of the 29 fields, current values from all candidates are shown; empty values shown as *"— empty —"*; first candidate is pre-selected as default
- Fields are grouped: Personal · Caste & Religion · Education & Career · Location · Family · About

### Step 3 — Confirm (`/admin/merge/confirm`)
Sticky save bar shows ✓ Confirm Merge · Cancel · ⚠ "Non-survivor profiles will be permanently deleted"

**What happens on confirm:**
1. Survivor's DB row is updated with the chosen field values
2. The selected photo is copied into the survivor's folder (replacing if needed)
3. All PDFs from every non-survivor are copied into the survivor's folder (collision-renamed `merged_1_<name>.pdf`, `merged_2_…`)
4. Non-survivor profiles are hard-deleted from the database
5. Non-survivor folders are moved to `trash/`
6. Redirected to the survivor's edit page to review the result

---

## 12. Admin — Field Configuration

`/admin/fields` — controls which fields appear on the **user-facing profile form** and whether they are required.

### Three States Per Field

| State | Effect |
|-------|--------|
| **Mandatory** | Shown on the profile form with a red ✱. User cannot save without filling it. |
| **Optional** | Shown on the profile form. Can be left blank. |
| **Hidden** | Not shown on the profile form at all. Admin edit always shows it. |

### How to Use
- All 29 fields listed, grouped by section
- Click any state button to change it — highlighted immediately
- Click **"✓ Save all settings"** — changes take effect immediately for all future profile saves

### Admin Bypass
The admin candidate edit page always shows all 29 fields regardless of this configuration.

### Cross-Field Rules
Two group rules are always enforced when at least one key in the group is mandatory:
- **At least one parent name** — father's name or mother's name must be provided
- **At least one parent contact** — father's contact or mother's contact must be provided

These rules are **never enforced** for existing admin-uploaded profiles — only when a user saves their own profile form.

---

## 13. Admin — Completeness Dashboard

`/admin/completeness` — audit view showing which mandatory fields are missing for each candidate.

### The Table
- One row per candidate (all profiles, no pagination limit)
- One column per currently-mandatory field
- Each cell: ✓ green (filled) or ✗ red (missing)
- Candidate name is a link directly to their edit page

### Missing Count Badge Per Row
- **✓ Complete** (green) — all mandatory fields filled
- **N missing** (amber/red) — count of missing mandatory fields

### Field Chips (above the table)
- One chip per non-hidden field, each showing how many candidates are missing that field across the entire DB
- Click chips to filter the table — OR logic (show rows missing any of the selected fields)
- Chip visibility saved to `localStorage` per browser

### Controls
- **All / Incomplete / Complete** tab filter
- **Search by name** — live client-side filtering
- **Sort by missing count** — click the "Missing ↕" column header

### Stats Bar
**N complete / N incomplete / N total** shown across the top.

---

## 14. Admin — Claims Review

See [Section 8 — Profile Claims](#8-profile-claims) for the full description. Admin-side URL is `/admin/claims`.

---

## 15. PDF & Photo Pipeline

### Inbox Processing

Two modes for ingesting new biodata files:

**`run_once.py`** — batch mode, processes everything currently in `inbox/`:
1. Groups identical files by MD5 hash; keeps only one per group
2. Runs `prepare_file()` in parallel (15 workers): extracts text, extracts photo, calls Claude
3. Runs `commit_result()` serially: dedup checks, renames file, writes photo, inserts DB row, moves to `processed/<stem>/`

**`watcher.py`** — real-time mode; monitors `inbox/` for new files and processes each one as it arrives.

### Supported Input Formats

| Format | Text extraction | Photo extraction |
|--------|----------------|-----------------|
| `.pdf` (text layer) | PyMuPDF direct | Embedded image heuristic → Claude Vision fallback |
| `.pdf` (scanned) | Tesseract OCR on rendered pages | Same |
| `.docx` | python-docx | Embedded images |
| `.jpg / .jpeg / .png / .bmp / .tiff / .webp` | Tesseract OCR | The image itself |

### Photo Extraction Logic
1. **Embedded portrait** — if the PDF/DOCX contains a small embedded image with a portrait aspect ratio, it is used directly (no API call)
2. **Full-page scan** — if the page is a full scan, it is sent to Claude Vision which returns bounding-box coordinates; the portrait is cropped from the high-res render
3. **No photo found** — the profile is created without a photo; can be added later via the admin edit page or detail panel

### Claude Field Extraction
The following 15 fields are extracted automatically from text by Claude:

name · date_of_birth · gender · height · weight · caste · education · occupation · job_location · residential_city · fathers_name · fathers_contact · mothers_name · mothers_contact · candidate_contact

The remaining 14 fields (blood_group · marital_status · manglik · gotra · salary · state · sub_caste · siblings · fathers_occupation · mothers_occupation · mother_tongue · about_me · hobbies · candidate_email) start as NULL for admin-uploaded profiles and must be filled in manually.

### Deduplication (3 layers)
1. **File hash** (SHA-256) already exists in DB → skip; move to existing folder as `duplicate_<filename>`
2. **Folder stem** already exists on disk → skip
3. **Same name + same DOB** already in DB → skip

### DOCX to PDF Conversion
Word documents are processed for text and photo extraction but do not produce a PDF automatically. To enable the PDF button for DOCX-sourced candidates, run the conversion script (requires Microsoft Word on macOS or LibreOffice on Linux) — see [DEPLOY.md](DEPLOY.md#8-ingesting-biodata-files).

### Manual Upload (Admin & Profile Owner)
- Admin can upload / replace a photo or PDF from any candidate's edit page or from the detail panel
- Users can upload / replace their own photo and PDF from `/profile/me/edit`
- All uploaded images are converted to JPEG at quality 95 via Pillow

---

## 16. All Fields Reference

### Default Mandatory Fields

| Field key | Label | Group |
|-----------|-------|-------|
| `name` | Full name | Personal |
| `date_of_birth` | Date of birth | Personal |
| `gender` | Gender | Personal |
| `height` | Height | Personal |
| `caste` | Caste | Caste & Religion |
| `education` | Education | Education & Career |
| `job_location` | Current location | Education & Career |
| `residential_city` | Family city | Location |

### Optional Fields (21)

| Field key | Label | Input type |
|-----------|-------|-----------|
| `weight` | Weight | text (e.g. "55 kg") |
| `blood_group` | Blood group | select |
| `marital_status` | Marital status | select |
| `mother_tongue` | Mother tongue | text |
| `sub_caste` | Sub-caste | text |
| `gotra` | Gotra | text |
| `manglik` | Manglik | select |
| `occupation` | Occupation | text |
| `salary` | Annual salary | text (e.g. "12 LPA") |
| `state` | State | text |
| `fathers_name` | Father's name | text |
| `fathers_contact` | Father's contact | text |
| `fathers_occupation` | Father's occupation | text |
| `mothers_name` | Mother's name | text |
| `mothers_contact` | Mother's contact | text |
| `mothers_occupation` | Mother's occupation | text |
| `siblings` | Siblings | text (e.g. "1 brother, 1 sister") |
| `candidate_contact` | Candidate phone | text |
| `candidate_email` | Candidate email | text |
| `hobbies` | Hobbies | text |
| `about_me` | About me | textarea |

### Computed Columns (never entered manually)

| Column | Derived from |
|--------|-------------|
| `birth_year` | Parsed from `date_of_birth` |
| `age` | Calculated from `date_of_birth` at save time |
| `height_inches` | Parsed from `height` string (handles `5'4"`, `163 cm`, `5ft4in`) |
| `weight_kg` | Parsed from `weight` string |

---

## 17. Known Limitations

### Security (must fix before public deployment)

| Issue | Fix |
|-------|-----|
| `SECRET_KEY` defaults to a hardcoded placeholder string | Generate a random value: `python3 -c "import secrets; print(secrets.token_hex(32))"` and set it in `.env` |
| No CSRF protection on state-changing POST forms | Add Flask-WTF |
| No rate limiting on `/login` or `/register` | Add Flask-Limiter |
| No upload size cap — any size file accepted | Set `app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024` |
| No security headers (X-Frame-Options, CSP, etc.) | Add Flask-Talisman or an `after_request` handler |
| API keys and admin password stored in `.env` in plain text | Ensure `.env` is in `.gitignore`; rotate any credentials that were ever committed |

### Missing Features

| Feature | Notes |
|---------|-------|
| No mobile-responsive layout on main browse and view pages | Zero `@media` breakpoints in `index.html` / `selected.html` |
| No sorting on the browse grid | Always newest-first; no sort by name / height / age |
| No weight minimum filter | Only weight max is implemented |
| No email notification on claim review | Users have no notification when their claim is accepted or rejected |
| Completeness dashboard loads all candidates at once | Fine at current scale (~500); will be slow at 10,000+ |
| No DB connection pooling | A new `psycopg2` connection is opened per request; use a pool for concurrent users |
| Duplicate label creation shows raw Postgres constraint error | Should return a friendly "that name is already taken" message |
