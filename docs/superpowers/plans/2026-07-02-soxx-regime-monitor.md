# SOXX Regime Monitor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a zero-cost, publicly hosted dashboard that computes and displays the SOXX regime state machine (RISK-ON / ARMED / FIRED / ACCUMULATION) every trading day, with an interactive YTD price chart color-coded by investment window.

**Architecture:** Static site (Vite + TypeScript + Chart.js 4.x) hosted on GitHub Pages. Python 3.11 pipeline (pandas + yfinance) runs nightly via GitHub Actions cron, writes `data/signals.json` back to the repo, which the static site reads at load time. No servers, no paid APIs, no secrets required.

**Tech Stack:** Python 3.11, pandas, numpy, yfinance, pytest; Node.js 24, TypeScript, Vite, Chart.js 4.x; GitHub Actions, GitHub Pages.

**Public URL:** `https://jgiancristofaro.github.io/soxx-regime-monitor/`

## Global Constraints

- Python 3.11; pin all pip packages to exact versions in `pipeline/requirements.txt`
- Node 20+; TypeScript strict mode; Vite base path `/soxx-regime-monitor/`
- Chart.js 4.x only; no React/Vue/Angular; no paid APIs; no external fonts/CDN
- All monetary advice blocked by disclaimer; MIT license only
- State machine constants are IN-SAMPLE, documented as such; unit tests must pass golden record EXACTLY
- Commit and push after every task (user preference)
- Append WORKLOG.MD after every significant decision or step (never edit old entries)

---

## File Map

| File | Responsibility |
|---|---|
| `CLAUDE.md` | Project conventions, build commands, AI agent preferences |
| `WORKLOG.MD` | Append-only decision log indexed for future agents |
| `pipeline/sources.py` | yfinance → stooq → stockanalysis fallback chain; returns validated DataFrame |
| `pipeline/state_machine.py` | Pure function: DataFrame → states/trades/bands; all constants at top |
| `pipeline/compute.py` | Orchestrator: fetch → signals → state machine → write JSON |
| `pipeline/requirements.txt` | Pinned dependencies |
| `pipeline/tests/fixtures/soxx_2026.csv` | Frozen Jan 2 – Jul 1 2026 daily OHLCV fixture |
| `pipeline/tests/test_signals.py` | Formula golden values from §6.4 |
| `pipeline/tests/test_state_machine.py` | Transition + trade golden record from §6.4 |
| `data/manual.json` | Owner-edited IV / P-C (optional) |
| `data/events.json` | Owner-edited chart annotations |
| `data/signals.json` | Committed by CI (generated output) |
| `data/history.csv` | Committed by CI (last-good raw data) |
| `docs/METHODOLOGY.md` | Ruleset doc, shipped verbatim from Starting docs |
| `site/index.html` | Single-page shell |
| `site/src/types.ts` | TypeScript interfaces for signals.json schema |
| `site/src/state.ts` | Load + parse signals.json; exports typed data |
| `site/src/theme.ts` | Dark/light mode toggle; color constants |
| `site/src/bands.ts` | Chart.js plugin: color-coded background band rendering |
| `site/src/charts.ts` | Main chart, decomposition panel, equity chart with crosshair sync |
| `site/src/main.ts` | App bootstrap; renders hero, checklist, tables, methodology |
| `site/src/styles.css` | Layout, typography, dark/light mode variables |
| `site/vite.config.ts` | base: '/soxx-regime-monitor/', copy data/ into dist |
| `site/package.json` | npm deps |
| `.github/workflows/ci.yml` | PR gate: pytest + tsc + vite build |
| `.github/workflows/daily.yml` | Cron: compute.py → commit signals.json → trigger deploy |
| `.github/workflows/deploy.yml` | Build site + deploy Pages on push to main |

---

## Task 1: Project Foundation & Documentation

**Files:**
- Create: `CLAUDE.md`
- Create: `WORKLOG.MD`
- Create: `README.md`
- Create: `LICENSE`
- Create: `docs/METHODOLOGY.md`
- Create: `data/manual.json`
- Create: `data/events.json`
- Create: `.gitignore`

