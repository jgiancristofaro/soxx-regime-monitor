# SOXX Regime Ruleset — v3 (Final)
**As of July 2, 2026 · iShares Semiconductor ETF (SOXX) · supersedes all prior versions**

---

## 0. The system in ten lines

1. The core signal is the **overnight/intraday return decomposition**: who is actually buying — institutions (intraday, open→close) or gaps (overnight, close→open).
2. When price rises but the 20-day intraday stream is negative, institutions are selling into the overnight bid. That is **distribution**. Arm the system.
3. When price falls but the 20-day intraday stream is strongly positive, institutions are buying the decline. That is **accumulation**. Stay long or re-enter.
4. Once armed, **de-risk only into strength** — on the next +1% intraday or +1.5% day — never into a panic close. This single execution rule was worth ~20 points of return in 2026.
5. Realized vol does **not** trigger anything. It sizes the position (σ_target / RV envelope).
6. Implied vol does **not** trigger anything. It selects the de-risking instrument: IV rich vs. RV → sell shares; IV cheap vs. RV → hold shares, buy puts.
7. Fundamentals (TSMC revenue, memory pricing, hyperscaler capex) do **not** trigger anything. They set the maximum position cap, monthly.
8. Moving averages are confirmation and re-entry lines only (20-DMA), never the primary signal.
9. In 2026 this fired three times: a small February whipsaw, and a clean exit at **$639 on June 18 — four days before the $655 all-time high** — flat through the entire June 23 / June 26 / July 1–2 slide.
10. **Current state: ARMED + FIRED → FLAT** (since June 18). Re-entry conditions in §5.

---

## 1. Signal definitions (exact formulas)

All computed daily on adjusted OHLC. Rolling windows require ~60 prior sessions of history; never display or act on a window that crosses fewer than 20 real sessions.

| # | Name | Formula | Notes |
|---|---|---|---|
| S1 | Overnight return | `on_t = open_t / close_{t-1} − 1` | Gap: futures, Asia, retail MOO |
| S2 | Intraday return | `id_t = close_t / open_t − 1` | Regular session: institutional execution window |
| S3 | Overnight stream | `on20_t = Σ on over trailing 20 sessions` | |
| S4 | Intraday stream | `id20_t = Σ id over trailing 20 sessions` | **The core series** |
| S5 | Total return stream | `ret20_t = Σ daily returns over trailing 20 sessions` | |
| S6 | 20-DMA | mean of trailing 20 closes | Re-entry/confirmation line only |
| S7 | Realized vol | `rv20_t = stdev(daily ret, 20) × √252` (also rv10) | Sizing only |
| S8 | Turbulence | `z_t = |ret_t − mean(ret,60)| / stdev(ret,60)` | Crash-day confirm (z > 3) |
| S9 | AR(1) | rolling 20-day autocorrelation of daily returns | Playbook selector: < −0.15 = mean-reverting tape → sell rallies, never short holes |
| S10 | VRP spread | `IV30 − rv20` (IV from SOXX/SMH 30-day ATM, manual or feed) | Instrument selector only |

Semis carry a structural overnight bias (TSMC/Korea/Taiwan news prints while the US sleeps), so `on20` is compared to its own trailing baseline, never to zero. `id20` is the decision series.

## 2. State machine

```
WARM-UP  : first 20 sessions of any data window → no signals displayed
RISK-ON  : default. id20 ≥ 0, no arm condition.
ARMED    : (id20 < 0 AND ret20 > +2%)  OR  (id20 < −3%)
ACCUM    : id20 > +2% AND ret20 < −2%   (institutions buying a falling tape)
FIRED    : was ARMED, exit executed → position 0 (or hedged, per §4)
```

**Transitions**
- RISK-ON → ARMED: on the first close meeting the arm condition.
- ARMED → FIRED: execute the exit on the first day with `id_t > +1%` OR `ret_t > +1.5%` (**exec-into-strength**). Escape valve: if no strength day within 3 sessions AND close < 20-DMA, exit at next close regardless.
- ARMED → RISK-ON: arm condition clears for 2 consecutive sessions before an exit executes → cancel (this handled the June 11–17 rip correctly).
- FIRED → RISK-ON (re-entry), either of:
  - **Accumulation flip:** ACCUM state prints (the March 2026 pattern — earliest credible tell, leads MAs by weeks), or
  - **Trend reclaim:** 2 consecutive closes above the 20-DMA with `id20 > 0`.
- ACCUM overrides ARMED (never exit into a tape institutions are buying).

## 3. What volatility is for (and not for)

- **RV is the sizer, not the trigger.** Tested directly: an RV-acceleration trigger is *fully long today* — it cannot see distribution because the May melt-up polluted the vol baseline. Position size = `min(1, 40% / rv20) × state multiplier` (RISK-ON 1.0, ARMED 0.5–0.7, FIRED 0, ACCUM 1.0), all under the strategic cap (§6).
- **IV is the instrument selector, not the trigger.** When armed: if `IV30 − rv20` > +10 pts (insurance rich) → de-risk by selling shares; if < −10 pts (insurance cheap) → hold more shares and buy 1–3 month puts instead. **Today: IV ≈ 46 vs. rv20 ≈ 85 → −39 pts → puts are drastically cheap relative to realized movement; the correct expression of risk-off right now is owning puts, not adding to the sold position.** Deeply negative VRP does not persist: either realized calms (puts expire, small cost) or IV reprices violently higher (puts pay).

