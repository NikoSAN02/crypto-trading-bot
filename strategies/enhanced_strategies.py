"""
Enhanced Trading Strategies — Improvements for higher success rate.

New strategies:
  1. Funding Timing — enter right before funding payment window
  2. Rate Stability — only enter coins with stable (not spiking) rates
  3. Trailing Exit — close early when rate drops 50% from entry
  4. Cross-Exchange Arb — compare Bybit vs Binance funding rates
  5. Rate Momentum — track rate direction (rising vs falling)
"""

import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta


class RateTracker:
    """Track funding rate history for stability analysis."""

    def __init__(self, max_history=12):
        self.history = {}  # {symbol: [(timestamp, rate), ...]}
        self.max_history = max_history

    def record(self, symbol, rate):
        """Record a funding rate observation."""
        if symbol not in self.history:
            self.history[symbol] = []
        self.history[symbol].append((time.time(), rate))
        # Keep only recent
        if len(self.history[symbol]) > self.max_history:
            self.history[symbol] = self.history[symbol][-self.max_history:]

    def get_stability(self, symbol):
        """
        Return stability score 0-1 for a coin.
        1.0 = rate has been very stable (low variance)
        0.0 = rate is jumping around wildly
        """
        entries = self.history.get(symbol, [])
        if len(entries) < 3:
            return 0.5  # Not enough data

        rates = [r for _, r in entries]
        avg = sum(rates) / len(rates)
        if avg == 0:
            return 0.5

        # Coefficient of variation
        variance = sum((r - avg) ** 2 for r in rates) / len(rates)
        std_dev = variance ** 0.5
        cv = abs(std_dev / avg)

        # Lower CV = more stable
        # CV of 0.1 = 90% stable, CV of 1.0 = 0% stable
        stability = max(0, min(1, 1 - cv))
        return round(stability, 2)

    def get_momentum(self, symbol):
        """
        Return rate momentum: 'rising', 'falling', or 'flat'.
        Compares last 3 observations.
        """
        entries = self.history.get(symbol, [])
        if len(entries) < 4:
            return "unknown"

        recent = [r for _, r in entries[-3:]]
        older = [r for _, r in entries[-6:-3]] if len(entries) >= 6 else [entries[0][1]]

        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)

        if older_avg == 0:
            return "unknown"

        change = (recent_avg - older_avg) / abs(older_avg)
        if change > 0.2:
            return "rising"
        elif change < -0.2:
            return "falling"
        return "flat"

    def was_recently_high(self, symbol, threshold_apy=30, window_minutes=60):
        """Check if rate was above threshold recently (spike detection)."""
        entries = self.history.get(symbol, [])
        cutoff = time.time() - (window_minutes * 60)
        for ts, rate in entries:
            if ts > cutoff:
                apy = rate * 3 * 365 * 100
                if apy > threshold_apy:
                    return True
        return False


class FundingTimer:
    """Optimal timing around Bybit's 8-hour funding windows."""

    # Bybit funding times (UTC)
    FUNDING_HOURS = [0, 8, 16]

    @staticmethod
    def minutes_until_funding():
        """Minutes until next funding payment."""
        now = datetime.now(timezone.utc)
        current_hour = now.hour

        # Find next funding hour
        next_hour = None
        for h in FundingTimer.FUNDING_HOURS:
            if h > current_hour:
                next_hour = h
                break
        if next_hour is None:
            next_hour = FundingTimer.FUNDING_HOURS[0] + 24  # Tomorrow

        next_funding = now.replace(hour=next_hour % 24, minute=0, second=0, microsecond=0)
        if next_hour >= 24:
            next_funding += timedelta(days=1)

        delta = next_funding - now
        return int(delta.total_seconds() / 60)

    @staticmethod
    def is_good_entry_window(minutes_before=30):
        """Is it a good time to enter? (within X min before funding)"""
        mins = FundingTimer.minutes_until_funding()
        return mins <= minutes_before

    @staticmethod
    def should_wait_for_window(minutes_threshold=60):
        """Should we wait for the next funding window?"""
        mins = FundingTimer.minutes_until_funding()
        # If funding is >60 min away, might be worth waiting
        # If <60 min, just enter now
        return mins > minutes_threshold

    @staticmethod
    def get_timing_info():
        """Return human-readable timing info."""
        mins = FundingTimer.minutes_until_funding()
        hours = mins // 60
        remaining = mins % 60

        if mins <= 15:
            return f"OPTIMAL ENTRY — funding in {mins} min"
        elif mins <= 30:
            return f"Good entry window — funding in {mins} min"
        elif mins <= 60:
            return f"OK to enter — funding in {mins} min"
        else:
            return f"Consider waiting — funding in {hours}h {remaining}m"


