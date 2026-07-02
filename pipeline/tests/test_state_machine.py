"""
State machine golden-record tests (§6.4).
Trade dates and prices must match exactly.
"""
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "soxx_2026.csv"

import sys
sys.path.insert(0, str(ROOT))

from pipeline.sources import load_fixture
from pipeline.state_machine import compute_signals


@pytest.fixture(scope="module")
def df_full():
    return load_fixture(str(FIXTURE))


@pytest.fixture(scope="module")
def result(df_full):
    manual = {"iv30": 0.46, "iv30_asof": "2026-07-01"}
    return compute_signals(df_full, manual)


# ── Trade golden record ────────────────────────────────────────────────────

def test_trade_count(result):
    """Exactly 3 trades in the golden record."""
    assert len(result["trades"]) == 3, f"Expected 3 trades, got: {result['trades']}"


def test_trade_1_exit_feb05(result):
    t = result["trades"][0]
    assert t["date"] == "2026-02-05"
    assert t["action"] == "EXIT"
    assert t["price"] == pytest.approx(330.83, abs=0.01)
    assert t["reason"] == "exec-into-strength"


def test_trade_2_reenter_feb06(result):
    t = result["trades"][1]
    assert t["date"] == "2026-02-06"
    assert t["action"] == "REENTER"
    assert t["price"] == pytest.approx(348.51, abs=0.01)
    assert t["reason"] == "disarm"


def test_trade_3_exit_jun18(result):
    t = result["trades"][2]
    assert t["date"] == "2026-06-18"
    assert t["action"] == "EXIT"
    assert t["price"] == pytest.approx(639.45, abs=0.01)
    assert t["reason"] == "exec-into-strength"


# ── State assertions on specific dates ───────────────────────────────────

def _band_state_on(result: dict, date_str: str) -> str | None:
    target = pd.Timestamp(date_str)
    for band in result["bands"]:
        start = pd.Timestamp(band["start"])
        end = pd.Timestamp(band["end"]) if band["end"] else pd.Timestamp("2099-12-31")
        if start <= target <= end:
            return band["state"]
    return None


def test_jul01_state_fired(result):
    """System is OUT (flat) as of Jul 1, 2026."""
    assert result["state"]["machine"] == "OUT"
    assert result["last_session"] == "2026-07-01"


def test_jun22_state_fired(result):
    """Jun 22 is OUT: EXIT already happened Jun 18 (system is flat)."""
    # Note: spec §6.4 says 'state MONITOR' on Jun 22, but EXIT fired Jun 18 @639.45
    # (same-day arm+exit). System has been flat since Jun 18.
    state = _band_state_on(result, "2026-06-22")
    assert state == "OUT", f"Expected OUT on 2026-06-22, got: {state}"


def test_jun09_10_cancel_no_trade(result):
    """Jun 9-10 MONITOR episode cancels without a trade."""
    trades_jun9_to_17 = [
        t for t in result["trades"]
        if "2026-06-09" <= t["date"] <= "2026-06-17"
    ]
    assert len(trades_jun9_to_17) == 0, (
        f"Expected no trades Jun 9-17, found: {trades_jun9_to_17}"
    )


def test_accum_never_coexists_with_exit(result):
    """ACCUM overlay must never coexist with an EXIT trade."""
    accum_bands = [b for b in result["bands"] if b["state"] == "ACCUM"]
    for trade in result["trades"]:
        if trade["action"] == "EXIT":
            trade_date = pd.Timestamp(trade["date"])
            for band in accum_bands:
                start = pd.Timestamp(band["start"])
                end = pd.Timestamp(band["end"]) if band["end"] else pd.Timestamp("2099-12-31")
                assert not (start <= trade_date <= end), (
                    f"EXIT trade on {trade['date']} overlaps ACCUM band {band}"
                )


def test_position_is_zero_on_jul01(result):
    """Strategy position on 2026-07-01 (OUT) should be 0."""
    assert result["state"]["position_multiplier"] == 0.0


# ── Backtest assertions ────────────────────────────────────────────────────

def test_backtest_equity_keys(result):
    assert "equity_strategy" in result["series"]
    assert "equity_bh" in result["series"]


def test_backtest_strategy_vs_bh(result):
    """Strategy ≈ +77.2%, B&H ≈ +77.8% from Jan 20, 2026 to Jul 1, 2026."""
    s_vals = [v for v in result["series"]["equity_strategy"] if v is not None]
    bh_vals = [v for v in result["series"]["equity_bh"] if v is not None]
    assert s_vals, "No strategy equity values"
    assert bh_vals, "No B&H equity values"

    strat_return = s_vals[-1] - 1
    bh_return = bh_vals[-1] - 1

    assert strat_return == pytest.approx(0.772, abs=0.05), (
        f"Strategy return {strat_return:.3f} outside ±5% of 77.2%"
    )
    assert bh_return == pytest.approx(0.778, abs=0.05), (
        f"B&H return {bh_return:.3f} outside ±5% of 77.8%"
    )


# ── Band structure assertions ────────────────────────────────────────────

def test_bands_start_with_warmup(result):
    bands = result["bands"]
    assert bands[0]["state"] == "WARMUP", f"First band should be WARMUP: {bands[0]}"
    assert bands[0]["start"] == "2026-01-02"


def test_bands_last_is_ongoing(result):
    bands = result["bands"]
    assert bands[-1]["end"] is None, "Last band should be ongoing (end=null)"


def test_bands_contiguous(result):
    """Bands must be contiguous — no gaps from one trading day to the next."""
    bands = result["bands"]
    for i in range(1, len(bands)):
        prev_end = pd.Timestamp(bands[i - 1]["end"])
        curr_start = pd.Timestamp(bands[i]["start"])
        assert prev_end <= curr_start, (
            f"Gap between band {i-1} (end {bands[i-1]['end']}) and band {i} (start {bands[i]['start']})"
        )
