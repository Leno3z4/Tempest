from __future__ import annotations
import logging, time
from datetime import datetime, timedelta, timezone
from .ai import GeminiAdvisor
from .config import Settings
from .indicators import moonshot_score, pct_change, rsi, signal_score, volatility_pct
from .models import AIDecision, MarketSnapshot, Position
from .risk import RiskEngine
from .robinhood import RobinhoodCrypto, RobinhoodAPIError, floor_step
from .store import Store

log = logging.getLogger("tempest")

class TempestEngine:
    def __init__(self, settings: Settings):
        settings.validate()
        self.s = settings
        self.store = Store(settings.db_path)
        self.rh = RobinhoodCrypto(settings.robinhood_api_key, settings.robinhood_private_key)
        self.ai = GeminiAdvisor(settings.gemini_api_key, settings.gemini_model, settings.gemini_web_grounding)
        self.risk = RiskEngine(settings)
        self.pairs: dict[str, dict] = {}
        self.account_number: str | None = None
        self.last_ai_run: datetime | None = None
        self.cached_ai: dict[str, AIDecision] = {}

    @property
    def mode(self) -> str:
        return "LIVE" if self.s.live_trading else "PAPER"

    def bootstrap(self) -> None:
        accounts = self.rh.accounts()
        active = [a for a in accounts if str(a.get("status", "")).lower() == "active"]
        if not active:
            raise RobinhoodAPIError("No active Robinhood Crypto account returned")
        self.account_number = str(active[0]["account_number"])
        rows = self.rh.trading_pairs([f"{s}-USD" for s in self.s.meme_symbols])
        self.pairs = {
            str(r["asset_code"]).upper(): r
            for r in rows
            if str(r.get("status", "")).lower() == "tradable"
            and bool(r.get("is_api_tradable", False))
        }
        log.info("Mode=%s configured=%d api-tradable=%d", self.mode, len(self.s.meme_symbols), len(self.pairs))
        if not self.pairs:
            raise RobinhoodAPIError("None of the configured meme symbols are API-tradable")

    def _holdings(self) -> dict[str, float]:
        rows = self.rh.holdings(self.account_number)
        return {
            str(r["asset_code"]).upper(): float(r.get("quantity_available_for_trading") or 0)
            for r in rows
        }

    def _buying_power(self) -> float:
        return float(self.rh.account(self.account_number).get("buying_power") or 0)

    def _snapshot(self, symbol: str, quote: dict[str, float], holdings: dict[str, float]) -> MarketSnapshot:
        row = self.pairs[symbol]
        bid, ask = quote["bid"], quote["ask"]
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 10000 if mid > 0 else 999999
        now = datetime.now(timezone.utc)
        self.store.add_snapshot(symbol, bid, ask, mid, now)
        prices = self.store.prices(symbol, now - timedelta(seconds=self.s.history_seconds))
        pos = next((p for p in self.store.positions() if p.symbol == symbol), None)
        pnl = ((bid / pos.entry_price) - 1) * 100 if pos else None
        high = max(pos.high_water_price, bid) if pos else None
        if pos and high != pos.high_water_price:
            self.store.update_position(symbol, high_water_price=high)

        rsi_value = rsi(prices)
        m1 = pct_change(prices[-4], mid) if len(prices) >= 4 else None
        m5 = pct_change(prices[-16], mid) if len(prices) >= 16 else None
        vol = volatility_pct(prices[-16:]) if len(prices) >= 16 else None
        return MarketSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            mid=mid,
            spread_bps=spread,
            rsi_14=rsi_value,
            momentum_1m_pct=m1,
            momentum_5m_pct=m5,
            volatility_5m_pct=vol,
            samples=len(prices),
            holding_qty=holdings.get(symbol, 0),
            entry_price=pos.entry_price if pos else None,
            unrealized_pnl_pct=pnl,
            high_water_price=high,
            min_order_amount=float(row.get("min_order_amount") or 0),
            asset_increment=float(row.get("asset_increment") or 0),
            max_order_size=float(row.get("max_order_size") or 0),
            signal_score=signal_score(
                rsi_value=rsi_value,
                momentum_5m=m5,
                volatility_5m=vol,
                spread_bps=spread,
                samples=len(prices),
            ),
            moonshot_score=moonshot_score(
                rsi_value=rsi_value,
                momentum_1m=m1,
                momentum_5m=m5,
                volatility_5m=vol,
                spread_bps=spread,
                samples=len(prices),
            ),
        )

    def _ai_review(self, snapshots: list[MarketSnapshot]) -> None:
        now = datetime.now(timezone.utc)
        if self.last_ai_run and (now - self.last_ai_run).total_seconds() < self.s.ai_interval_seconds:
            return
        open_symbols = {p.symbol for p in self.store.positions()}
        candidates = [
            s for s in snapshots
            if s.signal_score >= self.s.min_signal_score
            or s.moonshot_score >= self.s.moonshot_min_score
            or s.symbol in open_symbols
        ]
        candidates.sort(
            key=lambda s: (s.symbol in open_symbols, s.moonshot_score, s.signal_score, s.momentum_5m_pct or -999),
            reverse=True,
        )
        constraints = {
            "max_order_usd": self.s.max_order_usd,
            "max_position_usd": self.s.max_position_usd,
            "max_daily_spend_usd": self.s.max_daily_spend_usd,
            "max_open_positions": self.s.max_open_positions,
            "stop_loss_pct": self.s.stop_loss_pct,
            "take_profit_pct": self.s.take_profit_pct,
            "trailing_stop_pct": self.s.trailing_stop_pct,
            "max_hold_minutes": self.s.max_hold_minutes,
            "moonshot_target_multiple": self.s.moonshot_target_multiple,
            "moonshot_stop_loss_pct": self.s.moonshot_stop_loss_pct,
            "moonshot_trailing_stop_pct": self.s.moonshot_trailing_stop_pct,
        }
        try:
            result = self.ai.analyze({
                "mode": self.mode,
                "constraints": constraints,
                "candidates": [s.model_dump(mode="json") for s in candidates[:self.s.max_candidates]],
            })
        except Exception:
            log.exception("Gemini analysis failed; no new entries this cycle")
            self.last_ai_run = now
            return
        self.cached_ai = {d.symbol: d for d in result.decisions}
        for d in result.decisions:
            self.store.record_decision(d.symbol, d.action, d.confidence, d.model_dump(), now)
        self.last_ai_run = now
        log.info(
            "AI: %s",
            ", ".join(f"{d.symbol}:{d.action}:{d.trade_style}:{d.confidence:.2f}" for d in result.decisions)
            or "no decisions",
        )

    def _estimated_buy(self, snapshot: MarketSnapshot, order_usd: float) -> tuple[float, float, float]:
        if snapshot.ask <= 0:
            raise RobinhoodAPIError("invalid ask")
        qty = floor_step(order_usd / snapshot.ask, snapshot.asset_increment)
        if snapshot.max_order_size > 0:
            qty = floor_step(min(qty, snapshot.max_order_size), snapshot.asset_increment)
        if qty <= 0:
            raise RobinhoodAPIError("quantity rounded to zero")
        est = self.rh.estimated_price(f"{snapshot.symbol}-USD", "ask", qty)
        est_total = float(est.get("est_total_cost") or 0)
        est_fee = float(est.get("est_fee") or 0)
        if est_total <= 0:
            est_total = qty * float(est.get("ask") or snapshot.ask) + est_fee
        friction = max(0.0, (est_total / order_usd - 1) * 100) if order_usd else 999
        return qty, est_total, max(friction, est_fee / order_usd * 100 if order_usd else 999)

    def _buy(self, snapshot: MarketSnapshot, decision: AIDecision, buying_power: float, daily_spend: float) -> bool:
        requested = decision.suggested_quote_usd or self.s.max_order_usd
        amount = min(requested, self.s.max_order_usd, self.s.max_position_usd, buying_power)
        try:
            qty, estimated_total, friction = self._estimated_buy(snapshot, amount)
        except Exception as exc:
            log.info("BUY %s skipped: %s", snapshot.symbol, exc)
            return False
        gate = self.risk.entry_check(
            decision=decision,
            snapshot=snapshot,
            order_usd=estimated_total,
            buying_power=buying_power,
            open_positions=len(self.store.positions()),
            daily_spend=daily_spend,
            daily_realized_pnl=self.store.daily_realized_pnl(datetime.now(timezone.utc)),
            last_trade_time=self.store.last_trade_time(snapshot.symbol),
            estimated_round_trip_pct=friction + snapshot.spread_bps / 100,
        )
        if not gate.allowed:
            log.info("BUY blocked %s: %s", snapshot.symbol, "; ".join(gate.reasons))
            return False

        now = datetime.now(timezone.utc)
        moonshot = decision.trade_style == "MOONSHOT"
        if not self.s.live_trading:
            self.store.record_trade(
                ts=now, symbol=snapshot.symbol, side="buy", quote_amount=estimated_total, quantity=qty,
                price=snapshot.ask, order_id=f"paper-{int(now.timestamp()*1000)}", state="filled",
                mode="paper", payload={"ai": decision.model_dump(), "moonshot": moonshot},
            )
            self.store.set_position(Position(
                symbol=snapshot.symbol, quantity=qty, entry_price=snapshot.ask, entry_time=now,
                high_water_price=snapshot.ask, moonshot=moonshot, next_stage_index=0,
            ))
            log.info("PAPER BUY %s $%.4f style=%s", snapshot.symbol, estimated_total, decision.trade_style)
            return True

        if not self.account_number:
            raise RobinhoodAPIError("Account is not initialized")
        response = self.rh.place_market_order(
            account_number=self.account_number, symbol=f"{snapshot.symbol}-USD", side="buy", asset_quantity=qty
        )
        oid = str(response.get("id"))
        filled = self.rh.wait_for_fill(self.account_number, oid)
        state = str(filled.get("state", "unknown")).lower()
        fq = float(filled.get("filled_asset_quantity") or qty)
        price = float(filled.get("average_price") or snapshot.ask)
        actual_quote = fq * price + float(filled.get("fee_charged") or 0)
        self.store.record_trade(
            ts=now, symbol=snapshot.symbol, side="buy", quote_amount=actual_quote, quantity=fq,
            price=price, order_id=oid, state=state, mode="live",
            payload={"ai": decision.model_dump(), **filled},
        )
        if state == "filled":
            self.store.set_position(Position(
                symbol=snapshot.symbol, quantity=fq, entry_price=price, entry_time=now,
                high_water_price=price, moonshot=moonshot, next_stage_index=0,
            ))
            return True
        return False

    def _sell_quantity(self, snapshot: MarketSnapshot, position: Position, quantity: float, reason: str, advance_stage: bool = False) -> bool:
        qty = floor_step(min(quantity, snapshot.holding_qty or quantity), snapshot.asset_increment)
        if snapshot.max_order_size > 0:
            qty = floor_step(min(qty, snapshot.max_order_size), snapshot.asset_increment)
        if qty <= 0:
            log.info("SELL %s skipped: zero quantity after increment rounding", snapshot.symbol)
            return False
        try:
            estimate = self.rh.estimated_price(f"{snapshot.symbol}-USD", "bid", qty)
            estimated_credit = float(estimate.get("est_total_credit") or 0)
            if estimated_credit > 0 and snapshot.min_order_amount > estimated_credit:
                log.info("SELL %s skipped: estimated credit $%.4f below broker minimum $%.4f", snapshot.symbol, estimated_credit, snapshot.min_order_amount)
                return False
        except Exception as exc:
            log.info("SELL %s preflight skipped: %s", snapshot.symbol, exc)
            return False
        now = datetime.now(timezone.utc)

        if not self.s.live_trading:
            pnl = (snapshot.bid - position.entry_price) * qty
            self.store.record_trade(
                ts=now, symbol=snapshot.symbol, side="sell", quote_amount=qty * snapshot.bid, quantity=qty,
                price=snapshot.bid, order_id=f"paper-{int(now.timestamp()*1000)}", state="filled",
                mode="paper", realized_pnl=pnl, payload={"reason": reason},
            )
            remaining = max(0.0, position.quantity - qty)
            if remaining <= max(snapshot.asset_increment, 1e-15):
                self.store.delete_position(snapshot.symbol)
            else:
                self.store.update_position(
                    snapshot.symbol,
                    quantity=remaining,
                    next_stage_index=position.next_stage_index + (1 if advance_stage else 0),
                    high_water_price=snapshot.high_water_price or position.high_water_price,
                )
            log.info("PAPER SELL %s qty=%.12g pnl=%.6f reason=%s", snapshot.symbol, qty, pnl, reason)
            return True

        if not self.account_number:
            raise RobinhoodAPIError("Account is not initialized")
        response = self.rh.place_market_order(
            account_number=self.account_number, symbol=f"{snapshot.symbol}-USD", side="sell", asset_quantity=qty
        )
        oid = str(response.get("id"))
        filled = self.rh.wait_for_fill(self.account_number, oid)
        state = str(filled.get("state", "unknown")).lower()
        fq = float(filled.get("filled_asset_quantity") or qty)
        price = float(filled.get("average_price") or snapshot.bid)
        pnl = (price - position.entry_price) * fq if state == "filled" else None
        self.store.record_trade(
            ts=now, symbol=snapshot.symbol, side="sell", quote_amount=fq * price, quantity=fq,
            price=price, order_id=oid, state=state, mode="live", realized_pnl=pnl,
            payload={"reason": reason, **filled},
        )
        if state == "filled":
            remaining = max(0.0, position.quantity - fq)
            if remaining <= max(snapshot.asset_increment, 1e-15):
                self.store.delete_position(snapshot.symbol)
            else:
                self.store.update_position(
                    snapshot.symbol,
                    quantity=remaining,
                    next_stage_index=position.next_stage_index + (1 if advance_stage else 0),
                    high_water_price=snapshot.high_water_price or position.high_water_price,
                )
            return True
        return False

    def _manage_moonshot(self, snapshot: MarketSnapshot, position: Position) -> bool:
        multiple = snapshot.bid / position.entry_price if position.entry_price > 0 else 0
        stages = self.s.moonshot_stage_multiples
        fractions = self.s.moonshot_stage_fractions
        idx = position.next_stage_index
        changed = False
        while idx < len(stages) and multiple >= stages[idx]:
            current = next((p for p in self.store.positions() if p.symbol == snapshot.symbol), None)
            if current is None:
                break
            fraction = fractions[idx]
            qty = current.quantity if idx == len(stages) - 1 else current.quantity * fraction
            reason = f"moonshot stage {idx + 1}: {multiple:.2f}x >= {stages[idx]:.2f}x"
            if not self._sell_quantity(snapshot, current, qty, reason, advance_stage=True):
                break
            changed = True
            idx += 1
        return changed

    def _manage_position(self, snapshot: MarketSnapshot, position: Position) -> bool:
        if position.moonshot and self._manage_moonshot(snapshot, position):
            return True
        age = (datetime.now(timezone.utc) - position.entry_time).total_seconds() / 60
        result = self.risk.exit_check(
            snapshot=snapshot,
            decision=self.cached_ai.get(snapshot.symbol),
            position_age_minutes=age,
            moonshot=position.moonshot,
        )
        if result.allowed:
            return self._sell_quantity(snapshot, position, position.quantity, "; ".join(result.reasons))
        return False

    def tick(self) -> None:
        now = datetime.now(timezone.utc)
        holdings = self._holdings()
        buying_power = self._buying_power() if self.s.live_trading else max(self.s.max_daily_spend_usd, self.s.max_order_usd)
        symbols = list(self.pairs)
        quotes = self.rh.best_bid_ask([f"{s}-USD" for s in symbols])
        normalized = {key.rsplit("-", 1)[0]: value for key, value in quotes.items()}
        snapshots = [
            self._snapshot(s, normalized[s], holdings)
            for s in symbols
            if s in normalized and normalized[s]["bid"] > 0 and normalized[s]["ask"] > 0
        ]

        self._ai_review(snapshots)
        for snapshot in snapshots:
            position = next((p for p in self.store.positions() if p.symbol == snapshot.symbol), None)
            if position:
                self._manage_position(snapshot, position)

        if len(self.store.positions()) >= self.s.max_open_positions:
            return

        daily = self.store.daily_spend(now)
        candidates = sorted(
            snapshots,
            key=lambda x: (x.moonshot_score if self.s.moonshot_enabled else 0, x.signal_score),
            reverse=True,
        )
        for snapshot in candidates:
            decision = self.cached_ai.get(snapshot.symbol)
            if not decision or decision.action != "BUY" or snapshot.holding_qty > 0:
                continue
            if snapshot.signal_score < self.s.min_signal_score and not (
                self.s.moonshot_enabled and snapshot.moonshot_score >= self.s.moonshot_min_score
                and decision.trade_style == "MOONSHOT"
            ):
                continue
            if self._buy(snapshot, decision, buying_power, daily):
                break

        self.store.prune_snapshots(now - timedelta(seconds=self.s.history_seconds * 2))

    def run(self) -> None:
        self.bootstrap()
        log.info("Tempest running in %s mode", self.mode)
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("tick failed; no order should be assumed from a failed cycle")
            time.sleep(self.s.poll_seconds)
