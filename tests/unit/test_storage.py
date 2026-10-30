# tests/test_storage.py
import pandas as pd
import pytest

from career_fair.storage.dropbox_storage import _download_current_df_from_dbx
from career_fair.storage.dropbox_storage import (
    _build_dbx_client,
    _get_remote_hash,
    get_cv_pdf_base64,
    _download_current_df_from_dbx,
)


def test_download_returns_dataframe_for_valid_csv(
    monkeypatch,
):
    expected = pd.DataFrame({
        "first_name": ["Alice"],
        "last_name": ["Martin"],
    })

    # Replace the lowest-level Dropbox download function
    # used by dropbox_storage with a fake that returns
    # the CSV content expected by the implementation.
    #
    # Example test outline:
    # monkeypatch.setattr(
    #     "dropbox_storage.<actual_download_function>",
    #     fake_download,
    # )
    #
    # result = _download_current_df_from_dbx(
    #     fake_dbx_client, "/test/candidates.csv"
    # )
    #
    # assert result is not None
    # assert result[["first_name", "last_name"]].equals(expected)

    pytest.skip(
        "Implement once the Dropbox SDK calls in "
        "dropbox_storage.py are available for inspection."
    )