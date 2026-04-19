import json
import logging
import os
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class PoolPortfolio:
    """Manages a single capital pool (e.g. nifty50 or smallcap50)."""

    def __init__(
        self,
        pool_name: str,
        capital_remaining: float,
        profit_booked: float,
        total_losses_taken: float,
        holdings: list[dict],
    ):
        self.pool_name = pool_name
        self.capital_remaining = capital_remaining
        self.profit_booked = profit_booked
        self.total_losses_taken = total_losses_taken
        self.holdings = holdings

    def has_holdings(self) -> bool:
        return len(self.holdings) > 0

    def record_buy(self, symbol: str, quantity: int, buy_price: float):
        amount_invested = round(quantity * buy_price, 2)

        if amount_invested > self.capital_remaining:
            raise RuntimeError(
                f"[{self.pool_name}] Capital guard: need ₹{amount_invested:,.2f} but only "
                f"₹{self.capital_remaining:,.2f} available."
            )

        self.capital_remaining = round(self.capital_remaining - amount_invested, 2)
        self.holdings.append({
            "symbol": symbol,
            "quantity": quantity,
            "buy_price": round(buy_price, 2),
            "buy_date": datetime.now(IST).strftime("%Y-%m-%d"),
            "amount_invested": amount_invested,
        })
        logger.info(
            f"[{self.pool_name}] BUY recorded: {symbol} x{quantity} @ ₹{buy_price:.2f} | "
            f"Invested=₹{amount_invested:.2f} | Capital remaining=₹{self.capital_remaining:.2f}"
        )

    def record_sell(self, symbol: str, sell_price: float, pnl: float):
        holding = self._find_holding(symbol)
        if not holding:
            raise RuntimeError(f"[{self.pool_name}] No holding found for '{symbol}'.")

        sell_proceeds = round(holding["quantity"] * sell_price, 2)

        if pnl >= 0:
            self.capital_remaining = round(self.capital_remaining + holding["amount_invested"], 2)
            self.profit_booked = round(self.profit_booked + pnl, 2)
        else:
            self.capital_remaining = round(self.capital_remaining + sell_proceeds, 2)
            self.total_losses_taken = round(self.total_losses_taken + abs(pnl), 2)

        self.holdings = [h for h in self.holdings if h["symbol"] != symbol]
        pnl_str = f"+₹{pnl:.2f}" if pnl >= 0 else f"-₹{abs(pnl):.2f}"
        logger.info(
            f"[{self.pool_name}] SELL recorded: {symbol} x{holding['quantity']} @ ₹{sell_price:.2f} | "
            f"P&L={pnl_str} | Capital remaining=₹{self.capital_remaining:.2f}"
        )

    def _find_holding(self, symbol: str) -> dict | None:
        for h in self.holdings:
            if h["symbol"] == symbol:
                return h
        return None

    def to_dict(self) -> dict:
        return {
            "capital_remaining": round(self.capital_remaining, 2),
            "profit_booked": round(self.profit_booked, 2),
            "total_losses_taken": round(self.total_losses_taken, 2),
            "holdings": self.holdings,
        }


from agent.google_sheets import GoogleSheetsClient

class Portfolio:
    """
    Top-level portfolio with two pools: nifty50 and smallcap50.
    Persisted as a single JSON file or Google Sheet.
    """

    STATE_FILE = "state/portfolio_state.json"

    def __init__(self, nifty50: PoolPortfolio, smallcap50: PoolPortfolio,
                 last_updated: str, trading_day_complete: bool,
                 sheets_client: GoogleSheetsClient = None):
        self.nifty50 = nifty50
        self.smallcap50 = smallcap50
        self.last_updated = last_updated
        self.trading_day_complete = trading_day_complete
        self.sheets_client = sheets_client

    @classmethod
    def load(cls, sheets_client: GoogleSheetsClient = None) -> "Portfolio":
        data = None

        # 1. Try loading from Google Sheets first if client is provided
        if sheets_client:
            data = sheets_client.load_portfolio_state()
        
        # 2. Fallback to local JSON if Sheets failed or client not provided
        if not data and os.path.exists(cls.STATE_FILE):
            with open(cls.STATE_FILE, "r") as f:
                data = json.load(f)

        if not data:
            from config import NIFTY50_BUDGET, SMALLCAP50_BUDGET
            logger.info(
                f"No state found. Initialising: nifty50=₹{NIFTY50_BUDGET:,.2f}, "
                f"smallcap50=₹{SMALLCAP50_BUDGET:,.2f}."
            )
            return cls(
                nifty50=PoolPortfolio("nifty50", NIFTY50_BUDGET, 0.0, 0.0, []),
                smallcap50=PoolPortfolio("smallcap50", SMALLCAP50_BUDGET, 0.0, 0.0, []),
                last_updated=datetime.now(IST).isoformat(),
                trading_day_complete=False,
                sheets_client=sheets_client
            )

        n50 = data.get("nifty50", {})
        sc50 = data.get("smallcap50", {})

        nifty50 = PoolPortfolio(
            "nifty50",
            float(n50.get("capital_remaining", 5000)),
            float(n50.get("profit_booked", 0)),
            float(n50.get("total_losses_taken", 0)),
            n50.get("holdings", []),
        )
        smallcap50 = PoolPortfolio(
            "smallcap50",
            float(sc50.get("capital_remaining", 5000)),
            float(sc50.get("profit_booked", 0)),
            float(sc50.get("total_losses_taken", 0)),
            sc50.get("holdings", []),
        )
        logger.info(
            f"Portfolio loaded: nifty50 capital=₹{nifty50.capital_remaining:,.2f} "
            f"holdings={len(nifty50.holdings)} | smallcap50 capital=₹{smallcap50.capital_remaining:,.2f} "
            f"holdings={len(smallcap50.holdings)}"
        )
        return cls(
            nifty50=nifty50,
            smallcap50=smallcap50,
            last_updated=data.get("last_updated", ""),
            trading_day_complete=data.get("trading_day_complete", False),
            sheets_client=sheets_client
        )

    def save(self):
        self.last_updated = datetime.now(IST).isoformat()
        state_dict = {
            "nifty50": self.nifty50.to_dict(),
            "smallcap50": self.smallcap50.to_dict(),
            "last_updated": self.last_updated,
            "trading_day_complete": self.trading_day_complete,
        }

        # 1. Save to Google Sheets if client is available
        if self.sheets_client:
            self.sheets_client.save_portfolio_state(state_dict)

        # 2. Always save a local copy as backup
        os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
        with open(self.STATE_FILE, "w") as f:
            json.dump(state_dict, f, indent=2)
        
        logger.info(
            f"Portfolio saved locally and to Sheets (if enabled): "
            f"nifty50=₹{self.nifty50.capital_remaining:,.2f} "
            f"smallcap50=₹{self.smallcap50.capital_remaining:,.2f}"
        )
