"""
Main pipeline orchestrator: fetch OHLCV → compute signals → write JSON artifacts.
Run: python pipeline/compute.py
"""
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"


def load_manual() -> dict:
    path = DATA_DIR / "manual.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def load_events() -> list:
    path = DATA_DIR / "events.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def _append_options_history(manual: dict, today_str: str) -> None:
    """Append today's manual.json snapshot to data/options_history.csv (deduped by date)."""
    path = DATA_DIR / "options_history.csv"
    row = {
        "date": today_str,
        "iv30": manual.get("iv30"),
        "iv30_asof": manual.get("iv30_asof"),
        "pc_oi": manual.get("pc_oi"),
        "pc_oi_asof": manual.get("pc_oi_asof"),
        "pcvol": manual.get("pcvol"),
        "iv90": manual.get("iv90"),
        "skew25d": manual.get("skew25d"),
    }
    fieldnames = list(row.keys())
    existing_dates: set = set()
    if path.exists():
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                existing_dates.add(r.get("date", ""))
    if row["date"] in existing_dates:
        return
    write_header = not path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _drop_live_candle(
    df: pd.DataFrame,
    now_utc: "datetime | None" = None,
) -> "tuple[pd.DataFrame, bool]":
    """Drop the last row if its date equals today and current UTC time is before 21:30.

    Returns (df, was_dropped). Rationale: reject in-progress candles before the 16:30 ET
    close + 5-hour buffer (v3.6 Change 3 — prevents live-session prints entering signal math).
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    CUTOFF_HOUR, CUTOFF_MINUTE = 21, 30
    past_cutoff = now_utc.hour > CUTOFF_HOUR or (
        now_utc.hour == CUTOFF_HOUR and now_utc.minute >= CUTOFF_MINUTE
    )

    if df.empty or past_cutoff:
        return df, False

    if df.index[-1].strftime("%Y-%m-%d") == now_utc.strftime("%Y-%m-%d"):
        return df.iloc[:-1], True

    return df, False


def _fetch_fred_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add vix_slope and real_chg20 columns from FRED data (continue-on-error)."""
    sys.path.insert(0, str(ROOT))
    from pipeline.sources import fetch_fred_series

    days = 700  # extra buffer for 20-day change computation
    series = {}
    for sid in ["VIXCLS", "VXVCLS", "DGS10", "T10YIE"]:
        try:
            series[sid] = fetch_fred_series(sid, days=days)
        except Exception as e:
            print(f"  FRED {sid} skipped: {e}")

    if "VIXCLS" not in series or "VXVCLS" not in series:
        if "DGS10" not in series or "T10YIE" not in series:
            return df  # no FRED data at all

    df = df.copy()
    idx = df.index

    if "VIXCLS" in series and "VXVCLS" in series:
        vix = series["VIXCLS"].reindex(idx).ffill()
        vix3m = series["VXVCLS"].reindex(idx).ffill()
        df["vix_slope"] = vix3m - vix
        # Mark stale if FRED value is > 3 trading sessions old
        last_vix_date = series["VIXCLS"].index[-1]
        sessions_since = len(df[df.index > last_vix_date])
        if sessions_since > 3:
            print(f"  WARN: FRED VIX data is {sessions_since} sessions stale")
            df["vix_slope"] = np.nan

    if "DGS10" in series and "T10YIE" in series:
        dgs10 = series["DGS10"].reindex(idx).ffill()
        t10yie = series["T10YIE"].reindex(idx).ffill()
        real_yield = dgs10 - t10yie
        df["real_chg20"] = real_yield - real_yield.shift(20)
        last_dgs_date = series["DGS10"].index[-1]
        sessions_since = len(df[df.index > last_dgs_date])
        if sessions_since > 3:
            print(f"  WARN: FRED DGS10 data is {sessions_since} sessions stale")
            df["real_chg20"] = np.nan

    return df


def _fetch_asia_ohlcv(df: pd.DataFrame, days: int = 600) -> pd.DataFrame:
    """Inject TSM and EWY overnight returns as tsm_on/ewy_on columns (continue-on-error).

    Aligned to SOXX trading dates via reindex; NaN for unmatched sessions.
    Absent columns → state_machine.py skips gap_quality computation gracefully.
    """
    sys.path.insert(0, str(ROOT))
    from pipeline.sources import fetch_companion_ohlcv

    df = df.copy()
    for ticker, col_name in [("TSM", "tsm_on"), ("EWY", "ewy_on")]:
        try:
            comp = fetch_companion_ohlcv(ticker, days=days)
            on = comp["open"] / comp["close"].shift(1) - 1
            df[col_name] = on.reindex(df.index)
            print(f"  {ticker}: {len(comp)} rows, last {comp.index[-1].date()}")
        except Exception as e:
            print(f"  {ticker} skipped (non-blocking): {e}")

    return df


