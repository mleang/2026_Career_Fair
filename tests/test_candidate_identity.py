# tests/test_candidate_identity.py
"""Robust tests for candidate identity, key generation and schema normalization.

Expected identity contract:
- First and last names are normalized using _norm().
- Name normalization is case-insensitive, accent-insensitive and
  whitespace-insensitive.
- File paths are stripped but remain case-sensitive.
- Keys are deterministic and unambiguous (JSON serialization).
- Missing identity fields are represented consistently.
- _ensure_schema() preserves valid candidate rows and normalizes status fields.
"""

import json

import numpy as np
import pandas as pd
import pytest

from merge_handler import _key, _norm, _ensure_schema


FIRST = "first_name"
LAST = "last_name"
FILE = "file_path"

BOOL_COLS = ["seen", "intend_view", "cv_saved", "contacted"]
TEXT_COLS = [FIRST, LAST, FILE]


# ---------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------

@pytest.fixture
def base_df():
    """Small representative candidate DataFrame."""
    return pd.DataFrame([
        {
            FIRST: "Alice",
            LAST: "Martin",
            FILE: "/cv/Martin_Alice_CV.pdf",
            "seen": True,
            "intend_view": False,
            "cv_saved": False,
            "contacted": False,
        },
        {
            FIRST: "Bob",
            LAST: "Dupont",
            FILE: "/cv/Dupont_Bob_CV.pdf",
            "seen": False,
            "intend_view": True,
            "cv_saved": True,
            "contacted": False,
        },
        {
            FIRST: "Claire",
            LAST: "Durand",
            FILE: "/cv/Durand_Claire_CV.pdf",
            "seen": True,
            "intend_view": True,
            "cv_saved": False,
            "contacted": True,
        },
    ])


def make_candidates(rows):
    """Build a candidate DataFrame."""
    return pd.DataFrame(rows)


def candidate_keys(df):
    """Return keys in DataFrame row order."""
    return list(_key(df, FIRST, LAST, FILE))


def make_identity(first, last, file_path):
    """Build one candidate identity record."""
    return {
        FIRST: first,
        LAST: last,
        FILE: file_path,
    }


# ---------------------------------------------------------------------
# _norm(): text normalization
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("  André   Martin ", "Andre Martin"),
        ("ALICE MARTIN", "Alice Martin"),
        ("Alice    Martin", "Alice Martin"),
        (" Alice Martin ", "Alice Martin"),
        ("ÉLODIE", "elodie"),
        ("François", "Francois"),
        ("José", "Jose"),
        ("Müller", "Muller"),
    ],
)
def test_norm_handles_accents_case_and_whitespace(left, right):
    assert _norm(left) == _norm(right)


@pytest.mark.parametrize("value", [None, np.nan, pd.NA])
def test_norm_converts_missing_values_to_empty_string(value):
    assert _norm(value) == ""


def test_norm_returns_string():
    result = _norm(" Alice Martin ")
    assert isinstance(result, str)


def test_norm_collapses_tabs_and_newlines():
    assert _norm("Alice\t\nMartin") == _norm("Alice Martin")


def test_norm_does_not_remove_meaningful_characters():
    assert _norm("Jean-Pierre") == "jean-pierre"
    assert _norm("O'Connor") == "o'connor"


def test_norm_handles_unicode_composed_and_decomposed_characters():
    composed = "é"
    decomposed = "e\u0301"

    assert _norm(composed) == _norm(decomposed)


# ---------------------------------------------------------------------
# _key(): key generation and identity correctness
# ---------------------------------------------------------------------

def test_key_returns_one_key_per_dataframe_row(base_df):
    keys = _key(base_df, FIRST, LAST, FILE)

    assert isinstance(keys, pd.Series)
    assert len(keys) == len(base_df)


def test_key_is_stable_for_same_candidate(base_df):
    full_keys = _key(base_df, FIRST, LAST, FILE)
    individual_key = _key(base_df.iloc[[0]], FIRST, LAST, FILE)

    assert full_keys.iloc[0] == individual_key.iloc[0]


def test_key_is_deterministic(base_df):
    first_run = _key(base_df, FIRST, LAST, FILE)
    second_run = _key(base_df.copy(), FIRST, LAST, FILE)

    pd.testing.assert_series_equal(first_run, second_run)


