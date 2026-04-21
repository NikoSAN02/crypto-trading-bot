"""
Crypto Trading Bot — Bybit Funding Rate Arbitrage + Sentiment Engine

Usage:
  python main.py              # Scan only (no trading)
  python main.py --trade      # Scan + auto-trade (sentiment-gated)
  python main.py --status     # Check positions
  python main.py --close-all  # Close all positions
  python main.py --sentiment  # Show full sentiment analysis
  python main.py --volatility # Show volatility breakout setups
"""

import ccxt
import sys
import time
import json
from datetime import datetime, timezone

from config import API_KEY, API_SECRET, EXCHANGE_ID, SANDBOX
from strategies.funding_arb import FundingArbEngine
from strategies.sentiment_engine import SentimentEngine
from strategies.volatility_detector import VolatilityDetector
from risk.position_manager import PositionManager
from alerts.telegram_alerts import (
    send_alert, format_funding_opportunity,
    format_position_open, format_position_close, format_risk_report,
)


def create_exchange():
    """Initialize Bybit exchange connection."""
    exchange_class = getattr(ccxt, EXCHANGE_ID)
    exchange = exchange_class({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {"defaultType": "unified"},
        "enableRateLimit": True,
    })

    if SANDBOX:
        exchange.set_sandbox_mode(True)
        print("[!] SANDBOX MODE — no real funds at risk")

    exchange.load_markets()
    return exchange


def sentiment_mode(sentiment):
    """Show full sentiment analysis."""
    signal = sentiment.generate_signal()
    print(sentiment.format_signal(signal))


def volatility_mode():
    """Show volatility breakout setups."""
    detector = VolatilityDetector()
    results = detector.scan_all()
    print(detector.format_setups(results))


def scan_mode(engine, sentiment=None):
    """Scan for opportunities with optional sentiment context."""
    # Get sentiment signal first
    signal = None
    if sentiment:
        signal = sentiment.generate_signal()
        print(sentiment.format_signal(signal))

    opportunities = engine.scan_opportunities(top_n=15)

    if not opportunities:
        print("\nNo opportunities found (min rate: {:.1f}% APY)".format(
            engine.min_annual_rate
        ))
        return

    print(f"\n{'='*60}")
    print(f"  TOP FUNDING RATE OPPORTUNITIES")
    print(f"{'='*60}")
    for i, opp in enumerate(opportunities, 1):
        direction = "SHORT (collect)" if opp["fundingRate"] > 0 else "LONG"
        sentiment_note = ""
        if signal:
            if signal["signal"] in ("STRONG_BUY", "BUY") and opp["annualized"] > 30:
                sentiment_note = " [SENTIMENT: GO]"
            elif signal["signal"] in ("SELL", "STRONG_SELL"):
                sentiment_note = " [SENTIMENT: CAUTION]"
            else:
                sentiment_note = " [SENTIMENT: OK]"
        print(f"\n  {i}. {opp['symbol']}{sentiment_note}")
        print(f"     Rate: {opp['fundingRate']:+.6f}  |  APY: {opp['annualized']:+.1f}%")
        print(f"     Strategy: Buy spot + {direction} perp")
        print(f"     24h Volume: ${opp['volume_24h']:,.0f}")

    print(f"\n{'='*60}")
    if signal and signal["signal"] in ("SELL", "STRONG_SELL"):
        print(f"  ⚠️ Sentiment is {signal['signal']} — consider closing existing positions")
    else:
        print(f"  Run with --trade to auto-execute top opportunities")
    print(f"{'='*60}")