def _grade_earnings_reactions(reactions_path: "Path") -> list:
    """Load earnings_reactions.json, grade any ungraded events, write back, return list.

    Grade formula: pop = close(T+1)/close(T) - 1
                   retrace = first session in T+2..T+5 where close < close(T)
                   vol_flag = volume(retrace_day) >= volume(pop_day)
                   grade = DISTRIBUTION-CONFIRM if pop > 5% and retrace and vol_flag else normal
    """
    if not reactions_path.exists():
        return []

    with open(reactions_path) as f:
        records = json.load(f)

    sys.path.insert(0, str(ROOT))
    from pipeline.sources import fetch_companion_ohlcv

    modified = False
    for rec in records:
        if "grade" in rec:
            continue
        ticker = rec.get("ticker")
        report_date = rec.get("report_date")
        if not ticker or not report_date:
            continue
        try:
            comp = fetch_companion_ohlcv(ticker, days=120)
            t0 = pd.Timestamp(report_date)
            dates = sorted(comp.index)
            t_idx = next((i for i, d in enumerate(dates) if d >= t0), None)
            if t_idx is None or t_idx + 1 >= len(dates):
                continue
            pre_close = float(comp.loc[dates[t_idx], "close"])
            t1 = dates[t_idx + 1]
            pop = float(comp.loc[t1, "close"] / pre_close - 1)
            pop_vol = float(comp.loc[t1, "volume"])
            rec["pop"] = round(pop, 4)
            retrace_date = None
            retrace_vol = None
            for j in range(t_idx + 2, min(t_idx + 6, len(dates))):
                d = dates[j]
                if float(comp.loc[d, "close"]) < pre_close:
                    retrace_date = d.strftime("%Y-%m-%d")
                    retrace_vol = float(comp.loc[d, "volume"])
                    break
            rec["retrace_date"] = retrace_date
            vol_flag = bool(retrace_vol is not None and retrace_vol >= pop_vol)
            rec["vol_flag"] = vol_flag
            rec["grade"] = (
                "DISTRIBUTION-CONFIRM"
                if pop > 0.05 and retrace_date is not None and vol_flag
                else "normal"
            )
            modified = True
            print(f"  Graded {ticker} {report_date}: {rec['grade']} (pop={pop:.2%})")
        except Exception as e:
            print(f"  Earnings grading skipped {ticker} {report_date}: {e}")

    if modified:
        with open(reactions_path, "w") as f:
            json.dump(records, f, indent=2)

    return records


