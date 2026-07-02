# WORKLOG — SOXX Regime Monitor

> Append-only decision log. NEVER edit old entries. Add new entries at the bottom.
> AI agents: read this index first to understand prior decisions before starting work.

---

## Index

| # | Date | Title |
|---|------|-------|
| [001](#001) | 2026-07-02 | Project initialized from PRD |
| [002](#002) | 2026-07-02 | Directory structure and toolchain decisions |
| [003](#003) | 2026-07-02 | GitHub repo naming and Pages URL |
| [004](#004) | 2026-07-02 | State machine constant choices (in-sample) |
| [005](#005) | 2026-07-02 | Frontend stack decision: Vite + TS + Chart.js, no framework |
| [006](#006) | 2026-07-02 | Data pipeline fallback chain order |

---

<a name="001"></a>
## [001] 2026-07-02 — Project initialized from PRD

Created SOXX Regime Monitor project from the PRD+Tech Spec document `Starting docs/SOXX_Dashboard_PRD_and_Tech_Spec.md` and the methodology document `Starting docs/SOXX_Regime_Analysis_2026-07-02.md`. The user requested a complete build including a public GitHub Pages URL, CLAUDE.md, and WORKLOG.MD.

Initial state per the spec as of July 2, 2026: state machine is FIRED (flat) since June 18, 2026, when SOXX exited at $639.45. Current close ~$599.70 (July 1), id20 = −6.5%, on20 = +7.8%.

---

<a name="002"></a>
## [002] 2026-07-02 — Directory structure and toolchain decisions

**Working directory:** `C:\Users\jgian\Documents\Claude\semis-tracker` is treated as the repo root (the folder was pre-created for this project). GitHub repo will be named `soxx-regime-monitor` (per PRD §1.3).

**Environment verified:**
- Node.js v24.16.0 / npm 11.13.0
- Python 3.11.9
- GitHub CLI: authenticated as `jgiancristofaro`

**Directory layout follows PRD §6.1 exactly** — no deviations. `Starting docs/` folder (pre-existing reference material) stays in the repo as-is for reference but is not served by the site.

---

<a name="003"></a>
## [003] 2026-07-02 — GitHub repo naming and Pages URL

Repo name: `soxx-regime-monitor` (as specified in PRD §1.3).
Public URL: `https://jgiancristofaro.github.io/soxx-regime-monitor/`
Vite base path: `/soxx-regime-monitor/` (required for asset loading under the subpath).
Pages source: GitHub Actions (not branch deploy) — required for the deploy.yml workflow approach.

---

<a name="004"></a>
## [004] 2026-07-02 — State machine constant choices (in-sample)

All constants in `pipeline/state_machine.py` are in-sample choices validated against Jan 20 – Jul 1, 2026 data (n=3 episodes). They reproduce the golden record exactly:

- EXIT Feb 5 @ $330.83 (exec-into-strength)
- REENTER Feb 6 @ $348.51 (disarm after 2 consecutive sessions clearing arm condition)
- EXIT Jun 18 @ $639.45 (exec-into-strength — id_t crossed +1%)

The Jun 9–10 ARMED episode cancels without a trade (disarm). The Mar 9–31 ACCUM overlay fires (id20 +7.1%, ret20 < -2%) and correctly prevents an exit during that accumulation period.

WARNING: These thresholds are not proven forward-looking. Changing them requires updating the golden-record test fixtures and documenting here.

---

<a name="005"></a>
## [005] 2026-07-02 — Frontend stack decision: Vite + TS + Chart.js, no framework

PRD allows React but specifies no state libraries needed. Chose vanilla TypeScript with Vite and Chart.js 4.x (no React) because:
1. PRD explicitly states page state is trivial
2. Removes a dependency layer and bundle size
3. Chart.js integration is simpler without React wrapper
4. Aligns with PRD's preferred approach

Chart.js 4.x for: line charts (price, decomp, equity), scatter overlay for trade markers, custom plugin for background bands (beforeDatasetsDraw fillRect).

---

<a name="006"></a>
## [006] 2026-07-02 — Data pipeline fallback chain order

Fallback chain: yfinance (primary) → Stooq CSV → StockAnalysis API.

Rationale per PRD §3.1:
- yfinance: most reliable for adjusted OHLCV with split correction (SOXX had 3:1 split March 2024)
- Stooq: free, reliable CSV endpoint, good fallback
- StockAnalysis: JSON API, 1-year cap but sufficient for YTD signals

If ALL sources fail: reuse `data/history.csv` (last-good committed data) and set `data_stale: true` in signals.json. Frontend shows yellow banner. This ensures the site never crashes on a pipeline failure.

IV30 and P/C OI are manual-entry (`data/manual.json`) — not automated, treated as optional. All state-machine logic works without them.

---
