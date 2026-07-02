# PRD + Technical Specification: SOXX Regime Monitor
**A public, daily-updating web dashboard for the overnight/intraday distribution signal on SOXX**
Version 1.0 · July 2, 2026 · Companion document: `SOXX_Regime_Analysis_2026-07-02.md` (the ruleset — ship it in the repo as `docs/METHODOLOGY.md`)

---

## 1. Product overview

### 1.1 Goal
A zero-cost, publicly hosted dashboard that computes and displays the SOXX regime state machine (RISK-ON / ARMED / FIRED / ACCUMULATION) every trading day, with an interactive YTD price chart whose background is color-coded by investment window, plus the five daily monitoring numbers. Data refreshes automatically via scheduled CI; no servers, no paid APIs, no secrets required for the MVP.

### 1.2 Users
Primary: the repo owner checking state after each close (30-second read: "what state am I in, did anything change today"). Secondary: public visitors reading methodology and history.

### 1.3 Deliverables
1. Public GitHub repository (suggested name: `soxx-regime-monitor`), MIT license.
2. Public URL via GitHub Pages: `https://<username>.github.io/soxx-regime-monitor/`.
3. Automated daily data pipeline via GitHub Actions (cron), committing computed JSON back to the repo.
4. Signal-change notifications via auto-created GitHub Issues (free, no email infra).

### 1.4 Success criteria (acceptance)
- Dashboard loads in < 2s on desktop and mobile, works with JS chart interactions, supports dark mode.
- After each US trading day, by 22:30 UTC, the site shows that day's bar, updated signals, and state.
- The state machine reproduces the 2026 golden record exactly (see §6.4 test vectors) — including EXIT on 2026-02-05, RE-ENTER on 2026-02-06, EXIT on 2026-06-18.
- Pipeline is idempotent (safe to re-run), survives one failed day gracefully (site shows stale-data banner, not a crash).
- Zero recurring cost.

### 1.5 Non-goals (MVP)
No user accounts, no brokerage integration, no order execution, no email/SMS, no intraday updates (EOD only), no backend server, no database. IV and put/call data are manual-entry/optional (see §4.3) — the MVP must fully function without them.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────┐
│ GitHub repo (public)                                        │
│                                                             │
│  .github/workflows/daily.yml ──cron──> pipeline/compute.py  │
│        (weekdays 22:00 UTC)             │                   │
│                                         ▼                   │
│                              data/signals.json   (committed)│
│                              data/history.csv    (committed)│
│                                         │                   │
│  .github/workflows/deploy.yml ──────────┴──> GitHub Pages   │
│        (on push to main)                                    │
│                                                             │
│  site/  (static frontend: Vite + vanilla TS + Chart.js)     │
└────────────────────────────────────────────────────────────┘
```

- **Pipeline**: Python 3.11, `pandas`, `numpy`, `yfinance` (primary), `requests` (fallback source). Runs in Actions, recomputes the FULL history every run (idempotent), writes JSON artifacts, commits with `[skip ci]` guard on the data commit, then triggers Pages deploy.
- **Frontend**: static site, Vite build, TypeScript, Chart.js 4.x (UMD or npm). No framework required; if the builder prefers React, that is acceptable, but do not add state libraries — page state is trivial.
- **Hosting**: GitHub Pages from the `gh-pages` branch or Actions artifact deploy (`actions/deploy-pages`). Site root serves `site/dist`.
- **Notifications**: pipeline compares today's state to yesterday's; on transition, creates a GitHub Issue titled `[SIGNAL] {OLD} → {NEW} on {date} @ {close}` using `GITHUB_TOKEN` (built-in, no secret setup).

---

## 3. Data requirements

### 3.1 Primary series (automated, required)
| Data | Ticker/source | Fields | History depth | Cadence |
|---|---|---|---|---|
| SOXX daily OHLCV | `yfinance` ticker `SOXX`, `auto_adjust=True` | date, open, high, low, close, volume | fetch 420 calendar days rolling (need ≥ 260 trading sessions: 200-DMA + 60-day baselines) | daily, post-close |
| Fallback #1 | Stooq CSV: `https://stooq.com/q/d/l/?s=soxx.us&i=d` | same | same | on yfinance failure |
| Fallback #2 | StockAnalysis API: `https://stockanalysis.com/api/symbol/e/SOXX/history?range=1Y&period=Daily` (JSON keys: t,o,h,l,c,a,v) | same | 1Y cap | on both failures |

