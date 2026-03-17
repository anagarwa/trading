# Trading Agent — Coding Assistant Prompt

## Overview

You are building an automated stock trading agent for the Indian equity market. The agent will trade Nifty 50 stocks using either Zerodha Kite Connect or ICICI Direct Breeze Connect APIs, runs as a GitHub Actions workflow, and manages a fixed capital of ₹10,000.

---

## 1. Project Structure

```
trading-agent/
├── .env                        # API credentials (never commit)
├── .env.example                # Template with empty values
├── config.py                   # Central config — broker switch lives here
├── broker/
│   ├── __init__.py
│   ├── base_broker.py          # Abstract base class
│   ├── kite_broker.py          # Zerodha Kite Connect implementation
│   └── breeze_broker.py        # ICICI Direct Breeze implementation
├── agent/
│   ├── __init__.py
│   ├── market_research.py      # Nifty 50 analysis + buy/sell decision logic
│   ├── portfolio.py            # Portfolio state, budget tracking
│   └── risk_manager.py         # Stop-loss, loss-booking logic
├── state/
│   └── portfolio_state.json    # Persisted portfolio state across runs
├── logs/
│   └── trading_log.csv         # Append-only log of every action
├── main.py                     # Entry point — called by GitHub Actions
├── requirements.txt
└── .github/
    └── workflows/
        └── trading.yml         # GitHub Actions schedule
```

---

## 2. Environment Variables (.env)

```env
# Broker switch — set to "kite" or "breeze"
ACTIVE_BROKER=kite

# Zerodha Kite Connect
KITE_API_KEY=your_api_key_here
KITE_API_SECRET=your_api_secret_here
KITE_ACCESS_TOKEN=your_access_token_here

# ICICI Direct Breeze Connect
BREEZE_API_KEY=your_api_key_here
BREEZE_API_SECRET=your_api_secret_here
BREEZE_SESSION_TOKEN=your_session_token_here

# Capital settings
INITIAL_BUDGET=10000

# Optional: Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

---

## 3. Broker Switch (config.py)

```python
import os
from dotenv import load_dotenv

load_dotenv()

# -------------------------------------------------------
# BROKER SWITCH — change ACTIVE_BROKER in .env to toggle
# -------------------------------------------------------
ACTIVE_BROKER = os.getenv("ACTIVE_BROKER", "kite").lower()
# Valid values: "kite" | "breeze"

