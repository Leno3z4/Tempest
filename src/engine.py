from __future__ import annotations
import logging, time
from datetime import datetime, timedelta, timezone
from typing import Any
from .ai import GeminiAdvisor
from .config import Settings
from .indicators import pct_change, rsi, signal_score, volatility_pct
from .models import AIDecision, MarketSnapshot, Position
from .risk import RiskEngine
from .robinhood import RobinhoodCrypto, RobinhoodAPIError, floor_step
from .store import Store

log=logging.getLogger("tempest")

class TempestEngine:
    def __init__(self, settings: Settings):
        settings.validate(); self.s=settings
        self.store=Store(settings.db_path)
        self.rh=RobinhoodCrypto(settings.robinhood_api_key,settings.robinhood_private_key)
        self.ai=GeminiAdvisor(settings.gemini_api_key,settings.gemini_model,settings.gemini_web_grounding)
        self.risk=RiskEngine(settings); self.pairs={}; self.account_number=None
        self.last_ai_run=None; self.cached_ai={}

    @property
    def mode(self): return "LIVE" if self.s.live_trading else "PAPER"

    def bootstrap(self):
        accounts=self.rh.accounts()
        active=[a for a in accounts if str(a.get("status","")).lower()=="active"]
        if not active: raise RobinhoodAPIError("No active Robinhood Crypto account returned")
        self.account_number=str(active[0]["account_number"])
        rows=self.rh.trading_pairs(list(self.s.meme_symbols))
        self.pairs={str(r["asset_code"]).upper():r for r in rows if str(r.get("status","")).lower()=="tradable" and bool(r.get("is_api_tradable",True))}
        log.info("Mode=%s configured=%d api-tradable=%d",self.mode,len(self.s.meme_symbols),len(self.pairs))

    def _holdings(self):
        rows=self.rh.holdings(self.account_number)
        return {str(r["asset_code"]).upper():float(r.get("quantity_available_for_trading") or 0) for r in rows}

    def _buying_power(self):
        return float(self.rh.account(self.account_number).get("buying_power") or 0)

    def _snapshot(self,symbol,quote,holdings):
        row=self.pairs[symbol]; bid,ask=quote["bid"],quote["ask"]; mid=(bid+ask)/2
        spread=(ask-bid)/mid*10000 if mid>0 else 999999
        now=datetime.now(timezone.utc); self.store.add_snapshot(symbol,bid,ask,mid,now)
        prices=self.store.prices(symbol,now-timedelta(seconds=self.s.history_seconds))
        pos=next((p for p in self.store.positions() if p.symbol==symbol),None)
        pnl=((bid/pos.entry_price)-1)*100 if pos else None
        high=max(pos.high_water_price,bid) if pos else None
        if pos and high!=pos.high_water_price: self.store.set_position(pos.model_copy(update={"high_water_price":high}))
        return MarketSnapshot(
            symbol=symbol,bid=bid,ask=ask,mid=mid,spread_bps=spread,
            rsi_14=rsi(prices),momentum_1m_pct=pct_change(prices[-4],mid) if len(prices)>=4 else None,
            momentum_5m_pct=pct_change(prices[-16],mid) if len(prices)>=16 else None,
            volatility_5m_pct=volatility_pct(prices[-16:]) if len(prices)>=16 else None,
            samples=len(prices),holding_qty=holdings.get(symbol,0),entry_price=pos.entry_price if pos else None,
            unrealized_pnl_pct=pnl,high_water_price=high,min_order_amount=float(row.get("min_order_amount") or 0),
            asset_increment=float(row.get("asset_increment") or 0),
            signal_score=signal_score(rsi_value=rsi(prices),momentum_5m=pct_change(prices[-16],mid) if len(prices)>=16 else None,
                                      volatility_5m=volatility_pct(prices[-16:]) if len(prices)>=16 else None,
                                      spread_bps=spread,samples=len(prices)))

    def _ai_review(self,snapshots):
        now=datetime.now(timezone.utc)
        if self.last_ai_run and (now-self.last_ai_run).total_seconds()<self.s.ai_interval_seconds: return
        open_symbols={p.symbol for p in self.store.positions()}
        candidates=[s for s in snapshots if s.signal_score>=self.s.min_signal_score or s.symbol in open_symbols]
        candidates.sort(key=lambda s:(s.symbol in open_symbols,s.signal_score,s.momentum_5m_pct or -999),reverse=True)
        try: result=self.ai.analyze({"mode":self.mode,"constraints":self.s.__dict__,"candidates":[s.model_dump(mode="json") for s in candidates[:self.s.max_candidates]]})
        except Exception:
            log.exception("Gemini analysis failed; no new entries")
            self.last_ai_run=now; return
        self.cached_ai={d.symbol:d for d in result.decisions}
        for d in result.decisions: self.store.record_decision(d.symbol,d.action,d.confidence,d.model_dump(),now)
        self.last_ai_run=now
        log.info("AI: %s",", ".join(f"{d.symbol}:{d.action}:{d.confidence:.2f}" for d in result.decisions) or "no decisions")

    def _estimated_buy(self,snapshot,order_usd):
        qty=floor_step(order_usd/snapshot.ask,snapshot.asset_increment)
        if qty<=0: raise RobinhoodAPIError("quantity rounded to zero")
        est=self.rh.estimated_price(snapshot.symbol+"-USD","ask",qty)
        total=float(est.get("est_total_cost") or order_usd); fee=float(est.get("est_fee") or 0)
        friction=max(0,(total/order_usd-1)*100) if order_usd else 999
        return qty,max(friction,fee/order_usd*100 if order_usd else 999)

    def _buy(self,snapshot,decision,buying_power,daily_spend):
        requested=decision.suggested_quote_usd or self.s.max_order_usd
        amount=min(requested,self.s.max_order_usd,self.s.max_position_usd,buying_power)
        try: qty,friction=self._estimated_buy(snapshot,amount)
        except Exception as e: log.info("BUY %s skipped: %s",snapshot.symbol,e); return
        gate=self.risk.entry_check(decision=decision,snapshot=snapshot,order_usd=amount,buying_power=buying_power,
            open_positions=len(self.store.positions()),daily_spend=daily_spend,
            daily_realized_pnl=self.store.daily_realized_pnl(datetime.now(timezone.utc)),
            last_trade_time=self.store.last_trade_time(snapshot.symbol),
            estimated_round_trip_pct=friction+snapshot.spread_bps/100)
        if not gate.allowed: log.info("BUY blocked %s: %s",snapshot.symbol,"; ".join(gate.reasons)); return
        now=datetime.now(timezone.utc)
        if not self.s.live_trading:
            self.store.record_trade(ts=now,symbol=snapshot.symbol,side="buy",quote_amount=amount,quantity=qty,price=snapshot.ask,
                order_id=f"paper-{int(now.timestamp()*1000)}",state="filled",mode="paper",payload={"ai":decision.model_dump()})
            self.store.set_position(Position(symbol=snapshot.symbol,quantity=qty,entry_price=snapshot.ask,entry_time=now,high_water_price=snapshot.ask))
            log.info("PAPER BUY %s $%.4f",snapshot.symbol,amount); return
        response=self.rh.place_market_order(account_number=self.account_number,symbol=snapshot.symbol+"-USD",side="buy",asset_quantity=qty)
        oid=str(response.get("id")); filled=self.rh.wait_for_fill(self.account_number,oid); state=str(filled.get("state","unknown")).lower()
        fq=float(filled.get("filled_asset_quantity") or qty); price=float(filled.get("average_price") or snapshot.ask)
        self.store.record_trade(ts=now,symbol=snapshot.symbol,side="buy",quote_amount=amount,quantity=fq,price=price,order_id=oid,state=state,mode="live",payload=filled)
        if state=="filled": self.store.set_position(Position(symbol=snapshot.symbol,quantity=fq,entry_price=price,entry_time=now,high_water_price=price))

    def _sell(self,snapshot,decision,reason):
        pos=next((p for p in self.store.positions() if p.symbol==snapshot.symbol),None)
        if not pos: return
        qty=floor_step(min(pos.quantity,snapshot.holding_qty or pos.quantity),snapshot.asset_increment)
        if qty<=0: return
        now=datetime.now(timezone.utc)
        if not self.s.live_trading:
            pnl=(snapshot.bid-pos.entry_price)*qty
            self.store.record_trade(ts=now,symbol=snapshot.symbol,side="sell",quote_amount=qty*snapshot.bid,quantity=qty,price=snapshot.bid,
                order_id=f"paper-{int(now.timestamp()*1000)}",state="filled",mode="paper",realized_pnl=pnl,payload={"reason":reason})
            self.store.delete_position(snapshot.symbol); log.info("PAPER SELL %s pnl=%.4f",snapshot.symbol,pnl); return
        response=self.rh.place_market_order(account_number=self.account_number,symbol=snapshot.symbol+"-USD",side="sell",asset_quantity=qty)
        oid=str(response.get("id")); filled=self.rh.wait_for_fill(self.account_number,oid); state=str(filled.get("state","unknown")).lower()
        fq=float(filled.get("filled_asset_quantity") or qty); price=float(filled.get("average_price") or snapshot.bid)
        pnl=(price-pos.entry_price)*fq if state=="filled" else None
        self.store.record_trade(ts=now,symbol=snapshot.symbol,side="sell",quote_amount=fq*price,quantity=fq,price=price,order_id=oid,state=state,mode="live",realized_pnl=pnl,payload={"reason":reason,**filled})
        if state=="filled": self.store.delete_position(snapshot.symbol)

    def tick(self):
        now=datetime.now(timezone.utc); holdings=self._holdings()
        buying_power=self._buying_power() if self.s.live_trading else max(self.s.max_daily_spend_usd,self.s.max_order_usd)
        symbols=list(self.pairs)
        quotes=self.rh.best_bid_ask([s+"-USD" for s in symbols])
        normalized={k.split("-")[0]:v for k,v in quotes.items()}
        snapshots=[self._snapshot(s,normalized[s],holdings) for s in symbols if s in normalized and normalized[s]["bid"]>0 and normalized[s]["ask"]>0]
        self._ai_review(snapshots)
        for s in snapshots:
            pos=next((p for p in self.store.positions() if p.symbol==s.symbol),None)
            if pos:
                age=(now-pos.entry_time).total_seconds()/60
                result=self.risk.exit_check(snapshot=s,decision=self.cached_ai.get(s.symbol),position_age_minutes=age)
                if result.allowed: self._sell(s,self.cached_ai.get(s.symbol),"; ".join(result.reasons))
        if len(self.store.positions())>=self.s.max_open_positions: return
        daily=self.store.daily_spend(now)
        for s in sorted(snapshots,key=lambda x:x.signal_score,reverse=True):
            d=self.cached_ai.get(s.symbol)
            if d and d.action=="BUY" and s.holding_qty<=0 and s.signal_score>=self.s.min_signal_score:
                self._buy(s,d,buying_power,daily)
                if self.store.positions(): break
        self.store.prune_snapshots(now-timedelta(seconds=self.s.history_seconds*2))

    def run(self):
        self.bootstrap(); log.info("Tempest running in %s mode",self.mode)
        while True:
            try: self.tick()
            except KeyboardInterrupt: raise
            except Exception: log.exception("tick failed; no order submitted on failed cycle")
            time.sleep(self.s.poll_seconds)
