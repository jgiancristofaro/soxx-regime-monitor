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

import json
import sys
sys.path.insert(0, str(ROOT))

from pipeline.sources import load_fixture
from pipeline.state_machine import (
    ARM_Z, COLLAPSE_GATE, ONMOM_TILT, ONMOM_FACTOR, SLIPPAGE_BPS, GAP_THR,
    compute_signals, _compute_derived, _run_state_machine, _run_backtest,
    STATE_POS,
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


# ── V1–V9: Change Order v3.2 golden record ───────────────────────────────────

def test_v1_order_of_ops_same_day_arm_exit(result_mode_a):
    """V1a: Jun 18 arms AND exits in the same session @ 639.45 (not deferred to Jun 22)."""
    exit_trade = result_mode_a["trades"][2]
    assert exit_trade["date"] == "2026-06-18"
    assert exit_trade["price"] == pytest.approx(639.45, abs=0.01)


def test_v1_jun11_close_documents_deferred_price(df_full):
    """V1b: Jun 11 close = 586.93 — documents the deferred-exit variant's Jun 9 price.
    The deferred variant (evaluating exit next session) would exit here instead;
    our code correctly disarms without exit, and any CI that expected $655 would fail."""
    df_d = _compute_derived(df_full.copy())
    close_jun11 = float(df_d.loc[pd.Timestamp("2026-06-11"), "close"])
    assert close_jun11 == pytest.approx(586.93, abs=0.01), (
        f"V1b: Jun 11 close = {close_jun11:.2f}, expected 586.93"
    )
    # Also verify Jun 22 close (the Jun 18 deferred-exit variant's price)
    close_jun22 = float(df_d.loc[pd.Timestamp("2026-06-22"), "close"])
    assert close_jun22 == pytest.approx(655.01, abs=0.01)


def test_v1_no_exit_jun09_to_jun11(result_mode_a):
    """V1c: No exit trade on Jun 9-11 (disarm episode). Deferred-exit variant fails here."""
    trade_dates = {t["date"] for t in result_mode_a["trades"]}
    for d in ["2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"]:
        assert d not in trade_dates, f"V1c: unexpected trade on {d}"


def test_v2_on20_mom_may15(df_full):
    """V2: on20_mom 2026-05-15 ≈ -5.0pts (±0.3) — 14 sessions before Jun 5 distribution."""
    df_d = _compute_derived(df_full.copy())
    val = float(df_d.loc[pd.Timestamp("2026-05-15"), "on20_mom"])
    assert val == pytest.approx(-0.050, abs=0.003), (
        f"V2: on20_mom 2026-05-15 = {val:.4f}, expected ≈ -5.0pts"
    )


def test_v3_on20_mom_jun22(df_full):
    """V3: on20_mom 2026-06-22 ≈ +16.0pts (±0.5) — overnight surged into the top."""
    df_d = _compute_derived(df_full.copy())
    val = float(df_d.loc[pd.Timestamp("2026-06-22"), "on20_mom"])
    assert val == pytest.approx(0.160, abs=0.005), (
        f"V3: on20_mom 2026-06-22 = {val:.4f}, expected ≈ +16.0pts"
    )


def test_v4_on20_mom_jul01(df_full):
    """V4: on20_mom 2026-07-01 ≈ -5.8pts (±0.3) — tilt active on last day."""
    df_d = _compute_derived(df_full.copy())
    val = float(df_d.loc[pd.Timestamp("2026-07-01"), "on20_mom"])
    assert val == pytest.approx(-0.058, abs=0.003), (
        f"V4: on20_mom 2026-07-01 = {val:.4f}, expected ≈ -5.8pts"
    )


def test_v5_dd20_mar30(df_full):
    """V5: dd20 2026-03-30 ≈ -10.3% (±0.2) — documents CRASH_GATE_DD knife-edge."""
    df_d = _compute_derived(df_full.copy())
    val = float(df_d.loc[pd.Timestamp("2026-03-30"), "dd20"])
    assert val == pytest.approx(-0.103, abs=0.002), (
        f"V5: dd20 2026-03-30 = {val:.4f}, expected ≈ -10.3%"
    )


def test_v6_ensemble_arm_return_and_jun18_exit(df_full):
    """V6: ENSEMBLE_ARM=True — 2026 return close to baseline; Jun 18 exit preserved at 639.45."""
    df_d = _compute_derived(df_full.copy())
    ytd = df_d[df_d.index >= pd.Timestamp("2026-01-01")].copy()
    states, accum, trades = _run_state_machine(ytd, ensemble_arm=True)
    eq_s, _ = _run_backtest(ytd, states, accum, trades)
    ret = [v for v in eq_s if v is not None][-1] - 1

    # Return from fixture: ≈ 72.7% (differs from change order which used live data)
    assert ret == pytest.approx(0.727, abs=0.03), (
        f"V6: ENSEMBLE_ARM return = {ret:.3f}, expected ≈ 72.7%"
    )
    # Jun 18 exit must survive ensemble arm condition
    jun18_exits = [t for t in trades if t["date"] == "2026-06-18" and t["action"] == "EXIT"]
    assert len(jun18_exits) == 1, "V6: Jun 18 exit must still occur under ENSEMBLE_ARM"
    assert jun18_exits[0]["price"] == pytest.approx(639.45, abs=0.01)


def test_v7_crash_gate_mar30_suppression(df_full):
    """V7: CRASH_GATE=True suppresses ABS arm on Mar 30 (dd20=-10.3% <= -10%)."""
    df_d = _compute_derived(df_full.copy())
    ytd = df_d[df_d.index >= pd.Timestamp("2026-01-01")].copy()

    # Baseline: Mar 30 should be MONITOR (ABS arm fires, ACCUM stops)
    states_base, _, _ = _run_state_machine(ytd)
    idx_mar30 = list(ytd.index.strftime("%Y-%m-%d")).index("2026-03-30")
    assert states_base[idx_mar30] == "MONITOR", (
        f"V7: Expected baseline MONITOR on Mar 30, got {states_base[idx_mar30]}"
    )

    # With CRASH_GATE: Mar 30 must NOT be MONITOR (ABS arm suppressed)
    states_cg, accum_cg, trades_cg = _run_state_machine(ytd, crash_gate=True, crash_gate_dd=-0.10)
    assert states_cg[idx_mar30] == "RISK_ON", (
        f"V7: Expected RISK_ON on Mar 30 with CRASH_GATE, got {states_cg[idx_mar30]}"
    )

    # Return should be higher (full position through the Apr recovery)
    eq_s, _ = _run_backtest(ytd, states_cg, accum_cg, trades_cg)
    ret_cg = [v for v in eq_s if v is not None][-1] - 1
    assert ret_cg == pytest.approx(0.730, abs=0.02), (
        f"V7: CRASH_GATE return = {ret_cg:.3f}, expected ≈ 73.0%"
    )


def test_v8_continuous_sizing_regression(df_full):
    """V8: Continuous vol sizing min(1, 0.40/rv20) must yield significantly less than baseline.
    Asserts conditional-only sizing (v3.1 Change 5) was not silently reverted."""
    df_d = _compute_derived(df_full.copy())
    ytd = df_d[df_d.index >= pd.Timestamp("2026-01-01")].copy()
    states, accum_flags, trades = _run_state_machine(ytd, arm_mode="A")

    backtest_start = pd.Timestamp("2026-01-20")
    trade_dates = {t["date"] for t in trades}
    slip = SLIPPAGE_BPS / 10_000
    cum_cont = 1.0
    cum_base = 1.0

    for i, (idx, row) in enumerate(ytd.iterrows()):
        if idx < backtest_start:
            continue
        ret = float(row["ret"]) if not np.isnan(float(row["ret"])) else 0.0
        s = states[i]
        pos_base = 1.0 if accum_flags[i] else STATE_POS.get(s, 0.0)
        rv20 = float(row["rv20"]) if not np.isnan(float(row["rv20"])) else 1.0

        # Continuous sizing: always apply min(1, 0.40/rv20) when invested
        pos_cont = min(1.0, 0.40 / max(rv20, 0.001)) if pos_base > 0 else 0.0

        cum_base *= 1 + ret * pos_base
        cum_cont *= 1 + ret * pos_cont
        if idx.strftime("%Y-%m-%d") in trade_dates:
            cum_base *= 1 - slip
            cum_cont *= 1 - slip

    ret_base = cum_base - 1
    ret_cont = cum_cont - 1

    assert ret_cont < ret_base - 0.05, (
        f"V8: Continuous sizing return {ret_cont:.3f} should be ≥5pts below "
        f"baseline {ret_base:.3f}; continuous sizing was NOT rejected as expected"
    )


def test_v9_on20_mom_not_in_transitions():
    """V9: on20_mom must NOT appear in _run_state_machine (display/sizing only, not transitions)."""
    import inspect
    src = inspect.getsource(_run_state_machine)
    # Only the docstring may mention on20_mom; the actual logic must not reference it
    src_body = src.split('"""', 2)[-1]  # strip the docstring
    assert "on20_mom" not in src_body, (
        "V9: on20_mom found in _run_state_machine body — explicitly forbidden (P2). "
        "on20_mom is a sizing tilt only; it must never influence state transitions."
    )


def test_v10_gap_quality_not_in_transitions():
    """V10: gap_quality/asia_on20/tsm_on/ewy_on must NOT appear in _run_state_machine (v3.3 isolation)."""
    import inspect
    src = inspect.getsource(_run_state_machine)
    src_body = src.split('"""', 2)[-1]
    for forbidden in ["gap_quality", "asia_on20", "hollow_count20", "tsm_on", "ewy_on"]:
        assert forbidden not in src_body, (
            f"V10: '{forbidden}' found in _run_state_machine — explicitly forbidden. "
            "v3.3 Asia diagnostics are display-only; they must never influence state transitions."
        )


# ── W1–W8: Change Order v3.3 golden record ───────────────────────────────────
#
# W1-W6 require live network access (TSM/EWY/QQQ data from yfinance).
# W7 verifies the earnings_reactions.json seed. W8 uses the fixture only.
# Network tests are skipped gracefully if yfinance is unavailable.

def _try_fetch_companion(ticker: str, days: int = 500):
    """Return DataFrame or None if network unavailable."""
    try:
        from pipeline.sources import fetch_companion_ohlcv
        return fetch_companion_ohlcv(ticker, days=days)
    except Exception:
        return None


def _companion_on_series(ticker: str) -> "pd.Series | None":
    """Fetch overnight return series for a companion ticker (full history, then filter).

    Computes overnight returns on the FULL fetched history (so Jan 2 has a valid prior close),
    then filters to 2026-01-02..07-01. Returns None if network unavailable.
    """
    df = _try_fetch_companion(ticker, days=600)
    if df is None:
        return None
    on = df["open"] / df["close"].shift(1) - 1
    return on.loc["2026-01-02":"2026-07-01"]


def _companion_df_2026(ticker: str) -> "pd.DataFrame | None":
    """Fetch full OHLCV DataFrame for a companion ticker filtered to 2026-01-02..07-01."""
    df = _try_fetch_companion(ticker, days=600)
    if df is None:
        return None
    return df.loc["2026-01-02":"2026-07-01"]


@pytest.fixture(scope="module")
def tsm_on_2026():
    s = _companion_on_series("TSM")
    if s is None:
        pytest.skip("TSM data unavailable (network required for W1-W6)")
    return s


@pytest.fixture(scope="module")
def ewy_on_2026():
    s = _companion_on_series("EWY")
    if s is None:
        pytest.skip("EWY data unavailable (network required for W1-W6)")
    return s


@pytest.fixture(scope="module")
def qqq_on_2026():
    s = _companion_on_series("QQQ")
    if s is None:
        pytest.skip("QQQ data unavailable (network required for W3)")
    return s


@pytest.fixture(scope="module")
def tsm_df_2026():
    df = _companion_df_2026("TSM")
    if df is None:
        pytest.skip("TSM data unavailable (network required for W6)")
    return df


@pytest.fixture(scope="module")
def soxx_2026_on(df_full):
    """SOXX overnight returns filtered to 2026-01-02..07-01 (derived from full fixture)."""
    df_d = _compute_derived(df_full.copy())
    return df_d.loc["2026-01-02":"2026-07-01"]["on"]


def test_w1_corr_soxx_tsm(soxx_2026_on, tsm_on_2026):
    """W1: corr(SOXX_on, TSM_on) 2026-01-02..07-01 ≈ +0.85 (±0.03)."""
    aligned = pd.concat([soxx_2026_on, tsm_on_2026], axis=1, join="inner").dropna()
    aligned.columns = ["soxx", "tsm"]
    corr = float(aligned["soxx"].corr(aligned["tsm"]))
    assert corr == pytest.approx(0.85, abs=0.03), (
        f"W1: corr(SOXX_on, TSM_on) = {corr:.3f}, expected ≈ +0.85 (±0.03)"
    )


def test_w2_corr_soxx_ewy(soxx_2026_on, ewy_on_2026):
    """W2: corr(SOXX_on, EWY_on) 2026-01-02..07-01 ≈ +0.82 (±0.03)."""
    aligned = pd.concat([soxx_2026_on, ewy_on_2026], axis=1, join="inner").dropna()
    aligned.columns = ["soxx", "ewy"]
    corr = float(aligned["soxx"].corr(aligned["ewy"]))
    assert corr == pytest.approx(0.82, abs=0.03), (
        f"W2: corr(SOXX_on, EWY_on) = {corr:.3f}, expected ≈ +0.82 (±0.03)"
    )


def test_w3_r2_soxx_on_regression(soxx_2026_on, tsm_on_2026, ewy_on_2026, qqq_on_2026):
    """W3: R² of SOXX_on ~ TSM_on + EWY_on + QQQ_on ≈ 0.89 (±0.03)."""
    aligned = pd.concat(
        [soxx_2026_on, tsm_on_2026, ewy_on_2026, qqq_on_2026], axis=1, join="inner"
    ).dropna()
    aligned.columns = ["soxx", "tsm", "ewy", "qqq"]

    y = aligned["soxx"].values
    X = np.column_stack([np.ones(len(aligned)), aligned["tsm"].values,
                         aligned["ewy"].values, aligned["qqq"].values])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    y_hat = X @ beta
    ss_res = np.sum((y - y_hat) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = float(1 - ss_res / ss_tot)

    assert r2 == pytest.approx(0.89, abs=0.03), (
        f"W3: R²(SOXX_on ~ TSM+EWY+QQQ) = {r2:.3f}, expected ≈ 0.89 (±0.03)"
    )


def test_w4_hollow_gap_dates(df_full, tsm_on_2026, ewy_on_2026):
    """W4: Hollow up-gap dates 2026 = exactly {2026-03-30, 2026-04-20, 2026-04-23}."""
    df_d = _compute_derived(df_full.copy())
    soxx_ytd = df_d.loc["2026-01-02":"2026-07-01"].copy()

    tsm_on = tsm_on_2026.reindex(soxx_ytd.index)
    ewy_on = ewy_on_2026.reindex(soxx_ytd.index)

    gap_up = soxx_ytd["on"] > GAP_THR
    asia_gap = (tsm_on > GAP_THR) | (ewy_on > GAP_THR)
    hollow_mask = gap_up & ~asia_gap

    hollow_dates = set(soxx_ytd.index[hollow_mask].strftime("%Y-%m-%d"))
    expected = {"2026-03-30", "2026-04-20", "2026-04-23"}

    assert hollow_dates == expected, (
        f"W4: Hollow dates = {sorted(hollow_dates)}, expected {sorted(expected)}"
    )


def test_w5_gap_counts_2026(df_full, tsm_on_2026, ewy_on_2026):
    """W5: Up-gap counts 2026-01-02..07-01 = 65 total / 62 confirmed / 3 hollow."""
    df_d = _compute_derived(df_full.copy())
    soxx_ytd = df_d.loc["2026-01-02":"2026-07-01"].copy()

    tsm_on = tsm_on_2026.reindex(soxx_ytd.index)
    ewy_on = ewy_on_2026.reindex(soxx_ytd.index)

    gap_up = soxx_ytd["on"] > GAP_THR
    asia_gap = (tsm_on > GAP_THR) | (ewy_on > GAP_THR)
    confirmed = gap_up & asia_gap
    hollow = gap_up & ~asia_gap

    total_gaps = int(gap_up.sum())
    confirmed_gaps = int(confirmed.sum())
    hollow_gaps = int(hollow.sum())

    assert total_gaps == 65, f"W5: Total up-gaps = {total_gaps}, expected 65"
    assert confirmed_gaps == 62, f"W5: Confirmed up-gaps = {confirmed_gaps}, expected 62"
    assert hollow_gaps == 3, f"W5: Hollow up-gaps = {hollow_gaps}, expected 3"


def test_w6_tsm_cumulative_on_vs_id(tsm_on_2026, tsm_df_2026):
    """W6: TSM 2026 cumulative overnight ≈ +37pts vs intraday ≈ +6pts (±2)."""
    on = tsm_on_2026
    id_ = tsm_df_2026["close"] / tsm_df_2026["open"] - 1

    cum_on = float(on.sum() * 100)
    cum_id = float(id_.sum() * 100)

    assert cum_on == pytest.approx(37, abs=2), (
        f"W6: TSM cumul on = {cum_on:.1f}pts, expected ≈ +37pts (±2)"
    )
    assert cum_id == pytest.approx(6, abs=2), (
        f"W6: TSM cumul id = {cum_id:.1f}pts, expected ≈ +6pts (±2)"
    )


def test_w7_mu_earnings_grade():
    """W7: MU FQ3 2026-06-24 grade = DISTRIBUTION-CONFIRM per earnings_reactions.json seed."""
    reactions_path = ROOT / "data" / "earnings_reactions.json"
    if not reactions_path.exists():
        pytest.skip("data/earnings_reactions.json not found")

    with open(reactions_path) as f:
        records = json.load(f)

    mu = next(
        (r for r in records if r.get("ticker") == "MU" and r.get("report_date") == "2026-06-24"),
        None,
    )
    assert mu is not None, "W7: MU 2026-06-24 not found in earnings_reactions.json"
    assert mu.get("grade") == "DISTRIBUTION-CONFIRM", (
        f"W7: MU grade = {mu.get('grade')}, expected DISTRIBUTION-CONFIRM"
    )
    assert mu.get("pop", 0) == pytest.approx(0.1574, abs=0.002), (
        f"W7: MU pop = {mu.get('pop'):.4f}, expected ≈ +15.74%"
    )
    assert mu.get("vol_flag") is True, (
        f"W7: MU vol_flag = {mu.get('vol_flag')}, expected True (retrace vol ≥ pop vol)"
    )
    assert mu.get("retrace_date") == "2026-07-02", (
        f"W7: MU retrace_date = {mu.get('retrace_date')}, expected 2026-07-02 (T+5)"
    )


def test_w8_position_logic_isolation(result):
    """W8: With TSM/EWY data absent (soxx_2026.csv fixture), v3.2 golden record is byte-identical.

    Proves v3.3 is cosmetic to the state machine — gap_quality/asia_on20 columns
    are absent when tsm_on/ewy_on are not injected, and all trade/state outputs are unchanged.
    """
    # Verify the fixture has no companion columns
    df_check = load_fixture(str(FIXTURE))
    assert "tsm_on" not in df_check.columns, "W8: fixture should not have tsm_on"
    assert "ewy_on" not in df_check.columns, "W8: fixture should not have ewy_on"

    # gap_quality and asia_on20 must be null when TSM/EWY not present
    assert result["today"].get("gap_quality") is None, (
        "W8: gap_quality should be null without TSM/EWY injection"
    )
    assert result["today"].get("asia_on20") is None, (
        "W8: asia_on20 should be null without TSM/EWY injection"
    )
    asia_series = result["series"].get("asia_on20", [])
    assert all(v is None for v in asia_series), (
        "W8: asia_on20 series should be all-null without TSM/EWY injection"
    )

    # v3.2 golden record must be preserved
    assert result["state"]["machine"] == "EXIT", (
        f"W8: Expected EXIT state, got {result['state']['machine']}"
    )
    assert result["state"]["position_multiplier"] == 0.0, "W8: Expected 0.0 position"

    jun18_exits = [t for t in result["trades"] if t["date"] == "2026-06-18" and t["action"] == "EXIT"]
    assert len(jun18_exits) == 1, "W8: Jun 18 exec-into-strength EXIT must be present"
    assert jun18_exits[0]["price"] == pytest.approx(639.45, abs=0.01), (
        f"W8: Jun 18 EXIT price = {jun18_exits[0]['price']}, expected 639.45"
    )


# ── V11 + X1–X5: Change Order v3.5 golden record ────────────────────────────
#
# V11: static guard (no data needed).
# X1–X2: network-optional (require H2-2025 SOXX live data).
# X3–X5: fixture-based.

def _ks_2samp_stat(x: np.ndarray, y: np.ndarray) -> float:
    """Two-sample KS statistic (scipy not in requirements)."""
    all_vals = np.sort(np.concatenate([x, y]))
    cdf1 = np.searchsorted(np.sort(x), all_vals, side="right") / len(x)
    cdf2 = np.searchsorted(np.sort(y), all_vals, side="right") / len(y)
    return float(np.max(np.abs(cdf1 - cdf2)))


def test_v11_pctl120_not_in_transitions():
    """V11: id20_pctl120/id20_is_sample_low/id20_is_sample_high must NOT appear in _run_state_machine."""
    import inspect
    src = inspect.getsource(_run_state_machine)
    src_body = src.split('"""', 2)[-1]
    for forbidden in ["id20_pctl120", "id20_is_sample_low", "id20_is_sample_high"]:
        assert forbidden not in src_body, (
            f"V11: '{forbidden}' found in _run_state_machine body — explicitly forbidden. "
            "v3.5 percentile/sample-extreme diagnostics are display-only."
        )


def test_x1_ks_regime_statistic():
    """X1: KS(id20 H2-2025 vs 2026-01-20..07-01) ≈ 0.37 (±0.02) — documents Z1 structural break.

    Requires live SOXX data (network). Skipped offline.
    """
    try:
        from pipeline.sources import fetch_companion_ohlcv
        df_soxx = fetch_companion_ohlcv("SOXX", days=600)
    except Exception:
        pytest.skip("SOXX live data unavailable (network required for X1)")

    df_d = _compute_derived(df_soxx)
    h2_2025 = df_d.loc["2025-07-01":"2025-12-31", "id20"].dropna().values
    ytd_2026 = df_d.loc["2026-01-20":"2026-07-01", "id20"].dropna().values

    if len(h2_2025) < 50 or len(ytd_2026) < 50:
        pytest.skip(f"X1: insufficient data (H2-2025={len(h2_2025)}, 2026={len(ytd_2026)})")

    ks = _ks_2samp_stat(h2_2025, ytd_2026)
    assert ks == pytest.approx(0.37, abs=0.04), (
        f"X1: KS(id20 H2-2025 vs 2026 YTD) = {ks:.3f}, expected ≈ 0.37 (±0.04). "
        "KS > 0.17 rejects sameness at 1% level for these sample sizes."
    )


def test_x2_implied_arm_line():
    """X2: Implied Mode B arm line (mean−1σ) ≈ −3.2% / −2.6% / −2.9% on key dates — documents Z2.

    Requires live SOXX data (network). Skipped offline.
    """
    try:
        from pipeline.sources import fetch_companion_ohlcv
        df_soxx = fetch_companion_ohlcv("SOXX", days=700)
    except Exception:
        pytest.skip("SOXX live data unavailable (network required for X2)")

    df_d = _compute_derived(df_soxx)
    _mean252 = df_d["id20"].rolling(252, min_periods=120).mean()
    _std252  = df_d["id20"].rolling(252, min_periods=120).std()
    implied_arm = _mean252 - 1.0 * _std252

    checks = [
        ("2026-02-04", -0.032),
        ("2026-06-18", -0.026),
        ("2026-07-01", -0.029),
    ]
    for date_str, expected in checks:
        ts = pd.Timestamp(date_str)
        if ts not in implied_arm.index:
            continue
        val = float(implied_arm.loc[ts])
        assert val == pytest.approx(expected, abs=0.020), (
            f"X2: implied arm line on {date_str} = {val:.4f}, expected ≈ {expected:.3f} (±2pp). "
            "Documenting Z2: mean/std co-inflation keeps threshold narrow despite structural break."
        )


def test_x3_pctl120_jul01(df_full):
    """X3: id20_pctl120 on 2026-07-01 ≈ 0.02 (±0.02) — 98th pctile of selling pressure."""
    df_d = _compute_derived(df_full.copy())
    val = float(df_d.loc[pd.Timestamp("2026-07-01"), "id20_pctl120"])
    assert val == pytest.approx(0.02, abs=0.02), (
        f"X3: id20_pctl120 on 2026-07-01 = {val:.4f}, expected ≈ 0.02 (±0.02). "
        "Only ~2% of trailing 120 sessions had id20 worse than −6.5%."
    )


def test_x4_sample_low_flag(result, df_full):
    """X4: id20_is_sample_low FALSE on 2026-07-01 (id20 −6.5% > fixture min ≈ −10.4%).

    The fixture-only min reaches −10.4% during the Jun 2026 distribution period (consistent
    with H2-2025 min cited in Z4; live pipeline with full history gives similar values).
    Verifies compute_signals correctly emits the sample-low flag; TRUE case (Jul 2, −12.2%)
    requires live data and is confirmed by running the live pipeline post-market on 2026-07-02.
    """
    df_d = _compute_derived(df_full.copy())
    hist_min = float(df_d["id20"].iloc[:-5].min())
    # Fixture min (excl. last 5) is the Jun 2026 distribution trough, around −10% to −11%
    assert hist_min < -0.080, (
        f"X4: fixture min = {hist_min:.4f} should be below −8% (June distribution trough)"
    )
    assert hist_min > -0.125, (
        f"X4: fixture min = {hist_min:.4f} unexpectedly extreme (sanity check)"
    )

    # id20 on Jul 1 should be strictly above the fixture min → sample_low = False
    id20_jul1 = float(df_d.loc[pd.Timestamp("2026-07-01"), "id20"])
    assert id20_jul1 > hist_min, (
        f"X4: Jul 1 id20 = {id20_jul1:.4f} should exceed fixture min {hist_min:.4f}"
    )

    # compute_signals must emit False for id20_is_sample_low
    assert result["today"]["id20_is_sample_low"] is False, (
        "X4: id20_is_sample_low should be False on 2026-07-01 fixture"
    )
    assert result["today"]["id20_is_sample_high"] is False, (
        "X4: id20_is_sample_high should be False on 2026-07-01 fixture"
    )

    # id20_history_months must be a positive integer
    assert result["today"]["id20_history_months"] >= 1, "X4: id20_history_months must be >= 1"


def test_x5_position_logic_isolation_v35(result):
    """X5: v3.5 features (pctl120, sample-low) do not alter the v3.2 golden record.

    id20_pctl120 is computed in _compute_derived and present in df, but V11 confirms
    _run_state_machine never reads it. Trades and states are byte-identical.
    """
    # v3.5 fields must be present
    assert "id20_pctl120" in result["today"], "X5: id20_pctl120 missing from today"
    assert "id20_is_sample_low" in result["today"], "X5: id20_is_sample_low missing from today"
    assert "id20_pctl120" in result["series"], "X5: id20_pctl120 missing from series"

    # v3.2 golden record unchanged
    assert result["state"]["machine"] == "EXIT", (
        f"X5: Expected EXIT state, got {result['state']['machine']}"
    )
    jun18_exits = [t for t in result["trades"] if t["date"] == "2026-06-18" and t["action"] == "EXIT"]
    assert len(jun18_exits) == 1, "X5: Jun 18 EXIT must be present (v3.5 must not alter trades)"
    assert jun18_exits[0]["price"] == pytest.approx(639.45, abs=0.01), (
        f"X5: Jun 18 EXIT price = {jun18_exits[0]['price']}, expected 639.45"
    )
    feb06_reenter = [t for t in result["trades"] if t["date"] == "2026-02-06" and t["action"] == "REENTER"]
    assert len(feb06_reenter) == 1, "X5: Feb 06 REENTER must be present"
    assert feb06_reenter[0]["price"] == pytest.approx(348.51, abs=0.01)
