"""
main.py — Trading Agent Entry Point

Called by GitHub Actions as:
    python main.py morning   # 9:35 AM IST
    python main.py midday    # 12:05 PM IST
    python main.py eod       # 3:00 PM IST
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
from agent.portfolio import Portfolio
from agent.risk_manager import RiskManager

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
    logger.error("Usage: python main.py <morning|midday|eod>")
    sys.exit(1)

RUN_TYPE = sys.argv[1].strip().lower()
if RUN_TYPE not in ("morning", "midday", "eod"):
    logger.error(f"Invalid RUN_TYPE '{RUN_TYPE}'. Expected: morning | midday | eod")
    sys.exit(1)

IST = pytz.timezone("Asia/Kolkata")
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

def execute_buy(broker, portfolio: Portfolio, candidate: dict):
    risk = RiskManager()
    max_invest = risk.max_investment(portfolio.capital_remaining)
    ltp = candidate["ltp"]
    quantity = int(max_invest // ltp)

    if quantity < 1:
        msg = (
            f"Skipping {candidate['symbol']} — insufficient capital for 1 share "
            f"(ltp=₹{ltp:,.2f}, max_invest=₹{max_invest:,.2f})."
        )
        log_info(msg)
        notify_skip(msg)
        return

    amount_invested = round(quantity * ltp, 2)
    symbol = candidate["symbol"]

    if DRY_RUN:
        log_info(f"[DRY_RUN] Would BUY {symbol} x{quantity} @ ₹{ltp:.2f}")
        portfolio.record_buy(symbol, quantity, ltp)
        log_trade(
            "BUY_DRY", symbol, quantity, ltp,
            order_id="DRY_RUN",
            capital_remaining=portfolio.capital_remaining,
            notes=f"amount_invested={amount_invested}",
        )
        notify_buy(symbol, quantity, ltp, amount_invested, portfolio.capital_remaining)
        return

    order = broker.place_market_buy(symbol, quantity)
    portfolio.record_buy(symbol, quantity, ltp)
    notes = (
        f"RSI={candidate.get('rsi', 'N/A'):.1f} "
        f"MACD_cross={candidate.get('macd_cross', False)}"
    )
    log_trade(
        "BUY", symbol, quantity, ltp,
        order_id=order["order_id"],
        capital_remaining=portfolio.capital_remaining,
        notes=notes,
    )
    notify_buy(symbol, quantity, ltp, amount_invested, portfolio.capital_remaining)


def execute_sell(broker, portfolio: Portfolio, holding: dict, quote: dict, reason: str):
    symbol = holding["symbol"]
    quantity = holding["quantity"]
    sell_price = quote["ltp"]
    pnl = round((sell_price - holding["buy_price"]) * quantity, 2)

    # PORTFOLIO ISOLATION: the bot only sells positions it recorded in portfolio_state.json.
    # It never reads the broker's full account positions. This guarantees the bot
    # cannot accidentally touch shares you bought manually outside this system.

    if DRY_RUN:
        log_info(
            f"[DRY_RUN] Would SELL {symbol} x{quantity} @ ₹{sell_price:.2f} "
            f"reason={reason} pnl=₹{pnl:.2f}"
        )
        portfolio.record_sell(symbol, sell_price, pnl)
        log_trade(
            "SELL_DRY", symbol, quantity, sell_price,
            order_id="DRY_RUN", reason=reason, pnl=pnl,
            capital_remaining=portfolio.capital_remaining,
        )
        notify_sell(symbol, reason, pnl, portfolio.capital_remaining, portfolio.profit_booked)
        return

    order = broker.place_market_sell(symbol, quantity)
    portfolio.record_sell(symbol, sell_price, pnl)
    log_trade(
        "SELL", symbol, quantity, sell_price,
        order_id=order["order_id"], reason=reason, pnl=pnl,
        capital_remaining=portfolio.capital_remaining,
    )
    notify_sell(symbol, reason, pnl, portfolio.capital_remaining, portfolio.profit_booked)


# ---------------------------------------------------------------------------
# Session runners
# ---------------------------------------------------------------------------


def _try_buy_best_candidate(broker, portfolio: Portfolio, research: MarketResearch,
                             exclude_symbol: str | None = None):
    """Shared helper: find the best Nifty 50 buy candidate and execute the buy."""
    if portfolio.capital_remaining < 500:
        msg = f"Capital ₹{portfolio.capital_remaining:.2f} < ₹500. Skipping buy scan."
        log_info(msg)
        notify_skip(msg)
        return
    candidate = research.find_best_buy_candidate(
        capital_remaining=portfolio.capital_remaining,
        exclude_symbol=exclude_symbol,
    )
    if candidate:
        execute_buy(broker, portfolio, candidate)
    else:
        msg = "No qualifying buy candidate found in Nifty 50."
        log_info(msg)
        notify_skip(msg)


def run_morning_session(broker, portfolio: Portfolio, risk: RiskManager, research: MarketResearch):
    """
    9:35 AM
    If holding:
      1. Hard stop-loss check — sell immediately, then look for replacement.
      2. Profit-target check — book profit, then look for replacement.
      3. Rotation analysis — compare held stock vs every other Nifty 50 stock.
         Rotate only if an alternative scores 30% better (guards against churn).
    If not holding: scan for best buy candidate.
    """
    log_info("=== Morning session started ===")

    if portfolio.has_holdings():
        for holding in list(portfolio.holdings):
            quote = broker.get_quote(holding["symbol"])
            ltp = quote["ltp"]

            # --- Hard stop-loss: mandatory, no further comparison ---
            if risk.should_stop_loss(holding["buy_price"], ltp):
                log_info(f"Hard stop-loss triggered for {holding['symbol']}. Selling.")
                execute_sell(broker, portfolio, holding, quote, reason="STOP_LOSS")
                _try_buy_best_candidate(broker, portfolio, research,
                                        exclude_symbol=holding["symbol"])
                return

            # --- Profit target: book profit, look for next best trade ---
            if risk.should_book_profit(holding["buy_price"], ltp):
                pnl_pct = risk.current_pnl_pct(holding["buy_price"], ltp)
                log_info(f"Profit target hit for {holding['symbol']} (+{pnl_pct:.2f}%). Booking profit.")
                execute_sell(broker, portfolio, holding, quote, reason="PROFIT_TARGET")
                _try_buy_best_candidate(broker, portfolio, research,
                                        exclude_symbol=holding["symbol"])
                return

            # --- Rotation analysis: is there a significantly better stock? ---
            # Project capital available if we were to sell the held stock now.
            projected_capital = portfolio.capital_remaining + holding["amount_invested"]
            rotation = research.find_best_rotation_candidate(
                held_symbol=holding["symbol"],
                projected_capital=projected_capital,
            )
            log_info(
                f"[MORNING ROTATION] {holding['symbol']} | "
                f"action={rotation['action']} | {rotation['reason']}"
            )

            if rotation["action"] == "rotate":
                execute_sell(broker, portfolio, holding, quote, reason="ROTATION")
                execute_buy(broker, portfolio, rotation["best_candidate"])
            elif rotation["action"] == "sell_no_replace":
                execute_sell(broker, portfolio, holding, quote, reason="WEAK_TECHNICALS")
                # No suitable replacement — sit in cash until next session
            else:
                log_info(f"Holding {holding['symbol']} after morning analysis.")
        return

    # No holdings — look for a new trade
    _try_buy_best_candidate(broker, portfolio, research)


def run_midday_session(broker, portfolio: Portfolio, risk: RiskManager, research: MarketResearch):
    """
    12:05 PM
    Mirrors morning logic exactly:
      - Hard stop-loss / profit-target checks first.
      - Then rotation analysis: compare held stock vs all Nifty 50 alternatives.
      - If no holdings, scan for a fresh buy (catches cases where morning found nothing).
    """
    log_info("=== Midday session started ===")

    if portfolio.has_holdings():
        for holding in list(portfolio.holdings):
            quote = broker.get_quote(holding["symbol"])
            ltp = quote["ltp"]

            # --- Hard stop-loss ---
            if risk.should_stop_loss(holding["buy_price"], ltp):
                log_info(f"Midday hard stop-loss for {holding['symbol']}.")
                execute_sell(broker, portfolio, holding, quote, reason="MIDDAY_STOP_LOSS")
                _try_buy_best_candidate(broker, portfolio, research,
                                        exclude_symbol=holding["symbol"])
                return

            # --- Profit target ---
            if risk.should_book_profit(holding["buy_price"], ltp):
                pnl_pct = risk.current_pnl_pct(holding["buy_price"], ltp)
                log_info(f"Midday profit target for {holding['symbol']} (+{pnl_pct:.2f}%). Booking.")
                execute_sell(broker, portfolio, holding, quote, reason="MIDDAY_PROFIT")
                _try_buy_best_candidate(broker, portfolio, research,
                                        exclude_symbol=holding["symbol"])
                return

            # --- Rotation analysis ---
            projected_capital = portfolio.capital_remaining + holding["amount_invested"]
            rotation = research.find_best_rotation_candidate(
                held_symbol=holding["symbol"],
                projected_capital=projected_capital,
            )
            log_info(
                f"[MIDDAY ROTATION] {holding['symbol']} | "
                f"action={rotation['action']} | {rotation['reason']}"
            )

            if rotation["action"] == "rotate":
                execute_sell(broker, portfolio, holding, quote, reason="MIDDAY_ROTATION")
                execute_buy(broker, portfolio, rotation["best_candidate"])
            elif rotation["action"] == "sell_no_replace":
                execute_sell(broker, portfolio, holding, quote, reason="MIDDAY_WEAK_TECHNICALS")
            else:
                log_info(f"Midday: holding {holding['symbol']}.")
        return

    # No holdings — try a midday entry if morning missed
    log_info("No holdings at midday. Scanning for entry opportunity.")
    _try_buy_best_candidate(broker, portfolio, research)


def run_eod_session(broker, portfolio: Portfolio, risk: RiskManager, research: MarketResearch):
    """
    3:00 PM — Final check before market close.
    ONLY sell decisions are made here. If we sell, we do NOT look for a
    replacement — we go flat and let the next morning's session find a new trade.
    """
    log_info("=== EOD session started ===")

    if not portfolio.has_holdings():
        log_info("No holdings at EOD. Nothing to do.")
        return

    for holding in list(portfolio.holdings):
        quote = broker.get_quote(holding["symbol"])
        ltp = quote["ltp"]
        pnl_pct = risk.current_pnl_pct(holding["buy_price"], ltp)

        if risk.should_stop_loss(holding["buy_price"], ltp):
            log_info(f"EOD stop-loss for {holding['symbol']} ({pnl_pct:.2f}%). Selling.")
            execute_sell(broker, portfolio, holding, quote, reason="EOD_STOP_LOSS")

        elif risk.should_book_profit(holding["buy_price"], ltp):
            log_info(f"EOD profit target for {holding['symbol']} (+{pnl_pct:.2f}%). Booking.")
            execute_sell(broker, portfolio, holding, quote, reason="EOD_PROFIT")

        elif risk.eod_should_sell(holding["buy_price"], ltp):
            log_info(
                f"EOD loss threshold exceeded for {holding['symbol']} "
                f"({pnl_pct:.2f}%). Selling before close."
            )
            execute_sell(broker, portfolio, holding, quote, reason="EOD_EXIT")

        else:
            log_info(
                f"EOD: holding {holding['symbol']} overnight. "
                f"P&L: {pnl_pct:+.2f}% — within acceptable range."
            )
        # NOTE: No buy scan after EOD sell. Tomorrow's morning session decides.


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger.info(
        f"Trading agent starting | RUN_TYPE={RUN_TYPE} | "
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
    research = MarketResearch(broker)

    try:
        if RUN_TYPE == "morning":
            run_morning_session(broker, portfolio, risk, research)
        elif RUN_TYPE == "midday":
            run_midday_session(broker, portfolio, risk, research)
        elif RUN_TYPE == "eod":
            run_eod_session(broker, portfolio, risk, research)
    except Exception as e:
        err = f"Unhandled error during {RUN_TYPE} session: {e}"
        logger.exception(err)
        notify_error(err)
    finally:
        portfolio.save()
        logger.info("Portfolio state saved. Agent run complete.")


if __name__ == "__main__":
    main()
