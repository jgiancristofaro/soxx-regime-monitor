"""
State machine: pure function, DataFrame → signals dict.
All constants at the top are in-sample choices (Jan–Jul 2026, n=3 episodes).
"""
import numpy as np
import pandas as pd

# ── In-sample thresholds ────────────────────────────────────────────────────
ARM_DIV_ID       = 0.00    # armed if id20 < 0 AND ret20 > +2%
ARM_DIV_RET      = 0.02
ARM_ABS_ID       = -0.03   # OR id20 < -3%
ACC_ID           = 0.02    # accumulation: id20 > +2% AND ret20 < -2%
ACC_RET          = -0.02
EXIT_ID          = 0.01    # exec-into-strength: daily intraday > +1%
EXIT_DAY         = 0.015   # OR total daily return > +1.5%
ESCAPE_SESSIONS  = 3       # escape valve after N sessions armed with no strength
DISARM_SESSIONS  = 2       # after N sessions armed, cancel if arm cond clears
REENTER_MA_DAYS  = 2       # closes above ma20 needed for trend-reclaim re-entry
WARMUP_SESSIONS  = 20      # first N sessions of YTD are warm-up (no signals)
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
    return df


def _run_state_machine(df: pd.DataFrame) -> tuple[list[str], list[bool], list[dict]]:
    """
    Run state machine row-by-row on YTD df (with pre-computed derived columns).
    Returns (states, accum_flags, trades).

    Key design decisions:
    - Warmup: first WARMUP_SESSIONS rows → no signals.
    - Same-day arm+exit: if arm condition first met on a strength day, EXIT that day
      (band records MONITOR for display; position is EXIT from next day).
    - Disarm priority: after DISARM_SESSIONS sessions in MONITOR, if arm cond clears,
      cancel (disarm) BEFORE checking exec-into-strength exit. This handles the
      June 9-10 episode (2 sessions in MONITOR → arm cond cleared Jun 11 → cancel).
    - EXIT disarm re-entry: if arm cond clears while EXIT → immediate re-entry.
    """
    # display_state is what gets recorded in the states list (for bands).
    # internal state (the `state` variable) drives the next day's logic.
    states: list[str] = []
    accum_flags: list[bool] = []
    trades: list[dict] = []

    state = RISK_ON
    sessions_since_arm = 0
    sessions_since_fired = 0
    reenter_above_ma20 = 0

    def _f(row, col, default=0.0):
        v = row.get(col) if isinstance(row, dict) else getattr(row, col, None)
        if v is None:
            return default
        try:
            f = float(v)
            return default if np.isnan(f) else f
        except Exception:
            return default

    for i, (idx, row) in enumerate(df.iterrows()):
        # Warm-up: no state machine decisions
        if i < WARMUP_SESSIONS:
            states.append(WARMUP)
            accum_flags.append(False)
            continue

        id20  = _f(row, "id20")
        ret20 = _f(row, "ret20")
        id_t  = _f(row, "id")
        ret_t = _f(row, "ret")
        ma20  = _f(row, "ma20", default=row["close"])
        close = row["close"]

        armed_cond = (id20 < ARM_DIV_ID and ret20 > ARM_DIV_RET) or (id20 < ARM_ABS_ID)
        acc_cond   = id20 > ACC_ID and ret20 < ACC_RET
        strength   = id_t > EXIT_ID or ret_t > EXIT_DAY

        display_state = state  # default: today's band = current state

        if state == RISK_ON:
            if armed_cond and not acc_cond:
                # Arm today — always show MONITOR in today's band
                display_state = MONITOR
                if strength:
                    # Same-day arm + exec-into-strength: EXIT immediately
                    state = EXIT
                    sessions_since_fired = 0
                    reenter_above_ma20 = 0
                    trades.append({
                        "date": idx.strftime("%Y-%m-%d"),
                        "price": round(close, 2),
                        "action": "EXIT",
                        "reason": "exec-into-strength",
                    })
                else:
                    state = MONITOR
                    sessions_since_arm = 0

        elif state == MONITOR:
            sessions_since_arm += 1

            if acc_cond:
                # Accumulation overrides MONITOR — stay invested, no exit
                state = RISK_ON
                sessions_since_arm = 0
            elif not armed_cond:
                # Arm condition cleared → disarm cancel immediately, no exit trade.
                # Priority over exec-into-strength: if the distribution signal
                # reversed, there is no reason to execute a defensive exit.
                state = RISK_ON
                sessions_since_arm = 0
            elif strength:
                # Armed condition still active AND exec-into-strength → EXIT
                state = EXIT
                sessions_since_fired = 0
                reenter_above_ma20 = 0
                trades.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "price": round(close, 2),
                    "action": "EXIT",
                    "reason": "exec-into-strength",
                })
                sessions_since_arm = 0
            elif sessions_since_arm >= ESCAPE_SESSIONS and close < ma20:
                # Escape valve: in MONITOR N sessions with no strength, below MA20
                state = EXIT
                sessions_since_fired = 0
                reenter_above_ma20 = 0
                trades.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "price": round(close, 2),
                    "action": "EXIT",
                    "reason": "escape-valve",
                })
                sessions_since_arm = 0

        elif state == EXIT:
            sessions_since_fired += 1

            if not armed_cond:
                # Original distribution signal cleared → disarm re-entry
                state = RISK_ON
                sessions_since_fired = 0
                reenter_above_ma20 = 0
                trades.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "price": round(close, 2),
                    "action": "REENTER",
                    "reason": "disarm",
                })
            elif acc_cond:
                # Accumulation flip (institutions buying the decline)
                state = RISK_ON
                sessions_since_fired = 0
                reenter_above_ma20 = 0
                trades.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "price": round(close, 2),
                    "action": "REENTER",
                    "reason": "accumulation flip",
                })
            elif close > ma20:
                reenter_above_ma20 += 1
                if reenter_above_ma20 >= REENTER_MA_DAYS and id20 > 0:
                    # Trend reclaim: N consecutive closes above MA20 with positive id20
                    state = RISK_ON
                    sessions_since_fired = 0
                    reenter_above_ma20 = 0
                    trades.append({
                        "date": idx.strftime("%Y-%m-%d"),
                        "price": round(close, 2),
                        "action": "REENTER",
                        "reason": "trend reclaim",
                    })
            else:
                reenter_above_ma20 = 0

        states.append(display_state)
        accum_flags.append(acc_cond and state == RISK_ON)

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
                # End previous band at the last day it was active
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


