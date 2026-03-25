import logging
from datetime import datetime, timedelta

import pandas as pd
import pytz
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator

from constants import NIFTY50_SYMBOLS, NIFTY_SMALLCAP_50_SYMBOLS

logger = logging.getLogger(__name__)

# Keywords that indicate negative news; presence triggers a news-based sell block
NEGATIVE_NEWS_KEYWORDS = [
    "fraud", "scam", "penalty", "fine", "banned", "arrested",
    "downgrade", "default", "bankrupt", "loss", "probe", "investigation",
    "recall", "ban", "sebi", "enforcement", "cbi", " ed ",
]

YF_SUFFIX = ".NS"

# How much better (relative score) an alternative must be to justify rotation.
# 1.30 = the alternative must score at least 30% higher than the held stock.
# This guards against unnecessary churn and transaction costs.
ROTATION_SCORE_PREMIUM = 1.30


class MarketResearch:

    def __init__(self, broker, stock_universe: list[str] | None = None):
        self.broker = broker
        self.stock_universe = stock_universe or NIFTY50_SYMBOLS

    # ------------------------------------------------------------------
    # Data fetching helpers
    # ------------------------------------------------------------------

    def _yf_symbol(self, symbol: str) -> str:
        return symbol + YF_SUFFIX

    def _fetch_price_history(self, symbol: str, days: int = 90) -> pd.DataFrame:
        """
        Fetch daily OHLCV.  Tries broker API first; falls back to yfinance.
        Returns a DataFrame with columns: Open, High, Low, Close, Volume
        (index = DatetimeIndex).
        """
        try:
            raw = self.broker.get_historical_data(symbol, interval="day", days=days)
            if not raw:
                raise ValueError("Empty response from broker historical API.")
            df = pd.DataFrame(raw)
            df = df.rename(columns={
                "datetime": "Date", "open": "Open", "high": "High",
                "low": "Low", "close": "Close", "volume": "Volume",
            })
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.sort_values("Date").set_index("Date")
            if df.empty:
                raise ValueError("DataFrame is empty after broker fetch.")
            return df
        except Exception as e:
            logger.info(
                f"Broker historical data unavailable for {symbol}: {e}. "
                "Falling back to yfinance."
            )
            ticker = yf.Ticker(self._yf_symbol(symbol))
            df = ticker.history(period="3mo")
            if df.empty:
                raise RuntimeError(f"No historical data found for {symbol} via yfinance.")
            return df

    # ------------------------------------------------------------------
    # Technical indicator computation
    # ------------------------------------------------------------------

    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        close = df["Close"].dropna()
        if len(close) < 30:
            raise ValueError("Insufficient history for indicator computation (need >= 30 bars).")

        rsi_series = RSIIndicator(close=close, window=14).rsi()
        macd_obj = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
        macd_line = macd_obj.macd()
        macd_signal = macd_obj.macd_signal()
        ema20 = EMAIndicator(close=close, window=20).ema_indicator()
        ema50 = EMAIndicator(close=close, window=50).ema_indicator()

        latest_rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else None
        latest_macd = float(macd_line.iloc[-1]) if not macd_line.empty else None
        prev_macd = float(macd_line.iloc[-2]) if len(macd_line) >= 2 else None
        latest_signal = float(macd_signal.iloc[-1]) if not macd_signal.empty else None
        prev_signal = float(macd_signal.iloc[-2]) if len(macd_signal) >= 2 else None
        latest_ema20 = float(ema20.iloc[-1]) if not ema20.empty else None
        latest_ema50 = float(ema50.iloc[-1]) if not ema50.empty else None
        latest_close = float(close.iloc[-1])

        # True if MACD just crossed above signal in the last bar
        macd_cross = bool(
            prev_macd is not None
            and prev_signal is not None
            and latest_macd is not None
            and latest_signal is not None
            and prev_macd <= prev_signal
            and latest_macd > latest_signal
        )
        macd_above_signal = bool(
            latest_macd is not None
            and latest_signal is not None
            and latest_macd > latest_signal
        )
        price_above_ema20 = bool(
            latest_ema20 is not None and latest_close > latest_ema20
        )

        return {
            "rsi": latest_rsi,
            "macd": latest_macd,
            "macd_signal": latest_signal,
            "macd_cross": macd_cross,
            "macd_above_signal": macd_above_signal,
            "ema20": latest_ema20,
            "ema50": latest_ema50,
            "close": latest_close,
            "price_above_ema20": price_above_ema20,
        }

    # ------------------------------------------------------------------
    # News check
    # ------------------------------------------------------------------

    def _has_negative_news(self, symbol: str) -> bool:
        """Return True if any negative-keyword article appeared in the last 48 h."""
        try:
            ticker = yf.Ticker(self._yf_symbol(symbol))
            news_items = ticker.news or []
            cutoff = datetime.now(pytz.utc) - timedelta(hours=48)
            for item in news_items:
                pub_ts = item.get("providerPublishTime", 0)
                pub_dt = datetime.fromtimestamp(pub_ts, tz=pytz.utc)
                if pub_dt < cutoff:
                    continue
                text = (item.get("title", "") + " " + item.get("summary", "")).lower()
                for kw in NEGATIVE_NEWS_KEYWORDS:
                    if kw in text:
                        logger.info(
                            f"Negative news for {symbol}: keyword '{kw.strip()}' "
                            f"in '{item.get('title', '')[:80]}'"
                        )
                        return True
        except Exception as e:
            logger.warning(f"News check failed for {symbol}: {e}")
        return False

    # ------------------------------------------------------------------
    # Public analysis methods
    # ------------------------------------------------------------------

    def analyse_stock(self, symbol: str) -> dict:
        """
        Full technical + news analysis for a single stock already in the portfolio.
        Returns a dict with indicators and hold_signal (bool).
        hold_signal=True means "no reason to sell based on technicals and news".
        """
        try:
            df = self._fetch_price_history(symbol)
            indicators = self._compute_indicators(df)
            quote = self.broker.get_quote(symbol)
            negative_news = self._has_negative_news(symbol)

            rsi = indicators.get("rsi")
            hold_signal = (
                not negative_news
                and indicators["price_above_ema20"]
                and (rsi is None or rsi > 35)
                and indicators["macd_above_signal"]
            )

            return {
                "symbol": symbol,
                "ltp": quote["ltp"],
                "change_pct": quote.get("change_pct", 0),
                **indicators,
                "negative_news": negative_news,
                "hold_signal": hold_signal,
            }
        except Exception as e:
            logger.error(f"Analysis failed for {symbol}: {e}")
            return {"symbol": symbol, "ltp": 0, "hold_signal": False, "error": str(e)}

    def _score_for_hold(self, indicators: dict) -> float:
        """
        Score an already-held stock using more lenient thresholds than a fresh buy.
        Returns 0.0 if the stock fails mandatory hold conditions (force-exit).

        More lenient than buy criteria because exiting a position has a cost
        (brokerage, slippage) and we only want to rotate if there's a clearly
        better opportunity.
          RSI range for hold : 30–70  (vs 40–65 for new buys)
          EMA20 tolerance    : 2 % below EMA20 allowed  (vs strictly above)
        """
        rsi = indicators.get("rsi")
        if rsi is None:
            return 0.5  # uncertain data; give benefit of the doubt

        # Hard exits regardless of alternatives
        if rsi < 30 or rsi > 75:
            return 0.0  # extreme territory

        ema20 = indicators.get("ema20")
        close = indicators.get("close")
        if ema20 and close and close < ema20 * 0.98:
            return 0.0  # more than 2 % below EMA20 — trend broken

        # Score: same formula as buy candidates so scores are comparable
        if indicators.get("macd_cross"):
            macd_weight = 2.0
        elif indicators.get("macd_above_signal"):
            macd_weight = 1.0
        else:
            macd_weight = 0.5  # below signal but not a disqualifier for holding

        rsi_score = rsi / 65.0
        price_factor = 1.0 if indicators.get("price_above_ema20") else 0.85
        return macd_weight * rsi_score * price_factor

    def find_best_rotation_candidate(
        self,
        held_symbol: str,
        projected_capital: float,
    ) -> dict:
        """
        Compare the currently held stock against every other stock in this
        research instance's universe and decide whether to hold or rotate.

        Returns a dict:
          {
            'action':        'hold' | 'rotate' | 'sell_no_replace',
            'held_score':    float,
            'held_analysis': dict,
            'best_candidate': dict | None,
            'reason':        str,
          }
        """
        # 1. Full analysis of the currently held stock
        held_analysis = self.analyse_stock(held_symbol)

        # Mandatory exit: negative news always overrides everything
        if held_analysis.get("negative_news"):
            logger.info(f"[ROTATION] Mandatory exit {held_symbol}: negative news detected.")
            best = self.find_best_buy_candidate(
                capital_remaining=projected_capital, exclude_symbol=held_symbol
            )
            action = "rotate" if best else "sell_no_replace"
            return {
                "action": action,
                "held_score": 0.0,
                "held_analysis": held_analysis,
                "best_candidate": best,
                "reason": f"Negative news for {held_symbol}. {'Rotating to ' + best['symbol'] + '.' if best else 'No replacement found — exiting.'}",
            }

        # 2. Score the held stock
        held_indicators = {
            k: held_analysis.get(k)
            for k in [
                "rsi", "macd", "macd_signal", "macd_cross",
                "macd_above_signal", "ema20", "ema50", "close", "price_above_ema20",
            ]
        }
        held_score = self._score_for_hold(held_indicators)

        if held_score == 0.0:
            # Failed mandatory hold conditions — look for a replacement
            logger.info(f"[ROTATION] {held_symbol} failed hold criteria "
                        f"(RSI={held_analysis.get('rsi')}, above_ema20={held_analysis.get('price_above_ema20')}).")
            best = self.find_best_buy_candidate(
                capital_remaining=projected_capital, exclude_symbol=held_symbol
            )
            action = "rotate" if best else "sell_no_replace"
            return {
                "action": action,
                "held_score": 0.0,
                "held_analysis": held_analysis,
                "best_candidate": best,
                "reason": (
                    f"{held_symbol} technicals deteriorated "
                    f"(RSI={held_analysis.get('rsi', 'N/A')}, "
                    f"above_ema20={held_analysis.get('price_above_ema20')}). "
                    + (f"Rotating to {best['symbol']}." if best else "No replacement found — exiting.")
                ),
            }

        # 3. Find the best alternative (excluding the held stock)
        best = self.find_best_buy_candidate(
            capital_remaining=projected_capital, exclude_symbol=held_symbol
        )

        if best is None:
            logger.info(f"[ROTATION] No alternative candidate found. Holding {held_symbol} (score={held_score:.3f}).")
            return {
                "action": "hold",
                "held_score": held_score,
                "held_analysis": held_analysis,
                "best_candidate": None,
                "reason": f"No better alternative today. Holding {held_symbol} (score={held_score:.3f}).",
            }

        alt_score = best.get("score", 0.0)
        threshold = held_score * ROTATION_SCORE_PREMIUM
        if alt_score >= threshold:
            logger.info(
                f"[ROTATION] Rotating: {best['symbol']} score={alt_score:.3f} "
                f">= {threshold:.3f} ({ROTATION_SCORE_PREMIUM}x held {held_symbol} score={held_score:.3f})."
            )
            return {
                "action": "rotate",
                "held_score": held_score,
                "held_analysis": held_analysis,
                "best_candidate": best,
                "reason": (
                    f"{best['symbol']} (score={alt_score:.3f}) is "
                    f"{alt_score/held_score:.1f}x better than {held_symbol} "
                    f"(score={held_score:.3f}). Rotating."
                ),
            }
        else:
            logger.info(
                f"[ROTATION] Holding {held_symbol} (score={held_score:.3f}). "
                f"Best alt {best['symbol']} (score={alt_score:.3f}) < "
                f"{ROTATION_SCORE_PREMIUM}x threshold."
            )
            return {
                "action": "hold",
                "held_score": held_score,
                "held_analysis": held_analysis,
                "best_candidate": best,
                "reason": (
                    f"Holding {held_symbol} (score={held_score:.3f}). "
                    f"{best['symbol']} (score={alt_score:.3f}) does not clear "
                    f"the {ROTATION_SCORE_PREMIUM}x rotation threshold."
                ),
            }

    def find_best_buy_candidate(
        self,
        capital_remaining: float | None = None,
        exclude_symbol: str | None = None,
    ) -> dict | None:
        """
        Scan all stocks in this research instance's universe and return the
        single best buy candidate, or None if no qualifying stock is found.
        """
        quotes = self.broker.get_quotes_for_symbols(self.stock_universe)
        quote_map = {q["symbol"]: q for q in quotes}

        # Identify the worst-performing stock of the day as a rough sector proxy
        worst_stock = ""
        if quotes:
            worst = min(quotes, key=lambda q: q.get("change_pct", 0))
            worst_stock = worst.get("symbol", "")

        candidates: list[dict] = []

        for symbol in self.stock_universe:
            try:
                # Skip excluded symbol (e.g. the stock we are about to sell)
                if exclude_symbol and symbol == exclude_symbol:
                    continue

                quote = quote_map.get(symbol)
                if not quote:
                    logger.debug(f"No quote found for {symbol}, skipping.")
                    continue

                ltp = quote["ltp"]
                change_pct = quote.get("change_pct", 0)

                # --- Quick pre-filters (no historical data needed) ---

                # Not in panic sell (down more than 2 % intraday)
                if change_pct < -2.0:
                    continue

                # Skip single worst performer of the day
                if symbol == worst_stock:
                    continue

                # Capital check: 1 share must be affordable within 80 % of capital
                if capital_remaining is not None and ltp > 0.80 * capital_remaining:
                    continue

                # Skip if ltp is 0 (market closed / data error)
                if ltp <= 0:
                    continue

                # --- Technical analysis ---
                df = self._fetch_price_history(symbol)
                indicators = self._compute_indicators(df)

                rsi = indicators.get("rsi")
                if rsi is None:
                    continue
                if not (40 <= rsi <= 65):
                    continue
                if not indicators["price_above_ema20"]:
                    continue
                if not (indicators["macd_above_signal"] or indicators["macd_cross"]):
                    continue
                if change_pct < 0:
                    # Stock must be flat or up on the day
                    continue

                # --- Negative news filter ---
                if self._has_negative_news(symbol):
                    continue

                # Score: MACD fresh cross gets double weight + RSI momentum
                macd_score = 2 if indicators["macd_cross"] else 1
                rsi_momentum = rsi / 65.0  # normalise to [~0.6, 1.0] in allowed range
                score = macd_score * rsi_momentum

                candidates.append({
                    **indicators,
                    "symbol": symbol,
                    "ltp": ltp,
                    "change_pct": change_pct,
                    "score": score,
                    "negative_news": False,
                })
                logger.info(
                    f"Candidate: {symbol} | RSI={rsi:.1f} | "
                    f"MACD_cross={indicators['macd_cross']} | score={score:.3f}"
                )

            except Exception as e:
                logger.warning(f"Could not analyse {symbol}: {e}")
                continue

        if not candidates:
            logger.info(f"No qualifying buy candidates found in {len(self.stock_universe)}-stock universe today.")
            return None

        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        logger.info(
            f"Best buy candidate: {best['symbol']} | score={best['score']:.3f} | ltp=₹{best['ltp']:.2f}"
        )
        return best
