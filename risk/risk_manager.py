"""
Risk Manager — Inspired by ai-hedge-fund's risk management.

Three key improvements:
  1. Volatility-adjusted position sizing
  2. Correlation-based exposure caps
  3. Performance metrics (Sharpe, Sortino, Max Drawdown)
"""

import math
from datetime import datetime, timezone


class VolatilityAdjustedSizer:
    """
    Position sizing that scales with funding rate volatility.

    Low volatility (stable rates) → larger positions (up to 25% allocation)
    High volatile (wild rates) → smaller positions (down to 8% allocation)
    """

    def __init__(self, min_alloc_pct=8, max_alloc_pct=25):
        self.min_alloc_pct = min_alloc_pct
        self.max_alloc_pct = max_alloc_pct

    def calculate_size(self, balance, stability_score, confidence=0.5):
        """
        Calculate position size in USD based on rate stability.

        Args:
            balance: Total available balance
            stability_score: 0-1 score from RateTracker.get_stability()
            confidence: 0-1 confidence from entry check

        Returns:
            Position size in USD
        """
        # Base allocation from stability
        # stability 1.0 → max_alloc, stability 0.3 → min_alloc
        if stability_score >= 0.3:
            alloc_pct = self.min_alloc_pct + (self.max_alloc_pct - self.min_alloc_pct) * (
                (stability_score - 0.3) / 0.7
            )
        else:
            alloc_pct = self.min_alloc_pct

        # Adjust by confidence
        alloc_pct *= 0.5 + (confidence * 0.5)  # 50-100% of calculated size

        size = balance * (alloc_pct / 100)
        return round(size, 2)


class CorrelationCap:
    """
    Reduce exposure when multiple positions have correlated funding rates.

    When rates spike together across pairs, total risk is higher.
    This class detects correlation and scales down accordingly.
    """

    def __init__(self, correlation_threshold=0.8, reduction_multiplier=0.7):
        self.correlation_threshold = correlation_threshold
        self.reduction_multiplier = reduction_multiplier

    def get_correlation_score(self, rate_tracker, symbols):
        """
        Estimate correlation between positions based on rate movements.
        Returns a score 0-1 (1 = highly correlated = bad).
        """
        if len(symbols) < 2:
            return 0.0

        # Get rate histories for all symbols
        histories = {}
        for sym in symbols:
            entries = rate_tracker.history.get(sym, [])
            if len(entries) >= 3:
                histories[sym] = [r for _, r in entries[-6:]]

        if len(histories) < 2:
            return 0.0

        # Calculate pairwise correlation
        sym_list = list(histories.keys())
        correlations = []

        for i in range(len(sym_list)):
            for j in range(i + 1, len(sym_list)):
                a = histories[sym_list[i]]
                b = histories[sym_list[j]]
                # Match lengths
                min_len = min(len(a), len(b))
                a, b = a[-min_len:], b[-min_len:]

                if min_len < 2:
                    continue

                corr = self._pearson(a, b)
                correlations.append(corr)

        if not correlations:
            return 0.0

        avg_corr = sum(correlations) / len(correlations)
        return max(0.0, avg_corr)  # Only positive correlation matters

    def get_exposure_multiplier(self, rate_tracker, symbols):
        """
        Returns a multiplier (0.5-1.0) to apply to position sizes.
        High correlation → lower multiplier.
        """
        corr_score = self.get_correlation_score(rate_tracker, symbols)

        if corr_score < 0.5:
            return 1.0  # Low correlation, no reduction
        elif corr_score < self.correlation_threshold:
            # Gradual reduction
            factor = (corr_score - 0.5) / (self.correlation_threshold - 0.5)
            return 1.0 - (factor * (1 - self.reduction_multiplier))
        else:
            return self.reduction_multiplier  # Max reduction

    @staticmethod
    def _pearson(x, y):
        """Simple Pearson correlation coefficient."""
        n = len(x)
        if n < 2:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        den_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
        den_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))

        if den_x == 0 or den_y == 0:
            return 0.0

        return num / (den_x * den_y)


