"""
main.py — Trading Agent Entry Point

Called by GitHub Actions (via Cloudflare Worker scheduler) as:
    python main.py run        # Every 2 hours starting 9:20 AM IST

The bot auto-detects whether it's before or after 3 PM IST:
  - Before 3 PM: full analysis (stop-loss, profit-target, rotation, buy)
  - After 3 PM:  sell-only (stop-loss, profit-target, EOD exit — no new buys)

Two independent pools are processed in each run:
  - nifty50:    trades from Nifty 50 constituents   (₹5,000 default)
  - smallcap50: trades from Nifty Smallcap 50       (₹5,000 default)
"""

import csv
import logging
import os
import sys
from datetime import datetime

import pytz

from config import ACTIVE_BROKER, DRY_RUN
from broker import get_broker
from agent.market_research import MarketResearch
from agent.notifications import notify_buy, notify_error, notify_sell, notify_skip
from agent.portfolio import Portfolio, PoolPortfolio
from agent.risk_manager import RiskManager
from constants import NIFTY50_SYMBOLS, NIFTY_SMALLCAP_50_SYMBOLS

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Run-type validation
# ---------------------------------------------------------------------------
if len(sys.argv) < 2:
    logger.error("Usage: python main.py <run>")
    sys.exit(1)

RUN_TYPE = sys.argv[1].strip().lower()
if RUN_TYPE not in ("run", "morning", "midday", "eod"):
    logger.error(f"Invalid RUN_TYPE '{RUN_TYPE}'. Expected: run (or legacy: morning|midday|eod)")
    sys.exit(1)

IST = pytz.timezone("Asia/Kolkata")

# Determine sell-only mode: after 3 PM IST, no new buys
_now_ist = datetime.now(IST)
SELL_ONLY = _now_ist.hour >= 15
# Legacy compatibility: explicit eod run_type also forces sell-only
if RUN_TYPE == "eod":
    SELL_ONLY = True
LOG_FILE = "logs/trading_log.csv"
LOG_HEADERS = [
    "timestamp", "run_type", "broker", "action",
    "symbol", "quantity", "price", "order_id",
    "reason", "pnl", "capital_remaining", "notes",
]


# ---------------------------------------------------------------------------
# CSV log helpers
# ---------------------------------------------------------------------------

def _ensure_log_file():
    os.makedirs("logs", exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=LOG_HEADERS).writeheader()


def _append_log_row(**kwargs):
    _ensure_log_file()
    row = {h: kwargs.get(h, "") for h in LOG_HEADERS}
    row["timestamp"] = datetime.now(IST).isoformat()
    row["run_type"] = RUN_TYPE
    row["broker"] = ACTIVE_BROKER
    with open(LOG_FILE, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=LOG_HEADERS).writerow(row)


def log_info(message: str):
    logger.info(message)
    _append_log_row(action="INFO", notes=message)


def log_trade(action, symbol, quantity, price, order_id="", reason="", pnl=0.0,
              capital_remaining=0.0, notes=""):
    logger.info(
        f"[TRADE] {action} {symbol} qty={quantity} @ ₹{price:.2f} "
        f"reason={reason} pnl={pnl:.2f}"
    )
    _append_log_row(
        action=action, symbol=symbol, quantity=quantity, price=price,
        order_id=order_id, reason=reason, pnl=pnl,
        capital_remaining=capital_remaining, notes=notes,
    )


# ---------------------------------------------------------------------------
# Kite token freshness guard
# ---------------------------------------------------------------------------

def _check_kite_token_freshness():
    """
    For Kite broker in live mode, verify that KITE_TOKEN_DATE equals today's
    date in IST.  The Kite access_token expires at 6 AM every day; if this
    date doesn't match, the token is stale and every API call will fail.

    This check runs BEFORE broker.connect() so the bot aborts immediately
    with a clear error and a Telegram alert rather than failing mid-session.
    Skip logic:
      DRY_RUN=true  → skip (no real orders, market-data-only calls may still work
                       with an old token during dev/testing)
      ACTIVE_BROKER != kite → skip (Breeze has its own session mechanism)
    """
    if ACTIVE_BROKER != "kite" or DRY_RUN:
        return

    token_date = os.getenv("KITE_TOKEN_DATE", "").strip()
    today_ist  = datetime.now(IST).strftime("%Y-%m-%d")

    if token_date != today_ist:
        kite_login_url = (
            "https://kite.zerodha.com/connect/login?v=3"
            f"&api_key={os.getenv('KITE_API_KEY', 'UNKNOWN')}"
        )
        err = (
            f"Kite access token is STALE or missing. "
            f"KITE_TOKEN_DATE='{token_date}' but today is '{today_ist}' (IST). "
            f"Please complete the daily login at: {kite_login_url}"
        )
        logger.error(err)
        notify_error(err)
        sys.exit(1)

    logger.info(f"Kite token freshness verified for {today_ist}.")


