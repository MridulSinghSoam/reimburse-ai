#!/usr/bin/env bash
# setup-azure.sh - Creates every Azure resource the project needs.
# Run it once from Azure Cloud Shell (Bash) or any terminal with the Azure CLI logged in (az login).
set -euo pipefail

# ---- CHANGE THESE (names must be globally unique, lowercase, no spaces) ----
PREFIX="reimburseai$RANDOM"          # e.g. reimburseaimridul
LOCATION="centralindia"              # Azure OpenAI may need eastus / swedencentral
RG="rg-reimburse-ai"
APP_NAME="${PREFIX}-app"
# -----------------------------------------------------------------------------

STORAGE="${PREFIX//-/}st"
DOCINTEL="${PREFIX}-docintel"
AOAI="${PREFIX}-openai"
PLAN="${PREFIX}-plan"

echo ">> Resource group"
az group create -n "$RG" -l "$LOCATION" -o none

echo ">> Storage account + private 'bills' container"
az storage account create -n "$STORAGE" -g "$RG" -l "$LOCATION" --sku Standard_LRS --allow-blob-public-access false -o none
STORAGE_CS=$(az storage account show-connection-string -n "$STORAGE" -g "$RG" --query connectionString -o tsv)
az storage container create -n bills --connection-string "$STORAGE_CS" -o none

echo ">> Document Intelligence (free F0 tier)"
az cognitiveservices account create -n "$DOCINTEL" -g "$RG" -l "$LOCATION" --kind FormRecognizer --sku F0 --custom-domain "$DOCINTEL" --yes -o none
DOCINTEL_ENDPOINT=$(az cognitiveservices account show -n "$DOCINTEL" -g "$RG" --query properties.endpoint -o tsv)
DOCINTEL_KEY=$(az cognitiveservices account keys list -n "$DOCINTEL" -g "$RG" --query key1 -o tsv)

echo ">> Azure OpenAI + gpt-4o-mini deployment (skipped if your subscription can't create it)"
AOAI_ENDPOINT=""; AOAI_KEY=""
if az cognitiveservices account create -n "$AOAI" -g "$RG" -l eastus --kind OpenAI --sku S0 --custom-domain "$AOAI" --yes -o none; then
  az cognitiveservices account deployment create -n "$AOAI" -g "$RG" \
    --deployment-name gpt-4o-mini --model-name gpt-4o-mini --model-version "2024-07-18" \
    --model-format OpenAI --sku-name GlobalStandard --sku-capacity 10 -o none || echo "!! Deployment failed; the app will use keyword rules."
  AOAI_ENDPOINT=$(az cognitiveservices account show -n "$AOAI" -g "$RG" --query properties.endpoint -o tsv)
  AOAI_KEY=$(az cognitiveservices account keys list -n "$AOAI" -g "$RG" --query key1 -o tsv)
else
  echo "!! Azure OpenAI not available on this subscription; the app will use keyword rules."
fi

echo ">> App Service (Linux, Python 3.11)"
az appservice plan create -n "$PLAN" -g "$RG" -l "$LOCATION" --is-linux --sku B1 -o none
az webapp create -n "$APP_NAME" -g "$RG" -p "$PLAN" --runtime "PYTHON:3.11" -o none

az webapp config appsettings set -n "$APP_NAME" -g "$RG" -o none --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true \
  DOCINTEL_ENDPOINT="$DOCINTEL_ENDPOINT" DOCINTEL_KEY="$DOCINTEL_KEY" \
  AOAI_ENDPOINT="$AOAI_ENDPOINT" AOAI_KEY="$AOAI_KEY" AOAI_DEPLOYMENT=gpt-4o-mini \
  STORAGE_CONNECTION_STRING="$STORAGE_CS" STORAGE_CONTAINER=bills \
  DATABASE_URL="sqlite:////home/data/reimbursements.db"

az webapp config set -n "$APP_NAME" -g "$RG" -o none \
  --startup-file "gunicorn -w 2 -k uvicorn.workers.UvicornWorker --bind=0.0.0.0:8000 --timeout 120 app.main:app"

echo ">> Allow publish-profile deployments (needed by GitHub Actions)"
az resource update -g "$RG" --name scm --namespace Microsoft.Web \
  --resource-type basicPublishingCredentialsPolicies --parent "sites/$APP_NAME" --set properties.allow=true -o none

echo ""
echo "=================================================================="
echo " Done! Your app will live at: https://${APP_NAME}.azurewebsites.net"
echo " 1) Put this in .github/workflows/deploy.yml -> AZURE_WEBAPP_NAME: ${APP_NAME}"
echo " 2) Copy the XML below into GitHub secret AZURE_WEBAPP_PUBLISH_PROFILE"
echo "=================================================================="
az webapp deployment list-publishing-profiles -n "$APP_NAME" -g "$RG" --xml
