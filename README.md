# Autonomous Trading Bot — Nifty 50 (Indian Equity Market)

> **Disclaimer:** This system is for educational and personal use only. Automated trading carries significant financial risk. Always paper-trade (dry-run) for several weeks before using real money. The developer is solely responsible for any financial outcomes.

---

## Table of Contents

1. [What This Bot Does](#1-what-this-bot-does)
2. [Project Structure](#2-project-structure)
3. [Environment Variables Reference](#3-environment-variables-reference)
4. [How Market Research Works](#4-how-market-research-works)
5. [Capital & Risk Rules](#5-capital--risk-rules)
6. [Trading Sessions (When It Runs)](#6-trading-sessions-when-it-runs)
7. [Quick Start — Dry Run (Paper Trading)](#7-quick-start--dry-run-paper-trading)
8. [Switching to Real / Live Trading](#8-switching-to-real--live-trading)
9. [Broker Setup](#9-broker-setup)
10. [Automated Kite Token Refresh](#10-automated-kite-token-refresh)
11. [GitHub Actions Setup](#11-github-actions-setup)
12. [Monitoring & Logs](#12-monitoring--logs)
13. [Testing Checklist Before Going Live](#13-testing-checklist-before-going-live)

---

## 1. What This Bot Does

This is a fully automated, rules-based equity trading agent for the Indian stock market (NSE). It:

- Runs **three times a day** on weekdays via GitHub Actions (or locally) — at market open, midday, and end-of-day.
- Scans all **Nifty 50 stocks** and picks the single best buy opportunity using technical analysis + a news filter.
- Manages a **fixed pool of ₹10,000** with strict capital protection rules.
- Holds **at most one stock at a time** (delivery/CNC trades only — no F&O, no intraday leverage).
- Automatically **books profits** at +2.5% and **cuts losses** at -1.5%.
- Sends **Telegram alerts** for every buy, sell, skip, and error.
- Persists all state in `state/portfolio_state.json` and an append-only `logs/trading_log.csv`.
- Supports **two brokers** out of the box: **Zerodha Kite Connect** and **ICICI Direct Breeze Connect**, switchable with a single environment variable.

---

## 2. Project Structure

```
trading/
├── .env                          # Your credentials — NEVER commit this
├── .env.example                  # Template — copy to .env and fill values
├── config.py                     # Reads all env vars; broker switch lives here
├── constants.py                  # Nifty 50 symbol list
├── requirements.txt
├── main.py                       # Entry point: python main.py <morning|midday|eod>
│
├── broker/
│   ├── base_broker.py            # Abstract interface (all brokers implement this)
│   ├── kite_broker.py            # Zerodha Kite Connect implementation
│   ├── breeze_broker.py          # ICICI Direct Breeze Connect implementation
│   └── __init__.py               # get_broker() factory — reads ACTIVE_BROKER
│
├── agent/
│   ├── portfolio.py              # Loads/saves state; enforces capital rules
│   ├── risk_manager.py           # Stop-loss, profit-target, EOD thresholds
│   ├── market_research.py        # Technical analysis + news scan + buy scoring
│   ├── notifications.py          # Telegram alerts
│   └── __init__.py
│
├── state/
│   └── portfolio_state.json      # Persisted between GitHub Actions runs
├── logs/
│   └── trading_log.csv           # Append-only trade audit trail
│
├── docs/
│   └── index.html                # GitHub Pages redirect page for Kite token exchange
│
├── scripts/
│   └── check_market_open.py      # Skips run on NSE holidays/weekends
│
└── .github/
    └── workflows/
        ├── trading.yml           # Scheduled GitHub Actions workflow (3×/day)
        └── token_exchange.yml    # Kite access_token refresh workflow
```

---

## 3. Environment Variables Reference

Copy `.env.example` to `.env` and fill in every value before running.

### Mandatory

| Variable | Description | Example |
|---|---|---|
| `ACTIVE_BROKER` | Which broker to use: `kite` or `breeze` | `kite` |
| `INITIAL_BUDGET` | Starting capital in ₹ | `10000` |

### Zerodha Kite Connect (required if `ACTIVE_BROKER=kite`)

| Variable | How to get it |
|---|---|
| `KITE_API_KEY` | Kite Developer Console → your app → API Key |
| `KITE_API_SECRET` | Same page as API Key |
| `KITE_ACCESS_TOKEN` | Auto-updated daily by `token_exchange.yml` (see [Automated Kite Token Refresh](#10-automated-kite-token-refresh)) |
| `KITE_TOKEN_DATE` | Auto-set to today's date (YYYY-MM-DD IST) by `token_exchange.yml` — used as stale-token guard |

### ICICI Direct Breeze Connect (required if `ACTIVE_BROKER=breeze`)

| Variable | How to get it |
|---|---|
| `BREEZE_API_KEY` | Breeze Developer Portal → your app |
| `BREEZE_API_SECRET` | Same page as API Key |
| `BREEZE_SESSION_TOKEN` | Generated via Breeze login flow each morning |

### Optional but Recommended

| Variable | Default | Description |
|---|---|---|
| `DRY_RUN` | `false` | Set to `true` to simulate trades without placing real orders |
| `TELEGRAM_BOT_TOKEN` | _(empty)_ | Your Telegram bot token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | _(empty)_ | Your Telegram chat or channel ID |
| `WEBHOOK_SECRET` | _(required for token automation)_ | Random secret string; must match value hardcoded in `docs/index.html` |

### Risk Threshold Overrides (all optional)

These allow you to tune the bot's behaviour without changing code. If not set, the defaults shown below are used.

| Variable | Default | What it controls |
|---|---|---|
| `STOP_LOSS_PCT` | `1.5` | Mandatory sell if loss reaches this % |
| `PROFIT_TARGET_PCT` | `2.5` | Sell and book profit when gain reaches this % |
| `EOD_LOSS_THRESHOLD` | `0.5` | End-of-day: sell if loss exceeds this % to avoid bad overnight holds |
| `MAX_SINGLE_TRADE_PCT` | `80` | Max % of remaining capital to use in a single trade |

---

## 4. How Market Research Works

This is the brain of the bot. On every morning run, `agent/market_research.py` performs a multi-stage pipeline for every Nifty 50 stock.

### Stage 1 — Fetch Live Quotes

The broker API is called to get live Last Traded Price (LTP), intraday change %, volume, and OHLC for all 50 stocks in a single batch request.

### Stage 2 — Quick Pre-Filters (no historical data needed)

These are cheap, fast checks that eliminate obvious non-candidates immediately:

| Filter | Rule |
|---|---|
| Panic sell | Skip if intraday change < -2% (stock is in freefall) |
| Worst performer of the day | Skip the single worst-performing stock as a proxy for sector weakness |
| Affordability | Skip if 1 share costs more than 80% of capital remaining |
| Zero price | Skip if LTP is 0 (data error or circuit breaker) |

### Stage 3 — Historical Data Fetch (last 90 days of daily OHLCV)

For each stock that passed Stage 2, the bot fetches 90 days of daily candlestick data:

- **Primary source:** Broker's own historical data API (free, already authenticated — Kite or Breeze).
- **Fallback:** `yfinance` is used automatically if the broker API fails or returns empty data.

### Stage 4 — Technical Indicator Computation

Using the `ta` (Technical Analysis) library, the following indicators are computed from the daily close prices:

| Indicator | Parameters | Purpose |
|---|---|---|
| **RSI** — Relative Strength Index | 14-period | Momentum filter: avoids overbought/oversold stocks |
| **MACD** — Moving Average Convergence Divergence | Fast=12, Slow=26, Signal=9 | Trend momentum; especially the crossover signal |
| **EMA 20** — Exponential Moving Average | 20-day | Short-term trend; price must be above this |
| **EMA 50** — Exponential Moving Average | 50-day | Medium-term trend context |

### Stage 5 — Buy Signal Criteria (ALL must be true)

A stock only becomes a candidate if it passes every one of these gates:

| Check | Condition | Rationale |
|---|---|---|
| RSI range | 40 ≤ RSI ≤ 65 | Not overbought (>65 = risky entry), not in freefall (<40 = falling knife) |
| Price vs EMA 20 | Close > EMA 20 | Confirms short-term uptrend |
| MACD momentum | MACD line > Signal line **OR** just crossed above | Positive momentum or fresh momentum entry signal |
| Intraday direction | Change % ≥ 0 | Stock is flat or rising today — not in active selling pressure |
| News filter | No negative news in last 48 hours | Avoids earnings shocks, SEBI actions, fraud news, downgrades |

### Stage 6 — Candidate Scoring & Selection

If multiple stocks pass all gates, each is scored:

```
score = macd_weight × rsi_momentum

where:
  macd_weight   = 2  if MACD just freshly crossed above signal  (stronger entry)
                  1  if MACD has been above signal for a while
  rsi_momentum  = RSI / 65   (normalised: higher RSI within the 40–65 band = stronger buy)
```

The **highest-scoring stock is selected as the single buy candidate**. Only one stock is ever bought per day.

### Stage 7 — News Scan (via yfinance)

For each candidate, the bot fetches the latest news headlines from Yahoo Finance and scans the combined title + summary text for negative keywords:

```
fraud, scam, penalty, fine, banned, arrested, downgrade, default,
bankrupt, loss, probe, investigation, recall, ban, SEBI, enforcement, CBI, ED
```

If any keyword match is found in an article published within the last 48 hours, the stock is skipped regardless of its technical score.

### Hold Analysis (for existing positions)

At every run (morning, midday, EOD), the bot also analyses any stock already held:

| Signal | Condition | Action |
|---|---|---|
| Strong hold | No negative news + price above EMA 20 + RSI > 35 + MACD above signal | Keep holding |
| Weak hold / exit | Any of the above conditions fail | Sell at next check |

---

## 5. Capital & Risk Rules

These rules are enforced in code and cannot be bypassed, even accidentally.

| Rule | Detail |
|---|---|
| **Fixed capital pool** | Bot starts with `INITIAL_BUDGET` (default ₹10,000). This is the only pool available for new trades. |
| **Profits are sacred** | Realised profits go into `profit_booked` and are **never re-invested**. Capital is only replenished by the original amount invested, not the profit. |
| **Losses permanently reduce capital** | If a trade loses ₹500, capital_remaining becomes ₹9,500. All future trades are sized against ₹9,500. |
| **One position at a time** | The bot never holds more than one stock simultaneously. |
| **Minimum capital to trade** | If capital_remaining < ₹500, no buy scan is performed. |
| **Max trade size** | 80% of capital_remaining (configurable via `MAX_SINGLE_TRADE_PCT`). |
| **Stop-loss** | Mandatory sell if loss ≥ 1.5% (configurable via `STOP_LOSS_PCT`). |
| **Profit target** | Sell when gain ≥ 2.5% (configurable via `PROFIT_TARGET_PCT`). |
| **EOD loss limit** | At 3 PM if loss ≥ 0.5%, sell before close to avoid a bad overnight hold (configurable via `EOD_LOSS_THRESHOLD`). |
| **Capital guard** | Before every buy, the code verifies `amount_to_invest ≤ capital_remaining`. If this assertion fails, the trade is aborted with an error. |

---

## 6. Trading Sessions (When It Runs)

The bot runs three times per trading day via GitHub Actions cron:

| Session | IST Time | What It Does |
|---|---|---|
| **Morning** | 9:35 AM | Hard stop-loss check → profit-target check → **rotation analysis** (compare held stock vs all Nifty 50) → buy if no holdings |
| **Midday** | 12:05 PM | Same as morning: hard stop-loss → profit-target → **rotation analysis** → buy if no holdings |
| **EOD** | 3:00 PM | **Sell-only.** Stop-loss / profit-target / EOD loss threshold. If sold, goes flat — no buy scan. Next day's morning session finds a new trade. |

**Market Holiday Guard:** Before every run, `scripts/check_market_open.py` checks if today is an NSE trading holiday (weekend or declared holiday). If the market is closed, the entire workflow exits early — no code runs, no state changes.

### Rotation Logic (Morning & Midday)

At every morning and midday session, if the bot holds a stock it does not simply ask "hold or sell?" — it asks **"is this still the best stock I could be holding right now?"**

The rotation pipeline:

1. **Hard exits first** — Stop-loss or profit-target triggered → sell, immediately buy the best available replacement.
2. **Score the held stock** using the same technical indicators (RSI, MACD, EMA), but with slightly more lenient thresholds to avoid unnecessary churn.
3. **Score every other Nifty 50 stock** using the standard buy criteria.
4. **Rotate only if** the best alternative scores ≥ 30% higher than the held stock (`ROTATION_SCORE_PREMIUM = 1.30`). The 30% premium accounts for brokerage and slippage costs.
5. **If held stock fails basic hold criteria** (RSI extremes, price > 2% below EMA20, negative news) → mandatory exit regardless of alternatives. If a replacement is found, rotate; otherwise sit in cash.
6. **If no alternative beats the threshold** → keep holding.

This prevents the bot from trading for the sake of trading while still ensuring you are always in the strongest available position.

---

## 7. Quick Start — Dry Run (Paper Trading)

Dry-run mode logs every decision (buy/sell/skip) to `logs/trading_log.csv` **without placing any real orders**. Use this for at least 2 weeks before going live.

### Step 1 — Clone and install dependencies

```bash
git clone <your-repo-url>
cd trading
pip install -r requirements.txt
```

### Step 2 — Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and set:

```env
ACTIVE_BROKER=kite        # or breeze
DRY_RUN=true              # ← THIS IS THE KEY SETTING FOR DRY RUN

# Fill in your broker credentials (needed even in dry run to fetch market data)
KITE_API_KEY=xxxx
KITE_API_SECRET=xxxx
KITE_ACCESS_TOKEN=xxxx    # Regenerate this daily (see Broker Setup below)

INITIAL_BUDGET=10000
```

> **Note:** Even in dry-run mode the broker API credentials are required because the bot needs to fetch live quotes and historical price data. The `DRY_RUN=true` flag only prevents order placement.

### Step 3 — Run locally

```bash
# Simulate the morning session (9:35 AM logic)
python main.py morning

# Simulate the midday session
python main.py midday

# Simulate the EOD session
python main.py eod
```

### Step 4 — Review the output

```bash
# See every decision the bot made
cat logs/trading_log.csv

# See the current simulated portfolio
cat state/portfolio_state.json
```

### Step 5 — Reset the portfolio for a fresh dry-run period

```bash
# Reset capital to ₹10,000 and clear all holdings
cat > state/portfolio_state.json << 'EOF'
{
  "capital_remaining": 10000.00,
  "profit_booked": 0.00,
  "total_losses_taken": 0.00,
  "holdings": [],
  "last_updated": "2026-03-15T00:00:00+05:30",
  "trading_day_complete": false
}
EOF
```

---

## 8. Switching to Real / Live Trading

> **Only do this after a minimum 2-week dry run with satisfactory results.**

### Step 1 — Change `DRY_RUN` to `false`

In your `.env`:

```env
DRY_RUN=false    # ← Change this
```

Or in GitHub Actions: go to **Settings → Secrets and variables → Actions** and set `DRY_RUN` to `false`.

### Step 2 — Ensure the access token is fresh

Both Kite and Breeze access/session tokens expire **daily**. For Kite, the automated token refresh system handles this — see [Section 10](#10-automated-kite-token-refresh). For Breeze, update `BREEZE_SESSION_TOKEN` manually each morning (Breeze does not support the OAuth redirect flow).

### Step 3 — Reset portfolio state to real capital

Edit `state/portfolio_state.json` to reflect your actual starting capital:

```json
{
  "capital_remaining": 10000.00,
  "profit_booked": 0.00,
  "total_losses_taken": 0.00,
  "holdings": [],
  "last_updated": "2026-03-15T00:00:00+05:30",
  "trading_day_complete": false
}
```

### Step 4 — Verify the GitHub Actions secrets

Confirm all of the following secrets are set in **GitHub → Settings → Secrets and variables → Actions**:

| Secret | Required | Notes |
|---|---|---|
| `GH_PAT` | Yes | Fine-grained PAT — needs **Contents: write** + **Secrets: write** |
| `ACTIVE_BROKER` | Yes | `kite` or `breeze` |
| `INITIAL_BUDGET` | Yes | |
| `DRY_RUN` | Yes | set to `false` for live |
| `KITE_API_KEY` | If using Kite | |
| `KITE_API_SECRET` | If using Kite | |
| `KITE_ACCESS_TOKEN` | If using Kite | Auto-updated by `token_exchange.yml` |
| `KITE_TOKEN_DATE` | If using Kite | Auto-updated by `token_exchange.yml` |
| `WEBHOOK_SECRET` | If using Kite | Random string; also hardcoded in `docs/index.html` |
| `BREEZE_API_KEY` | If using Breeze | |
| `BREEZE_API_SECRET` | If using Breeze | |
| `BREEZE_SESSION_TOKEN` | If using Breeze | Refresh manually each morning |
| `TELEGRAM_BOT_TOKEN` | Recommended | |
| `TELEGRAM_CHAT_ID` | Recommended | |

**Where to set these:**
- GitHub repository → **Settings → Secrets and variables → Actions → New repository secret**.
- These are **repository secrets** (not environment secrets) in the current setup.

### Step 5 — Do a manual workflow dispatch test

In GitHub → Actions → **Trading Agent** → **Run workflow**, select `morning` and verify a real order gets placed (or check logs if you used a limit of 1 share of the cheapest Nifty 50 stock for the first real test).

---

## 9. Broker Setup

### Zerodha Kite Connect

1. Create an app at [https://developers.kite.trade](https://developers.kite.trade).
2. Note your **API Key** and **API Secret**.
3. Set the **redirect URL** in the Kite Developer Console to:
   ```
   https://YOUR_GITHUB_USERNAME.github.io/trading-agent/
   ```
   This is the GitHub Pages page (`docs/index.html`) that captures the `request_token` and automatically triggers the token exchange workflow. See [Section 10](#10-automated-kite-token-refresh) for full setup.

### ICICI Direct Breeze Connect

1. Register at [https://api.icicidirect.com](https://api.icicidirect.com) and create an app.
2. Note your **API Key** and **API Secret**.
3. The **Session Token** is generated by logging into Breeze and extracting the session token from your browser cookies or the Breeze SDK login flow. See the [Breeze Connect documentation](https://github.com/Idirect-Tech/Breeze-Python-SDK).

### Switching Between Brokers

Change a single variable — no code changes needed:

```env
# In .env (local) or GitHub Secrets (CI)
ACTIVE_BROKER=kite    # Use Zerodha
ACTIVE_BROKER=breeze  # Use ICICI Direct
```

---

## 10. Automated Kite Token Refresh

The Kite `access_token` expires every day at 6 AM IST. This system automates the refresh so you never need to manually update GitHub Secrets.

### How It Works (Full Flow)

```
You (each morning before 9:30 AM)
  │
  └─▶ Open in browser:
      https://kite.zerodha.com/connect/login?v=3&api_key=YOUR_API_KEY
          │
          │  (Zerodha login + 2-FA)
          │
          ▼
      Zerodha redirects to GitHub Pages:
      https://YOUR_USERNAME.github.io/trading-agent/?request_token=XXXX
          │
          │  docs/index.html reads request_token from URL
          │  fires POST to GitHub API → triggers token_exchange.yml
          ▼
      GitHub Actions: token_exchange.yml
          │  1. Validates webhook_secret
          │  2. SHA256(api_key + request_token + api_secret)
          │  3. POST → https://api.kite.trade/session/token
          │  4. Updates KITE_ACCESS_TOKEN secret
          │  5. Updates KITE_TOKEN_DATE secret (YYYY-MM-DD IST)
          ▼
      9:35 AM: trading.yml fires
          │  main.py checks KITE_TOKEN_DATE == today → ✅
          └─▶ bot runs with fresh token
```

**From your perspective, the daily effort is: open one URL in your browser, click "Authorise", close the tab. ~10 seconds.**

### One-Time Setup

### Credential Placement Matrix (Important)

| Item | Where to store | Why |
|---|---|---|
| `FINE_GRAINED_PAT` | Hardcoded in `docs/index.html` | Needed by browser JS to call GitHub `workflow_dispatch` API |
| `WEBHOOK_SECRET` | Both places: hardcoded in `docs/index.html` **and** GitHub Repository Secret `WEBHOOK_SECRET` | Request is accepted only if both values match |
| `GH_PAT` | GitHub Repository Secret `GH_PAT` | Used server-side in workflows to push files and update secrets |
| `KITE_API_KEY` | GitHub Repository Secret `KITE_API_KEY` | Used in token exchange workflow |
| `KITE_API_SECRET` | GitHub Repository Secret `KITE_API_SECRET` | Used in token exchange workflow |
| `KITE_ACCESS_TOKEN` | GitHub Repository Secret `KITE_ACCESS_TOKEN` (auto-updated) | Consumed by trading workflow |
| `KITE_TOKEN_DATE` | GitHub Repository Secret `KITE_TOKEN_DATE` (auto-updated) | Freshness guard in `main.py` |

> `FINE_GRAINED_PAT` is intentionally client-side in this GitHub Pages design. Keep its scope minimal and rotate frequently.

#### 1. Enable GitHub Pages

- Push the repo to GitHub.
- Go to **Settings → Pages**.
- Source: **Deploy from a branch**, Branch: `main`, Folder: `/docs`.
- Your redirect page is now live at `https://YOUR_USERNAME.github.io/REPO_NAME/`.

#### 2. Configure `docs/index.html`

Open `docs/index.html` and fill in the four constants at the top of the `<script>` block:

```javascript
const OWNER            = "your-github-username";      // ← GitHub username
const REPO             = "trading-agent";              // ← repo name
const FINE_GRAINED_PAT = "github_pat_...";             // ← see step 3
const WEBHOOK_SECRET   = "your-random-secret";         // ← must match GitHub secret
```

#### 3. Create the Fine-Grained PAT (for the HTML page)

- GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**.
- **Repository access:** this repo only.
- **Permissions (minimum):**
  - `Actions: Read and write`
  - `Metadata: Read` (granted by default on fine-grained PATs)
- Do **not** grant `Contents`, `Secrets`, `Administration`, or org-level scopes.
- Set expiry to **90 days** (you'll get an email reminder to rotate it).
- Copy the token into `docs/index.html` as `FINE_GRAINED_PAT`.

> This PAT is embedded in a public HTML page. The limited scope (Actions:Write only) means the worst an attacker can do is trigger `token_exchange.yml` with a fake token — which fails harmlessly at the Kite API.

#### 4. Add GitHub Secrets

| Secret | Value |
|---|---|
| `GH_PAT` | A **separate** fine-grained PAT (not the same as `FINE_GRAINED_PAT`) |
| `WEBHOOK_SECRET` | The same random string you put in `docs/index.html` |
| `KITE_API_KEY` | From Kite Developer Console |
| `KITE_API_SECRET` | From Kite Developer Console |

For `GH_PAT`, set these repository permissions:

| Permission | Access needed | Why |
|---|---|---|
| `Contents` | `Read and write` | `trading.yml` commits and pushes `state/portfolio_state.json` and `logs/trading_log.csv` |
| `Secrets` | `Read and write` | `token_exchange.yml` updates `KITE_ACCESS_TOKEN` and `KITE_TOKEN_DATE` |
| `Metadata` | `Read` | Required baseline permission |

Optional bootstrap note:
- `KITE_ACCESS_TOKEN` and `KITE_TOKEN_DATE` can be created automatically by `token_exchange.yml` on first successful run.
- If you prefer, you can also create them manually once in GitHub Secrets.

#### 5. Set Redirect URL in Kite Developer Console

Set the **redirect URL** to:
```
https://YOUR_USERNAME.github.io/REPO_NAME/
```

### Token Staleness Guard

`main.py` checks `KITE_TOKEN_DATE` at startup:
- If `KITE_TOKEN_DATE` matches today's date (IST) → proceed normally.
- If it doesn't match (token not yet refreshed) → log an error, send a Telegram alert with the login URL, and exit immediately before any market data calls.

This prevents silent failures where an expired token causes cryptic API errors during a live trading session.

### Future Improvement — Cloudflare Worker

The current architecture embeds a fine-grained PAT inside a public HTML file. While the scope is intentionally minimal, a more secure and elegant solution is to replace `docs/index.html` with a **Cloudflare Worker**:

- The Worker runs server-side (no secrets in client-side JavaScript).
- It receives the Kite redirect, validates the `request_token`, and calls the GitHub API to trigger `token_exchange.yml` — all without exposing any credentials.
- Cloudflare Workers have a generous free tier (100,000 requests/day).
- This would also allow adding rate-limiting and replay-attack protection.

This migration is planned for a future release. The current GitHub Pages approach works well for a personal bot where the risk of token misuse is low.

### Security Checklist for PATs

- Use **two different PATs**:
  - `FINE_GRAINED_PAT` in `docs/index.html` (Actions only).
  - `GH_PAT` in GitHub Secrets (Contents + Secrets write).
- Never reuse your personal all-repo PAT.
- Rotate both PATs every 60-90 days.
- If leaked, revoke immediately in GitHub Developer Settings.

---

## 11. GitHub Actions Setup

Two workflows run in this repo:

| Workflow | File | Trigger |
|---|---|---|
| **Trading Agent** | `trading.yml` | Cron: 3× per trading day + manual dispatch |
| **Kite Token Exchange** | `token_exchange.yml` | `workflow_dispatch` (fired by `docs/index.html`) |

To enable:

1. **Push this repository to GitHub.**
2. **Add all required secrets** (see [Step 4 in Section 8](#step-4--verify-the-github-actions-secrets) and the secret table in [Section 10](#10-automated-kite-token-refresh)).
3. **Enable GitHub Pages** (see [Section 10 — One-Time Setup](#one-time-setup)).
4. **Enable Actions** if disabled: GitHub → Actions → "I understand my workflows, go ahead and enable them".

The trading bot runs automatically at:
- 9:35 AM IST — morning session
- 12:05 PM IST — midday session
- 3:00 PM IST — EOD session

To **trigger manually** for testing:
- GitHub → Actions → **Trading Agent** → **Run workflow** → select `morning`, `midday`, or `eod`.

---

## 12. Monitoring & Logs

### Checking the Bot's Current Status

Run this command **any time** — locally or in CI — to see exactly what the bot holds, unrealised P&L, and recent trades:

```bash
# Offline mode — reads state/portfolio_state.json and logs/trading_log.csv (no broker needed)
python scripts/status.py

# Live mode — connects to your broker and fetches real-time quotes
python scripts/status.py --live
```

**Sample output:**

```
────────────────────────────────────────────────────────────────
  TRADING BOT STATUS  —  LIVE QUOTES
  Last state update : 2026-03-15T12:07:45+05:30
────────────────────────────────────────────────────────────────

  CAPITAL SUMMARY
  Available trading capital:        ₹  2,100.00
  Amount currently invested:        ₹  7,900.00
  Gross portfolio value (approx):   ₹ 10,000.00
  Profit booked (never reinvested): ₹    156.00
  Total losses absorbed:            ₹     44.00
  Net realised P&L:                 +₹   112.00

  CURRENT HOLDINGS  (1 open position)
  Symbol          Qty   Buy Price    Invested         LTP  Unrealised P&L    P&L %
  ──────────────────────────────────────────────────────────────────────────────────
  INFY              3  ₹ 1,950.00  ₹ 5,850.00  ₹ 1,975.00        +₹  75.00   +1.28%
                    Purchased: 2026-03-15

  RECENT TRADES (last 5)
  ...
```

### Where the data lives

| File | What it contains |
|---|---|
| [state/portfolio_state.json](state/portfolio_state.json) | Current capital, holdings, profit booked — committed to the repo after every run |
| [logs/trading_log.csv](logs/trading_log.csv) | Append-only audit trail of every decision: buy, sell, hold, skip, info |

### Portfolio Isolation

**The bot only ever acts on positions it bought itself.** It reads holdings strictly from `state/portfolio_state.json` — it never reads from the broker's full account position list. This means:

- Any stocks you bought manually in your Zerodha/ICICI account are completely invisible to the bot.
- The bot will never sell anything you didn't buy through this system.
- If you manually sell a stock the bot holds, the bot will try to sell it again next session and receive a broker error — the error is logged and the bot aborts gracefully without touching anything else.

### Trading Log CSV columns

Every decision appends a row:

```
timestamp,run_type,broker,action,symbol,quantity,price,order_id,reason,pnl,capital_remaining,notes
2026-03-15T09:40:12+05:30,morning,kite,BUY,RELIANCE,2,2450.00,ORD123,,0,5100.00,RSI=58.2 MACD_cross=True
2026-03-15T12:07:45+05:30,midday,kite,INFO,,,,,,,5100.00,Midday check: holding position
2026-03-15T15:02:10+05:30,eod,kite,SELL,RELIANCE,2,2478.00,ORD456,EOD_EXIT,56.00,5156.00,
```

**Action types:**

| Action | Meaning |
|---|---|
| `BUY` | Real buy order placed |
| `SELL` | Real sell order placed |
| `BUY_DRY` | Dry-run: would have bought |
| `SELL_DRY` | Dry-run: would have sold |
| `INFO` | Informational log (hold, skip, etc.) |

**Sell reason codes:**

| Reason | Trigger |
|---|---|
| `STOP_LOSS` / `MIDDAY_STOP_LOSS` / `EOD_STOP_LOSS` | Loss hit mandatory stop-loss threshold |
| `PROFIT_TARGET` / `MIDDAY_PROFIT` / `EOD_PROFIT` | Gain hit profit-target threshold |
| `ROTATION` / `MIDDAY_ROTATION` | A better Nifty 50 stock was found; rotating |
| `WEAK_TECHNICALS` / `MIDDAY_WEAK_TECHNICALS` | Held stock failed hold criteria; no replacement found |
| `EOD_EXIT` | EOD 0.5% loss threshold breached before close |

### Portfolio State (`state/portfolio_state.json`)

This file is committed back to the repo by GitHub Actions after every run. It acts as persistent memory across workflow executions. You can view it at any time to see current capital, holdings, and booked profits.

### Telegram Alerts

If configured, you receive real-time notifications for:

- ✅ **BUY** — symbol, quantity, price, amount invested, capital remaining
- 💚/🔴 **SELL** — symbol, reason, P&L, capital remaining, total profit booked
- ⏭ **SKIP** — reason why no trade was placed today
- 🚨 **ERROR** — full error message for immediate intervention

---

## 13. Testing Checklist Before Going Live

```
[ ] Ran DRY_RUN=true for at least 2 weeks and reviewed logs/trading_log.csv
[ ] Verified portfolio state persists correctly between GitHub Actions runs
[ ] Confirmed profit_booked never re-enters capital_remaining
[ ] Tested broker switch: set ACTIVE_BROKER=breeze and confirmed quotes flow
[ ] Simulated a stop-loss scenario (manually buy at high price, ltp drops 2%)
[ ] Confirmed capital_remaining is correctly reduced after a loss
[ ] Triggered workflow_dispatch manually for morning, midday, and eod
[ ] Verified market holiday detection skips run on a known NSE holiday
[ ] Confirmed Telegram alerts are received for BUY, SELL, SKIP, ERROR
[ ] GitHub Pages redirect page live at https://YOUR_USERNAME.github.io/REPO_NAME/
[ ] Set redirect URL in Kite Developer Console to GitHub Pages URL
[ ] token_exchange.yml fires successfully after browser login (check Actions tab)
[ ] KITE_TOKEN_DATE secret updated correctly after token exchange
[ ] KITE_ACCESS_TOKEN secret updated correctly after token exchange
[ ] Token staleness check in main.py aborts with Telegram alert if token is stale
[ ] Fine-grained PAT in docs/index.html rotated every 90 days (calendar reminder set)
[ ] GH_PAT has Contents: write + Secrets: write permissions
```