Rules:
- Use **adjusted** OHLC consistently (SOXX has split history — e.g., 3:1 in March 2024; adjusted series keeps overnight/intraday math valid across splits).
- Persist the fetched raw series to `data/history.csv` each run; if all sources fail, reuse the committed CSV and set `data_stale: true` in `signals.json` (frontend shows a yellow banner with last-good date).
- Validate on ingest: no zero/negative prices, `low ≤ open,close ≤ high`, date monotonic, no duplicate dates, gap between last two sessions ≤ 5 calendar days (else warn). Reject rows failing validation and log to Action summary.

### 3.2 Comparison series (automated, optional but cheap — include in MVP)
| Data | Source | Purpose |
|---|---|---|
| SMH daily close | yfinance `SMH` | confluence line, toggleable on chart |
| ^VIX daily close | yfinance `^VIX` | context panel only (not used in signals) |

### 3.3 Manual/semi-automated inputs (graceful degradation required)
| Data | Source | Entry method | Used for |
|---|---|---|---|
| SOXX 30-day mean IV | AlphaQuery `https://www.alphaquery.com/stock/SOXX/volatility-option-statistics/30-day/iv-mean` (page is client-rendered — scraping is fragile; treat as manual) or Barchart | `data/manual.json` edited by owner (or a scraper job marked `continue-on-error: true`) | VRP spread S10 = IV30 − rv20; instrument-selector card |
| SOXX put/call OI ratio | AlphaQuery / Fintel | same `manual.json` | context card only |

`data/manual.json` schema:
```json
{ "iv30": 0.46, "iv30_asof": "2026-07-01", "pc_oi": 2.9, "pc_oi_asof": "2026-06-18" }
```
Frontend displays these with their as-of dates and greys them out if > 5 trading days stale. All state-machine logic must work when this file is absent.

### 3.4 Static/reference data
- `docs/METHODOLOGY.md` — the ruleset document (ship verbatim).
- `data/events.json` — hand-maintained annotations for the chart (see §5.3), seeded with: 2026-02-04 (first distribution flag), 2026-03-09 (accumulation begins), 2026-06-03 (AVGO guidance AMC), 2026-06-05 (−10.4% crash), 2026-06-18 (trigger EXIT @ 639.45), 2026-06-22 (ATH 655.01, id20 turns negative), 2026-06-26 (Korea capex announcements), 2026-07-01 (−6.4%, Burry SOXX short disclosed).

---

## 4. Computation spec (pipeline/compute.py)

### 4.1 Derived series — exact formulas
Compute on the full fetched history, in this order. `ret_t = close_t/close_{t−1} − 1`.

```python
df["on"]    = df.open / df.close.shift(1) - 1            # S1 overnight
df["id"]    = df.close / df.open - 1                     # S2 intraday
df["on20"]  = df.on.rolling(20).sum()                    # S3
df["id20"]  = df.id.rolling(20).sum()                    # S4  << decision series
df["ret20"] = df.ret.rolling(20).sum()                   # S5
df["ma20"]  = df.close.rolling(20).mean()                # S6
df["ma50"]  = df.close.rolling(50).mean()
df["ma200"] = df.close.rolling(200).mean()
df["rv10"]  = df.ret.rolling(10).std() * sqrt(252)       # S7
df["rv20"]  = df.ret.rolling(20).std() * sqrt(252)
df["turb"]  = (df.ret - df.ret.rolling(60).mean()).abs() / df.ret.rolling(60).std()   # S8
df["ar1"]   = df.ret.rolling(20).apply(lambda x: pd.Series(x).autocorr())             # S9
df["rsi14"] = wilder_rsi(df.close, 14)                   # context only
df["vol30"] = df.volume.rolling(30).mean()
df["dist_day"] = (df.ret <= -0.01) & (df.volume > 1.3 * df.vol30)
df["dist20"]   = df.dist_day.rolling(20).sum()           # context card
vrp = manual.iv30 - df.rv20.iloc[-1]  if manual.iv30 else None    # S10
```

