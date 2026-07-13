"""
P1-P7 tests for Change Order v3.8 preview run.
All tests use _snapshot_override and _history_override to avoid network calls.
"""
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import pytz

ROOT = Path(__file__).parent.parent.parent
DATA_DIR = ROOT / "data"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_history(n_rows: int = 260) -> pd.DataFrame:
    """Load real history.csv for projection tests."""
    history_path = DATA_DIR / "history.csv"
    df = pd.read_csv(history_path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index)
    df.columns = [c.lower() for c in df.columns]
    return df.tail(n_rows)


def _et(hour: int, minute: int, date_str: str = "2026-07-15") -> datetime:
    """Return a timezone-aware ET datetime."""
    et = pytz.timezone("US/Eastern")
    return et.localize(datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M"))


def _fake_snap(last_price: float = 590.0) -> tuple:
    return (590.0, last_price, last_price + 5.0, last_price - 3.0, "15:40")


# ---------------------------------------------------------------------------
# P1 — preview run modifies ONLY data/preview.json
# ---------------------------------------------------------------------------

def test_p1_preview_writes_only_preview_json(tmp_path, monkeypatch):
    """P1: The preview run must not touch signals.json, history.csv, or any
    other settled-record artifact."""
    import pipeline.preview as pv

    monkeypatch.setattr(pv, "PREVIEW_JSON", tmp_path / "preview.json")
    monkeypatch.setattr(pv, "HISTORY_CSV", DATA_DIR / "history.csv")
    monkeypatch.setattr(pv, "DATA_DIR", tmp_path)

    history = _make_history()
    # Today is one session after the last history row
    today_str = (history.index[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    now_et = _et(15, 40, today_str)

    snap = _fake_snap(float(history["close"].iloc[-1]) * 1.005)  # slight up-day

    result = pv.run_preview(
        now_et=now_et,
        _snapshot_override=snap,
        _history_override=history,
    )

    # Only preview.json was written; signals.json untouched
    assert (tmp_path / "preview.json").exists()
    assert not (tmp_path / "signals.json").exists()
    assert not (tmp_path / "history.csv").exists()
    assert result["date"] == today_str
    assert result["volume_unreliable"] is True


# ---------------------------------------------------------------------------
# P2 — buffer classifier
# ---------------------------------------------------------------------------

def test_p2_buffer_classifier_clear_and_borderline():
    """P2: Synthetic margins at ±0.4pp / ±0.6pp around each trigger type."""
    import pipeline.preview as pv
    from pipeline.state_machine import ARM_ABS_ID, EXIT_ID, RISK_ON, MONITOR, EXIT

    # ARM via id20_abs: ARM_ABS_ID = -0.03
    # CLEAR when buffer >= BUFFER_20D (0.005)  →  id20 must be <= ARM_ABS_ID - 0.005
    # BORDERLINE when buffer < 0.005  →  id20 between ARM_ABS_ID and ARM_ABS_ID - 0.005

    # BORDERLINE: id20 = -0.031 (0.001 below threshold, < 0.005)
    deriv_borderline = {"id20": -0.031, "ret20": 0.0, "id_t": 0.0, "ret_t": 0.0,
                        "close": 500.0, "ma20": 490.0, "id20_z": -1.5, "on20": 0.0}
    action, _, _, ac, _ = pv._classify(RISK_ON, MONITOR, None, deriv_borderline)
    assert action == "ARM"
    assert ac == "BORDERLINE"

    # CLEAR: id20 = -0.037 (0.007 below threshold, >= 0.005)
    deriv_clear = dict(deriv_borderline, id20=-0.037)
    action, _, _, ac, _ = pv._classify(RISK_ON, MONITOR, None, deriv_clear)
    assert action == "ARM"
    assert ac == "CLEAR"

    # EXIT via id_t: EXIT_ID = 0.01
    # CLEAR when id_t - EXIT_ID >= BUFFER_DAY (0.005)  →  id_t >= 0.015
    # BORDERLINE: id_t = 0.012
    deriv_exit_b = {"id20": 0.0, "ret20": 0.0, "id_t": 0.012, "ret_t": 0.0,
                    "close": 500.0, "ma20": 490.0, "id20_z": 0.0, "on20": 0.0}
    fake_trade = {"action": "EXIT", "reason": "exec-into-strength id_t"}
    _, moc, _, ac, _ = pv._classify(EXIT, EXIT, fake_trade, deriv_exit_b)
    assert ac == "BORDERLINE"

    # CLEAR: id_t = 0.016
    deriv_exit_c = dict(deriv_exit_b, id_t=0.016)
    _, moc, _, ac, _ = pv._classify(EXIT, EXIT, fake_trade, deriv_exit_c)
    assert ac == "CLEAR"


# ---------------------------------------------------------------------------
# P3 — settle supersedes preview; preview_log.csv row; banner state machine
# ---------------------------------------------------------------------------

def test_p3_settle_supersedes_preview(tmp_path):
    """P3: _settle_supersedes_preview writes preview_log.csv and marks preview settled."""
    import pipeline.compute as cm

    last_session = "2026-07-15"

    # Seed preview.json with an active preview
    preview = {
        "date": last_session,
        "snapshot_et": "15:41",
        "late": False,
        "skipped": None,
        "spot": 580.0,
        "provisional_ret": 0.005,
        "provisional_id": 0.002,
        "projected_state": "RISK_ON",
        "current_settled_state": "RISK_ON",
        "projected_action": "NONE",
        "action_class": "NONE",
        "moc_eligible": False,
        "margins": None,
        "volume_unreliable": True,
        "note": "No projected transition",
    }
    preview_path = tmp_path / "preview.json"
    preview_path.write_text(json.dumps(preview))

    # Mock DATA_DIR so that settle function writes to tmp_path
    original_data_dir = cm.DATA_DIR
    cm.DATA_DIR = tmp_path

    try:
        result = {
            "last_session": last_session,
            "trades": [],  # no trade today → settled_action = NONE
            "today": {"close": 582.5},
        }
        cm._settle_supersedes_preview(result, last_session)
    finally:
        cm.DATA_DIR = original_data_dir

    # preview.json should now be marked settled
    updated = json.loads(preview_path.read_text())
    assert updated["settled"] is True
    assert updated["settled_action"] == "NONE"
    assert updated["moc_eligible"] is False

    # preview_log.csv should have one row
    log_path = tmp_path / "preview_log.csv"
    assert log_path.exists()
    with open(log_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["date"] == last_session
    assert rows[0]["projected_action"] == "NONE"
    assert rows[0]["settled_action"] == "NONE"
    assert rows[0]["agreed"] == "True"
    assert rows[0]["spot_at_preview"] == "580.0"


# ---------------------------------------------------------------------------
# P4 — time guard: late flag and abort
# ---------------------------------------------------------------------------

def test_p4_late_flag_after_1546(tmp_path, monkeypatch):
    """P4: preview generated after 15:46 ET carries late=True."""
    import pipeline.preview as pv

    monkeypatch.setattr(pv, "PREVIEW_JSON", tmp_path / "preview.json")

    history = _make_history()
    today_str = (history.index[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    now_et = _et(15, 50, today_str)  # 15:50 → late
    snap = _fake_snap(float(history["close"].iloc[-1]) * 1.002)

    result = pv.run_preview(
        now_et=now_et,
        _snapshot_override=snap,
        _history_override=history,
    )
    assert result["late"] is True


def test_p4_abort_after_1559(tmp_path, monkeypatch):
    """P4: preview run after 15:59 ET calls sys.exit(1) and does NOT write."""
    import pipeline.preview as pv

    monkeypatch.setattr(pv, "PREVIEW_JSON", tmp_path / "preview.json")

    history = _make_history()
    today_str = (history.index[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    now_et = _et(16, 1, today_str)  # 16:01 → abort

    with pytest.raises(SystemExit) as exc:
        pv.run_preview(
            now_et=now_et,
            _snapshot_override=_fake_snap(),
            _history_override=history,
        )
    assert exc.value.code == 1
    assert not (tmp_path / "preview.json").exists()


# ---------------------------------------------------------------------------
# P5 — volume_unreliable always True in preview output
# ---------------------------------------------------------------------------

def test_p5_volume_unreliable(tmp_path, monkeypatch):
    """P5: preview output always carries volume_unreliable=True."""
    import pipeline.preview as pv

    monkeypatch.setattr(pv, "PREVIEW_JSON", tmp_path / "preview.json")

    history = _make_history()
    today_str = (history.index[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    now_et = _et(15, 40, today_str)
    snap = _fake_snap(float(history["close"].iloc[-1]) * 1.001)

    result = pv.run_preview(
        now_et=now_et,
        _snapshot_override=snap,
        _history_override=history,
    )
    assert result["volume_unreliable"] is True


# ---------------------------------------------------------------------------
# P6 — half-day: preview skips or uses early window
# ---------------------------------------------------------------------------

def test_p6_half_day_skip(tmp_path, monkeypatch):
    """P6: On a half-day, preview writes skipped='half-day'."""
    import pipeline.preview as pv

    monkeypatch.setattr(pv, "PREVIEW_JSON", tmp_path / "preview.json")
    # Force _is_half_day to return True regardless of calendar
    monkeypatch.setattr(pv, "_is_half_day", lambda _: True)

    history = _make_history()
    today_str = (history.index[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    now_et = _et(12, 40, today_str)

    result = pv.run_preview(
        now_et=now_et,
        _snapshot_override=None,   # no override → triggers half-day check first
        _history_override=history,
    )
    assert result["skipped"] == "half-day"
    assert result["projected_action"] == "NONE"


# ---------------------------------------------------------------------------
# P7 — regression: settled record byte-identical with preview feature present
# ---------------------------------------------------------------------------

def test_p7_settled_record_unaffected(tmp_path):
    """P7: _settle_supersedes_preview must not modify signals.json or history.csv."""
    import pipeline.compute as cm

    last_session = "2026-07-15"
    signals_path = tmp_path / "signals.json"
    sentinel = {"state": {"machine": "RISK_ON"}, "last_session": last_session, "trades": []}
    signals_path.write_text(json.dumps(sentinel))
    original_content = signals_path.read_bytes()

    preview_path = tmp_path / "preview.json"
    preview = {
        "date": last_session, "snapshot_et": "15:41", "late": False, "skipped": None,
        "spot": 580.0, "provisional_ret": 0.005, "provisional_id": 0.002,
        "projected_state": "RISK_ON", "current_settled_state": "RISK_ON",
        "projected_action": "NONE", "action_class": "NONE", "moc_eligible": False,
        "margins": None, "volume_unreliable": True, "note": "No projected transition",
    }
    preview_path.write_text(json.dumps(preview))

    original_data_dir = cm.DATA_DIR
    cm.DATA_DIR = tmp_path
    try:
        result = {
            "last_session": last_session,
            "trades": [],
            "today": {"close": 582.5},
        }
        cm._settle_supersedes_preview(result, last_session)
    finally:
        cm.DATA_DIR = original_data_dir

    # signals.json must be byte-identical
    assert signals_path.read_bytes() == original_content
