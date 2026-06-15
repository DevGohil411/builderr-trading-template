"""Strategy-level checks for the Proven Calmar Defender agent.py.

No network, no private engine, no third-party packages.
Run:
    python strategy_selftest.py
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import agent


UNIVERSE = (
    "SPY", "QQQ", "DIA", "IWM",
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLRE", "XLC", "SMH",
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
)


def bars(start: float, returns: list[float]) -> list[dict]:
    out = []
    px = start
    d = date(2024, 1, 1)
    for r in returns:
        px *= 1.0 + r
        out.append({
            "ts": d.isoformat(),
            "open": px,
            "high": px * 1.01,
            "low": px * 0.99,
            "close": px,
            "volume": 1_000_000,
        })
        d += timedelta(days=1)
    return out


def market(kind: str) -> dict[str, list[dict]]:
    if kind == "risk_off":
        base = [-0.003] * 110
        return {t: bars(100.0, base) for t in UNIVERSE}

    if kind == "high_vol":
        calm_up = [0.002] * 110
        qqq_chop = [0.035, -0.03] * 55
        data = {t: bars(100.0, calm_up) for t in UNIVERSE}
        data["QQQ"] = bars(100.0, qqq_chop)
        return data

    # Low-vol risk-on, with differentiated momentum strong enough to clear
    # the +1% hysteresis band above the 100-day SMA.
    data = {t: bars(100.0, [0.001] * 110) for t in UNIVERSE}
    for t in ("SMH", "NVDA", "XLK"):
        data[t] = bars(100.0, [0.012] * 110)
    for t in ("QQQ", "AAPL", "META"):
        data[t] = bars(100.0, [0.008] * 110)
    data["SPY"] = bars(100.0, [0.006] * 110)
    return data


def reset_agent_state() -> None:
    agent._last_rebalance_bar_date = None
    agent._last_regime = None
    agent._last_targets = {}
    agent._cooldown_days = 0
    agent._peak_equity = 0.0


def beta_gross(weights: dict[str, float]) -> float:
    return sum(w * agent.BETA_MULTIPLE.get(t, 1.0) for t, w in weights.items())


def test_empty_data_returns_no_orders() -> None:
    reset_agent_state()
    assert agent.decide({}, {"cash": 100_000, "positions": [], "last_prices": {}}, 100_000) == []


def test_insufficient_history_returns_empty_targets() -> None:
    short_market = {t: bars(100.0, [0.001] * 40) for t in UNIVERSE}
    assert agent.target_weights(short_market) == {}


def test_risk_off_returns_cash_or_defensive() -> None:
    weights = agent.target_weights(market("risk_off"))
    if agent.SOFT_DEFENSIVE_WEIGHTS:
        assert set(weights).issubset({t for t, _ in agent.SOFT_DEFENSIVE_WEIGHTS})
    else:
        assert weights == {}


def test_risk_on_selects_positive_momentum() -> None:
    weights = agent.target_weights(market("risk_on"))
    assert {"SMH", "NVDA", "XLK"} & set(weights)
    assert len(weights) >= 3


def test_high_vol_disables_risk_on() -> None:
    weights = agent.target_weights(market("high_vol"))
    assert all(t not in weights for t in ("SMH", "NVDA", "XLK"))


def test_caps_hold() -> None:
    for kind in ("risk_off", "high_vol", "risk_on"):
        weights = agent.target_weights(market(kind))
        assert all(w < 0.230001 for w in weights.values()), (kind, weights)
        assert beta_gross(weights) <= 1.350001, (kind, weights, beta_gross(weights))


def test_orders_are_bounded_and_fast() -> None:
    reset_agent_state()
    m = market("risk_on")
    latest = {t: b[-1]["close"] for t, b in m.items()}
    portfolio = {"cash": 100_000.0, "positions": [], "last_prices": latest}
    start = time.perf_counter()
    orders = agent.decide(m, portfolio, 100_000.0)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.05, elapsed
    assert 0 < len(orders) < 50, orders
    assert all(o["side"] in {"buy", "sell"} and o["quantity"] > 0 for o in orders)
    assert agent.decide(m, portfolio, 100_000.0) == []


def test_tiny_stale_position_is_not_sold() -> None:
    orders = agent.orders_to_rebalance(
        targets={"SPY": 0.20},
        positions={"XYZ": {"quantity": 0.5, "avg_cost": 100.0}},
        total_equity=100_000.0,
        prices={"XYZ": 100.0, "SPY": 500.0},
        cash_available=0.0,
    )
    assert orders == []


def test_drawdown_governor_scales_down() -> None:
    reset_agent_state()
    # Establish peak.
    assert agent._drawdown_scale(120_000.0) == 1.0
    # 110k is 8.3% drawdown -> between tier 2 (6%) and tier 3 (10%) -> 0.35.
    assert agent._drawdown_scale(110_000.0) == 0.35
    # 100k is 16.7% drawdown -> beyond tier 3 -> 0.10.
    assert agent._drawdown_scale(100_000.0) == 0.10
    # 115k is 4.2% drawdown -> between tier 1 (3%) and tier 2 (6%) -> 0.65.
    assert agent._drawdown_scale(115_000.0) == 0.65
    # 118k is 1.7% drawdown -> below tier 1 -> 1.0.
    assert agent._drawdown_scale(118_000.0) == 1.0


def run() -> None:
    tests = [
        test_empty_data_returns_no_orders,
        test_insufficient_history_returns_empty_targets,
        test_risk_off_returns_cash_or_defensive,
        test_risk_on_selects_positive_momentum,
        test_high_vol_disables_risk_on,
        test_caps_hold,
        test_orders_are_bounded_and_fast,
        test_tiny_stale_position_is_not_sold,
        test_drawdown_governor_scales_down,
    ]
    for test in tests:
        test()
    print(f"OK {len(tests)} strategy checks passed.")


if __name__ == "__main__":
    run()
