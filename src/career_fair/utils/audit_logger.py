# Structured activity events and authentication logs.
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import csv
import streamlit as st

# =====================================
# ------------- LOGGING ---------------
# =====================================

UTC = timezone.utc
PARIS_TZ = ZoneInfo("Europe/Paris")

def timestamp_utc() -> str:
    """ISO 8601 timestamp for logs and persistent storage."""
    return datetime.now(UTC).isoformat(timespec="seconds")

def timestamp_paris() -> str:
    """Return the current Paris time as an ISO 8601 timestamp."""
    return datetime.now(PARIS_TZ).isoformat(timespec="seconds")

# Track authentication events (auth_log_*.csv)
def log_auth_event(auth_log: str, username: str, event: str, success: bool) -> None:
    """Log authentication-related events locally."""

    file_exists = auth_log.exists()

    with auth_log.open("a", newline="", encoding="utf-8") as f:
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

# Track user activity (activity_log_*.csv) 
def log_activity(
    activity_log_path : str,
    event: str,
    candidate_id: str | None = None,
) -> None:
    """Log recruiter activity locally."""

    username = st.session_state.get("username", "")
    recruiter_id = st.session_state.get("recruiter_id", "")

    file_exists = activity_log_path.exists()

    with activity_log_path.open("a", newline="", encoding="utf-8") as f:
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