### 4.2 State machine — exact rules (single source of truth; implement once, unit-tested)
Constants (top of file, documented as in-sample choices):
```python
ARM_DIV_ID, ARM_DIV_RET = 0.0, 0.02      # armed if id20<0 and ret20>+2%
ARM_ABS_ID              = -0.03          # or id20 < -3%
ACC_ID, ACC_RET         = 0.02, -0.02    # accumulation: id20>+2% and ret20<-2%
EXIT_ID, EXIT_DAY       = 0.01, 0.015    # exec-into-strength thresholds
ESCAPE_SESSIONS         = 3              # escape valve
DISARM_SESSIONS         = 2              # arm condition must clear 2 days to cancel
REENTER_MA_DAYS         = 2              # closes above ma20 to re-enter
WARMUP_SESSIONS         = 20
```
Pseudocode (iterate chronologically; state persists day to day):
```
armed_cond = (id20 < ARM_DIV_ID and ret20 > ARM_DIV_RET) or (id20 < ARM_ABS_ID)
acc_cond   = (id20 > ACC_ID and ret20 < ACC_RET)

state ∈ {RISK_ON, ARMED, FIRED}; overlay flag ACCUM when acc_cond (ACCUM overrides ARMED)

RISK_ON:  if armed_cond and not acc_cond -> ARMED (record arm_date)
ARMED:    if acc_cond -> RISK_ON
          elif id_t > EXIT_ID or ret_t > EXIT_DAY -> FIRED (record exit trade @ close_t)
          elif sessions_since_arm >= ESCAPE_SESSIONS and close < ma20 -> FIRED (escape valve)
          elif not armed_cond for DISARM_SESSIONS consecutive sessions -> RISK_ON (cancel)
FIRED:    if acc_cond -> RISK_ON (record re-entry @ close_t, reason="accumulation flip")
          elif close > ma20 for REENTER_MA_DAYS consecutive and id20 > 0
               -> RISK_ON (record re-entry, reason="trend reclaim")
```
Position multiplier by state: RISK_ON 1.0 · ARMED 0.6 · FIRED 0.0 · (ACCUM overlay forces 1.0). Suggested size (display only, not advice): `min(1, 0.40 / rv20) × multiplier`.

### 4.3 Strategy equity & trade log
From Jan 20, 2026 (skip warm-up), simulate: hold `position` set at prior close's state, next-day return application (`sret_t = ret_t × pos_{t−1}`). Output cumulative equity vs. buy-and-hold, and the full trade log (date, price, action, reason). This must reproduce §6.4 golden trades.

### 4.4 Outputs — data/signals.json (schema)
```json
{
  "generated_utc": "2026-07-02T22:05:00Z",
  "data_stale": false,
  "last_session": "2026-07-02",
  "state": {"machine": "FIRED", "accum_overlay": false, "since": "2026-06-18",
            "position_multiplier": 0.0, "suggested_size": 0.0,
            "short_permitted": false},
  "today": {"close": 584.05, "ret": -0.0261, "id20": -0.071, "on20": 0.064,
            "ret20": -0.006, "ma20": 598.4, "ma50": 545.1, "ma200": 376.2,
            "rv10": 0.83, "rv20": 0.86, "turb": 1.4, "ar1": -0.27,
            "rsi14": 48.1, "dist20": 5, "vrp": -0.40, "iv30_asof": "2026-07-01"},
  "checklist": [
    {"id": "id20",  "label": "Intraday 20d stream", "value": -0.071, "fmt": "pct1",
     "status": "red",   "note": "negative and falling"},
    {"id": "on20",  "label": "Overnight 20d stream", "value": 0.064, "fmt": "pct1",
     "status": "amber", "note": "fading from +25.4% on Jun 22"},
    {"id": "ma",    "label": "Close vs 20-DMA",      "value": -0.024, "fmt": "pct1",
     "status": "red",   "note": "re-entry line 598.4"},
    {"id": "rv20",  "label": "Realized vol (20d)",   "value": 0.86,  "fmt": "pct0",
     "status": "red",   "note": "regime normalizes < 50%"},
    {"id": "vrp",   "label": "IV30 − RV20",          "value": -0.40, "fmt": "pts",
     "status": "amber", "note": "puts cheap vs realized — hedge with options"}
  ],
  "bands": [
    {"start": "2026-01-02", "end": "2026-01-30", "state": "WARMUP"},
    {"start": "2026-02-02", "end": "2026-02-03", "state": "RISK_ON"},
    {"start": "2026-02-04", "end": "2026-02-05", "state": "ARMED"},
    {"start": "2026-02-06", "end": "2026-03-06", "state": "RISK_ON"},
    {"start": "2026-03-09", "end": "2026-03-31", "state": "ACCUM"},
    {"start": "2026-04-01", "end": "2026-06-08", "state": "RISK_ON"},
    {"start": "2026-06-09", "end": "2026-06-10", "state": "ARMED"},
    {"start": "2026-06-11", "end": "2026-06-17", "state": "RISK_ON"},
    {"start": "2026-06-18", "end": "2026-06-18", "state": "ARMED"},
    {"start": "2026-06-19", "end": null,         "state": "FIRED"}
  ],
  "trades": [
    {"date": "2026-02-05", "price": 330.83, "action": "EXIT",    "reason": "exec-into-strength"},
    {"date": "2026-02-06", "price": 348.51, "action": "REENTER", "reason": "disarm"},
    {"date": "2026-06-18", "price": 639.45, "action": "EXIT",    "reason": "exec-into-strength"}
  ],
  "series": {"dates": [], "close": [], "ma20": [], "id20": [], "on20": [],
             "rv20": [], "equity_strategy": [], "equity_bh": [], "smh_close": []},
  "events": []
}
```
Series arrays cover YTD + prior 60 sessions (for windows), aligned by index. Round: prices 2dp, ratios 4dp. Band `end: null` = ongoing.

