"""Opu's entry — "Six-One Momentum, Cash When Scared".

A first real trading bot. The idea is the classic academic momentum signal
(6-month return, skipping the most recent month so we don't chase a one-week
pop), applied across the big index, sector, and mega-cap names. Hold the four
strongest, equal weight. If fewer than four names are actually trending up, the
empty slots just sit in cash — when in doubt, hold cash.

Why I think it survives the three admission regimes:
  * Crash / contagion: most names go negative momentum fast → we drop to cash,
    which caps the drawdown instead of riding it down.
  * Slow rate-driven downtrend: same gate — names roll over one by one and we
    de-risk into cash rather than averaging down.
  * Vol spike + snapback: we stay defensive through the spike, then momentum
    turns positive again and we rotate back in for the recovery.

No leverage (every name is 1x), no network, no LLM, stdlib only. Each holding
is capped at 22% so we're always well under the 30% concentration limit, and
gross is ~0.88x — nowhere near the 1.5x leverage cap.
"""
from __future__ import annotations

# ---- knobs (kept simple on purpose) ----------------------------------------
LOOKBACK_DAYS = 126      # ~6 trading months
SKIP_DAYS = 21           # skip the most recent ~1 month (classic 6-1 momentum)
TOP_N = 4                # hold the 4 strongest trending names
WEIGHT_EACH = 0.22       # 4 x 22% = 88% invested, 12% cash buffer; each < 30% cap
REBALANCE_EVERY = 5      # re-check ~weekly (bars are daily); dead-band stops churn
DEAD_BAND = 0.02         # ignore trades smaller than 2% of equity

# Candidate universe — all inside the v0 list, all 1x (no leveraged ETFs).
UNIVERSE = (
    "SPY", "QQQ", "DIA", "IWM",                 # broad index
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "SMH",  # sectors
    "AAPL", "MSFT", "NVDA",                     # mega-cap tech
)

_tick = 0
_last_rebalance = -10**9


def _closes(bars):
    return [float(b["close"]) for b in bars] if bars else []


def _momentum(bars):
    """6-month return, skipping the most recent month. None if not enough history."""
    closes = _closes(bars)
    if len(closes) < LOOKBACK_DAYS + 1:
        return None
    start = closes[-(LOOKBACK_DAYS + 1)]
    end = closes[-(SKIP_DAYS + 1)]  # value as of ~1 month ago
    if start <= 0:
        return None
    return end / start - 1.0


def _target_weights(market_state):
    ranked = []
    for t in UNIVERSE:
        m = _momentum(market_state.get(t) or [])
        if m is not None and m > 0:        # absolute-momentum gate: only positive trends
            ranked.append((m, t))
    ranked.sort(reverse=True)
    winners = [t for _, t in ranked[:TOP_N]]
    # Empty slots stay in cash on purpose — we do NOT pile into weak names.
    return {t: WEIGHT_EACH for t in winners}


def decide(market_state, portfolio_state, cash):
    global _tick, _last_rebalance
    _tick += 1

    positions = {p["ticker"]: p for p in portfolio_state.get("positions", [])}
    last_prices = portfolio_state.get("last_prices", {})
    equity = portfolio_state.get("cash", cash)
    for tk, pos in positions.items():
        equity += pos["quantity"] * last_prices.get(tk, pos.get("avg_cost", 0))

    if _tick - _last_rebalance < REBALANCE_EVERY:
        return []

    targets = _target_weights(market_state)

    orders = []
    # Exit anything that's no longer a winner (incl. when we've gone fully to cash).
    for ticker, pos in positions.items():
        if ticker not in targets and pos["quantity"] > 0:
            orders.append({"ticker": ticker, "side": "sell", "quantity": pos["quantity"]})

    # Move each target toward its weight, ignoring tiny adjustments.
    for ticker, weight in targets.items():
        bars = market_state.get(ticker)
        if not bars:
            continue
        last_close = float(bars[-1]["close"])
        if last_close <= 0 or equity <= 0:
            continue
        target_dollars = equity * weight
        cur_qty = positions.get(ticker, {}).get("quantity", 0)
        delta_qty = int((target_dollars - cur_qty * last_close) // last_close)
        if abs(delta_qty * last_close) < DEAD_BAND * equity:
            continue
        if delta_qty > 0:
            orders.append({"ticker": ticker, "side": "buy", "quantity": delta_qty})
        elif delta_qty < 0 and cur_qty > 0:
            orders.append({"ticker": ticker, "side": "sell", "quantity": min(abs(delta_qty), cur_qty)})

    if orders:
        _last_rebalance = _tick
    return orders