- [ ] Write `CLAUDE.md` with project conventions, build commands, AI agent preferences
- [ ] Write initial `WORKLOG.MD` entry #1 (project initialization)
- [ ] Write `README.md` (what/why, screenshot placeholder, quickstart, disclaimer)
- [ ] Write `LICENSE` (MIT, year 2026, jgiancristofaro)
- [ ] Copy `Starting docs/SOXX_Regime_Analysis_2026-07-02.md` → `docs/METHODOLOGY.md`
- [ ] Write `data/manual.json` with current values: `{ "iv30": 0.46, "iv30_asof": "2026-07-01", "pc_oi": 2.9, "pc_oi_asof": "2026-06-18" }`
- [ ] Write `data/events.json` with seeded annotations from §3.4
- [ ] Write `.gitignore` (Python venv, node_modules, dist, __pycache__, .DS_Store)
- [ ] `git init && git add -A && git commit -m "chore: project foundation and documentation"`

---

## Task 2: Pipeline — Requirements & Fixture

**Files:**
- Create: `pipeline/requirements.txt`
- Create: `pipeline/tests/fixtures/soxx_2026.csv` (from Starting docs fixture)
- Create: `pipeline/__init__.py`
- Create: `pipeline/tests/__init__.py`

- [ ] Write `pipeline/requirements.txt` with pinned versions: pandas==2.2.3, numpy==2.0.2, yfinance==0.2.62, requests==2.32.3, pytest==8.3.4, python-dateutil==2.9.0
- [ ] Copy `Starting docs/data/soxx_2026.csv` → `pipeline/tests/fixtures/soxx_2026.csv`
- [ ] Verify fixture has correct columns: date, open, high, low, close, volume
- [ ] Run `pip install -r pipeline/requirements.txt` to verify pins resolve
- [ ] Write empty `__init__.py` files
- [ ] `git add -A && git commit -m "chore: pipeline requirements and test fixture"`

---

## Task 3: Pipeline — Sources (Data Fetch Chain)

**Files:**
- Create: `pipeline/sources.py`

**Produces:** `fetch_ohlcv(days: int = 420) -> pd.DataFrame` with columns: date (datetime index), open, high, low, close, volume. Returns validated DataFrame or raises on total failure.

- [ ] Write `pipeline/sources.py`:

```python
"""
OHLCV fetch chain: yfinance → stooq → stockanalysis.
Returns daily adjusted OHLCV for SOXX as a validated DataFrame.
"""
import io
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

TICKER = "SOXX"


def _validate(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        raise ValueError(f"{source}: empty result")
    df = df.copy()
    bad = (df["close"] <= 0) | (df["open"] <= 0) | (df["low"] <= 0) | (df["high"] <= 0)
    bad |= df["low"] > df[["open", "close"]].min(axis=1)
    bad |= df["high"] < df[["open", "close"]].max(axis=1)
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
    raw.columns = [c.lower() for c in raw.columns]
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
    df = df[df.index >= cutoff]
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
    df = df[df.index >= cutoff]
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


def load_fixture(path: str) -> pd.DataFrame:
    """Load a local CSV fixture (for tests / stale-data fallback)."""
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    return _validate(df[["open", "high", "low", "close", "volume"]], f"fixture:{path}")
```

- [ ] `git add -A && git commit -m "feat(pipeline): OHLCV fetch chain with yfinance/stooq/stockanalysis fallbacks"`

---

## Task 4: Pipeline — State Machine (Pure Function)

**Files:**
- Create: `pipeline/state_machine.py`

**Produces:** `compute_signals(df: pd.DataFrame, manual: dict) -> dict` returning the full signals structure matching `signals.json` schema.

- [ ] Write `pipeline/state_machine.py`:

