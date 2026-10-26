import time
from pathlib import Path
from datetime import timezone
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
import html

# from streamlit_autorefresh import st_autorefresh
from ui_render import configure_page, render_header
from authentication_handler import authenticate, render_login
from audit_logger import log_activity
from dropbox_storage import _build_dbx_client, _get_remote_hash, get_cv_pdf_base64, _download_current_df_from_dbx
from merge_handler import _ensure_schema, _norm, _key, _build_delta_from_editor
from merge_handler import _apply_optimistic, _compute_view_and_key, _flush_to_disk
from merge_handler import _reset_grid, _handle_editor_change, _active_editor_key, _mark_dirty
from merge_handler import _three_way_merge, _snapshot_base
import logging
from dropbox_storage import DROPBOX_TIMEOUT_SEC

# ==============================================
# --------- CONFIGURATION SETTINGS  ------------
# ==============================================
SPONSOR_NAME_UPPER = "LOREAL"
SPONSOR_NAME_LOWER = SPONSOR_NAME_UPPER.lower()
SPONSOR_NAME = "L'Oréal"
APP_TITLE  = f"{SPONSOR_NAME} Career Fair"
PAGE_ICON  = "🎓"
STATE_DBX_PATH = st.secrets.get(f"STATE_DBX_PATH_{SPONSOR_NAME_UPPER}")
DOODLE_URL = st.secrets[f"DOODLE_URL_EDITOR_{SPONSOR_NAME_UPPER}"] if f"DOODLE_URL_EDITOR_{SPONSOR_NAME_UPPER}" in st.secrets else None
LOGO_PATH = "images/logo_loreal.jpg"

# Auto-refresh (poll)
UTC = timezone.utc
PARIS_TZ = ZoneInfo("Europe/Paris")
ENABLE_REMOTE_POLL     = True    # active/désactive la détection des changements distants
HASH_CHECK_TTL_SEC     = 1     # toutes les 1s on vérifie le hash Dropbox (léger)
REFRESH_MS             = 1000 #2500    # cadence de refresh UI (2.5s) Poll approximately every 2 seconds
AUTOSAVE_DEBOUNCE_SEC = 1.0
MAX_SAVE_ATTEMPTS = 5

# UI keys
EDITOR_KEY = "candidates_overview"
GRID_FILTER_KEY = "grid_filter_key"

# Logging for authentication and activity
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
AUTH_LOG = LOG_DIR / f"auth_log_{SPONSOR_NAME_LOWER}.csv"
ACTIVITY_LOG = LOG_DIR / f"activity_log_{SPONSOR_NAME_LOWER}.csv"
DEBUG = False

# Authentication
ALLOWED_USERNAME = SPONSOR_NAME_LOWER

# Logging setup 
LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    handler = logging.FileHandler(LOGS_DIR / f"app_{SPONSOR_NAME_LOWER}.log")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

# =====================================
# ------------ DATA SCHEMA ------------
# =====================================
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


configure_page(APP_TITLE, PAGE_ICON)

# =====================================
# AUTHENTICATION
# =====================================
authenticate(st.session_state.get("username", ""), st.session_state.get("password", ""), ALLOWED_USERNAME, AUTH_LOG)
render_login(SPONSOR_NAME, ALLOWED_USERNAME, AUTH_LOG)
username = st.session_state.username
recruiter_id = st.session_state.recruiter_id

# ======================================
# ------------- HEADER -----------------
# ======================================
render_header(LOGO_PATH, SPONSOR_NAME, username, recruiter_id, AUTH_LOG)

# --- Dropbox auth (access token OR refresh token) ---
if "dbx" not in st.session_state:
    try:
        st.session_state.dbx = _build_dbx_client() # ✅ Creates with timeout
        st.session_state.dbx_error = None
    except Exception as e:
        st.session_state.dbx = None
        st.session_state.dbx_error = str(e)

DBX = st.session_state.dbx  # ✅ Use cached client
dbx_error = st.session_state.dbx_error

# Initialize hash on first run
if "last_seen_hash" not in st.session_state:
    try:
        initial_hash = _get_remote_hash(DBX, STATE_DBX_PATH)
        st.session_state.last_seen_hash = initial_hash
        st.session_state.last_hash_check = time.time()
        logger.info(f"Initialized hash: {initial_hash[:16] if initial_hash else 'None'}...")
    except Exception as e:
        logger.warning(f"Failed to initialize hash: {e}")
        st.session_state.last_seen_hash = None
        st.session_state.last_hash_check = time.time()

