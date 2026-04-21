# Crypto Trading Bot

Automated crypto trading bot focused on **funding rate arbitrage** and low-risk strategies on Bybit.

## Strategies

1. **Funding Rate Arbitrage** (Phase 2+) — Spot buy + perp short, collect funding payments
2. **Triangular Arbitrage** (Phase 4+) — Single-exchange cross-pair inefficiencies

## Setup

```bash
pip install ccxt websockets python-dotenv
# Edit config.py with your Bybit API keys
# Start with sandbox mode: SANDBOX = True
python main.py
```

## Phases

- [x] Phase 1 — API connection, balance check, funding rate scanner
- [ ] Phase 2 — Funding rate arbitrage engine (auto-hedge)
- [ ] Phase 3 — Risk management + position sizing
- [ ] Phase 4 — Triangular arbitrage scanner
- [ ] Phase 5 — Dashboard + Telegram alerts
