"""Tests for concurrent recruiter edits to a shared candidate-status CSV.

The tests exercise three-way merging, conflict reporting, input immutability,
idempotence, and simulated concurrent writes using compare-and-swap (CAS).

Expected merge behavior:
- Changes affecting different candidates or columns are preserved.
- Identical changes do not create conflicts.
- Changes made on only one side are retained.
- Boolean values follow the merge implementation's logical-OR policy when
  both sides change the same field.
- Concurrent writers retry after a stale revision is rejected.
"""
import random
import threading

import pytest

from merge_handler import _three_way_merge
from conftest import (
    FakeDropbox, make_df, FIRST, LAST, FILE, SEEN, INT, SAVE, CONT,
    TEXT_COLS, BOOL_COLS,
)


def merge(base, ours, remote):
    """Merge local and remote DataFrames against a shared base snapshot.

    Args:
        base: DataFrame representing the original shared state.
        ours: DataFrame containing the local recruiter's edits.
        remote: DataFrame containing the latest shared state.

    Returns:
        A tuple containing the merged DataFrame and a list of conflicts.
    """
    return _three_way_merge(
        base, ours, remote, TEXT_COLS, BOOL_COLS, FIRST, LAST, FILE
    )


def value(df, path, col):
    """Return a candidate's boolean field, identified by its exact file path.

    Raises:
        AssertionError: If no candidate matches the supplied path.
    """
    rows = df.loc[df[FILE] == path, col]
    assert not rows.empty, (
        f"Candidate path {path!r} not found. "
        f"Available paths: {df[FILE].tolist()}"
    )
    return bool(rows.iloc[0])


def test_edits_on_different_candidates_are_both_kept(base_df):
    """Preserve independent edits made to two different candidates."""
    ours, remote = base_df.copy(), base_df.copy()
    ours.loc[ours[FIRST] == "Alice", SEEN] = True
    remote.loc[remote[FIRST] == "Bob", CONT] = True

    merged, conflicts = merge(base_df, ours, remote)

    assert value(merged, "/cv/Martin_Alice.pdf", SEEN)
    assert value(merged, "/cv/Durand_Bob.pdf", CONT)
    assert conflicts == []


def test_edits_on_different_columns_of_same_candidate_are_both_kept(base_df):
    """Preserve edits to different status fields of the same candidate."""
    ours, remote = base_df.copy(), base_df.copy()
    ours.loc[ours[FIRST] == "Alice", SEEN] = True
    remote.loc[remote[FIRST] == "Alice", INT] = True

    merged, conflicts = merge(base_df, ours, remote)

    assert value(merged, "/cv/Martin_Alice.pdf", SEEN)
    assert value(merged, "/cv/Martin_Alice.pdf", INT)
    assert conflicts == []


def test_same_change_on_both_sides_is_not_a_conflict(base_df):
    """Avoid reporting a conflict when both recruiters make the same edit."""
    ours, remote = base_df.copy(), base_df.copy()
    for d in (ours, remote):
        d.loc[d[FIRST] == "Alice", SAVE] = True

    merged, conflicts = merge(base_df, ours, remote)

    assert value(merged, "/cv/Martin_Alice.pdf", SAVE)
    assert conflicts == []


def test_remote_only_change_is_applied(base_df):
    """Apply a remote edit when the local DataFrame remains unchanged."""
    remote = base_df.copy()
    remote.loc[remote[FIRST] == "Chloé", SEEN] = True

    merged, _ = merge(base_df, base_df.copy(), remote)

    assert value(merged, "/cv/Bernard_Chloe.pdf", SEEN)


def test_local_only_change_survives_untouched_remote(base_df):
    """Retain a local edit when the remote state has not changed."""
    ours = base_df.copy()
    ours.loc[ours[FIRST] == "Bob", CONT] = True

    merged, conflicts = merge(base_df, ours, base_df.copy())

    assert value(merged, "/cv/Durand_Bob.pdf", CONT)
    assert conflicts == []


def test_unmark_by_one_recruiter_is_preserved(base_df):
    """Preserve an unmark when another recruiter edits a different candidate."""
    base = base_df.copy()
    base.loc[base[FIRST] == "Alice", SEEN] = True
    ours, remote = base.copy(), base.copy()
    ours.loc[ours[FIRST] == "Alice", SEEN] = False
    remote.loc[remote[FIRST] == "Bob", SEEN] = True

    merged, _ = merge(base, ours, remote)

    assert not value(merged, "/cv/Martin_Alice.pdf", SEEN)
    assert value(merged, "/cv/Durand_Bob.pdf", SEEN)


def test_conflicting_boolean_edits_are_merged_without_text_conflict(base_df):
    """Merge identical boolean unmarks without reporting a text conflict.

    Both recruiters start from a state where Alice is marked as viewed and
    independently unmark her. Because both edits agree, no conflict is expected.
    """
    base = base_df.copy(deep=True)
    base.loc[base[FIRST] == "Alice", SEEN] = True

    ours = base.copy(deep=True)
    remote = base.copy(deep=True)

    ours.loc[ours[FIRST] == "Alice", SEEN] = False
    remote.loc[remote[FIRST] == "Alice", SEEN] = False

    merged, conflicts = merge(base, ours, remote)

    assert not value(merged, "/cv/Martin_Alice.pdf", SEEN)
    assert conflicts == []