# ---------------------------------------------------------------------------
# Trade execution helpers
# ---------------------------------------------------------------------------

def execute_buy(broker, pool: PoolPortfolio, candidate: dict):
    risk = RiskManager()
    max_invest = risk.max_investment(pool.capital_remaining)
    ltp = candidate["ltp"]
    quantity = int(max_invest // ltp)

    if quantity < 1:
        msg = (
            f"[{pool.pool_name}] Skipping {candidate['symbol']} — insufficient capital for 1 share "
            f"(ltp=₹{ltp:,.2f}, max_invest=₹{max_invest:,.2f})."
        )
        log_info(msg)
        notify_skip(msg)
        return

    amount_invested = round(quantity * ltp, 2)
    symbol = candidate["symbol"]

    if DRY_RUN:
        log_info(f"[{pool.pool_name}][DRY_RUN] Would BUY {symbol} x{quantity} @ ₹{ltp:.2f}")
        pool.record_buy(symbol, quantity, ltp)
        log_trade(
            "BUY_DRY", symbol, quantity, ltp,
            order_id="DRY_RUN",
            capital_remaining=pool.capital_remaining,
            notes=f"pool={pool.pool_name} amount_invested={amount_invested}",
        )
        notify_buy(symbol, quantity, ltp, amount_invested, pool.capital_remaining)
        return

    order = broker.place_market_buy(symbol, quantity)
    pool.record_buy(symbol, quantity, ltp)
    notes = (
        f"pool={pool.pool_name} RSI={candidate.get('rsi', 'N/A'):.1f} "
        f"MACD_cross={candidate.get('macd_cross', False)}"
    )
    log_trade(
        "BUY", symbol, quantity, ltp,
        order_id=order["order_id"],
        capital_remaining=pool.capital_remaining,
        notes=notes,
    )
    notify_buy(symbol, quantity, ltp, amount_invested, pool.capital_remaining)


def execute_sell(broker, pool: PoolPortfolio, holding: dict, quote: dict, reason: str):
    symbol = holding["symbol"]
    quantity = holding["quantity"]
    sell_price = quote["ltp"]
    pnl = round((sell_price - holding["buy_price"]) * quantity, 2)

    if DRY_RUN:
        log_info(
            f"[{pool.pool_name}][DRY_RUN] Would SELL {symbol} x{quantity} @ ₹{sell_price:.2f} "
            f"reason={reason} pnl=₹{pnl:.2f}"
        )
        pool.record_sell(symbol, sell_price, pnl)
        log_trade(
            "SELL_DRY", symbol, quantity, sell_price,
            order_id="DRY_RUN", reason=reason, pnl=pnl,
            capital_remaining=pool.capital_remaining,
            notes=f"pool={pool.pool_name}",
        )
        notify_sell(symbol, reason, pnl, pool.capital_remaining, pool.profit_booked)
        return

    order = broker.place_market_sell(symbol, quantity)
    pool.record_sell(symbol, sell_price, pnl)
    log_trade(
        "SELL", symbol, quantity, sell_price,
        order_id=order["order_id"], reason=reason, pnl=pnl,
        capital_remaining=pool.capital_remaining,
        notes=f"pool={pool.pool_name}",
    )
    notify_sell(symbol, reason, pnl, pool.capital_remaining, pool.profit_booked)


# ---------------------------------------------------------------------------
# Session runners
# ---------------------------------------------------------------------------


def _try_buy_best_candidate(broker, pool: PoolPortfolio, research: MarketResearch,
                             exclude_symbol: str | None = None):
    """Shared helper: find the best buy candidate and execute the buy."""
    if pool.capital_remaining < 500:
        msg = f"[{pool.pool_name}] Capital ₹{pool.capital_remaining:.2f} < ₹500. Skipping buy scan."
        log_info(msg)
        notify_skip(msg)
        return
    candidate = research.find_best_buy_candidate(
        capital_remaining=pool.capital_remaining,
        exclude_symbol=exclude_symbol,
    )
    if candidate:
        execute_buy(broker, pool, candidate)
    else:
        msg = f"[{pool.pool_name}] No qualifying buy candidate found."
        log_info(msg)
        notify_skip(msg)


def process_pool(broker, pool: PoolPortfolio, risk: RiskManager,
                 research: MarketResearch, sell_only: bool):
    """
    Process a single capital pool (nifty50 or smallcap50).

    sell_only=False (before 3 PM):
      If holding: stop-loss → profit-target → rotation analysis → buy replacement.
      If not holding: scan for best buy candidate.

    sell_only=True (after 3 PM):
      If holding: stop-loss / profit-target / EOD loss threshold.
      No buy scan at all.
    """
    pool_label = pool.pool_name.upper()
    log_info(f"--- Processing pool: {pool_label} (sell_only={sell_only}) ---")

    if pool.has_holdings():
        for holding in list(pool.holdings):
            quote = broker.get_quote(holding["symbol"])
            ltp = quote["ltp"]
            pnl_pct = risk.current_pnl_pct(holding["buy_price"], ltp)

            # --- Hard stop-loss: mandatory ---
            if risk.should_stop_loss(holding["buy_price"], ltp):
                log_info(f"[{pool_label}] Stop-loss triggered for {holding['symbol']}. Selling.")
                execute_sell(broker, pool, holding, quote, reason="STOP_LOSS")
                if not sell_only:
                    _try_buy_best_candidate(broker, pool, research,
                                            exclude_symbol=holding["symbol"])
                continue

            # --- Profit target ---
            if risk.should_book_profit(holding["buy_price"], ltp):
                log_info(f"[{pool_label}] Profit target hit for {holding['symbol']} (+{pnl_pct:.2f}%).")
                execute_sell(broker, pool, holding, quote, reason="PROFIT_TARGET")
                if not sell_only:
                    _try_buy_best_candidate(broker, pool, research,
                                            exclude_symbol=holding["symbol"])
                continue

            # --- After 3 PM: EOD loss threshold check ---
            if sell_only:
                if risk.eod_should_sell(holding["buy_price"], ltp):
                    log_info(
                        f"[{pool_label}] EOD loss threshold for {holding['symbol']} "
                        f"({pnl_pct:.2f}%). Selling before close."
                    )
                    execute_sell(broker, pool, holding, quote, reason="EOD_EXIT")
                else:
                    log_info(
                        f"[{pool_label}] Holding {holding['symbol']} overnight. "
                        f"P&L: {pnl_pct:+.2f}%."
                    )
                continue

            # --- Before 3 PM: rotation analysis ---
            projected_capital = pool.capital_remaining + holding["amount_invested"]
            rotation = research.find_best_rotation_candidate(
                held_symbol=holding["symbol"],
                projected_capital=projected_capital,
            )
            log_info(
                f"[{pool_label} ROTATION] {holding['symbol']} | "
                f"action={rotation['action']} | {rotation['reason']}"
            )

            if rotation["action"] == "rotate":
                execute_sell(broker, pool, holding, quote, reason="ROTATION")
                execute_buy(broker, pool, rotation["best_candidate"])
            elif rotation["action"] == "sell_no_replace":
                execute_sell(broker, pool, holding, quote, reason="WEAK_TECHNICALS")
            else:
                log_info(f"[{pool_label}] Holding {holding['symbol']}.")
        return

    # No holdings — scan for a new trade (only if before 3 PM)
    if sell_only:
        log_info(f"[{pool_label}] No holdings at EOD. Nothing to do.")
        return

    _try_buy_best_candidate(broker, pool, research)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger.info(
        f"Trading agent starting | RUN_TYPE={RUN_TYPE} | SELL_ONLY={SELL_ONLY} | "
        f"BROKER={ACTIVE_BROKER} | DRY_RUN={DRY_RUN}"
    )
    _ensure_log_file()

    # Abort immediately if the Kite token hasn't been refreshed today
    _check_kite_token_freshness()

    try:
        broker = get_broker()
        broker.connect()
    except Exception as e:
        err = f"Broker connection failed: {e}"
        logger.error(err)
        notify_error(err)
        sys.exit(1)

    portfolio = Portfolio.load()
    risk = RiskManager()

    # One research instance per stock universe
    nifty50_research = MarketResearch(broker, stock_universe=NIFTY50_SYMBOLS)
    smallcap_research = MarketResearch(broker, stock_universe=NIFTY_SMALLCAP_50_SYMBOLS)

    try:
        process_pool(broker, portfolio.nifty50, risk, nifty50_research, SELL_ONLY)
        process_pool(broker, portfolio.smallcap50, risk, smallcap_research, SELL_ONLY)
    except Exception as e:
        err = f"Unhandled error during trading run: {e}"
        logger.exception(err)
        notify_error(err)
    finally:
        portfolio.save()
        logger.info("Portfolio state saved. Agent run complete.")


if __name__ == "__main__":
    main()
