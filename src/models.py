from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field

Action = Literal["BUY", "HOLD", "SELL"]
TradeStyle = Literal["NORMAL", "MOONSHOT"]

class MarketSnapshot(BaseModel):
    symbol: str
    name: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    bid: float
    ask: float
    mid: float
    spread_bps: float
    rsi_14: float | None = None
    momentum_1m_pct: float | None = None
    momentum_5m_pct: float | None = None
    volatility_5m_pct: float | None = None
    samples: int = 0
    holding_qty: float = 0.0
    entry_price: float | None = None
    unrealized_pnl_pct: float | None = None
    high_water_price: float | None = None
    min_order_amount: float = 0.0
    asset_increment: float = 0.0
    signal_score: float = 0.0
    moonshot_score: float = 0.0

class AIDecision(BaseModel):
    symbol: str
    action: Action
    trade_style: TradeStyle = "NORMAL"
    confidence: float = Field(ge=0.0, le=1.0)
    moonshot_score: float = Field(default=0.0, ge=0.0, le=1.0)
    suggested_quote_usd: float = Field(default=0.0, ge=0.0)
    thesis: str = Field(min_length=1, max_length=1200)
    risk_flags: list[str] = Field(default_factory=list, max_length=12)
    catalysts: list[str] = Field(default_factory=list, max_length=12)
    invalidation: str = Field(default="", max_length=600)
    upside_case_multiple: float = Field(default=1.0, ge=1.0, le=1000.0)

class AIBatchDecision(BaseModel):
    decisions: list[AIDecision] = Field(default_factory=list, max_length=20)

class Position(BaseModel):
    symbol: str
    quantity: float
    entry_price: float
    entry_time: datetime
    high_water_price: float
    moonshot: bool = False
    next_stage_index: int = 0
