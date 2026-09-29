"""
OHLCV validation tests (pipeline/sources.py _validate).

Regression for 2026-09-28: yfinance returned an incomplete final bar (NaN OHLC),
which passed validation, nulled today.id20, and crashed compute.py after the
signals write. No network calls.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "soxx_2026.csv"

import sys
sys.path.insert(0, str(ROOT))

from pipeline.sources import _validate, load_fixture
from pipeline.state_machine import compute_signals


@pytest.fixture(scope="module")
def df_full():
    return load_fixture(str(FIXTURE))


def _with_row(df, row):
    return pd.concat([df, pd.DataFrame([row], index=[df.index[-1] + pd.Timedelta(days=1)])])


def test_s1_all_nan_last_bar_dropped(df_full):
    bad = _with_row(df_full, dict(open=np.nan, high=np.nan, low=np.nan, close=np.nan, volume=np.nan))
    out = _validate(bad, "test")
    assert out.index[-1] == df_full.index[-1]
    assert len(out) == len(df_full)


def test_s2_partial_nan_last_bar_dropped(df_full):
    bad = _with_row(df_full, dict(open=np.nan, high=600.0, low=590.0, close=595.0, volume=1e6))
    out = _validate(bad, "test")
    assert out.index[-1] == df_full.index[-1]


def test_s3_nan_bar_no_longer_nulls_today_signals(df_full):
    bad = _with_row(df_full, dict(open=np.nan, high=np.nan, low=np.nan, close=np.nan, volume=np.nan))
    result = compute_signals(_validate(bad, "test"), {})
    assert result["today"]["id20"] is not None
    assert result["today"]["on20"] is not None


def test_s4_clean_data_unchanged(df_full):
    out = _validate(df_full, "test")
    assert len(out) == len(df_full)
    assert out.index.equals(df_full.index)
