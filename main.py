"""
Crypto Trading Bot — Bybit Funding Rate Arbitrage

Usage:
  python main.py              # Scan only (no trading)
  python main.py --trade      # Scan + auto-trade
  python main.py --status     # Check positions
  python main.py --close-all  # Close all positions
"""

import ccxt
import sys
import time
import json
from datetime import datetime, timezone

from config import API_KEY, API_SECRET, EXCHANGE_ID, SANDBOX
from strategies.funding_arb import FundingArbEngine
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


def scan_mode(engine):
    """Just scan for opportunities, don't trade."""
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
        print(f"\n  {i}. {opp['symbol']}")
        print(f"     Rate: {opp['fundingRate']:+.6f}  |  APY: {opp['annualized']:+.1f}%")
        print(f"     Strategy: Buy spot + {direction} perp")
        print(f"     24h Volume: ${opp['volume_24h']:,.0f}")

    print(f"\n{'='*60}")
    print(f"  Run with --trade to auto-execute top opportunities")
    print(f"{'='*60}")


def trade_mode(exchange, engine, pm):
    """Scan + auto-trade the best opportunities."""
    # Pre-check
    report = pm.get_risk_report()
    print(format_risk_report(report))

    if not report["safe_to_trade"]:
        send_alert("🚨 Trading halted — drawdown limit exceeded")
        return

    # Check existing positions
    if engine.positions:
        print("\n[CHECK] Reviewing existing positions...")
        engine.check_positions()

    # Scan for new opportunities
    opportunities = engine.scan_opportunities(top_n=5)
    if not opportunities:
        print("\nNo new opportunities found")
        return

    available, _ = pm.get_available_balance()
    print(f"\nAvailable for trading: ${available:.2f}")

    # Execute top opportunities
    for opp in opportunities:
        if opp["symbol"] in engine.positions:
            continue

        ok, reason = pm.pre_trade_check(opp["symbol"], engine.max_position_usd)
        if not ok:
            print(f"  [RISK] Skipping {opp['symbol']}: {reason}")
            continue

        pos = engine.open_position(opp, available)
        if pos:
            send_alert(format_position_open(pos))
            available -= pos["usd_value"]

        time.sleep(1)  # Rate limit buffer

    # Final status
    status = engine.get_status()
    print(f"\n[STATUS] {status['positions']} positions open, {status['total_trades']} total trades")


def status_mode(exchange, engine, pm):
    """Show current bot status."""
    report = pm.get_risk_report()
    print(format_risk_report(report))

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
    print("  CRYPTO TRADING BOT — Funding Rate Arbitrage")
    print(f"  Exchange: {EXCHANGE_ID}  |  Sandbox: {SANDBOX}")
    print("=" * 60)

    # Initialize
    exchange = create_exchange()
    print(f"Connected to {EXCHANGE_ID} — {len(exchange.markets)} markets loaded")

    engine = FundingArbEngine(exchange)
    pm = PositionManager(exchange)

    # Parse command
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"

    if cmd == "--trade":
        trade_mode(exchange, engine, pm)
    elif cmd == "--status":
        status_mode(exchange, engine, pm)
    elif cmd == "--close-all":
        close_all_mode(exchange, engine)
    elif cmd == "--scan" or cmd == "scan":
        scan_mode(engine)
    else:
        print(f"\nUsage:")
        print(f"  python main.py              # Scan opportunities")
        print(f"  python main.py --trade      # Auto-trade")
        print(f"  python main.py --status     # Bot status")
        print(f"  python main.py --close-all  # Close all positions")


if __name__ == "__main__":
    main()
