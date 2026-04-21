"""
Paper Trading Simulator — Live market data, fake money.

Simulates the full bot with real market data but no exchange connection.
Perfect for testing strategies before going live.

Usage:
  python paper_trader.py                # One-shot scan + paper trade
  python paper_trader.py --live         # Continuous paper trading (5 min interval)
  python paper_trader.py --live 60      # Continuous, 1 min interval
  python paper_trader.py --backtest 7   # Backtest last 7 days
"""

import json
import sys
import time
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from strategies.enhanced_strategies import (
    RateTracker, FundingTimer, should_enter_enhanced, should_exit_enhanced,
    fetch_cross_exchange_rates
)


class PaperTrader:
    """Simulated trading with real market data."""

    def __init__(self, starting_balance=200):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.positions = {}  # {symbol: {amount, entry_price, side, entry_time, ...}}
        self.trade_log = []
        self.balance_history = [{"time": datetime.now(timezone.utc).isoformat(), "balance": starting_balance}]
        self.state_file = os.path.expanduser("~/projects/crypto-trading-bot/paper_state.json")

        # Enhanced features
        self.rate_tracker = RateTracker(max_history=12)
        self.funding_timer = FundingTimer()

        # Leverage config (safest approach: 3x)
        self.leverage = 3
        self.liquidation_buffer_pct = 50  # Close if price moves 50% toward liquidation
        self.max_leverage_drawdown_pct = 30  # Hard stop: close all if equity drops 30%

        # Position config (wider net)
        self.max_position_usd = 60  # Larger positions with leverage
        self.max_positions = 5  # More diversification
        self.reserve_pct = 15  # Less idle cash, more deployed
        self.slippage_pct = 0.1
        self.fee_pct = 0.06

        # Risk tracking
        self.peak_balance = starting_balance
        self.total_funding_collected = 0

    def _fetch_json(self, url, timeout=10):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paper-trader/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            return None

    def get_available(self):
        """Available balance minus reserve and open position value (accounting for leverage)."""
        # With leverage, our margin is position_value / leverage
        margin_used = sum(p["usd_value"] / self.leverage for p in self.positions.values())
        available = self.balance - margin_used
        reserved = available * (self.reserve_pct / 100)
        return max(0, available - reserved)

    def get_total_exposure(self):
        """Total notional exposure across all positions."""
        return sum(p["usd_value"] for p in self.positions.values())

    def get_equity(self):
        """Current equity = balance + unrealized PnL."""
        return self.balance + self.total_funding_collected

    # ─── Data Fetching ─────────────────────────────────────────

    def fetch_funding_rates(self):
        """Get funding rates from Bybit public API."""
        data = self._fetch_json("https://api.bybit.com/v5/market/tickers?category=linear")
        if not data or not data.get("result", {}).get("list"):
            return []

        results = data["result"]["list"]
        active = [r for r in results
                  if r.get("turnover24h") and float(r["turnover24h"]) > 10_000_000]

        rates = []
        for r in active:
            rate = float(r.get("fundingRate", 0))
            apy = rate * 3 * 365 * 100
            rates.append({
                "symbol": r["symbol"].replace("USDT", ""),
                "funding_rate": rate,
                "apy": round(apy, 1),
                "turnover": float(r.get("turnover24h", 0)),
                "price": float(r.get("lastPrice", 0)),
                "change_24h": round(float(r.get("price24hPcnt", 0)) * 100, 1),
            })

        rates.sort(key=lambda x: x["apy"], reverse=True)
        return rates

    def fetch_fear_greed(self):
        """Get Fear and Greed Index."""
        data = self._fetch_json("https://api.alternative.me/fng/?limit=7")
        if not data or "data" not in data:
            return None
        entries = data["data"]
        values = [int(e["value"]) for e in entries]
        return {
            "value": int(entries[0]["value"]),
            "classification": entries[0]["value_classification"],
            "avg_7d": round(sum(values) / len(values), 1),
            "trend": "improving" if int(entries[0]["value"]) > sum(values) / len(values) else "deteriorating",
        }

    def fetch_market_prices(self):
        """Get current prices for coins we might trade."""
        url = ("https://api.coingecko.com/api/v3/coins/markets?"
               "vs_currency=usd&order=volume_desc&per_page=30&page=1&sparkline=false")
        data = self._fetch_json(url)
        if not data:
            return {}
        prices = {}
        for c in data:
            prices[c["symbol"].upper()] = c["current_price"]
        return prices

    # ─── Trading Logic ─────────────────────────────────────────

    def generate_signal(self):
        """Sentiment signal from live data."""
        fng = self.fetch_fear_greed()
        funding = self.fetch_funding_rates()

        score = 0
        reasons = []

        if fng:
            v = fng["value"]
            if v <= 25:
                score += 40
                reasons.append(f"Extreme Fear ({v}) — buy signal")
            elif v <= 40:
                score += 20
                reasons.append(f"Fear ({v}) — accumulating")
            elif v >= 75:
                score -= 40
                reasons.append(f"Extreme Greed ({v}) — take profit")
            elif v >= 60:
                score -= 20
                reasons.append(f"Greed ({v}) — cautious")

            if fng["trend"] == "improving" and fng["avg_7d"] < 30:
                score += 15
                reasons.append("Recovering from extreme fear — bullish divergence")

        # Funding bias
        top10 = funding[:10] if funding else []
        if top10:
            avg_apy = sum(r["apy"] for r in top10) / len(top10)
            if avg_apy < -50:
                score += 30
                reasons.append(f"Bearish overcrowded (avg {avg_apy:.0f}% APY) — squeeze likely")
            elif avg_apy < -10:
                score += 15
                reasons.append(f"Bearish positioning (avg {avg_apy:.0f}% APY)")
            elif avg_apy > 50:
                score -= 30
                reasons.append(f"Bullish overcrowded (avg {avg_apy:.0f}% APY) — long squeeze risk")

        score = max(-100, min(100, score))

        if score >= 60:
            signal = "STRONG_BUY"
        elif score >= 30:
            signal = "BUY"
        elif score <= -60:
            signal = "STRONG_SELL"
        elif score <= -30:
            signal = "SELL"
        else:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "score": score,
            "reasons": reasons,
            "fng": fng,
            "funding": funding,
        }

    def find_opportunities(self, funding):
        """Find best funding arb opportunities with enhanced checks."""
        opportunities = []
        for r in funding:
            if r["apy"] < 10:
                continue
            if r["symbol"] in self.positions:
                continue
            if r["price"] <= 0:
                continue

            # Record rate for tracking
            self.rate_tracker.record(r["symbol"], r["funding_rate"])

            # Enhanced entry check
            should_enter, reason, confidence = should_enter_enhanced(r, self.rate_tracker)
            if not should_enter:
                continue

            r["confidence"] = confidence
            r["entry_reason"] = reason
            opportunities.append(r)

        # Sort by confidence first, then APY
        opportunities.sort(key=lambda x: (x.get("confidence", 0), x["apy"]), reverse=True)
        return opportunities[:5]

    def open_position(self, opp):
        """Paper-trade: open a leveraged funding arb position."""
        available = self.get_available()
        # With leverage, we can open larger positions with less capital
        max_notional = self.max_position_usd * self.leverage
        margin_needed = min(self.max_position_usd, available * 0.4)
        if margin_needed < 10:
            return None

        # Notional position = margin * leverage
        notional = margin_needed * self.leverage

        price = opp["price"]
        # Apply slippage
        buy_price = price * (1 + self.slippage_pct / 100)
        amount = notional / buy_price
        fee = notional * (self.fee_pct / 100)
        usd_value = notional

        position = {
            "symbol": opp["symbol"],
            "amount": round(amount, 6),
            "entry_price": buy_price,
            "entry_funding_rate": opp["funding_rate"],
            "entry_apy": opp["apy"],
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "usd_value": usd_value,
            "margin": margin_needed,
            "leverage": self.leverage,
            "fee_paid": fee,
            "funding_collected": 0,
            "confidence": opp.get("confidence", 0.5),
            "entry_reason": opp.get("entry_reason", ""),
        }
        self.positions[opp["symbol"]] = position
        self.balance -= fee  # Deduct fee from balance

        self.trade_log.append({
            "time": position["entry_time"],
            "action": "OPEN",
            "symbol": opp["symbol"],
            "price": buy_price,
            "amount": amount,
            "usd_value": usd_value,
            "fee": fee,
            "apy": opp["apy"],
        })
        return position

    def close_position(self, symbol, reason="", current_price=None):
        """Paper-trade: close a position."""
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        price = current_price or pos["entry_price"]

        # Sell with slippage
        sell_price = price * (1 - self.slippage_pct / 100)
        proceeds = pos["amount"] * sell_price
        fee = proceeds * (self.fee_pct / 100)
        net = proceeds - fee

        # PnL = (sell - buy) for spot + funding collected
        price_pnl = (sell_price - pos["entry_price"]) * pos["amount"]
        funding_pnl = pos["funding_collected"]
        total_pnl = price_pnl + funding_pnl - pos["fee_paid"] - fee

        self.balance += net + funding_pnl

        self.trade_log.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "action": "CLOSE",
            "symbol": symbol,
            "price": sell_price,
            "amount": pos["amount"],
            "usd_value": proceeds,
            "fee": fee,
            "price_pnl": round(price_pnl, 2),
            "funding_pnl": round(funding_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "reason": reason,
        })

        del self.positions[symbol]
        return {"total_pnl": total_pnl, "price_pnl": price_pnl, "funding_pnl": funding_pnl}

    def collect_funding(self, funding_rates):
        """Simulate funding payment collection — amplified by leverage."""
        rate_map = {r["symbol"]: r for r in funding_rates}
        for symbol, pos in self.positions.items():
            rate_info = rate_map.get(symbol)
            if not rate_info:
                continue
            # Funding is paid on NOTIONAL value (margin * leverage)
            notional = pos["usd_value"]  # This is already the leveraged notional
            # 8-hour funding payment
            payment = notional * rate_info["funding_rate"]
            if pos["entry_funding_rate"] > 0:
                pos["funding_collected"] += payment
            else:
                pos["funding_collected"] += abs(payment)
            self.total_funding_collected += abs(payment)

    def check_exit_conditions(self, funding_rates):
        """Check if any positions should be closed using enhanced exit logic."""
        rate_map = {r["symbol"]: r for r in funding_rates}
        to_close = []

        for symbol, pos in self.positions.items():
            rate_info = rate_map.get(symbol)
            if not rate_info:
                continue

            current_rate = rate_info["funding_rate"]
            current_price = rate_info["price"]

            # Record for tracking
            self.rate_tracker.record(symbol, current_rate)

            # Enhanced exit check
            should_exit, reason = should_exit_enhanced(
                pos, current_rate, pos["entry_funding_rate"], pos["funding_collected"]
            )

            if should_exit:
                to_close.append((symbol, reason, rate_info["price"]))
                continue

            # Liquidation protection for leveraged positions
            entry_price = pos["entry_price"]
            price_move_pct = abs(current_price - entry_price) / entry_price * 100

            # At 3x leverage, liquidation is at ~33% adverse move
            # We close at 20% adverse move (before liquidation)
            liq_threshold = 100 / pos.get("leverage", 3) * 0.6  # 60% of way to liquidation
            if price_move_pct > liq_threshold:
                to_close.append((
                    symbol,
                    f"LIQ PROTECT: price moved {price_move_pct:.0f}% (threshold: {liq_threshold:.0f}%)",
                    current_price
                ))

        return to_close

    def check_portfolio_risk(self):
        """Portfolio-level risk check — close all if drawdown too deep."""
        equity = self.get_equity()
        if self.peak_balance:
            drawdown = ((self.peak_balance - equity) / self.peak_balance) * 100
        else:
            drawdown = 0

        self.peak_balance = max(self.peak_balance, equity)

        if drawdown > self.max_leverage_drawdown_pct:
            return True, f"Portfolio drawdown {drawdown:.1f}% exceeds limit {self.max_leverage_drawdown_pct}%"
        return False, ""

    def save_state(self):
        """Save paper trading state to disk."""
        state = {
            "balance": self.balance,
            "starting_balance": self.starting_balance,
            "positions": self.positions,
            "trade_log": self.trade_log,
            "balance_history": self.balance_history[-100:],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    def load_state(self):
        """Load previous state if exists."""
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            self.balance = state.get("balance", self.starting_balance)
            self.positions = state.get("positions", {})
            self.trade_log = state.get("trade_log", [])
            self.balance_history = state.get("balance_history", [])
            return True
        except Exception:
            return False

    # ─── Display ───────────────────────────────────────────────

    def print_dashboard(self, signal=None):
        """Print full trading dashboard."""
        pnl = self.balance - self.starting_balance
        pnl_pct = (pnl / self.starting_balance) * 100
        exposure = self.get_total_exposure()
        equity = self.get_equity()

        print("\n" + "=" * 60)
        print("  PAPER TRADING DASHBOARD")
        print("=" * 60)

        # Balance with leverage context
        if pnl >= 0:
            print(f"  Balance:    ${self.balance:.2f}  (+${pnl:.2f} / +{pnl_pct:.1f}%)")
        else:
            print(f"  Balance:    ${self.balance:.2f}  (${pnl:.2f} / {pnl_pct:.1f}%)")
        print(f"  Equity:     ${equity:.2f}  (balance + funding)")
        print(f"  Exposure:   ${exposure:.0f}  ({self.leverage}x leverage)")
        print(f"  Margin:     ${exposure/self.leverage:.0f}  used")
        print(f"  Available:  ${self.get_available():.2f}")
        print(f"  Positions:  {len(self.positions)}/{self.max_positions}")
        print(f"  Funding:    +${self.total_funding_collected:.2f} total collected")

        # Portfolio risk
        risk_ok, risk_msg = self.check_portfolio_risk()
        if not risk_ok and risk_msg:
            print(f"  RISK ALERT: {risk_msg}")

        # Signal
        if signal:
            icons = {"STRONG_BUY": "STRONG BUY", "BUY": "BUY", "NEUTRAL": "NEUTRAL",
                     "SELL": "SELL", "STRONG_SELL": "STRONG SELL"}
            print(f"\n  Sentiment: {icons.get(signal['signal'], signal['signal'])} (score: {signal['score']:+d})")
            if signal.get("fng"):
                print(f"  Fear and Greed: {signal['fng']['value']} ({signal['fng']['classification']})")
            for r in signal.get("reasons", [])[:3]:
                print(f"    - {r}")

        # Funding timing
        timing = self.funding_timer.get_timing_info()
        print(f"\n  Timing: {timing}")

        # Open positions
        if self.positions:
            print(f"\n  Open Positions:")
            for sym, pos in self.positions.items():
                held_hours = (datetime.now(timezone.utc) - datetime.fromisoformat(
                    pos["entry_time"])).total_seconds() / 3600
                stability = self.rate_tracker.get_stability(sym)
                momentum = self.rate_tracker.get_momentum(sym)
                conf = pos.get("confidence", 0)
                lev = pos.get("leverage", 1)
                margin = pos.get("margin", pos["usd_value"])
                print(f"    {sym:<10} ${pos['usd_value']:>.0f} notional ({lev}x, ${margin:.0f} margin)  "
                      f"@ {pos['entry_apy']:+.0f}% APY  funding: ${pos['funding_collected']:.2f}")
                print(f"              conf: {conf:.0%}  stable: {stability:.0%}  "
                      f"trend: {momentum}  held: {held_hours:.0f}h")
        else:
            print("\n  No open positions")

        # Recent trades
        if self.trade_log:
            print(f"\n  Recent Trades ({len(self.trade_log)} total):")
            for t in self.trade_log[-5:]:
                action = t["action"]
                if action == "OPEN":
                    print(f"    {t['time'][:16]}  OPEN  {t['symbol']:<10} ${t['usd_value']:.0f} @ {t['apy']:+.0f}% APY")
                else:
                    pnl_str = f"${t['total_pnl']:+.2f}" if "total_pnl" in t else ""
                    print(f"    {t['time'][:16]}  CLOSE {t['symbol']:<10} {pnl_str}  ({t.get('reason','')})")

        print("=" * 60)

    # ─── Run Modes ─────────────────────────────────────────────

    def run_once(self):
        """One full cycle: scan, signal, trade."""
        print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}] Scanning...")

        signal = self.generate_signal()
        funding = signal.get("funding", [])

        # Collect funding from open positions
        if self.positions and funding:
            self.collect_funding(funding)

        # Check exit conditions
        if self.positions and funding:
            to_close = self.check_exit_conditions(funding)
            for symbol, reason, price in to_close:
                result = self.close_position(symbol, reason=reason, current_price=price)
                if result:
                    print(f"  CLOSED {symbol}: ${result['total_pnl']:+.2f}")

        # Check sentiment gate
        sig = signal["signal"]
        if sig == "STRONG_SELL" and self.positions:
            for sym in list(self.positions.keys()):
                rate_map = {r["symbol"]: r for r in funding}
                r = rate_map.get(sym)
                price = r["price"] if r else None
                self.close_position(sym, reason="STRONG_SELL signal", current_price=price)
            print("  Closed all positions — STRONG_SELL sentiment")
        elif sig in ("STRONG_BUY", "BUY"):
            # Open new positions
            opportunities = self.find_opportunities(funding)
            opened = 0
            for opp in opportunities:
                if len(self.positions) >= self.max_positions:
                    break
                pos = self.open_position(opp)
                if pos:
                    print(f"  OPENED {opp['symbol']}: ${pos['usd_value']:.0f} @ {opp['apy']:+.0f}% APY")
                    opened += 1
            if opened == 0 and not self.positions:
                print("  No suitable opportunities found")
        elif sig == "NEUTRAL":
            # Only open if very high APY
            opportunities = self.find_opportunities(funding)
            high_apy = [o for o in opportunities if o["apy"] > 50]
            if high_apy and len(self.positions) < self.max_positions:
                pos = self.open_position(high_apy[0])
                if pos:
                    print(f"  OPENED {high_apy[0]['symbol']}: ${pos['usd_value']:.0f} @ {high_apy[0]['apy']:+.0f}% APY (high APY override)")

        # Record balance
        self.balance_history.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "balance": round(self.balance, 2),
        })
        self.save_state()

        self.print_dashboard(signal)

        # Cross-exchange arb scan (every 6 cycles)
        if not hasattr(self, '_cycle_count'):
            self._cycle_count = 0
        self._cycle_count += 1
        if self._cycle_count % 6 == 0:
            print("\n  Cross-Exchange Arb Scan:")
            try:
                xrate = fetch_cross_exchange_rates()
                if xrate["spreads"]:
                    for s in xrate["spreads"][:5]:
                        print(f"    {s['coin']:<10} Bybit: {s['bybit_apy']:+.0f}%  Binance: {s['binance_apy']:+.0f}%  "
                              f"Spread: {s['spread']:+.0f}%  -> {s['best_exchange']}")
                else:
                    print("    No significant spreads found")
            except Exception as e:
                print(f"    Error: {e}")

    def run_live(self, interval_seconds=300):
        """Continuous paper trading."""
        self.load_state()
        cycle = 0

        print("=" * 60)
        print(f"  LIVE PAPER TRADING — ${self.starting_balance} starting balance")
        print(f"  Interval: {interval_seconds}s")
        print("=" * 60)

        while True:
            cycle += 1
            print(f"\n--- Cycle {cycle} ---")
            try:
                self.run_once()
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                self.print_dashboard()
                break
            except Exception as e:
                print(f"  ERROR: {e}")

            print(f"\nNext cycle in {interval_seconds}s... (Ctrl+C to stop)")
            time.sleep(interval_seconds)


def main():
    trader = PaperTrader(starting_balance=200)

    if "--live" in sys.argv:
        idx = sys.argv.index("--live")
        interval = 300
        if idx + 1 < len(sys.argv):
            try:
                interval = int(sys.argv[idx + 1])
            except ValueError:
                pass
        trader.run_live(interval)
    else:
        trader.run_once()


if __name__ == "__main__":
    main()
