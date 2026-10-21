import streamlit as st
from argon2 import PasswordHasher
from argon2.exceptions import (
    VerifyMismatchError,
    VerificationError,
    InvalidHashError,
)

from audit_logger import log_auth_event

# =====================================
# ------- AUTHENTICATION --------------
# =====================================
PASSWORD_HASHER = PasswordHasher()

def authenticate(username: str, password: str, allowed_username: str, auth_log_path: str) -> str | None:
    """Authenticate the recruiter assigned to this app and return recruiter_id."""

    username = username.strip().lower()

    try:
        # 1. Restrict access to the recruiter assigned to this app
        if username != allowed_username:
            raise ValueError("Unauthorized recruiter for this app")

        # 2. Retrieve the recruiter's credentials
        recruiter = st.secrets["recruiters"][username]
        password_hash = recruiter["password_hash"]

        # 3. Verify the password
        PASSWORD_HASHER.verify(password_hash, password)

        # 4. Log successful authentication
        log_auth_event(
            auth_log_path,
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
            auth_log_path,
            username=username,
            event="login_failure",
            success=False,
        )

        return None

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
        normalized_username = username.strip().lower()
        recruiter_id = authenticate(normalized_username, password, allowed_username, auth_log_path)

        if recruiter_id is None:
            st.error("Invalid username or password.")
        else:
            st.session_state.authenticated = True
            st.session_state.username = normalized_username
            st.session_state.recruiter_id = recruiter_id
            st.rerun()

    # Prevent the rest of the application from running
    # while the user remains unauthenticated.
    st.stop()

    return False  # Unreachable; st.stop() halts execution.