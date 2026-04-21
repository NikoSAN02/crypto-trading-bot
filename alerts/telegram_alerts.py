"""
Telegram Alerts — Send trade notifications via existing Hermes Telegram bot.
"""

import json
import os
from datetime import datetime, timezone


def send_alert(message, chat_id=None):
    """
    Send alert via hermes gateway or direct Telegram API.
    For now, just print to console. Can be extended with actual Telegram API.
    """
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    formatted = f"[{timestamp}] {message}"
    print(formatted)
    return formatted


def format_funding_opportunity(opp):
    """Format a funding opportunity for display."""
    return (
        f"\U0001f3af {opp['symbol']}\n"
        f"   Rate: {opp['fundingRate']:+.6f} ({opp['annualized']:+.1f}% APY)\n"
        f"   Volume: ${opp['volume_24h']:,.0f}\n"
        f"   Next funding: {opp.get('nextFunding', 'N/A')}"
    )


def format_position_open(pos):
    """Format an opened position for display."""
    return (
        f"\u2705 OPENED: {pos['symbol']}\n"
        f"   Size: {pos['amount']} \u2248 ${pos['usd_value']:.2f}\n"
        f"   Entry rate: {pos['entry_annualized']:+.1f}% APY"
    )


def format_position_close(pos, reason=""):
    """Format a closed position for display."""
    return (
        f"\u274c CLOSED: {pos.get('symbol', '?')}\n"
        f"   Reason: {reason}\n"
        f"   Size: ${pos.get('usd_value', 0):.2f}"
    )


def format_risk_report(report):
    """Format risk report for display."""
    status = "\u2705 SAFE" if report["safe_to_trade"] else "\U0001f6a8 HALTED"
    return (
        f"\U0001f4ca Risk Report [{status}]\n"
        f"   Balance: ${report['total_balance']:,.2f}\n"
        f"   Available: ${report['available_for_trading']:,.2f}\n"
        f"   Drawdown: {report['drawdown_pct']:.1f}% (limit: {report['max_drawdown_limit']}%)"
    )


def format_sentiment_signal(signal):
    """Format sentiment signal for alerts."""
    icons = {
        "STRONG_BUY": "\U0001f7e2\U0001f7e2",
        "BUY": "\U0001f7e2",
        "NEUTRAL": "\u26aa",
        "SELL": "\U0001f534",
        "STRONG_SELL": "\U0001f534\U0001f534",
    }
    icon = icons.get(signal["signal"], "")

    fng = signal.get("fng", {})
    lines = [
        f"{icon} Sentiment: {signal['signal']} (score: {signal['score']:+d})",
        f"   Fear & Greed: {fng.get('value', '?')} ({fng.get('classification', '?')})",
        f"   Funding: {signal.get('funding_bias', '?')}",
    ]

    if signal.get("top_arb"):
        arb = signal["top_arb"]
        lines.append(f"   Best arb: {arb['symbol']} @ {arb['apy']:.0f}% APY")

    return "\n".join(lines)


def format_volatility_alert(setup):
    """Format a volatility setup for alerts."""
    direction_icon = "\U0001f525" if setup["confidence"] > 0.6 else "\U0001f4a1"
    return (
        f"{direction_icon} {setup['symbol']} — {setup['setup']}\n"
        f"   Direction: {setup['direction']}\n"
        f"   Confidence: {setup['confidence']:.0%}\n"
        f"   {setup.get('note', '')}"
    )