INITIAL_BUDGET = float(os.getenv("INITIAL_BUDGET", 10000))
```

The broker switch must be read from `.env` so it can also be toggled via a GitHub Actions secret without code changes.

---

## 4. Broker Abstraction Layer

### base_broker.py — Abstract Interface

```python
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

    @abstractmethod
    def place_market_buy(self, symbol: str, quantity: int) -> dict:
        """Place market buy order. Return {order_id, symbol, quantity, status}"""

    @abstractmethod
    def place_market_sell(self, symbol: str, quantity: int) -> dict:
        """Place market sell order. Return {order_id, symbol, quantity, status}"""

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return open positions: [{symbol, quantity, avg_price, ltp, pnl}]"""

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """Return order status for given order_id."""
```

### kite_broker.py — Zerodha Implementation

- Use `kiteconnect` Python package
- Read `KITE_API_KEY`, `KITE_API_SECRET`, `KITE_ACCESS_TOKEN` from env
- Map Nifty 50 symbols to Kite instrument tokens on startup
- Implement all abstract methods from `BaseBroker`
- Exchange: `NSE`, product type: `CNC` for delivery, `MIS` for intraday
- Handle token expiry gracefully — log and abort if access token is stale

### breeze_broker.py — ICICI Direct Implementation

- Use `breeze-connect` Python package
- Read `BREEZE_API_KEY`, `BREEZE_API_SECRET`, `BREEZE_SESSION_TOKEN` from env
- Implement all abstract methods from `BaseBroker`
- Map symbol names to Breeze's stock code format
- Exchange code: `NSE`

### Broker Factory (broker/__init__.py)

```python
from config import ACTIVE_BROKER
from broker.kite_broker import KiteBroker
from broker.breeze_broker import BreezeBroker

def get_broker():
    if ACTIVE_BROKER == "kite":
        return KiteBroker()
    elif ACTIVE_BROKER == "breeze":
        return BreezeBroker()
    else:
        raise ValueError(f"Unknown broker: {ACTIVE_BROKER}. Use 'kite' or 'breeze'.")
```

---

## 5. Portfolio State (state/portfolio_state.json)

This file is committed back to the repo by the GitHub Action after every run so state persists across workflow executions.

```json
{
  "capital_remaining": 10000.00,
  "profit_booked": 0.00,
  "total_losses_taken": 0.00,
  "holdings": [
    {
      "symbol": "RELIANCE",
      "quantity": 2,
      "buy_price": 2450.00,
      "buy_date": "2025-03-10",
      "amount_invested": 4900.00
    }
  ],
  "last_updated": "2025-03-10T09:40:00+05:30",
  "trading_day_complete": false
}
```

### Capital Rules (CRITICAL — enforce strictly)

- `capital_remaining` starts at ₹10,000 and is the ONLY pool available for new trades.
- `profit_booked` accumulates all realised profits. This is NEVER reinvested.
- When a loss is realised: deduct it from `capital_remaining`. It cannot go below 0.
- At no point should `sum(holdings[].amount_invested)` exceed `capital_remaining` before the trade.
- If `capital_remaining` < ₹500, skip buy decisions for the day — not enough to take a meaningful position.

---

## 6. Market Research & Decision Logic (agent/market_research.py)

### Data Sources

Use web search (via `requests` + `BeautifulSoup` or a financial data API) to fetch:

- 1-day and 5-day price change for each Nifty 50 stock
- Intraday candlestick data (5-min OHLCV for the current day)
- RSI (14-period), MACD (12,26,9), 20-day EMA, 50-day EMA
- Sector performance (which sectors are up/down today)
- Any major news for the stock (earnings, regulatory, promoter activity)

Preferred data sources (in order):
1. Broker's own historical/quote API (free, already authenticated)
2. `yfinance` as fallback for historical data
3. NSE India website for index-level data

### Buy Decision Criteria

The agent should recommend a BUY only if ALL of the following are true:

- RSI is between 40–65 (not overbought, not in freefall)
- Price is above 20-day EMA (uptrend confirmation)
- MACD line is above signal line OR just crossed above (momentum)
- Stock is up or flat on the day (not in panic sell)
- No major negative news in the last 48 hours
- Sector is not the worst-performing sector of the day
- The amount needed for a minimum 1 share does not exceed 80% of `capital_remaining`

If multiple stocks qualify, rank by: strongest MACD crossover + highest RSI-momentum score. Pick the top 1 candidate only.

### Position Sizing

- Maximum single trade: 80% of `capital_remaining`
- Calculate quantity as: `floor(max_investment / ltp)`
- Minimum quantity: 1 share
- If calculated quantity is 0, skip the trade

### Sell Decision Criteria

The agent should recommend a SELL at the 12:05 PM and 3:00 PM checks if ANY of the following are true:

**Profit booking:**
- Current profit on position >= 2.5%

**Stop-loss / loss booking (ALWAYS check this first):**
- Current loss on position >= 1.5% → mandatory exit, book the loss
- Stock has broken below 20-day EMA since purchase
- RSI has dropped below 35 (momentum reversal)
- Major negative news detected since purchase

**End-of-day rule (3:00 PM check only):**
- If it is the 3:00 PM run and position is still open, evaluate: if loss < 0.5%, hold overnight. If loss >= 0.5% or if the technical picture has deteriorated (EMA broken, RSI falling), sell before market close (before 3:20 PM).

**Hold criteria:**
- If none of the sell criteria above are met, hold the position.

---

## 7. Risk Manager (agent/risk_manager.py)

```python
class RiskManager:

    STOP_LOSS_PCT = 1.5       # Mandatory sell if loss exceeds this
    PROFIT_TARGET_PCT = 2.5   # Sell if profit exceeds this
    EOD_LOSS_THRESHOLD = 0.5  # At 3 PM: sell if loss >= this
    MAX_SINGLE_TRADE_PCT = 80 # Max % of remaining capital per trade

    def should_stop_loss(self, buy_price, current_price) -> bool:
        loss_pct = ((buy_price - current_price) / buy_price) * 100
        return loss_pct >= self.STOP_LOSS_PCT

    def should_book_profit(self, buy_price, current_price) -> bool:
        profit_pct = ((current_price - buy_price) / buy_price) * 100
        return profit_pct >= self.PROFIT_TARGET_PCT

    def eod_should_sell(self, buy_price, current_price) -> bool:
        loss_pct = ((buy_price - current_price) / buy_price) * 100
        return loss_pct >= self.EOD_LOSS_THRESHOLD
```

All thresholds should also be overridable via environment variables so you can tune without code changes.

---

## 8. main.py — Entry Point

```python
import sys
import json
from datetime import datetime
import pytz

from config import ACTIVE_BROKER, INITIAL_BUDGET
from broker import get_broker
from agent.market_research import MarketResearch
from agent.portfolio import Portfolio
from agent.risk_manager import RiskManager

RUN_TYPE = sys.argv[1]  # "morning" | "midday" | "eod"
IST = pytz.timezone("Asia/Kolkata")

def main():
    broker = get_broker()
    broker.connect()

    portfolio = Portfolio.load()        # Load from state/portfolio_state.json
    risk = RiskManager()
    research = MarketResearch(broker)

    if RUN_TYPE == "morning":
        run_morning_session(broker, portfolio, risk, research)
    elif RUN_TYPE == "midday":
        run_midday_session(broker, portfolio, risk, research)
    elif RUN_TYPE == "eod":
        run_eod_session(broker, portfolio, risk, research)
    else:
        raise ValueError(f"Unknown RUN_TYPE: {RUN_TYPE}")

    portfolio.save()

def run_morning_session(broker, portfolio, risk, research):
    """9:35 AM — Check existing holdings, then look for new trade."""

    # 1. Check existing holdings first
    if portfolio.has_holdings():
        for holding in portfolio.holdings:
            quote = broker.get_quote(holding["symbol"])
            if risk.should_stop_loss(holding["buy_price"], quote["ltp"]):
                execute_sell(broker, portfolio, holding, quote, reason="STOP_LOSS")
                return  # Done for the morning — one trade at a time

        # If holding looks fine, run fresh analysis: keep vs sell + new buy
        for holding in portfolio.holdings:
            quote = broker.get_quote(holding["symbol"])
            analysis = research.analyse_stock(holding["symbol"])
            if not analysis["hold_signal"]:
                execute_sell(broker, portfolio, holding, quote, reason="MORNING_EXIT")
                break

    # 2. Look for new trade if no holdings or capital available
    if portfolio.capital_remaining >= 500 and not portfolio.has_holdings():
        candidate = research.find_best_buy_candidate()
        if candidate:
            execute_buy(broker, portfolio, candidate)

def run_midday_session(broker, portfolio, risk, research):
    """12:05 PM — Decide whether to sell current holdings."""
    if not portfolio.has_holdings():
        log("No holdings. Nothing to evaluate at midday.")
        return

    for holding in portfolio.holdings:
        quote = broker.get_quote(holding["symbol"])
        if (risk.should_stop_loss(holding["buy_price"], quote["ltp"]) or
                risk.should_book_profit(holding["buy_price"], quote["ltp"])):
            execute_sell(broker, portfolio, holding, quote, reason="MIDDAY_EXIT")
            return

    log("Midday check: holding position. Will re-evaluate at EOD.")

def run_eod_session(broker, portfolio, risk, research):
    """3:00 PM — Final check. Sell if needed before market close."""
    if not portfolio.has_holdings():
        log("No holdings at EOD.")
        return

    for holding in portfolio.holdings:
        quote = broker.get_quote(holding["symbol"])
        if (risk.should_stop_loss(holding["buy_price"], quote["ltp"]) or
                risk.should_book_profit(holding["buy_price"], quote["ltp"]) or
                risk.eod_should_sell(holding["buy_price"], quote["ltp"])):
            execute_sell(broker, portfolio, holding, quote, reason="EOD_EXIT")
        else:
            log(f"Holding {holding['symbol']} overnight. Loss within acceptable range.")

def execute_buy(broker, portfolio, candidate):
    max_investment = portfolio.capital_remaining * 0.8
    quantity = int(max_investment // candidate["ltp"])
    if quantity < 1:
        log(f"Skipping {candidate['symbol']} — insufficient capital for 1 share.")
        return
    order = broker.place_market_buy(candidate["symbol"], quantity)
    portfolio.record_buy(candidate["symbol"], quantity, candidate["ltp"])
    log_trade("BUY", candidate["symbol"], quantity, candidate["ltp"], order["order_id"])

def execute_sell(broker, portfolio, holding, quote, reason):
    order = broker.place_market_sell(holding["symbol"], holding["quantity"])
    pnl = (quote["ltp"] - holding["buy_price"]) * holding["quantity"]
    portfolio.record_sell(holding["symbol"], quote["ltp"], pnl)
    log_trade("SELL", holding["symbol"], holding["quantity"], quote["ltp"],
              order["order_id"], reason=reason, pnl=pnl)
```

---

## 9. GitHub Actions Workflow (.github/workflows/trading.yml)

```yaml
name: Trading Agent

on:
  schedule:
    - cron: '5 4 * * 1-5'   # 9:35 AM IST (UTC+5:30 = 04:05 UTC) Mon-Fri
    - cron: '35 6 * * 1-5'  # 12:05 PM IST (06:35 UTC) Mon-Fri
    - cron: '30 9 * * 1-5'  # 3:00 PM IST (09:30 UTC) Mon-Fri
  workflow_dispatch:          # Allow manual trigger for testing
    inputs:
      run_type:
        description: 'Run type: morning | midday | eod'
        required: true
        default: 'morning'

jobs:
  trade:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4
        with:
          token: ${{ secrets.GH_PAT }}  # PAT needed to push state back

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Determine run type
        id: run_type
        run: |
          HOUR=$(TZ="Asia/Kolkata" date +%H)
          MIN=$(TZ="Asia/Kolkata" date +%M)
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            echo "type=${{ github.event.inputs.run_type }}" >> $GITHUB_OUTPUT
          elif [ "$HOUR" = "09" ]; then
            echo "type=morning" >> $GITHUB_OUTPUT
          elif [ "$HOUR" = "12" ]; then
            echo "type=midday" >> $GITHUB_OUTPUT
          else
            echo "type=eod" >> $GITHUB_OUTPUT
          fi

      - name: Check if market is open (skip holidays)
        run: python scripts/check_market_open.py
        # Exit code 0 = market open, 1 = holiday/weekend → skip

      - name: Run trading agent
        env:
          ACTIVE_BROKER: ${{ secrets.ACTIVE_BROKER }}
          KITE_API_KEY: ${{ secrets.KITE_API_KEY }}
          KITE_API_SECRET: ${{ secrets.KITE_API_SECRET }}
          KITE_ACCESS_TOKEN: ${{ secrets.KITE_ACCESS_TOKEN }}
          BREEZE_API_KEY: ${{ secrets.BREEZE_API_KEY }}
          BREEZE_API_SECRET: ${{ secrets.BREEZE_API_SECRET }}
          BREEZE_SESSION_TOKEN: ${{ secrets.BREEZE_SESSION_TOKEN }}
          INITIAL_BUDGET: ${{ secrets.INITIAL_BUDGET }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python main.py ${{ steps.run_type.outputs.type }}

      - name: Commit updated portfolio state
        run: |
          git config user.name "trading-bot"
          git config user.email "bot@trading-agent"
          git add state/portfolio_state.json logs/trading_log.csv
          git diff --staged --quiet || git commit -m "state: update after ${{ steps.run_type.outputs.type }} run"
          git push
```

---

## 10. Market Holiday Check (scripts/check_market_open.py)

```python
"""
Exits with code 1 (skip) if today is an NSE market holiday.
Fetch NSE holiday list from: https://www.nseindia.com/api/holiday-master?type=trading
Cache it in state/nse_holidays.json and refresh monthly.
"""
import sys
import json
import requests
from datetime import date

def is_market_open():
    today = date.today()
    if today.weekday() >= 5:  # Saturday or Sunday
        return False
    # Load cached holiday list or fetch fresh
    try:
        with open("state/nse_holidays.json") as f:
            holidays = json.load(f)
        holiday_dates = [h["tradingDate"] for h in holidays.get("CM", [])]
        return today.strftime("%d-%b-%Y") not in holiday_dates
    except Exception:
        return True  # If uncertain, proceed — broker will reject if closed

if not is_market_open():
    print("Market is closed today. Skipping.")
    sys.exit(1)

sys.exit(0)
```

---

## 11. Logging (logs/trading_log.csv)

Every action must be appended to `logs/trading_log.csv`:

```
timestamp,run_type,broker,action,symbol,quantity,price,order_id,reason,pnl,capital_remaining,notes
2025-03-10T09:40:12+05:30,morning,kite,BUY,RELIANCE,2,2450.00,ORD123,,0,5100.00,RSI=58 MACD_cross=True
2025-03-10T12:07:45+05:30,midday,kite,HOLD,RELIANCE,2,2465.00,,,,5100.00,profit=0.6% below target
2025-03-10T15:02:10+05:30,eod,kite,SELL,RELIANCE,2,2478.00,ORD456,EOD_EXIT,56.00,5156.00,profit_booked
```

---

## 12. Notifications (Optional but Recommended)

Send a Telegram message at each of these events:
- Successful BUY: symbol, quantity, price, amount invested, capital remaining
- Successful SELL: symbol, reason, P&L, capital remaining, profit booked total
- SKIP (no trade found): brief reason
- ERROR: full error message so you can intervene

---

## 13. Requirements (requirements.txt)

```
kiteconnect>=4.2.0
breeze-connect>=1.0.0
yfinance>=0.2.40
pandas>=2.0.0
numpy>=1.26.0
python-dotenv>=1.0.0
requests>=2.31.0
beautifulsoup4>=4.12.0
pytz>=2024.1
ta>=0.11.0          # Technical analysis (RSI, MACD, EMA)
```

---

## 14. Important Constraints — Enforce These in Code

1. **Capital cap**: Total `sum(holdings[].amount_invested)` must NEVER exceed `capital_remaining` at time of purchase.
2. **Profits are sacred**: `profit_booked` is read-only after being written. It is never used for new trades.
3. **Losses reduce the pool permanently**: If ₹500 is lost, `capital_remaining` becomes ₹9,500. You trade with ₹9,500 from then on.
4. **One position at a time**: The agent holds at most 1 stock at any time.
5. **No leveraged products**: Only delivery (CNC) trades. No F&O, no MIS intraday leverage.
6. **Graceful abort on API errors**: If broker authentication fails or an order is rejected, log the error and abort — do not retry blindly.
7. **Dry run mode**: Add a `DRY_RUN=true` env variable that logs all decisions but never places real orders. Use this for testing.

---

## 15. Testing Checklist Before Live Trading

- [ ] Run with `DRY_RUN=true` for 2 weeks and review `trading_log.csv`
- [ ] Verify portfolio state persists correctly across workflow runs
- [ ] Test broker switch: set `ACTIVE_BROKER=breeze` and confirm Breeze orders flow
- [ ] Simulate a stop-loss scenario manually and confirm capital is correctly reduced
- [ ] Confirm `profit_booked` never re-enters `capital_remaining`
- [ ] Trigger `workflow_dispatch` manually for each run type
- [ ] Verify market holiday detection works on a known NSE holiday

---

*Disclaimer: This system is for educational and personal use. Automated trading carries significant financial risk. Always paper-trade first. The developer is solely responsible for any financial outcomes.*
