# SOXX Regime Monitor

A zero-cost, publicly hosted dashboard tracking the overnight/intraday return decomposition signal on SOXX (iShares Semiconductor ETF). Computes and displays the regime state machine daily — RISK-ON / ARMED / FIRED / ACCUMULATION — with an interactive YTD price chart color-coded by investment window.

**Live dashboard:** https://jgiancristofaro.github.io/soxx-regime-monitor/

---

## What it does

The core signal is the **overnight vs. intraday return decomposition**: who is buying — institutions (intraday, open→close) or gaps (overnight, close→open)?

- When price rises but the 20-day intraday stream (`id20`) is negative, institutions are selling into the overnight bid → **distribution** → system arms
- When price falls but `id20` is strongly positive, institutions are buying the decline → **accumulation** → stay long or re-enter
- Once armed, de-risk only into strength (next +1% intraday or +1.5% day)

In 2026 this fired three times, including a clean exit at **$639 on June 18 — four days before the $655 ATH**.

## The five daily numbers

| # | Signal | What it means |
|---|--------|---------------|
| 1 | **id20** | 20-day intraday stream — the decision series |
| 2 | **on20** | 20-day overnight stream — is the overnight bid holding? |
| 3 | Close vs 20-DMA | Re-entry line |
| 4 | rv20 | Realized vol — sets position size |
| 5 | IV30 − RV20 | Instrument selector: puts cheap or expensive vs. realized |

## Quickstart

### Run the pipeline locally

```bash
pip install -r pipeline/requirements.txt
python pipeline/compute.py
# → generates data/signals.json and data/history.csv
```

### Run the frontend dev server

```bash
cd site
npm install
npm run dev
```

### Run tests

```bash
pytest pipeline/tests/ -v
```

## How it works

```
GitHub Actions (cron, weekdays 22:15 UTC)
  pipeline/compute.py → data/signals.json (committed)
  
GitHub Pages (deploys on every push to main)
  site/ (Vite + TypeScript + Chart.js) → static dashboard
```

No servers, no paid APIs, no secrets required.

## Update IV / P-C data (optional, ~weekly)

Edit `data/manual.json` directly in the GitHub web UI:
```json
{ "iv30": 0.46, "iv30_asof": "2026-07-01", "pc_oi": 2.9, "pc_oi_asof": "2026-06-18" }
```
Values from [AlphaQuery SOXX 30-day IV-mean](https://www.alphaquery.com/stock/SOXX/volatility-option-statistics/30-day/iv-mean). Commit → site redeploys automatically.

## Add a chart annotation

Append to `data/events.json`:
```json
{"date": "2026-07-10", "label": "TSMC June revenue", "text": "Monthly revenue report"}
```

## Methodology

See [docs/METHODOLOGY.md](docs/METHODOLOGY.md) for the complete ruleset including signal formulas, state machine transitions, and known limitations.

## Disclaimer

> **Research and education only. Not investment advice.** The signals shown are based on in-sample rules with n=3 confirming episodes (Jan–Jul 2026). Thresholds were chosen on the same data they are tested against. Past performance does not guarantee future results. No warranty of any kind. Position sizing shown is illustrative only — do your own due diligence before making any financial decisions.

## License

MIT © 2026 jgiancristofaro

Data sources: Yahoo Finance (yfinance), Stooq, StockAnalysis. Used for personal research only; raw price data is not redistributed beyond the small cached CSV required for reproducibility.
