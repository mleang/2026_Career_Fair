# 🧪 Recruiter App — Test Suite

This directory contains automated tests for the Hi! PARIS Career Fair recruiter application.

The tests cover authentication, candidate identity, Dropbox storage behaviour, concurrent updates, remote refresh logic, CV retrieval, and Streamlit UI behaviour.

The objective is to catch regressions before deployment, particularly those that could cause:

- Unauthorized recruiter access.
- Incorrect candidate matching.
- Lost or overwritten candidate-status updates.
- Inconsistent local and remote data.
- Failed CSV or CV retrieval.
- Unexpected UI behaviour.

The test suite should use synthetic data and mocked external services. **Do not run automated tests against production candidate data or production Dropbox files.**

---

## 1. Test organization

The current test modules are organized as follows:

| File | Main responsibility |
|---|---|
| `conftest.py` | Shared fixtures, candidate DataFrames, test constants, and fake storage helpers. |
| `test_authentication_handler.py` | Authentication logic, password verification, and failure cases. |
| `test_authentication_old.py` | Legacy authentication tests; review before relying on them as current coverage. |
| `test_app_ui.py` | Streamlit application rendering and UI-level tests. |
| `test_ui_and_auth.py` | Login flow, authenticated UI rendering, and candidate search availability. |
| `test_candidate_identity.py` | Candidate identity and matching behaviour. |
| `test_concurrency.py` | Merge behaviour and concurrent candidate-status updates. |
| `test_production_refresh.py` | Session refresh behaviour and reconciliation with remote data. |
| `test_storage.py` | Storage-layer behaviour and Dropbox-related operations. |
| `test_cv_opening.py` | CV retrieval/opening behaviour. |

The exact assertions and coverage depend on the current contents of each module. Keep this table synchronized when tests are added, removed, or reorganized.

---

## 2. Test architecture

The suite uses several complementary testing approaches.

### 2.1. Unit tests

Unit tests verify individual functions independently of the Streamlit interface or external services.

Examples include:

- Candidate identity normalization.
- Candidate matching.
- Three-way merge behaviour.
- Authentication decisions.
- CSV and storage helper behaviour.

These tests should be fast, deterministic, and independent of network access.

### 2.2. Mocked storage tests

Dropbox interactions are mocked or replaced with fake implementations where appropriate.

This allows tests to exercise application logic without requiring:

- A real Dropbox account.
- Production access tokens.
- Network access.
- Real candidate-state CSV files.
- Real CV PDFs.

A mocked Dropbox client can simulate success, missing files, API errors, and write conflicts.

A passing mocked storage test does **not** prove that the production Dropbox credentials, permissions, paths, or network integration are correctly configured.

### 2.3. Streamlit AppTest

The Streamlit tests use:

```python
from streamlit.testing.v1 import AppTest
```

`AppTest` runs the application in a test environment and allows the tests to inspect widgets, session state, and application exceptions.

The UI tests can verify behaviour such as:

- Whether the login form renders.
- Whether invalid credentials are rejected.
- Whether valid credentials establish an authenticated session.
- Whether the authenticated candidate interface renders.
- Whether the candidate search widget is available.

For tests that focus on the authenticated interface, the helper `login_as_recruiter()` can inject an authenticated session. This deliberately bypasses password verification, so it should not replace the separate authentication tests.

### 2.4. Concurrency and merge tests

The concurrency tests verify the merge logic independently of a real multi-user browser deployment.

They are intended to detect:

- Lost updates.
- Incorrect handling of remote-only changes.
- Incorrect handling of local-only changes.
- Conflicts when two sessions edit the same field.
- Incorrect handling of boolean values, including unchecking a flag.
- Accidental coupling between candidates with identical names but different CV paths.
- Mutation of input DataFrames.
- Non-idempotent merge behaviour.

A fake storage implementation can simulate revision-aware writes and stale revisions.

These tests are important, but they do not fully reproduce the timing and network behaviour of several real Streamlit sessions connected to Dropbox.

---

## 3. Shared fixtures

`conftest.py` contains shared test fixtures and helpers.

The main shared elements include:

- Candidate column names.
- Boolean status column names.
- `make_df()` for creating synthetic candidate DataFrames.
- `base_df` and `candidates` fixtures.
- A fake Dropbox/storage helper for deterministic tests.