### 4.5 Notification step
After compute, diff `state.machine + accum_overlay` vs. the previous committed `signals.json`. On change, `gh api` create issue: title `[SIGNAL] ARMED → FIRED on 2026-06-18 @ $639.45`, body = today's checklist table + link to site. Label: `signal-change`.

---

## 5. Frontend spec (site/)

### 5.1 Layout (single page, top to bottom; 680–1100px content column, responsive to 360px)
1. **Status hero** — large state chip (color per §5.4) + one-sentence machine explanation, `since` date, position multiplier, suggested size, and — when FIRED — the two re-entry conditions with live values ("needs 2 closes > $598.40 with id20 > 0 — currently $584.05, id20 −7.1%"). If `data_stale`: yellow banner "Data last updated {date}".
2. **Five-number checklist** — metric-card row rendered from `checklist[]`: label, formatted value, status dot (green/amber/red), note. This IS the daily read; it must be above the fold on mobile.
3. **Main chart (interactive, ~420px)** — YTD SOXX close line with **color-coded background bands** from `bands[]` (the investment windows), 20-DMA dashed overlay, trade markers (▼ EXIT red, ▲ REENTER green, with price labels), event flags from `events.json` (hover for text). Interactions: crosshair tooltip (date, close, id20, on20, state), range buttons [YTD | 3M | 1M | All], series toggles (20-DMA, SMH indexed to 100, trade markers, events), band hover shows state name + date range.
4. **Decomposition panel (~260px, x-axis synced/linked crosshair with main chart)** — id20 (solid, series-blue) vs. on20 (dashed, series-yellow), zero baseline, same background bands. This is the signal's engine room; label the Jun 22 crossing ("intraday turned negative at the ATH").
5. **Strategy vs. buy-and-hold (~220px)** — two equity lines from `series.equity_*`, plus stat tiles: YTD return both, max DD both, # switches, current position.
6. **Trade log table** — from `trades[]`: date, action, price, reason, P&L vs. B&H since.
7. **Context strip** — RSI-14, distribution-day count (20d), turbulence, AR(1) with playbook note ("< −0.15: mean-reverting — sell rallies, don't short holes"), P/C OI + IV with as-of dates (grey when stale).
8. **Methodology** — collapsible render of `docs/METHODOLOGY.md` + prominent disclaimer: *"Research/education only. Not investment advice. In-sample rules, n=3 episodes."*
9. Footer: data sources + timestamps, GitHub link, license.

