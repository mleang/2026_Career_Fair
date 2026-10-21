import base64
import csv
import io
import pathlib
import time
import unicodedata
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import dropbox
import pandas as pd
import streamlit as st
import requests
import html


from argon2 import PasswordHasher
from argon2.exceptions import (
    VerifyMismatchError,
    VerificationError,
    InvalidHashError,
)

# ==============================================
# --------- CONFIGURATION SETTINGS  ------------
# ==============================================
APP_TITLE  = "TotalEnergies Career Fair"
PAGE_ICON  = "🎓"
SPONSOR_NAME_UPPER = "TOTAL"
SPONSOR_NAME_LOWER = SPONSOR_NAME_UPPER.lower()
SPONSOR_NAME = "TotalEnergies"
STATE_DBX_PATH = st.secrets.get(f"STATE_DBX_PATH_{SPONSOR_NAME_UPPER}")
DOODLE_URL = st.secrets[f"DOODLE_URL_{SPONSOR_NAME_UPPER}"] if f"DOODLE_URL_{SPONSOR_NAME_UPPER}" in st.secrets else None

# STATE_DBX_PATH = st.secrets.get("STATE_DBX_PATH_TOTAL")
# DOODLE_URL = st.secrets["DOODLE_URL_TOTAL"] if "DOODLE_URL_TOTAL" in st.secrets else None
CV_ROOT = "/CVs_2025"
LOGO_PATH = "images/logo-partenaires-total-energie-300x135.png" #"images/total.png"


# Auto-refresh (poll)
UTC = timezone.utc
PARIS_TZ = ZoneInfo("Europe/Paris")
ENABLE_REMOTE_POLL     = True    # active/désactive la détection des changements distants
HASH_CHECK_TTL_SEC     = 3       # toutes les 3s on vérifie le hash Dropbox (léger)
REFRESH_MS             = 1000 #2500    # cadence de refresh UI (2.5s)
AUTO_SAVE_INTERVAL_SEC = 2
AUTOSAVE_DEBOUNCE_SEC = 0.5

# UI keys
# EDITOR_KEY =  "grid_all"       # state key pour st.data_editor
EDITOR_KEY = "candidates_overview"
GRID_FILTER_KEY = "grid_filter_key"

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

AUTH_LOG = LOG_DIR / f"auth_log_{SPONSOR_NAME_LOWER}.csv"
ACTIVITY_LOG = LOG_DIR / f"activity_log_{SPONSOR_NAME_LOWER}.csv"
DEBUG = False

# Authentication
ALLOWED_USERNAME = SPONSOR_NAME_LOWER


# =====================================
# ------------ DATA SCHEMA ------------
# =====================================

# Dropbox
# CSV
# candidate data
# UI
# etc.

# ---------------- Columns ----------------
FIRST, LAST, FILE = "first_name", "last_name", "file_name"   # FILE is a full URL
SEEN, INT, SAVE, CONT = "seen", "intend_view", "cv_saved", "contacted"

BOOL_COLS = [
    SEEN,
    INT,
    SAVE,
    CONT
]

TEXT_COLS = [
    FIRST,
    LAST,
    FILE,
]

ALL_COLS = [
    *TEXT_COLS,
    *BOOL_COLS
]


# =====================================
# -------------- UI BOOT --------------
# =====================================
def configure_page() -> None:
    """Configure the Streamlit page and load custom CSS."""

    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=PAGE_ICON,
        layout="wide",
    )

    css_path = pathlib.Path("assets/styles.css")

    if css_path.exists():
        st.markdown(
            f"<style>{css_path.read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True,
        )


configure_page()

# =====================================
# ------------- LOGGING ---------------
# =====================================
def now_utc() -> datetime:
    """Current timezone-aware time in UTC."""
    return datetime.now(UTC)

def now_paris() -> datetime:
    """Current timezone-aware time in Paris."""
    return datetime.now(PARIS_TZ)

def timestamp_utc() -> str:
    """ISO 8601 timestamp for logs and persistent storage."""
    return now_utc().isoformat(timespec="seconds")

def timestamp_paris() -> str:
    """Return the current Paris time as an ISO 8601 timestamp."""
    return datetime.now(PARIS_TZ).isoformat(timespec="seconds")

def _utc_now():
    return datetime.now(timezone.utc).isoformat()

def log_auth_event(username: str, event: str, success: bool) -> None:
    """Log authentication-related events locally."""

    file_exists = AUTH_LOG.exists()

    with AUTH_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "timestamp_paris",
                "username",
                "event",
                "success",
            ])

        writer.writerow([
            timestamp_paris(),
            username,
            event,
            int(success),
        ])

def log_activity(
    event: str,
    candidate_id: str | None = None,
) -> None:
    """Log recruiter activity locally."""

    username = st.session_state.get("username", "")
    recruiter_id = st.session_state.get("recruiter_id", "")

    file_exists = ACTIVITY_LOG.exists()

    with ACTIVITY_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "timestamp_paris",
                "username",
                "recruiter_id",
                "event",
                "candidate_id",
            ])

        writer.writerow([
            timestamp_paris(),
            username,
            recruiter_id,
            event,
            candidate_id or "",
        ])


# =====================================
# ------- AUTHENTICATION --------------
# =====================================
PASSWORD_HASHER = PasswordHasher()


def authenticate(username: str, password: str) -> str | None:
    """Authenticate the recruiter assigned to this app and return recruiter_id."""

    username = username.strip().lower()

    try:
        # 1. Restrict access to the recruiter assigned to this app
        if username != ALLOWED_USERNAME:
            raise ValueError("Unauthorized recruiter for this app")

        # 2. Retrieve the recruiter's credentials
        recruiter = st.secrets["recruiters"][username]
        password_hash = recruiter["password_hash"]

        # 3. Verify the password
        PASSWORD_HASHER.verify(password_hash, password)

        # 4. Log successful authentication
        log_auth_event(
            username=username,
            event="login_success",
            success=True,
        )

        return recruiter["recruiter_id"]

    except (
        KeyError,
        TypeError,
        ValueError,
        VerifyMismatchError,
        VerificationError,
        InvalidHashError,
    ):
        log_auth_event(
            username=username,
            event="login_failure",
            success=False,
        )

        return None
    
