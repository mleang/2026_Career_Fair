import io
import time
import unicodedata
import dropbox
import pandas as pd
import streamlit as st
import logging
import requests
import json

from audit_logger import log_activity

# _ensure_schema()	Ensure required columns and data types are present.
# _norm()	Normalize values before comparing them, such as handling missing values.
# _key()	Generate a stable key identifying a candidate or row.
# _index_by_key()	Index rows by their stable keys for comparison and merging.
# _snapshot_base()	Preserve the original data snapshot used as the merge baseline.
# _three_way_merge(base, ours, theirs)	Compare the original snapshot, local edits, and latest remote data.
# _apply_optimistic()	Apply local edits optimistically to the working DataFrame.
# _build_delta_from_editor()	Extract changes made in the Streamlit editor relative to the baseline.
# _flush_to_disk()	Coordinate saving: fetch latest data, merge edits, and persist the reconciled result.

from logging_config import setup_logging
logger = setup_logging("merge_handler")
# ---------------- Helpers ----------------
def _norm(value: str) -> str:
    """Normalize text for search/comparison."""
    if value is None or pd.isna(value):
        return ""
    value = str(value).strip()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = " ".join(value.lower().split())
    return value

def _ensure_schema(df: pd.DataFrame, text_cols: list, bool_cols: list, first: str, last: str, file: str) -> pd.DataFrame:
    df = df.copy() # .copy(deep=True)  # Avoid modifying the original DataFrame
    for c in text_cols:
        if c not in df:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str)

    for c in bool_cols:
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
        df[first].fillna("").astype(str).str.strip().ne("")
        | df[last].fillna("").astype(str).str.strip().ne("")
        | df[file].fillna("").astype(str).str.strip().ne("")
    )

    return df.loc[valid, [*text_cols, *bool_cols]].copy()


# ---------------- Keys & snapshots ----------------