def test_key_distinguishes_candidates(base_df):
    keys = _key(base_df, FIRST, LAST, FILE)

    assert keys.nunique() == len(base_df)


def test_key_normalizes_name_accents_and_internal_spaces():
    df = make_candidates([
        make_identity("André", "Martin", "/cv/a.pdf"),
        make_identity("Andre", "Martin", "/cv/a.pdf"),
        make_identity("ANDRE", "  Martin  ", "/cv/a.pdf"),
    ])

    keys = candidate_keys(df)

    assert keys[0] == keys[1] == keys[2]


def test_key_does_not_collide_when_fields_contain_delimiter():
    df = make_candidates([
        make_identity("A||B", "C", "/cv/one.pdf"),
        make_identity("A", "B||C", "/cv/one.pdf"),
    ])

    keys = candidate_keys(df)

    assert keys[0] != keys[1]


def test_key_serialization_preserves_field_boundaries():
    """JSON encoding must preserve the three individual identity fields."""
    df = make_candidates([
        make_identity("A||B", "C", "/cv/one.pdf"),
    ])

    key = _key(df, FIRST, LAST, FILE).iloc[0]
    decoded = json.loads(key)

    assert decoded == ["a||b", "c", "/cv/one.pdf"]


def test_key_preserves_dataframe_index():
    df = make_candidates([
        make_identity("Alice", "Martin", "/cv/alice.pdf"),
        make_identity("Bob", "Dupont", "/cv/bob.pdf"),
    ])
    df.index = [10, 30]

    keys = _key(df, FIRST, LAST, FILE)

    assert keys.index.equals(df.index)


def test_key_preserves_non_consecutive_and_string_indexes():
    df = make_candidates([
        make_identity("Alice", "Martin", "/cv/alice.pdf"),
        make_identity("Bob", "Dupont", "/cv/bob.pdf"),
    ])
    df.index = ["candidate_a", "candidate_b"]

    keys = _key(df, FIRST, LAST, FILE)

    assert keys.index.tolist() == ["candidate_a", "candidate_b"]


@pytest.mark.parametrize(
    ("first", "last", "path"),
    [
        (None, "Martin", "/cv/one.pdf"),
        (np.nan, "Martin", "/cv/one.pdf"),
        (pd.NA, "Martin", "/cv/one.pdf"),
        ("Alice", None, "/cv/one.pdf"),
        ("Alice", np.nan, "/cv/one.pdf"),
        ("Alice", pd.NA, "/cv/one.pdf"),
    ],
)
def test_key_handles_missing_name_components(first, last, path):
    df = make_candidates([make_identity(first, last, path)])

    keys = _key(df, FIRST, LAST, FILE)

    assert len(keys) == 1
    assert isinstance(keys.iloc[0], str)


def test_missing_names_with_different_paths_remain_distinct():
    df = make_candidates([
        make_identity(None, "Martin", "/cv/one.pdf"),
        make_identity(np.nan, "Martin", "/cv/two.pdf"),
        make_identity(pd.NA, "Martin", "/cv/three.pdf"),
    ])

    keys = candidate_keys(df)

    assert len(keys) == 3
    assert len(set(keys)) == 3


def test_missing_values_are_equivalent_in_key_generation():
    df = make_candidates([
        make_identity(None, "Martin", "/cv/one.pdf"),
        make_identity(np.nan, "Martin", "/cv/one.pdf"),
        make_identity(pd.NA, "Martin", "/cv/one.pdf"),
    ])

    keys = candidate_keys(df)

    assert keys[0] == keys[1] == keys[2]


def test_duplicate_candidate_keys_are_detectable():
    df = make_candidates([
        make_identity("Alice", "Martin", "/cv/alice.pdf"),
        make_identity("Alice", "Martin", "/cv/alice.pdf"),
    ])

    keys = _key(df, FIRST, LAST, FILE)

    assert keys.iloc[0] == keys.iloc[1]
    assert keys.duplicated().tolist() == [False, True]


def test_same_name_with_different_file_paths_is_not_a_duplicate():
    df = make_candidates([
        make_identity("Alex", "Martin", "/cv/alex_1.pdf"),
        make_identity("Alex", "Martin", "/cv/alex_2.pdf"),
    ])

    keys = candidate_keys(df)

    assert keys[0] != keys[1]


