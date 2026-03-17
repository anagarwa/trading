import json
import logging
import os
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class Portfolio:
    STATE_FILE = "state/portfolio_state.json"

    def __init__(
        self,
        capital_remaining: float,
        profit_booked: float,
        total_losses_taken: float,
        holdings: list[dict],
        last_updated: str,
        trading_day_complete: bool,
    ):
        self.capital_remaining = capital_remaining
        self.profit_booked = profit_booked
        self.total_losses_taken = total_losses_taken
        self.holdings = holdings
        self.last_updated = last_updated
        self.trading_day_complete = trading_day_complete

    @classmethod
    def load(cls) -> "Portfolio":
        if not os.path.exists(cls.STATE_FILE):
            from config import INITIAL_BUDGET
            logger.info(f"No state file found. Initialising with budget ₹{INITIAL_BUDGET:,.2f}.")
            return cls(
                capital_remaining=INITIAL_BUDGET,
                profit_booked=0.0,
                total_losses_taken=0.0,
                holdings=[],
                last_updated=datetime.now(IST).isoformat(),
                trading_day_complete=False,
            )
        with open(cls.STATE_FILE, "r") as f:
            data = json.load(f)
        logger.info(
            f"Portfolio loaded: capital=₹{data.get('capital_remaining', 0):,.2f} "
            f"holdings={len(data.get('holdings', []))}"
        )
        return cls(
            capital_remaining=float(data["capital_remaining"]),
            profit_booked=float(data.get("profit_booked", 0.0)),
            total_losses_taken=float(data.get("total_losses_taken", 0.0)),
            holdings=data.get("holdings", []),
            last_updated=data.get("last_updated", ""),
            trading_day_complete=data.get("trading_day_complete", False),
        )

    def save(self):
        os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
        self.last_updated = datetime.now(IST).isoformat()
        data = {
            "capital_remaining": round(self.capital_remaining, 2),
            "profit_booked": round(self.profit_booked, 2),
            "total_losses_taken": round(self.total_losses_taken, 2),
            "holdings": self.holdings,
            "last_updated": self.last_updated,
            "trading_day_complete": self.trading_day_complete,
        }
        with open(self.STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Portfolio saved: capital=₹{self.capital_remaining:,.2f}")

    def has_holdings(self) -> bool:
        return len(self.holdings) > 0

    def record_buy(self, symbol: str, quantity: int, buy_price: float):
        amount_invested = round(quantity * buy_price, 2)

        # Critical capital guard — prevents over-committing
        if amount_invested > self.capital_remaining:
            raise RuntimeError(
                f"Capital guard: need ₹{amount_invested:,.2f} but only "
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
            f"BUY recorded: {symbol} x{quantity} @ ₹{buy_price:.2f} | "
            f"Invested=₹{amount_invested:.2f} | Capital remaining=₹{self.capital_remaining:.2f}"
        )

    def record_sell(self, symbol: str, sell_price: float, pnl: float):
        holding = self._find_holding(symbol)
        if not holding:
            raise RuntimeError(f"No holding found for '{symbol}' in portfolio.")

        sell_proceeds = round(holding["quantity"] * sell_price, 2)

        if pnl >= 0:
            # Profits are sacred — capital_remaining only gets back the original investment.
            # The profit is tracked separately and NEVER re-enters the trading pool.
            self.capital_remaining = round(self.capital_remaining + holding["amount_invested"], 2)
            self.profit_booked = round(self.profit_booked + pnl, 2)
        else:
            # Losses permanently reduce the trading pool.
            self.capital_remaining = round(self.capital_remaining + sell_proceeds, 2)
            self.total_losses_taken = round(self.total_losses_taken + abs(pnl), 2)

        self.holdings = [h for h in self.holdings if h["symbol"] != symbol]
        pnl_str = f"+₹{pnl:.2f}" if pnl >= 0 else f"-₹{abs(pnl):.2f}"
        logger.info(
            f"SELL recorded: {symbol} x{holding['quantity']} @ ₹{sell_price:.2f} | "
            f"P&L={pnl_str} | Capital remaining=₹{self.capital_remaining:.2f}"
        )

    def _find_holding(self, symbol: str) -> dict | None:
        for h in self.holdings:
            if h["symbol"] == symbol:
                return h
        return None
