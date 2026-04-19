PROJECT_ID=project-ea9b6c2e-79c7-47c8-8ac
IMAGE_NAME=trading-bot
REGION=asia-south1

build:
	gcloud builds submit --project $(PROJECT_ID) --tag gcr.io/$(PROJECT_ID)/$(IMAGE_NAME) .

deploy:
	gcloud run jobs deploy trading-job-india \
		--image gcr.io/$(PROJECT_ID)/$(IMAGE_NAME) \
		--region $(REGION) \
		--vpc-egress all-traffic

push: build deploy

deploy-receiver:
	gcloud run deploy kite-receiver \
		--source . \
		--region $(REGION) \
		--vpc-connector trading-connector \
		--vpc-egress all-traffic \
		--allow-unauthenticated \
		--command="python" \
		--args="receiver.py" \
		--set-secrets="KITE_API_KEY=KITE_API_KEY:latest,KITE_API_SECRET=KITE_API_SECRET:latest"