def _run_backtest(df: pd.DataFrame, states: list[str], accum_flags: list[bool]) -> tuple[list, list]:
    """Simulate strategy equity from Jan 20; returns (equity_strategy, equity_bh)."""
    backtest_start = pd.Timestamp(f"{df.index[-1].year}-01-20")
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
        cum_s  *= 1 + ret * pos
        cum_bh *= 1 + ret
        eq_s.append(round(cum_s, 6))
        eq_bh.append(round(cum_bh, 6))

    return eq_s, eq_bh


def compute_signals(df: pd.DataFrame, manual: dict | None = None) -> dict:
    """
    Full pipeline: raw OHLCV → signals dict matching signals.json schema.
    df must be the full history DataFrame (≥ 260 sessions ideally).
    manual: optional {"iv30": float, "iv30_asof": str, "pc_oi": float, "pc_oi_asof": str}
    """
    if manual is None:
        manual = {}

    # 1. Compute derived columns on full history for window accuracy
    df_full = _compute_derived(df)

    # 2. State machine runs on YTD only (resets each Jan 1)
    ytd_start = pd.Timestamp(f"{df_full.index[-1].year}-01-01")
    df_ytd = df_full[df_full.index >= ytd_start].copy()

    states, accum_flags, trades = _run_state_machine(df_ytd)

    # 3. Build bands and backtest
    bands = _build_bands(df_ytd, states, accum_flags)
    eq_s, eq_bh = _run_backtest(df_ytd, states, accum_flags)

    # 4. Last-row summary
    last = df_full.iloc[-1]
    last_state = states[-1]
    last_accum = accum_flags[-1]
    pos_mult = 1.0 if last_accum else STATE_POS.get(last_state, 0.0)
    rv20_last = float(last["rv20"]) if not np.isnan(last["rv20"]) else 1.0
    suggested_size = round(min(1.0, 0.40 / max(rv20_last, 0.001)) * pos_mult, 3)

    iv30 = manual.get("iv30")
    vrp = round(float(iv30) - rv20_last, 4) if iv30 is not None else None

    def _safe(v, digits):
        try:
            f = float(v)
            return round(f, digits) if not np.isnan(f) else None
        except Exception:
            return None

    # Determine "since" date for current state
    state_since = trades[-1]["date"] if trades else df_ytd.index[0].strftime("%Y-%m-%d")

    return {
        "last_session": df_full.index[-1].strftime("%Y-%m-%d"),
        "data_stale": False,
        "state": {
            "machine": last_state,
            "accum_overlay": last_accum,
            "since": state_since,
            "position_multiplier": pos_mult,
            "suggested_size": suggested_size,
            "short_permitted": bool(
                _safe(last["close"], 2) is not None
                and _safe(last["ma50"], 2) is not None
                and _safe(last["close"], 2) < _safe(last["ma50"], 2)
                and _safe(last["id20"], 4) is not None
                and _safe(last["id20"], 4) < 0
                and _safe(last["on20"], 4) is not None
                and _safe(last["on20"], 4) < 0
            ),
        },
        "today": {
            "close":    _safe(last["close"], 2),
            "ret":      _safe(last["ret"], 4),
            "id20":     _safe(last["id20"], 4),
            "on20":     _safe(last["on20"], 4),
            "ret20":    _safe(last["ret20"], 4),
            "ma20":     _safe(last["ma20"], 2),
            "ma50":     _safe(last["ma50"], 2),
            "ma200":    _safe(last["ma200"], 2),
            "rv10":     _safe(last["rv10"], 4),
            "rv20":     _safe(last["rv20"], 4),
            "turb":     _safe(last["turb"], 4),
            "ar1":      _safe(last["ar1"], 4),
            "rsi14":    _safe(last["rsi14"], 4),
            "dist20":   int(last["dist20"]) if not np.isnan(last["dist20"]) else None,
            "vrp":      vrp,
            "iv30_asof": manual.get("iv30_asof"),
        },
        "bands": bands,
        "trades": trades,
        "series": {
            "dates": [d.strftime("%Y-%m-%d") for d in df_ytd.index],
            "close": [_safe(v, 2) for v in df_ytd["close"]],
            "ma20":  [_safe(v, 2) for v in df_ytd["ma20"]],
            "id20":  [_safe(v, 4) for v in df_ytd["id20"]],
            "on20":  [_safe(v, 4) for v in df_ytd["on20"]],
            "rv20":  [_safe(v, 4) for v in df_ytd["rv20"]],
            "equity_strategy": eq_s,
            "equity_bh":       eq_bh,
            "smh_close":       [None] * len(df_ytd),
        },
        "checklist": _build_checklist(last, vrp, manual),
        "events": [],
    }


def _build_checklist(last: pd.Series, vrp: float | None, manual: dict) -> list[dict]:
    def _safe(v, digits):
        try:
            f = float(v)
            return round(f, digits) if not np.isnan(f) else None
        except Exception:
            return None

    id20  = _safe(last["id20"], 4)
    on20  = _safe(last["on20"], 4)
    ma20  = _safe(last["ma20"], 2)
    close = float(last["close"])
    rv20  = _safe(last["rv20"], 4)
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
    ]
