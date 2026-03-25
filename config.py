import os
from dotenv import load_dotenv

load_dotenv()

# -------------------------------------------------------
# BROKER SWITCH — change ACTIVE_BROKER in .env to toggle
# -------------------------------------------------------
ACTIVE_BROKER = os.getenv("ACTIVE_BROKER", "kite").lower()
# Valid values: "kite" | "breeze"

INITIAL_BUDGET = float(os.getenv("INITIAL_BUDGET", "10000"))

# Per-pool budgets: how much capital each stock universe gets.
# Defaults: 50/50 split of INITIAL_BUDGET.
NIFTY50_BUDGET = float(os.getenv("NIFTY50_BUDGET", str(INITIAL_BUDGET / 2)))
SMALLCAP50_BUDGET = float(os.getenv("SMALLCAP50_BUDGET", str(INITIAL_BUDGET / 2)))

# Telegram notifications (optional)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Dry run — set DRY_RUN=true in .env to log decisions without placing real orders
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
