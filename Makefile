PROJECT_ID=project-ea9b6c2e-79c7-47c8-8ac
IMAGE_NAME=trading-bot
REGION=asia-south1

# Load variables from .env for local deployment use
ifneq ("$(wildcard .env)","")
    include .env
    export $(shell sed 's/=.*//' .env)
endif

build:
	gcloud builds submit --project $(PROJECT_ID) --tag gcr.io/$(PROJECT_ID)/$(IMAGE_NAME) .

# --- The Trading Bot Job (Needs everything) ---
deploy:
	gcloud run jobs deploy trading-job-india \
		--image gcr.io/$(PROJECT_ID)/$(IMAGE_NAME) \
		--region $(REGION) \
		--vpc-egress all-traffic \
		--args "run" \
		--set-env-vars "PORTFOLIO_SHEET_ID=$(PORTFOLIO_SHEET_ID),LOGS_SHEET_ID=$(LOGS_SHEET_ID),TELEGRAM_CHAT_ID=$(TELEGRAM_CHAT_ID),DRY_RUN=$(DRY_RUN)" \
		--set-secrets "KITE_API_KEY=KITE_API_KEY:latest,KITE_API_SECRET=KITE_API_SECRET:latest,KITE_ACCESS_TOKEN=KITE_ACCESS_TOKEN:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest" \
		--quiet

# --- The Auth Receiver Service (Lean & Focused) ---
deploy-receiver:
	gcloud run deploy kite-receiver \
		--source . \
		--region $(REGION) \
		--vpc-connector trading-connector \
		--vpc-egress all-traffic \
		--allow-unauthenticated \
		--command="python" \
		--args="receiver.py" \
		--set-secrets "KITE_API_KEY=KITE_API_KEY:latest,KITE_API_SECRET=KITE_API_SECRET:latest,KITE_ACCESS_TOKEN=KITE_ACCESS_TOKEN:latest" \
		--quiet

push: build deploy