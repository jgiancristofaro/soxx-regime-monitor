# External Review Adjudication — v3.6

**Date:** 2026-07-07
**Subject:** Independent critique claiming +85.38% YTD vs. +71.80% buy-and-hold via a "Unified Strategy" combining Regime Monitor signals with GEX/Volume-Profile overlays.
**Outcome:** Headline number disqualified (live-candle basis). One element promoted to config flag (WEAK_BOUNCE_EXIT). All other proposals rejected. Adjudication record ships with the codebase per v3.6 acceptance criteria.

---

## Summary

The critique reproduced the correct July 6 close ($581.51, +2.68%) and produced internally consistent arithmetic. Its headline outperformance, however, was measured against an unfinished July 7 intraday print ($538.93 vs. $555.90 at last check) — a live candle that was still open when the review was published. After substituting the settled July 7 close, the incumbent machine beats the proposal on identical data. One structural idea from the proposal survives: restricting exits-into-strength to failed bounces (close < MA20), which performs better than the default in the H2-2025 out-of-sample window.

---

## Finding-by-Finding Record

### G1 — Arithmetic and price data: VALID
The backtest arithmetic is internally exact. The July 6 close of $581.51 (+2.68%) is real. Their trigger table reproduces exactly — but only with the arm condition `id20 < 0` (not `id20 < −0.01` as stated in their text). This is a documentation/code mismatch in their own material; the `< 0` version is what we tested.

### G2 — Trade structure: CONFIRMED, but materially misleading
Their winning variant held 100% long from January 2 through July 6, then went to cash. Every other "transition" in their table was a positionless state flip — the portfolio column was buy-and-hold marked to market throughout. The strategy's only active decision was the single July 6 exit. The framing as a "regime-aware" system obscures that they were fully invested the entire time.

### G3 — H2-2025 out-of-sample: GENUINE FINDING
On H2-2025 (their architecture, our test framework): +22.0% vs. +25.3% B&H with one round-trip exit (vs. the incumbent's +16.7% with five exits). This is the only finding that survives adjudication. The MA20 exit filter suppresses false exits in grinding/range regimes at the cost of forfeiting some top-of-range gains. This becomes **WEAK_BOUNCE_EXIT** (default OFF, v3.6 Change 1).

### G4 — July 7 headline: DISQUALIFIED
Their "July 7 close $538.93" was an intraday print of a live session, verified open at time of review (~$555.90 at last check). All to-the-cent outperformance claims ("preserved $1,357.39") were calculated against an unfinished candle. This is the source of v3.6 Change 3 (live-candle guard in the pipeline). **No finalized backtest should rely on an intraday print as a settlement price.**

### G5 — Incumbent comparison: NOT PROVIDED BY THEM; WE DID IT
They benchmarked only against buy-and-hold, never against the incumbent. Matched post-warmup window (January 20 basis → July 7 live print at time of adjudication):

| Variant | Return | Notes |
|---------|--------|-------|
| Buy-and-hold | +64.8% | |
| Their proposal | +72.4% | Single exit July 6 @ $581.51 |
| Incumbent base (hybrid) | ≈+75.5% | Flat since Jun 22 @ $655.01 |
| Incumbent golden (Mode A) | ≈+79.9% | Flat since Jun 18 @ $639.45 |

The incumbent beats the proposal on identical data. Their apparent edge came from two sources: (1) a January 2 backtest start (harvesting the warmup period whose rolling windows straddle Dec 2025 — the contamination excluded since v3.1); (2) benchmarking against buy-and-hold only.

### G6 — GEX claims: SELF-REFUTING
The winning variant's "Zero-Gamma proxy" is literally the 20-DMA — no options data anywhere in it. The genuinely GEX-calibrated variant ($665.79 level) underperformed buy-and-hold by 4 points in their own table. GEX content is decorative and adds no information over a moving average. **Rejected.**

### G7 — March critique of the incumbent: FACTUALLY FALSE
They claimed the incumbent "likely re-entered the March 25 bear trap." The incumbent's trade log shows no exit before mid-June. The machine never exited in March. The real March flaw — the Mar 31 ABS-arm bottom-sale — is already documented and gated by CRASH_GATE (v3.2 P5).

### G8 — Structural limitation of their arm: CONFIRMED (we identified it, they didn't)
Their DIV-only arm (requires ret20 > +2%) **cannot fire once a decline is underway** — the trend condition can't be satisfied when prices are falling. Only their MA50-crossing clause fires, and that fires after the breakdown. Their exit filter (close < MA20) also kept them fully invested through the $655 → $581 slide (June 22 → July 6); the "July 7 preservation" case study omits that the incumbent was flat 10–13% higher at the time. The ABS arm (`id20 < −3%`) in the incumbent closes exactly this gap.

---

## What Was Not Changed

| Item | Decision |
|------|----------|
| GEX / Zero-Gamma filtering | Rejected: GEX-calibrated variant lost to B&H; "proxy" = 20-DMA |
| DIV-only arming (drop ABS arm) | Rejected: structural dead zone in declining regimes (G8) |
| Jan 2 backtest starts | Remain excluded (warmup contamination, v3.1) |
| All existing thresholds, ACCUM, sizing, hybrid arm | Unchanged |

---

## Change Resulting From This Review

**WEAK_BOUNCE_EXIT = False** (config flag, default OFF)

When ON: exec-into-strength in MONITOR state checks close vs. MA20 before filling.
- `close < MA20` → EXIT (failed bounce, fill at close)
- `close >= MA20` → RISK_ON (genuine reclaim, no trade, disarm)

Evidence: H2-2025 +6pts improvement over default; 2026 window −3pts (forfeits Jun 18/22 exits at $639–655 in exchange for one Jul 6 exit at $581.51). Character: trades exit quality in genuine distributions for fewer false exits in grind regimes. Status: leading promotion candidate; held to the same standard as ENSEMBLE_ARM/CRASH_GATE — default OFF pending 2019–2024 calibration.

The live-candle guard (Change 3) was also triggered by this review.