def test_merge_does_not_mutate_inputs(base_df):
    """Ensure merging leaves the base, local, and remote inputs unchanged."""
    ours, remote = base_df.copy(), base_df.copy()
    ours.loc[0, SEEN] = True
    snap = (base_df.copy(), ours.copy(), remote.copy())

    merge(base_df, ours, remote)

    for before, after in zip(snap, (base_df, ours, remote)):
        assert before.equals(after)


def test_merge_is_idempotent(base_df):
    """Ensure merging an already-merged state introduces no further changes."""
    ours, remote = base_df.copy(), base_df.copy()
    ours.loc[0, SEEN] = True
    remote.loc[1, CONT] = True

    merged, _ = merge(base_df, ours, remote)
    again, conflicts = merge(merged, merged.copy(), merged.copy())

    assert again.reset_index(drop=True).equals(merged.reset_index(drop=True))
    assert conflicts == []


def _recruiter(store, row_idx, col, errors, retries=50):
    """Simulate a recruiter editing a cell and retrying failed CAS writes.

    The recruiter reads a base snapshot, applies a local edit, merges against
    the latest shared state, and attempts a compare-and-swap write. If another
    writer has already updated the store, it retries using the latest revision.

    Any exception or exhausted retry loop is recorded in ``errors``.
    """
    try:
        base, rev = store.read()
        ours = base.copy(deep=True)
        ours.loc[row_idx, col] = True
        for _ in range(retries):
            remote, remote_rev = store.read()
            merged, _ = merge(base, ours, remote)
            if store.write(merged, remote_rev):
                return
            base = remote
            ours = merged
        errors.append(f"row {row_idx} never saved")
    except Exception as exc:  # pragma: no cover - surfaced by assertion
        errors.append(repr(exc))


@pytest.mark.parametrize("seed", range(5))
def test_many_concurrent_recruiters_lose_no_edits(seed):
    """Simulate concurrent recruiters and verify that every planned edit survives.

    Five deterministic random seeds exercise different thread execution orders
    and status-field assignments. Each recruiter updates a distinct row, while
    the in-memory store rejects writes based on stale revisions.
    """
    random.seed(seed)
    n = 12
    df = make_df([(f"First{i}", f"Last{i}", f"/cv/{i}.pdf") for i in range(n)])
    store = FakeDropbox(df)
    cols = [SEEN, INT, SAVE, CONT]
    plan = [(i, random.choice(cols)) for i in range(n)]
    errors = []

    threads = [
        threading.Thread(target=_recruiter, args=(store, i, c, errors))
        for i, c in plan
    ]
    random.shuffle(threads)
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    final, _ = store.read()
    for i, c in plan:
        assert bool(final.loc[i, c]), f"lost edit: row {i} col {c}"
    assert len(final) == n


def test_stale_write_is_rejected_by_store():
    """Reject a second write using a revision that is no longer current."""
    store = FakeDropbox(make_df([("A", "B", "/a.pdf")]))
    df, rev = store.read()
    assert store.write(df, rev) is True
    assert store.write(df, rev) is False

def test_same_person_different_cv_paths_keep_independent_statuses():
    base = make_df([
        ("Alice", "Martin", "/cv/Alice_v1.pdf"),
        ("Alice", "Martin", "/cv/Alice_v2.pdf"),
    ])

    ours = base.copy(deep=True)
    remote = base.copy(deep=True)

    ours.loc[ours[FILE] == "/cv/Alice_v1.pdf", SEEN] = True
    remote.loc[remote[FILE] == "/cv/Alice_v2.pdf", CONT] = True

    merged, conflicts = merge(base, ours, remote)

    assert len(merged) == 2
    assert value(merged, "/cv/Alice_v1.pdf", SEEN)
    assert not value(merged, "/cv/Alice_v1.pdf", CONT)

    assert not value(merged, "/cv/Alice_v2.pdf", SEEN)
    assert value(merged, "/cv/Alice_v2.pdf", CONT)

    assert conflicts == []

def test_all_recruiter_statuses_survive_merge():
    """Preserve all four status fields for an unchanged candidate identity."""
    base = make_df([
        ("Alice", "Martin", "/cv/Alice.pdf"),
        ("Bob", "Durand", "/cv/Bob.pdf"),
    ])

    # Establish distinct, non-default status combinations.
    base.loc[base[FILE] == "/cv/Alice.pdf", [SEEN, INT, SAVE, CONT]] = [
        True, False, True, False
    ]
    base.loc[base[FILE] == "/cv/Bob.pdf", [SEEN, INT, SAVE, CONT]] = [
        False, True, False, True
    ]

    ours = base.copy(deep=True)
    remote = base.copy(deep=True)

    # An unrelated edit should not disturb Alice's existing statuses.
    ours.loc[ours[FILE] == "/cv/Bob.pdf", SEEN] = True

    merged, conflicts = merge(base, ours, remote)

    assert len(merged) == 2
    assert conflicts == []

    assert value(merged, "/cv/Alice.pdf", SEEN) is True
    assert value(merged, "/cv/Alice.pdf", INT) is False
    assert value(merged, "/cv/Alice.pdf", SAVE) is True
    assert value(merged, "/cv/Alice.pdf", CONT) is False

    assert value(merged, "/cv/Bob.pdf", SEEN) is True
    assert value(merged, "/cv/Bob.pdf", INT) is True
    assert value(merged, "/cv/Bob.pdf", SAVE) is False
    assert value(merged, "/cv/Bob.pdf", CONT) is True