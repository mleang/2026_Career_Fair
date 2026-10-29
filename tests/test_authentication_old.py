"""Unit tests for the recruiter's authentication module.

Purpose
-------
Verify that ``authenticate()`` enforces the application's authentication
rules and returns a consistent result:

- Successful authentication returns the configured recruiter ID.
- Invalid credentials return ``None``.
- Usernames are matched exactly, including letter casing.
- Passwords are verified exactly as submitted.
- Failed authentication attempts generate failure audit events.
- Missing recruiter configuration is handled as an authentication failure.
- Passwords and other sensitive credentials are not written to audit logs.

These are unit tests: Streamlit secrets and audit logging are mocked, so
the tests do not require real credentials, a running Streamlit application,
or a physical audit-log file.
"""

from unittest.mock import patch

import pytest
from argon2 import PasswordHasher

from authentication_handler import authenticate


# -------------------------------------------------------------------
# Test configuration
# -------------------------------------------------------------------

# A known password used only in tests. Never use a real recruiter password.
PASSWORD = "CorrectPassword123!"

# Generate a valid Argon2 password hash for the mock recruiter configuration.
# The hash is used by authenticate(); the plaintext password is never stored
# in the mocked secrets.
HASH = PasswordHasher().hash(PASSWORD)

# Expected identity returned by authenticate() after successful verification.
RECRUITER_ID = "recruiter1"

# Exact username configured for this application.
# Matching is case-sensitive: "loreal" is different from "LOREAL".
ALLOWED_USERNAME = "loreal"


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------

@pytest.fixture
def auth_log(tmp_path):
    """Return a temporary path for the authentication audit log.

    ``tmp_path`` is provided by pytest and is unique to each test.
    The path lets tests exercise the authentication interface without
    writing to the application's real audit log.
    """
    return str(tmp_path / "auth.log")


@pytest.fixture
def recruiter_secrets():
    """Return mock Streamlit secrets for one authorized recruiter.

    The structure mirrors the expected application configuration:
    ``st.secrets["recruiters"][username]`` contains a password hash
    and the recruiter's ID.
    """
    return {
        "recruiters": {
            ALLOWED_USERNAME: {
                "password_hash": HASH,
                "recruiter_id": RECRUITER_ID,
            }
        }
    }


@pytest.fixture
def mock_auth_dependencies(recruiter_secrets):
    """Mock authentication dependencies and expose the audit-log mock.

    Streamlit secrets are replaced with test configuration, and
    ``log_auth_event`` is mocked to prevent real log writes and to let
    tests verify whether the correct audit event was emitted.

    The yielded mock is the mocked ``log_auth_event`` function.
    """
    with (
        patch(
            "authentication_handler.st.secrets",
            recruiter_secrets,
        ),
        patch(
            "authentication_handler.log_auth_event",
        ) as log_event,
    ):
        yield log_event


# -------------------------------------------------------------------
# Successful authentication
# -------------------------------------------------------------------

def test_correct_credentials_return_recruiter_id(
    auth_log, mock_auth_dependencies
):
    """Return the configured recruiter ID for valid credentials.

    Purpose:
        Verify the main success path: the username matches the configured
        account, Argon2 verifies the password, and the function returns
        the recruiter's ID.

    Also verify:
        Exactly one successful-login audit event is emitted with the
        expected username and success flag.
    """
    result = authenticate(
        ALLOWED_USERNAME,
        PASSWORD,
        ALLOWED_USERNAME,
        auth_log,
    )

    assert result == RECRUITER_ID

    mock_auth_dependencies.assert_called_once_with(
        auth_log,
        username=ALLOWED_USERNAME,
        event="login_success",
        success=True,
    )


# -------------------------------------------------------------------
# Incorrect password
# -------------------------------------------------------------------

def test_wrong_password_returns_none_and_logs_failure(
    auth_log, mock_auth_dependencies
):
    """Reject an authorized username supplied with the wrong password.

    Purpose:
        Confirm that knowing the correct username is not sufficient to
        authenticate. Argon2 password verification must succeed.

    Expected behavior:
        The function returns None and records exactly one failed-login
        audit event. It must not return a recruiter ID.
    """
    result = authenticate(
        ALLOWED_USERNAME,
        "WrongPassword!",
        ALLOWED_USERNAME,
        auth_log,
    )

    assert result is None

    mock_auth_dependencies.assert_called_once_with(
        auth_log,
        username=ALLOWED_USERNAME,
        event="login_failure",
        success=False,
    )


