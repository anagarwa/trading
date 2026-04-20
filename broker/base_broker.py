from abc import ABC, abstractmethod


class BaseBroker(ABC):

    @abstractmethod
    def connect(self) -> bool:
        """Authenticate and establish session. Return True on success."""

    @abstractmethod
    def get_quote(self, symbol: str) -> dict:
        """Return latest quote: {symbol, ltp, open, high, low, volume, change_pct}"""

    @abstractmethod
    def get_nifty50_quotes(self) -> list[dict]:
        """Return quotes for all 50 Nifty 50 constituents."""

    def get_quotes_for_symbols(self, symbols: list[str]) -> list[dict]:
        """
        Return quotes for an arbitrary list of NSE symbols.
        Default implementation calls get_quote() per symbol.
        Subclasses should override for batch efficiency.
        """
        result = []
        for sym in symbols:
            try:
                result.append(self.get_quote(sym))
            except Exception:
                pass
        return result

    @abstractmethod
    def place_market_buy(self, symbol: str, quantity: int, price: float | None = None) -> dict:
        """Place market buy order. Return {order_id, symbol, quantity, status}"""

    @abstractmethod
    def place_market_sell(self, symbol: str, quantity: int, price: float | None = None) -> dict:
        """Place market sell order. Return {order_id, symbol, quantity, status}"""

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return open positions: [{symbol, quantity, avg_price, ltp, pnl}]"""

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """Return order status for given order_id."""

    @abstractmethod
    def get_historical_data(self, symbol: str, interval: str, days: int) -> list[dict]:
        """
        Return historical OHLCV data.
        Each dict: {datetime, open, high, low, close, volume}
        interval: "day" | "5minute" | "minute"
        """
