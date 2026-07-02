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


def main():
    sys.path.insert(0, str(ROOT))
    from pipeline.sources import fetch_ohlcv, load_fixture
    from pipeline.state_machine import compute_signals

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

    # FRED enrichment (continue-on-error — never blocks the core pipeline)
    print("Fetching FRED data...")
    df = _fetch_fred_columns(df)

    # Hourly bar capture (optional, continue-on-error)
    print("Fetching hourly bars...")
    _fetch_hourly_bars(df)

    manual = load_manual()
    events = load_events()

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _append_options_history(manual, today_str)

    print("Computing signals...")
    result = compute_signals(df, manual)
    result["generated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result["data_stale"] = stale
    result["events"] = events

    signals_path.parent.mkdir(parents=True, exist_ok=True)
    with open(signals_path, "w") as f:
        json.dump(result, f, indent=2)

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