```python
"""
State machine: pure function, DataFrame → {state, bands, trades, series}.
Constants at top are in-sample choices — documented as such.
"""
import numpy as np
import pandas as pd

# In-sample thresholds (Jan 20 – Jul 1 2026, n=3 episodes)
ARM_DIV_ID       = 0.00   # armed if id20 < 0 and ret20 > +2%
ARM_DIV_RET      = 0.02
ARM_ABS_ID       = -0.03  # or id20 < -3%
ACC_ID           = 0.02   # accumulation: id20 > +2% and ret20 < -2%
ACC_RET          = -0.02
EXIT_ID          = 0.01   # exec-into-strength: id_t > +1%
EXIT_DAY         = 0.015  # or ret_t > +1.5%
ESCAPE_SESSIONS  = 3
DISARM_SESSIONS  = 2
REENTER_MA_DAYS  = 2
WARMUP_SESSIONS  = 20


def _wilder_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_signals(df: pd.DataFrame, manual: dict | None = None) -> dict:
    if manual is None:
        manual = {}
    df = df.copy()

    # Derived series
    df["ret"]    = df["close"] / df["close"].shift(1) - 1
    df["on"]     = df["open"] / df["close"].shift(1) - 1
    df["id"]     = df["close"] / df["open"] - 1
    df["on20"]   = df["on"].rolling(20).sum()
    df["id20"]   = df["id"].rolling(20).sum()
    df["ret20"]  = df["ret"].rolling(20).sum()
    df["ma20"]   = df["close"].rolling(20).mean()
    df["ma50"]   = df["close"].rolling(50).mean()
    df["ma200"]  = df["close"].rolling(200).mean()
    df["rv10"]   = df["ret"].rolling(10).std() * np.sqrt(252)
    df["rv20"]   = df["ret"].rolling(20).std() * np.sqrt(252)
    df["turb"]   = (df["ret"] - df["ret"].rolling(60).mean()).abs() / df["ret"].rolling(60).std()
    df["ar1"]    = df["ret"].rolling(20).apply(
        lambda x: pd.Series(x).autocorr() if pd.Series(x).std() > 0 else np.nan, raw=False
    )
    df["rsi14"]  = _wilder_rsi(df["close"], 14)
    df["vol30"]  = df["volume"].rolling(30).mean()
    df["dist_day"] = (df["ret"] <= -0.01) & (df["volume"] > 1.3 * df["vol30"])
    df["dist20"]   = df["dist_day"].rolling(20).sum()

    iv30 = manual.get("iv30")
    vrp = (iv30 - df["rv20"].iloc[-1]) if iv30 is not None else None

    # State machine
    RISK_ON, ARMED, FIRED = "RISK_ON", "ARMED", "FIRED"
    states = []
    accum_flags = []
    trades = []
    state = RISK_ON
    arm_date = None
    sessions_since_arm = 0
    disarm_streak = 0
    reenter_above_ma20 = 0

    for i, (idx, row) in enumerate(df.iterrows()):
        if i < WARMUP_SESSIONS - 1:
            states.append("WARMUP")
            accum_flags.append(False)
            continue

        id20   = row["id20"]  if not np.isnan(row["id20"])  else 0
        ret20  = row["ret20"] if not np.isnan(row["ret20"]) else 0
        id_t   = row["id"]    if not np.isnan(row["id"])    else 0
        ret_t  = row["ret"]   if not np.isnan(row["ret"])   else 0
        ma20   = row["ma20"]  if not np.isnan(row["ma20"])  else row["close"]
        close  = row["close"]

        armed_cond = (id20 < ARM_DIV_ID and ret20 > ARM_DIV_RET) or (id20 < ARM_ABS_ID)
        acc_cond   = (id20 > ACC_ID and ret20 < ACC_RET)

        if state == RISK_ON:
            if armed_cond and not acc_cond:
                state = ARMED
                arm_date = idx
                sessions_since_arm = 0
                disarm_streak = 0
        elif state == ARMED:
            sessions_since_arm += 1
            if acc_cond:
                state = RISK_ON
                arm_date = None
                sessions_since_arm = 0
                disarm_streak = 0
            elif id_t > EXIT_ID or ret_t > EXIT_DAY:
                state = FIRED
                trades.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "price": round(close, 2),
                    "action": "EXIT",
                    "reason": "exec-into-strength",
                })
                arm_date = None
                sessions_since_arm = 0
            elif sessions_since_arm >= ESCAPE_SESSIONS and close < ma20:
                state = FIRED
                trades.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "price": round(close, 2),
                    "action": "EXIT",
                    "reason": "escape-valve",
                })
                arm_date = None
            elif not armed_cond:
                disarm_streak += 1
                if disarm_streak >= DISARM_SESSIONS:
                    state = RISK_ON
                    arm_date = None
                    sessions_since_arm = 0
                    disarm_streak = 0
                    trades.append({
                        "date": idx.strftime("%Y-%m-%d"),
                        "price": round(close, 2),
                        "action": "REENTER",
                        "reason": "disarm",
                    })
            else:
                disarm_streak = 0
        elif state == FIRED:
            if acc_cond:
                state = RISK_ON
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
                    state = RISK_ON
                    reenter_above_ma20 = 0
                    trades.append({
                        "date": idx.strftime("%Y-%m-%d"),
                        "price": round(close, 2),
                        "action": "REENTER",
                        "reason": "trend reclaim",
                    })
            else:
                reenter_above_ma20 = 0

        states.append(state)
        accum_flags.append(acc_cond and state == RISK_ON)

    df = df.iloc[len(df) - len(states):]  # trim warmup rows not in states list
    # Re-attach warmup rows for band building
    full_states = ["WARMUP"] * (len(df.index) - len(states)) + states

    # Actually rebuild with all rows
    df_all = df.copy()
    all_states = []
    all_accum = []
    state = RISK_ON
    arm_date = None
    sessions_since_arm = 0
    disarm_streak = 0
    reenter_above_ma20 = 0
    trades = []

    for i, (idx, row) in enumerate(df_all.iterrows()):
        if i < WARMUP_SESSIONS - 1:
            all_states.append("WARMUP")
            all_accum.append(False)
            continue

        id20   = row["id20"]  if not np.isnan(row.get("id20", np.nan))  else 0
        ret20  = row["ret20"] if not np.isnan(row.get("ret20", np.nan)) else 0
        id_t   = row["id"]    if not np.isnan(row.get("id", np.nan))    else 0
        ret_t  = row["ret"]   if not np.isnan(row.get("ret", np.nan))   else 0
        ma20   = row["ma20"]  if not np.isnan(row.get("ma20", np.nan))  else row["close"]
        close  = row["close"]

        armed_cond = (id20 < ARM_DIV_ID and ret20 > ARM_DIV_RET) or (id20 < ARM_ABS_ID)
        acc_cond   = (id20 > ACC_ID and ret20 < ACC_RET)

        if state == RISK_ON:
            if armed_cond and not acc_cond:
                state = ARMED
                arm_date = idx
                sessions_since_arm = 0
                disarm_streak = 0
        elif state == ARMED:
            sessions_since_arm += 1
            if acc_cond:
                state = RISK_ON
                arm_date = None
                sessions_since_arm = 0
                disarm_streak = 0
            elif id_t > EXIT_ID or ret_t > EXIT_DAY:
                state = FIRED
                trades.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "price": round(close, 2),
                    "action": "EXIT",
                    "reason": "exec-into-strength",
                })
                arm_date = None
                sessions_since_arm = 0
            elif sessions_since_arm >= ESCAPE_SESSIONS and close < ma20:
                state = FIRED
                trades.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "price": round(close, 2),
                    "action": "EXIT",
                    "reason": "escape-valve",
                })
                arm_date = None
            elif not armed_cond:
                disarm_streak += 1
                if disarm_streak >= DISARM_SESSIONS:
                    state = RISK_ON
                    arm_date = None
                    sessions_since_arm = 0
                    disarm_streak = 0
                    trades.append({
                        "date": idx.strftime("%Y-%m-%d"),
                        "price": round(close, 2),
                        "action": "REENTER",
                        "reason": "disarm",
                    })
            else:
                disarm_streak = 0
        elif state == FIRED:
            if acc_cond:
                state = RISK_ON
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
                    state = RISK_ON
                    reenter_above_ma20 = 0
                    trades.append({
                        "date": idx.strftime("%Y-%m-%d"),
                        "price": round(close, 2),
                        "action": "REENTER",
                        "reason": "trend reclaim",
                    })
            else:
                reenter_above_ma20 = 0

        all_states.append(state)
        all_accum.append(acc_cond)

    df_all["state"] = all_states
    df_all["accum"] = all_accum

    # Build bands (contiguous date ranges by state)
    bands = []
    prev_state = None
    band_start = None
    for idx, row in df_all.iterrows():
        s = row["state"]
        effective = "ACCUM" if (s == "RISK_ON" and row["accum"]) else s
        if effective != prev_state:
            if prev_state is not None:
                bands.append({
                    "start": band_start.strftime("%Y-%m-%d"),
                    "end": idx.strftime("%Y-%m-%d"),
                    "state": prev_state,
                })
            band_start = idx
            prev_state = effective
    if prev_state is not None:
        bands.append({
            "start": band_start.strftime("%Y-%m-%d"),
            "end": None,
            "state": prev_state,
        })

    # Strategy backtest from 2026-01-20
    backtest_start = pd.Timestamp("2026-01-20")
    pos = 0.0
    equity_strategy = []
    equity_bh = []
    cum_s = 1.0
    cum_bh = 1.0
    for idx, row in df_all.iterrows():
        if idx < backtest_start:
            equity_strategy.append(None)
            equity_bh.append(None)
            continue
        ret = row["ret"] if not np.isnan(row.get("ret", np.nan)) else 0
        s = row["state"]
        if s == "RISK_ON":
            pos = 1.0
        elif s == "ARMED":
            pos = 0.6
        elif s == "FIRED":
            pos = 0.0
        else:
            pos = 1.0  # WARMUP or ACCUM
        cum_s  *= (1 + ret * pos)
        cum_bh *= (1 + ret)
        equity_strategy.append(round(cum_s, 6))
        equity_bh.append(round(cum_bh, 6))

    last = df_all.iloc[-1]
    last_state = last["state"]
    accum_overlay = bool(last["accum"])
    pos_mult = {"RISK_ON": 1.0, "ARMED": 0.6, "FIRED": 0.0, "WARMUP": 0.0}.get(last_state, 1.0)
    if accum_overlay:
        pos_mult = 1.0
    rv20_last = last["rv20"] if not np.isnan(last["rv20"]) else 1.0
    suggested_size = round(min(1.0, 0.40 / rv20_last) * pos_mult, 3)

    def pct4(v):
        return round(float(v), 4) if not np.isnan(v) else None

    def p2(v):
        return round(float(v), 2) if not np.isnan(v) else None

    # Build series (YTD + prior 60 sessions)
    ytd_start = pd.Timestamp(f"{df_all.index[-1].year}-01-01")
    series_start = max(df_all.index[0], ytd_start - pd.Timedelta(days=90))
    ser = df_all[df_all.index >= series_start]

    return {
        "last_session": df_all.index[-1].strftime("%Y-%m-%d"),
        "state": {
            "machine": last_state,
            "accum_overlay": accum_overlay,
            "since": next(
                (t["date"] for t in reversed(trades) if t["action"] in ("EXIT", "REENTER")),
                df_all.index[0].strftime("%Y-%m-%d"),
            ),
            "position_multiplier": pos_mult,
            "suggested_size": suggested_size,
            "short_permitted": bool(
                last["close"] < last["ma50"]
                and last["id20"] < 0
                and last["on20"] < 0
            ),
        },
        "today": {
            "close":   p2(last["close"]),
            "ret":     pct4(last["ret"]),
            "id20":    pct4(last["id20"]),
            "on20":    pct4(last["on20"]),
            "ret20":   pct4(last["ret20"]),
            "ma20":    p2(last["ma20"]),
            "ma50":    p2(last["ma50"]),
            "ma200":   p2(last["ma200"]),
            "rv10":    pct4(last["rv10"]),
            "rv20":    pct4(last["rv20"]),
            "turb":    pct4(last["turb"]),
            "ar1":     pct4(last["ar1"]),
            "rsi14":   pct4(last["rsi14"]),
            "dist20":  int(last["dist20"]) if not np.isnan(last["dist20"]) else None,
            "vrp":     round(vrp, 4) if vrp is not None else None,
            "iv30_asof": manual.get("iv30_asof"),
        },
        "bands": bands,
        "trades": trades,
        "series": {
            "dates":            [d.strftime("%Y-%m-%d") for d in ser.index],
            "close":            [p2(v) for v in ser["close"]],
            "ma20":             [p2(v) for v in ser["ma20"]],
            "id20":             [pct4(v) for v in ser["id20"]],
            "on20":             [pct4(v) for v in ser["on20"]],
            "rv20":             [pct4(v) for v in ser["rv20"]],
            "equity_strategy":  equity_strategy[-len(ser):],
            "equity_bh":        equity_bh[-len(ser):],
        },
    }
```