def _fetch_hourly_bars(df_daily: pd.DataFrame) -> None:
    """Best-effort: fetch last-5-session 60-minute bars and append to data/hourly.csv."""
    try:
        import yfinance as yf
        hourly_path = DATA_DIR / "hourly.csv"
        raw = yf.download("SOXX", period="5d", interval="60m", auto_adjust=True, progress=False)
        if raw.empty:
            return
        raw.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in raw.columns]
        raw.index.name = "timestamp"
        raw = raw.reset_index()
        raw["timestamp"] = pd.to_datetime(raw["timestamp"]).dt.strftime("%Y-%m-%dT%H:%M")

        existing_ts: set = set()
        if hourly_path.exists():
            with open(hourly_path, newline="") as f:
                for r in csv.DictReader(f):
                    existing_ts.add(r.get("timestamp", ""))

        write_header = not hourly_path.exists()
        with open(hourly_path, "a", newline="") as f:
            fieldnames = ["timestamp", "open", "high", "low", "close", "volume"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            for _, row in raw.iterrows():
                ts = row["timestamp"]
                if ts not in existing_ts:
                    writer.writerow({
                        "timestamp": ts,
                        "open": round(float(row.get("open", 0)), 4),
                        "high": round(float(row.get("high", 0)), 4),
                        "low": round(float(row.get("low", 0)), 4),
                        "close": round(float(row.get("close", 0)), 4),
                        "volume": int(row.get("volume", 0)),
                    })
        print("  Hourly bars updated")
    except Exception as e:
        print(f"  Hourly bars skipped (non-blocking): {e}")


def _settle_supersedes_preview(result: dict, last_session: str) -> None:
    """
    Called by the settle run after signals.json is written.
    If data/preview.json exists for today's settled session, record the
    settled outcome, append a row to data/preview_log.csv, and mark the
    preview as settled so the frontend can compare projected vs actual.
    """
    preview_path = DATA_DIR / "preview.json"
    if not preview_path.exists():
        return

    with open(preview_path) as f:
        preview = json.load(f)

    # Only process if the preview covers today's settled session
    if preview.get("date") != last_session:
        return

    projected_action = preview.get("projected_action", "NONE")
    last_trade = next(
        (t for t in reversed(result.get("trades", [])) if t.get("date") == last_session),
        None,
    )
    settled_action = last_trade["action"] if last_trade else "NONE"
    agreed = settled_action == projected_action

    # Append to preview_log.csv (deduped by date)
    log_path = DATA_DIR / "preview_log.csv"
    fieldnames = [
        "date", "snapshot_et", "projected_action", "projected_class",
        "settled_action", "agreed", "spot_at_preview", "close",
    ]
    existing_dates: set = set()
    if log_path.exists():
        with open(log_path, newline="") as f:
            for r in csv.DictReader(f):
                existing_dates.add(r.get("date", ""))
    if last_session not in existing_dates:
        write_header = not log_path.exists()
        close_val = float(result.get("today", {}).get("close", 0))
        with open(log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerow({
                "date": last_session,
                "snapshot_et": preview.get("snapshot_et", ""),
                "projected_action": projected_action,
                "projected_class": preview.get("action_class", "NONE"),
                "settled_action": settled_action,
                "agreed": agreed,
                "spot_at_preview": preview.get("spot", ""),
                "close": round(close_val, 2),
            })
        print(f"  Preview log: projected={projected_action} settled={settled_action} agreed={agreed}")

    # Mark preview.json as settled (frontend reads this to switch banner state)
    preview["settled"] = True
    preview["settled_action"] = settled_action
    preview["moc_eligible"] = False
    preview_path.write_text(json.dumps(preview, indent=2))
    print(f"  Preview superseded: settled_action={settled_action}")


def main():
    sys.path.insert(0, str(ROOT))
    from pipeline.sources import fetch_ohlcv, load_fixture
    from pipeline.state_machine import compute_signals, _validate_consistency

    history_path = DATA_DIR / "history.csv"
    signals_path = DATA_DIR / "signals.json"
    stale = False

    print("Fetching OHLCV data...")
    try:
        df = fetch_ohlcv(days=600)
        df.to_csv(history_path)
        print(f"  Fetched {len(df)} rows, last session: {df.index[-1].date()}")
    except RuntimeError as e:
        print(f"  All live sources failed: {e}")
        if history_path.exists():
            print("  Falling back to committed history.csv")
            df = load_fixture(str(history_path))
            stale = True
        else:
            print("  No fallback available — aborting")
            sys.exit(1)

    # Live-candle guard (v3.6 Change 3): drop today's row if before 21:30 UTC
    df, is_live_candle = _drop_live_candle(df)
    if is_live_candle:
        print(f"  WARN: Dropped live intraday candle (before 21:30 UTC); last settled: {df.index[-1].date()}")

    # FRED enrichment (continue-on-error — never blocks the core pipeline)
    print("Fetching FRED data...")
    df = _fetch_fred_columns(df)

    # Asia overnight companion data (continue-on-error — injects tsm_on/ewy_on)
    print("Fetching Asia companion data (TSM, EWY)...")
    df = _fetch_asia_ohlcv(df)

    # Hourly bar capture (optional, continue-on-error)
    print("Fetching hourly bars...")
    _fetch_hourly_bars(df)

    manual = load_manual()
    events = load_events()

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print("Fetching options metrics (IV30, P/C OI)...")
    try:
        from pipeline.options import fetch_options_metrics
        spot = float(df["close"].iloc[-1])
        auto = fetch_options_metrics("SOXX", spot=spot)
        manual = {
            **manual,
            "iv30": auto["iv30"],
            "iv30_asof": today_str,
            "pc_oi": auto["pc_oi"],
            "pc_oi_asof": today_str,
        }
        print(f"  iv30={auto['iv30']:.4f} pc_oi={auto['pc_oi']:.4f} (auto, expiries={auto['iv30_expiries']})")
    except Exception as e:
        print(f"  Options fetch failed, falling back to data/manual.json: {e}")

    _append_options_history(manual, today_str)

    # Grade earnings reactions (continue-on-error — writes back to earnings_reactions.json)
    print("Grading earnings reactions...")
    reactions = _grade_earnings_reactions(DATA_DIR / "earnings_reactions.json")

    print("Computing signals...")
    result = compute_signals(df, manual)
    result["generated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result["data_stale"] = stale
    result["is_live_candle"] = is_live_candle

    # Merge chart events with graded earnings reactions
    reaction_events = [
        {
            "date": r["report_date"],
            "label": r.get("label", f"{r['ticker']} earnings"),
            "type": "earnings_reaction",
            "ticker": r["ticker"],
            "grade": r.get("grade"),
            "pop": r.get("pop"),
        }
        for r in reactions
        if r.get("grade")
    ]
    result["events"] = events + reaction_events

    _validate_consistency(result)

    signals_path.parent.mkdir(parents=True, exist_ok=True)
    with open(signals_path, "w") as f:
        json.dump(result, f, indent=2)

    _settle_supersedes_preview(result, result.get("last_session", today_str))

    print(f"\nSignals written to {signals_path}")
    print(f"  State: {result['state']['machine']}")
    print(f"  Since: {result['state']['since']}")
    print(f"  Last session: {result['last_session']}")
    print(f"  id20: {result['today']['id20']:.4f}")
    print(f"  on20: {result['today']['on20']:.4f}")
    if result['today'].get('on20_mom') is not None:
        print(f"  on20_mom: {result['today']['on20_mom']:.4f}")
    if result['today'].get('vix_slope') is not None:
        print(f"  vix_slope: {result['today']['vix_slope']:.2f}")
    print(f"  Trades: {len(result['trades'])}")


if __name__ == "__main__":
    main()
