"""
Run Loop — Continuous trading bot that scans and trades every N minutes.

Usage:  python run_loop.py --interval 300  (default: 5 minutes)
"""

import sys
import time
from datetime import datetime, timezone

from config import API_KEY, API_SECRET, EXCHANGE_ID, SANDBOX
from main import create_exchange
from strategies.funding_arb import FundingArbEngine
from risk.position_manager import PositionManager
from alerts.telegram_alerts import (
    send_alert, format_funding_opportunity,
    format_position_open, format_position_close, format_risk_report,
)


def run_loop(interval_seconds=300):
    """Main trading loop."""
    print(f"\n{'='*60}")
    print(f"  TRADING LOOP — interval: {interval_seconds}s")
    print(f"{'='*60}\n")

    exchange = create_exchange()
    engine = FundingArbEngine(exchange)
    pm = PositionManager(exchange)
    cycle = 0

    while True:
        cycle += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n--- Cycle {cycle} | {now} ---")

        try:
            # Refresh market data every 10 cycles
            if cycle % 10 == 0:
                exchange.load_markets()

            # Risk check
            report = pm.get_risk_report()
            if not report["safe_to_trade"]:
                print(f"[RISK] Drawdown {report['drawdown_pct']:.1f}% — trading halted")
                send_alert(format_risk_report(report))
                time.sleep(interval_seconds)
                continue

            # Check existing positions
            if engine.positions:
                engine.check_positions()

            # Scan + trade
            available, _ = pm.get_available_balance()
            if available > 20:  # Only scan if we have funds
                opportunities = engine.scan_opportunities(top_n=3)
                for opp in opportunities:
                    if opp["symbol"] in engine.positions:
                        continue
                    ok, reason = pm.pre_trade_check(opp["symbol"], engine.max_position_usd)
                    if not ok:
                        continue
                    pos = engine.open_position(opp, available)
                    if pos:
                        send_alert(format_position_open(pos))
                    time.sleep(1)

            # Print status
            status = engine.get_status()
            print(f"[STATUS] {status['positions']} positions | {status['total_trades']} trades")

        except Exception as e:
            print(f"[ERROR] Cycle {cycle}: {e}")
            send_alert(f"⚠️ Bot error: {e}")

        print(f"Next scan in {interval_seconds}s...")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    interval = 300  # 5 minutes default
    if "--interval" in sys.argv:
        idx = sys.argv.index("--interval")
        if idx + 1 < len(sys.argv):
            interval = int(sys.argv[idx + 1])

    run_loop(interval)