- [ ] `git add -A && git commit -m "feat(pipeline): state machine — pure function with signals, bands, trades, backtest"`

---

## Task 5: Pipeline — Tests (Golden Record)

**Files:**
- Create: `pipeline/tests/test_signals.py`
- Create: `pipeline/tests/test_state_machine.py`

- [ ] Write `pipeline/tests/test_signals.py` verifying §6.4 signal values
- [ ] Write `pipeline/tests/test_state_machine.py` verifying §6.4 trade record
- [ ] Run `pytest pipeline/tests/ -v` — all must pass
- [ ] `git add -A && git commit -m "test(pipeline): golden record tests for signals and state machine"`

---

## Task 6: Pipeline — Compute Orchestrator

**Files:**
- Create: `pipeline/compute.py`

- [ ] Write `pipeline/compute.py` that: loads manual.json, fetches OHLCV (or uses history.csv on failure), computes signals, writes `data/signals.json` and `data/history.csv`
- [ ] Run locally: `python pipeline/compute.py` → verify `data/signals.json` created
- [ ] `git add -A && git commit -m "feat(pipeline): compute orchestrator — fetch, compute, write JSON"`

---

## Task 7: Frontend — Build Setup

**Files:**
- Create: `site/package.json`
- Create: `site/vite.config.ts`
- Create: `site/tsconfig.json`
- Create: `site/index.html`

