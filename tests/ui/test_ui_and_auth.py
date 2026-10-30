
"""Streamlit UI and authentication tests for the recruiter application.

Authentication tests exercise the real login form and authentication handler.

Authenticated UI tests use a helper that injects an authenticated session,
so they can focus on candidate-interface behavior without repeatedly testing
password verification.

Dropbox operations are mocked to keep these tests deterministic and offline.
"""

import pytest
from unittest.mock import MagicMock
from argon2 import PasswordHasher
from streamlit.testing.v1 import AppTest
from pathlib import Path

from conftest import APP_PATH


# ---------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------

USERNAME = "loreal"
GOOD_PASSWORD = "CorrectPassword123!"
RECRUITER_ID = "recruiter-test-01"

PASSWORD_HASH = PasswordHasher().hash(GOOD_PASSWORD)
TIMEOUT = 15


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch, candidates):
    """Create an AppTest instance of the recruiter application.

    Dropbox operations are mocked to keep tests deterministic and
    independent of external services.
    """
    store = {"df": candidates.copy(deep=True)}
    fake_dbx = MagicMock()

    # Patch the modules actually imported by apps/my_app.py.
    import career_fair.storage.dropbox_storage as dropbox_storage
    import career_fair.storage.merge_handler as merge_handler

    monkeypatch.setattr(
        dropbox_storage,
        "_build_dbx_client",
        lambda *args, **kwargs: fake_dbx,
    )
    monkeypatch.setattr(
        dropbox_storage,
        "_get_remote_hash",
        lambda *args, **kwargs: "hash-1",
    )
    monkeypatch.setattr(
        dropbox_storage,
        "_download_current_df_from_dbx",
        lambda *args, **kwargs: store["df"].copy(deep=True),
    )
    monkeypatch.setattr(
        dropbox_storage,
        "get_cv_pdf_base64",
        lambda *args, **kwargs: "JVBERi0=",
    )
    monkeypatch.setattr(
        merge_handler,
        "_flush_to_disk",
        lambda *args, **kwargs: True,
    )

    # Execute the actual application, not the diagnostic form script.
    at = AppTest.from_file(APP_PATH, default_timeout=TIMEOUT)

    at.secrets["STATE_DBX_PATH_LOREAL"] = "/state_loreal.csv"
    at.secrets["recruiters"] = {
        USERNAME: {
            "password_hash": PASSWORD_HASH,
            "recruiter_id": RECRUITER_ID,
        }
    }

    at.run()

    return at


# ---------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------

def test_login_form_is_displayed(app):
    """Verify that an unauthenticated user sees the login form.

    The username and password fields and the Sign in button must be
    rendered. The user must not be authenticated initially.
    """
    app.run()

    assert not app.exception

    labels = [element.label for element in app.text_input]

    assert "Username" in labels
    assert "Password" in labels

    assert any(
        button.label == "🔐 Sign in"
        for button in app.button
    )

    assert app.session_state["authenticated"] is False


def test_wrong_password_is_rejected(app):
    """Verify that a valid username with an incorrect password is rejected.

    The login attempt must leave the user unauthenticated and display
    a generic error message without exposing application data.
    """
    app.run()

    app.text_input[0].set_value(USERNAME)
    app.text_input[1].set_value("WrongPassword123!")

    sign_in = next(
        button for button in app.button
        if button.label == "🔐 Sign in"
    )

    sign_in.click().run()

    assert not app.exception
    assert app.session_state["authenticated"] is False
    assert app.session_state.get("recruiter_id") is None

    assert any(
        "Invalid username or password" in error.value
        for error in app.error
    )


def test_wrong_username_is_rejected(app):
    """Verify that an unauthorized username cannot authenticate.

    Even when the correct password is supplied, a username other than
    the configured recruiter username must be rejected.
    """
    app.run()

    app.text_input[0].set_value("unauthorized")
    app.text_input[1].set_value(GOOD_PASSWORD)

    sign_in = next(
        button for button in app.button
        if button.label == "🔐 Sign in"
    )

    sign_in.click().run()

    assert not app.exception
    assert app.session_state["authenticated"] is False
    assert app.session_state.get("recruiter_id") is None

    assert any(
        "Invalid username or password" in error.value
        for error in app.error
    )


def test_valid_credentials_authenticate_user(app):
    """Verify that valid credentials establish the recruiter session.

    The real login form and authentication handler are exercised.
    On success, the session must contain the authenticated flag,
    username, and recruiter ID.
    """
    app.run()

    app.text_input[0].set_value(USERNAME)
    app.text_input[1].set_value(GOOD_PASSWORD)

    sign_in = next(
        button for button in app.button
        if button.label == "🔐 Sign in"
    )

    sign_in.click().run()

    assert not app.exception
    assert app.session_state["authenticated"] is True
    assert app.session_state["username"] == USERNAME
    assert app.session_state["recruiter_id"] == RECRUITER_ID


# ---------------------------------------------------------------------
# Authenticated UI helper
# ---------------------------------------------------------------------

def login_as_recruiter(app):
    """Prepare an authenticated session for application UI tests.

    This helper deliberately bypasses the login form. Authentication
    behavior is tested separately above. The helper is intended only
    for tests that focus on the authenticated application interface.
    """
    app.run()

    app.session_state["authenticated"] = True
    app.session_state["username"] = USERNAME
    app.session_state["recruiter_id"] = RECRUITER_ID

    app.run()

    if app.exception:
        details = "\n".join(
            f"{type(exc.value).__name__}: {exc.value}"
            for exc in app.exception
        )
        pytest.fail(
            f"Streamlit app failed during authenticated rendering:\n{details}"
        )

    return app


# ---------------------------------------------------------------------
# Authenticated UI tests
# ---------------------------------------------------------------------

def test_candidate_search_is_available_after_login(app):
    """Verify that the candidate search widget is rendered after login.

    The test also checks that Dropbox initialization succeeded, because
    candidate data cannot be loaded when the Dropbox dependency fails.
    """
    app = login_as_recruiter(app)

    assert app.session_state.get("dbx_error") is None

    assert any(
        element.key == "candidate_search"
        for element in app.selectbox
    )

# ---------------------------------------------------------------------
# Logout tests
# ---------------------------------------------------------------------

def test_logout_clears_authenticated_session(app):
    """Verify that logging out invalidates the recruiter session.

    After clicking Log out, the authenticated flag must be False,
    recruiter identity information must be cleared, and the login
    form must be displayed again.
    """
    # Start with an authenticated recruiter session.
    app = login_as_recruiter(app)

    assert app.session_state["authenticated"] is True
    assert app.session_state["username"] == USERNAME
    assert app.session_state["recruiter_id"] == RECRUITER_ID

    # Find and click the logout button.
    logout_button = next(
        button for button in app.button
        if button.key == "logout_header"
    )

    logout_button.click().run()

    # Verify that the session has been invalidated.
    assert not app.exception
    assert app.session_state["authenticated"] is False
    assert app.session_state.get("username") is None
    assert app.session_state.get("recruiter_id") is None

    # Verify that the login form is displayed again.
    assert any(
        element.label == "Username"
        for element in app.text_input
    )
    assert any(
        button.label == "🔐 Sign in"
        for button in app.button
    )