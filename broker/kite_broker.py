import logging
import os
from datetime import datetime, timedelta

import pytz
from kiteconnect import KiteConnect

from broker.base_broker import BaseBroker
from constants import NIFTY50_SYMBOLS

logger = logging.getLogger(__name__)


class KiteBroker(BaseBroker):

    def __init__(self):
        self.api_key = os.getenv("KITE_API_KEY")
        self.api_secret = os.getenv("KITE_API_SECRET")
        self.access_token = os.getenv("KITE_ACCESS_TOKEN")
        self.kite: KiteConnect | None = None
        self._instrument_tokens: dict[str, int] = {}

    def connect(self) -> bool:
        if not self.api_key or not self.access_token:
            raise RuntimeError(
                "KITE_API_KEY and KITE_ACCESS_TOKEN must be set in the environment."
            )
        self.kite = KiteConnect(api_key=self.api_key)
        self.kite.set_access_token(self.access_token)
        try:
            profile = self.kite.profile()
            logger.info(f"Kite connected: {profile['user_name']}")
        except Exception as e:
            raise RuntimeError(
                f"Kite authentication failed — access token may be stale: {e}"
            )
        self._load_instrument_tokens()
        return True

    def _load_instrument_tokens(self):
        """Build symbol → instrument_token map for all Nifty 50 stocks."""
        instruments = self.kite.instruments("NSE")
        for inst in instruments:
            if inst["tradingsymbol"] in NIFTY50_SYMBOLS:
                self._instrument_tokens[inst["tradingsymbol"]] = inst["instrument_token"]
        missing = set(NIFTY50_SYMBOLS) - set(self._instrument_tokens.keys())
        if missing:
            logger.warning(f"Instrument tokens not found for: {missing}")

    def get_quote(self, symbol: str) -> dict:
        full_symbol = f"NSE:{symbol}"
        quotes = self.kite.quote([full_symbol])
        q = quotes[full_symbol]
        ltp = q["last_price"]
        prev_close = q["ohlc"]["close"]
        change_pct = ((ltp - prev_close) / prev_close * 100) if prev_close else 0.0
        return {
            "symbol": symbol,
            "ltp": ltp,
            "open": q["ohlc"]["open"],
            "high": q["ohlc"]["high"],
            "low": q["ohlc"]["low"],
            "volume": q["volume"],
            "change_pct": round(change_pct, 4),
        }

    def get_nifty50_quotes(self) -> list[dict]:
        full_symbols = [f"NSE:{s}" for s in NIFTY50_SYMBOLS]
        # Kite allows up to 500 instruments per call
        quotes = self.kite.quote(full_symbols)
        result = []
        for sym in NIFTY50_SYMBOLS:
            full_sym = f"NSE:{sym}"
            if full_sym not in quotes:
                logger.warning(f"Quote missing for {sym}")
                continue
            q = quotes[full_sym]
            ltp = q["last_price"]
            prev_close = q["ohlc"]["close"]
            change_pct = ((ltp - prev_close) / prev_close * 100) if prev_close else 0.0
            result.append({
                "symbol": sym,
                "ltp": ltp,
                "open": q["ohlc"]["open"],
                "high": q["ohlc"]["high"],
                "low": q["ohlc"]["low"],
                "volume": q["volume"],
                "change_pct": round(change_pct, 4),
            })
        return result

    def place_market_buy(self, symbol: str, quantity: int) -> dict:
        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=self.kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=self.kite.TRANSACTION_TYPE_BUY,
            quantity=quantity,
            product=self.kite.PRODUCT_CNC,
            order_type=self.kite.ORDER_TYPE_MARKET,
        )
        logger.info(f"Kite BUY order placed: {order_id} — {symbol} x{quantity}")
        return {"order_id": str(order_id), "symbol": symbol, "quantity": quantity, "status": "PLACED"}

    def place_market_sell(self, symbol: str, quantity: int) -> dict:
        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=self.kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=self.kite.TRANSACTION_TYPE_SELL,
            quantity=quantity,
            product=self.kite.PRODUCT_CNC,
            order_type=self.kite.ORDER_TYPE_MARKET,
        )
        logger.info(f"Kite SELL order placed: {order_id} — {symbol} x{quantity}")
        return {"order_id": str(order_id), "symbol": symbol, "quantity": quantity, "status": "PLACED"}

    def get_positions(self) -> list[dict]:
        positions_data = self.kite.positions()
        result = []
        for pos in positions_data.get("net", []):
            if pos["quantity"] != 0:
                result.append({
                    "symbol": pos["tradingsymbol"],
                    "quantity": pos["quantity"],
                    "avg_price": pos["average_price"],
                    "ltp": pos["last_price"],
                    "pnl": pos["pnl"],
                })
        return result

    def get_order_status(self, order_id: str) -> dict:
        orders = self.kite.orders()
        for order in orders:
            if str(order["order_id"]) == order_id:
                return {
                    "order_id": order_id,
                    "status": order["status"],
                    "filled_quantity": order.get("filled_quantity", 0),
                    "average_price": order.get("average_price", 0),
                }
        return {"order_id": order_id, "status": "NOT_FOUND"}

    def get_historical_data(self, symbol: str, interval: str = "day", days: int = 90) -> list[dict]:
        token = self._instrument_tokens.get(symbol)
        if not token:
            raise ValueError(f"Instrument token not found for {symbol}. Was connect() called?")

        IST = pytz.timezone("Asia/Kolkata")
        to_date = datetime.now(IST)
        from_date = to_date - timedelta(days=days)

        # Kite interval names differ from generic names
        interval_map = {"day": "day", "5minute": "5minute", "minute": "minute"}
        kite_interval = interval_map.get(interval, "day")

        data = self.kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=kite_interval,
        )
        return [
            {
                "datetime": d["date"],
                "open": float(d["open"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "close": float(d["close"]),
                "volume": int(d["volume"]),
            }
            for d in data
        ]