# =====================================
# DEBUG show Dropbox token status in sidebar 
# =====================================
if DEBUG:
    with st.sidebar:
        st.sidebar.subheader("Status")
        st.sidebar.write("🔐 Token:", "✅" if DBX else f"❌ {dbx_error or ''}")
        st.sidebar.write("📝 Write path (App Folder):", STATE_DBX_PATH)

# ---------------- Session bootstrap ----------------
if "df" not in st.session_state:
    try:
        # st.session_state.df = fetch_state_df()
        st.session_state.df = _download_current_df_from_dbx(DBX, STATE_DBX_PATH)
        st.session_state.df = _ensure_schema(st.session_state.df, TEXT_COLS, BOOL_COLS, FIRST, LAST, FILE)
        
        st.session_state.df["_full"] = (
            st.session_state.df[FIRST].fillna("").astype(str) + " " +
            st.session_state.df[LAST].fillna("").astype(str)
        ).map(_norm)
        _snapshot_base(TEXT_COLS, BOOL_COLS, FIRST, LAST, FILE)
    except Exception as e:
        # st.error(f"Failed to load CSV from shared link: {e}")
        st.error(f"Failed to load CSV from Dropbox: {e}")
        st.stop()


# ----------------
# SESSION STATE 
# ----------------
# Defaults (sans logique buffer)
st.session_state.setdefault("q", "")
st.session_state.setdefault("prev_q", "")
st.session_state.setdefault("last_auto_save", 0.0)

# Auto-refresh state
st.session_state.setdefault("last_seen_hash", None)
st.session_state.setdefault("last_hash_check", 0.0)

st.session_state.setdefault("buffer_dirty", False)
st.session_state.setdefault("editor_generation", 0)
st.session_state.setdefault("last_edit_at", None)
st.session_state.setdefault("last_saved_at", None)
st.session_state.setdefault("save_status", "Saved")
st.session_state.setdefault("save_error", None)

st.session_state.setdefault("dirty", False)
st.session_state.setdefault("save_conflicts", [])
st.session_state.setdefault("remote_change_pending", False)

    
# ============================================================
# SEARCH / CANDIDATE SELECTION
# ============================================================
st.markdown(
    '<div class="section-title">🔎 Find a candidate</div>',
    unsafe_allow_html=True,
)

search_col, save_col, refresh_col, doodle_col = st.columns([3, 1, 1, 1])

# ------------------------------------------------------------
# Candidate search + selection
# ------------------------------------------------------------
grid = st.session_state.df.copy()
row_keys = _key(grid, FIRST, LAST, FILE).tolist()
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
        log_activity(ACTIVITY_LOG, "save_now_clicked")
        st.session_state["_want_save"] = True