def render_login() -> bool:
    """
    Render login screen.

    Returns True if authentication is successful/already active.
    """

    if st.session_state.get("authenticated", False):
        return True

    st.title(f"Hi! PARIS Career Fair 2026 – {SPONSOR_NAME} Login")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input(
            "Password",
            type="password",
        )
        submitted = st.form_submit_button(
            "🔐 Sign in",
            type="primary",
        )

        if submitted:
            recruiter_id = authenticate(
                username,
                password,
            )
            if recruiter_id is None:
                st.error("Invalid username or password.")
            else:
                st.session_state.authenticated = True
                st.session_state.username = username.strip().lower()
                st.session_state.recruiter_id = recruiter_id
                st.rerun()
    return False


# ============================================================
# DROPBOX CLIENT
# ============================================================
def _access_token_from_refresh() -> str | None:
    """Get a short-lived Dropbox access token from a refresh token."""

    secrets = st.secrets

    required = (
        "DROPBOX_APP_KEY",
        "DROPBOX_APP_SECRET",
        "DROPBOX_REFRESH_TOKEN",
    )

    if not all(key in secrets for key in required):
        return None

    response = requests.post(
        "https://api.dropbox.com/oauth2/token",
        data={
            "refresh_token": secrets["DROPBOX_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
            "client_id": secrets["DROPBOX_APP_KEY"],
            "client_secret": secrets["DROPBOX_APP_SECRET"],
        },
        timeout=15,
    )

    response.raise_for_status()

    return response.json()["access_token"]


@st.cache_resource(show_spinner=False)
def _build_dbx_client() -> dropbox.Dropbox:
    """Create the Dropbox client."""

    secrets = st.secrets

    token = (
        secrets.get("DROPBOX_ACCESS_TOKEN")
        or _access_token_from_refresh()
    )

    if not token:
        raise RuntimeError(
            "Dropbox token missing. Provide either "
            "DROPBOX_ACCESS_TOKEN or "
            "DROPBOX_APP_KEY + DROPBOX_APP_SECRET + "
            "DROPBOX_REFRESH_TOKEN."
        )

    return dropbox.Dropbox(token)


def _refresh_dbx_client() -> dropbox.Dropbox:
    """Refresh the Dropbox client."""

    token = _access_token_from_refresh()

    if token:
        return dropbox.Dropbox(token)

    return _build_dbx_client()


def initialize_dropbox() -> tuple[dropbox.Dropbox | None, str | None]:
    """Initialize Dropbox and return (client, error)."""

    try:
        return _build_dbx_client(), None
    except Exception as exc:
        return None, str(exc)

# =====================================
# AUTHENTICATION
# =====================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False


if not st.session_state.authenticated:
    st.title(f"Hi! PARIS Career Fair 2026 – {SPONSOR_NAME} Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input(
            "Password",
            type="password"
        )

        submitted = st.form_submit_button(
            "🔐 Sign in",
            type="primary"
        )

        if submitted:
            recruiter_id = authenticate(username, password)
            if recruiter_id is None:
                st.error("Invalid username or password.")
            else:
                st.session_state.authenticated = True
                st.session_state.username = username.strip().lower()
                st.session_state.recruiter_id = recruiter_id
                st.rerun()
    st.stop()


username = st.session_state.username
recruiter_id = st.session_state.recruiter_id


# ============================================================
# ------------- HEADER -----------------
# ============================================================
logo_col, title_col, account_col = st.columns(
    [1.1, 5.2, 1.7],
    vertical_alignment="center",
)

with logo_col:
    try:
        st.image(LOGO_PATH, width=140)
    except Exception:
        pass

with title_col:
    st.markdown(
        f"""
        <div class="app-header">
            <div class="app-title">{SPONSOR_NAME} Career Fair</div>
            <div class="app-subtitle">
                Find candidates, review CVs and track outreach
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with account_col:
    st.markdown(
        f"""
        <div class="account-box">
            <div class="account-label"></div>
            <div class="account-user"
                 title="{html.escape(username, quote=True)}">
                Welcome {html.escape(recruiter_id)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Center the smaller logout button
    _, logout_col, _ = st.columns([1, 2, 1])

    with logout_col:
        if st.button(
            "↪ Log out",
            key="logout_header",
            use_container_width=True,
        ):
            log_auth_event(
                username=username,
                event="logout",
                success=True,
            )

            for key in ("authenticated", "username", "recruiter_id"):
                st.session_state.pop(key, None)

            st.rerun()

st.divider()

# ---------------- Dropbox auth (access token OR refresh token) ----------------
DBX = None
dbx_error = None
try:
    DBX = _build_dbx_client()
except Exception as e:
    dbx_error = str(e)

# =====================================
# DEBUG show Dropbox token status in sidebar 
# =====================================
if DEBUG:
    with st.sidebar:
        st.sidebar.subheader("Status")
        st.sidebar.write("🔐 Token:", "✅" if DBX else f"❌ {dbx_error or ''}")
        st.sidebar.write("📝 Write path (App Folder):", STATE_DBX_PATH)


# ---------------- Helpers ----------------
def _norm(value: str) -> str:
    """Normalize text for search/comparison."""
    if value is None:
        return ""
    value = str(value).strip()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = " ".join(value.lower().split())
    return value

def _ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in TEXT_COLS:
        if c not in df:
            df[c] = ""

    for c in BOOL_COLS:
        if c not in df:
            df[c] = False
        else:
            ser = df[c].astype(str).str.strip().str.lower()
            df[c] = ser.map({
                "true": True, "1": True, "yes": True, "y": True,
                "false": False, "0": False, "no": False, "n": False
            }).fillna(False).astype(bool)
    
    # Keep only actual candidates
    valid = (
        df[FIRST].fillna("").astype(str).str.strip().ne("")
        | df[LAST].fillna("").astype(str).str.strip().ne("")
        | df[FILE].fillna("").astype(str).str.strip().ne("")
    )

    df = df.loc[valid].copy()

    return df[[*TEXT_COLS, *BOOL_COLS]] 

# Dropbox I/O helpers (read fresh / write overwrite with auto-refresh)
def _ensure_folder_tree(dbx: dropbox.Dropbox, path: str):
    from posixpath import dirname
    parent = dirname(path.rstrip("/"))
    if parent and parent != "/":
        try:
            dbx.files_create_folder_v2(parent)
        except dropbox.exceptions.ApiError:
            pass

@st.cache_data(show_spinner=False, ttl=1)
def _download_current_df_from_dbx() -> pd.DataFrame:
    """Fresh read from Dropbox API to avoid CDN cache."""
    global DBX
    
    if DBX is None:
        raise RuntimeError("Dropbox not configured")
    try:
        _, resp = DBX.files_download(STATE_DBX_PATH)
    except dropbox.exceptions.AuthError:
        DBX = _refresh_dbx_client()
        _, resp = DBX.files_download(STATE_DBX_PATH)
        
    return _ensure_schema(pd.read_csv(io.BytesIO(resp.content), skip_blank_lines=True,))


def _upload_with_auto_refresh(data: bytes, path: str) -> tuple[bool, str | None]:
    global DBX
    try:
        _ensure_folder_tree(DBX, path)
        DBX.files_upload(data, path=path, mode=dropbox.files.WriteMode("overwrite"))
        return (True, None)
    except dropbox.exceptions.AuthError:
        try:
            DBX = _refresh_dbx_client()
            _ensure_folder_tree(DBX, path)
            DBX.files_upload(data, path=path, mode=dropbox.files.WriteMode("overwrite"))
            return (True, None)
        except Exception as e2:
            return (False, f"AuthError after refresh: {e2}") # TODO:  avoid exposing raw Dropbox API errors ?
    except dropbox.exceptions.ApiError as e:
        return (False, f"ApiError: {e}") # TODO:  avoid exposing raw Dropbox API errors ? 
    except Exception as e:
        return (False, f"Write error: {e}") # TODO:  avoid exposing raw Dropbox API errors ?

def _dbx_call(fn, retries: int = 3):
    global DBX
    last = None
    for attempt in range(retries):
        try:
            return fn(DBX)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last = e
            time.sleep(0.5 * (attempt + 1))
            DBX = _refresh_dbx_client()
        except dropbox.exceptions.AuthError as e:
            last = e
            DBX = _refresh_dbx_client()
    raise last

# ---------------- CV helpers ----------------
def _validate_cv_path(path: str) -> str:
    """Validate and normalize a private Dropbox CV path."""

    if not isinstance(path, str):
        raise ValueError("Invalid CV path.")

    path = path.strip()

    if not path:
        raise ValueError("Missing CV path.")

    # The CSV must contain Dropbox paths, never URLs
    if "://" in path:
        raise ValueError("CV path must not be a URL.")

    # Normalize leading slash
    if not path.startswith("/"):
        path = "/" + path

    # Normalize path
    normalized = str(PurePosixPath(path))

    # Prevent ../ traversal
    if ".." in PurePosixPath(normalized).parts:
        raise ValueError("Invalid CV path.")

    # CVs must be inside the dedicated private Dropbox folder
    if not normalized.startswith(CV_ROOT + "/"):
        raise ValueError("CV path outside allowed directory.")

    return normalized

def get_cv_pdf_base64(path: str) -> str:
    """Download a private PDF from Dropbox and return its base64 content."""
    path = _validate_cv_path(path)

    _, response = _dbx_call(
        lambda d: d.files_download(path)
    )

    pdf_bytes = response.content

    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("Downloaded file is not a valid PDF.")

    return base64.b64encode(pdf_bytes).decode("ascii")

# -------- Remote hash (léger) pour auto-refresh --------
@st.cache_data(show_spinner=False, ttl=1)
def _get_remote_hash() -> str | None:
    """Retourne content_hash du fichier côté Dropbox (sans télécharger le CSV)."""
    if DBX is None:
        return None
    try:
        md = DBX.files_get_metadata(STATE_DBX_PATH)
        # md peut être FileMetadata avec .content_hash
        if hasattr(md, "content_hash"):
            return md.content_hash
    except Exception:
        return None
    return None

# ---------------- Keys & snapshots ----------------

def _key(df_like: pd.DataFrame) -> pd.Series:
    return (
        df_like[FIRST].fillna("").astype(str).str.strip().str.lower() + "||" +
        df_like[LAST ].fillna("").astype(str).str.strip().str.lower() + "||" +
        df_like[FILE ].fillna("").astype(str).str.strip()
    )

# Keep an immutable snapshot to compute deltas (base)
def _snapshot_base():
    st.session_state.base_df = _ensure_schema(st.session_state.df).copy(deep=True)

# ---------------- Merge (3-way) ----------------

def _index_by_key(df: pd.DataFrame) -> pd.DataFrame:
    d = _ensure_schema(df).copy()
    d["_k"] = _key(d)
    return d.set_index("_k")

def _three_way_merge(base: pd.DataFrame, ours: pd.DataFrame, theirs: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """Retourne (merged_df, conflicts_list). Booleans: OR sur double modif. Text: ours gagne si double modif."""
    B, O, T = (_index_by_key(base), _index_by_key(ours), _index_by_key(theirs))
    all_idx = B.index.union(O.index).union(T.index)
    B, O, T = B.reindex(all_idx), O.reindex(all_idx), T.reindex(all_idx)

    #out = T[[*TEXT_COLS, *DATE_COLS, *BOOL_COLS]].copy()  # base "theirs"
    out = T[[*TEXT_COLS, *BOOL_COLS]].copy()
    conflicts = []

    # TEXT COLS
    for c in TEXT_COLS:#[*TEXT_COLS, *DATE_COLS]:
        b = B[c].astype("string") if c in B else pd.Series(index=all_idx, dtype="string")
        o = O[c].astype("string") if c in O else pd.Series(index=all_idx, dtype="string")
        t = T[c].astype("string") if c in T else pd.Series(index=all_idx, dtype="string")
        changed_o = (o != b)
        changed_t = (t != b)
        only_o = changed_o & ~changed_t
        only_t = changed_t & ~changed_o
        both   = changed_o & changed_t
        col = out[c].astype("string")
        col[only_o] = o[only_o]
        col[only_t] = t[only_t]
        if both.any():
            conflicts.extend([f"{c}@{idx}" for idx in both[both].index.tolist()])
            col[both] = o[both]  # ours wins
        col = col.fillna("")
        out[c] = col

    # BOOL COLS: OR si double modif
    for c in BOOL_COLS:
        b = B[c].astype("boolean") if c in B else pd.Series(index=all_idx, dtype="boolean")
        o = O[c].astype("boolean") if c in O else pd.Series(index=all_idx, dtype="boolean")
        t = T[c].astype("boolean") if c in T else pd.Series(index=all_idx, dtype="boolean")
        changed_o = (o != b) & ~(o.isna() & b.isna())
        changed_t = (t != b) & ~(t.isna() & b.isna())
        only_o = changed_o & ~changed_t
        only_t = changed_t & ~changed_o
        both   = changed_o & changed_t
        col = out[c].astype("boolean")
        col[only_o] = o[only_o]
        col[only_t] = t[only_t]
        col[both]   = (o[both].fillna(False) | t[both].fillna(False))
        out[c] = col.fillna(False).astype(bool)

    return out.reset_index(drop=True), conflicts

# ---------------- Optimistic UI apply ----------------

def _apply_optimistic(full_df: pd.DataFrame, delta_rows: pd.DataFrame) -> pd.DataFrame:
    if delta_rows.empty:
        return full_df
    full = full_df.copy()
    full["_k"] = _key(full)
    tmp = delta_rows.copy(); tmp["_k"] = _key(tmp)
    cols = ["_k"] + [c for c in (TEXT_COLS + BOOL_COLS) if c in tmp.columns]
    full = full.merge(tmp[cols], on="_k", how="left", suffixes=("", "_new"))
    for c in TEXT_COLS + BOOL_COLS:
        newc = f"{c}_new"
        if newc in full:
            if c in BOOL_COLS:
                full[c] = full[newc].combine_first(full[c]).astype(bool)
            else:
                full[c] = full[newc].combine_first(full[c]).astype(str)
            full.drop(columns=[newc], inplace=True)
    full.drop(columns=["_k"], inplace=True)
    return full

# ---------------- Editor delta build ----------------

def _build_delta_from_editor(grid_df: pd.DataFrame, edited_rows: dict) -> pd.DataFrame:
    expected_cols = [*TEXT_COLS, *BOOL_COLS]
    if not edited_rows:
        return pd.DataFrame(columns=expected_cols)

    out = []
    for i, changes in edited_rows.items():
        i = int(i)
        if 0 <= i < len(grid_df):
            base = grid_df.iloc[i][TEXT_COLS].to_dict()
            dirty = False
            for col, val in changes.items():
                if col in BOOL_COLS:
                    base[col] = bool(val); dirty = True
                elif col in TEXT_COLS:
                    base[col] = str(val); dirty = True
            if dirty:
                out.append(base)

    if not out:
        return pd.DataFrame(columns=expected_cols)

    df = pd.DataFrame(out)
    for c in BOOL_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    for c in TEXT_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    return df.reindex(columns=expected_cols)

# ---------------- Compute view & key ----------------

def _compute_view_and_key(base_df: pd.DataFrame, query: str) -> tuple[pd.DataFrame, str]:
    query_norm = _norm(query)
    if query_norm:
        tokens = [t for t in query_norm.split() if t.strip()]
        mask = pd.Series(True, index=base_df.index)
        if "_full" not in base_df:
            tmp_full = (base_df[FIRST].fillna("").astype(str) + " " + base_df[LAST].fillna("").astype(str)).map(_norm)
        else:
            tmp_full = base_df["_full"]
        for t in tokens:
            mask &= tmp_full.str.contains(t, na=False)
        view_df = base_df[mask].copy()
    else:
        view_df = base_df.copy()
    key = f"q::{query_norm}::n{len(view_df)}"
    return view_df, key

# ---------------- Grid reset ----------------

def _reset_grid(view_df: pd.DataFrame, filter_key: str) -> None:
    st.session_state.pop(EDITOR_KEY, None)
    st.session_state.pop("editor_logged_changes", None)
    st.session_state.grid_df = view_df[[*TEXT_COLS, *BOOL_COLS]].copy()
    st.session_state[GRID_FILTER_KEY] = filter_key


# ---------------- Editor changes handler ---------------

def _active_editor_key() -> str:
    return st.session_state.get("_active_editor_key", EDITOR_KEY)


def _mark_dirty() -> None:
    st.session_state.buffer_dirty = True
    st.session_state.last_edit_at = time.time()


def _handle_editor_change() -> None:
    """Apply editor changes to the working DataFrame and log checkbox changes."""
    editor_key = _active_editor_key()
    editor_state = st.session_state.get(editor_key, {})
    edited_rows = editor_state.get("edited_rows", {})

    grid_df = st.session_state.get("overview_grid_df")
    if not edited_rows or grid_df is None:
        return

    delta = _build_delta_from_editor(grid_df, edited_rows)
    if delta.empty:
        return

    st.session_state.df = _apply_optimistic(
        st.session_state.df,
        delta,
    )
    _mark_dirty()

    logged_changes = st.session_state.setdefault(
        "editor_logged_changes", {}
    )

    for row_idx, changes in edited_rows.items():
        row_idx = int(row_idx)
        if not 0 <= row_idx < len(grid_df):
            continue

        row = grid_df.iloc[row_idx]
        candidate_id = _key(pd.DataFrame([row])).iloc[0]

        for col, new_value in changes.items():
            if col not in BOOL_COLS:
                continue

            new_value = bool(new_value)
            change_key = f"{candidate_id}::{col}"

            # The initial state is the value shown before the edit.
            previous_value = logged_changes.get(
                change_key, bool(row[col])
            )
            if previous_value == new_value:
                continue

            events = {
                SEEN: {
                    True: "candidate_viewed",
                    False: "candidate_unviewed",
                },
                INT: {
                    True: "candidate_interested",
                    False: "candidate_uninterested",
                },
                SAVE: {
                    True: "candidate_saved",
                    False: "candidate_unsaved",
                },
                CONT: {
                    True: "candidate_contacted",
                    False: "candidate_uncontacted",
                },
            }

            event = events.get(col, {}).get(new_value)
            if event:
                log_activity(event=event, candidate_id=candidate_id)

            logged_changes[change_key] = new_value


def _collect_pending_editor_changes() -> None:
    """Capture any editor deltas not yet reflected in the working DataFrame."""
    editor_state = st.session_state.get(_active_editor_key(), {})
    edited_rows = editor_state.get("edited_rows", {})

    if not edited_rows:
        return

    grid_df = st.session_state.get("overview_grid_df")
    if grid_df is None:
        raise RuntimeError("The overview editor's source data is missing.")

    delta = _build_delta_from_editor(grid_df, edited_rows)
    if not delta.empty:
        st.session_state.df = _apply_optimistic(
            st.session_state.df, delta
        )
        _mark_dirty()


# ---------------- Save/flush with MERGE ----------------
def _is_revision_conflict(exc):
    """
    Identify a likely Dropbox revision conflict.

    Review this against the exact Dropbox SDK version in your
    environment. Unrecognized errors must fail safely, not retry
    indefinitely.
    """
    message = str(exc).lower()

    return any(
        token in message
        for token in (
            "conflict",
            "incorrect_revision",
            "revision mismatch",
            "write conflict",
        )
    )

# Collects pending changes from the editor before saving.
# Downloads the latest remote state.
# Merges local and remote changes against the last confirmed baseline.
# Tries a conditional upload.
# Retries revision conflicts a limited number of times.
# Updates the baseline and clears the dirty flag only after a confirmed save.

def _flush_to_disk(
    ok_text="Changes saved.",
    err_text="Could not save changes.",
    max_attempts=4,
):
    """Save local changes using a three-way merge and conditional Dropbox write."""

    if not st.session_state.get("buffer_dirty", False):
        return True

    base_df = st.session_state.get("base_df")
    if base_df is None:
        st.error("Cannot save: no confirmed baseline is available.")
        return False

    base_df = _ensure_schema(base_df.copy(deep=True))
    ours_df = _ensure_schema(st.session_state.df.copy(deep=True))

    for attempt in range(1, max_attempts + 1):
        try:
            remote_df, remote_rev = _download_latest_for_save()

            merged_df, conflicts = _three_way_merge(
                base_df,
                ours_df,
                remote_df,
            )
            merged_df = _ensure_schema(merged_df)

            if conflicts:
                st.warning(
                    f"{len(conflicts)} merge conflict(s) detected. "
                    "Conflicting text fields were resolved in favor of "
                    "the local values; checkbox values were merged."
                )

            save_df = merged_df.copy()
            for col in BOOL_COLS:
                save_df[col] = save_df[col].fillna(False).astype(bool)

            buffer = io.BytesIO()
            save_df.to_csv(buffer, index=False, encoding="utf-8")

            try:
                uploaded_metadata = _upload_if_revision_matches(
                    buffer.getvalue(),
                    remote_rev,
                )
            except dropbox.exceptions.ApiError as exc:
                if _is_revision_conflict(exc) and attempt < max_attempts:
                    continue
                if _is_revision_conflict(exc):
                    raise RuntimeError(
                        "The shared CSV kept changing during the save. "
                        "Your local edits are retained; please try again."
                    ) from exc
                raise

            # Commit local state only after Dropbox confirms the upload.
            st.session_state.df = merged_df.copy(deep=True)
            st.session_state.base_df = merged_df.copy(deep=True)
            st.session_state.buffer_dirty = False
            st.session_state.last_batch_write = time.time()
            st.session_state.last_seen_hash = getattr(
                uploaded_metadata, "content_hash", None
            )
            st.session_state.last_hash_check = time.time()

            # Force a fresh editor widget on the next rerun.
            st.session_state.editor_generation = (
                st.session_state.get("editor_generation", 0) + 1
            )
            st.session_state.pop("editor_logged_changes", None)

            st.toast(ok_text)
            return True

        except Exception as exc:
            # Never discard the local version after a failed save.
            st.session_state.df = ours_df.copy(deep=True)
            st.session_state.buffer_dirty = True
            st.error(f"{err_text} {exc}")
            return False

    return False


# ---------------- Session bootstrap ----------------
if "df" not in st.session_state:
    try:
        # st.session_state.df = fetch_state_df()
        st.session_state.df = _download_current_df_from_dbx()
        st.session_state.df["_full"] = (
            st.session_state.df[FIRST].fillna("").astype(str) + " " +
            st.session_state.df[LAST].fillna("").astype(str)
        ).map(_norm)
        _snapshot_base()
    except Exception as e:
        # st.error(f"Failed to load CSV from shared link: {e}")
        st.error(f"Failed to load CSV from Dropbox: {e}")
        st.stop()

# Defaults (sans logique buffer)
st.session_state.setdefault("q", "")
st.session_state.setdefault("prev_q", "")
st.session_state.setdefault("last_auto_save", 0.0)

# Auto-refresh state
st.session_state.setdefault("last_seen_hash", None)
st.session_state.setdefault("last_hash_check", 0.0)


# Download the latest remote DataFrame and revision for save operations.
def _download_latest_for_save():
    """
    Return the latest remote DataFrame and the revision obtained
    from the same Dropbox download operation.

    Do not decorate this function with st.cache_data.
    """
    global DBX

    if DBX is None:
        raise RuntimeError("Dropbox client is not initialized.")

    try:
        metadata, response = DBX.files_download(STATE_DBX_PATH)

    except dropbox.exceptions.AuthError:
        DBX = _refresh_dbx_client()
        metadata, response = DBX.files_download(STATE_DBX_PATH)

    remote_df = pd.read_csv(
        io.BytesIO(response.content),
        skip_blank_lines=True,
    )

    remote_df = _ensure_schema(remote_df)

    return remote_df, metadata.rev

# Upload only if the revision is still current
def _upload_if_revision_matches(csv_bytes, expected_rev):
    """
    Upload only if the Dropbox file still has expected_rev.

    Returns the metadata of the committed file.
    Never falls back to an unconditional overwrite.
    """
    global DBX

    if DBX is None:
        raise RuntimeError("Dropbox client is not initialized.")

    try:
        return DBX.files_upload(
            csv_bytes,
            STATE_DBX_PATH,
            mode=dropbox.files.WriteMode.update(expected_rev),
            mute=True,
        )

    except dropbox.exceptions.AuthError:
        DBX = _refresh_dbx_client()

        # Keep the same expected revision after token refresh.
        # If the file changed in the meantime, this must fail safely.
        return DBX.files_upload(
            csv_bytes,
            STATE_DBX_PATH,
            mode=dropbox.files.WriteMode.update(expected_rev),
            mute=True,
        )
    
# ============================================================
# SEARCH / CANDIDATE SELECTION
# ============================================================
st.markdown(
    '<div class="section-title">🔎 Find a candidate</div>',
    unsafe_allow_html=True,
)

search_col, save_col, doodle_col = st.columns([3, 1, 1])

# ------------------------------------------------------------
# Candidate search + selection
# ------------------------------------------------------------
grid = st.session_state.df.copy()
row_keys = _key(grid).tolist()
label_of = dict(
    zip(
        row_keys,
        (
            grid[FIRST].astype(str)
            + " "
            + grid[LAST].astype(str)
        ).tolist(),
    )
)

with search_col:
    sel = st.selectbox(
        "Candidate",
        options=row_keys,
        format_func=label_of.get,
        index=None,
        placeholder="Type a first or last name…",
        key="candidate_search",
        label_visibility="collapsed",
    )



# ------------------------------------------------------------
# Save
# ------------------------------------------------------------
with save_col:
    if st.button(
        "💾 Save now",
        key="save_top",
        use_container_width=True,
    ):
        log_activity("save_now_clicked")
        st.session_state["_want_save"] = True

# ------------------------------------------------------------
# Doodle
# ------------------------------------------------------------
with doodle_col:
    if DOODLE_URL:
        st.link_button(
            "📅 Doodle",
            DOODLE_URL,
            use_container_width=True,
        )

filter_active = bool(
    st.session_state.q.strip()
)
 
    
# ---- Flush when toggling filter state (keep only B) ----
# B) filter -> no filter : matérialiser les deltas en mémoire du widget puis flush
if st.session_state.prev_q.strip() and (not filter_active):
    ed_state_pre = st.session_state.get(EDITOR_KEY, {})
    edited_rows_pre = ed_state_pre.get("edited_rows", {})
    if edited_rows_pre:
        df_tmp = _build_delta_from_editor(st.session_state.get("grid_df", pd.DataFrame()), edited_rows_pre)
        if not df_tmp.empty:
            st.session_state.df = _apply_optimistic(st.session_state.df, df_tmp)
    _flush_to_disk("Saved changes before removing filter ✅", "Save failed")

st.session_state.prev_q = st.session_state.q

# ---------------- Grid (stable source; no replacement during the same rerun) ----------------
def _toggle_candidate(row_key: str, col: str) -> None:
    """
    Toggle one candidate boolean field.

    The candidate is identified by its stable row key rather than
    by its position in the filtered grid.

    Any pending edits made through st.data_editor are applied first.
    """

    # -------------------------------------------------
    # 1. Apply pending data_editor changes first
    # -------------------------------------------------
    ed_state = st.session_state.get(EDITOR_KEY, {})
    edited_rows = ed_state.get("edited_rows", {})

    if edited_rows:
        # delta = _build_delta_from_editor(
        #     st.session_state.grid_df,
        #     edited_rows,
        # )
        editor_df = st.session_state.get(
            "overview_grid_df",
            pd.DataFrame(columns=[*TEXT_COLS, *BOOL_COLS]),
        )

        delta = _build_delta_from_editor(
            editor_df,
            edited_rows,
        )

        if not delta.empty:
            st.session_state.df = _apply_optimistic(
                st.session_state.df,
                delta,
            )

    # -------------------------------------------------
    # 2. Find candidate in the complete dataframe
    # -------------------------------------------------
    df = st.session_state.df
    mask = _key(df) == row_key
    if not mask.any():
        return

    # -------------------------------------------------
    # 3. Toggle selected column
    # -------------------------------------------------
    old_value = bool(df.loc[mask, col].iloc[0])
    new_value = not old_value
    df.loc[mask, col] = new_value
    
    st.session_state.buffer_dirty = True
    st.session_state.last_edit_at = time.time()

    # -------------------------------------------------
    # 4. Log action
    # -------------------------------------------------
    events = {
        SEEN: {
            True: "candidate_viewed",
            False: "candidate_unviewed",
        },
        INT: {
            True: "candidate_interested",
            False: "candidate_uninterested",
        },
        SAVE: {
            True: "candidate_saved",
            False: "candidate_unsaved",
        },
        CONT: {
            True: "candidate_contacted",
            False: "candidate_uncontacted",
        },
    }

    event = events.get(col, {}).get(new_value)

    if event:
        log_activity(
            event=event,
            candidate_id=row_key,
        )

    # -------------------------------------------------
    # 5. Rebuild filtered grid
    # -------------------------------------------------
    view_df, filter_key = _compute_view_and_key(
        st.session_state.df,
        st.session_state.q,
    )
    _reset_grid(
        view_df,
        filter_key,
    )

    # The overview will be rebuilt from the updated full dataframe
    # on the next Streamlit rerun.
    st.session_state.pop(EDITOR_KEY, None)
    st.session_state.pop("editor_logged_changes", None)


# grid = st.session_state.grid_df
grid = st.session_state.df.copy()
row_keys = _key(grid).tolist()
label_of = dict(zip(row_keys, (grid[FIRST].astype(str) + " " + grid[LAST].astype(str)).tolist()))

# ============================================================
# CANDIDATE
# ============================================================
if sel is None:

    st.info(
        "👆 Start typing a name above to find a candidate."
    )

else:

    selected_mask = _key(grid) == sel
    selected_row = grid.loc[selected_mask].iloc[0]

    candidate_id = str(sel)
    path = str(selected_row[FILE] or "").strip()

    st.markdown(
        f"""
        <div class="candidate-name">
            👤 Candidate : {selected_row[FIRST]} {selected_row[LAST]}
        </div>
        """,
        unsafe_allow_html=True,
    )
    
    open_c, seen_c, cont_c = st.columns([1, 1, 1])

    with open_c:
        if not path:
            st.button(
                "📄 No CV",
                disabled=True,
                use_container_width=True,
            )

        else:
            if st.button(
                "📄 Open CV",
                use_container_width=True,
            ):
                try:
                    pdf_b64 = get_cv_pdf_base64(path)

                    html = f"""
                    <script>
                    const base64 = "{pdf_b64}";
                    const binary = atob(base64);
                    const bytes = new Uint8Array(binary.length);

                    for (let i = 0; i < binary.length; i++) {{
                        bytes[i] = binary.charCodeAt(i);
                    }}

                    const blob = new Blob(
                        [bytes],
                        {{ type: "application/pdf" }}
                    );

                    const url = URL.createObjectURL(blob);

                    window.open(url, "_blank");

                    setTimeout(
                        () => URL.revokeObjectURL(url),
                        60000
                    );
                    </script>
                    """

                    st.components.v1.html(
                        html,
                        height=0,
                    )

                    log_activity(
                        event="cv_view",
                        candidate_id=candidate_id,
                    )

                except Exception:
                    st.error(
                        "Unable to open this CV."
                    )

    with seen_c:
        is_viewed = bool(selected_row[SEEN])

        st.button(
            "👁 Unmark viewed"
            if is_viewed
            else "👁 Mark viewed",
            use_container_width=True,
            on_click=_toggle_candidate,
            args=(candidate_id, SEEN),
        )

    with cont_c:
        is_contacted = bool(selected_row[CONT])

        st.button(
            "✉️ Unmark contacted"
            if is_contacted
            else "✉️ Mark contacted",
            use_container_width=True,
            on_click=_toggle_candidate,
            args=(candidate_id, CONT),
        )


# ============================================================
# CANDIDATES OVERVIEW 
# ============================================================
st.markdown(
    '<div class="section-title">👥 Candidates Overview</div>',
    unsafe_allow_html=True,
)

# Initialize state
st.session_state.setdefault("status_filter", "All")
st.session_state.setdefault("editor_generation", 0)

# Start from the complete local dataset
df = _ensure_schema(st.session_state.df.copy(deep=True))

# Apply status filter
status_filter = st.session_state.status_filter

if status_filter == "Viewed":
    filtered_df = df[df[SEEN]].copy()
elif status_filter == "Interested":
    filtered_df = df[df[INT]].copy()
elif status_filter == "CVs saved":
    filtered_df = df[df[SAVE]].copy()
elif status_filter == "Contacted":
    filtered_df = df[df[CONT]].copy()
else:
    filtered_df = df.copy()

# Keep the selection even when a status filter excludes that candidate.
sel = st.session_state.get("candidate_search")
if sel is not None:
    overview_df = filtered_df.loc[_key(filtered_df).eq(sel)].copy()
    if overview_df.empty:
        st.caption(
            "The selected candidate does not match the current status filter."
        )
    else:
        st.caption(
            f"Showing selected candidate: "
            f"{overview_df.iloc[0][FIRST]} {overview_df.iloc[0][LAST]}"
        )
else:
    overview_df = filtered_df.copy()
    st.caption(f"Showing {len(overview_df)} candidates")

# Store the exact editor source, including the private Dropbox path.
# FILE is deliberately excluded from the visible editor.
st.session_state.overview_grid_df = overview_df[
    [*TEXT_COLS, *BOOL_COLS]
].copy()

active_editor_key = (
    f"{EDITOR_KEY}_{st.session_state.editor_generation}"
)
st.session_state["_active_editor_key"] = active_editor_key

st.data_editor(
    st.session_state.overview_grid_df.drop(columns=[FILE], errors="ignore"),
    key=active_editor_key,
    on_change=_handle_editor_change,
    column_config={
        FIRST: st.column_config.TextColumn("First name"),
        LAST: st.column_config.TextColumn("Last name"),
        SEEN: st.column_config.CheckboxColumn("Viewed"),
        INT: st.column_config.CheckboxColumn("Interested"),
        SAVE: st.column_config.CheckboxColumn("Saved"),
        CONT: st.column_config.CheckboxColumn("Contacted"),
    },
    disabled=[FIRST, LAST],
    hide_index=True,
    use_container_width=True,
    num_rows="fixed",
)



# # ---------------- Read changes from editor state (no DataFrame diff) ----------------
# ed_state = st.session_state.get(EDITOR_KEY, {})
# edited_rows = ed_state.get("edited_rows", {})  # {row_idx: {col: new_val, ...}}

# delta_df = _build_delta_from_editor(st.session_state.grid_df, edited_rows)

# # ---------------- Apply edits (optimistic), no buffer/debounce ----------------
# now = time.time()
# if not delta_df.empty:
#     st.session_state.df = _apply_optimistic(st.session_state.df, delta_df)



# ---------------- Autosave ----------------
# now = time.time()

# if st.session_state.get("buffer_dirty", False):
#     st.warning("Changes pending — saving shortly.")

#     last_edit_at = st.session_state.get("last_edit_at", now)

#     if now - last_edit_at >= AUTOSAVE_DEBOUNCE_SEC:
#         _flush_to_disk()

# else:
#     st.caption("All changes saved.")

now = time.time()

if st.session_state.get("buffer_dirty", False):
    last_edit_at = st.session_state.get("last_edit_at", now)

    if now - last_edit_at >= AUTOSAVE_DEBOUNCE_SEC:
        _flush_to_disk("Saved ✅", "Auto-save failed")
    else:
        st.caption("Changes pending — saving shortly.")
else:
    st.caption("All changes saved.")

# Explicit Save button: force a save attempt.
if st.session_state.pop("_want_save", False):
    # Make sure the debounce does not prevent a manual save.
    st.session_state.buffer_dirty = True
    st.session_state.last_edit_at = 0.0

    _flush_to_disk("Saved ✅", "Save failed")
  
# Traitement du bouton Save now
# if st.session_state.pop("_want_save", False):
#     _flush_to_disk("Saved ✅", "Save failed")

# ---------------- Auto-save périodique (toutes les 5s) ----------------
# if (now - st.session_state["last_auto_save"]) >= 5:
#     _flush_to_disk("Saved ✅", "Auto-save failed")
#     st.session_state["last_auto_save"] = now

# ---------------- Auto-refresh (poll hash) ----------------
if ENABLE_REMOTE_POLL:
    # 1) Vérifie périodiquement le hash Dropbox (léger, pas de download)
    should_check = (now - st.session_state["last_hash_check"]) >= HASH_CHECK_TTL_SEC
    if should_check:
        rhash = _get_remote_hash()
        st.session_state["last_hash_check"] = now
        if rhash and st.session_state["last_seen_hash"] is None:
            st.session_state["last_seen_hash"] = rhash
        elif rhash and rhash != st.session_state["last_seen_hash"]:
            # Un autre utilisateur a modifié le fichier → merge 3-voies
            try:
                # remote_df represents the CSV downloaded from Dropbox at that moment. It may become outdated immediately after downloading if another recruiter saves a change. It is therefore a snapshot, not a permanently current object.
                remote_df = _download_current_df_from_dbx()
            except Exception:
                # remote_df = fetch_state_df()
                #st.error("Unable to access the private Dropbox state.")
                remote_df = None
                st.error("Unable to refresh data right now.")

            if remote_df is not None:
    

                base_df = st.session_state.get("base_df", remote_df.copy())
                ours_df = st.session_state.df

                merged, conflicts = _three_way_merge(base_df, ours_df, remote_df)
                if conflicts:
                    st.warning(
                        f"{len(conflicts)} conflict(s) detected while merging. "
                        "Please check the merged data."
                    )

                st.session_state.df = merged
                _snapshot_base()
                st.session_state["last_seen_hash"] = rhash

                # Rebuild grid sans changer le filtre
                base_df_for_view = st.session_state.df
                view_df, current_filter_key = _compute_view_and_key(base_df_for_view, st.session_state.q)
                _reset_grid(view_df, current_filter_key)

                msg = "Données mises à jour depuis Dropbox 🔄 (merge appliqué)"
                if conflicts:
                    msg += f" • {len(conflicts)} conflit(s) résolu(s)"
                st.toast(msg)

    # 2) Déclenche l’auto-refresh UI toutes REFRESH_MS ms (API stable)
    try:
        st.autorefresh(interval=REFRESH_MS, key="__poll_remote__")
    except Exception:
        # Compat anciennes versions de Streamlit
        if hasattr(st, "experimental_autorefresh"):
            st.experimental_autorefresh(interval=REFRESH_MS, key="__poll_remote__")


st.divider()

# ============================================================
# RECRUITER OVERVIEW - CANDIDATES TRACKING
# ============================================================
st.markdown(
    '<div class="section-title">📊 Candidates tracking</div>',
    unsafe_allow_html=True,
)
# st.caption("Track your progress and quickly find candidates to follow up with.")
# df = st.session_state.df
if "status_filter" not in st.session_state:
    st.session_state.status_filter = "All"

def set_status_filter(status: str):
    st.session_state.status_filter = status
    st.session_state.candidate_search = None

def as_bool(series):
    """Handle actual booleans and common CSV string representations."""
    return (
        series.fillna(False)
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(["true", "1", "yes", "oui"])
    )


# Compact summary at the top
# 1. Normalize boolean columns
for col in BOOL_COLS:
    df[col] = as_bool(df[col]) if col in df.columns else False

# 2. Compute statistics from the full candidate dataframe
total = len(df)
viewed = int(df[SEEN].sum())
interested = int(df[INT].sum())
saved = int(df[SAVE].sum())
contacted = int(df[CONT].sum())

# 3. Compact summary at the top
st.caption(
    f"👥 **{total} candidates** · "
    f"👁️ {viewed} viewed · "
    f"👍 {interested} interested · "
    f"🔖 {saved} CVs saved · "
    f"✉️ {contacted} contacted"
)

# 4. Candidate filter
filter_options = ["All", "Viewed", "Interested", "CVs saved", "Contacted"]

if "status_filter" not in st.session_state:
    st.session_state.status_filter = "All"


def on_status_filter_change():
    ed_state = st.session_state.get(EDITOR_KEY, {})
    edited_rows = ed_state.get("edited_rows", {})

    if edited_rows:
        editor_df = st.session_state.get(
            "overview_grid_df",
            pd.DataFrame(columns=[*TEXT_COLS, *BOOL_COLS]),
        )
        delta = _build_delta_from_editor(editor_df, edited_rows)

        if not delta.empty:
            st.session_state.df = _apply_optimistic(
                st.session_state.df,
                delta,
            )
            st.session_state.buffer_dirty = True
            st.session_state.last_edit_at = time.time()

    st.session_state.pop(EDITOR_KEY, None)
    st.session_state.pop("editor_logged_changes", None)
    # Reset only the editor's pending widget state.
 

st.selectbox(
    "Filter candidates",
    options=filter_options,
    key="status_filter",
    on_change=on_status_filter_change,
)

