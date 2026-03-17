import logging
import os
from datetime import datetime, timedelta

import pytz
from breeze_connect import BreezeConnect

from broker.base_broker import BaseBroker
from constants import NIFTY50_SYMBOLS

logger = logging.getLogger(__name__)

# Breeze stock codes that differ from NSE trading symbols.
# Verify against Breeze's stock master before going live.
BREEZE_SYMBOL_MAP: dict[str, str] = {
    "BAJAJ-AUTO": "BAJAAU",
    "M&M":        "MHMIL",
    "NESTLEIND":  "NESTL",
    "HINDUNILVR": "HUNL",
    "HEROMOTOCO": "HMOTO",
    "SHRIRAMFIN": "SRTRF",
}


class BreezeBroker(BaseBroker):

    def __init__(self):
        self.api_key = os.getenv("BREEZE_API_KEY")
        self.api_secret = os.getenv("BREEZE_API_SECRET")
        self.session_token = os.getenv("BREEZE_SESSION_TOKEN")
        self.breeze: BreezeConnect | None = None

    def _to_breeze_code(self, symbol: str) -> str:
        return BREEZE_SYMBOL_MAP.get(symbol, symbol)

    def connect(self) -> bool:
        if not self.api_key or not self.session_token:
            raise RuntimeError(
                "BREEZE_API_KEY and BREEZE_SESSION_TOKEN must be set in the environment."
            )
        self.breeze = BreezeConnect(api_key=self.api_key)
        try:
            self.breeze.generate_session(
                api_secret=self.api_secret,
                session_token=self.session_token,
            )
            logger.info("Breeze Connect session established.")
        except Exception as e:
            raise RuntimeError(
                f"Breeze authentication failed — session token may be stale: {e}"
            )
        return True

    def get_quote(self, symbol: str) -> dict:
        stock_code = self._to_breeze_code(symbol)
        resp = self.breeze.get_quotes(
            stock_code=stock_code,
            exchange_code="NSE",
            expiry_date="",
            product_type="cash",
            right="",
            strike_price="",
        )
        if resp.get("Status") != 200 or not resp.get("Success"):
            raise RuntimeError(f"Failed to get quote for {symbol}: {resp}")
        q = resp["Success"][0]
        ltp = float(q.get("ltp", q.get("last_traded_price", 0)))
        prev_close = float(q.get("previous_close", ltp))
        change_pct = ((ltp - prev_close) / prev_close * 100) if prev_close else 0.0
        return {
            "symbol": symbol,
            "ltp": ltp,
            "open": float(q.get("open", 0)),
            "high": float(q.get("high", 0)),
            "low": float(q.get("low", 0)),
            "volume": int(q.get("total_quantity_traded", 0)),
            "change_pct": round(change_pct, 4),
        }

    def get_nifty50_quotes(self) -> list[dict]:
        result = []
        for symbol in NIFTY50_SYMBOLS:
            try:
                result.append(self.get_quote(symbol))
            except Exception as e:
                logger.warning(f"Could not fetch Breeze quote for {symbol}: {e}")
        return result

    def place_market_buy(self, symbol: str, quantity: int) -> dict:
        stock_code = self._to_breeze_code(symbol)
        resp = self.breeze.place_order(
            stock_code=stock_code,
            exchange_code="NSE",
            product="cash",
            action="buy",
            order_type="market",
            quantity=str(quantity),
            price="0",
            validity="day",
            stoploss="0",
            disclosed_quantity="0",
            expiry_date="",
            right="",
            strike_price="0",
        )
        if resp.get("Status") != 200:
            raise RuntimeError(f"Breeze BUY order failed for {symbol}: {resp}")
        order_id = str(resp.get("Success", {}).get("order_id", "UNKNOWN"))
        logger.info(f"Breeze BUY order placed: {order_id} — {symbol} x{quantity}")
        return {"order_id": order_id, "symbol": symbol, "quantity": quantity, "status": "PLACED"}

    def place_market_sell(self, symbol: str, quantity: int) -> dict:
        stock_code = self._to_breeze_code(symbol)
        resp = self.breeze.place_order(
            stock_code=stock_code,
            exchange_code="NSE",
            product="cash",
            action="sell",
            order_type="market",
            quantity=str(quantity),
            price="0",
            validity="day",
            stoploss="0",
            disclosed_quantity="0",
            expiry_date="",
            right="",
            strike_price="0",
        )
        if resp.get("Status") != 200:
            raise RuntimeError(f"Breeze SELL order failed for {symbol}: {resp}")
        order_id = str(resp.get("Success", {}).get("order_id", "UNKNOWN"))
        logger.info(f"Breeze SELL order placed: {order_id} — {symbol} x{quantity}")
        return {"order_id": order_id, "symbol": symbol, "quantity": quantity, "status": "PLACED"}

    def get_positions(self) -> list[dict]:
        resp = self.breeze.get_portfolio_positions()
        if resp.get("Status") != 200:
            logger.warning(f"Could not fetch Breeze positions: {resp}")
            return []
        result = []
        for pos in resp.get("Success") or []:
            qty = int(pos.get("quantity", 0))
            if qty != 0:
                result.append({
                    "symbol": pos.get("stock_code", ""),
                    "quantity": qty,
                    "avg_price": float(pos.get("average_cost", 0)),
                    "ltp": float(pos.get("ltp", 0)),
                    "pnl": float(pos.get("unrealised_pnl", 0)),
                })
        return result

    def get_order_status(self, order_id: str) -> dict:
        resp = self.breeze.get_order_detail(exchange_code="NSE", order_id=order_id)
        if resp.get("Status") != 200 or not resp.get("Success"):
            return {"order_id": order_id, "status": "NOT_FOUND"}
        order = resp["Success"][0] if resp["Success"] else {}
        return {
            "order_id": order_id,
            "status": order.get("order_status", "UNKNOWN"),
            "filled_quantity": int(order.get("traded_quantity", 0)),
            "average_price": float(order.get("trade_price", 0)),
        }

    def get_historical_data(self, symbol: str, interval: str = "day", days: int = 90) -> list[dict]:
        stock_code = self._to_breeze_code(symbol)
        IST = pytz.timezone("Asia/Kolkata")
        to_date = datetime.now(IST)
        from_date = to_date - timedelta(days=days)

        interval_map = {"day": "1day", "5minute": "5minute", "minute": "1minute"}
        breeze_interval = interval_map.get(interval, "1day")

        resp = self.breeze.get_historical_data(
            interval=breeze_interval,
            from_date=from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            to_date=to_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            stock_code=stock_code,
            exchange_code="NSE",
            product_type="cash",
        )
        if resp.get("Status") != 200:
            raise RuntimeError(f"Breeze historical data failed for {symbol}: {resp}")
        return [
            {
                "datetime": d.get("datetime"),
                "open": float(d.get("open", 0)),
                "high": float(d.get("high", 0)),
                "low": float(d.get("low", 0)),
                "close": float(d.get("close", 0)),
                "volume": int(d.get("volume", 0)),
            }
            for d in (resp.get("Success") or [])
        ]
