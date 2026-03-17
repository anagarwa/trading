"""
scripts/check_market_open.py

Exits with code 0 if the NSE market is open today, or code 1 if it is a
weekend or a declared NSE trading holiday. Used as a gate step in the
GitHub Actions workflow so no further steps run on closed days.

Holiday data is fetched from the NSE public API and cached for 30 days
in state/nse_holidays.json to avoid excessive requests.
"""

import json
import os
import sys
from datetime import date, datetime

import requests

HOLIDAY_CACHE_FILE = "state/nse_holidays.json"
NSE_HOLIDAY_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
CACHE_TTL_DAYS = 30


def _fetch_holidays_from_nse() -> dict:
    """
    Fetch the NSE holiday master via the public REST endpoint.
    NSE sets strict browser-like header requirements, so we mimic a browser.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    }
    session = requests.Session()
    # Hit the home page first to acquire session cookies required by the API
    session.get("https://www.nseindia.com/", headers=headers, timeout=15)
    resp = session.get(NSE_HOLIDAY_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _load_cached_holidays() -> dict | None:
    if not os.path.exists(HOLIDAY_CACHE_FILE):
        return None
    with open(HOLIDAY_CACHE_FILE) as f:
        data = json.load(f)
    cached_at = data.get("_cached_at", "")
    if cached_at:
        try:
            cached_date = datetime.fromisoformat(cached_at).date()
            if (date.today() - cached_date).days > CACHE_TTL_DAYS:
                return None  # Cache stale
        except ValueError:
            return None
    return data


def _save_holidays(data: dict):
    os.makedirs(os.path.dirname(HOLIDAY_CACHE_FILE), exist_ok=True)
    data["_cached_at"] = date.today().isoformat()
    with open(HOLIDAY_CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_market_open() -> bool:
    today = date.today()

    # Weekends are always closed
    if today.weekday() >= 5:
        return False

    # Try cache, then live fetch
    holidays = _load_cached_holidays()
    if holidays is None:
        try:
            holidays = _fetch_holidays_from_nse()
            _save_holidays(holidays)
        except Exception as e:
            print(f"Warning: could not fetch NSE holiday list: {e}. Assuming market is open.")
            return True  # Conservative: assume open so the broker rejects gracefully if closed

    # NSE returns holidays under the "CM" (capital market) segment
    holiday_dates = [h.get("tradingDate", "") for h in holidays.get("CM", [])]
    today_str = today.strftime("%d-%b-%Y")  # e.g. "15-Mar-2026"
    return today_str not in holiday_dates


if __name__ == "__main__":
    if is_market_open():
        print(f"Market is OPEN today ({date.today()}).")
        sys.exit(0)
    else:
        print(f"Market is CLOSED today ({date.today()}). Skipping trading run.")
        sys.exit(1)