### 5.2 Chart implementation notes
- Chart.js 4.x line charts, `pointRadius: 0`, `tension: 0.2`, `interaction: {mode:'index', intersect:false}`, `maintainAspectRatio:false` in fixed-height wrappers.
- Bands: custom plugin `beforeDatasetsDraw` — map band date ranges to x-pixels via `scale.getPixelForValue`, `fillRect` low-alpha colors (§5.4). Same plugin instance on both charts.
- Trade markers: scatter dataset overlaid on the price chart (triangle pointStyle) OR `afterDatasetsDraw` canvas draws; labels must not collide (offset alternating).
- Crosshair sync: on hover of either chart, `setActiveElements` on the other by index.
- Dark mode: `prefers-color-scheme` media listener; re-instantiate charts with dark palette (grid `rgba(137,135,129,.18)`, ticks `#898781`); band alphas +0.04 in dark mode.

### 5.3 Events
Render `events.json` items as small flag markers on the date axis; tooltip shows the annotation. Keep ≤ 12 visible; beyond that, cluster.

### 5.4 State color system (bands, chips, status dots — use everywhere consistently)
| State | Meaning | Band fill (light) | Chip/solid |
|---|---|---|---|
| RISK_ON | invested | `rgba(27,175,122,0.10)` | `#1baf7a` |
| ACCUM | invested, buy-the-dip regime | `rgba(42,120,214,0.14)` | `#2a78d6` |
| ARMED | distribution flagged, de-risk into strength | `rgba(237,161,0,0.14)` | `#eda100` |
| FIRED | flat | `rgba(227,73,72,0.15)` | `#e34948` |
| WARMUP | insufficient window | `rgba(137,135,129,0.10)` | `#898781` |

Never rely on color alone: chips carry text labels; the price line itself stays a single neutral blue (`#2a78d6`) — the bands carry the state.

---

## 6. Repository, CI/CD, testing

### 6.1 Repo structure
```
soxx-regime-monitor/
├── README.md                  # what/why, screenshot, quickstart, disclaimer
├── LICENSE                    # MIT
├── docs/METHODOLOGY.md        # the ruleset doc, verbatim
├── pipeline/
│   ├── compute.py             # fetch → validate → signals → state machine → JSON
│   ├── sources.py             # yfinance + stooq + stockanalysis fallback chain
│   ├── state_machine.py       # pure function: DataFrame -> states/trades/bands
│   ├── requirements.txt       # pandas, numpy, yfinance, requests (pin versions)
│   └── tests/
│       ├── test_signals.py    # formula golden values (§6.4)
│       ├── test_state_machine.py  # transition + trade golden record (§6.4)
│       └── fixtures/soxx_2026.csv # frozen Jan 2 – Jul 1 2026 daily OHLCV
├── data/
│   ├── signals.json           # committed by CI
│   ├── history.csv            # committed by CI (last-good raw data)
│   ├── manual.json            # owner-edited IV / P-C
│   └── events.json            # owner-edited annotations
├── site/                      # Vite + TS + Chart.js
│   ├── index.html
│   ├── src/ (main.ts, charts.ts, bands.ts, state.ts, theme.ts, styles.css)
│   └── vite.config.ts         # base: '/soxx-regime-monitor/'
└── .github/workflows/
    ├── daily.yml              # cron pipeline + commit + issue-on-change
    ├── deploy.yml             # build site + deploy Pages on push to main
    └── ci.yml                 # pytest + tsc + build on PR
```

### 6.2 daily.yml essentials
- `schedule: cron '15 22 * * 1-5'` (22:15 UTC ≈ 6:15pm ET; after close, before Yahoo EOD settles fully — also add a second run `0 2 * * 2-6` as a catch-up) + `workflow_dispatch` for manual runs.
- Steps: checkout → setup-python 3.11 → `pip install -r pipeline/requirements.txt` → `python pipeline/compute.py` → if `data/` diff: commit as `github-actions[bot]` with message `data: {date} state={STATE}` → state-diff issue step → trigger deploy (or let deploy.yml fire on push).
- `permissions: contents: write, issues: write`. Concurrency group to prevent overlap. `continue-on-error` on the (optional) IV scraper step only — never on core fetch (core failure path = reuse history.csv + `data_stale`).
- Market-holiday handling: if last fetched session == previously committed session, exit 0 without commit (no noise commits).

### 6.3 deploy.yml
Standard Pages flow: on push to main affecting `site/` or `data/` → `npm ci && npm run build` (Vite copies `data/*.json` into dist or fetch them relative from repo raw path — prefer copying into `dist/data/` at build so the site is self-contained) → `actions/upload-pages-artifact` → `actions/deploy-pages`. Enable Pages: Settings → Pages → Source: GitHub Actions.

