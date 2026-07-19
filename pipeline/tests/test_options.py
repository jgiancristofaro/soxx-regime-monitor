"""
Options-chain automation tests (IV30 / P/C OI, pipeline/options.py).

All tests inject expiries/chain_fn — no live network calls. O1-O7 mirror the
project's lettered-test-vector convention used in test_state_machine.py.
"""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent.parent
import sys
sys.path.insert(0, str(ROOT))

from pipeline.options import fetch_options_metrics

TODAY = date(2026, 7, 19)
SPOT = 520.0


def _chain(strikes_ivs_oi_calls, strikes_ivs_oi_puts):
    calls = pd.DataFrame(strikes_ivs_oi_calls, columns=["strike", "impliedVolatility", "openInterest"])
    puts = pd.DataFrame(strikes_ivs_oi_puts, columns=["strike", "impliedVolatility", "openInterest"])
    return calls, puts


def _make_chain_fn(chains: dict):
    def chain_fn(expiry):
        return chains[expiry]
    return chain_fn


# ── O1: interpolation between two bracketing expiries ──────────────────────
def test_o1_variance_interpolation():
    # Expiry A: 20 days out, ATM iv = 0.40. Expiry B: 40 days out, ATM iv = 0.50.
    chains = {
        "2026-08-08": _chain([(520.0, 0.40, 100)], [(520.0, 0.40, 80)]),   # +20d
        "2026-08-28": _chain([(520.0, 0.50, 200)], [(520.0, 0.50, 150)]),  # +40d
    }
    result = fetch_options_metrics(
        spot=SPOT, today=TODAY,
        expiries=["2026-08-08", "2026-08-28"],
        chain_fn=_make_chain_fn(chains),
    )
    # Hand-computed variance interpolation at T=30: w = (40-30)/(40-20) = 0.5
    # var30 = 0.5*(0.40^2*20) + 0.5*(0.50^2*40) = 0.5*3.2 + 0.5*10 = 6.6 -> iv30 = sqrt(6.6/30)
    var30 = 0.5 * (0.40 ** 2 * 20) + 0.5 * (0.50 ** 2 * 40)
    expected = round((var30 / 30) ** 0.5, 4)
    assert result["iv30"] == expected
    assert result["iv30_expiries"] == ["2026-08-08", "2026-08-28"]


# ── O2: exact 30-day expiry -> no interpolation ─────────────────────────────
def test_o2_exact_30_day_expiry_no_interpolation():
    exp = (TODAY + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    chains = {exp: _chain([(520.0, 0.45, 100)], [(520.0, 0.45, 90)])}
    result = fetch_options_metrics(
        spot=SPOT, today=TODAY, expiries=[exp], chain_fn=_make_chain_fn(chains),
    )
    assert result["iv30"] == 0.45
    assert result["iv30_expiries"] == [exp]


# ── O3: only near-dated (<30d) expiries -> falls back to nearest single ─────
def test_o3_only_near_dated_falls_back():
    chains = {
        "2026-07-24": _chain([(520.0, 0.35, 50)], [(520.0, 0.35, 40)]),  # +5d
        "2026-08-03": _chain([(520.0, 0.38, 60)], [(520.0, 0.38, 45)]),  # +15d
    }
    result = fetch_options_metrics(
        spot=SPOT, today=TODAY,
        expiries=["2026-07-24", "2026-08-03"],
        chain_fn=_make_chain_fn(chains),
    )
    # Nearest to 30d among <30d options is the +15d expiry (2026-08-03)
    assert result["iv30_expiries"] == ["2026-08-03"]
    assert result["iv30"] == 0.38


# ── O4: only far-dated (>30d) expiries -> falls back to nearest single ──────
def test_o4_only_far_dated_falls_back():
    chains = {
        "2026-09-18": _chain([(520.0, 0.42, 70)], [(520.0, 0.42, 55)]),   # +61d
        "2026-10-16": _chain([(520.0, 0.44, 80)], [(520.0, 0.44, 65)]),   # +89d
    }
    result = fetch_options_metrics(
        spot=SPOT, today=TODAY,
        expiries=["2026-09-18", "2026-10-16"],
        chain_fn=_make_chain_fn(chains),
    )
    assert result["iv30_expiries"] == ["2026-09-18"]
    assert result["iv30"] == 0.42


# ── O5: zero/NaN IV at nearest strike skips to next-nearest ─────────────────
def test_o5_zero_iv_skips_to_next_strike():
    exp = "2026-08-18"  # +30d
    chains = {
        exp: _chain(
            [(520.0, 0.0, 10), (525.0, 0.41, 20)],
            [(520.0, 0.0, 8), (515.0, 0.39, 15)],
        ),
    }
    result = fetch_options_metrics(
        spot=SPOT, today=TODAY, expiries=[exp], chain_fn=_make_chain_fn(chains),
    )
    # 520 strike has zero IV both sides -> skip; next-nearest strikes (515 put, 525 call)
    # are equidistant (5 away) -> both considered, average of the two valid IVs
    assert result["iv30"] == round((0.41 + 0.39) / 2, 4)


# ── O6: P/C OI aggregates across all expiries, excludes >400d ───────────────
def test_o6_pc_oi_aggregates_and_excludes_far_leaps():
    chains = {
        "2026-08-18": _chain([(520.0, 0.40, 100)], [(520.0, 0.40, 200)]),  # +30d
        "2026-09-18": _chain([(520.0, 0.42, 50)], [(520.0, 0.42, 150)]),   # +61d
        "2028-01-21": _chain([(520.0, 0.50, 9999)], [(520.0, 0.50, 9999)]),  # LEAPS, >400d, excluded
    }
    result = fetch_options_metrics(
        spot=SPOT, today=TODAY,
        expiries=list(chains.keys()),
        chain_fn=_make_chain_fn(chains),
    )
    # calls: 100+50=150, puts: 200+150=350 (2028 LEAPS excluded from both)
    assert result["pc_oi"] == round(350 / 150, 4)


# ── O7: error cases surface as exceptions for the caller to catch ───────────
def test_o7_no_expiries_raises():
    with pytest.raises(RuntimeError):
        fetch_options_metrics(spot=SPOT, today=TODAY, expiries=[], chain_fn=lambda e: (None, None))


def test_o7_no_future_expiries_raises():
    with pytest.raises(RuntimeError):
        fetch_options_metrics(
            spot=SPOT, today=TODAY, expiries=["2026-01-01"], chain_fn=lambda e: (None, None),
        )


def test_o7_no_call_oi_raises():
    exp = "2026-08-18"
    chains = {exp: _chain([(520.0, 0.40, 0)], [(520.0, 0.40, 50)])}
    with pytest.raises(RuntimeError):
        fetch_options_metrics(spot=SPOT, today=TODAY, expiries=[exp], chain_fn=_make_chain_fn(chains))


def test_o7_missing_spot_raises():
    with pytest.raises(ValueError):
        fetch_options_metrics(today=TODAY, expiries=["2026-08-18"], chain_fn=lambda e: (None, None))
