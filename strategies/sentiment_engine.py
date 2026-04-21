"""
Sentiment Engine — Market sentiment analysis for trading decisions.

Pulls data from:
  - Fear & Greed Index (alternative.me) — crypto market sentiment
  - Bybit funding rates (public API) — positioning bias
  - CoinGecko (public API) — volume, volatility, trending coins

Produces composite signals:
  STRONG_BUY  — Extreme fear + positive funding = high-conviction setup
  BUY         — Fear + reasonable funding = accumulate
  NEUTRAL     — No clear edge, stay in reserve
  SELL        — Greed + negative funding = take profit
  STRONG_SELL — Extreme greed = close everything
"""

import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone


class SentimentEngine:
    def __init__(self, config=None):
        self.config = config or {}

        # Thresholds
        self.extreme_fear_threshold = self.config.get("extreme_fear", 25)
        self.fear_threshold = self.config.get("fear", 40)
        self.greed_threshold = self.config.get("greed", 60)
        self.extreme_greed_threshold = self.config.get("extreme_greed", 75)

        # Funding rate thresholds (annualized)
        self.high_funding_apy = self.config.get("high_funding_apy", 15.0)
        self.low_funding_apy = self.config.get("low_funding_apy", -10.0)

        # Cache
        self._fng_cache = None
        self._funding_cache = None
        self._market_cache = None
        self._last_fetch = {}
        self.cache_ttl = 300  # 5 minutes

        # History for divergence detection
        self.signal_history = []

    def _fetch_json(self, url, timeout=10):
        """Fetch JSON from URL with error handling."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-bot/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            print(f"  [SENTIMENT] Fetch error for {url}: {e}")
            return None

    def _is_fresh(self, key):
        """Check if cached data is still fresh."""
        last = self._last_fetch.get(key, 0)
        return (time.time() - last) < self.cache_ttl

    # ─── Data Sources ──────────────────────────────────────────

    def fetch_fear_greed(self):
        """Fetch Fear & Greed Index (current + 7-day history)."""
        if self._is_fresh("fng") and self._fng_cache:
            return self._fng_cache

        data = self._fetch_json("https://api.alternative.me/fng/?limit=7")
        if not data or "data" not in data:
            return self._fng_cache  # Return stale cache if available

        entries = data["data"]
        current = {
            "value": int(entries[0]["value"]),
            "classification": entries[0]["value_classification"],
            "timestamp": int(entries[0]["timestamp"]),
        }

        # Calculate trend (7-day average vs current)
        values = [int(e["value"]) for e in entries]
        avg_7d = sum(values) / len(values)
        trend = "improving" if current["value"] > avg_7d else "deteriorating"

        # Detect divergence: extreme fear improving = bullish signal
        was_extreme = any(v <= self.extreme_fear_threshold for v in values[1:])

        self._fng_cache = {
            "current": current,
            "avg_7d": round(avg_7d, 1),
            "trend": trend,
            "was_extreme": was_extreme,
            "history": [{"value": int(e["value"]), "date": datetime.fromtimestamp(
                int(e["timestamp"]), tz=timezone.utc).strftime("%b %d")} for e in entries],
        }
        self._last_fetch["fng"] = time.time()
        return self._fng_cache

    def fetch_funding_rates(self, exchange=None):
        """Fetch funding rates from Bybit public API."""
        if self._is_fresh("funding") and self._funding_cache:
            return self._funding_cache

        # Try public Bybit API first (no auth needed)
        data = self._fetch_json("https://api.bybit.com/v5/market/tickers?category=linear")
        if data and data.get("result", {}).get("list"):
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
                    "apy": apy,
                    "turnover": float(r.get("turnover24h", 0)),
                    "price_change_24h": float(r.get("price24hPcnt", 0)) * 100,
                })

            rates.sort(key=lambda x: abs(x["apy"]), reverse=True)

            # Classify overall funding bias
            top_10 = rates[:10]
            avg_apy = sum(r["apy"] for r in top_10) / len(top_10) if top_10 else 0
            positive_count = sum(1 for r in top_10 if r["apy"] > 5)

            if avg_apy > 20:
                bias = "bullish_overheated"  # Longs paying too much
            elif avg_apy > 5:
                bias = "bullish"
            elif avg_apy < -20:
                bias = "bearish_overheated"  # Shorts paying too much
            elif avg_apy < -5:
                bias = "bearish"
            else:
                bias = "neutral"

            self._funding_cache = {
                "rates": rates[:30],
                "top_positive": [r for r in rates if r["apy"] > self.high_funding_apy][:5],
                "top_negative": [r for r in rates if r["apy"] < self.low_funding_apy][:5],
                "avg_apy": round(avg_apy, 1),
                "bias": bias,
                "positive_count": positive_count,
            }
            self._last_fetch["funding"] = time.time()
            return self._funding_cache

        # Fallback: use ccxt if exchange object provided
        if exchange:
            return self._fetch_funding_ccxt(exchange)

        return self._funding_cache

    def _fetch_funding_ccxt(self, exchange):
        """Fallback: fetch funding rates via ccxt."""
        rates = []
        try:
            for market in list(exchange.markets.values())[:100]:
                if not (market.get("swap") and market.get("active") and "/USDT" in market["symbol"]):
                    continue
                try:
                    ticker = exchange.fetch_funding_rate(market["symbol"])
                    rate = ticker.get("fundingRate", 0)
                    if rate is None:
                        continue
                    apy = rate * 3 * 365 * 100
                    rates.append({
                        "symbol": market["base"],
                        "funding_rate": rate,
                        "apy": apy,
                        "turnover": 0,
                        "price_change_24h": 0,
                    })
                except Exception:
                    continue
        except Exception:
            pass

        rates.sort(key=lambda x: abs(x["apy"]), reverse=True)
        self._funding_cache = {
            "rates": rates[:30],
            "top_positive": [r for r in rates if r["apy"] > self.high_funding_apy][:5],
            "top_negative": [r for r in rates if r["apy"] < self.low_funding_apy][:5],
            "avg_apy": round(sum(r["apy"] for r in rates[:10]) / max(len(rates[:10]), 1), 1),
            "bias": "neutral",
            "positive_count": sum(1 for r in rates[:10] if r["apy"] > 5),
        }
        self._last_fetch["funding"] = time.time()
        return self._funding_cache

    def fetch_market_overview(self):
        """Fetch top coins by volume from CoinGecko."""
        if self._is_fresh("market") and self._market_cache:
            return self._market_cache

        url = ("https://api.coingecko.com/api/v3/coins/markets?"
               "vs_currency=usd&order=volume_desc&per_page=30&page=1&sparkline=false"
               "&price_change_percentage=24h,7d")
        data = self._fetch_json(url)
        if not data:
            return self._market_cache

        coins = []
        for c in data:
            coins.append({
                "symbol": c["symbol"].upper(),
                "price": c["current_price"],
                "change_24h": c.get("price_change_percentage_24h") or 0,
                "change_7d": c.get("price_change_percentage_7d_in_currency") or 0,
                "volume": c.get("total_volume") or 0,
            })

        # Detect market-wide moves
        non_stable = [c for c in coins if c["symbol"] not in
                      ("USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE", "USD1",
                       "RLUSD", "PYUSD")]
        avg_24h = sum(c["change_24h"] for c in non_stable[:15]) / max(len(non_stable[:15]), 1)
        avg_7d = sum(c["change_7d"] for c in non_stable[:15]) / max(len(non_stable[:15]), 1)

        # Find outliers — coins moving hard against the trend
        outliers_high = [c for c in non_stable if abs(c["change_7d"]) > 30][:5]
        outliers_low = [c for c in non_stable if c["change_7d"] < -15][:5]

        self._market_cache = {
            "coins": coins,
            "non_stable": non_stable[:20],
            "avg_change_24h": round(avg_24h, 2),
            "avg_change_7d": round(avg_7d, 2),
            "outliers_high": outliers_high,
            "outliers_low": outliers_low,
        }
        self._last_fetch["market"] = time.time()
        return self._market_cache

    # ─── Signal Generation ─────────────────────────────────────

    def generate_signal(self, exchange=None):
        """Generate composite trading signal from all data sources."""
        fng = self.fetch_fear_greed()
        funding = self.fetch_funding_rates(exchange)
        market = self.fetch_market_overview()

        score = 0  # -100 (extreme bearish) to +100 (extreme bullish)
        reasons = []

        if not fng:
            return {"signal": "NEUTRAL", "score": 0, "reasons": ["Fear & Greed data unavailable"]}

        # ── Fear & Greed Component (weight: 40%) ──
        fng_value = fng["current"]["value"]
        if fng_value <= self.extreme_fear_threshold:
            score += 40
            reasons.append(f"Extreme Fear ({fng_value}) — historical buy signal")
        elif fng_value <= self.fear_threshold:
            score += 20
            reasons.append(f"Fear ({fng_value}) — accumulating zone")
        elif fng_value >= self.extreme_greed_threshold:
            score -= 40
            reasons.append(f"Extreme Greed ({fng_value}) — take profit zone")
        elif fng_value >= self.greed_threshold:
            score -= 20
            reasons.append(f"Greed ({fng_value}) — cautious")
        else:
            reasons.append(f"Neutral sentiment ({fng_value})")

        # Trend bonus
        if fng["trend"] == "improving" and fng["was_extreme"]:
            score += 15
            reasons.append("Sentiment recovering from extreme fear — bullish divergence")

        # ── Funding Rate Component (weight: 35%) ──
        if funding:
            bias = funding["bias"]
            if bias == "bearish_overheated":
                score += 30
                reasons.append(f"Bearish overcrowded (avg {funding['avg_apy']:.0f}% APY) — short squeeze likely")
            elif bias == "bearish":
                score += 15
                reasons.append(f"Bearish positioning (avg {funding['avg_apy']:.0f}% APY)")
            elif bias == "bullish_overheated":
                score -= 30
                reasons.append(f"Bullish overcrowded (avg {funding['avg_apy']:.0f}% APY) — long squeeze risk")
            elif bias == "bullish":
                score -= 15
                reasons.append(f"Bullish positioning (avg {funding['avg_apy']:.0f}% APY)")
            else:
                reasons.append(f"Funding rates neutral (avg {funding['avg_apy']:.0f}% APY)")

            # Funding arb opportunities
            if funding["top_positive"]:
                best = funding["top_positive"][0]
                reasons.append(f"Best funding arb: {best['symbol']} at {best['apy']:.0f}% APY")
        else:
            reasons.append("Funding rate data unavailable")

        # ── Market Momentum Component (weight: 25%) ──
        if market:
            avg_24h = market["avg_change_24h"]
            avg_7d = market["avg_change_7d"]

            if avg_7d < -10:
                score += 20
                reasons.append(f"Market down {avg_7d:.1f}% (7d) — potential bottom")
            elif avg_7d > 15:
                score -= 15
                reasons.append(f"Market up {avg_7d:.1f}% (7d) — overheated")
            elif avg_24h > 5:
                score -= 5
                reasons.append(f"Strong 24h bounce (+{avg_24h:.1f}%) — chasing risk")
            else:
                reasons.append(f"Market steady (24h: {avg_24h:+.1f}%, 7d: {avg_7d:+.1f}%)")
        else:
            reasons.append("Market data unavailable")

        # ── Composite Signal ──
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

        result = {
            "signal": signal,
            "score": score,
            "reasons": reasons,
            "fng": fng["current"] if fng else None,
            "funding_bias": funding["bias"] if funding else "unknown",
            "top_arb": funding["top_positive"][0] if funding and funding["top_positive"] else None,
            "market_avg_7d": market["avg_change_7d"] if market else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.signal_history.append(result)
        return result

    def get_position_multiplier(self, signal):
        """
        Return a position size multiplier based on sentiment.
        0.0 = don't trade, 1.0 = normal, 1.5 = aggressive, 0.5 = conservative
        """
        multipliers = {
            "STRONG_BUY": 1.5,
            "BUY": 1.0,
            "NEUTRAL": 0.5,
            "SELL": 0.3,
            "STRONG_SELL": 0.0,
        }
        return multipliers.get(signal["signal"], 0.5)

    def should_open_funding_arb(self, signal):
        """Decide if funding arb should be opened based on sentiment."""
        if signal["signal"] in ("STRONG_BUY", "BUY"):
            return True, "Sentiment supports opening positions"
        if signal["signal"] == "NEUTRAL" and signal.get("top_arb"):
            arb = signal["top_arb"]
            if arb["apy"] > 50:  # High APY overrides neutral sentiment
                return True, f"High APY override: {arb['symbol']} at {arb['apy']:.0f}%"
        return False, f"Sentiment too weak: {signal['signal']} (score: {signal['score']})"

    def should_close_positions(self, signal):
        """Decide if positions should be closed based on sentiment."""
        if signal["signal"] == "STRONG_SELL":
            return True, "Extreme greed — close all"
        if signal["signal"] == "SELL" and signal["score"] < -50:
            return True, "Strong greed — close most"
        return False, ""

    # ─── Display ───────────────────────────────────────────────

    def format_signal(self, signal):
        """Format signal for display."""
        icons = {
            "STRONG_BUY": "🟢🟢 STRONG BUY",
            "BUY": "🟢 BUY",
            "NEUTRAL": "⚪ NEUTRAL",
            "SELL": "🔴 SELL",
            "STRONG_SELL": "🔴🔴 STRONG SELL",
        }
        icon = icons.get(signal["signal"], signal["signal"])

        lines = [
            f"{'='*50}",
            f"  SENTIMENT SIGNAL: {icon}  (score: {signal['score']:+d})",
            f"{'='*50}",
        ]

        if signal.get("fng"):
            lines.append(f"  Fear & Greed: {signal['fng']['value']} ({signal['fng']['classification']})")
        lines.append(f"  Funding Bias: {signal.get('funding_bias', '?')}")
        if signal.get("market_avg_7d") is not None:
            lines.append(f"  Market 7d: {signal['market_avg_7d']:+.1f}%")

        lines.append("")
        lines.append("  Reasons:")
        for r in signal.get("reasons", []):
            lines.append(f"    - {r}")

        if signal.get("top_arb"):
            arb = signal["top_arb"]
            lines.append(f"\n  Best Funding Arb: {arb['symbol']} @ {arb['apy']:.0f}% APY")

        lines.append(f"{'='*50}")
        return "\n".join(lines)