# ✅ Refresh Button ----
with refresh_col:
    if st.button(
        "🔄 Refresh",
        key="refresh_top",
        use_container_width=True,
        help="Download latest data from Dropbox"
    ):
        logger.info("[MANUAL REFRESH] User clicked refresh button")
        log_activity(ACTIVITY_LOG, "manual_refresh_clicked")
        
        try:
            # Force download latest data
            remote_df = _download_current_df_from_dbx(DBX, STATE_DBX_PATH)
            
            if remote_df is not None:
                # Update remote hash first
                rhash = _get_remote_hash(DBX, STATE_DBX_PATH)
                
                base_df = st.session_state.get("base_df")
                ours_df = st.session_state.get("df")
                
                # 3-way merge
                if base_df is None or ours_df is None:
                    st.session_state.df = remote_df.copy(deep=True)
                    st.session_state.base_df = remote_df.copy(deep=True)
                    conflicts = []
                    logger.info("[MANUAL REFRESH] No baseline - initialized from remote")
                else:
                    merged_df, conflicts = _three_way_merge(
                        base_df,
                        ours_df,
                        remote_df,
                        TEXT_COLS, 
                        BOOL_COLS,
                        FIRST, 
                        LAST, 
                        FILE
                    )
                    merged_df = _ensure_schema(
                        merged_df, 
                        TEXT_COLS, 
                        BOOL_COLS, 
                        FIRST, 
                        LAST, 
                        FILE
                    )
                    
                    st.session_state.df = merged_df.copy(deep=True)
                    st.session_state.base_df = merged_df.copy(deep=True)
                    logger.info(f"[MANUAL REFRESH] Merged {len(merged_df)} rows, {len(conflicts)} conflicts")
                
                # Update hash and flags
                st.session_state.last_seen_hash = rhash
                st.session_state.last_hash_check = time.time()
                st.session_state.remote_change_pending = False
                
                # Refresh grid display
                view_df, filter_key = _compute_view_and_key(
                    st.session_state.df,
                    st.session_state.get("q", ""),
                    FIRST, 
                    LAST
                )
                _reset_grid(
                    view_df, 
                    filter_key, 
                    EDITOR_KEY, 
                    GRID_FILTER_KEY, 
                    TEXT_COLS, 
                    BOOL_COLS
                )
                
                if conflicts:
                    st.warning(
                        f"ℹ️ {len(conflicts)} conflict(s) detected. "
                        "Resolved in favor of local values."
                    )
                
                logger.info("[MANUAL REFRESH] Refresh complete ✅")
                st.toast("✅ Data refreshed!")
                st.rerun()
        
        except Exception as e:
            logger.error(f"[MANUAL REFRESH] Failed: {e}", exc_info=True)
            st.error(f"❌ Refresh failed: {e}")
            
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
            st.session_state.df = _apply_optimistic(st.session_state.df, df_tmp, TEXT_COLS, BOOL_COLS, FIRST, LAST, FILE)
    _flush_to_disk(DBX, STATE_DBX_PATH, TEXT_COLS, BOOL_COLS, FIRST, LAST, FILE, "Saved changes before removing filter ✅", "Save failed")

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
    ed_state = st.session_state.get(_active_editor_key(EDITOR_KEY), {})
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

        if editor_df is None or editor_df.empty:
            return
    
        delta = _build_delta_from_editor(
            editor_df,
            edited_rows,
            TEXT_COLS,
            BOOL_COLS,
        )

        if not delta.empty:
            st.session_state.df = _apply_optimistic(
                st.session_state.df,
                delta,
                TEXT_COLS,
                BOOL_COLS, 
                FIRST,  
                LAST,
                FILE
            )

    # -------------------------------------------------
    # 2. Find candidate in the complete dataframe
    # -------------------------------------------------
    df = st.session_state.df
    mask = _key(df, FIRST, LAST, FILE) == row_key
    if not mask.any():
        return

    # -------------------------------------------------
    # 3. Toggle selected column
    # -------------------------------------------------
    old_value = bool(df.loc[mask, col].iloc[0])
    new_value = not old_value
    df.loc[mask, col] = new_value
    
    # st.session_state.buffer_dirty = True
    # st.session_state.last_edit_at = time.time()
    _mark_dirty()

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
            ACTIVITY_LOG,
            event=event,
            candidate_id=row_key,
        )

    # -------------------------------------------------
    # 5. Rebuild filtered grid
    # -------------------------------------------------
    view_df, filter_key = _compute_view_and_key(
        st.session_state.df,
        st.session_state.q,
        FIRST, 
        LAST
    )
    _reset_grid(
        view_df,
        filter_key,
        EDITOR_KEY, 
        GRID_FILTER_KEY, 
        TEXT_COLS, 
        BOOL_COLS
    )



grid = st.session_state.df.copy()  # st.session_state.df : Current working data
row_keys = _key(grid, FIRST, LAST, FILE).tolist()
label_of = dict(zip(row_keys, (grid[FIRST].astype(str) + " " + grid[LAST].astype(str)).tolist()))

# ============================================================
# CANDIDATE
# ============================================================
# Candidate Search & Selection: Recruiters can search/select candidates using a dropdown 
# or filter by status (Viewed, Interested, Saved, Contacted).
if sel is None:

    st.info(
        "👆 Start typing a name above to find a candidate."
    )

else:

    selected_mask = _key(grid, FIRST, LAST, FILE) == sel
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
                    pdf_b64 = get_cv_pdf_base64(DBX, path)

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
                        ACTIVITY_LOG,
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
# df: a temporary local DataFrame used in the overview and statistics.
df = _ensure_schema(st.session_state.df, TEXT_COLS, BOOL_COLS, FIRST, LAST, FILE) # _ensure_schema already has .copy(deep=True)

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