The candidate-state schema used by the tests is:

```python
TEXT_COLS = [
    "first_name",
    "last_name",
    "file_path",
]

BOOL_COLS = [
    "seen",
    "intend_view",
    "cv_saved",
    "contacted",
]
```

The test data is synthetic. For example:

```csv
first_name,last_name,file_path,seen,intend_view,cv_saved,contacted
Alice,Photon,/fake_cvs/Photon_Alice.pdf,0,0,0,0
Bob,Neutron,/fake_cvs/Neutron_Bob.pdf,1,1,0,0
Ada,Quark,/fake_cvs/Quark_Ada.pdf,1,0,0,0
```

The candidate identity includes the CV path. Two candidates with the same first and last name but different CV paths must be treated as distinct records.

When adding new tests, prefer the shared fixtures over defining slightly different candidate DataFrames in every test module.

---

## 4. Running the tests

Run commands from the repository root, not from inside the `tests/` directory.

### 4.1. Install dependencies

Activate the project's virtual environment:

```bash
source .venv/bin/activate
```

Install the project dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If the test dependencies are not included in `requirements.txt`, install them separately:

```bash
pip install pytest
```

### 4.2. Run the complete test suite

```bash
pytest
```

### 4.3. Run with verbose output

```bash
pytest -v
```

### 4.4. Run one test module

For example:

```bash
pytest tests/test_authentication_handler.py -v
```

```bash
pytest tests/test_concurrency.py -v
```

```bash
pytest tests/test_production_refresh.py -v
```

```bash
pytest tests/test_ui_and_auth.py -v
```

### 4.5. Run one specific test

```bash
pytest tests/test_ui_and_auth.py::test_valid_credentials_authenticate_user -v
```

### 4.6. Stop at the first failure

```bash
pytest -x
```

### 4.7. Show print output

```bash
pytest -s
```

### 4.8. Run with coverage

If `pytest-cov` is installed:

```bash
pytest --cov=. --cov-report=term-missing
```

Coverage reports are useful for identifying untested code paths. A high coverage percentage alone does not prove that the application is correct.

---

## 5. Streamlit UI test configuration

The UI tests use `AppTest.from_file()` to execute the Streamlit application.

The application path is configured through `APP_PATH` in `conftest.py`.

For example:

```python
APP_PATH = os.environ.get(
    "APP_PATH",
    str(ROOT / "my_app.py"),
)
```

If the intended entry point is `app_loreal.py`, either update the default or specify the path when running the tests:

```bash
APP_PATH=app_loreal.py pytest tests/test_ui_and_auth.py -v
```

Check that the selected file is the actual entry point you intend to test.

### Mocking external dependencies

The UI fixture mocks Dropbox-related functions to avoid real external calls.

The test environment supplies a secrets structure compatible with the application's authentication handler:

```python
at.secrets["recruiters"] = {
    "loreal": {
        "password_hash": PASSWORD_HASH,
        "recruiter_id": "recruiter-test-01",
    }
}
```

The password hash is generated during the test using Argon2.

The tests should not need production passwords, production hashes, or real Dropbox credentials.

### Authentication tests versus authenticated UI tests

There are two different testing objectives:

1. **Authentication tests:** exercise the login form and real password verification.
2. **Authenticated UI tests:** inject an authenticated session and focus on candidate-interface behaviour.

Keeping these concerns separate makes the tests easier to diagnose.

---

## 6. Important test scenarios

The following scenarios are particularly important for the recruiter application.

### 6.1. Authentication

Verify that:

- Valid credentials authenticate successfully.
- An incorrect password is rejected.
- An unauthorized username is rejected.
- Missing credentials are rejected.
- Malformed or invalid password hashes are handled safely.
- Missing recruiter configuration does not authenticate a user.
- Authentication failures are logged appropriately.
- A failure to write a successful-login audit event is handled safely.
- Logout invalidates the authenticated session.

### 6.2. Candidate identity

Verify that:

- Names can be normalized for matching.
- Case differences do not create false mismatches.
- Accented and unaccented forms are handled as intended.
- Duplicate names with different CV paths remain distinct.
- The same candidate is not accidentally duplicated during refresh or merge.

### 6.3. Storage

Verify that:

- Candidate-state data can be read.
- Candidate-state data can be written.
- Missing Dropbox paths are handled.
- Invalid or empty CV paths are rejected.
- Dropbox API failures are handled.
- Failed writes do not silently discard local changes.
- Revision conflicts are detected and handled.

