#!/bin/bash

PROJECT_ID="ai-her-485503"
SERVICE_NAME="vapi-restaurant-backend"
REGION="us-central1"

echo "🚀 Deploying [$SERVICE_NAME] to GCP Project [$PROJECT_ID]..."

# Load .env variables
if [ -f .env ]; then
    set -o allexport
    source .env
    set +o allexport
else
    echo "⚠️ .env file not found or not readable. Ensure variables are set in CI/CD or Cloud Run."
fi

# Deploy
gcloud run deploy $SERVICE_NAME \
    --source . \
    --project $PROJECT_ID \
    --region $REGION \
    --allow-unauthenticated \
    --set-env-vars DATABASE_URL="$DATABASE_URL",TWILIO_ACCOUNT_SID="$TWILIO_ACCOUNT_SID",TWILIO_AUTH_TOKEN="$TWILIO_AUTH_TOKEN",TWILIO_PHONE_NUMBER="$TWILIO_PHONE_NUMBER",STORE_PHONE_NUMBER="$STORE_PHONE_NUMBER",TRANSFER_PHONE_NUMBER="$TRANSFER_PHONE_NUMBER"

echo "✅ Deployment initiated."