def trade_mode(exchange, engine, pm, sentiment):
    """Sentiment-gated auto-trading."""
    # Always check sentiment first
    signal = sentiment.generate_signal(exchange)
    print(sentiment.format_signal(signal))

    # Pre-check
    report = pm.get_risk_report()
    print(format_risk_report(report))

    if not report["safe_to_trade"]:
        send_alert("Trading halted — drawdown limit exceeded")
        return

    # Check if we should close everything
    should_close, close_reason = sentiment.should_close_positions(signal)
    if should_close and engine.positions:
        print(f"\n[SENTIMENT] Closing all positions: {close_reason}")
        for symbol in list(engine.positions.keys()):
            result = engine.close_position(symbol, reason=close_reason)
            if result:
                send_alert(format_position_close(
                    engine.positions.get(symbol, {}), close_reason))
        return

    # Check existing positions
    if engine.positions:
        print("\n[CHECK] Reviewing existing positions...")
        engine.check_positions()

    # Check sentiment gate for new positions
    should_open, open_reason = sentiment.should_open_funding_arb(signal)
    if not should_open:
        print(f"\n[SENTIMENT GATE] Not opening new positions: {open_reason}")
        return

    print(f"\n[SENTIMENT GATE] Open: {open_reason}")

    # Scan for opportunities
    opportunities = engine.scan_opportunities(top_n=5)
    if not opportunities:
        print("\nNo new opportunities found")
        return

    # Adjust position sizing by sentiment
    multiplier = sentiment.get_position_multiplier(signal)
    adjusted_max = engine.max_position_usd * multiplier
    print(f"  Position multiplier: {multiplier}x (max ${adjusted_max:.0f} per position)")

    available, _ = pm.get_available_balance()
    print(f"  Available for trading: ${available:.2f}")

    # Execute top opportunities
    for opp in opportunities:
        if opp["symbol"] in engine.positions:
            continue

        ok, reason = pm.pre_trade_check(opp["symbol"], adjusted_max)
        if not ok:
            print(f"  [RISK] Skipping {opp['symbol']}: {reason}")
            continue

        # Override max position for the engine temporarily
        original_max = engine.max_position_usd
        engine.max_position_usd = adjusted_max
        pos = engine.open_position(opp, available)
        engine.max_position_usd = original_max

        if pos:
            send_alert(format_position_open(pos))
            available -= pos["usd_value"]

        time.sleep(1)  # Rate limit buffer

    # Final status
    status = engine.get_status()
    print(f"\n[STATUS] {status['positions']} positions open, {status['total_trades']} total trades")


def status_mode(exchange, engine, pm, sentiment=None):
    """Show current bot status with sentiment context."""
    report = pm.get_risk_report()
    print(format_risk_report(report))

    if sentiment:
        signal = sentiment.generate_signal()
        fng = signal.get("fng", {})
        if fng:
            print(f"  Sentiment: {signal['signal']} (FNG: {fng.get('value', '?')})")

    if engine.positions:
        print(f"\nOpen positions: {len(engine.positions)}")
        for sym, pos in engine.positions.items():
            print(f"  {sym}: ${pos['usd_value']:.2f} @ {pos['entry_annualized']:+.1f}% APY")
    else:
        print("\nNo open positions")

    if engine.trade_log:
        print(f"\nTrade history: {len(engine.trade_log)} trades")
        for log in engine.trade_log[-5:]:
            print(f"  {log['timestamp'][:19]} {log['action']} {log['symbol']} ${log['usd_value']:.2f}")


def close_all_mode(exchange, engine):
    """Close all open positions."""
    if not engine.positions:
        print("No positions to close")
        return

    print(f"Closing {len(engine.positions)} positions...")
    for symbol in list(engine.positions.keys()):
        result = engine.close_position(symbol, reason="close_all")
        if result:
            send_alert(format_position_close(engine.positions.get(symbol, {}), "manual close_all"))


def main():
    print("=" * 60)
    print("  CRYPTO TRADING BOT — Sentiment + Funding Arb")
    print(f"  Exchange: {EXCHANGE_ID}  |  Sandbox: {SANDBOX}")
    print("=" * 60)

    # Parse command early (some commands don't need exchange)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"

    # Commands that don't need exchange connection
    if cmd == "--volatility":
        volatility_mode()
        return

    if cmd == "--sentiment":
        sentiment = SentimentEngine()
        sentiment_mode(sentiment)
        return

    # Initialize exchange for other commands
    exchange = create_exchange()
    print(f"Connected to {EXCHANGE_ID} — {len(exchange.markets)} markets loaded")

    engine = FundingArbEngine(exchange)
    pm = PositionManager(exchange)
    sentiment = SentimentEngine()

    if cmd == "--sentiment":
        sentiment_mode(sentiment)
    elif cmd == "--trade":
        trade_mode(exchange, engine, pm, sentiment)
    elif cmd == "--status":
        status_mode(exchange, engine, pm, sentiment)
    elif cmd == "--close-all":
        close_all_mode(exchange, engine)
    elif cmd == "--scan" or cmd == "scan":
        scan_mode(engine, sentiment)
    else:
        print(f"\nUsage:")
        print(f"  python main.py              # Scan opportunities + sentiment")
        print(f"  python main.py --trade      # Sentiment-gated auto-trade")
        print(f"  python main.py --status     # Bot status + sentiment")
        print(f"  python main.py --close-all  # Close all positions")
        print(f"  python main.py --sentiment  # Full sentiment analysis")
        print(f"  python main.py --volatility # Volatility breakout setups")


if __name__ == "__main__":
    main()
