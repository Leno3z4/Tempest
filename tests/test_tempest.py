import unittest
from src.config import Settings
from src.indicators import moonshot_score, pct_change, rsi, signal_score
from src.models import AIDecision, MarketSnapshot, Position
from src.risk import RiskEngine

class Tests(unittest.TestCase):
    def test_indicators(self):
        self.assertAlmostEqual(pct_change(100, 105), 5)
        self.assertEqual(rsi([100 + i * .1 for i in range(30)]), 100)
        self.assertGreaterEqual(signal_score(rsi_value=60, momentum_5m=1.5, volatility_5m=1, spread_bps=50, samples=30), 0)
        self.assertGreaterEqual(moonshot_score(rsi_value=60, momentum_1m=2, momentum_5m=4, volatility_5m=3, spread_bps=50, samples=30), 0)

    def test_risk_caps(self):
        r = RiskEngine(Settings())
        s = MarketSnapshot(symbol="DOGE", bid=.1, ask=.1005, mid=.10025, spread_bps=49.9, signal_score=.8, moonshot_score=.8)
        d = AIDecision(symbol="DOGE", action="BUY", confidence=.9, suggested_quote_usd=5, thesis="test")
        out = r.entry_check(decision=d, snapshot=s, order_usd=5, buying_power=5, open_positions=0, daily_spend=0, daily_realized_pnl=0, last_trade_time=None, estimated_round_trip_pct=1)
        self.assertFalse(out.allowed)

    def test_daily_loss(self):
        r = RiskEngine(Settings())
        s = MarketSnapshot(symbol="DOGE", bid=.1, ask=.1005, mid=.10025, spread_bps=49.9, signal_score=.8, moonshot_score=.8)
        d = AIDecision(symbol="DOGE", action="BUY", confidence=.9, suggested_quote_usd=.2, thesis="test")
        out = r.entry_check(decision=d, snapshot=s, order_usd=.2, buying_power=2, open_positions=0, daily_spend=0, daily_realized_pnl=-.5, last_trade_time=None, estimated_round_trip_pct=1)
        self.assertFalse(out.allowed)

    def test_moonshot_gate(self):
        r = RiskEngine(Settings())
        s = MarketSnapshot(symbol="DOGE", bid=.1, ask=.1005, mid=.10025, spread_bps=49.9, signal_score=.8, moonshot_score=.8)
        d = AIDecision(symbol="DOGE", action="BUY", trade_style="MOONSHOT", confidence=.9, moonshot_score=.8, suggested_quote_usd=.2, thesis="test")
        out = r.entry_check(decision=d, snapshot=s, order_usd=.2, buying_power=2, open_positions=0, daily_spend=0, daily_realized_pnl=0, last_trade_time=None, estimated_round_trip_pct=1)
        self.assertTrue(out.allowed)

    def test_position_schema(self):
        p=Position(symbol="DOGE",quantity=10,entry_price=.1,entry_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),high_water_price=.1,moonshot=True,next_stage_index=2)
        self.assertTrue(p.moonshot)
        self.assertEqual(p.next_stage_index,2)

if __name__=="__main__": unittest.main()
