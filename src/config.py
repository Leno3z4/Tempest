from __future__ import annotations
import os
from dataclasses import dataclass, field

def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1","true","yes","y","on"}

def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)

def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)

def _csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    return tuple(x.strip().upper() for x in value.split(",") if x.strip())

@dataclass(frozen=True)
class Settings:
    robinhood_api_key: str = field(default_factory=lambda: os.getenv("ROBINHOOD_API_KEY", ""))
    robinhood_private_key: str = field(default_factory=lambda: os.getenv("ROBINHOOD_PRIVATE_KEY", ""))
    live_trading: bool = field(default_factory=lambda: _bool("LIVE_TRADING", False))
    live_confirmation: str = field(default_factory=lambda: os.getenv("LIVE_TRADING_CONFIRM", ""))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    gemini_web_grounding: bool = field(default_factory=lambda: _bool("GEMINI_WEB_GROUNDING", False))
    poll_seconds: int = field(default_factory=lambda: _int("POLL_SECONDS", 20))
    ai_interval_seconds: int = field(default_factory=lambda: _int("AI_INTERVAL_SECONDS", 120))
    history_seconds: int = field(default_factory=lambda: _int("HISTORY_SECONDS", 1800))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/tempest.db"))
    meme_symbols: tuple[str, ...] = field(default_factory=lambda: _csv("MEME_SYMBOLS", ("DOGE","SHIB","BONK","WIF","PEPE","FLOKI","POPCAT","MOODENG","PNUT","PENGU","MEW","TRUMP","CASHCAT","ZORA")))
    max_order_usd: float = field(default_factory=lambda: _float("MAX_ORDER_USD", 0.50))
    max_position_usd: float = field(default_factory=lambda: _float("MAX_POSITION_USD", 0.75))
    max_daily_spend_usd: float = field(default_factory=lambda: _float("MAX_DAILY_SPEND_USD", 1.00))
    max_daily_loss_usd: float = field(default_factory=lambda: _float("MAX_DAILY_LOSS_USD", 0.50))
    max_open_positions: int = field(default_factory=lambda: _int("MAX_OPEN_POSITIONS", 1))
    max_spread_bps: float = field(default_factory=lambda: _float("MAX_SPREAD_BPS", 250.0))
    max_round_trip_cost_pct: float = field(default_factory=lambda: _float("MAX_ROUND_TRIP_COST_PCT", 4.0))
    cooldown_seconds: int = field(default_factory=lambda: _int("COOLDOWN_SECONDS", 300))
    min_ai_confidence: float = field(default_factory=lambda: _float("MIN_AI_CONFIDENCE", 0.72))
    min_signal_score: float = field(default_factory=lambda: _float("MIN_SIGNAL_SCORE", 0.50))
    max_candidates: int = field(default_factory=lambda: _int("MAX_CANDIDATES", 8))
    stop_loss_pct: float = field(default_factory=lambda: _float("STOP_LOSS_PCT", 7.0))
    take_profit_pct: float = field(default_factory=lambda: _float("TAKE_PROFIT_PCT", 12.0))
    trailing_stop_pct: float = field(default_factory=lambda: _float("TRAILING_STOP_PCT", 5.0))
    max_hold_minutes: int = field(default_factory=lambda: _int("MAX_HOLD_MINUTES", 180))

    def validate(self) -> None:
        if self.live_trading and self.live_confirmation != "I_UNDERSTAND":
            raise ValueError("Live trading requires LIVE_TRADING=1 and LIVE_TRADING_CONFIRM=I_UNDERSTAND")
        if self.live_trading and (not self.robinhood_api_key or not self.robinhood_private_key):
            raise ValueError("Live trading requires Robinhood API credentials")
        if self.poll_seconds < 5:
            raise ValueError("POLL_SECONDS must be >= 5")
        if self.max_order_usd <= 0 or self.max_position_usd <= 0:
            raise ValueError("Order and position caps must be positive")
        if self.max_order_usd > self.max_position_usd:
            raise ValueError("MAX_ORDER_USD cannot exceed MAX_POSITION_USD")
        if self.max_daily_spend_usd <= 0 or self.max_daily_loss_usd <= 0:
            raise ValueError("Daily spend and loss caps must be positive")
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be >= 1")
        if not self.meme_symbols:
            raise ValueError("MEME_SYMBOLS cannot be empty")