# Selection is optional - just show selected candidate if they exist
sel = st.session_state.get("candidate_search")
if sel is not None:
    selected_rows = filtered_df.loc[_key(filtered_df, FIRST, LAST, FILE).eq(sel)]
    if not selected_rows.empty:
        overview_df = selected_rows.copy()
        st.caption(
            f"👁️ Viewing: {overview_df.iloc[0][FIRST]} {overview_df.iloc[0][LAST]}"
        )
    else:
        # Selection doesn't match filter
        overview_df = filtered_df.copy()
        st.caption("⚠️ Selected candidate not in this filter. Showing all.")
else:
    overview_df = filtered_df.copy()

# Store the exact editor source, including the private Dropbox path.
# FILE is deliberately excluded from the visible editor.
st.session_state.overview_grid_df = overview_df[
    [*TEXT_COLS, *BOOL_COLS]
].copy()

active_editor_key = (
    f"{EDITOR_KEY}_{st.session_state.editor_generation}"
)
st.session_state["_active_editor_key"] = active_editor_key

# Interactive Grid (st.data_editor): 
# Recruiters can edit candidate attributes or check/uncheck status checkboxes directly in a table view.
# st.data_editor : needed to translate edited row positions back to candidates.
st.data_editor(
    st.session_state.overview_grid_df.drop(columns=[FILE], errors="ignore"),
    key=active_editor_key,
    on_change=_handle_editor_change,
    args=(EDITOR_KEY, ACTIVITY_LOG, TEXT_COLS, BOOL_COLS, SEEN, INT, SAVE, CONT, FIRST, LAST, FILE),    
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

# ---------------- Autosave ----------------
now = time.time()

@st.fragment(run_every=0.5)
def autosave_status():
    now = time.time()

    if st.session_state.pop("_want_save", False):
        if st.session_state.get("buffer_dirty", False):
            st.session_state.last_edit_at = 0.0

            _flush_to_disk(
                DBX, STATE_DBX_PATH, TEXT_COLS, BOOL_COLS,
                FIRST, LAST, FILE, "Saved ✅", "Save failed"
            )
        else:
            st.info("No unsaved changes.")

    elif st.session_state.get("buffer_dirty", False):
        last_edit_at = st.session_state.get("last_edit_at")

        if last_edit_at is None:
            st.session_state.last_edit_at = now
            remaining_wait = AUTOSAVE_DEBOUNCE_SEC

        else:
            elapsed = now - last_edit_at
            remaining_wait = AUTOSAVE_DEBOUNCE_SEC - elapsed

        if remaining_wait <= 0:
            st.caption("💾 Saving...")

            save_start = time.time()
            success = _flush_to_disk(
                DBX, STATE_DBX_PATH, TEXT_COLS, BOOL_COLS,
                FIRST, LAST, FILE,
                "Saved ✅", "Auto-save failed"
            )

            duration = time.time() - save_start

            if success and not st.session_state.get("buffer_dirty"):
                st.caption(f"✅ Saved in {duration:.1f}s")

            elif not success:
                st.caption("❌ Save failed. Your changes are retained.")
        else:
            st.caption(
                f"⏳ Changes pending — saving in {remaining_wait:.1f}s..."
            )

    else:
        st.caption("✅ All changes saved")


autosave_status()

# ---------------- Auto-refresh (poll hash) ----------------
# Now define for main script
now = time.time()
if ENABLE_REMOTE_POLL:
    # 1) Déclenche l’auto-refresh UI toutes REFRESH_MS ms (API stable)
    try:
        st.autorefresh(interval=REFRESH_MS, key="__poll_remote__")
    except Exception:
        # Compat anciennes versions de Streamlit
        if hasattr(st, "experimental_autorefresh"):
            st.experimental_autorefresh(interval=REFRESH_MS, key="__poll_remote__")

    # 2) Vérifie périodiquement le hash Dropbox (léger, pas de download)
    should_check = (now - st.session_state["last_hash_check"]) >= HASH_CHECK_TTL_SEC
    if should_check:
        rhash = _get_remote_hash(DBX, STATE_DBX_PATH)
        last_seen = st.session_state.get("last_seen_hash")
        
        # ✅ ADD LOGGING
        logger.debug(f"[HASH CHECK] Remote hash = {rhash[:16] if rhash else 'None'}")
        logger.debug(f"[HASH CHECK] Last seen hash = {last_seen[:16] if last_seen else 'None'}")
        logger.debug(f"[HASH CHECK] Match = {rhash == last_seen}")
        
        # Initialize on first run        
        if rhash and st.session_state["last_seen_hash"] is None:
            st.session_state["last_seen_hash"] = rhash
            st.session_state.last_hash_check = now
            logger.info("[HASH CHECK] Initialized last_seen_hash")
        
        # Detect remote change
        elif rhash and rhash != st.session_state["last_seen_hash"]:
            logger.warning(f"[REMOTE CHANGE] Detected! {last_seen[:8]}... → {rhash[:8]}...")
                
            # If user is actively editing, just warn them
            # Remote file changed — check if we have local edits
            if st.session_state.get("buffer_dirty", False):
                # ⚠️ Local edits in progress - User is actively editing — flag remote change, don't merge yet
                st.session_state.remote_change_pending = True
                logger.info("[REMOTE CHANGE] Set remote_change_pending=True (user editing)")
            
            elif st.session_state.get("remote_change_pending", False):
                # Already flagged pending from previous cycle — still waiting for user to save
                logger.debug("[REMOTE CHANGE] Already pending from previous cycle")
                pass
        
            # If user is NOT editing, merge automatically
            else:
                logger.info("[REMOTE MERGE] Merging remote changes (no local edits)")
                # ✅ Safe to merge: no local edits, no pending remote changes
                # Un autre utilisateur a modifié le fichier → merge 3-voies
                try:
                    # Download latest remote version
                    # remote_df represents the CSV downloaded from Dropbox at that moment. It may become outdated immediately after downloading if another recruiter saves a change. It is therefore a snapshot, not a permanently current object.
                    remote_df = _download_current_df_from_dbx(DBX, STATE_DBX_PATH)
                    if remote_df is not None:
                            base_df = st.session_state.get("base_df")
                            ours_df = st.session_state.get("df")
                            
                            if base_df is None or ours_df is None:
                                # No baseline yet, just use remote
                                st.session_state.df = remote_df.copy(deep=True)
                                st.session_state.base_df = remote_df.copy(deep=True)
                                logger.info("[REMOTE MERGE] No baseline - using remote as-is")
                            else:
                                # 3-way merge
                                merged_df, conflicts = _three_way_merge(
                                    base_df,
                                    ours_df,
                                    remote_df,
                                    TEXT_COLS, 
                                    BOOL_COLS,
                                    FIRST, 
                                    LAST, 
                                    FILE
                                )
                                merged_df = _ensure_schema(
                                    merged_df, 
                                    TEXT_COLS, 
                                    BOOL_COLS, 
                                    FIRST, 
                                    LAST, 
                                    FILE
                                )
                                
                                st.session_state.df = merged_df.copy(deep=True)
                                st.session_state.base_df = merged_df.copy(deep=True)
                                logger.info(f"[REMOTE MERGE] Merged {len(merged_df)} rows, {len(conflicts)} conflicts")
                                
                                if conflicts:
                                    st.warning(
                                        f"ℹ️ {len(conflicts)} conflict(s) detected in automatic merge. "
                                        "Resolved in favor of local values."
                                    )
                            
                            # ✅ Update remote hash AFTER successful merge
                            st.session_state.last_seen_hash = rhash
                            st.session_state.last_hash_check = now
                            st.session_state.remote_change_pending = False
                            
                            # ✅ REFRESH THE GRID DISPLAY
                            view_df, filter_key = _compute_view_and_key(
                                st.session_state.df,
                                st.session_state.get("q", ""),
                                FIRST, 
                                LAST
                            )
                            _reset_grid(
                                view_df, 
                                filter_key, 
                                EDITOR_KEY, 
                                GRID_FILTER_KEY, 
                                TEXT_COLS, 
                                BOOL_COLS
                            )
                            
                            logger.info("[REMOTE MERGE] Grid refreshed ✅")
                            st.toast("📥 Data refreshed from Dropbox")
                            
                            # ✅ RERUN TO SHOW THE NEW DATA
                            st.rerun()

                except Exception:
                    logger.error(f"[REMOTE MERGE] Failed to download: {e}", exc_info=True)
                    remote_df = None
                    st.warning(f"⚠️ Failed to merge remote changes: {e}")
                    st.error("Unable to refresh data right now.")


                if remote_df is not None:
                    if st.session_state.get("buffer_dirty", False):
                        # Keep df and base_df unchanged.
                        # Do not acknowledge this remote version yet.
                        st.session_state.remote_change_pending = True
                        logger.info("[REMOTE MERGE] Buffer became dirty, deferring merge")

                    else:
                        base_df = st.session_state.get("base_df")

                        if base_df is None:
                            # No baseline: initialize safely from the downloaded version.
                            st.session_state.df = remote_df.copy(deep=True)
                            st.session_state.base_df = remote_df.copy(deep=True)
                            merged = remote_df.copy(deep=True)
                            conflicts = []

                        else:
                            ours_df = st.session_state.df.copy(deep=True)
                            merged, conflicts = _three_way_merge(
                                base_df,
                                ours_df,
                                remote_df,
                                TEXT_COLS, 
                                BOOL_COLS,
                                FIRST, 
                                LAST, 
                                FILE
                            )

                            merged = _ensure_schema(merged, TEXT_COLS, BOOL_COLS, FIRST, LAST, FILE)

                            st.session_state.df = merged.copy(deep=True)
                            st.session_state.base_df = merged.copy(deep=True)

                        # Acknowledge only after applying the remote snapshot.
                        st.session_state["last_seen_hash"] = rhash
                        st.session_state.remote_change_pending = False
                        logger.info(f"[REMOTE MERGE] Merge complete, conflicts={len(conflicts)}")


                        if conflicts:
                            st.warning(
                                f"{len(conflicts)} conflict(s) detected while "
                                "merging remote changes."
                            )

                        view_df, filter_key = _compute_view_and_key(
                            st.session_state.df,
                            st.session_state.q,
                            FIRST, 
                            LAST
                        )
                        _reset_grid(view_df, filter_key, EDITOR_KEY, GRID_FILTER_KEY, TEXT_COLS, BOOL_COLS)

                        st.toast("Data refreshed from Dropbox.")

        
        else:
            # Hash matches, everything in sync
            st.session_state.last_hash_check = now
            logger.debug("[HASH CHECK] Hashes match - in sync ✅")
                
    # ✅ SHOW WARNING HERE (in main script, not fragment)
    if st.session_state.get("remote_change_pending", False) and st.session_state.get("buffer_dirty", False):
        st.warning(
            "⚠️ Another recruiter made changes while you were editing. "
            "After you save, their changes will be merged in."
        )
        
st.divider()

# ============================================================
# RECRUITER OVERVIEW - CANDIDATES TRACKING
# ============================================================
st.markdown(
    '<div class="section-title">📊 Candidates tracking</div>',
    unsafe_allow_html=True,
)

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
    # Clear the selected candidate when filter changes
    st.session_state.candidate_search = None
    active_editor_key = st.session_state.get("_active_editor_key")
    editor_state = st.session_state.get(active_editor_key, {})
    edited_rows = editor_state.get("edited_rows", {})

    if edited_rows:
        editor_df = st.session_state.get("overview_grid_df", pd.DataFrame(columns=[*TEXT_COLS, *BOOL_COLS]))

        if editor_df is not None and not editor_df.empty:
            delta = _build_delta_from_editor(
                editor_df,
                edited_rows,
                TEXT_COLS,
                BOOL_COLS
            )

            if not delta.empty:
                st.session_state.df = _apply_optimistic(
                    st.session_state.df,
                    delta,
                    TEXT_COLS, 
                    BOOL_COLS, 
                    FIRST, 
                    LAST, 
                    FILE
                )
                _mark_dirty()

    # The edits are now in df; clear the old widget state.
    st.session_state.pop(active_editor_key, None)
    st.session_state.pop("_active_editor_key", None)
    st.session_state.pop("editor_logged_changes", None)

st.selectbox(
    "Filter candidates",
    options=filter_options,
    key="status_filter",
    on_change=on_status_filter_change,
)