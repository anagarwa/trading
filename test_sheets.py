import google.auth
from googleapiclient.discovery import build

# REPLACE THIS with your actual Spreadsheet ID from the URL
SPREADSHEET_ID = '1OnxOoYpOl47HtmZq820HZ5UQz9PRLCpCMxLLnks5JN4'

def test_sheet_write():
    print("Checking credentials...")
    # This automatically picks up your Cloud Shell identity
    credentials, project = google.auth.default(
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    
    try:
        service = build('sheets', 'v4', credentials=credentials)
        sheet = service.spreadsheets()
        
        # Prepare a simple "Hello World" row with a timestamp
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = [[now, "Hello World", "Connection Successful!"]]
        
        body = {'values': values}
        
        print(f"Attempting to write to sheet: {SPREADSHEET_ID}...")
        result = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A1", 
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        
        print(f"✅ Success! Updated cells: {result.get('updates').get('updatedCells')}")
        
    except Exception as e:
        print(f"❌ Failed: {e}")

if __name__ == "__main__":
    test_sheet_write()