- [ ] Write `site/package.json` with chart.js 4.x, vite, typescript
- [ ] Write `site/vite.config.ts` with base `/soxx-regime-monitor/` and data copy
- [ ] Write `site/tsconfig.json` strict mode
- [ ] Write `site/index.html` shell
- [ ] Run `npm install` in `site/`
- [ ] `git add -A && git commit -m "chore(site): vite+ts frontend scaffold"`

---

## Task 8: Frontend — Types, State, Theme

**Files:**
- Create: `site/src/types.ts`
- Create: `site/src/state.ts`
- Create: `site/src/theme.ts`

- [ ] Write TypeScript interfaces matching signals.json schema
- [ ] Write state.ts to fetch + parse signals.json
- [ ] Write theme.ts with color constants from §5.4 and dark mode toggle
- [ ] `git add -A && git commit -m "feat(site): types, state loader, theme system"`

---

## Task 9: Frontend — Styles & Layout

**Files:**
- Create: `site/src/styles.css`

- [ ] Write responsive CSS with CSS custom properties for theming
- [ ] Layout: 680-1100px column, responsive to 360px, dark/light mode
- [ ] `git add -A && git commit -m "feat(site): responsive layout and theme styles"`

---

## Task 10: Frontend — Status Hero + Checklist

**Files:**
- Modify: `site/src/main.ts`

