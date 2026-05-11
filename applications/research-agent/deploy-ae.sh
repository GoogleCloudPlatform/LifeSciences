#!/bin/bash

# Research Agent - ADK Deployment Script
# This script deploys the research agent to Vertex AI Agent Engine using ADK

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Research Agent Deployment ===${NC}"
echo ""

# Load environment variables from .env file
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Please create a .env file based on .env.example"
    exit 1
fi

echo -e "${YELLOW}Loading configuration from .env...${NC}"
set -a  # Export all variables
source .env
set +a

# Validate required variables
REQUIRED_VARS=(
    "GOOGLE_CLOUD_PROJECT"
    "ADK_STAGING_BUCKET"
    "ADK_REGION"
    "ADK_DISPLAY_NAME"
)

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo -e "${RED}Error: Required environment variable $var is not set${NC}"
        echo "Please set it in your .env file"
        exit 1
    fi
done

# Display configuration
echo ""
echo -e "${GREEN}Deployment Configuration:${NC}"
echo "  Project:        $GOOGLE_CLOUD_PROJECT"
echo "  Region:         $ADK_REGION"
echo "  Staging Bucket: $ADK_STAGING_BUCKET"
echo "  Display Name:   $ADK_DISPLAY_NAME"
if [ -n "$ADK_DESCRIPTION" ]; then
    echo "  Description:    $ADK_DESCRIPTION"
fi
echo ""

# Check if staging bucket exists
echo -e "${YELLOW}Checking staging bucket...${NC}"
if ! gsutil ls "$ADK_STAGING_BUCKET" > /dev/null 2>&1; then
    echo -e "${RED}Error: Staging bucket $ADK_STAGING_BUCKET does not exist${NC}"
    echo ""
    echo "Create it with:"
    echo "  gsutil mb -p $GOOGLE_CLOUD_PROJECT -l $ADK_REGION $ADK_STAGING_BUCKET"
    exit 1
fi
echo -e "${GREEN}✓ Staging bucket exists${NC}"

# Generate requirements.txt from pyproject.toml for deployment
echo ""
echo -e "${YELLOW}Generating requirements.txt from pyproject.toml...${NC}"
uv pip compile pyproject.toml -o research_agent/requirements.txt --quiet
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Generated research_agent/requirements.txt${NC}"
else
    echo -e "${RED}Error: Failed to generate requirements.txt${NC}"
    exit 1
fi

# Confirm deployment
echo ""
read -p "Deploy agent to Vertex AI? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled"
    exit 0
fi

# Build deployment command
DEPLOY_CMD="uv run adk deploy agent_engine \
    --project=$GOOGLE_CLOUD_PROJECT \
    --region=$ADK_REGION \
    --staging_bucket=$ADK_STAGING_BUCKET \
    --display_name=\"$ADK_DISPLAY_NAME\" \
    --absolutize_imports=true \
    --env_file=.env"

# Add optional description if set
if [ -n "$ADK_DESCRIPTION" ]; then
    DEPLOY_CMD="$DEPLOY_CMD \
    --description=\"$ADK_DESCRIPTION\""
fi

# Add the agent directory (deploy the research_agent package)
DEPLOY_CMD="$DEPLOY_CMD \
    research_agent"

echo ""
echo -e "${YELLOW}Note: Environment variables from .env will be embedded in the deployed code${NC}"
echo "The agent will use the following configuration:"
echo "  GOOGLE_CLOUD_PROJECT: $GOOGLE_CLOUD_PROJECT"
echo "  GOOGLE_CLOUD_LOCATION: $GOOGLE_CLOUD_LOCATION"
echo "  ROOT_MODEL: $ROOT_MODEL"
echo "  SYNTHESIS_MODEL: $SYNTHESIS_MODEL"

echo ""
echo -e "${YELLOW}Deploying agent...${NC}"
echo ""

# Execute deployment and capture output
DEPLOY_OUTPUT=$(eval $DEPLOY_CMD 2>&1)
DEPLOY_EXIT_CODE=$?

# Display the output
echo "$DEPLOY_OUTPUT"

# Check output for "Deploy failed" message (ADK sometimes returns 0 even on failure)
if echo "$DEPLOY_OUTPUT" | grep -q "Deploy failed"; then
    DEPLOY_EXIT_CODE=1
fi

# Cleanup generated files
echo ""
echo -e "${YELLOW}Cleaning up generated requirements.txt...${NC}"
rm -f research_agent/requirements.txt
echo -e "${GREEN}✓ Cleaned up generated files${NC}"

if [ $DEPLOY_EXIT_CODE -eq 0 ]; then
    echo ""
    echo -e "${GREEN}=== Deployment Successful ===${NC}"
    echo ""
    echo "Your research agent has been deployed to Vertex AI Agent Engine"
    echo ""
    echo "Access it in Google Cloud Console:"
    echo "  https://console.cloud.google.com/gen-app-builder/agents?project=$GOOGLE_CLOUD_PROJECT"
    echo ""
else
    echo ""
    echo -e "${RED}=== Deployment Failed ===${NC}"
    echo ""
    echo "Common issues:"
    echo "  1. ADK version compatibility - try updating: uv pip install --upgrade google-adk"
    echo "  2. Syntax errors in agent code - check all Python files"
    echo "  3. Missing dependencies - ensure all imports are available"
    echo ""
    echo "Check the error messages above for details"
    exit 1
fi
