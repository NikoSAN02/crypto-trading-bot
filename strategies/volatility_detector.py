"""
Volatility Detector — Find coins primed for big moves (either direction).

Strategies:
  1. Range Compression — coins trading in tight range after a trend, breakout imminent
  2. Funding Extremes — extremely positive/negative funding = contrarian play
  3. Volume Anomalies — sudden volume spikes without price movement = coiled spring
  4. Divergence Detection — price up but funding negative (or vice versa) = reversal signal

For "market-proof" trading: these setups profit from the MOVE, not the DIRECTION.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone


class VolatilityDetector:
    def __init__(self, config=None):
        self.config = config or {}

        # Compression detection
        self.min_volume_usd = self.config.get("min_volume", 10_000_000)  # $10M minimum
        self.compression_ratio = self.config.get("compression_ratio", 0.3)  # 24h range < 30% of 7d range
        self.volume_spike_mult = self.config.get("volume_spike", 2.0)  # 2x normal volume

    def _fetch_json(self, url, timeout=10):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-bot/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"  [VOLATILITY] Fetch error: {e}")
            return None

    def find_compressed_coins(self):
        """
        Find coins where 24h range is compressed relative to 7d range.
        These are coiled springs — likely to break hard in either direction.
        """
        url = ("https://api.coingecko.com/api/v3/coins/markets?"
               "vs_currency=usd&order=volume_desc&per_page=50&page=1"
               "&sparkline=true&price_change_percentage=24h,7d")
        data = self._fetch_json(url)
        if not data:
            return []

        compressed = []
        for coin in data:
            symbol = coin["symbol"].upper()
            # Skip stablecoins
            if symbol in ("USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE", "USD1",
                          "RLUSD", "PYUSD"):
                continue

            vol = coin.get("total_volume") or 0
            if vol < self.min_volume_usd:
                continue

            high_24h = coin.get("high_24h", 0)
            low_24h = coin.get("low_24h", 0)
            price = coin.get("current_price", 0)
            sparkline = coin.get("sparkline_in_7d", {}).get("price", [])

            if not (high_24h and low_24h and price and len(sparkline) > 20):
                continue

            # 24h range as % of price
            range_24h_pct = ((high_24h - low_24h) / price) * 100

            # 7d range from sparkline
            spark_min = min(sparkline)
            spark_max = max(sparkline)
            range_7d_pct = ((spark_max - spark_min) / spark_min) * 100 if spark_min > 0 else 100

            # Compression: 24h range is small relative to 7d range
            if range_7d_pct > 5:  # Only meaningful if 7d had decent movement
                compression = range_24h_pct / range_7d_pct if range_7d_pct > 0 else 1
            else:
                continue

            if compression < self.compression_ratio:
                change_7d = coin.get("price_change_percentage_7d_in_currency") or 0
                compressed.append({
                    "symbol": symbol,
                    "price": price,
                    "range_24h_pct": round(range_24h_pct, 2),
                    "range_7d_pct": round(range_7d_pct, 2),
                    "compression": round(compression, 3),
                    "change_7d": round(change_7d, 1),
                    "volume": vol,
                    "setup": "compression_breakout",
                    "direction": "either",
                    "confidence": min(0.9, 1 - compression),  # Lower compression = higher confidence
                })

        compressed.sort(key=lambda x: x["confidence"], reverse=True)
        return compressed[:10]

    def find_funding_extremes(self):
        """
        Find coins with extreme funding rates — contrarian plays.
        Very positive funding = shorts get paid, crowded long → squeeze up or dump
        Very negative funding = longs get paid, crowded short → squeeze down or pump
        Either way, expect a big move.
        """
        data = self._fetch_json("https://api.bybit.com/v5/market/tickers?category=linear")
        if not data or not data.get("result", {}).get("list"):
            return []

        results = data["result"]["list"]
        active = [r for r in results
                  if r.get("turnover24h") and float(r["turnover24h"]) > self.min_volume_usd]

        extremes = []
        for r in active:
            rate = float(r.get("fundingRate", 0))
            apy = rate * 3 * 365 * 100
            change_24h = float(r.get("price24hPcnt", 0)) * 100
            symbol = r["symbol"].replace("USDT", "")

            # Extreme positive: longs are crowded
            if apy > 100:
                extremes.append({
                    "symbol": symbol,
                    "funding_apy": round(apy, 0),
                    "change_24h": round(change_24h, 1),
                    "setup": "funding_extreme_positive",
                    "direction": "either",
                    "note": "Crowded long — could squeeze up or dump. Watch for reversal.",
                    "confidence": min(0.9, apy / 500),
                })
            # Extreme negative: shorts are crowded
            elif apy < -50:
                extremes.append({
                    "symbol": symbol,
                    "funding_apy": round(apy, 0),
                    "change_24h": round(change_24h, 1),
                    "setup": "funding_extreme_negative",
                    "direction": "either",
                    "note": "Crowded short — could squeeze down or pump. Watch for reversal.",
                    "confidence": min(0.9, abs(apy) / 300),
                })

        extremes.sort(key=lambda x: x["confidence"], reverse=True)
        return extremes[:10]

    def find_volume_anomalies(self):
        """
        Find coins with unusual volume but minimal price movement.
        High volume + tight range = accumulation/distribution → big move incoming.
        """
        url = ("https://api.coingecko.com/api/v3/coins/markets?"
               "vs_currency=usd&order=volume_desc&per_page=50&page=1&sparkline=false"
               "&price_change_percentage=24h")
        data = self._fetch_json(url)
        if not data:
            return []

        # Calculate median volume
        volumes = [c.get("total_volume") or 0 for c in data if c.get("total_volume")]
        if not volumes:
            return []
        volumes.sort()
        median_vol = volumes[len(volumes) // 2]

        anomalies = []
        for coin in data:
            symbol = coin["symbol"].upper()
            if symbol in ("USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE", "USD1",
                          "RLUSD", "PYUSD"):
                continue

            vol = coin.get("total_volume") or 0
            change_24h = abs(coin.get("price_change_percentage_24h") or 0)

            # High volume, low price movement
            if vol > median_vol * self.volume_spike_mult and change_24h < 2.0:
                high_24h = coin.get("high_24h", 0)
                low_24h = coin.get("low_24h", 0)
                price = coin.get("current_price", 0)

                range_pct = 0
                if high_24h and low_24h and price:
                    range_pct = ((high_24h - low_24h) / price) * 100

                anomalies.append({
                    "symbol": symbol,
                    "price": price,
                    "volume": vol,
                    "volume_vs_median": round(vol / max(median_vol, 1), 1),
                    "change_24h": round(change_24h, 1),
                    "range_24h_pct": round(range_pct, 2),
                    "setup": "volume_anomaly",
                    "direction": "either",
                    "confidence": min(0.8, (vol / max(median_vol, 1)) / 10),
                })

        anomalies.sort(key=lambda x: x["confidence"], reverse=True)
        return anomalies[:5]

    def scan_all(self):
        """Run all detectors and return combined results."""
        print("\n[VOLATILITY] Scanning for breakout setups...")

        compressed = self.find_compressed_coins()
        print(f"  Compressed coins: {len(compressed)}")

        funding_extremes = self.find_funding_extremes()
        print(f"  Funding extremes: {len(funding_extremes)}")

        volume_anomalies = self.find_volume_anomalies()
        print(f"  Volume anomalies: {len(volume_anomalies)}")

        all_setups = compressed + funding_extremes + volume_anomalies
        all_setups.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        return {
            "compressed": compressed,
            "funding_extremes": funding_extremes,
            "volume_anomalies": volume_anomalies,
            "all_setups": all_setups[:15],
        }

    def format_setups(self, results):
        """Format volatility setups for display."""
        lines = [f"\n{'='*55}", "  VOLATILITY DETECTION — Breakout Setups", f"{'='*55}"]

        if results["compressed"]:
            lines.append("\n  COMPRESSION BREAKOUTS (tight range, big move coming):")
            for s in results["compressed"][:5]:
                lines.append(f"    {s['symbol']:<10} 24h range: {s['range_24h_pct']:.1f}%  "
                             f"7d range: {s['range_7d_pct']:.1f}%  "
                             f"confidence: {s['confidence']:.0%}")

        if results["funding_extremes"]:
            lines.append("\n  FUNDING EXTREMES (contrarian plays):")
            for s in results["funding_extremes"][:5]:
                lines.append(f"    {s['symbol']:<10} APY: {s['funding_apy']:+.0f}%  "
                             f"24h: {s['change_24h']:+.1f}%  {s['note']}")

        if results["volume_anomalies"]:
            lines.append("\n  VOLUME ANOMALIES (high vol, low move = coiled spring):")
            for s in results["volume_anomalies"][:5]:
                lines.append(f"    {s['symbol']:<10} vol: {s['volume_vs_median']:.1f}x median  "
                             f"24h: {s['change_24h']:+.1f}%  range: {s['range_24h_pct']:.1f}%")

        if not any([results["compressed"], results["funding_extremes"], results["volume_anomalies"]]):
            lines.append("\n  No setups detected — market is quiet, wait for action.")

        lines.append(f"{'='*55}")
        return "\n".join(lines)
