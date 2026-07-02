"""
State machine: pure function, DataFrame → signals dict.
All constants at the top are in-sample choices (Jan–Jul 2026, n=3 episodes).
"""
import numpy as np
import pandas as pd

# ── In-sample thresholds ────────────────────────────────────────────────────
ARM_DIV_ID       = 0.00    # Mode A: armed if id20 < 0 AND ret20 > +2%
ARM_DIV_RET      = 0.02
ARM_ABS_ID       = -0.03   # Mode A: OR id20 < -3%
ARM_Z            = -1.0    # Mode B: id20 252-day z-score threshold
COLLAPSE_GATE    = 0.08    # Mode A gate: id20 must have fallen ≥ 8pts from 60-session max
ACC_ID           = 0.02    # accumulation: id20 > +2% AND ret20 < -2%
ACC_RET          = -0.02
ACCUM_STOP_PX    = 0.08    # invalidate ACCUM if close drops 8% from ACCUM-start close
ACCUM_STOP_ID    = 0.01    # invalidate ACCUM if id20 rolls back below +1%
EXIT_ID          = 0.01    # exec-into-strength: daily intraday > +1%
EXIT_DAY         = 0.015   # OR total daily return > +1.5%
ESCAPE_SESSIONS  = 3       # escape valve after N sessions armed with no strength
DISARM_SESSIONS  = 2       # retained for documentation; disarm is now unconditional
REENTER_MA_DAYS  = 2       # closes above ma20 needed for trend-reclaim re-entry
WARMUP_SESSIONS  = 20      # first N sessions of YTD are warm-up (no signals)
SLIPPAGE_BPS     = 5       # basis points per trade side (applied in backtest)
# ── on20_mom sizing tilt (display only — never a state transition) ─────────
ONMOM_TILT       = -0.05   # on20_mom threshold for sizing tilt in MONITOR
ONMOM_FACTOR     = 0.8     # multiply suggested_size by this when tilt is active
# ── Config-flagged optional behaviors (both DEFAULT OFF) ─────────────────
# Change evidence per METHODOLOGY.md §9 / PRESSURE_TEST.md:
ENSEMBLE_ARM     = False   # 2-of-3 vote at N=15/20/25; -4.1pts 2026, +2.2pts H2-2025 (P4)
CRASH_GATE       = False   # suppress ABS arm when dd20 ≤ CRASH_GATE_DD (P5)
CRASH_GATE_DD    = -0.10   # knife-edge threshold; ON only after multi-year calibration
# ────────────────────────────────────────────────────────────────────────────

RISK_ON, MONITOR, EXIT, WARMUP, ACCUM = "RISK_ON", "MONITOR", "EXIT", "WARMUP", "ACCUM"

STATE_POS = {RISK_ON: 1.0, MONITOR: 0.6, EXIT: 0.0, WARMUP: 0.0, ACCUM: 1.0}


