"""
Funding Rate Arbitrage Engine

Strategy:
1. Find coins with HIGH positive funding rates (shorts get paid)
2. BUY spot + SHORT perpetual (same amount, same exchange)
3. Collect funding payments every 8 hours
4. Close both positions when funding rate drops below threshold

This is delta-neutral — profit comes from funding, not price direction.
"""

import ccxt
import time
import json
from datetime import datetime, timezone


class FundingArbEngine:
    def __init__(self, exchange, config=None):
        self.exchange = exchange
        self.config = config or {}
        self.positions = {}  # Track open positions
        self.trade_log = []

        # Default config
        self.min_annual_rate = self.config.get("min_annual_rate", 15.0)  # Min 15% APY to enter
        self.exit_annual_rate = self.config.get("exit_annual_rate", 5.0)  # Exit below 5% APY
        self.max_position_usd = self.config.get("max_position_usd", 100)  # Max $100 per position
        self.max_positions = self.config.get("max_positions", 3)  # Max 3 concurrent positions
        self.slippage_buffer = self.config.get("slippage_buffer", 0.001)  # 0.1% slippage buffer

    def scan_opportunities(self, top_n=20):
        """Scan all perp markets for funding rate opportunities."""
        print("\n[SCANNER] Scanning funding rates...")
        opportunities = []

        for market in self.exchange.markets.values():
            if not (market.get("swap") and market.get("active") and "/USDT" in market["symbol"]):
                continue
            try:
                ticker = self.exchange.fetch_funding_rate(market["symbol"])
                rate = ticker.get("fundingRate", 0)
                if not rate:
                    continue

                annualized = rate * 3 * 365 * 100  # 3 payments/day

                # We want HIGH POSITIVE rates (shorts get paid)
                if annualized >= self.min_annual_rate:
                    # Check if spot market exists and has liquidity
                    spot_symbol = market["symbol"].split(":")[0]  # BTC/USDT:USDT -> BTC/USDT
                    try:
                        spot_ticker = self.exchange.fetch_ticker(spot_symbol)
                        volume_24h = spot_ticker.get("quoteVolume", 0)
                        if volume_24h < 1_000_000:  # Skip low liquidity (<$1M daily)
                            continue
                    except Exception:
                        continue

                    opportunities.append({
                        "symbol": market["symbol"],
                        "spot_symbol": spot_symbol,
                        "fundingRate": rate,
                        "annualized": annualized,
                        "markPrice": ticker.get("markPrice"),
                        "nextFunding": ticker.get("nextFundingTime"),
                        "volume_24h": volume_24h,
                    })
            except Exception:
                continue

        opportunities.sort(key=lambda x: x["annualized"], reverse=True)
        return opportunities[:top_n]

    def calculate_position_size(self, symbol, spot_symbol, available_usdt):
        """Calculate safe position size based on available balance and config."""
        max_size = min(self.max_position_usd, available_usdt * 0.3)  # Use max 30% of balance

        if max_size < 10:  # Minimum $10 position
            return 0, 0

        # Get current price
        ticker = self.exchange.fetch_ticker(spot_symbol)
        price = ticker["last"]
        amount = max_size / price

        # Round to exchange precision
        market = self.exchange.market(symbol)
        amount = self.exchange.amount_to_precision(symbol, amount)
        return float(amount), max_size

    def open_position(self, opportunity, available_usdt):
        """Open a funding rate arb position: buy spot + short perp."""
        symbol = opportunity["symbol"]
        spot_symbol = opportunity["spot_symbol"]

        if symbol in self.positions:
            print(f"  [SKIP] Already have position in {symbol}")
            return None

        if len(self.positions) >= self.max_positions:
            print(f"  [SKIP] Max positions reached ({self.max_positions})")
            return None

        # Calculate size
        amount, usd_value = self.calculate_position_size(symbol, spot_symbol, available_usdt)
        if amount == 0:
            print(f"  [SKIP] Insufficient balance for {symbol}")
            return None

        print(f"\n[OPEN] Funding arb: {symbol}")
        print(f"  Size: {amount} ≈ ${usd_value:.2f}")
        print(f"  Funding rate: {opportunity['annualized']:.1f}% APY")

        try:
            # 1. Buy spot
            print(f"  [1/2] Buying spot {spot_symbol}...")
            spot_order = self.exchange.create_order(
                symbol=spot_symbol,
                type="market",
                side="buy",
                amount=amount,
            )
            print(f"  Spot order: {spot_order['id']} — filled {spot_order.get('filled', 0)}")

            # 2. Short perp
            print(f"  [2/2] Shorting perp {symbol}...")
            perp_order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side="sell",
                amount=amount,
            )
            print(f"  Perp order: {perp_order['id']} — filled {perp_order.get('filled', 0)}")

            # Record position
            position = {
                "symbol": symbol,
                "spot_symbol": spot_symbol,
                "amount": amount,
                "entry_price": spot_order.get("average", opportunity["markPrice"]),
                "entry_funding_rate": opportunity["fundingRate"],
                "entry_annualized": opportunity["annualized"],
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "spot_order_id": spot_order["id"],
                "perp_order_id": perp_order["id"],
                "usd_value": usd_value,
            }
            self.positions[symbol] = position
            self._log_trade("OPEN", position)
            return position

        except Exception as e:
            print(f"  [ERROR] Failed to open {symbol}: {e}")
            # Try to close anything that was opened
            self._emergency_close(symbol, amount)
            return None

    def close_position(self, symbol, reason="manual"):
        """Close a funding arb position: sell spot + close short."""
        if symbol not in self.positions:
            print(f"  [SKIP] No position in {symbol}")
            return None

        pos = self.positions[symbol]
        amount = pos["amount"]

        print(f"\n[CLOSE] {symbol} (reason: {reason})")
        print(f"  Size: {amount} ≈ ${pos['usd_value']:.2f}")
        print(f"  Held since: {pos['entry_time']}")

        try:
            # 1. Sell spot
            print(f"  [1/2] Selling spot {pos['spot_symbol']}...")
            spot_order = self.exchange.create_order(
                symbol=pos["spot_symbol"],
                type="market",
                side="sell",
                amount=amount,
            )

            # 2. Close perp short (buy to close)
            print(f"  [2/2] Closing perp short {symbol}...")
            perp_order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side="buy",
                amount=amount,
            )

            # Calculate profit estimate
            exit_price = spot_order.get("average", 0)
            price_pnl = (exit_price - pos["entry_price"]) * amount  # Should be ~0 (hedge)
            self._log_trade("CLOSE", pos, exit_price=exit_price, price_pnl=price_pnl)

            del self.positions[symbol]
            return {"symbol": symbol, "price_pnl": price_pnl}

        except Exception as e:
            print(f"  [ERROR] Failed to close {symbol}: {e}")
            return None

    def check_positions(self):
        """Check all positions — exit if funding rate dropped below threshold."""
        for symbol in list(self.positions.keys()):
            try:
                ticker = self.exchange.fetch_funding_rate(symbol)
                rate = ticker.get("fundingRate", 0)
                annualized = rate * 3 * 365 * 100

                pos = self.positions[symbol]
                print(f"  {symbol}: funding={rate:+.6f} ({annualized:+.1f}% APY)")

                if annualized < self.exit_annual_rate:
                    print(f"  [EXIT SIGNAL] Funding rate too low: {annualized:.1f}% < {self.exit_annual_rate}%")
                    self.close_position(symbol, reason="funding_rate_below_threshold")

            except Exception as e:
                print(f"  [ERROR] Checking {symbol}: {e}")

    def _emergency_close(self, symbol, amount):
        """Emergency close — best effort to unwind."""
        try:
            spot_symbol = symbol.split(":")[0]
            self.exchange.create_order(spot_symbol, "market", "sell", amount)
        except Exception:
            pass
        try:
            self.exchange.create_order(symbol, "market", "buy", amount)
        except Exception:
            pass

    def _log_trade(self, action, position, exit_price=None, price_pnl=None):
        """Log a trade for history."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "symbol": position["symbol"],
            "amount": position["amount"],
            "usd_value": position["usd_value"],
            "entry_rate": position["entry_annualized"],
        }
        if exit_price:
            entry["exit_price"] = exit_price
            entry["price_pnl"] = price_pnl
        self.trade_log.append(entry)
        print(f"  [LOG] {action} {position['symbol']}")

    def get_status(self):
        """Return current bot status."""
        return {
            "positions": len(self.positions),
            "max_positions": self.max_positions,
            "open_positions": list(self.positions.keys()),
            "total_trades": len(self.trade_log),
            "config": {
                "min_annual_rate": self.min_annual_rate,
                "exit_annual_rate": self.exit_annual_rate,
                "max_position_usd": self.max_position_usd,
            },
        }
