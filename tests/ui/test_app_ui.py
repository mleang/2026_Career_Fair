"""Streamlit UI tests for the recruiter application."""

import pytest
from streamlit.testing.v1 import AppTest
from argon2 import PasswordHasher
from conftest import APP_PATH

ALLOWED = "loreal"
PASSWORD = "CorrectPassword123!"
HASH = PasswordHasher().hash(PASSWORD)

@pytest.mark.skipif(
    False,
    reason="Enable after app startup dependencies are mocked",
)
def test_app_renders_without_uncaught_exception():
    """The app must render without raising an uncaught Streamlit exception."""
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()

    assert not at.exception


def test_login_form_is_visible_when_logged_out():
    """Unauthenticated users must see the login form rather than candidate data."""
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()

    assert not at.exception
    assert len(at.text_input) >= 1
    assert len(at.button) >= 1


def make_app():
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.secrets["recruiters"] = {
        "loreal": {"password_hash": HASH, "recruiter_id": "rec-1"}
    }
    return at

def test_wrong_password_stays_on_login():
    at = make_app().run()
    at.text_input[0].set_value("loreal")
    at.text_input[1].set_value("wrong")
    at.button[0].click().run()
    assert at.error[0].value == "Invalid username or password."
    assert not at.session_state["authenticated"]

def test_wrong_password_does_not_reveal_candidate_data():
    """Rejected credentials must leave the user unauthenticated and hide CV data."""
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.secrets["recruiters"] = {
        "loreal": {"password_hash": HASH, "recruiter_id": "rec-1"}
    }
    at.run()

    at.text_input[0].set_value("loreal")   # Username
    at.text_input[1].set_value("wrong")    # Password
    at.button[0].click().run()             # "🔐 Sign in" (form submit buttons count as buttons)

    assert not at.exception
    assert at.error[0].value == "Invalid username or password."
    assert not at.session_state["authenticated"]
    assert len(at.selectbox) == 0          # no candidate search or filter shown
    # at = AppTest.from_file(APP_PATH, default_timeout=30).run()

    # # Replace these keys with the real widget keys from render_login().
    # at.text_input(key="username").set_value("recruiter1")
    # at.text_input(key="password").set_value("WrongPassword!")
    # at.button(key="login").click()
    # at.run()

    # assert not at.exception

    # # Replace "authenticated" with your actual session-state flag.
    # assert not at.session_state.get("authenticated", False)

    # # Add a project-specific assertion that candidate data is not rendered.
    # # For example, assert a known candidate name is absent from at.markdown.



# python -m pytest tests/ \
#   --cov=authentication_handler \
#   --cov=merge_handler \
#   --cov=dropbox_storage \
#   --cov-report=term-missing