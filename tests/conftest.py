# tests/conftest.py
"""Shared fixtures for the career-fair app tests.

Assumptions (adjust if your helper modules differ):
  * merge_handler / authentication_handler / dropbox_storage are importable
    from the project root (run pytest from there).
  * The Streamlit entry point is given by the APP_PATH env var
    (default: app.py).
"""
import os
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent

APP_PATH = os.environ.get(
    "APP_PATH",
    str(ROOT / "apps" / "my_app.py"),
)

FIRST, LAST, FILE = "first_name", "last_name", "file_path"
SEEN, INT, SAVE, CONT = "seen", "intend_view", "cv_saved", "contacted"
TEXT_COLS = [FIRST, LAST, FILE]
BOOL_COLS = [SEEN, INT, SAVE, CONT]
ALL_COLS = [*TEXT_COLS, *BOOL_COLS]


def make_df(rows):
    """rows: list of (first, last, file) or (first, last, file, {bool: True})."""
    out = []
    for r in rows:
        first, last, file = r[:3]
        flags = r[3] if len(r) > 3 else {}
        rec = {FIRST: first, LAST: last, FILE: file}
        rec.update({c: bool(flags.get(c, False)) for c in BOOL_COLS})
        out.append(rec)
    return pd.DataFrame(out, columns=ALL_COLS)


@pytest.fixture
def cols():
    return dict(
        TEXT_COLS=TEXT_COLS, BOOL_COLS=BOOL_COLS,
        FIRST=FIRST, LAST=LAST, FILE=FILE,
    )


@pytest.fixture
def base_df():
    return make_df([
        ("Alice", "Martin", "/cv/Martin_Alice.pdf"),
        ("Bob", "Durand", "/cv/Durand_Bob.pdf"),
        ("Chloé", "Bernard", "/cv/Bernard_Chloe.pdf"),
    ])

@pytest.fixture
def candidates():
    """Small, deterministic dataset for candidate identity tests."""
    return make_df([
        ("Alice", "Photon", "/test/Photon_Alice.pdf", 
         {"seen": True,  "cv_saved": True}),
        ("Alice", "Martin", "/test/Martin_Alice.pdf",
        {"intend_view": True}),
        ("Bob", "Durand", "/test/Durand_Bob.pdf",
         {"intend_view": True, "seen": True, "cv_saved": True}),
        ("Bob", "Neutron", "/test/Neutron_Bob.pdf",
         {"seen": True, "cv_saved": True}),
        ("Ada", "Quark", "/test/Quark_Ada.pdf",
         {"seen": True}),
        ("Camille", "Orion", "/test/Orion_Camille.pdf",
         {"seen": True}),
        ("Charlie", "Nova", "/test/Nova_Charlie.pdf",
         {"seen": True, "intend_view": True}),
    ])

class FakeDropbox:
    """In-memory CSV store with a content hash and compare-and-swap writes,
    mimicking Dropbox's rev-based conflict detection."""

    def __init__(self, df):
        self._lock = threading.Lock()
        self.df = df.copy(deep=True)
        self.rev = 1

    def read(self):
        with self._lock:
            return self.df.copy(deep=True), self.rev

    def write(self, df, expected_rev):
        """Returns True on success, False if someone else wrote first."""
        with self._lock:
            if expected_rev != self.rev:
                return False
            self.df = df.copy(deep=True)
            self.rev += 1
            return True