def _key(df_like: pd.DataFrame, first: str, last: str, file: str) -> pd.Series:
    """Generate collision-resistant candidate keys.

    Names:
        - case-insensitive
        - accent-insensitive
        - whitespace-normalized

    File paths:
        - leading/trailing whitespace stripped
        - case-sensitive

    JSON serialization prevents collisions between fields containing
    the separator used by the previous implementation.
    """
    # Previous implementation used a simple string concatenation with "||" as a separator.
    # return (
    #     df_like[first].fillna("").astype(str).str.strip().str.lower() + "||" +
    #     df_like[last].fillna("").astype(str).str.strip().str.lower() + "||" +
    #     df_like[file].fillna("").astype(str).str.strip()
    # )

    first_names = df_like[first].map(_norm)
    last_names = df_like[last].map(_norm)
    file_paths = df_like[file].fillna("").astype(str).str.strip()

    keys = [
        json.dumps(
            [first_name, last_name, file_path],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for first_name, last_name, file_path in zip(
            first_names,
            last_names,
            file_paths,
        )
    ]

    return pd.Series(keys, index=df_like.index)


# Keep an immutable snapshot to compute deltas (base)
def _snapshot_base(text_cols: list, bool_cols: list, first: str, last: str, file: str) -> None:
    st.session_state.base_df = _ensure_schema(st.session_state.df, text_cols, bool_cols, first, last, file).copy(deep=True)

# ---------------- Merge (3-way) ----------------
def _index_by_key(df: pd.DataFrame, text_cols: list, bool_cols: list, first: str, last: str, file: str) -> pd.DataFrame:
    d = _ensure_schema(df, text_cols, bool_cols, first, last, file).copy()
    d["_k"] = _key(d, first, last, file)
    return d.set_index("_k")

def _three_way_merge(base: pd.DataFrame, ours: pd.DataFrame, theirs: pd.DataFrame, text_cols: list, bool_cols: list, first: str, last: str, file: str) -> tuple[pd.DataFrame, list]:
    """Retourne (merged_df, conflicts_list). Booleans: OR sur double modif. Text: ours gagne si double modif."""
    B, O, T = (_index_by_key(base, text_cols, bool_cols, first, last, file), _index_by_key(ours, text_cols, bool_cols, first, last, file), _index_by_key(theirs, text_cols, bool_cols, first, last, file))
    all_idx = B.index.union(O.index).union(T.index)
    B, O, T = B.reindex(all_idx), O.reindex(all_idx), T.reindex(all_idx)

    out = T[[*text_cols, *bool_cols]].copy() # base "theirs"
    conflicts = []

    # TEXT COLS
    for c in text_cols:
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
    for c in bool_cols:
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
def _apply_optimistic(full_df: pd.DataFrame, delta_rows: pd.DataFrame, text_cols: list, bool_cols: list, first: str, last: str, file: str) -> pd.DataFrame:
    if delta_rows.empty:
        return full_df
    full = full_df.copy()
    full["_k"] = _key(full, first, last, file)
    tmp = delta_rows.copy(); tmp["_k"] = _key(tmp, first, last, file)
    cols = ["_k"] + [c for c in (text_cols + bool_cols) if c in tmp.columns]
    full = full.merge(tmp[cols], on="_k", how="left", suffixes=("", "_new"))
    for c in text_cols + bool_cols:
        newc = f"{c}_new"
        if newc in full:
            if c in bool_cols:
                full[c] = full[newc].combine_first(full[c]).astype(bool)
            else:
                full[c] = full[newc].combine_first(full[c]).astype(str)
            full.drop(columns=[newc], inplace=True)
    full.drop(columns=["_k"], inplace=True)
    return full

# ---------------- Editor delta build ----------------
def _build_delta_from_editor(grid_df: pd.DataFrame, edited_rows: dict, text_cols: list, bool_cols: list) -> pd.DataFrame:
    expected_cols = [*text_cols, *bool_cols]
    if not edited_rows:
        return pd.DataFrame(columns=expected_cols)

    out = []
    for i, changes in edited_rows.items():
        i = int(i)
        if 0 <= i < len(grid_df):
            base = grid_df.iloc[i][text_cols].to_dict()
            dirty = False
            for col, val in changes.items():
                if col in bool_cols:
                    base[col] = bool(val); dirty = True
                elif col in text_cols:
                    base[col] = str(val); dirty = True
            if dirty:
                out.append(base)

    if not out:
        return pd.DataFrame(columns=expected_cols)

    df = pd.DataFrame(out)
    for c in bool_cols:
        if c not in df.columns:
            df[c] = pd.NA
    for c in text_cols:
        if c not in df.columns:
            df[c] = pd.NA
    return df.reindex(columns=expected_cols)

# ---------------- Compute view & key ----------------
def _compute_view_and_key(base_df: pd.DataFrame, query: str, first_name: str, last_name: str) -> tuple[pd.DataFrame, str]:
    query_norm = _norm(query)
    if query_norm:
        tokens = [t for t in query_norm.split() if t.strip()]
        mask = pd.Series(True, index=base_df.index)
        if "_full" not in base_df:
            tmp_full = (base_df[first_name].fillna("").astype(str) + " " + base_df[last_name].fillna("").astype(str)).map(_norm)
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
def _reset_grid(view_df: pd.DataFrame, filter_key: str, editor_key: str, grid_filter_key: str, text_cols: list, bool_cols: list) -> None:
    active_editor_key = _active_editor_key(editor_key)

    # Remove the current editor's widget state.
    if active_editor_key:
        st.session_state.pop(active_editor_key, None)

    st.session_state.pop("_active_editor_key", None)
    st.session_state.pop("editor_logged_changes", None)

    # Store the filter represented by the new grid.
    st.session_state[grid_filter_key] = filter_key

    # Store the snapshot used to interpret edits from the new editor.
    st.session_state.overview_grid_df = (
        view_df[[*text_cols, *bool_cols]].copy(deep=True)
    )

    # Force Streamlit to create a new editor instance.
    st.session_state.editor_generation = (
        st.session_state.get("editor_generation", 0) + 1
    )

# ---------------- Editor changes handler ---------------
def _active_editor_key(editor_key: str) -> str:
    return st.session_state.get(
        "_active_editor_key",
        f"{editor_key}_{st.session_state.get('editor_generation', 0)}",
    )

def _mark_dirty() -> None:
    st.session_state.buffer_dirty = True
    st.session_state.last_edit_at = time.time()
    st.session_state.save_status = "Pending"
    st.session_state.save_error = None



def _handle_editor_change(editor_key: str, activity_log_path: str, text_cols: list, bool_cols: list, seen: str, interest: str, save: str, cont: str, first: str, last: str, file: str) -> None:
    """Apply editor changes to the working DataFrame and log checkbox changes."""
    # Read pending edits from the active editor's state to get the delta : edited_rows
    
    active_editor_key = _active_editor_key(editor_key)
    if not active_editor_key:
        return
    
    editor_state = st.session_state.get(active_editor_key, {})
    edited_rows = editor_state.get("edited_rows", {})

    grid_df = st.session_state.get("overview_grid_df")
    if not edited_rows or grid_df is None:
        return


    # Apply the delta to the working DataFrame and mark it dirty.
    # Use overview_grid_df to map row indices to candidates.
    delta = _build_delta_from_editor(grid_df, edited_rows, text_cols, bool_cols)
    if delta.empty:
        return

    # Update the working DataFrame optimistically with the delta.
    st.session_state.df = _apply_optimistic(
        st.session_state.df,
        delta,
        text_cols, 
        bool_cols, 
        first, 
        last, 
        file
    )
    
    # Mark the buffer as dirty to indicate that there are unsaved changes.
    _mark_dirty()

    # Log checkbox changes for analytics and activity tracking.
    logged_changes = st.session_state.setdefault(
        "editor_logged_changes", {}
    )

    for row_idx, changes in edited_rows.items():
        try:
            row_idx = int(row_idx)
        except (ValueError, TypeError):
            continue

        if not 0 <= row_idx < len(grid_df):
            continue

        row = grid_df.iloc[row_idx]
        candidate_id = _key(pd.DataFrame([row]), first, last, file).iloc[0]

        for col, new_value in changes.items():
            if col not in bool_cols:
                continue

            # Accept only actual Boolean values (including NumPy bools).
            if not isinstance(new_value, (bool,)):
                # Handle numpy.bool_ without accepting strings or integers.
                if type(new_value).__name__ != "bool_":
                    continue
                new_value = bool(new_value)

            new_value = bool(new_value)
            change_key = f"{candidate_id}::{col}"

            # The initial state is the value shown before the edit.
            previous_value = logged_changes.get(
                change_key, bool(row[col])
            )
            if previous_value == new_value:
                continue
                
            events = {
                seen: {
                    True: "candidate_viewed",
                    False: "candidate_unviewed",
                },
                interest: {
                    True: "candidate_interested",
                    False: "candidate_uninterested",
                },
                save: {
                    True: "candidate_saved",
                    False: "candidate_unsaved",
                },
                cont: {
                    True: "candidate_contacted",
                    False: "candidate_uncontacted",
                },
            }

            event = events.get(col, {}).get(new_value)
            if event:
                log_activity(activity_log_path=activity_log_path, event=event, candidate_id=candidate_id)

            logged_changes[change_key] = new_value


def _collect_pending_editor_changes(editor_key: str, text_cols: list, bool_cols: list, first: str, last: str, file: str) -> None:
    """Capture any editor deltas not yet reflected in the working DataFrame."""
    editor_state = st.session_state.get(_active_editor_key(editor_key), {})
    edited_rows = editor_state.get("edited_rows", {})

    if not edited_rows:
        return

    grid_df = st.session_state.get("overview_grid_df")
    if grid_df is None:
        raise RuntimeError("The overview editor's source data is missing.")

    delta = _build_delta_from_editor(grid_df, edited_rows, text_cols, bool_cols)
    if not delta.empty:
        st.session_state.df = _apply_optimistic(
            st.session_state.df, delta, text_cols, bool_cols, first, last, file
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
    dbx: dropbox.Dropbox,
    state_dbx_path: str,
    text_cols: list, 
    bool_cols: list, 
    first: str, 
    last: str, 
    file: str,
    ok_text="Changes saved.",
    err_text="Could not save changes.",
    max_attempts=4,    
):
    """Save local changes using a three-way merge and conditional Dropbox write."""

    if not st.session_state.get("buffer_dirty", False):
        return True
    

    start_time = time.time()  # Track save duration
    base_df = st.session_state.get("base_df")
    if base_df is None:
        st.error("Cannot save: no confirmed baseline is available.")
        return False

    base_df = _ensure_schema(base_df.copy(deep=True), text_cols, bool_cols, first, last, file)
    ours_df = _ensure_schema(st.session_state.df.copy(deep=True), text_cols, bool_cols, first, last, file)

    for attempt in range(1, max_attempts + 1):
        try:
            remote_df, remote_rev = _download_latest_for_save(dbx, state_dbx_path, text_cols, bool_cols, first, last, file)

            merged_df, conflicts = _three_way_merge(
                base_df,
                ours_df,
                remote_df,
                text_cols, 
                bool_cols, 
                first, 
                last, 
                file
            )
            merged_df = _ensure_schema(merged_df, text_cols, bool_cols, first, last, file)

            if conflicts:
                st.warning(
                    f"{len(conflicts)} merge conflict(s) detected. "
                    "Conflicting text fields were resolved in favor of "
                    "the local values; checkbox values were merged."
                )

            save_df = merged_df.copy()
            for col in bool_cols:
                save_df[col] = save_df[col].fillna(False).astype(bool)

            buffer = io.BytesIO()
            save_df.to_csv(buffer, index=False, encoding="utf-8")

            try:
                uploaded_metadata = _upload_if_revision_matches(
                    buffer.getvalue(),
                    remote_rev,
                    dbx, 
                    state_dbx_path
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
            
            # Update remote hash
            # st.session_state.last_seen_hash = getattr(
            #     uploaded_metadata, "content_hash", None
            # )
            # ✅ CRITICAL: Update hash IMMEDIATELY after successful upload
            # This prevents the warning from showing again on next check
            new_hash = getattr(uploaded_metadata, "content_hash", None)
            st.session_state.last_seen_hash = new_hash
            st.session_state.last_hash_check = time.time()
            
            logger.info(f"Updated hash after save: {new_hash[:8] if new_hash else 'None'}...")
            logger.info(f"Updated last_hash_check to {time.time()}")
            
            # ✅ CLEAR THE FLAG
            st.session_state.remote_change_pending = False
            logger.info(f"[SAVE SUCCESS] Updated hash = {new_hash[:16] if new_hash else 'None'}")
            logger.info(f"[SAVE SUCCESS] Cleared remote_change_pending")
        
            # Force a fresh editor widget on the next rerun.
            st.session_state.editor_generation = (
                st.session_state.get("editor_generation", 0) + 1
            )
            st.session_state.pop("editor_logged_changes", None)

            
            # Show save time
            save_duration = time.time() - start_time
            st.toast(f"{ok_text} ({save_duration:.1f}s)")
            
            # ✅ RERUN TO APPLY THE UPDATED HASH
            logger.info(f"Successfully saved to {state_dbx_path}")
            st.rerun()

            return True

        except Exception as exc:
            # Never discard the local version after a failed save.
            st.session_state.df = ours_df.copy(deep=True)
            st.session_state.buffer_dirty = True
            #st.error(f"{err_text} {exc}")
            save_duration = time.time() - start_time
            st.error(f"{err_text} (failed after {save_duration:.1f}s): {exc}")
            return False

    return False




# Download the latest remote DataFrame and revision for save operations.
def _download_latest_for_save(dbx: dropbox.Dropbox, state_dbx_path: str, text_cols: list, bool_cols: list, first: str, last: str, file: str) -> tuple[pd.DataFrame, str]:
    """
    Return the latest remote DataFrame and the revision obtained
    from the same Dropbox download operation.

    Do not decorate this function with st.cache_data.
    
    Args:
        dbx: Dropbox client
        state_dbx_path: Path to CSV file in Dropbox
        text_cols, bool_cols: Column schemas
        timeout: API call timeout in seconds
        
    Returns:
        (remote_df, revision_hash)
        
    Raises:
        RuntimeError: If download or parse fails
    """
    #global DBX

    if dbx is None:
        raise RuntimeError("Dropbox client is not initialized.")

    try:
        logger.debug(f"Downloading latest from {state_dbx_path}")
        metadata, response = dbx.files_download(state_dbx_path)
    
    except requests.exceptions.Timeout as exc:
        logger.warning(f"Download timeout after 10 s")
        raise RuntimeError(
            f"Download timed out after 10s. "
            "Dropbox server is slow. Retrying..."
        ) from exc

    except dropbox.exceptions.AuthError as exc:
        logger.error(f"Auth failed downloading {state_dbx_path}: {exc}")
        #DBX = _refresh_dbx_client()
        #metadata, response = dbx.files_download(state_dbx_path)
        #raise RuntimeError("Dropbox authentication failed. Please check your credentials.")
        raise RuntimeError(
            "Dropbox authentication failed. "
            "Please re-authenticate and try again."
        ) from exc
    
    except dropbox.exceptions.ApiError as exc:
        error_msg = str(exc).lower()
        logger.error(f"API error downloading {state_dbx_path}: {exc}")
        
        if "not_found" in error_msg:
            raise RuntimeError(
                f"CSV file not found at {state_dbx_path}. "
                "Check that the path is correct."
            ) from exc
        
        elif "permission_denied" in error_msg:
            raise RuntimeError(
                "Permission denied: unable to access the CSV file. "
                "Check Dropbox sharing settings."
            ) from exc
        
        else:
            raise RuntimeError(f"Dropbox API error: {exc}") from exc
    
    except Exception as exc:
        logger.error(f"Unexpected error downloading {state_dbx_path}: {exc}", exc_info=True)
        raise RuntimeError(f"Unexpected error downloading CSV: {exc}") from exc
    
    # Parse CSV with robust error handling
    try:
        # ✅ on_bad_lines='skip' to handle corrupted rows
        remote_df = pd.read_csv(
            io.BytesIO(response.content),
            skip_blank_lines=True,
            on_bad_lines='skip',  # Skip rows with parsing errors
            engine='python',       # Use python engine for on_bad_lines
        )
        if remote_df.empty:
            logger.warning(f"Remote CSV at {state_dbx_path} is empty")
    except pd.errors.ParserError as exc:
        logger.error(f"CSV parse error in {state_dbx_path}: {exc}")
        raise RuntimeError(f"Failed to parse remote CSV: {exc}") from exc
    except Exception as exc:
        logger.error(f"Unexpected error reading CSV: {exc}", exc_info=True)
        raise RuntimeError(f"Unexpected error reading remote CSV: {exc}") from exc

    remote_df = _ensure_schema(remote_df, text_cols, bool_cols, first, last, file)

    return remote_df, metadata.rev

#------------------------------------------------
# Upload only if the revision is still current
#------------------------------------------------
def _upload_if_revision_matches(csv_bytes: bytes, expected_rev: str, dbx: dropbox.Dropbox, state_dbx_path: str) -> dropbox.files.FileMetadata:
    """
    Upload only if the Dropbox file still has expected_rev.

    Returns the metadata of the committed file.
    Never falls back to an unconditional overwrite.
    """
    # global dbx

    if dbx is None:
        raise RuntimeError("Dropbox client is not initialized.")

    try:
        logger.debug(f"Uploading {len(csv_bytes)} bytes to {state_dbx_path} (expected rev: {expected_rev[:8]}...)")
        
        return dbx.files_upload(
            csv_bytes,
            state_dbx_path,
            mode=dropbox.files.WriteMode.update(expected_rev),
            mute=True,
        )

    except requests.exceptions.Timeout as exc:
        # Network timeout
        logger.error(f"Upload timeout after 10s: {exc}")
        raise RuntimeError(
            f"Upload timed out after 10s. "
            "Dropbox server is slow or unreachable. "
            "Your changes are retained; please try again."
        ) from exc

    except dropbox.exceptions.AuthError as exc:
        # Authentication failure
        logger.error(f"Auth failed during upload: {exc}")
        raise RuntimeError(
            "Dropbox authentication failed. "
            "Please re-authenticate and try again."
        ) from exc

    except dropbox.exceptions.ApiError as exc:
        # Other API errors (including revision conflicts, permission denied, etc.)
        error_msg = str(exc).lower()
        logger.error(f"Dropbox API error during upload: {exc}")
        
        if "conflict" in error_msg or "incorrect_revision" in error_msg or "revision mismatch" in error_msg:
            # Revision conflict — caller will retry with fresh data
            logger.debug("Revision conflict detected; will retry with latest remote version")
            raise  # ← Re-raise for caller to handle retry
        
        elif "permission_denied" in error_msg:
            raise RuntimeError(
                "Permission denied: unable to write to the CSV file. "
                "Check Dropbox sharing settings."
            ) from exc
        
        elif "path_disallowed" in error_msg:
            raise RuntimeError(
                f"Path not allowed: {state_dbx_path} "
                "Check Dropbox app permissions."
            ) from exc
        
        elif "rate_limit" in error_msg:
            raise RuntimeError(
                "Rate limited by Dropbox. "
                "Please try again in a few moments."
            ) from exc
        
        else:
            # Generic API error
            raise RuntimeError(
                f"Dropbox API error during upload: {exc}"
            ) from exc

    except Exception as exc:
        # Unexpected errors
        logger.error(f"Unexpected error during upload: {exc}", exc_info=True)
        raise RuntimeError(
            f"Unexpected error uploading to Dropbox: {exc}"
        ) from exc
    


def _refresh_from_remote(
    remote_df: pd.DataFrame,
    text_cols: list,
    bool_cols: list,
    first: str,
    last: str,
    file: str,
) -> tuple[pd.DataFrame, list]:
    """Reconcile a downloaded remote snapshot with the current session.

    If the session has no baseline, initialize from the remote snapshot.

    If there are no unsaved local changes, adopt the remote snapshot as
    both the working DataFrame and the merge baseline.

    If local changes are pending, merge them with the remote snapshot
    against the existing baseline. Preserve that original baseline and
    keep the dirty flag set so a later save can perform its own fresh
    download-and-merge cycle.

    This function does not download from Dropbox, update remote hashes,
    save the CSV, or trigger a Streamlit rerun. Those side effects are
    deliberately kept in the caller.

    Returns:
        (working_dataframe, conflicts)
    """
    remote = _ensure_schema(
        remote_df.copy(deep=True),
        text_cols, bool_cols, first, last, file,
    )

    base = st.session_state.get("base_df")
    ours = st.session_state.get("df")
    dirty = st.session_state.get("buffer_dirty", False)

    if base is None or ours is None:
        working = remote.copy(deep=True)
        st.session_state.df = working.copy(deep=True)
        st.session_state.base_df = remote.copy(deep=True)
        st.session_state.buffer_dirty = False
        return working, []

    base = _ensure_schema(
        base.copy(deep=True),
        text_cols, bool_cols, first, last, file,
    )
    ours = _ensure_schema(
        ours.copy(deep=True),
        text_cols, bool_cols, first, last, file,
    )

    merged, conflicts = _three_way_merge(
        base, ours, remote,
        text_cols, bool_cols, first, last, file,
    )
    merged = _ensure_schema(
        merged, text_cols, bool_cols, first, last, file,
    )

    st.session_state.df = merged.copy(deep=True)

    if dirty:
        # Keep the original baseline: the local edits are not yet saved.
        st.session_state.base_df = base.copy(deep=True)
        st.session_state.buffer_dirty = True
    else:
        # No local edits: the downloaded snapshot is now the baseline.
        st.session_state.base_df = remote.copy(deep=True)
        st.session_state.buffer_dirty = False

    return merged, conflicts
