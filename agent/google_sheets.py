import logging
import google.auth
import httplib2
import google_auth_httplib2
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    """Helper to interact with Google Sheets for portfolio state and logs."""

    def __init__(self, portfolio_sheet_id: str, logs_sheet_id: str):
        self.portfolio_sheet_id = portfolio_sheet_id
        self.logs_sheet_id = logs_sheet_id
        self.credentials, self.project = google.auth.default(
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        # Create an authorized http instance with a timeout to avoid warnings
        http = google_auth_httplib2.AuthorizedHttp(
            self.credentials, http=httplib2.Http(timeout=10)
        )
        self.service = build('sheets', 'v4', http=http)
        self.sheet = self.service.spreadsheets()

    def load_portfolio_state(self) -> dict | None:
        """Reads summary and holdings from the portfolio spreadsheet."""
        if not self.portfolio_sheet_id:
            logger.warning("PORTFOLIO_SHEET_ID not set. Cannot load state from Sheets.")
            return None

        try:
            # 1. Load Summary
            summary_result = self.sheet.values().get(
                spreadsheetId=self.portfolio_sheet_id,
                range="Summary!A2:H2"
            ).execute()
            summary_values = summary_result.get('values', [])
            if not summary_values:
                logger.info("No summary data found in Portfolio Sheet.")
                return None
            
            row = summary_values[0]
            # [last_updated, trading_day_complete, n50_cap, n50_profit, n50_loss, sc50_cap, sc50_profit, sc50_loss]
            # Fill defaults if columns are missing
            while len(row) < 8:
                row.append("0.0")

            state = {
                "last_updated": row[0],
                "trading_day_complete": str(row[1]).upper() == "TRUE",
                "nifty50": {
                    "capital_remaining": float(row[2]),
                    "profit_booked": float(row[3]),
                    "total_losses_taken": float(row[4]),
                    "holdings": []
                },
                "smallcap50": {
                    "capital_remaining": float(row[5]),
                    "profit_booked": float(row[6]),
                    "total_losses_taken": float(row[7]),
                    "holdings": []
                }
            }

            # 2. Load Holdings
            holdings_result = self.sheet.values().get(
                spreadsheetId=self.portfolio_sheet_id,
                range="Holdings!A2:F100"
            ).execute()
            holdings_values = holdings_result.get('values', [])
            
            for h_row in holdings_values:
                if len(h_row) < 6:
                    continue
                # [symbol, pool, qty, buy_price, buy_date, invested]
                symbol = h_row[0]
                pool = h_row[1]
                qty = int(h_row[2])
                buy_price = float(h_row[3])
                buy_date = h_row[4]
                invested = float(h_row[5])

                holding = {
                    "symbol": symbol,
                    "quantity": qty,
                    "buy_price": buy_price,
                    "buy_date": buy_date,
                    "amount_invested": invested
                }

                if pool in state:
                    state[pool]["holdings"].append(holding)
            
            logger.info(f"Successfully loaded portfolio state from Sheets.")
            return state

        except Exception as e:
            logger.error(f"Error loading portfolio from Sheets: {e}")
            return None

    def save_portfolio_state(self, state: dict):
        """Saves summary and holdings to the portfolio spreadsheet."""
        if not self.portfolio_sheet_id:
            logger.warning("PORTFOLIO_SHEET_ID not set. Cannot save state to Sheets.")
            return

        try:
            # 1. Prepare Summary Row
            n50 = state["nifty50"]
            sc50 = state["smallcap50"]
            summary_values = [[
                state["last_updated"],
                str(state["trading_day_complete"]).upper(),
                n50["capital_remaining"],
                n50["profit_booked"],
                n50["total_losses_taken"],
                sc50["capital_remaining"],
                sc50["profit_booked"],
                sc50["total_losses_taken"]
            ]]
            
            self.sheet.values().update(
                spreadsheetId=self.portfolio_sheet_id,
                range="Summary!A2:H2",
                valueInputOption="USER_ENTERED",
                body={'values': summary_values}
            ).execute()

            # 2. Prepare Holdings Rows
            holdings_values = []
            for pool_name in ["nifty50", "smallcap50"]:
                for h in state[pool_name]["holdings"]:
                    holdings_values.append([
                        h["symbol"],
                        pool_name,
                        h["quantity"],
                        h["buy_price"],
                        h["buy_date"],
                        h["amount_invested"]
                    ])
            
            # Clear existing holdings (up to row 100)
            self.sheet.values().clear(
                spreadsheetId=self.portfolio_sheet_id,
                range="Holdings!A2:F100"
            ).execute()

            if holdings_values:
                self.sheet.values().update(
                    spreadsheetId=self.portfolio_sheet_id,
                    range="Holdings!A2",
                    valueInputOption="USER_ENTERED",
                    body={'values': holdings_values}
                ).execute()

            logger.info("Successfully saved portfolio state to Sheets.")

        except Exception as e:
            logger.error(f"Error saving portfolio to Sheets: {e}")

    def append_log(self, row_values: list):
        """Appends a single log row to the logs spreadsheet."""
        if not self.logs_sheet_id:
            logger.warning("LOGS_SHEET_ID not set. Cannot append log to Sheets.")
            return

        try:
            body = {'values': [row_values]}
            self.sheet.values().append(
                spreadsheetId=self.logs_sheet_id,
                range="Logs!A1",
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
        except Exception as e:
            logger.error(f"Error appending log to Sheets: {e}")
