"""
Hybrid Paper Trader — Arb + Signal Detection combined.

TIER 1: Delta-neutral funding arb (steady income, ~0% price risk)
  - Scan Bybit vs Binance funding rate differentials
  - Open long on cheaper exchange, short on expensive exchange
  - Collect funding spread, close when spread narrows

TIER 2: Signal-based directional trades (explosive gains)
  - Volume anomalies, funding extremes, OI divergence, L/S ratio, Fear & Greed
  - Composite score >80 = strong buy signal
  - Enter with tight stop-loss, take profit on momentum

Capital allocation:
  - 60% ($120) → Arb tier
  - 40% ($80)  → Signal tier
"""

import json
import sys
import time
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from strategies.signal_scanner import SignalScanner
from strategies.sentiment_engine import SentimentEngine
from strategies.enhanced_strategies import RateTracker, FundingTimer


class HybridTrader:
    """Combined arb + signal-based trading engine."""

    def __init__(self, starting_balance=200):
        self.starting_balance = starting_balance
        self.balance = starting_balance

        # Positions split by tier
        self.arb_positions = {}   # Tier 1: delta-neutral arb
        self.signal_positions = {}  # Tier 2: directional signal trades
        self.trade_log = []
        self.balance_history = [{"time": datetime.now(timezone.utc).isoformat(), "balance": starting_balance}]

        self.state_file = os.path.expanduser("~/projects/crypto-trading-bot/hybrid_state.json")

        # Engines
        self.scanner = SignalScanner()
        self.sentiment = SentimentEngine()
        self.rate_tracker = RateTracker(max_history=12)
        self.funding_timer = FundingTimer()

        # Capital allocation
        self.arb_allocation_pct = 60  # 60% for arb
        self.signal_allocation_pct = 40  # 40% for signals

        # Config
        self.leverage = 3
        self.fee_pct = 0.06
        self.slippage_pct = 0.1
        self.max_arb_positions = 3
        self.max_signal_positions = 2
        self.reserve_pct = 15

        # Arb config
        self.min_arb_spread_apy = 15  # Min 15% APY spread to open arb
        self.arb_exit_spread_apy = 5  # Close arb when spread drops below 5%

        # Signal config
        self.min_signal_score = 65  # Min composite score to enter
        self.signal_stop_loss_pct = 5  # 5% price stop loss (15% at 3x)
        self.signal_take_profit_pct = 10  # 10% price take profit (30% at 3x)
        self.min_signal_hold_hours = 2  # Hold at least 2 hours

        # Risk tracking
        self.peak_balance = starting_balance
        self.total_funding_collected = 0

    def _fetch_json(self, url, timeout=10):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hybrid-trader/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    # ─── Tier 1: Funding Arb ───────────────────────────────────

    def get_arb_available(self):
        """Capital available for arb positions."""
        total_arb = self.balance * (self.arb_allocation_pct / 100)
        used = sum(p["margin"] for p in self.arb_positions.values())
        return max(0, total_arb - used)

    def get_signal_available(self):
        """Capital available for signal positions."""
        total_signal = self.balance * (self.signal_allocation_pct / 100)
        used = sum(p["margin"] for p in self.signal_positions.values())
        return max(0, total_signal - used)

    def scan_arb_opportunities(self):
        """Find funding rate spreads between Bybit and Binance."""
        # Fetch Bybit rates
        bybit_data = self._fetch_json("https://api.bybit.com/v5/market/tickers?category=linear")
        bybit_rates = {}
        if bybit_data and bybit_data.get("result", {}).get("list"):
            for r in bybit_data["result"]["list"]:
                turnover = float(r.get("turnover24h", 0))
                if turnover < 10_000_000:
                    continue
                symbol = r["symbol"].replace("USDT", "")
                rate = float(r.get("fundingRate", 0))
                price = float(r.get("lastPrice", 0))
                bybit_rates[symbol] = {"rate": rate, "apy": rate * 3 * 365 * 100, "price": price}

        # Fetch Binance rates
        binance_data = self._fetch_json("https://fapi.binance.com/fapi/v1/premiumIndex")
        binance_rates = {}
        if binance_data:
            for item in binance_data:
                sym = item["symbol"]
                if not sym.endswith("USDT"):
                    continue
                base = sym.replace("USDT", "")
                rate = float(item.get("lastFundingRate", 0))
                price = float(item.get("markPrice", 0))
                binance_rates[base] = {"rate": rate, "apy": rate * 3 * 365 * 100, "price": price}

        # Find spreads
        opportunities = []
        for coin in bybit_rates:
            if coin in binance_rates and coin not in self.arb_positions:
                bb = bybit_rates[coin]
                bn = binance_rates[coin]
                spread = bb["apy"] - bn["apy"]

                if abs(spread) >= self.min_arb_spread_apy:
                    # Determine direction: long the cheaper exchange, short the expensive one
                    if spread > 0:
                        long_exchange = "binance"
                        short_exchange = "bybit"
                        long_rate = bn["rate"]
                        short_rate = bb["rate"]
                    else:
                        long_exchange = "bybit"
                        short_exchange = "binance"
                        long_rate = bb["rate"]
                        short_rate = bn["rate"]

                    opportunities.append({
                        "symbol": coin,
                        "spread_apy": round(abs(spread), 1),
                        "long_exchange": long_exchange,
                        "short_exchange": short_exchange,
                        "long_rate": long_rate,
                        "short_rate": short_rate,
                        "avg_price": (bb["price"] + bn["price"]) / 2,
                        "bybit_apy": round(bb["apy"], 1),
                        "binance_apy": round(bn["apy"], 1),
                    })

        opportunities.sort(key=lambda x: x["spread_apy"], reverse=True)
        return opportunities

    def open_arb_position(self, opp):
        """Open a delta-neutral arb position (simulated)."""
        available = self.get_arb_available()
        if available < 15:
            return None
        if len(self.arb_positions) >= self.max_arb_positions:
            return None

        # Size: use available arb capital, split between the two legs
        margin = min(available * 0.4, 30)  # Max $30 margin per arb
        notional = margin * self.leverage
        price = opp["avg_price"]
        amount = notional / price

        fee_open = notional * (self.fee_pct / 100) * 2  # Both legs
        self.balance -= fee_open

        position = {
            "symbol": opp["symbol"],
            "amount": round(amount, 6),
            "margin": margin,
            "notional": notional,
            "entry_price": price,
            "spread_apy": opp["spread_apy"],
            "long_exchange": opp["long_exchange"],
            "short_exchange": opp["short_exchange"],
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "funding_collected": 0,
            "fee_paid": fee_open,
            "tier": "arb",
        }
        self.arb_positions[opp["symbol"]] = position

        self.trade_log.append({
            "time": position["entry_time"],
            "action": "ARB_OPEN",
            "symbol": opp["symbol"],
            "spread_apy": opp["spread_apy"],
            "notional": notional,
            "fee": fee_open,
        })

        return position

    def collect_arb_funding(self):
        """Collect funding from arb positions — only at Bybit funding windows."""
        now = datetime.now(timezone.utc)
        FUNDING_HOURS = [0, 8, 16]
        is_funding_time = any(now.hour == h and now.minute < 10 for h in FUNDING_HOURS)
        if not is_funding_time:
            return

        funding_key = f"{now.strftime('%Y-%m-%d')}_{now.hour}"
        if getattr(self, '_last_arb_funding_key', None) == funding_key:
            return
        self._last_arb_funding_key = funding_key

        for symbol, pos in self.arb_positions.items():
            # Arb funding = absolute spread × notional × (8/24/365)
            # We collect from BOTH sides — the long side gets paid when rate is negative,
            # the short side gets paid when rate is positive
            funding = pos["notional"] * (pos["spread_apy"] / 100) * (8 / 24 / 365)
            pos["funding_collected"] += funding
            self.total_funding_collected += funding
            self.balance += funding

    def check_arb_exits(self):
        """Check if arb positions should be closed."""
        to_close = []
        fresh_opps = self.scan_arb_opportunities()
        opp_map = {o["symbol"]: o for o in fresh_opps}

        for symbol, pos in self.arb_positions.items():
            # Check if spread still exists
            if symbol in opp_map:
                current_spread = opp_map[symbol]["spread_apy"]
                if current_spread < self.arb_exit_spread_apy:
                    to_close.append((symbol, f"Spread narrowed to {current_spread:.1f}%"))
            else:
                # Spread disappeared (one side went below threshold)
                to_close.append((symbol, "Spread disappeared"))

            # Hold time check — minimum 8 hours (1 funding window)
            held_hours = (datetime.now(timezone.utc) - datetime.fromisoformat(pos["entry_time"])).total_seconds() / 3600
            if held_hours < 8 and not to_close:
                # Skip exit checks during hold period (unless spread collapsed)
                if to_close and to_close[-1][0] == symbol:
                    pass  # Already marked for close
                else:
                    continue

        return to_close

    def close_arb_position(self, symbol, reason=""):
        """Close an arb position."""
        if symbol not in self.arb_positions:
            return None

        pos = self.arb_positions[symbol]
        notional = pos["notional"]
        fee_close = notional * (self.fee_pct / 100) * 2  # Both legs

        # PnL = funding collected - total fees
        total_pnl = pos["funding_collected"] - pos["fee_paid"] - fee_close
        self.balance += (pos["margin"] - pos["fee_paid"]) - fee_close + pos["funding_collected"]
        self.balance -= pos["margin"]  # Remove the margin we already had
        # Actually, let me simplify the accounting:
        # At open: balance -= fee_open (already done)
        # At close: balance += margin + funding - fee_close
        # But margin was "allocated" not deducted, so just add funding - fee_close
        self.balance -= fee_close

        self.trade_log.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "action": "ARB_CLOSE",
            "symbol": symbol,
            "funding_collected": round(pos["funding_collected"], 4),
            "total_pnl": round(total_pnl, 4),
            "reason": reason,
        })

        del self.arb_positions[symbol]
        return {"total_pnl": total_pnl, "funding_pnl": pos["funding_collected"]}

    # ─── Tier 2: Signal Trades ─────────────────────────────────

    def scan_signal_opportunities(self):
        """Run signal scanner and return top opportunities."""
        results = self.scanner.full_scan()

        # Filter: only high-score coins not already held
        opportunities = []
        for r in results:
            if r["composite_score"] < self.min_signal_score:
                continue
            if r["symbol"] in self.signal_positions:
                continue
            if r["volume_usd"] < 5_000_000:  # Min $5M daily volume
                continue
            if abs(r.get("price_change_pct", 0)) > 20:  # Skip coins already pumping
                continue
            # Skip dead coins (no price movement = stablecoin or delisted)
            if abs(r.get("price_change_pct", 0)) < 0.3:
                continue
            # Require at least 2 signals to have real data (not default 50)
            sig = r.get("signals", {})
            real_signals = sum(1 for v in sig.values() if v != 50)
            if real_signals < 2:
                continue  # Not enough data — skip entirely
            opportunities.append(r)

        return opportunities[:3]  # Max 3 candidates

    def open_signal_position(self, opp):
        """Open a directional signal trade."""
        available = self.get_signal_available()
        if available < 10:
            return None
        if len(self.signal_positions) >= self.max_signal_positions:
            return None

        margin = min(available * 0.5, 25)  # Max $25 margin per signal trade
        notional = margin * self.leverage

        # Get current price from Binance
        price_data = self._fetch_json(
            f"https://api.binance.com/api/v3/ticker/price?symbol={opp['symbol']}USDT"
        )
        if not price_data or "price" not in price_data:
            return None

        price = float(price_data["price"])
        amount = notional / price

        fee_open = notional * (self.fee_pct / 100)
        self.balance -= fee_open

        position = {
            "symbol": opp["symbol"],
            "amount": round(amount, 6),
            "margin": margin,
            "notional": notional,
            "entry_price": price,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "composite_score": opp["composite_score"],
            "stop_loss_price": price * (1 - self.signal_stop_loss_pct / 100),
            "take_profit_price": price * (1 + self.signal_take_profit_pct / 100),
            "fee_paid": fee_open,
            "tier": "signal",
            "signals": opp.get("signals", {}),
        }
        self.signal_positions[opp["symbol"]] = position

        self.trade_log.append({
            "time": position["entry_time"],
            "action": "SIGNAL_OPEN",
            "symbol": opp["symbol"],
            "score": opp["composite_score"],
            "notional": notional,
            "price": price,
            "fee": fee_open,
        })

        return position

    def check_signal_exits(self):
        """Check if signal positions should be closed."""
        to_close = []
        now = datetime.now(timezone.utc)

        for symbol, pos in self.signal_positions.items():
            # Get current price
            price_data = self._fetch_json(
                f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT"
            )
            if not price_data:
                continue

            current_price = float(price_data["price"])
            held_hours = (now - datetime.fromisoformat(pos["entry_time"])).total_seconds() / 3600

            # Stop loss
            if current_price <= pos["stop_loss_price"]:
                to_close.append((symbol, f"STOP LOSS: price {current_price:.4f} < {pos['stop_loss_price']:.4f}", current_price))
                continue

            # Take profit
            if current_price >= pos["take_profit_price"]:
                to_close.append((symbol, f"TAKE PROFIT: price {current_price:.4f} > {pos['take_profit_price']:.4f}", current_price))
                continue

            # Minimum hold time
            if held_hours < self.min_signal_hold_hours:
                continue

            # Re-score: check if signal is still strong
            # Simple check: if price dropped >3% since entry, close
            price_change = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
            if held_hours > 4 and price_change < -2:
                to_close.append((symbol, f"Weak momentum: {price_change:+.1f}% after {held_hours:.0f}h", current_price))
                continue

            # Time-based exit: close after 24 hours if not profitable
            if held_hours > 24 and price_change < 1:
                to_close.append((symbol, f"Time exit: {price_change:+.1f}% after {held_hours:.0f}h", current_price))
                continue

        return to_close

    def close_signal_position(self, symbol, reason="", current_price=None):
        """Close a signal trade."""
        if symbol not in self.signal_positions:
            return None

        pos = self.signal_positions[symbol]
        price = current_price or pos["entry_price"]
        notional = pos["notional"]

        fee_close = notional * (self.fee_pct / 100)
        price_pnl = (price - pos["entry_price"]) * pos["amount"] * pos.get("leverage", self.leverage)
        # Simplified: pnl = price_change% × notional
        price_pnl = ((price - pos["entry_price"]) / pos["entry_price"]) * notional
        total_pnl = price_pnl - pos["fee_paid"] - fee_close

        self.balance += total_pnl

        self.trade_log.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "action": "SIGNAL_CLOSE",
            "symbol": symbol,
            "entry_price": pos["entry_price"],
            "exit_price": price,
            "price_pnl": round(price_pnl, 4),
            "total_pnl": round(total_pnl, 4),
            "reason": reason,
        })

        del self.signal_positions[symbol]
        return {"total_pnl": total_pnl, "price_pnl": price_pnl}

    # ─── Main Loop ─────────────────────────────────────────────

    def run_once(self):
        """One full cycle of the hybrid trader."""
        now = datetime.now(timezone.utc)
        print(f"\n{'='*60}")
        print(f"  HYBRID TRADER — {now.strftime('%H:%M:%S UTC')}")
        print(f"{'='*60}")

        # ── Tier 1: Arb ──
        print(f"\n  ━━ TIER 1: FUNDING ARB ━━")

        # Collect funding
        self.collect_arb_funding()

        # Check arb exits
        arb_exits = self.check_arb_exits()
        for symbol, reason in arb_exits:
            result = self.close_arb_position(symbol, reason)
            if result:
                print(f"  CLOSED ARB {symbol}: ${result['total_pnl']:+.4f} ({reason})")

        # Scan for new arb opportunities
        arb_opps = self.scan_arb_opportunities()
        if arb_opps:
            print(f"  Found {len(arb_opps)} arb opportunities:")
            for o in arb_opps[:5]:
                print(f"    {o['symbol']:<10} Spread: {o['spread_apy']:>5.1f}% APY  "
                      f"({o['long_exchange']} long / {o['short_exchange']} short)")

            # Open arb positions
            for opp in arb_opps:
                if len(self.arb_positions) >= self.max_arb_positions:
                    break
                pos = self.open_arb_position(opp)
                if pos:
                    print(f"  OPENED ARB {opp['symbol']}: ${pos['notional']:.0f} notional, "
                          f"spread {opp['spread_apy']:.1f}% APY")
        else:
            print(f"  No arb opportunities (min spread: {self.min_arb_spread_apy}%)")

        # ── Tier 2: Signals ──
        print(f"\n  ━━ TIER 2: SIGNAL HUNTER ━━")

        # Check signal exits
        signal_exits = self.check_signal_exits()
        for symbol, reason, price in signal_exits:
            result = self.close_signal_position(symbol, reason, price)
            if result:
                print(f"  CLOSED SIGNAL {symbol}: ${result['total_pnl']:+.4f} ({reason})")

        # Scan for signal opportunities (every 3 cycles to reduce API calls)
        if not hasattr(self, '_signal_cycle'):
            self._signal_cycle = 0
        self._signal_cycle += 1

        signal_opps = []
        if self._signal_cycle % 3 == 1 or not self.signal_positions:
            signal_opps = self.scan_signal_opportunities()
            if signal_opps:
                print(f"  Found {len(signal_opps)} signal opportunities:")
                for s in signal_opps:
                    print(f"    {s['symbol']:<10} Score: {s['composite_score']:.1f}  "
                          f"{s['classification']}")
                    sig = s.get("signals", {})
                    print(f"      Vol:{sig.get('volume',50):.0f} Fund:{sig.get('funding',50):.0f} "
                          f"OI:{sig.get('oi',50):.0f} LS:{sig.get('ls_ratio',50):.0f} "
                          f"FNG:{sig.get('fng',50):.0f}")

                # Open signal positions
                for opp in signal_opps:
                    if len(self.signal_positions) >= self.max_signal_positions:
                        break
                    pos = self.open_signal_position(opp)
                    if pos:
                        print(f"  OPENED SIGNAL {opp['symbol']}: ${pos['notional']:.0f} @ "
                              f"${pos['entry_price']:.4f}, score {opp['composite_score']:.0f}")
            else:
                print(f"  No signal opportunities (min score: {self.min_signal_score})")
        else:
            print(f"  Skipping signal scan (cycle {self._signal_cycle}, scan every 3)")

        # ── Dashboard ──
        self.print_dashboard()

        # Save state
        self.balance_history.append({
            "time": now.isoformat(),
            "balance": round(self.balance, 2),
        })
        self.save_state()

    def print_dashboard(self):
        """Print hybrid trader dashboard."""
        arb_exposure = sum(p["notional"] for p in self.arb_positions.values())
        signal_exposure = sum(p["notional"] for p in self.signal_positions.values())
        total_exposure = arb_exposure + signal_exposure
        pnl = self.balance - self.starting_balance
        pnl_pct = (pnl / self.starting_balance) * 100

        print(f"\n  ── Portfolio ──")
        if pnl >= 0:
            print(f"  Balance:  ${self.balance:.2f}  (+${pnl:.2f} / +{pnl_pct:.1f}%)")
        else:
            print(f"  Balance:  ${self.balance:.2f}  (${pnl:.2f} / {pnl_pct:.1f}%)")
        print(f"  Exposure: ${total_exposure:.0f} total")
        print(f"    Arb:    ${arb_exposure:.0f} ({len(self.arb_positions)}/{self.max_arb_positions} positions)")
        print(f"    Signal: ${signal_exposure:.0f} ({len(self.signal_positions)}/{self.max_signal_positions} positions)")
        print(f"  Funding:  +${self.total_funding_collected:.4f} collected")

        if self.arb_positions:
            print(f"\n  ── Arb Positions ──")
            for sym, pos in self.arb_positions.items():
                held = (datetime.now(timezone.utc) - datetime.fromisoformat(pos["entry_time"])).total_seconds() / 3600
                print(f"    {sym:<10} ${pos['notional']:.0f} notional  spread {pos['spread_apy']:.1f}%  "
                      f"funding ${pos['funding_collected']:.4f}  held {held:.0f}h")

        if self.signal_positions:
            print(f"\n  ── Signal Positions ──")
            for sym, pos in self.signal_positions.items():
                held = (datetime.now(timezone.utc) - datetime.fromisoformat(pos["entry_time"])).total_seconds() / 3600
                # Get current price for P&L
                price_data = self._fetch_json(
                    f"https://api.binance.com/api/v3/ticker/price?symbol={sym}USDT"
                )
                if price_data:
                    current = float(price_data["price"])
                    change = ((current - pos["entry_price"]) / pos["entry_price"]) * 100
                    pnl_usd = (change / 100) * pos["notional"]
                    print(f"    {sym:<10} ${pos['notional']:.0f} notional  score {pos['composite_score']:.0f}  "
                          f"{change:+.1f}% (${pnl_usd:+.2f})  held {held:.0f}h")
                else:
                    print(f"    {sym:<10} ${pos['notional']:.0f} notional  score {pos['composite_score']:.0f}  "
                          f"held {held:.0f}h")

        # Recent trades
        if self.trade_log:
            print(f"\n  ── Recent Trades ({len(self.trade_log)} total) ──")
            for t in self.trade_log[-5:]:
                action = t["action"]
                sym = t["symbol"]
                if action == "ARB_OPEN":
                    print(f"    {t['time'][:16]}  {action}  {sym:<10} spread {t['spread_apy']:.1f}%")
                elif action == "ARB_CLOSE":
                    print(f"    {t['time'][:16]}  {action}  {sym:<10} ${t['total_pnl']:+.4f}  ({t.get('reason','')})")
                elif action == "SIGNAL_OPEN":
                    print(f"    {t['time'][:16]}  {action}  {sym:<10} score {t['score']:.0f}  ${t['notional']:.0f}")
                elif action == "SIGNAL_CLOSE":
                    print(f"    {t['time'][:16]}  {action}  {sym:<10} ${t['total_pnl']:+.4f}  ({t.get('reason','')})")

        print(f"\n{'='*60}")

    def save_state(self):
        """Save state to disk."""
        state = {
            "balance": self.balance,
            "starting_balance": self.starting_balance,
            "arb_positions": self.arb_positions,
            "signal_positions": self.signal_positions,
            "trade_log": self.trade_log[-50:],
            "balance_history": self.balance_history[-100:],
            "total_funding_collected": self.total_funding_collected,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    def load_state(self):
        """Load previous state."""
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            self.balance = state.get("balance", self.starting_balance)
            self.arb_positions = state.get("arb_positions", {})
            self.signal_positions = state.get("signal_positions", {})
            self.trade_log = state.get("trade_log", [])
            self.balance_history = state.get("balance_history", [])
            self.total_funding_collected = state.get("total_funding_collected", 0)
            return True
        except Exception:
            return False

    def run_live(self, interval_seconds=300):
        """Continuous hybrid trading."""
        self.load_state()

        print("=" * 60)
        print(f"  HYBRID TRADER — ${self.starting_balance} starting balance")
        print(f"  Arb: {self.arb_allocation_pct}% | Signal: {self.signal_allocation_pct}%")
        print(f"  Interval: {interval_seconds}s")
        print("=" * 60)

        cycle = 0
        while True:
            cycle += 1
            print(f"\n--- Cycle {cycle} ---")
            try:
                self.run_once()
            except KeyboardInterrupt:
                print("\n\nStopped.")
                self.print_dashboard()
                break
            except Exception as e:
                print(f"  ERROR: {e}")
                import traceback
                traceback.print_exc()

            print(f"\nNext cycle in {interval_seconds}s... (Ctrl+C to stop)")
            time.sleep(interval_seconds)


def main():
    trader = HybridTrader(starting_balance=200)

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
