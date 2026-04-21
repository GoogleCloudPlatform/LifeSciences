#!/bin/bash

# Navigate to the project root directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

ENV_FILE="applications/sentinel/.env"

# Source the .env file if it exists to get the current DATA_STORE_ID
if [ -f "$ENV_FILE" ]; then
    # Use allexport to export all variables, then source, then unset allexport
    set -a
    source "$ENV_FILE"
    set +a
fi

# Check if DATA_STORE_ID is empty or not set

if [ -z "$PROJECT_ID" ]; then
    PROJECT_ID=$(gcloud config get-value project)
    export PROJECT_ID
fi

if [ -z "$SOURCE_GCS_BUCKET" ]; then
    SOURCE_GCS_BUCKET="${PROJECT_ID}-docs-source"
    gcloud storage buckets create gs://$SOURCE_GCS_BUCKET --location=us || true
    echo "SOURCE_GCS_BUCKET=$SOURCE_GCS_BUCKET" >> "$ENV_FILE"
    export SOURCE_GCS_BUCKET
fi

if [ -z "$STAGING_GCS_BUCKET" ]; then
    STAGING_GCS_BUCKET="${PROJECT_ID}-docs-staging"
    gcloud storage buckets create gs://$STAGING_GCS_BUCKET --location=us || true
    echo "STAGING_GCS_BUCKET=$STAGING_GCS_BUCKET" >> "$ENV_FILE"
    export STAGING_GCS_BUCKET
fi

if [ -z "$DATA_STORE_ID" ]; then
    echo "DATA_STORE_ID is not provided in .env."
    echo "Creating a new Data Store configured with LayoutParser and an Engine with linking..."
    
    # Run the python script to create the Datastore and capture the output (the new ID)
    NEW_ID=$(.venv/python3.12/bin/python applications/gemini_enterprise_doc_parser/create_datastore.py | tail -n 1)
    
    if [ -z "$NEW_ID" ]; then
        echo "Failed to create a new Data Store. Exiting."
        exit 1
    fi
    
    echo "Successfully created Data Store: $NEW_ID"
    
    # Write the new DATA_STORE_ID to the .env file
    if grep -q "^DATA_STORE_ID=" "$ENV_FILE"; then
        # Replace existing empty or old DATA_STORE_ID
        sed -i "s/^DATA_STORE_ID=.*/DATA_STORE_ID=$NEW_ID/" "$ENV_FILE"
    else
        # Append to the end
        echo "DATA_STORE_ID=$NEW_ID" >> "$ENV_FILE"
    fi
    
    # Export it for the current session
    export DATA_STORE_ID="$NEW_ID"
else
    echo "Using existing DATA_STORE_ID from .env: $DATA_STORE_ID"
fi

# Verify there are documents to process
if [ -z "$(ls -A docs 2>/dev/null)" ]; then
    echo "No documents found in the 'docs' folder. Please add files before running."
    exit 1
fi

echo "Starting the two-phase layout parser import and Gemini processing..."
# Execute the python script to perform the two-phase import
.venv/python3.12/bin/python applications/gemini_enterprise_doc_parser/ingest_clinical_data_with_layout_parser.py

echo "Ingestion and processing pipeline completed."
