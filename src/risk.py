from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from .config import Settings
from .models import AIDecision, MarketSnapshot

@dataclass(frozen=True)
class RiskResult:
    allowed: bool
    reasons: list[str]

class RiskEngine:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def entry_check(self, *, decision: AIDecision, snapshot: MarketSnapshot, order_usd: float,
                     buying_power: float, open_positions: int, daily_spend: float,
                     daily_realized_pnl: float, last_trade_time: datetime | None,
                     estimated_round_trip_pct: float) -> RiskResult:
        reasons = []
        now = datetime.now(timezone.utc)
        if decision.action != "BUY": reasons.append("AI did not authorize a BUY")
        if decision.confidence < self.s.min_ai_confidence: reasons.append(f"AI confidence {decision.confidence:.2f} below gate")
        if snapshot.signal_score < self.s.min_signal_score: reasons.append("deterministic signal score below gate")
        if snapshot.spread_bps > self.s.max_spread_bps: reasons.append("spread above gate")
        if estimated_round_trip_pct > self.s.max_round_trip_cost_pct: reasons.append("estimated round-trip friction above gate")
        if snapshot.min_order_amount > order_usd: reasons.append("order below broker minimum")
        if order_usd > self.s.max_order_usd: reasons.append("order exceeds max order")
        if order_usd > self.s.max_position_usd: reasons.append("order exceeds max position")
        if order_usd > buying_power: reasons.append("order exceeds buying power")
        if daily_spend + order_usd > self.s.max_daily_spend_usd: reasons.append("daily spend cap would be exceeded")
        if daily_realized_pnl <= -self.s.max_daily_loss_usd: reasons.append("daily realized-loss cap reached")
        if open_positions >= self.s.max_open_positions: reasons.append("max open positions reached")
        if last_trade_time is not None and (now - last_trade_time).total_seconds() < self.s.cooldown_seconds:
            reasons.append("symbol cooldown active")
        return RiskResult(not reasons, reasons)

    def exit_check(self, *, snapshot: MarketSnapshot, decision: AIDecision | None,
                    position_age_minutes: float) -> RiskResult:
        reasons = []
        hard = False
        pnl = snapshot.unrealized_pnl_pct
        if pnl is not None and pnl <= -self.s.stop_loss_pct:
            hard = True; reasons.append(f"stop-loss {pnl:.2f}%")
        if pnl is not None and pnl >= self.s.take_profit_pct:
            hard = True; reasons.append(f"take-profit {pnl:.2f}%")
        if snapshot.high_water_price and snapshot.bid > 0:
            drawdown = (snapshot.bid / snapshot.high_water_price - 1) * 100
            if drawdown <= -self.s.trailing_stop_pct:
                hard = True; reasons.append(f"trailing-stop {drawdown:.2f}%")
        if position_age_minutes >= self.s.max_hold_minutes:
            hard = True; reasons.append("max hold time")
        ai = decision is not None and decision.action == "SELL" and decision.confidence >= self.s.min_ai_confidence
        if ai: reasons.append("AI SELL signal")
        return RiskResult(hard or ai, reasons)