class PerformanceMetrics:
    """
    Track risk-adjusted performance metrics.

    Inspired by ai-hedge-fund's metrics calculator.
    Tracks: Sharpe ratio, Sortino ratio, Max Drawdown, Win Rate.
    """

    def __init__(self, risk_free_rate=0.0):
        self.risk_free_rate = risk_free_rate  # Daily risk-free rate
        self.balance_history = []
        self.trade_pnls = []
        self.peak = 0
        self.max_drawdown = 0

    def update(self, balance, timestamp=None):
        """Record a balance snapshot."""
        self.balance_history.append({
            "time": timestamp or datetime.now(timezone.utc).isoformat(),
            "balance": balance
        })
        self.peak = max(self.peak, balance)

        if self.peak > 0:
            dd = (self.peak - balance) / self.peak
            self.max_drawdown = max(self.max_drawdown, dd)

    def record_trade(self, pnl):
        """Record a completed trade PnL."""
        self.trade_pnls.append(pnl)

    def get_sharpe_ratio(self, periods=30):
        """
        Calculate Sharpe ratio from recent balance history.
        Annualized, based on daily returns.
        """
        if len(self.balance_history) < 3:
            return None

        # Calculate returns from last N periods
        history = self.balance_history[-periods:]
        if len(history) < 3:
            return None

        returns = []
        for i in range(1, len(history)):
            prev = history[i - 1]["balance"]
            curr = history[i]["balance"]
            if prev > 0:
                returns.append((curr - prev) / prev)

        if len(returns) < 2:
            return None

        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        std_dev = variance ** 0.5

        if std_dev == 0:
            return None

        # Annualize (assuming 5-min intervals, ~105k periods/year)
        # For simplicity, annualize from daily-ish returns
        sharpe = (avg_return - self.risk_free_rate) / std_dev
        return round(sharpe * (252 ** 0.5), 2)  # Annualized

    def get_sortino_ratio(self, periods=30):
        """
        Calculate Sortino ratio (like Sharpe but only penalizes downside).
        """
        if len(self.balance_history) < 3:
            return None

        history = self.balance_history[-periods:]
        returns = []
        for i in range(1, len(history)):
            prev = history[i - 1]["balance"]
            curr = history[i]["balance"]
            if prev > 0:
                returns.append((curr - prev) / prev)

        if len(returns) < 2:
            return None

        avg_return = sum(returns) / len(returns)

        # Downside deviation (only negative returns)
        downside = [r for r in returns if r < 0]
        if not downside:
            return None

        downside_var = sum(r ** 2 for r in downside) / len(downside)
        downside_dev = downside_var ** 0.5

        if downside_dev == 0:
            return None

        sortino = (avg_return - self.risk_free_rate) / downside_dev
        return round(sortino * (252 ** 0.5), 2)

    def get_win_rate(self):
        """Percentage of profitable trades."""
        if not self.trade_pnls:
            return None
        wins = sum(1 for p in self.trade_pnls if p > 0)
        return round(wins / len(self.trade_pnls) * 100, 1)

    def get_profit_factor(self):
        """Gross profit / gross loss. >1 means profitable."""
        if not self.trade_pnls:
            return None
        gross_profit = sum(p for p in self.trade_pnls if p > 0)
        gross_loss = abs(sum(p for p in self.trade_pnls if p < 0))
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else None
        return round(gross_profit / gross_loss, 2)

    def get_summary(self):
        """Full metrics summary."""
        return {
            "sharpe_ratio": self.get_sharpe_ratio(),
            "sortino_ratio": self.get_sortino_ratio(),
            "max_drawdown_pct": round(self.max_drawdown * 100, 1),
            "win_rate_pct": self.get_win_rate(),
            "profit_factor": self.get_profit_factor(),
            "total_trades": len(self.trade_pnls),
            "peak_balance": round(self.peak, 2),
        }