def _wilder_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _compute_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Add all derived columns to df (operates on full history for accuracy)."""
    df = df.copy()
    df["ret"]      = df["close"] / df["close"].shift(1) - 1
    df["on"]       = df["open"] / df["close"].shift(1) - 1
    df["id"]       = df["close"] / df["open"] - 1
    df["on20"]     = df["on"].rolling(20).sum()
    df["id20"]     = df["id"].rolling(20).sum()
    df["ret20"]    = df["ret"].rolling(20).sum()
    df["ma20"]     = df["close"].rolling(20).mean()
    df["ma50"]     = df["close"].rolling(50).mean()
    df["ma200"]    = df["close"].rolling(200).mean()
    df["rv10"]     = df["ret"].rolling(10).std() * np.sqrt(252)
    df["rv20"]     = df["ret"].rolling(20).std() * np.sqrt(252)
    df["turb"]     = (
        (df["ret"] - df["ret"].rolling(60).mean()).abs()
        / df["ret"].rolling(60).std()
    )
    df["ar1"] = df["ret"].rolling(20).apply(
        lambda x: pd.Series(x).autocorr() if pd.Series(x).std() > 0 else np.nan,
        raw=False,
    )
    df["rsi14"]    = _wilder_rsi(df["close"], 14)
    df["vol30"]    = df["volume"].rolling(30).mean()
    df["dist_day"] = (df["ret"] <= -0.01) & (df["volume"] > 1.3 * df["vol30"])
    df["dist20"]   = df["dist_day"].rolling(20).sum()
    # Hybrid arm series
    _id20_mean     = df["id20"].rolling(252, min_periods=120).mean()
    _id20_std      = df["id20"].rolling(252, min_periods=120).std()
    df["id20_z"]   = (df["id20"] - _id20_mean) / _id20_std.replace(0, np.nan)
    df["id20_max60"] = df["id20"].rolling(60).max()
    # Conditional sizing series
    df["rv20_p90"] = df["rv20"].rolling(252, min_periods=120).quantile(0.90)
    # New v3.2 derived series
    # on20_mom: 5-session change of overnight 20d stream (P2 — display + sizing tilt only)
    df["on20_mom"] = df["on20"] - df["on20"].shift(5)
    # dd20: 20-session drawdown from rolling max (used by CRASH_GATE)
    df["dd20"] = df["close"] / df["close"].rolling(20).max() - 1
    # Multi-N series for ENSEMBLE_ARM (N=15, 25; N=20 already exists as id20/ret20)
    df["id20_15"]     = df["id"].rolling(15).sum()
    df["ret20_15"]    = df["ret"].rolling(15).sum()
    _i15_mean         = df["id20_15"].rolling(252, min_periods=120).mean()
    _i15_std          = df["id20_15"].rolling(252, min_periods=120).std()
    df["id20_z_15"]   = (df["id20_15"] - _i15_mean) / _i15_std.replace(0, np.nan)
    df["id20_max60_15"] = df["id20_15"].rolling(60).max()
    df["id20_25"]     = df["id"].rolling(25).sum()
    df["ret20_25"]    = df["ret"].rolling(25).sum()
    _i25_mean         = df["id20_25"].rolling(252, min_periods=120).mean()
    _i25_std          = df["id20_25"].rolling(252, min_periods=120).std()
    df["id20_z_25"]   = (df["id20_25"] - _i25_mean) / _i25_std.replace(0, np.nan)
    df["id20_max60_25"] = df["id20_25"].rolling(60).max()
    # FRED-sourced columns (injected by compute.py; NaN when unavailable)
    if "vix_slope" not in df.columns:
        df["vix_slope"] = np.nan
    if "real_chg20" not in df.columns:
        df["real_chg20"] = np.nan
    return df


def _run_state_machine(
    df: pd.DataFrame,
    arm_mode: str = "hybrid",
    ensemble_arm: bool = ENSEMBLE_ARM,
    crash_gate: bool = CRASH_GATE,
    crash_gate_dd: float = CRASH_GATE_DD,
) -> tuple[list[str], list[bool], list[dict]]:
    """
    Run state machine row-by-row on YTD df (with pre-computed derived columns).
    Returns (states, accum_flags, trades).

    arm_mode:
      'hybrid'  — production rule: armB OR (armA AND collapse gate)
      'A'       — Mode A only (absolute thresholds); used for regression tests

    ensemble_arm: when True, armed = 2-of-3 vote of hybrid conditions at N=15/20/25.
    crash_gate: when True, suppress ABS arm (id20 < -3%) when dd20 ≤ crash_gate_dd.

    Key design decisions (Change 1 / order-of-operations — any deviation is a CI failure):
    1. Compute today's signals from today's official close.
    2. Update arm state (RISK_ON→MONITOR possible today).
    3. THEN evaluate exit triggers in the same session — same-day arm+exit is legal.
    4. Disarm counting starts the session AFTER arming.
    5. Re-entry checks evaluate only in sessions after the exit fill.
    6. All fills at the evaluation session's close, minus SLIPPAGE_BPS.

    on20_mom is EXPLICITLY FORBIDDEN in this function (display/sizing only, P2).
    """
    states: list[str] = []
    accum_flags: list[bool] = []
    trades: list[dict] = []

    state = RISK_ON
    sessions_since_arm = 0
    sessions_since_fired = 0
    reenter_above_ma20 = 0
    accum_active = False
    accum_start_close: float | None = None

    def _f(row, col, default=0.0):
        v = row.get(col) if isinstance(row, dict) else getattr(row, col, None)
        if v is None:
            return default
        try:
            f = float(v)
            return default if np.isnan(f) else f
        except Exception:
            return default

    def _fnan(row, col):
        """Return float or np.nan (never a fallback default)."""
        v = row.get(col) if isinstance(row, dict) else getattr(row, col, None)
        if v is None:
            return np.nan
        try:
            return float(v)
        except Exception:
            return np.nan

    def _arm_for_n(id20_N, ret20_N, id20_z_N, id20_max60_N, dd20_val):
        """Hybrid arm condition for one N-session window (used by ENSEMBLE_ARM)."""
        armA_abs_N = id20_N < ARM_ABS_ID
        if crash_gate and dd20_val <= crash_gate_dd:
            armA_abs_N = False  # suppress ABS arm during deep drawdown (CRASH_GATE)
        armA_N = (id20_N < ARM_DIV_ID and ret20_N > ARM_DIV_RET) or armA_abs_N
        armB_N = not np.isnan(id20_z_N) and id20_z_N < ARM_Z and ret20_N > ARM_DIV_RET
        collapse_N = (id20_max60_N - id20_N) >= COLLAPSE_GATE
        return armB_N or (armA_N and collapse_N)

    for i, (idx, row) in enumerate(df.iterrows()):
        if i < WARMUP_SESSIONS:
            states.append(WARMUP)
            accum_flags.append(False)
            continue

        id20       = _f(row, "id20")
        ret20      = _f(row, "ret20")
        id_t       = _f(row, "id")
        ret_t      = _f(row, "ret")
        ma20       = _f(row, "ma20", default=row["close"])
        close      = float(row["close"])
        id20_z     = _fnan(row, "id20_z")
        id20_max60 = _f(row, "id20_max60")
        dd20       = _f(row, "dd20")
        date_str   = idx.strftime("%Y-%m-%d")

        # ── Arm condition (Change 1: evaluated BEFORE exit trigger in same session) ──
        armA_abs = id20 < ARM_ABS_ID
        if crash_gate and dd20 <= crash_gate_dd:
            armA_abs = False  # suppress ABS arm (CRASH_GATE)
        armA = (id20 < ARM_DIV_ID and ret20 > ARM_DIV_RET) or armA_abs

        if arm_mode == "A":
            armed_cond = armA
        elif ensemble_arm:
            # 2-of-3 majority vote across N=15, 20, 25
            v1 = int(_arm_for_n(id20, ret20, id20_z, id20_max60, dd20))
            v2 = int(_arm_for_n(
                _f(row, "id20_15"), _f(row, "ret20_15"),
                _fnan(row, "id20_z_15"), _f(row, "id20_max60_15"), dd20,
            ))
            v3 = int(_arm_for_n(
                _f(row, "id20_25"), _f(row, "ret20_25"),
                _fnan(row, "id20_z_25"), _f(row, "id20_max60_25"), dd20,
            ))
            armed_cond = (v1 + v2 + v3) >= 2
        else:  # default hybrid
            id20_z_valid = not np.isnan(id20_z)
            armB = id20_z_valid and (id20_z < ARM_Z) and (ret20 > ARM_DIV_RET)
            armed_cond = armB or (armA and (id20_max60 - id20) >= COLLAPSE_GATE)

        acc_cond = id20 > ACC_ID and ret20 < ACC_RET
        strength = id_t > EXIT_ID or ret_t > EXIT_DAY

        display_state = state

        if state == RISK_ON:
            # ── ACCUM overlay management ───────────────────────────────────
            if accum_active:
                stop_px = accum_start_close is not None and close < accum_start_close * (1 - ACCUM_STOP_PX)
                stop_id = id20 < ACCUM_STOP_ID
                if stop_px or stop_id or not acc_cond:
                    accum_active = False
                    accum_start_close = None
                    # Arming resumes this same session (fall through below)

            if not accum_active and acc_cond:
                accum_active = True
                accum_start_close = close

            # ── Arm check (skipped while ACCUM active) ────────────────────
            if not accum_active and armed_cond:
                display_state = MONITOR
                if strength:
                    state = EXIT
                    sessions_since_fired = 0
                    reenter_above_ma20 = 0
                    trades.append({"date": date_str, "price": round(close, 2),
                                   "action": "EXIT", "reason": "exec-into-strength"})
                else:
                    state = MONITOR
                    sessions_since_arm = 0

        elif state == MONITOR:
            sessions_since_arm += 1

            if acc_cond:
                state = RISK_ON
                sessions_since_arm = 0
                accum_active = True
                accum_start_close = close
            elif not armed_cond:
                state = RISK_ON
                sessions_since_arm = 0
            elif strength:
                state = EXIT
                sessions_since_fired = 0
                reenter_above_ma20 = 0
                trades.append({"date": date_str, "price": round(close, 2),
                               "action": "EXIT", "reason": "exec-into-strength"})
                sessions_since_arm = 0
            elif sessions_since_arm >= ESCAPE_SESSIONS and close < ma20:
                state = EXIT
                sessions_since_fired = 0
                reenter_above_ma20 = 0
                trades.append({"date": date_str, "price": round(close, 2),
                               "action": "EXIT", "reason": "escape-valve"})
                sessions_since_arm = 0

        elif state == EXIT:
            sessions_since_fired += 1
            accum_active = False
            accum_start_close = None

            if not armed_cond:
                state = RISK_ON
                sessions_since_fired = 0
                reenter_above_ma20 = 0
                trades.append({"date": date_str, "price": round(close, 2),
                               "action": "REENTER", "reason": "disarm"})
            elif acc_cond:
                state = RISK_ON
                sessions_since_fired = 0
                reenter_above_ma20 = 0
                accum_active = True
                accum_start_close = close
                trades.append({"date": date_str, "price": round(close, 2),
                               "action": "REENTER", "reason": "accumulation flip"})
            elif close > ma20:
                reenter_above_ma20 += 1
                if reenter_above_ma20 >= REENTER_MA_DAYS and id20 > 0:
                    state = RISK_ON
                    sessions_since_fired = 0
                    reenter_above_ma20 = 0
                    trades.append({"date": date_str, "price": round(close, 2),
                                   "action": "REENTER", "reason": "trend reclaim"})
            else:
                reenter_above_ma20 = 0

        states.append(display_state)
        accum_flags.append(accum_active and state == RISK_ON)

    return states, accum_flags, trades


def _build_bands(df: pd.DataFrame, states: list[str], accum_flags: list[bool]) -> list[dict]:
    """Build contiguous date bands from state sequence."""
    bands: list[dict] = []
    prev_eff = None
    band_start = None

    for i, (idx, _) in enumerate(df.iterrows()):
        s = states[i]
        eff = ACCUM if (s == RISK_ON and accum_flags[i]) else s
        if eff != prev_eff:
            if prev_eff is not None:
                bands.append({
                    "start": band_start.strftime("%Y-%m-%d"),
                    "end": df.index[i - 1].strftime("%Y-%m-%d"),
                    "state": prev_eff,
                })
            band_start = idx
            prev_eff = eff

    if prev_eff is not None:
        bands.append({
            "start": band_start.strftime("%Y-%m-%d"),
            "end": None,
            "state": prev_eff,
        })
    return bands


def _run_backtest(
    df: pd.DataFrame,
    states: list[str],
    accum_flags: list[bool],
    trades: list[dict],
) -> tuple[list, list]:
    """Simulate strategy equity from Jan 20; returns (equity_strategy, equity_bh).
    Applies SLIPPAGE_BPS per trade side (net equity only)."""
    backtest_start = pd.Timestamp(f"{df.index[-1].year}-01-20")
    trade_dates = {t["date"] for t in trades}
    slip = SLIPPAGE_BPS / 10_000

    eq_s: list = []
    eq_bh: list = []
    cum_s = 1.0
    cum_bh = 1.0

    def _f(v):
        try:
            f = float(v)
            return f if not np.isnan(f) else 0.0
        except Exception:
            return 0.0

    for i, (idx, row) in enumerate(df.iterrows()):
        if idx < backtest_start:
            eq_s.append(None)
            eq_bh.append(None)
            continue
        ret = _f(row.get("ret") if isinstance(row, pd.Series) else getattr(row, "ret", 0))
        s = states[i]
        pos = STATE_POS.get(s, 1.0)
        if accum_flags[i]:
            pos = 1.0
        cum_s *= 1 + ret * pos
        if idx.strftime("%Y-%m-%d") in trade_dates:
            cum_s *= 1 - slip
        cum_bh *= 1 + ret
        eq_s.append(round(cum_s, 6))
        eq_bh.append(round(cum_bh, 6))

    return eq_s, eq_bh


def compute_signals(
    df: pd.DataFrame,
    manual: dict | None = None,
    arm_mode: str = "hybrid",
) -> dict:
    """
    Full pipeline: raw OHLCV → signals dict matching signals.json schema.
    df must be the full history DataFrame (≥ 410 sessions ideally for 252d z-scores).
    manual: optional {"iv30": float, "iv30_asof": str, "pc_oi": float, "pc_oi_asof": str}
    arm_mode: 'hybrid' (default, production) or 'A' (Mode-A regression tests only).
    """
    if manual is None:
        manual = {}

    # 1. Compute derived columns on full history for window accuracy
    df_full = _compute_derived(df)

    # 2. State machine runs on YTD only (resets each Jan 1)
    ytd_start = pd.Timestamp(f"{df_full.index[-1].year}-01-01")
    df_ytd = df_full[df_full.index >= ytd_start].copy()

    states, accum_flags, trades = _run_state_machine(df_ytd, arm_mode=arm_mode)

    # 3. Build bands and backtest
    bands = _build_bands(df_ytd, states, accum_flags)
    eq_s, eq_bh = _run_backtest(df_ytd, states, accum_flags, trades)

    # 4. Last-row summary
    last = df_full.iloc[-1]
    last_ytd = df_ytd.iloc[-1]
    last_state = states[-1]
    last_accum = accum_flags[-1]
    pos_mult = 1.0 if last_accum else STATE_POS.get(last_state, 0.0)
    rv20_last = float(last["rv20"]) if not np.isnan(last["rv20"]) else 1.0

    # Conditional sizing: only scale down in extreme-vol regime (rv20 > 90th pct)
    rv20_p90_last = float(last["rv20_p90"]) if not np.isnan(last["rv20_p90"]) else None
    if rv20_p90_last is not None and rv20_last > rv20_p90_last:
        size_base = min(1.0, 0.40 / max(rv20_last, 0.001))
    else:
        size_base = 1.0

    # on20_mom sizing tilt in MONITOR (DISPLAY ONLY — explicitly forbidden in transitions)
    on20_mom_last = _safe_val(last_ytd, "on20_mom")
    if last_state == MONITOR and on20_mom_last is not None and on20_mom_last < ONMOM_TILT:
        pos_mult = pos_mult * ONMOM_FACTOR

    suggested_size = round(size_base * pos_mult, 3)

    iv30 = manual.get("iv30")
    vrp = round(float(iv30) - rv20_last, 4) if iv30 is not None else None

    # Compute last-day arm mode flags (for "modes split" badge)
    id20_last  = float(last_ytd["id20"])  if not np.isnan(last_ytd["id20"])  else 0.0
    ret20_last = float(last_ytd["ret20"]) if not np.isnan(last_ytd["ret20"]) else 0.0
    id20_z_last   = float(last_ytd["id20_z"])    if not np.isnan(last_ytd["id20_z"])    else np.nan
    id20_max60_last = float(last_ytd["id20_max60"]) if not np.isnan(last_ytd["id20_max60"]) else 0.0
    arm_mode_a_last = (id20_last < ARM_DIV_ID and ret20_last > ARM_DIV_RET) or (id20_last < ARM_ABS_ID)
    arm_mode_b_last = (
        not np.isnan(id20_z_last)
        and id20_z_last < ARM_Z
        and ret20_last > ARM_DIV_RET
    )

    # Short-permission indicator (3 conditions, per-condition breakdown)
    close_sv  = _safe_val(last, "close")
    ma50_sv   = _safe_val(last, "ma50")
    id20_sv   = _safe_val(last_ytd, "id20")
    on20_sv   = _safe_val(last_ytd, "on20")
    vol_sv    = _safe_val(last, "volume")
    vol30_sv  = _safe_val(last, "vol30")
    sp_ma50_vol = bool(
        close_sv is not None and ma50_sv is not None and close_sv < ma50_sv
        and vol_sv is not None and vol30_sv is not None and vol30_sv > 0
        and vol_sv > 1.5 * vol30_sv
    )
    sp_id20_neg = bool(id20_sv is not None and id20_sv < 0)
    sp_on20_neg = bool(on20_sv is not None and on20_sv < 0)
    short_permission = {
        "ma50_vol": sp_ma50_vol,
        "id20_neg": sp_id20_neg,
        "on20_neg": sp_on20_neg,
        "all": bool(sp_ma50_vol and sp_id20_neg and sp_on20_neg),
    }

    def _safe(v, digits):
        try:
            f = float(v)
            return round(f, digits) if not np.isnan(f) else None
        except Exception:
            return None

    # Build history
    ytd_date_strs = [d.strftime("%Y-%m-%d") for d in df_ytd.index]
    close_by_date = dict(zip(ytd_date_strs, [round(float(v), 2) for v in df_ytd["close"].values]))
    trade_by_prev_end = {t["date"]: t for t in trades}

    history: list[dict] = []
    for i, band in enumerate(bands):
        band_date = band["start"]
        band_state = band["state"]
        price = close_by_date.get(band_date)

        if band_state == WARMUP:
            if i == 0:
                history.append({"date": band_date, "state": band_state, "price": price, "reason": "session start"})
            continue

        prev_end = bands[i - 1]["end"] if i > 0 else None
        trade = trade_by_prev_end.get(prev_end) if prev_end else None

        if trade:
            history.append({
                "date": trade["date"],
                "state": band_state,
                "price": trade["price"],
                "reason": trade["reason"],
            })
        else:
            prev_band_state = bands[i - 1]["state"] if i > 0 else None
            if band_state == RISK_ON:
                if prev_band_state == WARMUP:
                    reason = "warmup complete"
                elif prev_band_state == MONITOR:
                    reason = "disarm"
                elif prev_band_state == ACCUM:
                    reason = "accumulation ended"
                else:
                    reason = "—"
            elif band_state == MONITOR:
                reason = "distribution signal"
            elif band_state == ACCUM:
                reason = "accumulation overlay"
            else:
                reason = "—"
            history.append({"date": band_date, "state": band_state, "price": price, "reason": reason})

    history.sort(key=lambda x: x["date"])

    current_eff_state = ACCUM if last_accum else last_state
    current_entry = next((h for h in reversed(history) if h["state"] == current_eff_state), None)
    state_since = current_entry["date"] if current_entry else df_ytd.index[0].strftime("%Y-%m-%d")

    return {
        "last_session": df_full.index[-1].strftime("%Y-%m-%d"),
        "data_stale": False,
        "state": {
            "machine": last_state,
            "accum_overlay": last_accum,
            "since": state_since,
            "position_multiplier": round(pos_mult, 3),
            "suggested_size": suggested_size,
            "arm_mode_a": arm_mode_a_last,
            "arm_mode_b": arm_mode_b_last,
            "short_permission": short_permission,
        },
        "today": {
            "close":      _safe(last["close"], 2),
            "ret":        _safe(last["ret"], 4),
            "id20":       _safe(last_ytd["id20"], 4),
            "on20":       _safe(last_ytd["on20"], 4),
            "ret20":      _safe(last_ytd["ret20"], 4),
            "id20_z":     _safe(last_ytd["id20_z"], 4),
            "on20_mom":   _safe(last_ytd["on20_mom"], 4),
            "dd20":       _safe(last_ytd["dd20"], 4),
            "vix_slope":  _safe(last["vix_slope"], 4) if "vix_slope" in last.index else None,
            "real_chg20": _safe(last["real_chg20"], 4) if "real_chg20" in last.index else None,
            "ma20":       _safe(last["ma20"], 2),
            "ma50":       _safe(last["ma50"], 2),
            "ma200":      _safe(last["ma200"], 2),
            "rv10":       _safe(last["rv10"], 4),
            "rv20":       _safe(last["rv20"], 4),
            "rv20_p90":   _safe(last["rv20_p90"], 4),
            "turb":       _safe(last["turb"], 4),
            "ar1":        _safe(last["ar1"], 4),
            "rsi14":      _safe(last["rsi14"], 4),
            "dist20":     int(last["dist20"]) if not np.isnan(last["dist20"]) else None,
            "vrp":        vrp,
            "iv30_asof":  manual.get("iv30_asof"),
        },
        "bands": bands,
        "trades": trades,
        "history": history,
        "series": {
            "dates":    [d.strftime("%Y-%m-%d") for d in df_ytd.index],
            "close":    [_safe(v, 2) for v in df_ytd["close"]],
            "ma20":     [_safe(v, 2) for v in df_ytd["ma20"]],
            "id20":     [_safe(v, 4) for v in df_ytd["id20"]],
            "id20_z":   [_safe(v, 4) for v in df_ytd["id20_z"]],
            "on20":     [_safe(v, 4) for v in df_ytd["on20"]],
            "on20_mom": [_safe(v, 4) for v in df_ytd["on20_mom"]],
            "rv20":     [_safe(v, 4) for v in df_ytd["rv20"]],
            "equity_strategy": eq_s,
            "equity_bh":       eq_bh,
            "smh_close":       [None] * len(df_ytd),
        },
        "checklist": _build_checklist(last, last_ytd, vrp, manual),
        "events": [],
    }


def _safe_val(row, col):
    """Return float or None for a pandas Series row (NaN → None)."""
    try:
        v = float(row[col]) if col in row.index else np.nan
        return v if not np.isnan(v) else None
    except Exception:
        return None


def _build_checklist(last: pd.Series, last_ytd: pd.Series, vrp: float | None, manual: dict) -> list[dict]:
    def _safe(v, digits):
        try:
            f = float(v)
            return round(f, digits) if not np.isnan(f) else None
        except Exception:
            return None

    id20     = _safe(last_ytd["id20"], 4) if "id20" in last_ytd.index else None
    id20_z   = _safe(last_ytd["id20_z"], 4) if "id20_z" in last_ytd.index else None
    on20     = _safe(last_ytd["on20"], 4) if "on20" in last_ytd.index else None
    on20_mom = _safe(last_ytd["on20_mom"], 4) if "on20_mom" in last_ytd.index else None
    ma20     = _safe(last["ma20"], 2)
    close    = float(last["close"])
    rv20     = _safe(last["rv20"], 4)
    real_chg20 = _safe(last["real_chg20"], 4) if "real_chg20" in last.index else None
    ma20_pct = round(close / ma20 - 1, 4) if ma20 else None

    return [
        {
            "id": "id20", "label": "Intraday 20d stream",
            "value": id20, "fmt": "pct1",
            "status": "red" if (id20 is not None and id20 < 0) else "green",
            "note": "negative = distribution (institutions selling intraday)" if (id20 is not None and id20 < 0) else "positive = institutional buying",
        },
        {
            "id": "on20", "label": "Overnight 20d stream",
            "value": on20, "fmt": "pct1",
            "status": (
                "red" if (on20 is not None and id20 is not None and on20 < 0 and id20 < 0)
                else "amber" if (on20 is not None and id20 is not None and on20 > 0 and id20 < 0)
                else "green"
            ),
            "note": "fading overnight bid while id20 negative = divergence resolving",
        },
        {
            "id": "ma", "label": "Close vs 20-DMA",
            "value": ma20_pct, "fmt": "pct1",
            "status": "red" if (ma20_pct is not None and ma20_pct < -0.01) else "amber" if (ma20_pct is not None and ma20_pct < 0.02) else "green",
            "note": f"re-entry line {ma20:.1f}" if ma20 else "N/A",
        },
        {
            "id": "rv20", "label": "Realized vol (20d)",
            "value": rv20, "fmt": "pct0",
            "status": "red" if (rv20 is not None and rv20 > 0.60) else "amber" if (rv20 is not None and rv20 > 0.40) else "green",
            "note": "regime normalizes < 50%; sets position size",
        },
        {
            "id": "vrp", "label": "IV30 − RV20",
            "value": vrp, "fmt": "pts",
            "status": "amber" if vrp is not None else "grey",
            "note": (
                "puts cheap vs realized — hedge with options" if (vrp is not None and vrp < -0.10)
                else "insurance rich — de-risk via share sales" if (vrp is not None and vrp > 0.10)
                else "neutral" if vrp is not None else "no IV data (update data/manual.json)"
            ),
        },
        {
            "id": "id20_z", "label": "id20 z-score (252d)",
            "value": id20_z, "fmt": "z",
            "status": (
                "red"   if (id20_z is not None and id20_z < ARM_Z)
                else "amber" if (id20_z is not None and id20_z < -0.5)
                else "green" if id20_z is not None
                else "gray"
            ),
            "note": (
                "Mode B armed — relative distribution vs 1-yr baseline" if (id20_z is not None and id20_z < ARM_Z)
                else "approaching arm threshold" if (id20_z is not None and id20_z < -0.5)
                else "positive — no relative distribution signal" if id20_z is not None
                else "< 120 sessions — Mode B not yet available"
            ),
        },
        {
            "id": "on20_mom", "label": "on20 momentum (5d)",
            "value": on20_mom, "fmt": "pct1",
            "status": (
                "red"   if (on20_mom is not None and on20_mom < ONMOM_TILT)
                else "amber" if (on20_mom is not None and on20_mom < -0.02)
                else "green" if on20_mom is not None
                else "gray"
            ),
            "note": (
                f"sizing tilt active (×{ONMOM_FACTOR}) — overnight bid fading" if (on20_mom is not None and on20_mom < ONMOM_TILT)
                else "overnight bid weakening" if (on20_mom is not None and on20_mom < -0.02)
                else "overnight bid momentum positive" if on20_mom is not None
                else "insufficient data"
            ),
        },
        {
            "id": "real_chg20", "label": "Real yield Δ20d",
            "value": real_chg20, "fmt": "bp",
            "status": (
                "red"   if (real_chg20 is not None and real_chg20 > 0.20)
                else "amber" if (real_chg20 is not None and real_chg20 > 0.10)
                else "green" if real_chg20 is not None
                else "gray"
            ),
            "note": (
                "rising real yields — headwind for growth/tech" if (real_chg20 is not None and real_chg20 > 0.10)
                else "stable/falling real yields" if real_chg20 is not None
                else "context only — no position logic (sizing overlay rejected P6); FRED unavailable"
                if real_chg20 is None else ""
            ),
        },
    ]