def fetch_cross_exchange_rates():
    """
    Compare funding rates across Bybit and Binance.
    Returns dict with spread info for coins on both exchanges.
    """
    rates = {"bybit": {}, "binance": {}}

    # Bybit
    try:
        req = urllib.request.Request(
            "https://api.bybit.com/v5/market/tickers?category=linear",
            headers={"User-Agent": "crypto-bot/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            for r in data.get("result", {}).get("list", []):
                sym = r["symbol"].replace("USDT", "")
                rate = float(r.get("fundingRate", 0))
                turnover = float(r.get("turnover24h", 0))
                if turnover > 10_000_000:
                    rates["bybit"][sym] = {
                        "rate": rate,
                        "apy": rate * 3 * 365 * 100,
                        "turnover": turnover,
                    }
    except Exception:
        pass

    # Binance
    try:
        req = urllib.request.Request(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            headers={"User-Agent": "crypto-bot/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            for item in data:
                sym = item["symbol"]
                if not sym.endswith("USDT"):
                    continue
                base = sym.replace("USDT", "")
                # Get funding rate separately
            # Binance funding needs a different endpoint
        req2 = urllib.request.Request(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            headers={"User-Agent": "crypto-bot/1.0"}
        )
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            data2 = json.loads(resp2.read().decode())
            for item in data2:
                sym = item["symbol"]
                if not sym.endswith("USDT"):
                    continue
                base = sym.replace("USDT", "")
                rate = float(item.get("lastFundingRate", 0))
                rates["binance"][base] = {
                    "rate": rate,
                    "apy": rate * 3 * 365 * 100,
                }
    except Exception:
        pass

    # Find spreads
    spreads = []
    for coin in rates["bybit"]:
        if coin in rates["binance"]:
            bb = rates["bybit"][coin]
            bn = rates["binance"][coin]
            spread = bb["apy"] - bn["apy"]
            if abs(spread) > 10:  # Only meaningful spreads
                spreads.append({
                    "coin": coin,
                    "bybit_apy": round(bb["apy"], 1),
                    "binance_apy": round(bn["apy"], 1),
                    "spread": round(spread, 1),
                    "best_exchange": "bybit" if spread > 0 else "binance",
                    "turnover": bb.get("turnover", 0),
                })

    spreads.sort(key=lambda x: abs(x["spread"]), reverse=True)
    return {
        "spreads": spreads[:10],
        "bybit_count": len(rates["bybit"]),
        "binance_count": len(rates["binance"]),
    }


def should_enter_enhanced(opp, rate_tracker, timing_check=True):
    """
    Enhanced entry check with stability, momentum, and timing.

    Returns: (should_enter: bool, reason: str, confidence: float)
    """
    symbol = opp["symbol"]
    apy = opp["apy"]
    reasons = []
    confidence = 0.5

    # 1. Rate stability check
    stability = rate_tracker.get_stability(symbol)
    if stability < 0.3:
        return False, f"Rate too unstable (stability: {stability:.0%})", 0.1
    if stability > 0.7:
        confidence += 0.2
        reasons.append(f"stable rate ({stability:.0%})")

    # 2. Rate momentum check
    momentum = rate_tracker.get_momentum(symbol)
    if momentum == "falling":
        confidence -= 0.2
        reasons.append("WARNING: rate falling")
    elif momentum == "rising":
        confidence += 0.1
        reasons.append("rate rising")

    # 3. Spike detection — was rate recently much higher?
    if rate_tracker.was_recently_high(symbol, threshold_apy=50):
        confidence -= 0.15
        reasons.append("recent spike detected — may revert")

    # 4. Avoid coins pumping hard (likely to reverse)
    if abs(opp.get("change_24h", 0)) > 50:
        return False, f"24h change too extreme ({opp['change_24h']:+.0f}%)", 0.1

    # 5. Timing check
    if timing_check:
        timer = FundingTimer()
        mins = timer.minutes_until_funding()
        if mins <= 15:
            confidence += 0.15
            reasons.append(f"optimal timing ({mins}m to funding)")
        elif mins <= 30:
            confidence += 0.1
            reasons.append(f"good timing ({mins}m to funding)")
        elif mins > 90:
            confidence -= 0.1
            reasons.append(f"long wait ({mins}m to funding)")

    # 6. Minimum APY — must be high enough to beat fees
    # At $15 margin × 3x = $45 notional, fees = $0.054 round-trip
    # Need at least 2 funding payments to be profitable
    # 80% APY × $45 × (8/24/365) × 2 = $0.07 > $0.054 fees ✓
    min_apy = 80 if confidence > 0.4 else 120
    if apy < min_apy:
        return False, f"APY {apy:.0f}% too low (need {min_apy}%+)", confidence

    confidence = max(0.1, min(0.95, confidence))
    reason = " | ".join(reasons) if reasons else "OK"
    return True, reason, confidence


def should_exit_enhanced(pos, current_rate, entry_rate, funding_collected):
    """
    Enhanced exit check — hold through funding, only exit when truly dead.

    Returns: (should_exit: bool, reason: str)
    """
    symbol = pos["symbol"]
    entry_apy = pos["entry_apy"]
    current_apy = current_rate * 3 * 365 * 100

    # 1. Rate dropped below minimum — dead position
    if current_apy < 20:
        return True, f"APY collapsed ({current_apy:.0f}%)"

    # 2. Rate dropped 70% from entry (was 60% — more patient)
    if entry_apy > 0 and current_apy > 0:
        decay = 1 - (current_apy / entry_apy)
        if decay > 0.70:
            return True, f"Rate decayed {decay:.0%} ({entry_apy:.0f}% → {current_apy:.0f}%)"

    # 3. Rate flipped sign
    if (entry_rate > 0 and current_rate < 0) or (entry_rate < 0 and current_rate > 0):
        return True, f"Rate flipped ({current_rate:+.6f})"

    # 4. Funding collected 3x fees AND rate declining — take profit
    fees = pos.get("fee_paid", 0) * 2  # entry + estimated exit fee
    if funding_collected > fees * 3 and current_apy < entry_apy * 0.4:
        return True, f"Profit secured (${funding_collected:.2f}), rate dying"

    return False, "Hold"