### 6.4 Test vectors (golden record — CI must assert all of these against `fixtures/soxx_2026.csv`)
Signal values (tolerance ±0.2pp on percentages, ±0.3 on prices):
| Date | Assertion |
|---|---|
| 2026-06-22 | close 655.01; id20 = −1.5%; on20 = +25.4%; state ARMED |
| 2026-07-01 | close 599.70; id20 = −6.5%; on20 = +7.8%; ret20 = +1.9%; rv20 ≈ 85%; ma20 ≈ 600.3; ma50 ≈ 542.7; rsi14 ≈ 52.3; ar1 ≈ −0.28; state FIRED |
| 2026-06-05 | ret = −10.44%; turb > 3 (≈3.7) |
| 2026-03-13 | on20 ≈ −12.5%; id20 ≈ +7.1%; ACCUM overlay true |
| 2026-05-15 | ma50 ≈ 401.1; ma200 ≈ 322.9; rsi14 ≈ 64.8 |

State-machine trade record (exact):
| # | Date | Action | Close | Reason |
|---|---|---|---|---|
| 1 | 2026-02-05 | EXIT | 330.83 | exec-into-strength |
| 2 | 2026-02-06 | REENTER | 348.51 | disarm |
| 3 | 2026-06-18 | EXIT | 639.45 | exec-into-strength |

Backtest (from 2026-01-20, next-day position application): strategy ≈ +77.2% vs. B&H ≈ +77.8% at Jul 1 close; strategy position on 2026-07-01 = 0. Also assert: the June 9–10 ARMED episode cancels (disarm) without a trade; ACCUM overlay never coexists with an exit.

Frontend tests (lightweight): JSON schema validation of `signals.json`; band list is contiguous, non-overlapping, covers last session; Playwright smoke test — page renders, five checklist cards present, both canvases painted, state chip text matches JSON.

### 6.5 Operating manual (README content — write it for a non-developer)
- **Normal operation**: nothing to do; check the site after close. A `signal-change` GitHub Issue = state changed.
- **Update IV / P-C** (optional, ~weekly): edit `data/manual.json` in the GitHub web UI (values from AlphaQuery SOXX 30-day IV-mean page and put-call-OI page), commit — site redeploys automatically.
- **Add a chart annotation**: append to `data/events.json` (`{"date":"2026-07-10","label":"TSMC June revenue","text":"..."}`).
- **Pipeline failed** (red X on Actions, stale banner on site): re-run job from the Actions tab; if yfinance is down, the fallback chain usually covers it; worst case the site serves last-good data.
- **Change thresholds**: constants at the top of `pipeline/state_machine.py`; PRs must pass the golden-record tests — if a threshold change intentionally alters the record, update fixtures + document in CHANGELOG.md.
- **New year rollover**: bands/backtest auto-reset to the new YTD on Jan 1 (pipeline derives the year from the last session; "All" range keeps full history).

---

## 7. Build order (suggested for Claude Code)
1. `pipeline/` with tests against the frozen fixture (§6.4) — get the golden record green first; nothing else matters until it is.
2. `compute.py` end-to-end locally → `signals.json`.
3. `site/` reading the checked-in `signals.json`: hero + checklist → main chart with bands → decomposition panel → equity panel → tables → methodology.
4. Workflows: ci.yml → daily.yml → deploy.yml; enable Pages; verify a manual `workflow_dispatch` run commits data and deploys.
5. README + screenshot + disclaimer; cut `v1.0` release.

## 8. Roadmap (post-MVP, do not build now)
P2: automated IV via a paid/stable feed; email/ntfy notifications; SMH/SOXL flow cards; multi-ticker support (SMH, NVDA). P3: the v2 strategic tier (TSMC monthly revenue auto-scrape from pr.tsmc.com, TrendForce memory quotes, hyperscaler capex tracker) rendered as a monthly "cap" gauge; historical multi-year mode.

## 9. Compliance
Prominent site-wide disclaimer: research and education only, not investment advice, no warranty; signals are in-sample with n=3 confirming episodes. MIT license. Respect source ToS: yfinance/Stooq for personal research use; no redistribution of raw vendor data beyond the computed derived series and the small cached CSV needed for reproducibility.
