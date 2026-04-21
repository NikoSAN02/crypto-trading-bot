# Crypto Trading Bot

Automated crypto trading bot focused on **funding rate arbitrage** on Bybit.

## How It Works

**Funding Rate Arbitrage** — delta-neutral strategy that profits from funding payments:

1. Find coins with HIGH positive funding rates (shorts get paid every 8h)
2. BUY spot + SHORT perpetual (same amount) → price risk cancels out
3. Collect funding payments (e.g., 15-50% APY)
4. Close when rate drops below threshold

Profit comes from funding, NOT price direction. You win whether the coin goes up or down.

## Quick Start

```bash
pip install ccxt websockets python-dotenv

# Set up API keys
cp .env.example .env
# Edit .env with your Bybit API keys

# Scan for opportunities (no trading)
python main.py

# Auto-trade
python main.py --trade

# Continuous loop (scans every 5 min)
python run_loop.py

# Custom interval (10 minutes)
python run_loop.py --interval 600

# Check bot status
python main.py --status

# Close all positions
python main.py --close-all
```

## ⚠️ Bybit API Setup

1. Go to Bybit → API Management → Create Key
2. Enable: **Spot Trading** + **Unified Trading**
3. ❌ Do NOT enable Withdrawals
4. Start with **Sandbox mode** (`SANDBOX = True` in config.py)

## Project Structure

```
crypto-trading-bot/
├── main.py                    # CLI entry point
├── run_loop.py                # Continuous trading loop
├── config.py                  # Exchange config + .env loader
├── strategies/
│   └── funding_arb.py         # Funding rate arbitrage engine
├── risk/
│   └── position_manager.py    # Position sizing + risk controls
└── alerts/
    └── telegram_alerts.py     # Trade notifications
```

## Risk Controls

- **Max position size**: $100 per coin (configurable)
- **Max total exposure**: $500
- **Max drawdown**: 10% — bot halts if exceeded
- **Reserve**: Keeps 20% balance untouched
- **Min liquidity**: Skips coins with <$1M daily volume

## Roadmap

- [x] Phase 1 — API connection + funding rate scanner
- [x] Phase 2 — Funding rate arbitrage engine
- [x] Phase 3 — Risk management + position sizing
- [ ] Phase 4 — Triangular arbitrage scanner
- [ ] Phase 5 — Telegram bot integration + dashboard
