import ccxt
import json
import time
from config import API_KEY, API_SECRET, EXCHANGE_ID, SANDBOX


def create_exchange():
    """Initialize Bybit exchange connection."""
    exchange_class = getattr(ccxt, EXCHANGE_ID)
    exchange = exchange_class({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {
            "defaultType": "unified",  # Unified trading account
        },
    })

    if SANDBOX:
        exchange.set_sandbox_mode(True)
        print("[!] SANDBOX MODE — no real funds at risk")

    exchange.load_markets()
    return exchange


def get_balance(exchange):
    """Get account balance."""
    balance = exchange.fetch_balance()
    total = balance.get("total", {})
    free = balance.get("free", {})

    print("\n=== Account Balance ===")
    for coin in ["USDT", "BTC", "ETH", "SOL"]:
        if total.get(coin, 0) > 0:
            print(f"  {coin}: free={free.get(coin, 0):.6f}  total={total.get(coin, 0):.6f}")

    return balance


def get_funding_rates(exchange, limit=20):
    """Fetch funding rates for all perpetual contracts, sorted by rate."""
    markets = exchange.markets
    perp_markets = [
        m for m in markets.values()
        if m.get("swap") and m.get("active") and "/USDT" in m["symbol"]
    ]

    funding_rates = []
    for market in perp_markets:
        try:
            ticker = exchange.fetch_funding_rate(market["symbol"])
            rate = ticker.get("fundingRate", 0)
            if rate and abs(rate) > 0.0001:  # Filter noise
                funding_rates.append({
                    "symbol": market["symbol"],
                    "fundingRate": rate,
                    "annualized": rate * 3 * 365 * 100,  # 3 payments/day, annual %
                    "markPrice": ticker.get("markPrice"),
                    "nextFunding": ticker.get("nextFundingTime"),
                })
        except Exception:
            continue

    # Sort by absolute funding rate descending
    funding_rates.sort(key=lambda x: abs(x["fundingRate"]), reverse=True)
    return funding_rates[:limit]


def place_market_order(exchange, symbol, side, amount):
    """Place a market order."""
    print(f"\n[ORDER] {side.upper()} {amount} {symbol}")
    order = exchange.create_order(
        symbol=symbol,
        type="market",
        side=side,
        amount=amount,
    )
    print(f"  Order ID: {order['id']}")
    print(f"  Status: {order['status']}")
    print(f"  Filled: {order.get('filled', 0)} @ avg {order.get('average', 'N/A')}")
    return order


def place_limit_order(exchange, symbol, side, amount, price):
    """Place a limit order."""
    print(f"\n[ORDER] {side.upper()} {amount} {symbol} @ {price}")
    order = exchange.create_order(
        symbol=symbol,
        type="limit",
        side=side,
        amount=amount,
        price=price,
    )
    print(f"  Order ID: {order['id']}")
    print(f"  Status: {order['status']}")
    return order


if __name__ == "__main__":
    print("=== Bybit Trading Bot — Phase 1 ===\n")

    # 1. Connect
    exchange = create_exchange()
    print(f"Connected to {EXCHANGE_ID}")
    print(f"Markets loaded: {len(exchange.markets)}")

    # 2. Check balance
    get_balance(exchange)

    # 3. Scan funding rates
    print("\n=== Top Funding Rates (Perp/USDT) ===")
    rates = get_funding_rates(exchange)
    for r in rates:
        direction = "LONG" if r["fundingRate"] > 0 else "SHORT"
        print(f"  {r['symbol']:15s}  rate={r['fundingRate']:+.6f}  "
              f"APY={r['annualized']:+.1f}%  direction={direction}")

    print("\n[OK] Phase 1 complete — API connection working.")
