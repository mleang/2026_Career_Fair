import streamlit as st
import dropbox
import requests
import io
import time
import pandas as pd
from pathlib import Path, PurePosixPath
import base64
import logging

from src.career_fair.storage.merge_handler import _ensure_schema
from src.career_fair.utils.logging_config import setup_logging

logger = setup_logging("dropbox_storage")

# _access_token_from_refresh()	Obtain a Dropbox access token using the refresh token.
# _build_dbx_client()	Create an authenticated Dropbox client.
# _refresh_dbx_client()	Refresh or recreate the client when necessary.
# _ensure_folder_tree()	Ensure required Dropbox folders exist.
# _download_current_df_from_dbx()	Download the latest CSV and load it into a DataFrame.
# _upload_with_auto_refresh()	Upload a file, handling token refresh if necessary.
# _get_remote_hash()	Retrieve or calculate a remote file hash to detect changes.
# load_data() (optional public wrapper)	Provide the app with the current DataFrame and its revision/hash.
# save_data() (optional public wrapper)	Provide a simple interface for persisting data, delegating reconciliation to merge.py.

# Dropbox client, fresh downloads,
# revisions, conditional writes, and retries.
# ============================================================
# DROPBOX TIMEOUTS
# ============================================================
DROPBOX_TIMEOUT_SEC = 5  # Global timeout for all Dropbox API calls

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

    try:
        response = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "refresh_token": secrets["DROPBOX_REFRESH_TOKEN"],
                "grant_type": "refresh_token",
                "client_id": secrets["DROPBOX_APP_KEY"],
                "client_secret": secrets["DROPBOX_APP_SECRET"],
            },
            timeout=15, # Token refresh timeout
        )

        response.raise_for_status()

        return response.json()["access_token"]
 
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(
            "Token refresh timed out. Dropbox auth server is slow."
        ) from exc
    
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"Failed to refresh token: {exc}"
        ) from exc


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

    return dropbox.Dropbox(token, timeout=DROPBOX_TIMEOUT_SEC)


def _refresh_dbx_client() -> dropbox.Dropbox:
    """Refresh the Dropbox client."""

    token = _access_token_from_refresh()

    if token:
        return dropbox.Dropbox(token, timeout=DROPBOX_TIMEOUT_SEC)

    return _build_dbx_client()


def initialize_dropbox() -> tuple[dropbox.Dropbox | None, str | None]:
    """Initialize Dropbox and return (client, error)."""

    try:
        return _build_dbx_client(), None
    except Exception as exc:
        return None, str(exc)
    

# Dropbox I/O helpers (read fresh / write overwrite with auto-refresh)
def _ensure_folder_tree(dbx: dropbox.Dropbox, path: str):
    from posixpath import dirname
    parent = dirname(path.rstrip("/"))
    if parent and parent != "/":
        try:
            dbx.files_create_folder_v2(parent)
        except dropbox.exceptions.ApiError:
            pass


# @st.cache_data(show_spinner=False, ttl=1)
def _download_current_df_from_dbx(dbx: dropbox.Dropbox, state_dbx_path: str) -> pd.DataFrame:
    """Fresh read from Dropbox API to avoid CDN cache."""
    #global dbx
    
    if dbx is None:
        raise RuntimeError("Dropbox not configured")
    try:
        _, resp = dbx.files_download(state_dbx_path)
    except dropbox.exceptions.AuthError:
        dbx = _refresh_dbx_client()
        _, resp = dbx.files_download(state_dbx_path)
    
    try:
        # ✅ ADD: on_bad_lines='skip' to skip corrupted rows
        df = pd.read_csv(
            io.BytesIO(resp.content),
            skip_blank_lines=True,
            on_bad_lines='skip',  # Skip rows with parsing errors
            engine='python',       # Use python engine for on_bad_lines
        )
        return df
    except pd.errors.ParserError as exc:
        raise RuntimeError(f"Failed to parse CSV: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected error reading CSV: {exc}") from exc


