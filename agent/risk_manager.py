import os


class RiskManager:
    """
    Manages stop-loss, profit-target, and position-sizing thresholds.
    All thresholds are configurable via environment variables so they can be
    tuned without code changes.
    """

    def __init__(self):
        self.STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "1.5"))
        self.PROFIT_TARGET_PCT = float(os.getenv("PROFIT_TARGET_PCT", "2.5"))
        self.EOD_LOSS_THRESHOLD = float(os.getenv("EOD_LOSS_THRESHOLD", "0.5"))
        self.MAX_SINGLE_TRADE_PCT = float(os.getenv("MAX_SINGLE_TRADE_PCT", "80"))

    def current_pnl_pct(self, buy_price: float, current_price: float) -> float:
        """Positive = profit, negative = loss."""
        return ((current_price - buy_price) / buy_price) * 100

    def should_stop_loss(self, buy_price: float, current_price: float) -> bool:
        """Return True if the position has hit the mandatory stop-loss level."""
        loss_pct = ((buy_price - current_price) / buy_price) * 100
        return loss_pct >= self.STOP_LOSS_PCT

    def should_book_profit(self, buy_price: float, current_price: float) -> bool:
        """Return True if the position has hit the profit target."""
        profit_pct = ((current_price - buy_price) / buy_price) * 100
        return profit_pct >= self.PROFIT_TARGET_PCT

    def eod_should_sell(self, buy_price: float, current_price: float) -> bool:
        """
        End-of-day check (3 PM): sell if loss >= EOD_LOSS_THRESHOLD.
        A smaller threshold than the main stop-loss to avoid holding losers overnight.
        """
        loss_pct = ((buy_price - current_price) / buy_price) * 100
        return loss_pct >= self.EOD_LOSS_THRESHOLD

    def max_investment(self, capital_remaining: float) -> float:
        """Return the maximum ₹ amount allowed for a single trade."""
        return (self.MAX_SINGLE_TRADE_PCT / 100) * capital_remaining
