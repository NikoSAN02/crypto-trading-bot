"""
Enhanced Backtest — Tests BOTH directions of funding arb + triangular arb.
Uses LIVE Bybit market data. No API keys needed.
"""

import ccxt
import time
import json
from datetime import datetime, timezone


def funding_rate_backtest(exchange, top_n=15, days_back=7, position_size=100):
    """
    Bidirectional funding arb:
    - Positive funding: Buy spot + Short perp (shorts get paid)
    - Negative funding: Long perp only (longs get paid, no spot needed)
    
    This captures profit in BOTH market conditions.
    """
    print("=" * 70)
    print("  BIDIRECTIONAL FUNDING RATE BACKTEST")
    print(f"  Position: ${position_size}  |  Period: {days_back} days")
    print("=" * 70)

    # Scan ALL markets with meaningful rates
    print("\n[1/3] Scanning funding rates...")
    candidates = []
    checked = 0

    for market in exchange.markets.values():
        if not (market.get("swap") and market.get("active") and "/USDT" in market["symbol"]):
            continue
        checked += 1
        if checked > 300:
            break
        try:
            ticker = exchange.fetch_funding_rate(market["symbol"])
            rate = ticker.get("fundingRate", 0)
            if not rate or abs(rate) < 0.00005:
                continue

            spot_symbol = market["symbol"].split(":")[0]
            try:
                spot = exchange.fetch_ticker(spot_symbol)
                vol = spot.get("quoteVolume", 0)
                if vol < 500_000:
                    continue
            except:
                continue

            annualized = rate * 3 * 365 * 100
            candidates.append({
                "symbol": market["symbol"],
                "spot": spot_symbol,
                "rate": rate,
                "annualized": annualized,
                "volume": vol,
                "direction": "LONG" if rate < 0 else "SHORT",
            })
        except:
            continue

    candidates.sort(key=lambda x: abs(x["annualized"]), reverse=True)
    top = candidates[:top_n]

    print(f"  Scanned: {checked}  |  Found: {len(candidates)}  |  Testing: {len(top)}\n")
    print(f"  {'Symbol':20s} {'Current':>10s} {'APY':>10s} {'Vol($M)':>10s} {'Strategy':>10s}")
    print(f"  {'-'*62}")
    for c in top:
        vol_m = c["volume"] / 1e6
        print(f"  {c['symbol']:20s} {c['rate']:+.6f} {c['annualized']:+.1f}% {vol_m:>9.1f}M {c['direction']:>10s}")

    # Fetch historical rates for each
    print(f"\n[2/3] Fetching {days_back}-day history...\n")
    results = []

    for c in top:
        sym = c["symbol"]
        history = exchange.fetch_funding_rate_history(sym, limit=days_back * 3)
        if not history:
            print(f"  {sym:20s} — no history")
            continue

        total_funding = 0
        positive_payments = 0
        negative_payments = 0

        for entry in history:
            r = entry.get("fundingRate", 0)
            if r is None:
                continue
            payment = position_size * r
            total_funding += payment
            if r > 0:
                positive_payments += 1
            else:
                negative_payments += 1

        # Fees: entry + exit
        fees = position_size * 0.0012  # 0.06% * 2
        slippage = position_size * 0.0005
        net = total_funding - fees - slippage
        avg_rate = total_funding / (len(history) * position_size) if history else 0
        apy = avg_rate * 3 * 365 * 100

        winner = net > 0
        results.append({
            "symbol": sym,
            "payments": len(history),
            "pos_payments": positive_payments,
            "neg_payments": negative_payments,
            "total_funding": total_funding,
            "fees": fees,
            "slippage": slippage,
            "net": net,
            "avg_rate": avg_rate,
            "apy": apy,
            "winner": winner,
        })

        status = "✅" if winner else "❌"
        print(f"  {status} {sym:20s} payments={len(history):2d} "
              f"funding=${total_funding:+7.2f} fees=${fees:.2f} net=${net:+6.2f}  APY={apy:+.1f}%")

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'=' * 70}")

    if not results:
        print("  No data.")
        return

    winners = [r for r in results if r["winner"]]
    losers = [r for r in results if not r["winner"]]
    total_invested = len(results) * position_size
    total_net = sum(r["net"] for r in results)

    print(f"\n  Trades:     {len(results)}")
    print(f"  Winners:    {len(winners)} ({len(winners)/len(results)*100:.0f}%)")
    print(f"  Losers:     {len(losers)} ({len(losers)/len(results)*100:.0f}%)")
    print(f"  Invested:   ${total_invested:,.2f}")
    print(f"  Total fees: ${sum(r['fees'] for r in results):.2f}")
    print(f"  Net P&L:    ${total_net:+.2f}")
    print(f"  Return:     {total_net/total_invested*100:+.2f}% ({days_back} days)")
    print(f"  Ann. return:{total_net/total_invested*100*365/days_back:+.1f}%")

    if winners:
        print(f"\n  Best:  {max(winners, key=lambda x: x['net'])['symbol']}  "
              f"${max(winners, key=lambda x: x['net'])['net']:+.2f}")
    if losers:
        print(f"  Worst: {min(losers, key=lambda x: x['net'])['symbol']}  "
              f"${min(losers, key=lambda x: x['net'])['net']:+.2f}")

    # Verdict
    wr = len(winners) / len(results) * 100
    print(f"\n  Verdict: ", end="")
    if total_net > 0 and wr >= 60:
        print("✅ PROFITABLE — Good conditions for trading.")
    elif total_net > 0:
        print("⚠️ MARGINAL — Positive but risky. Small positions only.")
    elif wr >= 40:
        print("⏳ WAIT — Market not ideal. Rates too low/volatile.")
    else:
        print("❌ AVOID — Bad market conditions right now.")

    return results


