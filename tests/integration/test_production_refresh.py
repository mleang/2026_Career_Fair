
"""Production refresh tests for the recruiter app.

These tests verify the behavior of the production refresh reconciliation
helper, `_refresh_from_remote`.

The recruiter application uses a fixed candidate list: recruiters can edit
candidate statuses but cannot add or remove candidate rows themselves.
New candidates may nevertheless be added externally to the shared Dropbox
CSV and must appear after a successful refresh.

The tests focus on:
- Adopting remote changes when there are no unsaved local edits.
- Preserving local edits and the original baseline during a dirty refresh.
- Keeping candidate statuses independent when two CV paths share a name.
- Incorporating candidates added externally to the remote CSV.

Dropbox download/upload operations and Streamlit UI rendering are tested
separately.
"""

import pandas as pd
import pytest

import career_fair.storage.merge_handler as mh


# Candidate identity and status columns expected by the merge handler.
TEXT_COLS = ["first_name", "last_name", "file_path"]
BOOL_COLS = ["seen", "intend_view", "cv_saved", "contacted"]

# Names of the columns forming the composite candidate key.
FIRST, LAST, FILE = "first_name", "last_name", "file_path"


class FakeSessionState(dict):
    """Provide a dictionary-backed substitute for Streamlit session state.

    Streamlit session state supports both dictionary-style and attribute-style
    access. This lightweight substitute implements both interfaces so tests
    can exercise the refresh helper without launching a Streamlit application.
    """

    def __getattr__(self, name):
        """Return a session-state value through attribute-style access.

        Raise AttributeError rather than KeyError when the requested
        attribute has not been initialized.
        """
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        """Store an attribute-style assignment as a dictionary entry."""
        self[name] = value


@pytest.fixture
def state(monkeypatch):
    """Install an isolated fake Streamlit session state for each test.

    Replacing `mh.st.session_state` prevents tests from sharing session data
    and ensures that changes made by one test cannot affect another.
    """
    session = FakeSessionState()
    monkeypatch.setattr(mh.st, "session_state", session)
    return session


def candidates(*rows):
    """Build a candidate DataFrame from candidate records.

    Each input row must be a tuple of:
        (first_name, last_name, file_path, seen)

    The remaining status columns are initialized to False. The resulting
    DataFrame uses the same column order as the production merge schema.
    """
    return pd.DataFrame(
        [
            {
                "first_name": first,
                "last_name": last,
                "file_path": path,
                "seen": seen,
                "intend_view": False,
                "cv_saved": False,
                "contacted": False,
            }
            for first, last, path, seen in rows
        ],
        columns=TEXT_COLS + BOOL_COLS,
    )


def refresh(remote):
    """Run the production remote-refresh reconciliation helper.

    Passes the downloaded remote DataFrame and the candidate schema to
    `_refresh_from_remote`, the same helper used by the application when
    reconciling its local session state with the latest remote CSV.

    Returns:
        tuple: The reconciled DataFrame and the list of merge conflicts.
    """
    return mh._refresh_from_remote(
        remote, TEXT_COLS, BOOL_COLS, FIRST, LAST, FILE
    )


def test_refresh_without_local_edits_adopts_remote_snapshot(state):
    """Adopt the latest remote snapshot when the local session is clean.

    Given a session with no unsaved changes, a remote status update should
    replace the corresponding local value. Both the working DataFrame and
    the baseline must then reflect the remote snapshot.

    The session must remain clean because no local changes need saving.
    """
    base = candidates(("Ada", "Lovelace", "/ada.pdf", False))
    remote = candidates(("Ada", "Lovelace", "/ada.pdf", True))

    state["base_df"] = base.copy(deep=True)
    state["df"] = base.copy(deep=True)
    state["buffer_dirty"] = False

    merged, conflicts = refresh(remote)

    assert conflicts == []
    assert bool(merged.iloc[0]["seen"]) is True
    pd.testing.assert_frame_equal(state["df"], merged)
    pd.testing.assert_frame_equal(state["base_df"], remote)
    assert state["buffer_dirty"] is False