- [ ] Render state chip (color per §5.4), one-sentence explanation, since date, position multiplier
- [ ] FIRED state shows re-entry conditions with live values
- [ ] Five-number checklist cards from checklist[] with status dots
- [ ] `git add -A && git commit -m "feat(site): status hero and five-number checklist"`

---

## Task 11: Frontend — Band Plugin + Main Chart

**Files:**
- Create: `site/src/bands.ts`
- Create: `site/src/charts.ts`

- [ ] Write Chart.js band plugin (beforeDatasetsDraw, fillRect per state color)
- [ ] Main chart: YTD SOXX close + 20-DMA dashed + bands + trade markers + event flags
- [ ] Range buttons [YTD | 3M | 1M | All], series toggles
- [ ] Crosshair tooltip: date, close, id20, on20, state
- [ ] `git add -A && git commit -m "feat(site): main price chart with color-coded bands and trade markers"`

---

## Task 12: Frontend — Decomposition Panel + Equity + Tables

**Files:**
- Modify: `site/src/charts.ts`
- Modify: `site/src/main.ts`

- [ ] Decomposition panel: id20 vs on20 with synced crosshair, zero baseline, bands
- [ ] Equity panel: strategy vs buy-and-hold with stat tiles
- [ ] Trade log table: date, action, price, reason
- [ ] Context strip: RSI, dist20, turbulence, AR(1) with playbook note, P/C OI + IV
- [ ] Methodology collapse section
- [ ] Footer with data sources, timestamps, GitHub link, license
- [ ] `git add -A && git commit -m "feat(site): decomp panel, equity chart, tables, context strip, methodology"`

---

## Task 13: GitHub Actions Workflows

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/daily.yml`
- Create: `.github/workflows/deploy.yml`

- [ ] Write `ci.yml`: pytest + tsc + vite build on PR
- [ ] Write `daily.yml`: cron 22:15 UTC weekdays + workflow_dispatch, compute, commit, issue
- [ ] Write `deploy.yml`: build site + deploy Pages on push to main affecting site/ or data/
- [ ] `git add -A && git commit -m "feat(ci): GitHub Actions — daily pipeline, Pages deploy, CI gate"`

---

## Task 14: Push to GitHub + Enable Pages

- [ ] `gh repo create soxx-regime-monitor --public --source=. --remote=origin --push`
- [ ] Enable GitHub Pages via API: source = GitHub Actions
- [ ] Trigger manual workflow_dispatch on daily.yml to generate initial signals.json
- [ ] Verify site loads at `https://jgiancristofaro.github.io/soxx-regime-monitor/`
- [ ] `git tag v1.0.0 && git push --tags`
