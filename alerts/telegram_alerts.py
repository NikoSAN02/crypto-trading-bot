"""
Telegram Alerts — Send trade notifications via existing Hermes Telegram bot.
"""

import json
import os


def send_alert(message, chat_id=None):
    """
    Send alert via hermes gateway or direct Telegram API.
    For now, just print to console. Can be extended with actual Telegram API.
    """
    timestamp = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).strftime("%H:%M:%S UTC")
    formatted = f"[{timestamp}] {message}"
    print(formatted)
    return formatted


def format_funding_opportunity(opp):
    """Format a funding opportunity for display."""
    return (
        f"🎯 {opp['symbol']}\n"
        f"   Rate: {opp['fundingRate']:+.6f} ({opp['annualized']:+.1f}% APY)\n"
        f"   Volume: ${opp['volume_24h']:,.0f}\n"
        f"   Next funding: {opp.get('nextFunding', 'N/A')}"
    )


def format_position_open(pos):
    """Format an opened position for display."""
    return (
        f"✅ OPENED: {pos['symbol']}\n"
        f"   Size: {pos['amount']} ≈ ${pos['usd_value']:.2f}\n"
        f"   Entry rate: {pos['entry_annualized']:+.1f}% APY"
    )


def format_position_close(pos, reason=""):
    """Format a closed position for display."""
    return (
        f"❌ CLOSED: {pos['symbol']}\n"
        f"   Reason: {reason}\n"
        f"   Size: ${pos['usd_value']:.2f}"
    )


def format_risk_report(report):
    """Format risk report for display."""
    status = "✅ SAFE" if report["safe_to_trade"] else "🚨 HALTED"
    return (
        f"📊 Risk Report [{status}]\n"
        f"   Balance: ${report['total_balance']:,.2f}\n"
        f"   Available: ${report['available_for_trading']:,.2f}\n"
        f"   Drawdown: {report['drawdown_pct']:.1f}% (limit: {report['max_drawdown_limit']}%)"
    )
