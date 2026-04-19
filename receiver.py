import os
from flask import Flask, request
from kiteconnect import KiteConnect
from google.cloud import secretmanager

app = Flask(__name__)

# Config
API_KEY = os.environ.get("KITE_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET")
PROJECT_ID = "project-ea9b6c2e-79c7-47c8-8ac"

def update_secret(token_value):
    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{PROJECT_ID}/secrets/KITE_ACCESS_TOKEN"
    client.add_secret_version(
        parent=parent,
        payload={"data": token_value.encode("UTF-8")}
    )

@app.route('/login-callback')
def callback():
    request_token = request.args.get('request_token')
    if not request_token:
        return "❌ Error: No request token provided by Zerodha.", 400
    
    try:
        # 1. Initialize Kite and exchange the token
        kite = KiteConnect(api_key=API_KEY)
        session_data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = session_data["access_token"]
        
        # 2. VALIDATION CALL: Fetch Profile
        # This confirms the access_token is actually working with Zerodha's servers
        kite.set_access_token(access_token)
        user_profile = kite.profile()
        user_name = user_profile.get("user_name", "Trader")
        
        # 3. Only if the profile call succeeds, save to Secret Manager
        update_secret(access_token)
        
        return f"✅ Login Verified! Welcome, {user_name}. Your access token has been securely saved."

    except Exception as e:
        # This will catch things like 'Invalid Checksum' or 'Token Expired'
        return f"❌ Validation Failed: {str(e)}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)