# Tempest — AI-assisted Robinhood meme trading bot

Tempest is a paper-first meme-crypto trading engine for Robinhood's official Crypto Trading API with Gemini as an analyst. Gemini proposes BUY/HOLD/SELL decisions; deterministic risk controls decide whether an order can actually be sent.

## Safety model

- Paper mode is the default.
- Live trading requires both `LIVE_TRADING=1` and `LIVE_TRADING_CONFIRM=I_UNDERSTAND`.
- One open position by default.
- $0.50 max order, $0.75 max position, $1.00 daily spend cap, $0.50 daily realized-loss cap.
- Spread, estimated round-trip friction, broker minimum order amount, cooldown, and order-size checks run before execution.
- Hard stop-loss, take-profit, trailing stop, and max-hold exits operate without Gemini.
- Every AI decision and filled trade is journaled in SQLite.

These are engineering safeguards, not claims about profitable thresholds.

## Current integration

Tempest targets Robinhood's **Crypto Trading API**, not Robinhood Chain. Robinhood's current API exposes v2 trading-pair metadata, holdings, best bid/ask, estimated execution prices, orders, and order placement. API authentication uses `x-api-key`, `x-signature`, and `x-timestamp`.

The startup scanner asks Robinhood which configured meme symbols are actually API-tradable instead of assuming availability. Current public availability includes meme-style assets such as DOGE, SHIB, BONK, WIF, PEPE, FLOKI, POPCAT, MOODENG, PNUT, PENGU, MEW, TRUMP, CASHCAT, and ZORA; regional and platform availability can change.

Robinhood says crypto purchases can start at $1 in supported consumer flows, but the API's per-pair minimum order amount is authoritative for automation. With a $2 bankroll, spread, fees, and slippage can dominate, so Tempest refuses trades that violate the configured friction gate or the broker minimum.

## Gemini

The bot uses the current Google GenAI Python SDK and defaults to `gemini-3.8-flash`. Structured JSON output is enforced with a Pydantic schema. Optional Google Search grounding can add current public evidence to the analysis; it is disabled by default.

Gemini is intentionally **not** the execution authority. A model can return an explanation and proposed action, but it cannot override the risk engine.

## Reference research

The design was informed by current open-source patterns from:

- Chainstack's pump.fun/letsbonk trading bot — fast event-driven market listeners, configurable entry/exit filters, retries, rate limiting, and regression checks.
- Drakkar-Software/OctoBot — paper trading, backtesting, modular strategies, AI connectors, social signals, and journaling.
- nirholas/robinhood-trading-bot and nirholas/robinhood-chain-trading-bot — paper-first operation, decision journals, fail-closed risk gates, hard caps, kill switches, and LLM-assisted strategy layers.
- rohitsingh-iitd/robinhood-mcp-server — Robinhood Crypto REST/WebSocket separation, authentication, rate limiting, and trading endpoints.
- siropkin/robinhood-ai-trading-bot — AI-assisted decision loops and demo/manual/auto modes.

Robinhood Chain repositories are treated as a separate integration family because they execute on a blockchain rather than Robinhood's brokerage Crypto Trading API.

## Setup

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# macOS/Linux
# source .venv/bin/activate
pip install -r requirements.txt

# copy the example environment
copy .env.example .env  # Windows
# cp .env.example .env  # macOS/Linux
```

Fill in `.env` with Robinhood Crypto API credentials and a Gemini API key.

Run one paper cycle:

```bash
python -m src.main --once
```

Run continuously:

```bash
python -m src.main
```

Live mode is intentionally off until you explicitly enable both live settings.

## What the engine does

1. Discovers API-tradable configured meme assets.
2. Reads holdings and live bid/ask quotes.
3. Maintains 30-minute market history in SQLite.
4. Calculates momentum, RSI, volatility, spread, and a bounded signal score.
5. Sends the strongest candidates plus any open positions to Gemini.
6. Applies deterministic risk gates to every proposed entry.
7. Executes a paper fill or Robinhood market order.
8. Monitors every open position continuously for hard exits and AI exits.
9. Records decisions, fills, and realized P&L for post-trade analysis.

## Important limitation

A $2 bankroll is suitable for testing the software, not for assuming a meaningful or stable trading edge. The bot is designed to say **no trade** frequently when transaction friction, minimum order size, uncertainty, or risk limits make an entry unsuitable.