# -------------------------------------------------------------------
# Username authorization and case sensitivity
# -------------------------------------------------------------------

@pytest.mark.parametrize(
    "username",
    [
        "LOREAL",
        "Loreal",
        "unknown",
        "recruiter1",
        "@!é",
        "",
        " ", 
        "other_sponsor",
    ],
)
def test_non_matching_username_is_rejected(
    username, auth_log, mock_auth_dependencies
):
    """Reject usernames that do not exactly match the allowed username.

    Purpose:
        Test both unauthorized usernames and casing differences.

    Examples:
        If the configured username is ``loreal``, then ``LOREAL`` and
        ``Loreal`` must be rejected. The implementation must not lowercase
        usernames or otherwise make the comparison case-insensitive.

    Expected behavior:
        The function returns None and emits one failed-login audit event
        for the submitted username.
    """
    result = authenticate(
        username,
        PASSWORD,
        ALLOWED_USERNAME,
        auth_log,
    )

    assert result is None

    mock_auth_dependencies.assert_called_once_with(
        auth_log,
        username=username if username.strip() else "unknown",
        event="login_failure",
        success=False,
    )


# -------------------------------------------------------------------
# Missing or invalid credentials
# -------------------------------------------------------------------

@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("", PASSWORD),
        (ALLOWED_USERNAME, ""),
        ("", ""),
        (None, PASSWORD),
        (ALLOWED_USERNAME, None),
    ],
)
def test_missing_credentials_return_none_and_log_failure(
    username, password, auth_log, mock_auth_dependencies
):
    """Reject missing or empty credentials consistently.

    Purpose:
        Cover missing usernames, missing passwords, and combinations of
        missing credentials. None values also verify that the function
        handles non-string inputs without crashing.

    Expected behavior:
        The function returns None and records one failure event.
        For a missing or non-string username, the expected audit username
        is ``unknown`` if that is the implementation's logging policy.

    Security importance:
        Returning False instead of None is unsafe when the caller uses
        ``result is None`` to distinguish authentication failure from
        success. The function must use a consistent failure sentinel.
    """
    result = authenticate(
        username,
        password,
        ALLOWED_USERNAME,
        auth_log,
    )

    assert result is None

    mock_auth_dependencies.assert_called_once_with(
        auth_log,
        username=username if isinstance(username, str) and username else "unknown",
        event="login_failure",
        success=False,
    )


# -------------------------------------------------------------------
# Password handling
# -------------------------------------------------------------------

def test_password_whitespace_is_preserved(
    auth_log, mock_auth_dependencies
):
    """Verify that the submitted password is not silently trimmed.

    Purpose:
        Passwords may legitimately contain leading or trailing spaces.
        Authentication must verify the exact submitted string rather
        than modifying it with ``strip()``.

    This test submits a password that differs from the configured
    password by surrounding spaces. It should fail verification.

    Expected behavior:
        The function returns None and records one failed-login event.
    """
    password_with_spaces = " CorrectPassword123! "

    result = authenticate(
        ALLOWED_USERNAME,
        password_with_spaces,
        ALLOWED_USERNAME,
        auth_log,
    )

    assert result is None

    mock_auth_dependencies.assert_called_once_with(
        auth_log,
        username=ALLOWED_USERNAME,
        event="login_failure",
        success=False,
    )


# -------------------------------------------------------------------
# Configuration errors
# -------------------------------------------------------------------

def test_missing_recruiter_configuration_returns_none(auth_log):
    """Handle a missing recruiter entry without authenticating.

    Purpose:
        Simulate a configuration where the ``recruiters`` mapping exists
        but does not contain the authorized username.

    Expected behavior:
        Looking up the recruiter raises KeyError internally; the
        authentication function catches the expected configuration error,
        returns None, and records a failed-login event.

    This test does not validate every possible malformed configuration.
    Separate tests can cover missing password hashes, invalid hashes,
    and missing recruiter IDs.
    """
    with (
        patch(
            "authentication_handler.st.secrets",
            {"recruiters": {}},
        ),
        patch(
            "authentication_handler.log_auth_event",
        ) as log_event,
    ):
        result = authenticate(
            ALLOWED_USERNAME,
            PASSWORD,
            ALLOWED_USERNAME,
            auth_log,
        )

    assert result is None

    log_event.assert_called_once_with(
        auth_log,
        username=ALLOWED_USERNAME,
        event="login_failure",
        success=False,
    )
