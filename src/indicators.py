from __future__ import annotations
from statistics import pstdev

def pct_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (new / old - 1.0) * 100.0

def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for a, b in zip(values[-period - 1:-1], values[-period:]):
        delta = b - a
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def volatility_pct(values: list[float]) -> float | None:
    if len(values) < 2 or not values[-1]:
        return None
    returns = [(b / a - 1.0) * 100.0 for a, b in zip(values[:-1], values[1:]) if a]
    return pstdev(returns) if returns else None

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

def signal_score(*, rsi_value: float | None, momentum_5m: float | None,
                 volatility_5m: float | None, spread_bps: float, samples: int) -> float:
    if samples < 5:
        return 0.0
    momentum_component = _clamp((momentum_5m or 0.0) / 8.0, -1.0, 1.0)
    momentum_score = 0.5 + 0.5 * momentum_component
    rsi_component = 0.5 if rsi_value is None else _clamp(1.0 - abs(rsi_value - 55.0) / 55.0, 0.0, 1.0)
    vol_score = 0.5 if volatility_5m is None else _clamp(1.0 - volatility_5m / 8.0, 0.0, 1.0)
    spread_score = _clamp(1.0 - spread_bps / 500.0, 0.0, 1.0)
    return _clamp(0.40 * momentum_score + 0.25 * rsi_component + 0.20 * vol_score + 0.15 * spread_score, 0.0, 1.0)