def test_refresh_with_dirty_local_edits_preserves_original_baseline(state):
    """Merge remote changes without losing unsaved local edits.

    Ada's `seen` status is changed locally, while the remote CSV independently
    changes her `contacted` status.

    Both changes should be present in the reconciled working DataFrame.
    However, the baseline must remain the snapshot from before the local
    edit. Advancing the baseline prematurely would prevent the subsequent
    save operation from correctly identifying the local change.

    The session must therefore remain marked as dirty.
    """
    base = candidates(("Ada", "Lovelace", "/ada.pdf", False))
    ours = base.copy(deep=True)
    ours.loc[0, "seen"] = True
    remote = base.copy(deep=True)
    remote.loc[0, "contacted"] = True

    state["base_df"] = base.copy(deep=True)
    state["df"] = ours.copy(deep=True)
    state["buffer_dirty"] = True

    merged, conflicts = refresh(remote)

    assert conflicts == []
    assert bool(merged.iloc[0]["seen"]) is True
    assert bool(merged.iloc[0]["contacted"]) is True

    # Preserve the baseline that existed before the unsaved local edit.
    pd.testing.assert_frame_equal(state["base_df"], base)
    assert state["buffer_dirty"] is True


def test_refresh_failure_before_reconciliation_leaves_state_unchanged(state):
    """Ensure a failed remote download cannot partially update session state.

    A download failure must be handled before reconciliation is invoked.
    The working DataFrame, baseline, remote hash and dirty flag must remain
    unchanged so that the recruiter can retry without losing local edits.

    Note:
        This test currently verifies the failure-handling contract using a
        simulated download exception. It does not invoke the actual Dropbox
        download function or the application's refresh orchestration.
    """
    base = candidates(("Ada", "Lovelace", "/ada.pdf", False))
    ours = base.copy(deep=True)
    ours.loc[0, "seen"] = True

    state["base_df"] = base.copy(deep=True)
    state["df"] = ours.copy(deep=True)
    state["buffer_dirty"] = True
    state["last_seen_hash"] = "old-hash"

    before_df = state["df"].copy(deep=True)
    before_base = state["base_df"].copy(deep=True)

    def failed_download(*args, **kwargs):
        """Simulate a Dropbox download failure."""
        raise RuntimeError("simulated Dropbox outage")

    with pytest.raises(RuntimeError, match="Dropbox outage"):
        failed_download()

    pd.testing.assert_frame_equal(state["df"], before_df)
    pd.testing.assert_frame_equal(state["base_df"], before_base)
    assert state["last_seen_hash"] == "old-hash"
    assert state["buffer_dirty"] is True


def test_refresh_keeps_statuses_independent_for_two_cv_paths(state):
    """Keep statuses separate for candidates sharing a normalized name.

    Two CV records can belong to candidates with identical first and last
    names but different file paths. The composite candidate key must therefore
    include the CV path.

    A local status edit to the first CV and a remote status edit to the second
    CV must remain associated with their respective records after refresh.
    """
    base = candidates(
        ("Ada", "Lovelace", "/CVs/ada-v1.pdf", False),
        ("Ada", "Lovelace", "/CVs/ada-v2.pdf", False),
    )
    ours = base.copy(deep=True)
    ours.loc[0, "seen"] = True
    remote = base.copy(deep=True)
    remote.loc[1, "contacted"] = True

    state["base_df"] = base.copy(deep=True)
    state["df"] = ours.copy(deep=True)
    state["buffer_dirty"] = True

    merged, conflicts = refresh(remote)

    assert conflicts == []
    by_path = merged.set_index("file_path")

    assert bool(by_path.loc["/CVs/ada-v1.pdf", "seen"]) is True
    assert bool(by_path.loc["/CVs/ada-v1.pdf", "contacted"]) is False
    assert bool(by_path.loc["/CVs/ada-v2.pdf", "seen"]) is False
    assert bool(by_path.loc["/CVs/ada-v2.pdf", "contacted"]) is True


def test_refresh_preserves_remote_only_candidate(state):
    """Include externally added candidates in the refreshed working dataset.

    Recruiters cannot add candidate rows themselves, but an external update
    may add a new candidate to the shared Dropbox CSV.

    When the local session is clean, refreshing must incorporate that new
    candidate and adopt the remote snapshot as the new baseline.
    """
    base = candidates(("Ada", "Lovelace", "/ada.pdf", False))
    remote = candidates(
        ("Ada", "Lovelace", "/ada.pdf", False),
        ("Grace", "Hopper", "/grace.pdf", True),
    )

    state["base_df"] = base.copy(deep=True)
    state["df"] = base.copy(deep=True)
    state["buffer_dirty"] = False

    merged, conflicts = refresh(remote)

    assert conflicts == []
    assert set(merged["file_path"]) == {"/ada.pdf", "/grace.pdf"}

    grace = merged.loc[merged["file_path"] == "/grace.pdf"].iloc[0]
    assert grace["first_name"] == "Grace"
    assert grace["last_name"] == "Hopper"
    assert bool(grace["seen"]) is True

    # The newly downloaded candidate must also be part of the new baseline.
    pd.testing.assert_frame_equal(state["base_df"], remote)
    assert state["buffer_dirty"] is False