## 4. Trigger evidence — 2026 (in-sample, n=3 episodes; treat as protocol validation, not alpha proof)

| Variant (from Jan 20) | Return | Max DD | Switches | Position today |
|---|---|---|---|---|
| Buy & hold | +77.8% | −15.8% | — | 1.0 |
| Trigger, executed immediately | +57.0% | −20.1% | 11 | 0 |
| **Trigger, executed into strength** | **+77.2%** | −18.6% | **3** | **0** |
| RV-only trigger (control) | +73.1% | −16.8% | 2 | 1.0 (blind to distribution) |

Trade log (exec-into-strength): EXIT Feb 5 @ $331 → RE-ENTER Feb 6 @ $349 (whipsaw, −5% cost) → EXIT **Jun 18 @ $639** → flat since (price now ~$584).

Episode record of the raw state signal:
- **Feb 12–13 divergence** → preceded the −15.8% Feb 25–Mar 30 correction by ~2 weeks. ✔
- **Mar 9–31 accumulation** (on20 −12.5%, id20 +7.1%: decline was all gaps, institutions bought all day) → preceded the +40% April melt-up. ✔ (Also why no system avoids the Feb–Mar drawdown: the intraday side correctly read it as accumulation.)
- **Jun 9–10, then Jun 18–Jul 1 divergence** — id20 went negative on **Jun 22, the day of the $655 ATH**, while on20 was +25.4%: the final leg was entirely gaps being sold into. ✔

Known limits, stated plainly: rules designed and tested on the same six months; thresholds (+2%/−3%/+1%/+1.5%) are in-sample choices; forward 5–10-day returns conditioned on divergence showed no mean edge in 2026 (dip-buying bailed out every warning inside the sample; the June payoff is occurring now, outside it). Statistically this is a disciplined de-risking protocol with three confirming episodes — not a proven return engine. A 2020-style overnight gap crash would blow through the exec-into-strength rule; the 3-session escape valve is the only protection.

## 5. Current state and re-entry (July 2, 2026)

- State: **FIRED / FLAT** since Jun 18. id20 = −6.5% and falling. Critical nuance: **on20 has faded from +25.4% (Jun 22) to +7.8%** — the overnight bid was the only thing holding price up, and it is tiring. Overnight and intraday both negative = divergence resolving into downtrend; do not anticipate re-entry.
- Re-enter on either: (a) ACCUM prints — id20 > +2% while ret20 < −2%; or (b) two consecutive closes above the 20-DMA (~$598, falling) with id20 > 0.
- No shorting while AR(1) < −0.15 (currently −0.28: mean-reverting tape; June's SOXS/put crowd was carried out twice). A short becomes permissible only on: close < 50-DMA (~$545) on >1.5× volume AND id20 < 0 AND on20 < 0.
- Fundamental context (strategic cap, monthly): TSMC May +30% YoY (June print ~Jul 10), DRAM/NAND contracts still rising, hyperscaler capex ~$700B+ uncut, CoWoS sold out through 2027 — but Korea's $518–646B capacity announcement is the classic memory-cycle supply response, and the SK Hynix $29.4B US listing adds paper supply. **Cap = 0.6** until hyperscaler Q2 calls (late July) and TSMC June revenue re-affirm.

## 6. Daily monitoring — the five numbers (checked at each close)

1. **id20** — the decision series. Sign and 5-day slope.
2. **on20** — is the overnight bid holding? Both streams negative = trend, not chop.
3. **Close vs. 20-DMA** (~$598) — re-entry line; vs. 50-DMA (~$545) — short-permission line.
4. **rv20** (~85%) — sets size when re-entered; falling below ~50% normalizes the regime.
5. **IV30 − rv20** (~−39) — instrument selector; a snap back above 0 while armed = switch from puts to share sales.

Weekly adds: TSMC monthly revenue (~10th), TrendForce DRAM/NAND quotes, semi ETF net flows (SMH+SOXX+SOXL), P/C OI at front expiries, hyperscaler earnings calendar.

## 7. Data sources

Price/volume: Yahoo Finance or StockAnalysis.com (S&P Global) daily OHLCV — all core signals need OHLC only. IV: AlphaQuery/OptionCharts/Barchart (SOXX 30-day mean IV; SMH as fallback proxy). P/C OI: AlphaQuery/Fintel. Flows: ETF Action / ETF.com. Fundamentals: TSMC IR (monthly), TrendForce (memory), SEMI (billings), hyperscaler IR. Full 2026 research record (sell-off catalysts, options/flow/fundamental evidence, v1/v2 framework post-mortems) is preserved in the project research notes and the companion PRD.

*Research, not investment advice. Signals are in-sample on one regime; position sizing and the strategic cap exist because any of this can be wrong.*
