import logging

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def _send(message: str):
    """Send a Telegram message. Silently skipped if credentials are not configured."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.debug("Telegram notification sent.")
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")


def notify_buy(symbol: str, quantity: int, price: float, amount_invested: float, capital_remaining: float):
    _send(
        f"<b>✅ BUY</b>\n"
        f"Stock: <b>{symbol}</b>\n"
        f"Qty: {quantity} @ ₹{price:,.2f}\n"
        f"Invested: ₹{amount_invested:,.2f}\n"
        f"Capital Remaining: ₹{capital_remaining:,.2f}"
    )


def notify_sell(symbol: str, reason: str, pnl: float, capital_remaining: float, profit_booked_total: float):
    pnl_str = f"+₹{pnl:,.2f}" if pnl >= 0 else f"-₹{abs(pnl):,.2f}"
    emoji = "💚" if pnl >= 0 else "🔴"
    _send(
        f"<b>{emoji} SELL</b>\n"
        f"Stock: <b>{symbol}</b>\n"
        f"Reason: {reason}\n"
        f"P&L: {pnl_str}\n"
        f"Capital Remaining: ₹{capital_remaining:,.2f}\n"
        f"Total Profit Booked: ₹{profit_booked_total:,.2f}"
    )


def notify_skip(reason: str):
    _send(f"<b>⏭ SKIP</b>\n{reason}")


def notify_error(error_msg: str):
    _send(f"<b>🚨 ERROR</b>\n{error_msg}")
