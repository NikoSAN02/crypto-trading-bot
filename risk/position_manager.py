"""
Position Manager — Risk controls and position sizing.
"""

import time
from datetime import datetime, timezone


class PositionManager:
    def __init__(self, exchange, config=None):
        self.exchange = exchange
        self.config = config or {}

        # Risk parameters
        self.max_total_exposure_usd = config.get("max_total_exposure_usd", 500)
        self.max_single_position_usd = config.get("max_single_position_usd", 100)
        self.max_drawdown_pct = config.get("max_drawdown_pct", 10.0)  # Stop if 10% drawdown
        self.reserve_pct = config.get("reserve_pct", 20.0)  # Keep 20% in reserve

        self.starting_balance = None
        self.peak_balance = None

    def get_available_balance(self):
        """Get available USDT for trading (minus reserve)."""
        try:
            balance = self.exchange.fetch_balance()
            free_usdt = balance.get("free", {}).get("USDT", 0)
            total_usdt = balance.get("total", {}).get("USDT", 0)

            if self.starting_balance is None:
                self.starting_balance = total_usdt
                self.peak_balance = total_usdt

            self.peak_balance = max(self.peak_balance, total_usdt)

            # Apply reserve
            available = free_usdt * (1 - self.reserve_pct / 100)
            return max(0, available), total_usdt
        except Exception as e:
            print(f"  [ERROR] Balance check failed: {e}")
            return 0, 0

    def check_drawdown(self):
        """Check if drawdown limit exceeded. Returns True if safe to trade."""
        try:
            balance = self.exchange.fetch_balance()
            total_usdt = balance.get("total", {}).get("USDT", 0)

            if self.peak_balance and self.peak_balance > 0:
                drawdown = ((self.peak_balance - total_usdt) / self.peak_balance) * 100
                if drawdown > self.max_drawdown_pct:
                    print(f"  [RISK] Drawdown {drawdown:.1f}% exceeds limit {self.max_drawdown_pct}%")
                    return False
            return True
        except Exception:
            return True

    def calculate_position_size(self, price, current_exposure=0):
        """Calculate safe position size."""
        available, total = self.get_available_balance()

        # Cap by single position limit
        max_single = min(self.max_single_position_usd, available * 0.3)

        # Cap by total exposure limit
        remaining_exposure = self.max_total_exposure_usd - current_exposure
        max_single = min(max_single, remaining_exposure)

        if max_single < 10:
            return 0

        return max_single / price

    def pre_trade_check(self, symbol, amount_usd):
        """Run all risk checks before a trade. Returns True if safe."""
        # 1. Drawdown check
        if not self.check_drawdown():
            return False, "Drawdown limit exceeded"

        # 2. Position count check
        available, total = self.get_available_balance()
        if amount_usd > available * 0.5:
            return False, f"Position too large: ${amount_usd:.2f} > 50% of available ${available:.2f}"

        # 3. Total exposure check
        if amount_usd > self.max_single_position_usd:
            return False, f"Exceeds single position limit: ${self.max_single_position_usd}"

        return True, "OK"

    def get_risk_report(self):
        """Generate a risk report."""
        available, total = self.get_available_balance()
        drawdown = 0
        if self.peak_balance and self.peak_balance > 0:
            drawdown = ((self.peak_balance - total) / self.peak_balance) * 100

        return {
            "total_balance": total,
            "available_for_trading": available,
            "reserve_pct": self.reserve_pct,
            "drawdown_pct": drawdown,
            "peak_balance": self.peak_balance,
            "max_drawdown_limit": self.max_drawdown_pct,
            "max_exposure": self.max_total_exposure_usd,
            "safe_to_trade": drawdown <= self.max_drawdown_pct,
        }
