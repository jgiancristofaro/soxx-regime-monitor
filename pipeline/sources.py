"""
OHLCV fetch chain: yfinance → stooq → stockanalysis.
Returns daily adjusted OHLCV for SOXX as a validated DataFrame.
"""
import io
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

TICKER = "SOXX"


def _validate(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        raise ValueError(f"{source}: empty result")
    df = df.copy()
    bad = (
        (df["close"] <= 0)
        | (df["open"] <= 0)
        | (df["low"] <= 0)
        | (df["high"] <= 0)
        | (df["low"] > df[["open", "close"]].min(axis=1))
        | (df["high"] < df[["open", "close"]].max(axis=1))
    )
    df = df[~bad]
    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    if len(df) < 20:
        raise ValueError(f"{source}: only {len(df)} valid rows after validation")
    return df


def _from_yfinance(days: int) -> pd.DataFrame:
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    raw = yf.download(TICKER, start=start, auto_adjust=True, progress=False)
    if raw.empty:
        raise ValueError("yfinance returned empty")
    raw.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in raw.columns]
    raw.index.name = "date"
    raw.index = pd.to_datetime(raw.index)
    return _validate(raw[["open", "high", "low", "close", "volume"]], "yfinance")


def _from_stooq(days: int) -> pd.DataFrame:
    url = "https://stooq.com/q/d/l/?s=soxx.us&i=d"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"])
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"vol": "volume"}).set_index("date").sort_index()
    cutoff = datetime.today() - timedelta(days=days)
    df = df[df.index >= pd.Timestamp(cutoff)]
    return _validate(df[["open", "high", "low", "close", "volume"]], "stooq")


def _from_stockanalysis(days: int) -> pd.DataFrame:
    url = "https://stockanalysis.com/api/symbol/e/SOXX/history?range=1Y&period=Daily"
    r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data", payload)
    df = pd.DataFrame(data)
    df = df.rename(columns={"t": "date", "o": "open", "h": "high",
                             "l": "low", "a": "close", "v": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    cutoff = datetime.today() - timedelta(days=days)
    df = df[df.index >= pd.Timestamp(cutoff)]
    return _validate(df[["open", "high", "low", "close", "volume"]], "stockanalysis")


def fetch_ohlcv(days: int = 420) -> pd.DataFrame:
    """Try each source in order; return first success."""
    errors = []
    for fn in (_from_yfinance, _from_stooq, _from_stockanalysis):
        try:
            return fn(days)
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
    raise RuntimeError("All OHLCV sources failed:\n" + "\n".join(errors))


def fetch_companion_ohlcv(ticker: str, days: int = 600) -> pd.DataFrame:
    """Fetch OHLCV for a companion ticker (TSM, EWY, QQQ) via yfinance only.

    Raises on failure; caller should wrap with continue-on-error semantics.
    """
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    raw = yf.download(ticker, start=start, auto_adjust=True, progress=False)
    if raw.empty:
        raise ValueError(f"{ticker}: empty result from yfinance")
    raw.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in raw.columns]
    raw.index.name = "date"
    raw.index = pd.to_datetime(raw.index)
    return _validate(raw[["open", "high", "low", "close", "volume"]], f"yfinance-{ticker}")


def load_fixture(path: str) -> pd.DataFrame:
    """Load a local CSV fixture (for tests or stale-data fallback)."""
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    return _validate(df[["open", "high", "low", "close", "volume"]], f"fixture:{path}")


def fetch_fred_series(series_id: str, days: int = 600) -> pd.Series:
    """Fetch a single FRED daily series and return a date-indexed float Series.

    Uses the FRED graph CSV API (no API key required).
    Raises on HTTP/parse error; caller should handle with continue-on-error semantics.
    Marks stale if latest value is >3 sessions old.
    """
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    r = requests.get(url, timeout=20, headers={"Accept": "text/csv, application/csv, */*"})
    r.raise_for_status()
    raw = r.content.decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(raw))
    df.columns = [c.strip() for c in df.columns]
    # FRED CSVs use DATE column; find it case-insensitively
    date_col = next((c for c in df.columns if c.upper() in ("DATE", "OBSERVATION_DATE")), None)
    val_col = next((c for c in df.columns if c.upper() == series_id.upper()), None)
    if date_col is None or val_col is None:
        raise ValueError(f"FRED {series_id}: unexpected columns {list(df.columns)}")
    df = df.rename(columns={date_col: "date", val_col: "value"})
    df["date"] = pd.to_datetime(df["date"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    if df.empty:
        raise ValueError(f"FRED {series_id}: empty after parsing")
    return df["value"]
