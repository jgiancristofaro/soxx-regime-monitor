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


# ── Committed-tail restore (compute.py) ─────────────────────────────────────
# Regression for 2026-09-30: a later run got a NaN final bar, _validate dropped it,
# and the run committed last_session=09-29 over the good 09-30 data from the prior run.
from pipeline.compute import _restore_committed_tail


def test_s5_dropped_final_bar_restored_from_committed(df_full):
    fresh = df_full.iloc[:-1]
    out = _restore_committed_tail(fresh, df_full, str(df_full.index[-1].date()))
    assert out.index[-1] == df_full.index[-1]
    assert len(out) == len(df_full)
    assert list(out.columns) == list(df_full.columns)


def test_s6_nothing_restored_when_fetch_is_current(df_full):
    out = _restore_committed_tail(df_full, df_full, str(df_full.index[-1].date()))
    assert out.equals(df_full)


def test_s7_only_settled_sessions_restored(df_full):
    # history.csv holds a trailing live candle beyond the committed settled last_session
    committed_last = df_full.index[-2]
    fresh = df_full.iloc[:-3]
    out = _restore_committed_tail(fresh, df_full, str(committed_last.date()))
    assert out.index[-1] == committed_last
    assert df_full.index[-1] not in out.index


def test_s8_no_committed_state_is_noop(df_full):
    assert _restore_committed_tail(df_full, None, None).equals(df_full)
    assert _restore_committed_tail(df_full, df_full, None).equals(df_full)