### 6.4. Concurrent updates

Verify that:

- Changes to different candidates are preserved.
- Changes to different status columns are preserved.
- Identical changes from two sessions do not create unnecessary conflicts.
- Remote-only changes are incorporated.
- Local-only changes are preserved.
- A recruiter can uncheck a status that was previously true.
- A stale Dropbox revision cannot silently overwrite a newer remote version.
- Merge operations do not mutate their input DataFrames.
- Repeating a merge with the same inputs produces a consistent result.

Pay particular attention to simultaneous edits to the same candidate and the same boolean field.

### 6.5. Remote refresh

Verify that:

- A clean session adopts remote changes.
- A session with pending local changes preserves those changes.
- The original baseline is retained when local changes are still pending.
- Remote-only candidates are incorporated.
- Same-name candidates with different CV paths remain independent.
- Failed remote downloads do not silently replace valid local state with empty data.

The recruiter workflow does not allow recruiters to add candidates locally. New candidates should arrive through the authorized external candidate-list update process.

### 6.6. CV opening

Verify that:

- A valid Dropbox CV path is retrieved.
- The returned content can be encoded as expected.
- Invalid paths are rejected.
- Dropbox errors are handled.
- A failed CV retrieval does not crash unrelated candidate-management functionality.

---

## 7. What the tests do not prove

A passing test suite is an important quality check, but it is not equivalent to production validation.

In particular:

| Test type | What it demonstrates | What it does not demonstrate |
|---|---|---|
| Unit tests | Individual function behaviour | Full application integration |
| Mocked Dropbox tests | Behaviour under simulated Dropbox responses | Real Dropbox permissions or connectivity |
| Fake revision tests | Merge and optimistic-write logic | Actual multi-session Dropbox timing |
| Streamlit AppTest | Selected UI and session-state behaviour | Full browser behaviour on all devices |
| Playwright browser tests | Browser-level interaction at tested viewports | Native iOS/Android PDF behaviour |
| Staging integration tests | Interaction with real staging services | Every production failure mode |

Additional staging tests should cover:

- Real Dropbox authentication and permissions.
- Reading and writing a dedicated test CSV.
- Multiple authenticated sessions.
- Simultaneous updates.
- Refresh while another session is saving.
- Dropbox API throttling and transient network errors.
- CV retrieval on desktop and mobile devices.
- Logout and session cleanup.

Never use the production candidate CSV as a test target.

---

## 8. Recommended test workflow before deployment

Run the following sequence before pushing a significant change:

```bash
# 1. Run the complete test suite
pytest -v

# 2. Run the authentication tests
pytest tests/test_authentication_handler.py -v

# 3. Run candidate identity tests
pytest tests/test_candidate_identity.py -v

# 4. Run storage tests
pytest tests/test_storage.py -v

# 5. Run concurrency tests
pytest tests/test_concurrency.py -v

# 6. Run remote refresh tests
pytest tests/test_production_refresh.py -v

# 7. Run UI and authentication tests
pytest tests/test_ui_and_auth.py -v
```

Then:

1. Run the application locally.
2. Test the login and logout flows.
3. Search for candidates with and without accents.
4. Open a synthetic CV.
5. Change and uncheck each status flag.
6. Save and verify the resulting state.
7. Test with two independent sessions.
8. Confirm that remote changes are visible after refresh.
9. Test the application on a mobile browser.
10. Validate the same workflows in a staging deployment.

---

## 9. Test maintenance guidelines

When adding or modifying tests:

- Use synthetic data only.
- Reuse fixtures from `conftest.py`.
- Mock external services rather than calling production services.
- Test both successful and failing paths.
- Avoid relying on test execution order.
- Avoid shared mutable state between tests.
- Use descriptive test names.
- Keep tests focused on one behaviour where possible.
- Assert the resulting state, not only that a function did not raise an exception.
- Keep the tests aligned with the actual production entry point and implementation.
- Remove or clearly label obsolete tests so they are not mistaken for current coverage.

Before adding a new test, check whether an existing test already covers the same behaviour.

**The goal is not simply to make the test suite pass. It is to make regressions in authentication, candidate data, storage, and concurrent editing detectable before they affect recruiters during the Career Fair.**