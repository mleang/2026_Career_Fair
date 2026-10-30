"""Tests for recruiter authentication and audit-log failure handling.

These tests cover:
- Successful authentication.
- Invalid passwords and case-sensitive usernames.
- Empty and whitespace-only credentials.
- Password whitespace preservation.
- Missing or malformed recruiter configuration.
- Failure to write successful and unsuccessful authentication events.

The audit logger and Streamlit secrets are mocked so the tests are isolated
from real credentials, files, and external services.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError

import career_fair.auth.authentication_handler as authentication_handler
from career_fair.auth.authentication_handler import (
    AuditLogUnavailableError,
    authenticate,
)


# ---------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------

ALLOWED_USERNAME = "loreal"
PASSWORD = "CorrectPassword123!"
RECRUITER_ID = "recruiter1"
AUTH_LOG_PATH = "/tmp/test_auth.log"

PASSWORD_HASHER = PasswordHasher()
PASSWORD_HASH = PASSWORD_HASHER.hash(PASSWORD)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture
def auth_log():
    """Return a dummy log path; tests do not write to this file."""
    return AUTH_LOG_PATH


@pytest.fixture
def recruiter_secrets():
    """Provide a valid mock configuration for the authorized recruiter."""
    return {
        "recruiters": {
            ALLOWED_USERNAME: {
                "password_hash": PASSWORD_HASH,
                "recruiter_id": RECRUITER_ID,
            }
        }
    }


@pytest.fixture
def mock_auth_dependencies(monkeypatch, recruiter_secrets):
    """Mock Streamlit secrets and audit logging.

    Returns the mock logger so tests can inspect calls and simulate failures.
    """
    mock_logger = Mock()

    # authenticate() only needs st.secrets, so provide a lightweight mock.
    monkeypatch.setattr(
        authentication_handler,
        "st",
        SimpleNamespace(secrets=recruiter_secrets),
    )

    monkeypatch.setattr(
        authentication_handler,
        "log_auth_event",
        mock_logger,
    )

    return mock_logger


# ---------------------------------------------------------------------
# Successful authentication
# ---------------------------------------------------------------------

def test_valid_credentials_return_recruiter_id(
    auth_log,
    mock_auth_dependencies,
):
    """Valid credentials return the configured recruiter ID."""
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


# ---------------------------------------------------------------------
# Invalid credentials
# ---------------------------------------------------------------------

def test_wrong_password_is_rejected(
    auth_log,
    mock_auth_dependencies,
):
    """A wrong password is rejected and a failure event is logged."""
    result = authenticate(
        ALLOWED_USERNAME,
        "WrongPassword123!",
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
        "   ",
        " loreal ",
        "other_sponsor",
    ],
)
def test_non_matching_username_is_rejected(
    username,
    auth_log,
    mock_auth_dependencies,
):
    """Reject usernames that do not exactly match the authorized username."""
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


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("", PASSWORD),
        ("   ", PASSWORD),
        (ALLOWED_USERNAME, ""),
    ],
)
def test_missing_credentials_are_rejected(
    username,
    password,
    auth_log,
    mock_auth_dependencies,
):
    """Reject blank usernames and empty passwords."""
    result = authenticate(
        username,
        password,
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


def test_password_whitespace_is_not_removed(
    auth_log,
    mock_auth_dependencies,
):
    """A password with added whitespace must not match the original hash."""
    result = authenticate(
        ALLOWED_USERNAME,
        f" {PASSWORD}",
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


# ---------------------------------------------------------------------
# Invalid or incomplete configuration
# ---------------------------------------------------------------------

def test_missing_recruiter_configuration_is_rejected(
    auth_log,
    mock_auth_dependencies,
    monkeypatch,
):
    """Reject authentication if the recruiter is absent from secrets."""
    monkeypatch.setattr(
        authentication_handler,
        "st",
        SimpleNamespace(secrets={"recruiters": {}}),
    )

    result = authenticate(
        ALLOWED_USERNAME,
        PASSWORD,
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


@pytest.mark.parametrize(
    "recruiter_config",
    [
        {},
        {"password_hash": PASSWORD_HASH},
        {"recruiter_id": RECRUITER_ID},
        {"password_hash": "", "recruiter_id": RECRUITER_ID},
        {"password_hash": PASSWORD_HASH, "recruiter_id": ""},
    ],
)
def test_incomplete_recruiter_configuration_is_rejected(
    recruiter_config,
    auth_log,
    mock_auth_dependencies,
    monkeypatch,
):
    """Reject missing or empty password hashes and recruiter IDs."""
    monkeypatch.setattr(
        authentication_handler,
        "st",
        SimpleNamespace(
            secrets={
                "recruiters": {
                    ALLOWED_USERNAME: recruiter_config,
                }
            }
        ),
    )

    result = authenticate(
        ALLOWED_USERNAME,
        PASSWORD,
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


def test_malformed_password_hash_is_rejected(
    auth_log,
    mock_auth_dependencies,
    monkeypatch,
):
    """Reject an invalid Argon2 hash without granting access."""
    monkeypatch.setattr(
        authentication_handler,
        "st",
        SimpleNamespace(
            secrets={
                "recruiters": {
                    ALLOWED_USERNAME: {
                        "password_hash": "not-a-valid-argon2-hash",
                        "recruiter_id": RECRUITER_ID,
                    }
                }
            }
        ),
    )

    result = authenticate(
        ALLOWED_USERNAME,
        PASSWORD,
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


# ---------------------------------------------------------------------
# Audit logger unavailable
# ---------------------------------------------------------------------

def test_valid_credentials_but_success_logging_fails(
    auth_log,
    mock_auth_dependencies,
):
    """Do not grant access when the successful login cannot be audited."""
    mock_auth_dependencies.side_effect = OSError("Log file is unavailable")

    with pytest.raises(AuditLogUnavailableError):
        authenticate(
            ALLOWED_USERNAME,
            PASSWORD,
            ALLOWED_USERNAME,
            auth_log,
        )

    mock_auth_dependencies.assert_called_once_with(
        auth_log,
        username=ALLOWED_USERNAME,
        event="login_success",
        success=True,
    )


def test_invalid_password_is_rejected_when_failure_logging_fails(
    auth_log,
    mock_auth_dependencies,
):
    """A logging outage must never turn invalid credentials into success."""
    mock_auth_dependencies.side_effect = OSError("Log file is unavailable")

    result = authenticate(
        ALLOWED_USERNAME,
        "WrongPassword123!",
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


def test_missing_username_is_rejected_when_failure_logging_fails(
    auth_log,
    mock_auth_dependencies,
):
    """Blank credentials remain rejected even if failure logging fails."""
    mock_auth_dependencies.side_effect = OSError("Log file is unavailable")

    result = authenticate(
        "",
        PASSWORD,
        ALLOWED_USERNAME,
        auth_log,
    )

    assert result is None

    mock_auth_dependencies.assert_called_once_with(
        auth_log,
        username="unknown",
        event="login_failure",
        success=False,
    )
