import logging
import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)

class GlobalMarketResearch:
    """
    Analyzes Asian markets (China, Japan, Korea) to determine market bias.
    Useful for pre-market (9 AM IST) decisions.
    """

    # Major Asian Indices
    INDICES = {
        "NIKKEI_225": "^N225",      # Japan
        "HANG_SENG": "^HSI",       # Hong Kong / China Proxy
        "KOSPI": "^KS11",          # South Korea
        "SSE_COMPOSITE": "000001.SS" # China (Shanghai)
    }

    def get_asian_market_bias(self) -> dict:
        """
        Returns % change and sentiment for major Asian indices.
        Sentiment: 1.0 (Bullish), 0.0 (Neutral), -1.0 (Bearish)
        """
        results = {}
        bullish_count = 0
        bearish_count = 0

        for name, ticker_sym in self.INDICES.items():
            try:
                ticker = yf.Ticker(ticker_sym)
                # Fetching 2 days to compare current vs previous close
                df = ticker.history(period="2d")
                if len(df) < 2:
                    logger.warning(f"Insufficient data for {name}")
                    continue

                prev_close = df['Close'].iloc[-2]
                current_price = df['Close'].iloc[-1]
                change_pct = ((current_price - prev_close) / prev_close) * 100

                results[name] = round(change_pct, 2)

                if change_pct > 0.5:
                    bullish_count += 1
                elif change_pct < -0.5:
                    bearish_count += 1

            except Exception as e:
                logger.error(f"Failed to fetch {name}: {e}")

        # Overall bias
        if bullish_count >= 2:
            overall_sentiment = 1.0
        elif bearish_count >= 2:
            overall_sentiment = -1.0
        else:
            overall_sentiment = 0.0

        return {
            "indices": results,
            "overall_sentiment": overall_sentiment,
            "bias": "BULLISH" if overall_sentiment > 0 else "BEARISH" if overall_sentiment < 0 else "NEUTRAL"
        }