def test_empty_file_paths_are_handled_consistently():
    df = make_candidates([
        make_identity("Alice", "Martin", ""),
        make_identity("Alice", "Martin", ""),
    ])

    keys = candidate_keys(df)

    assert keys[0] == keys[1]


def test_missing_file_paths_are_handled_consistently():
    df = make_candidates([
        make_identity("Alice", "Martin", None),
        make_identity("Alice", "Martin", np.nan),
        make_identity("Alice", "Martin", pd.NA),
    ])

    keys = candidate_keys(df)

    assert keys[0] == keys[1] == keys[2]


def test_file_path_whitespace_is_stripped():
    df = make_candidates([
        make_identity("Alice", "Martin", " /cv/alice.pdf "),
        make_identity("Alice", "Martin", "/cv/alice.pdf"),
    ])

    keys = candidate_keys(df)

    assert keys[0] == keys[1]


def test_file_path_case_remains_significant():
    """Current identity contract treats file paths as case-sensitive."""
    df = make_candidates([
        make_identity("Alice", "Martin", "/cv/Alice.pdf"),
        make_identity("Alice", "Martin", "/cv/alice.pdf"),
    ])

    keys = candidate_keys(df)

    assert keys[0] != keys[1]


def test_key_generation_does_not_modify_input(base_df):
    original = base_df.copy(deep=True)

    _key(base_df, FIRST, LAST, FILE)

    pd.testing.assert_frame_equal(base_df, original)


def test_key_handles_empty_dataframe():
    df = pd.DataFrame(columns=TEXT_COLS)

    keys = _key(df, FIRST, LAST, FILE)

    assert isinstance(keys, pd.Series)
    assert keys.empty


# ---------------------------------------------------------------------
# _ensure_schema(): schema and candidate-row behavior
# ---------------------------------------------------------------------

