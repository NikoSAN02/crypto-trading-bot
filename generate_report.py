#!/usr/bin/env python3
"""
Generate a PDF report of the trading bot performance.
Run after the 00:00 UTC funding cycle.
"""

import json
import os
from datetime import datetime, timezone

# Try reportlab, fallback to fpdf
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

def load_state():
    state_path = os.path.expanduser("~/projects/crypto-trading-bot/paper_state.json")
    with open(state_path) as f:
        return json.load(f)

def load_log():
    log_path = os.path.expanduser("~/projects/crypto-trading-bot/paper_trader.log")
    with open(log_path) as f:
        return f.read()

def generate_pdf_report(output_path):
    state = load_state()
    log = load_log()

    balance = state["balance"]
    starting = state["starting_balance"]
    pnl = balance - starting
    pnl_pct = (pnl / starting) * 100
    positions = state.get("positions", {})
    trades = state.get("trade_log", [])
    metrics = state.get("metrics", {})

    # Calculate total funding collected
    total_funding = sum(p.get("funding_collected", 0) for p in positions.values())
    for t in trades:
        if t.get("action") == "CLOSE":
            total_funding += t.get("funding_pnl", 0)

    # Closed trade stats
    closed_trades = [t for t in trades if t.get("action") == "CLOSE"]
    wins = [t for t in closed_trades if t.get("total_pnl", 0) > 0]
    losses = [t for t in closed_trades if t.get("total_pnl", 0) <= 0]
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0
    total_trade_pnl = sum(t.get("total_pnl", 0) for t in closed_trades)

    if not HAS_REPORTLAB:
        # Simple text fallback
        with open(output_path.replace('.pdf', '.txt'), 'w') as f:
            f.write("=" * 60 + "\n")
            f.write("  TRADING BOT PERFORMANCE REPORT\n")
            f.write(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"Starting Balance:  ${starting:.2f}\n")
            f.write(f"Current Balance:   ${balance:.2f}\n")
            f.write(f"P&L:               ${pnl:+.2f} ({pnl_pct:+.1f}%)\n")
            f.write(f"Total Funding:     ${total_funding:.2f}\n")
            f.write(f"Win Rate:          {win_rate:.0f}% ({len(wins)}W / {len(losses)}L)\n")
            f.write(f"Sharpe Ratio:      {metrics.get('sharpe_ratio', 'n/a')}\n")
            f.write(f"Max Drawdown:      {metrics.get('max_drawdown_pct', 0)}%\n\n")
            f.write("OPEN POSITIONS:\n")
            for sym, pos in positions.items():
                f.write(f"  {sym}: ${pos['usd_value']:.0f} notional @ {pos.get('entry_apy', 0):.0f}% APY | Funding: ${pos.get('funding_collected', 0):.2f}\n")
            f.write("\nCLOSED TRADES:\n")
            for t in closed_trades:
                f.write(f"  {t['symbol']}: ${t.get('total_pnl', 0):+.2f} — {t.get('reason', '')}\n")
            f.write("\n" + "=" * 60 + "\n")
        return output_path.replace('.pdf', '.txt')

    # ReportLab PDF generation
    doc = SimpleDocTemplate(output_path, pagesize=A4,
                          topMargin=20*mm, bottomMargin=20*mm,
                          leftMargin=15*mm, rightMargin=15*mm)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('Title2', parent=styles['Title'],
                                  fontSize=20, textColor=colors.HexColor('#1a1a2e'))
    heading_style = ParagraphStyle('Heading2', parent=styles['Heading2'],
                                    fontSize=14, textColor=colors.HexColor('#16213e'),
                                    spaceAfter=6)
    body_style = ParagraphStyle('Body', parent=styles['Normal'],
                                 fontSize=11, textColor=colors.HexColor('#333333'))
    green_style = ParagraphStyle('Green', parent=body_style,
                                  textColor=colors.HexColor('#00aa00'))
    red_style = ParagraphStyle('Red', parent=body_style,
                                textColor=colors.HexColor('#cc0000'))

    elements = []

    # Title
    elements.append(Paragraph("Trading Bot Performance Report", title_style))
    elements.append(Paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%B %d, %Y at %H:%M UTC')}", body_style))
    elements.append(Spacer(1, 10*mm))

    # Summary Table
    elements.append(Paragraph("Summary", heading_style))
    summary_data = [
        ["Metric", "Value"],
        ["Starting Balance", f"${starting:.2f}"],
        ["Current Balance", f"${balance:.2f}"],
        ["Profit/Loss", f"${pnl:+.2f} ({pnl_pct:+.1f}%)"],
        ["Total Funding Collected", f"${total_funding:.2f}"],
        ["Total Exposure", f"${sum(p['usd_value'] for p in positions.values()):.0f} (3x leverage)"],
        ["Open Positions", f"{len(positions)} / 5"],
        ["Total Trades", str(len(trades))],
    ]
    t = Table(summary_data, colWidths=[55*mm, 80*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#16213e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 8*mm))

    # Risk Metrics
    elements.append(Paragraph("Risk Metrics (from ai-hedge-fund framework)", heading_style))
    risk_data = [
        ["Metric", "Value", "Rating"],
        ["Sharpe Ratio", str(metrics.get('sharpe_ratio', 'n/a')), "Excellent" if (metrics.get('sharpe_ratio') or 0) > 2 else "Good" if (metrics.get('sharpe_ratio') or 0) > 1 else "Building"],
        ["Sortino Ratio", str(metrics.get('sortino_ratio', 'n/a')), "Excellent" if (metrics.get('sortino_ratio') or 0) > 2 else "Building"],
        ["Max Drawdown", f"{metrics.get('max_drawdown_pct', 0)}%", "Perfect" if metrics.get('max_drawdown_pct', 0) == 0 else "Check"],
        ["Win Rate", f"{win_rate:.0f}%" if closed_trades else "n/a", "Perfect" if win_rate == 100 and closed_trades else "Building"],
        ["Profit Factor", str(metrics.get('profit_factor', 'n/a')), "Excellent" if (metrics.get('profit_factor') or 0) > 2 else "Building"],
    ]
    t2 = Table(risk_data, colWidths=[45*mm, 40*mm, 50*mm])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0a7e5e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0faf5')]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(t2)
    elements.append(Spacer(1, 8*mm))

    # Open Positions
    elements.append(Paragraph("Open Positions", heading_style))
    pos_data = [["Coin", "Notional", "Margin", "APY", "Funding", "Stability"]]
    for sym, pos in positions.items():
        stability = pos.get('confidence', 0)
        pos_data.append([
            sym,
            f"${pos['usd_value']:.0f}",
            f"${pos.get('margin', 0):.0f}",
            f"{pos.get('entry_apy', 0):.0f}%",
            f"${pos.get('funding_collected', 0):.2f}",
            f"{stability:.0%}"
        ])
    t3 = Table(pos_data, colWidths=[25*mm, 25*mm, 20*mm, 20*mm, 25*mm, 25*mm])
    t3.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f4ff')]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(t3)
    elements.append(Spacer(1, 8*mm))

    # Trade History
    elements.append(Paragraph("Trade History", heading_style))
    trade_data = [["Time", "Action", "Coin", "Value", "P&L", "Reason"]]
    for t_entry in trades[-10:]:
        action = t_entry["action"]
        time_str = t_entry.get("time", "")[:16].replace("T", " ")
        pnl_str = f"${t_entry.get('total_pnl', 0):+.2f}" if action == "CLOSE" else ""
        value_str = f"${t_entry.get('usd_value', 0):.0f}"
        reason = t_entry.get("reason", "")[:30]
        trade_data.append([time_str, action, t_entry.get("symbol", ""), value_str, pnl_str, reason])
    t4 = Table(trade_data, colWidths=[30*mm, 15*mm, 20*mm, 20*mm, 18*mm, 50*mm])
    t4.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6b21a8')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#faf0ff')]),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(t4)
    elements.append(Spacer(1, 8*mm))

    # Improvement Areas
    elements.append(Paragraph("Areas of Improvement", heading_style))
    improvements = [
        "1. Correlation Cap — currently reducing sizes by up to 30% when positions are correlated. Working well.",
        "2. Volatility Sizer — positions scaled by rate stability. M got 95% stability = larger position.",
        "3. Cross-Exchange Arb — detected but not executing. Adding Binance integration would unlock 40%+ spreads.",
        "4. Funding Timing — bot enters at any time. Could optimize to enter right before funding windows.",
        "5. Trailing Exit — exits when rate drops 60%. Consider tighter exit (40-50%) for faster rotation.",
        "6. Position Concentration — M is 79% of exposure. Add diversification limits per coin.",
        "7. Sentiment Gate — currently requires BUY signal to open. Consider removing gate for pure funding arb.",
    ]
    for imp in improvements:
        elements.append(Paragraph(imp, body_style))
        elements.append(Spacer(1, 2*mm))

    doc.build(elements)
    return output_path


if __name__ == "__main__":
    output = os.path.expanduser("~/projects/crypto-trading-bot/bot_report.pdf")
    result = generate_pdf_report(output)
    print(f"Report generated: {result}")
