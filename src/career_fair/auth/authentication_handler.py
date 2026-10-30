import streamlit as st
from argon2 import PasswordHasher
from argon2.exceptions import (
    VerifyMismatchError,
    VerificationError,
    InvalidHashError,
)

from src.career_fair.utils.audit_logger import log_auth_event

# =====================================
# ------- AUTHENTICATION --------------
# =====================================
PASSWORD_HASHER = PasswordHasher()

# def authenticate(username: str, password: str, allowed_username: str, auth_log_path: str) -> str | None:
#     """Authenticate the recruiter assigned to this app and return recruiter_id.
    
#     Returns:
#         The recruiter's ID on success, or None on failure.
#     """    

#     # Reject missing or malformed credentials safely.
#     username = username if isinstance(username, str) else ""
#     password = password if isinstance(password, str) else ""

#     allowed_username = (
#             allowed_username
#             if isinstance(allowed_username, str)
#             else ""
#         )
    
#     # Keep the submitted username unchanged for exact comparison and logging.
#     log_username = username if username.strip() else "unknown"

#     try:
#         # Reject missing or whitespace-only usernames and empty passwords.
#         # Do not strip the password: whitespace may be intentional.
#         if not username.strip() or not password:
#             raise ValueError("Missing credentials")

#         # Username matching is deliberately case-sensitive.
#         if username != allowed_username:
#             raise ValueError("Unauthorized recruiter")

#         # Retrieve the configured recruiter's credentials.
#         recruiter = st.secrets["recruiters"][username]
#         password_hash = recruiter["password_hash"]
#         recruiter_id = recruiter["recruiter_id"]

#         # Fail closed if the configuration is incomplete or malformed.
#         if not isinstance(password_hash, str) or not password_hash:
#             raise ValueError("Invalid password configuration")

#         if not isinstance(recruiter_id, str) or not recruiter_id:
#             raise ValueError("Invalid recruiter configuration")

#         # Verify the submitted password against its Argon2 hash.
#         PASSWORD_HASHER.verify(password_hash, password)

#         log_auth_event(
#             auth_log_path,
#             username=log_username,
#             event="login_success",
#             success=True,
#         )
#         return recruiter_id

#     except (
#         KeyError,
#         TypeError,
#         ValueError,
#         VerifyMismatchError,
#         VerificationError,
#         InvalidHashError,
#     ):
#         log_auth_event(
#             auth_log_path,
#             username=log_username,
#             event="login_failure",
#             success=False,
#         )
#         return None

def authenticate(
    username: str,
    password: str,
    allowed_username: str,
    auth_log_path: str,
) -> str | None:
    """Return the recruiter ID only if authentication and audit logging succeed."""

    username = username if isinstance(username, str) else ""
    password = password if isinstance(password, str) else ""
    allowed_username = (
        allowed_username if isinstance(allowed_username, str) else ""
    )
    log_username = username if username.strip() else "unknown"

    # Validate input and authenticate.
    try:
        if not username.strip() or not password:
            raise ValueError("Missing credentials")

        if username != allowed_username:
            raise ValueError("Unauthorized username")

        recruiter = st.secrets["recruiters"][username]
        password_hash = recruiter["password_hash"]
        recruiter_id = recruiter["recruiter_id"]

        if not isinstance(password_hash, str) or not password_hash:
            raise ValueError("Invalid password configuration")

        if not isinstance(recruiter_id, str) or not recruiter_id:
            raise ValueError("Invalid recruiter configuration")

        PASSWORD_HASHER.verify(password_hash, password)

    except (
        KeyError,
        TypeError,
        ValueError,
        VerifyMismatchError,
        VerificationError,
        InvalidHashError,
    ):
        # Best effort: record the failure, but never authenticate on this path.
        try:
            log_auth_event(
                auth_log_path,
                username=log_username,
                event="login_failure",
                success=False,
            )
        except OSError:
            # Logging is unavailable; authentication still fails.
            pass

        return None

    # Credentials are valid. Access is granted only if success is audited.
    try:
        log_auth_event(
            auth_log_path,
            username=log_username,
            event="login_success",
            success=True,
        )
    except OSError as exc:
        raise AuditLogUnavailableError from exc

    return recruiter_id

def render_login(sponsor_name: str, allowed_username: str, auth_log_path: str) -> bool:
    """
    Render the login form and stop execution if the user
    is not authenticated.

    Returns True if the session is authenticated.
    """
    st.session_state.setdefault("authenticated", False)

    # Already authenticated: allow the application to continue.
    if st.session_state["authenticated"]:
        return True

    st.title(f"Hi! PARIS Career Fair 2026 – {sponsor_name} Login")

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
        recruiter_id = authenticate(username, password, allowed_username, auth_log_path)

        if recruiter_id is None:
            st.error("Invalid username or password.")
        else:
            st.session_state.authenticated = True
            st.session_state.username = username
            st.session_state.recruiter_id = recruiter_id
            st.rerun()

    # Prevent the rest of the application from running
    # while the user remains unauthenticated.
    st.stop()

    return False  # Unreachable; st.stop() halts execution.

class AuditLogUnavailableError(Exception):
    """Raised when a successful authentication cannot be audited."""
