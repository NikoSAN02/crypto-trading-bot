"""
Run Loop — Continuous trading bot with sentiment-gated decisions.

Usage:  python run_loop.py --interval 300  (default: 5 minutes)

Sentiment is checked every cycle. Trading is gated by sentiment score:
  - STRONG_BUY/BUY: open positions normally, increased sizing
  - NEUTRAL: only open high-APY funding arb (50%+ APY), reduced sizing
  - SELL: don't open new, consider closing existing
  - STRONG_SELL: close all positions, go to sleep
"""

import sys
import time
from datetime import datetime, timezone

from config import API_KEY, API_SECRET, EXCHANGE_ID, SANDBOX
from main import create_exchange
from strategies.funding_arb import FundingArbEngine
from strategies.sentiment_engine import SentimentEngine
from strategies.volatility_detector import VolatilityDetector
from risk.position_manager import PositionManager
from alerts.telegram_alerts import (
    send_alert, format_funding_opportunity,
    format_position_open, format_position_close, format_risk_report,
)


def run_loop(interval_seconds=300):
    """Main sentiment-gated trading loop."""
    print(f"\n{'='*60}")
    print(f"  SENTIMENT-GATED TRADING LOOP — interval: {interval_seconds}s")
    print(f"{'='*60}\n")

    exchange = create_exchange()
    engine = FundingArbEngine(exchange)
    pm = PositionManager(exchange)
    sentiment = SentimentEngine()
    cycle = 0

    # Track last sentiment alert to avoid spam
    last_signal = None
    last_volatility_scan = 0

    while True:
        cycle += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n--- Cycle {cycle} | {now} ---")

        try:
            # Refresh market data every 10 cycles
            if cycle % 10 == 0:
                exchange.load_markets()
                print("[REFRESH] Markets reloaded")

            # ── Risk Check ──
            report = pm.get_risk_report()
            if not report["safe_to_trade"]:
                print(f"[RISK] Drawdown {report['drawdown_pct']:.1f}% — trading halted")
                send_alert(format_risk_report(report))
                time.sleep(interval_seconds)
                continue

            # ── Sentiment Analysis (every cycle) ──
            signal = sentiment.generate_signal(exchange)
            signal_changed = (last_signal is None or
                              last_signal["signal"] != signal["signal"] or
                              abs(last_signal["score"] - signal["score"]) > 20)

            if signal_changed:
                print(f"[SENTIMENT] {signal['signal']} (score: {signal['score']:+d})")
                for r in signal["reasons"][:3]:
                    print(f"  - {r}")

                # Alert on signal changes
                if last_signal and signal["signal"] != last_signal["signal"]:
                    send_alert(
                        f"Sentiment shift: {last_signal['signal']} -> {signal['signal']}\n"
                        f"Score: {signal['score']:+d}\n"
                        f"FNG: {signal.get('fng', {}).get('value', '?')}"
                    )
                last_signal = signal.copy()
            else:
                fng_val = signal.get("fng", {}).get("value", "?")
                print(f"[SENTIMENT] {signal['signal']} (score: {signal['score']:+d}, FNG: {fng_val})")

            # ── STRONG_SELL: Close Everything ──
            should_close, close_reason = sentiment.should_close_positions(signal)
            if should_close and engine.positions:
                print(f"[SENTIMENT] Force closing: {close_reason}")
                for symbol in list(engine.positions.keys()):
                    result = engine.close_position(symbol, reason=close_reason)
                    if result:
                        send_alert(format_position_close(
                            engine.positions.get(symbol, {}), close_reason))
                time.sleep(interval_seconds)
                continue

            # ── Check Existing Positions ──
            if engine.positions:
                print("[CHECK] Reviewing positions...")
                engine.check_positions()

            # ── Position Gate ──
            should_open, open_reason = sentiment.should_open_funding_arb(signal)
            if not should_open:
                print(f"[GATE] Not opening: {open_reason}")
                status = engine.get_status()
                print(f"[STATUS] {status['positions']} positions | {status['total_trades']} trades")
                time.sleep(interval_seconds)
                continue

            # ── Scan + Trade ──
            available, _ = pm.get_available_balance()
            if available < 20:
                print(f"[FUNDS] Low balance: ${available:.2f}")
                time.sleep(interval_seconds)
                continue

            # Adjust sizing by sentiment
            multiplier = sentiment.get_position_multiplier(signal)
            adjusted_max = engine.max_position_usd * multiplier
            print(f"[GATE] {open_reason} | sizing: {multiplier}x (${adjusted_max:.0f} max)")

            opportunities = engine.scan_opportunities(top_n=3)
            for opp in opportunities:
                if opp["symbol"] in engine.positions:
                    continue
                ok, reason = pm.pre_trade_check(opp["symbol"], adjusted_max)
                if not ok:
                    continue

                original_max = engine.max_position_usd
                engine.max_position_usd = adjusted_max
                pos = engine.open_position(opp, available)
                engine.max_position_usd = original_max

                if pos:
                    send_alert(format_position_open(pos))
                    available -= pos["usd_value"]
                time.sleep(1)

            # ── Volatility Scan (every 6 cycles ~= 30 min) ──
            time_since_vol = time.time() - last_volatility_scan
            if time_since_vol > interval_seconds * 6:
                print("\n[VOLATILITY] Running breakout scan...")
                detector = VolatilityDetector()
                vol_results = detector.scan_all()
                if vol_results["all_setups"]:
                    print(detector.format_setups(vol_results))
                    # Alert on high-confidence setups
                    top = vol_results["all_setups"][:3]
                    alert_lines = ["Volatility setups detected:"]
                    for s in top:
                        alert_lines.append(
                            f"  {s['symbol']}: {s['setup']} "
                            f"(conf: {s['confidence']:.0%})"
                        )
                    send_alert("\n".join(alert_lines))
                last_volatility_scan = time.time()

            # ── Print Status ──
            status = engine.get_status()
            print(f"[STATUS] {status['positions']} positions | {status['total_trades']} trades")

        except Exception as e:
            print(f"[ERROR] Cycle {cycle}: {e}")
            send_alert(f"Bot error: {e}")

        print(f"Next scan in {interval_seconds}s...")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    interval = 300  # 5 minutes default
    if "--interval" in sys.argv:
        idx = sys.argv.index("--interval")
        if idx + 1 < len(sys.argv):
            interval = int(sys.argv[idx + 1])

    run_loop(interval)