def _upload_with_auto_refresh(dbx: dropbox.Dropbox, data: bytes, path: str) -> tuple[bool, str | None]:
    # global dbx
    try:
        _ensure_folder_tree(dbx, path)
        dbx.files_upload(data, path=path, mode=dropbox.files.WriteMode("overwrite"))
        return (True, None)
    except dropbox.exceptions.AuthError:
        try:
            dbx = _refresh_dbx_client()
            _ensure_folder_tree(dbx, path)
            dbx.files_upload(data, path=path, mode=dropbox.files.WriteMode("overwrite"))
            return (True, None)
        except Exception as e2:
            return (False, f"AuthError after refresh: {e2}") # TODO:  avoid exposing raw Dropbox API errors ?
    except dropbox.exceptions.ApiError as e:
        return (False, f"ApiError: {e}") # TODO:  avoid exposing raw Dropbox API errors ? 
    except Exception as e:
        return (False, f"Write error: {e}") # TODO:  avoid exposing raw Dropbox API errors ?

def _dbx_call(fn, dbx: dropbox.Dropbox, retries: int = 3):
    # global dbx
    last = None
    for attempt in range(retries):
        try:
            return fn(dbx)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last = e
            time.sleep(0.5 * (attempt + 1))
            dbx = _refresh_dbx_client()
        except dropbox.exceptions.AuthError as e:
            last = e
            dbx = _refresh_dbx_client()
    raise last


# -------- Remote hash (léger) pour auto-refresh --------
@st.cache_data(show_spinner=False, ttl=1)
def _get_remote_hash(_dbx: dropbox.Dropbox, state_dbx_path: str) -> str | None:
    """Retourne content_hash du fichier côté Dropbox (sans télécharger le CSV)."""
    if _dbx is None:
        return None
    try:
        # Apply timeout to the Dropbox API call
        md = _dbx.files_get_metadata(state_dbx_path)
        # md peut être FileMetadata avec .content_hash
        if hasattr(md, "content_hash"):
            return md.content_hash
    except requests.exceptions.Timeout:
        st.warning("⏱️ Remote check timed out. Using cached data.")
        return None
    except dropbox.exceptions.AuthError:
        return None
    except Exception:
        return None
    return None



# ---------------- CV helpers ----------------
def _validate_cv_path(path: str, cv_root: str) -> str:
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
    if not normalized.startswith(cv_root + "/"):
        raise ValueError("CV path outside allowed directory.")

    return normalized

# def get_cv_pdf_base64(path: str, cv_root: str) -> str:
#     """Download a private PDF from Dropbox and return its base64 content."""
#     path = _validate_cv_path(path, cv_root)

#     _, response = _dbx_call(
#         lambda d: d.files_download(path)
#     )

#     pdf_bytes = response.content

#     if not pdf_bytes.startswith(b"%PDF"):
#         raise ValueError("Downloaded file is not a valid PDF.")

#     return base64.b64encode(pdf_bytes).decode("ascii")

def get_cv_pdf_base64(dbx: dropbox.Dropbox, cv_path: str) -> str:
    """Download a CV from Dropbox and return as base64-encoded string.

    Args:
        dbx: Initialized Dropbox client.
        cv_path: Dropbox path of the selected candidate's PDF.

    Returns:
        Base64-encoded PDF content as a UTF-8 string.

    Raises:
        ValueError: If the Dropbox client or CV path is invalid.
        RuntimeError: If Dropbox download or base64 encoding fails.
    """
    
    if not dbx:
        raise ValueError("Dropbox client not initialized")
    
    # Validate type before calling string methods
    if not isinstance(cv_path, str) or not cv_path.strip():
        raise ValueError(f"Invalid CV path: {cv_path}")
    
    # Remove accidental leading/trailing whitespace before passing the path to Dropbox.
    cv_path = cv_path.strip()
    
    try:
        print(f"DEBUG: Downloading CV from: {cv_path}")
        _, resp = dbx.files_download(cv_path)
        pdf_bytes = resp.content
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        print(f"DEBUG: Successfully downloaded {len(pdf_bytes)} bytes")
        return pdf_b64
    except dropbox.exceptions.ApiError as e:
        print(f"DEBUG: Dropbox API error: {e}")
        raise RuntimeError(f"Failed to download CV from {cv_path}: {e}")
    except Exception as e:
        print(f"DEBUG: Unexpected error: {e}")
        raise RuntimeError(f"Error encoding CV: {e}")