def test_ensure_schema_preserves_existing_candidate_rows(base_df):
    result = _ensure_schema(
        base_df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert len(result) == len(base_df)
    assert result[FIRST].tolist() == base_df[FIRST].tolist()
    assert result[LAST].tolist() == base_df[LAST].tolist()
    assert result[FILE].tolist() == base_df[FILE].tolist()


def test_ensure_schema_returns_expected_columns(base_df):
    result = _ensure_schema(
        base_df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert result.columns.tolist() == TEXT_COLS + BOOL_COLS


def test_ensure_schema_handles_missing_boolean_columns(base_df):
    incomplete = base_df.drop(columns=["contacted"])

    result = _ensure_schema(
        incomplete,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert "contacted" in result.columns
    assert result["contacted"].eq(False).all()
    assert len(result) == len(base_df)


def test_ensure_schema_adds_missing_text_columns():
    df = pd.DataFrame({
        FIRST: ["Alice"],
        LAST: ["Martin"],
    })

    result = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert FILE in result.columns
    assert result[FILE].tolist() == [""]
    assert len(result) == 1


def test_ensure_schema_adds_all_missing_boolean_columns():
    df = pd.DataFrame({
        FIRST: ["Alice"],
        LAST: ["Martin"],
        FILE: ["/cv/alice.pdf"],
    })

    result = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    for column in BOOL_COLS:
        assert column in result.columns
        assert result[column].tolist() == [False]
        assert result[column].dtype == bool


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("Y", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("N", False),
        (None, False),
        (np.nan, False),
        (pd.NA, False),
        ("unknown", False),
        ("", False),
    ],
)
def test_ensure_schema_normalizes_boolean_values(value, expected):
    df = pd.DataFrame({
        FIRST: ["Alice"],
        LAST: ["Martin"],
        FILE: ["/cv/alice.pdf"],
        "seen": [value],
    })

    result = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert result.loc[0, "seen"] is expected or (
        bool(result.loc[0, "seen"]) == expected
    )
    assert result["seen"].dtype == bool


def test_ensure_schema_preserves_existing_status_values(base_df):
    result = _ensure_schema(
        base_df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    for column in BOOL_COLS:
        assert result[column].tolist() == base_df[column].tolist()
        assert result[column].dtype == bool


def test_ensure_schema_converts_text_missing_values_to_empty_strings():
    df = pd.DataFrame({
        FIRST: [None, np.nan, pd.NA],
        LAST: ["Martin", "Martin", "Martin"],
        FILE: ["/cv/one.pdf", "/cv/two.pdf", "/cv/three.pdf"],
    })

    result = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert result[FIRST].tolist() == ["", "", ""]


def test_ensure_schema_drops_rows_without_any_identity_field():
    df = pd.DataFrame([
        make_identity("", "", ""),
        make_identity(None, None, None),
        make_identity("Alice", "", ""),
        make_identity("", "Martin", ""),
        make_identity("", "", "/cv/one.pdf"),
    ])

    result = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert len(result) == 3
    assert result[FIRST].tolist() == ["Alice", "", ""]
    assert result[LAST].tolist() == ["", "Martin", ""]


def test_ensure_schema_preserves_rows_with_missing_names_but_valid_path():
    df = pd.DataFrame([
        make_identity(None, None, "/cv/one.pdf"),
        make_identity("Alice", None, "/cv/two.pdf"),
    ])

    result = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert len(result) == 2


def test_ensure_schema_drops_extra_columns(base_df):
    df = base_df.copy()
    df["temporary_column"] = "temporary"
    df["unrelated_metadata"] = 123

    result = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert "temporary_column" not in result.columns
    assert "unrelated_metadata" not in result.columns


def test_ensure_schema_does_not_modify_input(base_df):
    original = base_df.copy(deep=True)

    _ensure_schema(
        base_df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    pd.testing.assert_frame_equal(base_df, original)


def test_ensure_schema_preserves_original_index():
    df = pd.DataFrame([
        make_identity("Alice", "Martin", "/cv/alice.pdf"),
        make_identity("Bob", "Dupont", "/cv/bob.pdf"),
    ], index=[10, 30])

    result = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert result.index.tolist() == [10, 30]


def test_ensure_schema_handles_empty_dataframe():
    df = pd.DataFrame(columns=TEXT_COLS)

    result = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    assert result.empty
    assert result.columns.tolist() == TEXT_COLS + BOOL_COLS


def test_ensure_schema_returns_boolean_dtypes(base_df):
    result = _ensure_schema(
        base_df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    for column in BOOL_COLS:
        assert result[column].dtype == bool


# ---------------------------------------------------------------------
# Combined identity/schema regression tests
# ---------------------------------------------------------------------

def test_schema_normalization_does_not_change_candidate_keys(base_df):
    """Schema normalization should preserve keys for valid existing records."""
    before = _key(base_df, FIRST, LAST, FILE)

    normalized = _ensure_schema(
        base_df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    after = _key(normalized, FIRST, LAST, FILE)

    assert before.tolist() == after.tolist()


def test_candidate_statuses_remain_attached_to_their_identity(base_df):
    """Normalization must not reorder or swap candidate status values."""
    original = base_df.copy(deep=True)

    result = _ensure_schema(
        base_df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    original_keys = _key(original, FIRST, LAST, FILE)
    result_keys = _key(result, FIRST, LAST, FILE)

    original_by_key = {
        key: tuple(original.loc[index, BOOL_COLS].tolist())
        for index, key in original_keys.items()
    }

    result_by_key = {
        key: tuple(result.loc[index, BOOL_COLS].tolist())
        for index, key in result_keys.items()
    }

    assert original_by_key == result_by_key


def test_duplicate_identity_keys_do_not_silently_become_unique():
    """Duplicate identities should remain detectable after schema normalization."""
    df = pd.DataFrame([
        {
            **make_identity("Alice", "Martin", "/cv/alice.pdf"),
            "seen": True,
        },
        {
            **make_identity("Alice", "Martin", "/cv/alice.pdf"),
            "seen": False,
        },
    ])

    normalized = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    keys = _key(normalized, FIRST, LAST, FILE)

    assert keys.duplicated().tolist() == [False, True]


def test_different_cv_paths_keep_candidate_records_distinct_after_schema():
    df = pd.DataFrame([
        {
            **make_identity("Alex", "Martin", "/cv/alex_1.pdf"),
            "seen": True,
        },
        {
            **make_identity("Alex", "Martin", "/cv/alex_2.pdf"),
            "seen": False,
        },
    ])

    normalized = _ensure_schema(
        df,
        TEXT_COLS,
        BOOL_COLS,
        FIRST,
        LAST,
        FILE,
    )

    keys = _key(normalized, FIRST, LAST, FILE)

    assert keys.nunique() == 2
    assert normalized["seen"].tolist() == [True, False]
