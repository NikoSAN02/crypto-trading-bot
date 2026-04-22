"""
Signal Scanner — Pre-pump/crash detection engine.

Scans for early signals using FREE APIs (no keys needed):
  1. Volume anomaly (Binance/CoinGecko) — unusual buying before price moves
  2. Funding extremes (Binance Futures) — crowded positions = squeeze incoming
  3. OI divergence (Binance Futures) — rising OI + flat price = explosion loading
  4. Long/Short ratio (Binance Futures) — smart money vs retail positioning
  5. Fear & Greed (Alternative.me) — extreme fear = contrarian buy

Each coin gets a 0-100 Pump Score:
  >80   = STRONG BUY (multiple signals align)
  60-79 = MODERATE BUY
  40-59 = NEUTRAL
  20-39 = CAUTION
  <20   = STRONG SELL
"""

import json
import time
import urllib.request
import statistics
from datetime import datetime, timezone


class SignalScanner:
    """Multi-signal anomaly detector for pre-pump/crash identification."""

    def __init__(self, config=None):
        self.config = config or {}
        self.cache = {}
        self.cache_ttl = 120  # 2 minutes
        self.scan_history = []

    def _fetch_json(self, url, timeout=10):
        """Fetch JSON with error handling."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "signal-scanner/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            return None

    def _is_fresh(self, key):
        return key in self.cache and (time.time() - self.cache[key].get("_ts", 0)) < self.cache_ttl

    # ─── Signal 1: Volume Anomaly ──────────────────────────────

    def scan_volume(self):
        """Detect unusual volume spikes via Binance 24hr tickers."""
        if self._is_fresh("volume"):
            return self.cache["volume"]["data"]

        data = self._fetch_json("https://api.binance.com/api/v3/ticker/24hr")
        if not data:
            return []

        results = []
        for item in data:
            symbol = item["symbol"]
            if not symbol.endswith("USDT"):
                continue
            base = symbol.replace("USDT", "")
            # Skip stablecoins and wrapped tokens
            if base in ("USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE", "WBTC", "STETH",
                        "USD1", "RLUSD", "PYUSD", "USDD", "GUSD", "FRAX", "LUSD", "SUSD",
                        "CUSD", "DOLA", "EURS", "JEUR", "XSGD", "BIDR", "IDRT", "BRZ",
                        "NGN", "TRYB", "QC", "HUSD", "UST", "USTC", "EURN",
                        "U", "EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD",
                        "BTCDOM", "DEFI", "MEME"):
                continue

            try:
                volume_usd = float(item["quoteVolume"])
                price_change = float(item["priceChangePercent"])
                trades = int(item.get("count", 0))
            except (ValueError, KeyError):
                continue

            if volume_usd < 1_000_000:  # Skip illiquid (<$1M daily)
                continue

            # Volume/MC proxy: high volume relative to price change = accumulation
            # If volume is high but price barely moved → someone is loading up
            if abs(price_change) > 0.1:
                volume_price_ratio = volume_usd / (abs(price_change) * 1_000_000)
            else:
                volume_price_ratio = volume_usd / 1_000_000  # Flat price, volume matters

            results.append({
                "symbol": base,
                "volume_usd": volume_usd,
                "price_change_pct": price_change,
                "trades": trades,
                "volume_price_ratio": round(volume_price_ratio, 2),
            })

        # Score: top volume coins get higher score, especially with flat price
        results.sort(key=lambda x: x["volume_usd"], reverse=True)
        for i, r in enumerate(results[:100]):
            rank_score = max(0, 100 - i)  # #1 = 100, #100 = 0
            # Bonus: high volume + low price change = accumulation (bullish)
            if abs(r["price_change_pct"]) < 2 and r["volume_usd"] > 50_000_000:
                rank_score = min(100, rank_score + 20)
            r["volume_score"] = rank_score

        self.cache["volume"] = {"data": results[:100], "_ts": time.time()}
        return results[:100]

    # ─── Signal 2: Funding Rate Extremes ───────────────────────

    def scan_funding(self):
        """Detect extreme funding rates via Binance Futures."""
        if self._is_fresh("funding"):
            return self.cache["funding"]["data"]

        # Current funding rates
        data = self._fetch_json("https://fapi.binance.com/fapi/v1/premiumIndex")
        if not data:
            return []

        results = []
        for item in data:
            symbol = item["symbol"]
            if not symbol.endswith("USDT"):
                continue
            base = symbol.replace("USDT", "")

            try:
                rate = float(item.get("lastFundingRate", 0))
                mark_price = float(item.get("markPrice", 0))
            except (ValueError, KeyError):
                continue

            if mark_price <= 0:
                continue

            apy = rate * 3 * 365 * 100

            # Score: extreme negative = bullish (short squeeze incoming)
            #        extreme positive = bearish (long squeeze incoming)
            if rate < -0.001:  # Very negative → shorts paying longs → squeeze setup
                score = min(100, int(abs(rate) * 100000))
            elif rate > 0.001:  # Very positive → longs paying shorts → overheated
                score = max(0, 100 - int(rate * 100000))
            else:
                score = 50  # Neutral

            results.append({
                "symbol": base,
                "funding_rate": rate,
                "apy": round(apy, 1),
                "mark_price": mark_price,
                "funding_score": score,
            })

        results.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)
        self.cache["funding"] = {"data": results, "_ts": time.time()}
        return results

    # ─── Signal 3: Open Interest Divergence ───────────────────

    def scan_open_interest(self):
        """Detect rising OI + flat price = explosion loading."""
        if self._is_fresh("oi"):
            return self.cache["oi"]["data"]

        # Get top symbols first
        tickers = self._fetch_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
        if not tickers:
            return []

        top_symbols = []
        for t in tickers:
            if not t["symbol"].endswith("USDT"):
                continue
            try:
                vol = float(t["quoteVolume"])
                if vol > 50_000_000:  # >$50M daily volume
                    top_symbols.append(t["symbol"])
            except (ValueError, KeyError):
                continue

        top_symbols = top_symbols[:50]  # Scan top 50 by volume

        results = []
        for symbol in top_symbols:
            oi_data = self._fetch_json(
                f"https://fapi.binance.com/futures/data/openInterestHist"
                f"?symbol={symbol}&period=1h&limit=24"
            )
            if not oi_data or len(oi_data) < 2:
                continue

            try:
                current_oi = float(oi_data[-1]["sumOpenInterest"])
                prev_oi = float(oi_data[0]["sumOpenInterest"])
                oi_change_pct = ((current_oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0

                # Get price change for divergence detection
                price_start = float(oi_data[0].get("sumOpenInterestValue", 0)) / max(prev_oi, 1)
                price_end = float(oi_data[-1].get("sumOpenInterestValue", 0)) / max(current_oi, 1)
                price_change_pct = ((price_end - price_start) / price_start * 100) if price_start > 0 else 0
            except (ValueError, KeyError, ZeroDivisionError):
                continue

            base = symbol.replace("USDT", "")

            # Score: rising OI + flat price = explosion loading (bullish)
            #        rising OI + rising price = trend confirmation
            #        falling OI + rising price = weak rally (bearish)
            if oi_change_pct > 5 and abs(price_change_pct) < 2:
                score = min(100, 70 + int(oi_change_pct))  # Divergence = high score
            elif oi_change_pct > 5 and price_change_pct > 0:
                score = min(100, 60 + int(oi_change_pct / 2))  # Trend confirmation
            elif oi_change_pct < -5 and price_change_pct > 0:
                score = max(0, 30 - int(abs(oi_change_pct)))  # Weak rally
            else:
                score = 50

            results.append({
                "symbol": base,
                "oi_change_pct": round(oi_change_pct, 2),
                "price_change_pct": round(price_change_pct, 2),
                "current_oi": current_oi,
                "oi_score": score,
            })

        results.sort(key=lambda x: abs(x["oi_change_pct"]), reverse=True)
        self.cache["oi"] = {"data": results, "_ts": time.time()}
        return results

    # ─── Signal 4: Long/Short Ratio ───────────────────────────

    def scan_long_short_ratio(self):
        """Detect smart money positioning via L/S ratio."""
        if self._is_fresh("ls_ratio"):
            return self.cache["ls_ratio"]["data"]

        # Get top trader L/S ratio
        top_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                       "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
                       "MATICUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT",
                       "PEPEUSDT", "SUIUSDT", "AAVEUSDT", "OPUSDT", "LTCUSDT",
                       "FILUSDT", "RENDERUSDT", "INJUSDT", "STXUSDT", "FETUSDT",
                       "ATOMUSDT", "WIFUSDT", "TIAUSDT", "SEIUSDT", "ORDIUSDT"]

        results = []
        for symbol in top_symbols:
            data = self._fetch_json(
                f"https://fapi.binance.com/futures/data/topLongShortPositionRatio"
                f"?symbol={symbol}&period=1h&limit=24"
            )
            if not data or len(data) < 2:
                continue

            try:
                current_ratio = float(data[-1]["longShortRatio"])
                prev_ratio = float(data[0]["longShortRatio"])
                ratio_change = current_ratio - prev_ratio

                base = symbol.replace("USDT", "")

                # Score: low ratio + rising = smart money going long
                #        high ratio + falling = smart money exiting
                if current_ratio < 0.8 and ratio_change > 0.1:
                    score = min(100, 70 + int(ratio_change * 100))  # Smart money buying
                elif current_ratio > 2.0 and ratio_change < -0.1:
                    score = max(0, 30 + int(ratio_change * 100))  # Smart money selling
                elif ratio_change > 0.2:
                    score = min(100, 60 + int(ratio_change * 50))  # Bullish shift
                elif ratio_change < -0.2:
                    score = max(0, 40 + int(ratio_change * 50))  # Bearish shift
                else:
                    score = 50

                results.append({
                    "symbol": base,
                    "ls_ratio": round(current_ratio, 3),
                    "ratio_change": round(ratio_change, 3),
                    "ls_score": score,
                })
            except (ValueError, KeyError, ZeroDivisionError):
                continue

        self.cache["ls_ratio"] = {"data": results, "_ts": time.time()}
        return results

    # ─── Signal 5: Fear & Greed ────────────────────────────────

    def scan_fear_greed(self):
        """Fetch Fear & Greed Index."""
        if self._is_fresh("fng"):
            return self.cache["fng"]

        data = self._fetch_json("https://api.alternative.me/fng/?limit=7")
        if not data or "data" not in data:
            return self.cache.get("fng", {"value": 50, "score": 50})

        entries = data["data"]
        values = [int(e["value"]) for e in entries]
        current = values[0]
        avg_7d = sum(values) / len(values)

        # Score: extreme fear = bullish (contrarian), extreme greed = bearish
        # Invert: fear 0 → score 100, greed 100 → score 0
        score = max(0, min(100, 100 - current))

        # Bonus: recovering from extreme fear
        was_extreme = any(v <= 25 for v in values[1:])
        if was_extreme and current > 30:
            score = min(100, score + 15)

        result = {
            "value": current,
            "classification": entries[0]["value_classification"],
            "avg_7d": round(avg_7d, 1),
            "trend": "improving" if current > avg_7d else "deteriorating",
            "fng_score": score,
        }
        self.cache["fng"] = result
        return result

    # ─── Composite Score ───────────────────────────────────────

    def full_scan(self):
        """
        Run all 5 signals and produce composite Pump Score for top coins.

        Returns list of {symbol, composite_score, signals} sorted by score.
        """
        print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}] Running signal scan...")

        # Fetch all signals (cached where possible)
        volume_data = self.scan_volume()
        funding_data = self.scan_funding()
        oi_data = self.scan_open_interest()
        ls_data = self.scan_long_short_ratio()
        fng = self.scan_fear_greed()

        print(f"  Volume: {len(volume_data)} coins")
        print(f"  Funding: {len(funding_data)} coins")
        print(f"  OI: {len(oi_data)} coins")
        print(f"  L/S: {len(ls_data)} coins")
        print(f"  Fear & Greed: {fng['value']} ({fng['classification']})")

        # Index signals by symbol
        vol_scores = {r["symbol"]: r for r in volume_data}
        fund_scores = {r["symbol"]: r for r in funding_data}
        oi_scores = {r["symbol"]: r for r in oi_data}
        ls_scores = {r["symbol"]: r for r in ls_data}

        # Collect all unique symbols
        all_symbols = set()
        all_symbols.update(vol_scores.keys())
        all_symbols.update(fund_scores.keys())
        all_symbols.update(oi_scores.keys())
        all_symbols.update(ls_scores.keys())

        # Calculate composite score for each symbol
        results = []
        for symbol in all_symbols:
            signals = {}
            weights_used = 0
            weighted_sum = 0

            # Volume (weight: 0.25)
            if symbol in vol_scores:
                signals["volume"] = vol_scores[symbol].get("volume_score", 50)
                weighted_sum += signals["volume"] * 0.25
                weights_used += 0.25

            # Funding (weight: 0.25)
            if symbol in fund_scores:
                signals["funding"] = fund_scores[symbol].get("funding_score", 50)
                weighted_sum += signals["funding"] * 0.25
                weights_used += 0.25

            # OI (weight: 0.20)
            if symbol in oi_scores:
                signals["oi"] = oi_scores[symbol].get("oi_score", 50)
                weighted_sum += signals["oi"] * 0.20
                weights_used += 0.20

            # L/S Ratio (weight: 0.15)
            if symbol in ls_scores:
                signals["ls_ratio"] = ls_scores[symbol].get("ls_score", 50)
                weighted_sum += signals["ls_ratio"] * 0.15
                weights_used += 0.15

            # Fear & Greed applies to all (weight: 0.15)
            signals["fng"] = fng["fng_score"]
            weighted_sum += signals["fng"] * 0.15
            weights_used += 0.15

            # Normalize
            if weights_used > 0:
                composite = round(weighted_sum / weights_used, 1)
            else:
                composite = 50

            # Coverage penalty: coins with fewer signals get discounted
            coverage = weights_used / 1.0  # 1.0 = all 5 signals present
            if coverage < 0.5:
                composite *= 0.8  # 20% penalty if <50% coverage
            elif coverage < 0.7:
                composite *= 0.9  # 10% penalty if <70% coverage
            composite = round(composite, 1)

            # Classify
            if composite >= 80:
                classification = "STRONG_BUY"
            elif composite >= 60:
                classification = "BUY"
            elif composite >= 40:
                classification = "NEUTRAL"
            elif composite >= 20:
                classification = "CAUTION"
            else:
                classification = "STRONG_SELL"

            # Get price info
            price_info = vol_scores.get(symbol, {})

            results.append({
                "symbol": symbol,
                "composite_score": composite,
                "classification": classification,
                "signals": signals,
                "volume_usd": price_info.get("volume_usd", 0),
                "price_change_pct": price_info.get("price_change_pct", 0),
                "funding_apy": fund_scores[symbol]["apy"] if symbol in fund_scores else 0,
            })

        results.sort(key=lambda x: x["composite_score"], reverse=True)

        # Store scan history
        self.scan_history.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "top_5": [(r["symbol"], r["composite_score"], r["classification"]) for r in results[:5]],
            "fng": fng["value"],
            "coins_scanned": len(results),
        })

        return results

    def format_results(self, results, top_n=10):
        """Format scan results for display."""
        lines = [
            "=" * 60,
            "  SIGNAL SCANNER — Pre-Pump Detection",
            "=" * 60,
        ]

        for r in results[:top_n]:
            direction = "🟢" if r["composite_score"] >= 60 else "🔴" if r["composite_score"] < 40 else "⚪"
            lines.append(
                f"  {direction} {r['symbol']:<10} Score: {r['composite_score']:>5.1f}  "
                f"{r['classification']:<12} Vol: ${r['volume_usd']/1e6:.0f}M  "
                f"Δ24h: {r['price_change_pct']:+.1f}%"
            )
            # Show individual signals
            sig = r["signals"]
            sig_str = " | ".join(
                f"{k}:{v:.0f}" for k, v in sig.items()
            )
            lines.append(f"       Signals: {sig_str}")

        lines.append("=" * 60)
        return "\n".join(lines)