def triangular_arb_scan(exchange):
    """Scan for triangular arbitrage opportunities on Bybit."""
    print(f"\n{'=' * 70}")
    print(f"  TRIANGULAR ARBITRAGE SCAN")
    print(f"{'=' * 70}")

    # Build triangle pairs: BTC → ETH → USDT → BTC
    triangles = [
        ("BTC/USDT", "ETH/BTC", "ETH/USDT"),
        ("BTC/USDT", "SOL/BTC", "SOL/USDT"),
        ("BTC/USDT", "XRP/BTC", "XRP/USDT"),
        ("ETH/USDT", "SOL/ETH", "SOL/USDT"),
        ("BTC/USDT", "DOGE/BTC", "DOGE/USDT"),
        ("BTC/USDT", "ADA/BTC", "ADA/USDT"),
        ("ETH/USDT", "ARB/ETH", "ARB/USDT"),
        ("BTC/USDT", "AVAX/BTC", "AVAX/USDT"),
        ("ETH/USDT", "LINK/ETH", "LINK/USDT"),
        ("BTC/USDT", "MATIC/BTC", "MATIC/USDT"),
    ]

    print(f"\n  Checking {len(triangles)} triangle paths...\n")
    opportunities = []

    for leg1, leg2, leg3 in triangles:
        try:
            # Check all three markets exist
            if leg1 not in exchange.markets or leg2 not in exchange.markets or leg3 not in exchange.markets:
                continue

            t1 = exchange.fetch_ticker(leg1)
            t2 = exchange.fetch_ticker(leg2)
            t3 = exchange.fetch_ticker(leg3)

            bid1 = t1.get("bid", 0)
            ask1 = t1.get("ask", 0)
            bid2 = t2.get("bid", 0)
            ask2 = t2.get("ask", 0)
            bid3 = t3.get("bid", 0)
            ask3 = t3.get("ask", 0)

            if not all([bid1, ask1, bid2, ask2, bid3, ask3]):
                continue

            # Path A: USDT → BTC → ETH → USDT (buy leg1, sell leg2, sell leg3)
            start = 1000  # $1000
            btc = start / ask1  # Buy BTC with USDT
            eth = btc * bid2    # Sell BTC for ETH
            end_a = eth * bid3  # Sell ETH for USDT
            profit_a = (end_a - start) / start * 100

            # Path B: USDT → ETH → BTC → USDT (buy leg3, buy leg2 reverse, sell leg1)
            eth2 = start / ask3  # Buy ETH with USDT
            btc2 = eth2 / ask2   # Buy BTC with ETH (reverse pair)
            end_b = btc2 * bid1  # Sell BTC for USDT
            profit_b = (end_b - start) / start * 100

            best_profit = max(profit_a, profit_b)
            path = "A" if profit_a > profit_b else "B"

            if best_profit > 0.05:  # >0.05% after fees (roughly)
                opportunities.append({
                    "triangle": f"{leg1} → {leg2} → {leg3}",
                    "path": path,
                    "profit_pct": best_profit,
                    "profit_usd": best_profit * 10,  # per $1000
                })

            status = "✅" if best_profit > 0.05 else "  "
            print(f"  {status} {' → '.join([leg1, leg2, leg3]):45s} profit={best_profit:+.3f}% (${best_profit*10:+.2f}/$1k)")

        except Exception as e:
            print(f"  ❌ {' → '.join([leg1, leg2, leg3]):45s} error: {str(e)[:30]}")

    # Summary
    print(f"\n  Opportunities found: {len(opportunities)}")
    if opportunities:
        opportunities.sort(key=lambda x: x["profit_pct"], reverse=True)
        print(f"\n  Top opportunities:")
        for o in opportunities[:5]:
            print(f"    {o['triangle']}")
            print(f"    Profit: {o['profit_pct']:.3f}% (${o['profit_usd']:.2f} per $1k)")
    else:
        print("  No profitable triangles right now (market is efficient).")
        print("  This is normal — triangular arb windows are rare and short-lived.")

    return opportunities


if __name__ == "__main__":
    print("Connecting to Bybit (live data)...\n")
    exchange = ccxt.bybit({"enableRateLimit": True})
    exchange.load_markets()
    print(f"Loaded {len(exchange.markets)} markets\n")

    # Run funding rate backtest
    funding_results = funding_rate_backtest(exchange, top_n=15, days_back=7)

    # Run triangular arb scan
    tri_results = triangular_arb_scan(exchange)

    # Final summary
    print(f"\n{'=' * 70}")
    print(f"  OVERALL ASSESSMENT")
    print(f"{'=' * 70}")
    print(f"\n  Funding Arb: {'✅ Viable' if funding_results and sum(r['net'] for r in funding_results) > 0 else '⏳ Wait for better rates'}")
    print(f"  Triangular:  {'✅ Found' if tri_results else '⏳ No opportunities now'}")
    print(f"\n  Recommendation: ", end="")
    if funding_results and sum(r['net'] for r in funding_results) > 0:
        print("Start with funding arb. Use small positions ($50-100).")
        print("Monitor rates daily — exit when APY drops below 10%.")
    else:
        print("Current market not ideal for either strategy.")
        print("Set up the bot, keep it scanning, and it will auto-trade")
        print("when conditions improve. Best rates usually appear during")
        print("volatile market moves (pumps or dumps).")
