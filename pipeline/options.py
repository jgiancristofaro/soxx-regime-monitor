"""
Automated IV30 and P/C OI ratio from the live SOXX options chain (yfinance, free).

Replaces manual entry into data/manual.json as the primary source. manual.json
remains a fallback: if the live fetch fails (network, no expiries, no usable
quotes), compute.py keeps whatever values are already in manual.json.

IV30 methodology: ATM implied vol (average of call/put IV at the strike nearest
spot) at the two expiries bracketing 30 calendar days out, interpolated on
total variance (iv^2 * T) — the same linear-in-variance convention CBOE uses
for VIX — then annualized back to a 30-day IV. This is an ATM approximation,
not a full variance-swap strip; it can diverge from a true VIX-style number
during heavy skew.

P/C OI: total put open interest / total call open interest, summed across
every expiry out to 400 calendar days (LEAPS beyond that carry negligible
open interest for this name and would only add fetch time).
"""
from datetime import date, datetime


def _atm_iv(calls, puts, spot: float) -> float:
    """Average call/put implied vol at the strike(s) nearest spot, skipping
    zero/NaN quotes (Yahoo reports 0.0 for stale/illiquid contracts).

    Strikes are grouped by exact distance from spot so ties (e.g. spot
    equidistant between two strikes) average all valid quotes at that
    distance instead of depending on arbitrary set-iteration order."""
    by_dist: dict = {}
    for k in set(calls["strike"]).union(set(puts["strike"])):
        by_dist.setdefault(abs(k - spot), []).append(k)

    for d in sorted(by_dist):
        ivs = []
        for k in by_dist[d]:
            c_iv = calls.loc[calls["strike"] == k, "impliedVolatility"]
            p_iv = puts.loc[puts["strike"] == k, "impliedVolatility"]
            ivs.extend(float(v) for v in list(c_iv) + list(p_iv) if v and v > 0)
        if ivs:
            return sum(ivs) / len(ivs)
    raise RuntimeError("No usable (non-zero) implied vol quotes near spot")


def fetch_options_metrics(
    ticker: str = "SOXX",
    spot: float | None = None,
    today: "date | None" = None,
    expiries: "list[str] | None" = None,
    chain_fn=None,
) -> dict:
    """
    Returns {"iv30": float, "pc_oi": float, "iv30_expiries": [str, ...]}.
    Raises RuntimeError/ValueError on any failure — caller falls back to manual.json.

    expiries / chain_fn are injectable for testing without live network calls.
    chain_fn(expiry: str) -> (calls_df, puts_df), each with columns
    "strike", "impliedVolatility", "openInterest".
    """
    if spot is None:
        raise ValueError("spot price is required")
    if today is None:
        today = date.today()

    if expiries is None or chain_fn is None:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        expiries = list(tk.options)

        def chain_fn(expiry: str):
            oc = tk.option_chain(expiry)
            return oc.calls, oc.puts

    if not expiries:
        raise RuntimeError(f"No options expiries available for {ticker}")

    dte = {}
    for e in expiries:
        exp_date = datetime.strptime(e, "%Y-%m-%d").date()
        days = (exp_date - today).days
        if days > 0:
            dte[e] = days
    if not dte:
        raise RuntimeError(f"No future options expiries available for {ticker}")

    sorted_exp = sorted(dte.items(), key=lambda kv: kv[1])
    lower = max((kv for kv in sorted_exp if kv[1] <= 30), key=lambda kv: kv[1], default=None)
    upper = min((kv for kv in sorted_exp if kv[1] >= 30), key=lambda kv: kv[1], default=None)

    chain_cache: dict = {}

    def get_chain(expiry: str):
        if expiry not in chain_cache:
            chain_cache[expiry] = chain_fn(expiry)
        return chain_cache[expiry]

    if lower and upper and lower[0] != upper[0]:
        e1, t1 = lower
        e2, t2 = upper
        calls1, puts1 = get_chain(e1)
        calls2, puts2 = get_chain(e2)
        iv1 = _atm_iv(calls1, puts1, spot)
        iv2 = _atm_iv(calls2, puts2, spot)
        w = (t2 - 30) / (t2 - t1)
        var30 = w * (iv1 ** 2 * t1) + (1 - w) * (iv2 ** 2 * t2)
        iv30 = (var30 / 30) ** 0.5
        expiries_used = [e1, e2]
    else:
        e1, t1 = lower or upper
        calls1, puts1 = get_chain(e1)
        iv30 = _atm_iv(calls1, puts1, spot)
        expiries_used = [e1]

    total_call_oi = 0.0
    total_put_oi = 0.0
    for e, days in sorted_exp:
        if days > 400:
            continue
        calls, puts = get_chain(e)
        total_call_oi += float(calls["openInterest"].fillna(0).sum())
        total_put_oi += float(puts["openInterest"].fillna(0).sum())

    if total_call_oi <= 0:
        raise RuntimeError(f"No call open interest found for {ticker}; cannot compute P/C OI")

    return {
        "iv30": round(float(iv30), 4),
        "pc_oi": round(total_put_oi / total_call_oi, 4),
        "iv30_expiries": expiries_used,
    }
