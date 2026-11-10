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


# ── yfinance trailing-NaN-row merge (pipeline/sources.py) ───────────────────
# Regression for 2026-11-09: a trailing all-NaN row on the same exchange date made yfinance's own
# merge discard the real bar, so the latest session vanished after validation.
import yfinance.utils as _yfu
import yfinance.scrapers.history as _yfh

_TZ = "America/New_York"


def _quotes(rows):
    idx = pd.DatetimeIndex([pd.Timestamp(t, tz=_TZ) for t, *_ in rows])
    data = [r[1:] for r in rows]
    return pd.DataFrame(
        data, index=idx,
        columns=["Open", "High", "Low", "Close", "Volume"],
    ).assign(**{"Dividends": 0.0, "Stock Splits": 0.0})


_GOOD = ("2026-11-09 09:30", 140.0, 142.0, 138.5, 141.2, 50e6)
_PRIOR = ("2026-11-06 09:30", 139.6, 140.2, 133.8, 136.5, 59e6)
_NAN = ("2026-11-09 21:27", np.nan, np.nan, np.nan, np.nan, np.nan)


def test_s9_patch_installed_where_yfinance_looks_it_up():
    assert getattr(_yfu.fix_Yahoo_returning_live_separate, "_nan_safe", False)
    assert _yfh.utils is _yfu


def test_s10_trailing_nan_same_date_keeps_real_bar():
    out, _ = _yfu.fix_Yahoo_returning_live_separate(_quotes([_PRIOR, _GOOD, _NAN]), "1d", _TZ, False)
    assert out["Close"].iloc[-1] == 141.2
    assert out.index[-1].date() == pd.Timestamp("2026-11-09").date()
    assert out["Close"].notna().all()


def test_s11_normal_same_date_duplicate_still_merged():
    live = ("2026-11-09 21:27", 140.0, 142.0, 138.5, 141.2, 50.5e6)
    out, _ = _yfu.fix_Yahoo_returning_live_separate(_quotes([_PRIOR, _GOOD, live]), "1d", _TZ, False)
    assert len(out) == 2 and out["Volume"].iloc[-1] == 50.5e6


def test_s12_trailing_nan_on_new_date_dropped_and_short_frames_unaffected():
    nxt = ("2026-11-10 09:30", np.nan, np.nan, np.nan, np.nan, np.nan)
    out, _ = _yfu.fix_Yahoo_returning_live_separate(_quotes([_PRIOR, _GOOD, nxt]), "1d", _TZ, False)
    assert len(out) == 2 and out["Close"].iloc[-1] == 141.2   # empty row dropped early; real bar kept
    single, _ = _yfu.fix_Yahoo_returning_live_separate(_quotes([_NAN]), "1d", _TZ, False)
    assert len(single) == 1

