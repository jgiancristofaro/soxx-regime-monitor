"""
Preview run: 3:40pm ET intraday snapshot → projected end-of-day state transition.
Writes data/preview.json ONLY. Never touches signals.json, history.csv, or any
settled-record artifact. Does not trigger the v3.7 consistency validator.
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
HISTORY_CSV = DATA_DIR / "history.csv"
PREVIEW_JSON = DATA_DIR / "preview.json"

# Buffer thresholds (v3.8 Change 3)
# A ±0.4% price move by 4pm shifts id_t/ret_t by ~±0.4pp and id20 by the same.
BUFFER_DAY = 0.005   # for id_t / ret_t triggers (exit-into-strength)
BUFFER_20D = 0.005   # for id20-based arm/disarm/re-entry conditions
BUFFER_MA  = 0.004   # for close vs MA20 trend-reclaim condition

# ET timezone
_ET = "US/Eastern"

# Preview window
LATE_CUTOFF  = (15, 46)   # past this → late=True (MOC window closed)
ABORT_CUTOFF = (15, 59)   # past this → abort, do not write


def _now_et() -> datetime:
    import pytz
    return datetime.now(pytz.timezone(_ET))


def _is_half_day(today_date: date) -> bool:
    """True if NYSE closes early today (1pm or 1:30pm close)."""
    try:
        import exchange_calendars as xcals
        import pytz
        nyse = xcals.get_calendar("XNYS")
        ts = pd.Timestamp(today_date)
        if not nyse.is_session(ts):
            return False
        close_utc = nyse.session_close(ts)
        close_et = close_utc.astimezone(pytz.timezone(_ET))
        return close_et.hour < 16
    except Exception:
        # Hardcoded fallback
        HALF_DAYS = {
            "2026-07-03", "2026-11-27", "2026-12-24",
            "2027-07-02", "2027-11-26", "2027-12-24",
        }
        return str(today_date) in HALF_DAYS


def _fetch_snapshot(ticker: str = "SOXX"):
    """
    Fetch today's 1-minute bars.
    Returns (open, last_price, session_high, session_low, snapshot_et_str) or None.
    """
    import pytz
    try:
        import yfinance as yf
        bars = yf.download(ticker, period="1d", interval="1m",
                           progress=False, auto_adjust=True)
        if bars is None or bars.empty:
            return None
        if isinstance(bars.columns, pd.MultiIndex):
            bars.columns = [c[0].lower() for c in bars.columns]
        else:
            bars.columns = [c.lower() for c in bars.columns]

        today_open    = float(bars["open"].iloc[0])
        last_price    = float(bars["close"].iloc[-1])
        session_high  = float(bars["high"].max())
        session_low   = float(bars["low"].min())

        last_idx = bars.index[-1]
        if hasattr(last_idx, "tz_convert"):
            last_et = last_idx.tz_convert(_ET)
        else:
            last_et = pytz.timezone(_ET).localize(last_idx.to_pydatetime())
        return today_open, last_price, session_high, session_low, last_et.strftime("%H:%M")
    except Exception as e:
        print(f"Snapshot fetch failed: {e}", file=sys.stderr)
        return None


def _load_history() -> pd.DataFrame:
    df = pd.read_csv(HISTORY_CSV, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index)
    df.columns = [c.lower() for c in df.columns]
    return df


def _project_state_machine(df_full: pd.DataFrame, today_str: str):
    """
    Run state machine on df_full (history + provisional bar).
    Returns (proj_eff_state, last_trade_today_or_None, derived_today_dict).
    """
    sys.path.insert(0, str(ROOT))
    from pipeline.state_machine import (
        _compute_derived, _run_state_machine,
        RISK_ON, EXIT, ACCUM,
    )

    df_d = _compute_derived(df_full.copy())
    ytd_start = pd.Timestamp(f"{pd.Timestamp(today_str).year}-01-01")
    df_ytd = df_d[df_d.index >= ytd_start].copy()

    states, accum_flags, trades = _run_state_machine(df_ytd)

    # Post-transition state (same logic as compute.py fix)
    proj_state = states[-1]
    last_trade_today = None
    if trades and trades[-1]["date"] == today_str:
        last_trade_today = trades[-1]
        if last_trade_today["action"] == "REENTER":
            proj_state = RISK_ON
        elif last_trade_today["action"] == "EXIT":
            proj_state = EXIT

    last_accum = accum_flags[-1]
    proj_eff = ACCUM if (proj_state == RISK_ON and last_accum) else proj_state

    # Derived scalars for today
    row = df_ytd.iloc[-1]

    def _s(col, default=0.0):
        v = row.get(col, default)
        return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else default

    derived = {
        "id20":   _s("id20"),
        "ret20":  _s("ret20"),
        "id_t":   _s("id"),
        "ret_t":  _s("ret"),
        "close":  _s("close"),
        "ma20":   _s("ma20"),
        "id20_z": _s("id20_z"),
        "on20":   _s("on20"),
    }
    return proj_eff, last_trade_today, derived


def _classify(settled_state: str, proj_eff: str, last_trade_today, derived: dict):
    """
    Returns (action, moc_eligible, margins, action_class, note).
    """
    sys.path.insert(0, str(ROOT))
    from pipeline.state_machine import (
        ARM_DIV_ID, ARM_DIV_RET, ARM_ABS_ID, ARM_Z,
        EXIT_ID, EXIT_DAY, ACC_ID, ACC_RET,
        RISK_ON, EXIT, ACCUM, MONITOR,
    )

    id20   = derived["id20"]
    ret20  = derived["ret20"]
    id_t   = derived["id_t"]
    ret_t  = derived["ret_t"]
    close  = derived["close"]
    ma20   = derived["ma20"]
    id20_z = derived["id20_z"]

    if last_trade_today:
        action = last_trade_today["action"]
        reason = last_trade_today.get("reason", "")
        moc_eligible = action in ("EXIT", "REENTER")
    elif proj_eff != settled_state:
        if settled_state in (RISK_ON, ACCUM) and proj_eff == MONITOR:
            action, reason = "ARM", ""
        elif settled_state == MONITOR and proj_eff in (RISK_ON, ACCUM):
            action, reason = "DISARM", ""
        else:
            action, reason = "ARM", ""
        moc_eligible = False
    else:
        return "NONE", False, None, "NONE", "No projected transition"

    # Compute margin over triggering threshold
    margins = None
    threshold = BUFFER_20D

    if action == "ARM":
        if id20 < ARM_DIV_ID and ret20 > ARM_DIV_RET:
            m = min(abs(id20 - ARM_DIV_ID), abs(ret20 - ARM_DIV_RET))
            margins = {"trigger": "id20_div", "value": round(id20, 4),
                       "threshold": ARM_DIV_ID, "buffer": round(m, 4)}
        elif id20 < ARM_ABS_ID:
            m = abs(id20 - ARM_ABS_ID)
            margins = {"trigger": "id20_abs", "value": round(id20, 4),
                       "threshold": ARM_ABS_ID, "buffer": round(m, 4)}
        else:
            m = abs(id20_z - ARM_Z)
            margins = {"trigger": "id20_z", "value": round(id20_z, 4),
                       "threshold": ARM_Z, "buffer": round(m, 4)}

    elif action == "DISARM":
        m = abs(id20)
        margins = {"trigger": "disarm", "value": round(id20, 4),
                   "threshold": 0.0, "buffer": round(m, 4)}

    elif action == "EXIT":
        if "escape" in reason:
            margins = {"trigger": "escape_valve", "value": None,
                       "threshold": None, "buffer": None}
        elif id_t > EXIT_ID:
            m = id_t - EXIT_ID
            margins = {"trigger": "id_t", "value": round(id_t, 4),
                       "threshold": EXIT_ID, "buffer": round(m, 4)}
            threshold = BUFFER_DAY
        else:
            m = ret_t - EXIT_DAY
            margins = {"trigger": "ret_t", "value": round(ret_t, 4),
                       "threshold": EXIT_DAY, "buffer": round(m, 4)}
            threshold = BUFFER_DAY

    elif action == "REENTER":
        if "accum" in reason:
            m = min(abs(id20 - ACC_ID), abs(ret20 - ACC_RET))
            margins = {"trigger": "accum", "value": round(id20, 4),
                       "threshold": ACC_ID, "buffer": round(m, 4)}
        elif "trend reclaim" in reason or (ma20 > 0 and close > ma20):
            m = abs(close / ma20 - 1) if ma20 > 0 else 0.0
            margins = {"trigger": "ma20", "value": round(close, 2),
                       "threshold": round(ma20, 2), "buffer": round(m, 4)}
            threshold = BUFFER_MA
        else:  # disarm
            m = abs(id20)
            margins = {"trigger": "disarm", "value": round(id20, 4),
                       "threshold": 0.0, "buffer": round(m, 4)}

    # Action class
    buf = margins.get("buffer") if margins else None
    if buf is None:
        ac = "BORDERLINE"
    else:
        ac = "CLEAR" if buf >= threshold else "BORDERLINE"

    # Note
    if margins and margins.get("buffer") is not None:
        note = (
            f"{margins['trigger']} margin {margins['buffer']:.3f} "
            f"({'≥' if ac == 'CLEAR' else '<'} buffer {threshold:.3f})"
        )
    else:
        note = f"{action} projected (margin not computable)"

    return action, moc_eligible, margins, ac, note


def run_preview(
    *,
    now_et: datetime | None = None,
    ticker: str = "SOXX",
    _snapshot_override=None,   # for tests: (open, close, high, low, et_str)
    _history_override: "pd.DataFrame | None" = None,
) -> dict:
    """
    Main entry point. Returns the preview dict (also written to PREVIEW_JSON).
    Keyword-only overrides allow unit testing without network or filesystem.
    """
    import pytz
    if now_et is None:
        now_et = datetime.now(pytz.timezone(_ET))
    today = now_et.date()
    today_str = today.isoformat()

    # 1. Abort guard
    if (now_et.hour, now_et.minute) >= ABORT_CUTOFF:
        msg = f"Preview aborted: past {ABORT_CUTOFF[0]}:{ABORT_CUTOFF[1]:02d} ET"
        print(msg, file=sys.stderr)
        sys.exit(1)

    late = (now_et.hour, now_et.minute) >= LATE_CUTOFF

    def _write(out: dict) -> dict:
        PREVIEW_JSON.write_text(json.dumps(out, indent=2))
        return out

    # 2. Half-day guard
    if _snapshot_override is None and _is_half_day(today):
        return _write({
            "date": today_str,
            "snapshot_et": now_et.strftime("%H:%M"), "late": late,
            "skipped": "half-day",
            "spot": None, "provisional_ret": None, "provisional_id": None,
            "projected_state": None, "current_settled_state": None,
            "projected_action": "NONE", "action_class": "NONE",
            "moc_eligible": False, "margins": None,
            "volume_unreliable": True,
            "note": "Half-day: preview skipped",
        })

    # 3. Current settled state
    signals_path = DATA_DIR / "signals.json"
    current_settled_state = "UNKNOWN"
    if signals_path.exists():
        with open(signals_path) as f:
            current_settled_state = json.load(f).get("state", {}).get("machine", "UNKNOWN")

    # 4. Intraday snapshot
    snap = _snapshot_override if _snapshot_override is not None else _fetch_snapshot(ticker)
    if snap is None:
        return _write({
            "date": today_str,
            "snapshot_et": now_et.strftime("%H:%M"), "late": late,
            "skipped": "fetch_failed",
            "spot": None, "provisional_ret": None, "provisional_id": None,
            "projected_state": None,
            "current_settled_state": current_settled_state,
            "projected_action": "NONE", "action_class": "NONE",
            "moc_eligible": False, "margins": None,
            "volume_unreliable": True,
            "note": "Intraday fetch failed",
        })

    today_open, last_price, session_high, session_low, snapshot_et = snap

    # 5. Build provisional bar
    history = _history_override if _history_override is not None else _load_history()
    prev_close = float(history["close"].iloc[-1])
    provisional_ret = (last_price - prev_close) / prev_close
    provisional_id  = (last_price - today_open) / today_open

    new_row = pd.DataFrame(
        [[today_open, session_high, session_low, last_price, 0]],
        columns=["open", "high", "low", "close", "volume"],
        index=[pd.Timestamp(today_str)],
    )
    df_ext = pd.concat([history, new_row])

    # 6. Project state machine
    try:
        proj_eff, last_trade_today, derived = _project_state_machine(df_ext, today_str)
    except Exception as e:
        print(f"State machine projection failed: {e}", file=sys.stderr)
        return _write({
            "date": today_str,
            "snapshot_et": snapshot_et, "late": late, "skipped": None,
            "spot": round(last_price, 2),
            "provisional_ret": round(provisional_ret, 4),
            "provisional_id": round(provisional_id, 4),
            "projected_state": None,
            "current_settled_state": current_settled_state,
            "projected_action": "NONE", "action_class": "NONE",
            "moc_eligible": False, "margins": None,
            "volume_unreliable": True,
            "note": f"Projection error: {e}",
        })

    # 7. Classify action and compute margins
    action, moc_eligible, margins, ac, note = _classify(
        current_settled_state, proj_eff, last_trade_today, derived
    )

    if late and action != "NONE":
        moc_eligible = False
        note += " [LATE — MOC window closed]"

    out = {
        "date": today_str,
        "snapshot_et": snapshot_et,
        "late": late,
        "skipped": None,
        "spot": round(last_price, 2),
        "provisional_ret": round(provisional_ret, 4),
        "provisional_id": round(provisional_id, 4),
        "projected_state": proj_eff,
        "current_settled_state": current_settled_state,
        "projected_action": action,
        "action_class": ac,
        "moc_eligible": moc_eligible and not late,
        "margins": margins,
        "volume_unreliable": True,
        "note": note,
    }
    print(f"Preview: {action} ({ac}) spot={last_price:.2f} at {snapshot_et} ET")
    return _write(out)


if __name__ == "__main__":
    run_preview()
