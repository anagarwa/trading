#!/usr/bin/env python3
"""
scripts/status.py — Portfolio Status Dashboard

Run at any time (locally or in CI) to see the bot's current state.

Usage:
    # Offline — reads only state/portfolio_state.json and logs/trading_log.csv
    python scripts/status.py

    # Live — connects to the configured broker and fetches real-time quotes
    python scripts/status.py --live
"""

import csv
import json
import os
import sys
from datetime import datetime

# Allow running from the project root  (python scripts/status.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PORTFOLIO_FILE = "state/portfolio_state.json"
LOG_FILE = "logs/trading_log.csv"
SEP = "─" * 64


def _load_portfolio() -> dict:
    if not os.path.exists(PORTFOLIO_FILE):
        print("No portfolio state file found. Has the bot run yet?")
        print(f"Expected: {PORTFOLIO_FILE}")
        sys.exit(1)
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def _fetch_live_quotes(holdings: list[dict]) -> dict[str, float]:
    """Connect to the configured broker and return {symbol: ltp} for held stocks."""
    if not holdings:
        return {}
    try:
        from broker import get_broker
        broker = get_broker()
        broker.connect()
        quotes = {}
        for h in holdings:
            try:
                q = broker.get_quote(h["symbol"])
                quotes[h["symbol"]] = q["ltp"]
            except Exception as e:
                print(f"  Warning: could not fetch live quote for {h['symbol']}: {e}")
        return quotes
    except Exception as e:
        print(f"  Warning: broker connection failed ({e}). Showing offline data only.")
        return {}


def _recent_trades(n: int = 15) -> list[dict]:
    if not os.path.exists(LOG_FILE):
        return []
    rows = []
    with open(LOG_FILE, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("action", "").replace("_DRY", "") in ("BUY", "SELL"):
                rows.append(row)
    return rows[-n:]


def _pnl_colour(val: float) -> str:
    """Return a simple +/- prefixed string."""
    return f"+₹{val:,.2f}" if val >= 0 else f"-₹{abs(val):,.2f}"


def print_status(portfolio: dict, live_quotes: dict | None = None):
    live = bool(live_quotes)
    mode_label = "LIVE QUOTES" if live else "OFFLINE (state file only)"

    print()
    print(SEP)
    print(f"  TRADING BOT STATUS  —  {mode_label}")
    print(f"  Last state update : {portfolio.get('last_updated', 'unknown')}")
    print(SEP)

    # ── Capital summary ─────────────────────────────────────────────────────
    cap      = portfolio["capital_remaining"]
    profit   = portfolio["profit_booked"]
    losses   = portfolio["total_losses_taken"]
    holdings = portfolio.get("holdings", [])

    invested = sum(h["amount_invested"] for h in holdings)

    print()
    print("  CAPITAL SUMMARY")
    print(f"  {'Available trading capital:':<32} ₹{cap:>10,.2f}")
    print(f"  {'Amount currently invested:':<32} ₹{invested:>10,.2f}")
    print(f"  {'Gross portfolio value (approx):':<32} ₹{cap + invested:>10,.2f}")
    print(f"  {'Profit booked (never reinvested):':<32} ₹{profit:>10,.2f}")
    print(f"  {'Total losses absorbed:':<32} ₹{losses:>10,.2f}")
    net = profit - losses
    print(f"  {'Net realised P&L:':<32} {_pnl_colour(net):>11}")

    # ── Current holdings ─────────────────────────────────────────────────────
    print()
    print(f"  CURRENT HOLDINGS  ({len(holdings)} open position{'s' if len(holdings) != 1 else ''})")

    if not holdings:
        print("  No open positions. Bot is in cash.")
    else:
        header = f"  {'Symbol':<14} {'Qty':>5}  {'Buy Price':>10}  {'Invested':>10}  {'LTP':>11}  {'Unrealised P&L':>16}  {'P&L %':>7}"
        print(header)
        print("  " + "─" * (len(header) - 2))

        total_unrealised = 0.0
        for h in holdings:
            sym       = h["symbol"]
            qty       = h["quantity"]
            buy_p     = h["buy_price"]
            amt_inv   = h["amount_invested"]
            buy_date  = h.get("buy_date", "?")

            if live_quotes and sym in live_quotes:
                ltp      = live_quotes[sym]
                pnl      = round((ltp - buy_p) * qty, 2)
                pnl_pct  = ((ltp - buy_p) / buy_p) * 100
                total_unrealised += pnl
                ltp_str  = f"₹{ltp:>9,.2f}"
                pnl_str  = _pnl_colour(pnl)
                pct_str  = f"{pnl_pct:>+.2f}%"
            else:
                ltp_str  = "N/A"
                pnl_str  = "N/A"
                pct_str  = "N/A"

            print(f"  {sym:<14} {qty:>5}  ₹{buy_p:>9,.2f}  ₹{amt_inv:>9,.2f}  {ltp_str:>11}  {pnl_str:>16}  {pct_str:>7}")
            print(f"  {'':14}  Purchased: {buy_date}")

        if live_quotes:
            print("  " + "─" * (len(header) - 2))
            print(f"  {'TOTAL UNREALISED P&L':>64}  {_pnl_colour(total_unrealised):>16}")

    # ── Recent trades ────────────────────────────────────────────────────────
    trades = _recent_trades()
    print()
    print(f"  RECENT TRADES (last {len(trades)})")

    if not trades:
        print("  No trades recorded yet.")
    else:
        print(f"  {'Timestamp':<28}  {'Session':<8}  {'Action':<10}  {'Symbol':<12}  {'Price':>10}  {'P&L':>12}  Reason")
        print("  " + "─" * 100)
        for row in trades:
            price_str = f"₹{float(row['price']):,.2f}" if row.get("price") else "—"
            pnl_val   = row.get("pnl", "")
            pnl_str   = (_pnl_colour(float(pnl_val)) if pnl_val and pnl_val not in ("", "0", "0.0") else "—")
            print(
                f"  {row.get('timestamp','')[:27]:<28}  "
                f"{row.get('run_type',''):<8}  "
                f"{row.get('action',''):<10}  "
                f"{row.get('symbol',''):<12}  "
                f"{price_str:>10}  "
                f"{pnl_str:>12}  "
                f"{row.get('reason','')}"
            )

    print()
    print(SEP)
    print()


if __name__ == "__main__":
    live_mode = "--live" in sys.argv
    portfolio = _load_portfolio()

    live_quotes: dict[str, float] = {}
    if live_mode:
        print("Fetching live quotes from broker…")
        live_quotes = _fetch_live_quotes(portfolio.get("holdings", []))

    print_status(portfolio, live_quotes if live_mode else None)
