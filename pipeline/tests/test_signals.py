"""
Signal formula golden-value tests (§6.4).
Tolerance: ±0.2pp on percentages, ±0.3 on prices.
"""
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "soxx_2026.csv"

import sys
sys.path.insert(0, str(ROOT))

from pipeline.sources import load_fixture
from pipeline.state_machine import compute_signals, _compute_derived


@pytest.fixture(scope="module")
def df_full():
    return load_fixture(str(FIXTURE))


@pytest.fixture(scope="module")
def df_derived(df_full):
    return _compute_derived(df_full)


@pytest.fixture(scope="module")
def result(df_full):
    manual = {"iv30": 0.46, "iv30_asof": "2026-07-01", "pc_oi": 2.9, "pc_oi_asof": "2026-06-18"}
    return compute_signals(df_full, manual)


def _row(df: pd.DataFrame, date_str: str) -> pd.Series:
    return df.loc[pd.Timestamp(date_str)]


# ── 2026-06-22 assertions ─────────────────────────────────────────────────

def test_jun22_close(df_derived):
    row = _row(df_derived, "2026-06-22")
    assert row["close"] == pytest.approx(655.01, abs=0.3)


def test_jun22_id20(df_derived):
    row = _row(df_derived, "2026-06-22")
    # id20 ≈ -1.5% (in decimal: -0.015)
    assert row["id20"] == pytest.approx(-0.015, abs=0.002)


def test_jun22_on20(df_derived):
    row = _row(df_derived, "2026-06-22")
    # on20 ≈ +25.4%
    assert row["on20"] == pytest.approx(0.254, abs=0.002)


# ── 2026-07-01 assertions ─────────────────────────────────────────────────

def test_jul01_close(df_derived):
    row = _row(df_derived, "2026-07-01")
    assert row["close"] == pytest.approx(599.70, abs=0.3)


def test_jul01_id20(df_derived):
    row = _row(df_derived, "2026-07-01")
    # id20 ≈ -6.5%
    assert row["id20"] == pytest.approx(-0.065, abs=0.002)


def test_jul01_on20(df_derived):
    row = _row(df_derived, "2026-07-01")
    # on20 ≈ +7.8%
    assert row["on20"] == pytest.approx(0.078, abs=0.002)


def test_jul01_ret20(df_derived):
    row = _row(df_derived, "2026-07-01")
    # ret20 ≈ +1.9%
    assert row["ret20"] == pytest.approx(0.019, abs=0.002)


def test_jul01_rv20(df_derived):
    row = _row(df_derived, "2026-07-01")
    # rv20 ≈ 85%
    assert row["rv20"] == pytest.approx(0.85, abs=0.02)


def test_jul01_ma20(df_derived):
    row = _row(df_derived, "2026-07-01")
    # ma20 ≈ 600.3
    assert row["ma20"] == pytest.approx(600.3, abs=0.3)


def test_jul01_ma50(df_derived):
    row = _row(df_derived, "2026-07-01")
    # ma50 ≈ 542.7
    assert row["ma50"] == pytest.approx(542.7, abs=0.3)


def test_jul01_rsi14(df_derived):
    row = _row(df_derived, "2026-07-01")
    # rsi14 ≈ 52.3
    assert row["rsi14"] == pytest.approx(52.3, abs=1.0)


def test_jul01_ar1(df_derived):
    row = _row(df_derived, "2026-07-01")
    # ar1 ≈ -0.28
    assert row["ar1"] == pytest.approx(-0.28, abs=0.05)


# ── 2026-06-05 crash assertions ───────────────────────────────────────────

def test_jun05_ret(df_derived):
    row = _row(df_derived, "2026-06-05")
    # ret ≈ -10.44%
    assert row["ret"] == pytest.approx(-0.1044, abs=0.002)


def test_jun05_turb(df_derived):
    row = _row(df_derived, "2026-06-05")
    # turb > 3 (≈ 3.7)
    assert row["turb"] > 3.0


# ── 2026-03-13 accumulation assertions ───────────────────────────────────

def test_mar13_on20(df_derived):
    row = _row(df_derived, "2026-03-13")
    # on20 ≈ -12.5%
    assert row["on20"] == pytest.approx(-0.125, abs=0.002)


def test_mar13_id20(df_derived):
    row = _row(df_derived, "2026-03-13")
    # id20 ≈ +7.1%
    assert row["id20"] == pytest.approx(0.071, abs=0.002)


def test_mar13_accum_overlay(result):
    # ACCUM band should include 2026-03-13
    bands = result["bands"]
    accum_bands = [b for b in bands if b["state"] == "ACCUM"]
    assert len(accum_bands) > 0, "No ACCUM bands found"
    target = pd.Timestamp("2026-03-13")
    covered = any(
        pd.Timestamp(b["start"]) <= target <= pd.Timestamp(b["end"] or "2099-12-31")
        for b in accum_bands
    )
    assert covered, f"2026-03-13 not covered by any ACCUM band; bands: {accum_bands}"


# ── 2026-05-15 assertions ─────────────────────────────────────────────────

def test_may15_ma50(df_derived):
    row = _row(df_derived, "2026-05-15")
    # ma50 ≈ 401.1
    assert row["ma50"] == pytest.approx(401.1, abs=0.3)


def test_may15_ma200(df_derived):
    row = _row(df_derived, "2026-05-15")
    # ma200 ≈ 322.9
    assert row["ma200"] == pytest.approx(322.9, abs=0.3)


def test_may15_rsi14(df_derived):
    row = _row(df_derived, "2026-05-15")
    # rsi14 ≈ 64.8
    assert row["rsi14"] == pytest.approx(64.8, abs=1.5)
