"""
State machine golden-record tests (§6.4).
Trade dates and prices must match exactly.

Fixtures:
  result       — hybrid arm mode (production default); used for N1-N8 hybrid assertions.
  result_mode_a — Mode-A arm only; used for the trade golden record and backtest regression.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "soxx_2026.csv"

import sys
sys.path.insert(0, str(ROOT))

from pipeline.sources import load_fixture
from pipeline.state_machine import (
    ARM_Z, COLLAPSE_GATE, compute_signals, _compute_derived
)


@pytest.fixture(scope="module")
def df_full():
    return load_fixture(str(FIXTURE))


@pytest.fixture(scope="module")
def result(df_full):
    """Hybrid arm mode (production default). Used for N1–N8 assertions."""
    manual = {"iv30": 0.46, "iv30_asof": "2026-07-01"}
    return compute_signals(df_full, manual)


@pytest.fixture(scope="module")
def result_mode_a(df_full):
    """Mode-A arm only. Used for trade golden record + backtest regression."""
    manual = {"iv30": 0.46, "iv30_asof": "2026-07-01"}
    return compute_signals(df_full, manual, arm_mode="A")


# ── Helper ────────────────────────────────────────────────────────────────────

def _band_state_on(result: dict, date_str: str) -> str | None:
    target = pd.Timestamp(date_str)
    for band in result["bands"]:
        start = pd.Timestamp(band["start"])
        end = pd.Timestamp(band["end"]) if band["end"] else pd.Timestamp("2099-12-31")
        if start <= target <= end:
            return band["state"]
    return None


# ── Trade golden record (Mode-A regression) ──────────────────────────────────

def test_trade_count(result_mode_a):
    """Exactly 3 trades in the Mode-A golden record."""
    assert len(result_mode_a["trades"]) == 3, (
        f"Expected 3 trades, got: {result_mode_a['trades']}"
    )


def test_trade_1_exit_feb05(result_mode_a):
    t = result_mode_a["trades"][0]
    assert t["date"] == "2026-02-05"
    assert t["action"] == "EXIT"
    assert t["price"] == pytest.approx(330.83, abs=0.01)
    assert t["reason"] == "exec-into-strength"


def test_trade_2_reenter_feb06(result_mode_a):
    t = result_mode_a["trades"][1]
    assert t["date"] == "2026-02-06"
    assert t["action"] == "REENTER"
    assert t["price"] == pytest.approx(348.51, abs=0.01)
    assert t["reason"] == "disarm"


def test_trade_3_exit_jun18(result_mode_a):
    t = result_mode_a["trades"][2]
    assert t["date"] == "2026-06-18"
    assert t["action"] == "EXIT"
    assert t["price"] == pytest.approx(639.45, abs=0.01)
    assert t["reason"] == "exec-into-strength"


# ── State assertions on specific dates (Mode-A regression) ───────────────────

def test_jul01_state_fired(result_mode_a):
    """System is OUT (flat) as of Jul 1, 2026."""
    assert result_mode_a["state"]["machine"] == "EXIT"
    assert result_mode_a["last_session"] == "2026-07-01"


def test_jun22_state_fired(result_mode_a):
    """Jun 22 is OUT: EXIT already happened Jun 18 (same-day arm+exit)."""
    state = _band_state_on(result_mode_a, "2026-06-22")
    assert state == "EXIT", f"Expected EXIT on 2026-06-22, got: {state}"


def test_jun09_10_cancel_no_trade(result_mode_a):
    """Jun 9–10 MONITOR episode cancels without a trade."""
    trades_jun9_to_17 = [
        t for t in result_mode_a["trades"]
        if "2026-06-09" <= t["date"] <= "2026-06-17"
    ]
    assert len(trades_jun9_to_17) == 0, (
        f"Expected no trades Jun 9-17, found: {trades_jun9_to_17}"
    )


def test_accum_never_coexists_with_exit(result_mode_a):
    """ACCUM overlay must never coexist with an EXIT trade."""
    accum_bands = [b for b in result_mode_a["bands"] if b["state"] == "ACCUM"]
    for trade in result_mode_a["trades"]:
        if trade["action"] == "EXIT":
            trade_date = pd.Timestamp(trade["date"])
            for band in accum_bands:
                start = pd.Timestamp(band["start"])
                end = pd.Timestamp(band["end"]) if band["end"] else pd.Timestamp("2099-12-31")
                assert not (start <= trade_date <= end), (
                    f"EXIT trade on {trade['date']} overlaps ACCUM band {band}"
                )


def test_position_is_zero_on_jul01(result_mode_a):
    """Strategy position on 2026-07-01 (EXIT) should be 0."""
    assert result_mode_a["state"]["position_multiplier"] == 0.0


# ── Backtest regression (Mode-A) ──────────────────────────────────────────────

def test_backtest_equity_keys(result_mode_a):
    assert "equity_strategy" in result_mode_a["series"]
    assert "equity_bh" in result_mode_a["series"]


def test_backtest_strategy_vs_bh(result_mode_a):
    """Mode-A strategy ≈ +72% net of slippage; B&H ≈ +75% (backtest from Jan 20)."""
    s_vals = [v for v in result_mode_a["series"]["equity_strategy"] if v is not None]
    bh_vals = [v for v in result_mode_a["series"]["equity_bh"] if v is not None]
    assert s_vals, "No strategy equity values"
    assert bh_vals, "No B&H equity values"

    strat_return = s_vals[-1] - 1
    bh_return = bh_vals[-1] - 1

    assert strat_return == pytest.approx(0.72, abs=0.05), (
        f"Mode-A strategy return {strat_return:.3f} outside ±5% of 72%"
    )
    assert bh_return == pytest.approx(0.75, abs=0.05), (
        f"B&H return {bh_return:.3f} outside ±5% of 75%"
    )


# ── Band structure assertions (Mode-A regression) ────────────────────────────

def test_bands_start_with_warmup(result_mode_a):
    bands = result_mode_a["bands"]
    assert bands[0]["state"] == "WARMUP", f"First band should be WARMUP: {bands[0]}"
    assert bands[0]["start"] == "2026-01-02"


def test_bands_last_is_ongoing(result_mode_a):
    bands = result_mode_a["bands"]
    assert bands[-1]["end"] is None, "Last band should be ongoing (end=null)"


def test_bands_contiguous(result_mode_a):
    """Bands must be contiguous — no gaps from one trading day to the next."""
    bands = result_mode_a["bands"]
    for i in range(1, len(bands)):
        prev_end = pd.Timestamp(bands[i - 1]["end"])
        curr_start = pd.Timestamp(bands[i]["start"])
        assert prev_end <= curr_start, (
            f"Gap between band {i-1} (end {bands[i-1]['end']}) and band {i} (start {bands[i]['start']})"
        )


# ── N1–N8: hybrid assertions ──────────────────────────────────────────────────

def test_n1_hybrid_arm_dates_include(df_full):
    """N1: Hybrid arm condition fires on 2026-02-04, 2026-06-09, 2026-06-18."""
    df_d = _compute_derived(df_full.copy())
    ytd = df_d[df_d.index >= pd.Timestamp("2026-01-01")]

    armed_dates = set()
    for dt, row in ytd.iterrows():
        id20 = float(row["id20"]) if not np.isnan(row["id20"]) else 0.0
        ret20 = float(row["ret20"]) if not np.isnan(row["ret20"]) else 0.0
        id20_z = float(row["id20_z"]) if not np.isnan(row["id20_z"]) else float("nan")
        id20_max60 = float(row["id20_max60"]) if not np.isnan(row["id20_max60"]) else 0.0
        armA = (id20 < 0 and ret20 > 0.02) or (id20 < -0.03)
        armB = (not np.isnan(id20_z)) and id20_z < ARM_Z and ret20 > 0.02
        if armB or (armA and (id20_max60 - id20) >= COLLAPSE_GATE):
            armed_dates.add(dt.strftime("%Y-%m-%d"))

    for required in ["2026-02-04", "2026-06-09", "2026-06-18"]:
        assert required in armed_dates, (
            f"N1: Expected {required} in hybrid arm dates; got {sorted(armed_dates)}"
        )


def test_n2_id20_z_feb04(df_full):
    """N2: id20_z on 2026-02-04 ≈ −1.32 (±0.05)."""
    df_d = _compute_derived(df_full.copy())
    z = float(df_d.loc[pd.Timestamp("2026-02-04"), "id20_z"])
    assert z == pytest.approx(-1.32, abs=0.05), (
        f"N2: id20_z on 2026-02-04 = {z:.4f}, expected ≈ -1.32"
    )


def test_n3_id20_z_jun18_modeb_not_arm(df_full):
    """N3: id20_z on 2026-06-18 ≈ −0.62 — Mode B alone must NOT arm (z > −1.0)."""
    df_d = _compute_derived(df_full.copy())
    row = df_d.loc[pd.Timestamp("2026-06-18")]
    z = float(row["id20_z"])
    ret20 = float(row["ret20"])
    armB = (not np.isnan(z)) and z < ARM_Z and ret20 > 0.02

    assert z == pytest.approx(-0.62, abs=0.05), (
        f"N3: id20_z on 2026-06-18 = {z:.4f}, expected ≈ -0.62"
    )
    assert not armB, (
        f"N3: Mode B must NOT arm on 2026-06-18 (z={z:.4f} > ARM_Z={ARM_Z})"
    )


def test_n4_id20_z_jul01(df_full):
    """N4: id20_z on 2026-07-01 ≈ −1.61 (±0.05)."""
    df_d = _compute_derived(df_full.copy())
    z = float(df_d.loc[pd.Timestamp("2026-07-01"), "id20_z"])
    assert z == pytest.approx(-1.61, abs=0.05), (
        f"N4: id20_z on 2026-07-01 = {z:.4f}, expected ≈ -1.61"
    )


def test_n5_jun04_riskon_high_id20(result):
    """N5: 2026-06-04 is RISK_ON with id20 ≈ +10.9% — no gap protection claimed."""
    state = _band_state_on(result, "2026-06-04")
    assert state == "RISK_ON", f"N5: Expected RISK_ON on 2026-06-04, got {state}"

    df_d = _compute_derived(load_fixture(str(FIXTURE)).copy())
    id20 = float(df_d.loc[pd.Timestamp("2026-06-04"), "id20"])
    assert id20 == pytest.approx(0.109, abs=0.005), (
        f"N5: id20 on 2026-06-04 = {id20:.4f}, expected ≈ +10.9%"
    )


def test_n6_same_day_arm_exit_jun18(result):
    """N6: Jun 18 arms AND exits in one session (same-day arm+exit) at close 639.45."""
    history = result["history"]
    jun18 = [h for h in history if h["date"] == "2026-06-18"]

    states_on_jun18 = {h["state"] for h in jun18}
    assert "MONITOR" in states_on_jun18, "N6: Jun 18 should show MONITOR in history"
    assert "EXIT" in states_on_jun18, "N6: Jun 18 should show EXIT in history"

    exit_entry = next(h for h in jun18 if h["state"] == "EXIT")
    assert exit_entry["price"] == pytest.approx(639.45, abs=0.01), (
        f"N6: EXIT price on Jun 18 = {exit_entry['price']}, expected 639.45"
    )


def test_n7_modea_false_arm_rate_h2_2025(df_full):
    """N7: Mode-A fires on exactly 33 of 128 sessions in H2-2025."""
    df_d = _compute_derived(df_full.copy())
    h2 = df_d[
        (df_d.index >= pd.Timestamp("2025-07-01"))
        & (df_d.index <= pd.Timestamp("2025-12-31"))
    ]
    armed = sum(
        1 for _, r in h2.iterrows()
        if (float(r["id20"]) < 0 and float(r["ret20"]) > 0.02)
        or float(r["id20"]) < -0.03
    )
    assert len(h2) == 128, f"N7: Expected 128 H2-2025 sessions, got {len(h2)}"
    assert armed == 33, (
        f"N7: Mode-A armed on {armed} of {len(h2)} H2-2025 sessions, expected 33"
    )


def test_n8_hybrid_backtest_near_modea(result, result_mode_a):
    """N8: Hybrid backtest return (net of 5bps/side) is within ±5pts of Mode-A result."""
    def _strat_return(r):
        vals = [v for v in r["series"]["equity_strategy"] if v is not None]
        return vals[-1] - 1

    strat_h = _strat_return(result)
    strat_a = _strat_return(result_mode_a)

    assert strat_h == pytest.approx(strat_a, abs=0.05), (
        f"N8: Hybrid return {strat_h:.3f} deviates more than ±5pts from "
        f"Mode-A return {strat_a:.3f}"
    )
    # Position must be 0 (EXIT) on 2026-07-01
    assert result["state"]["machine"] == "EXIT", (
        f"N8: Expected EXIT on 2026-07-01, got {result['state']['machine']}"
    )
    assert result["state"]["position_multiplier"] == 0.0
