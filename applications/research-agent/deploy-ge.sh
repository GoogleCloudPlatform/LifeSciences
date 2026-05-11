#!/bin/bash

# Research Agent - Google AgentSpace Deployment Script
# This script deploys the research agent to Google AgentSpace (Gen App Builder)
# Prerequisites: Agent must already be deployed to Agent Engine via deploy-ae.sh

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Research Agent - AgentSpace Deployment ===${NC}"
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

# Validate required variables for AgentSpace deployment
REQUIRED_VARS=(
    "GOOGLE_CLOUD_PROJECT"
    "GOOGLE_CLOUD_PROJECT_NUMBER"
    "AGENT_ENGINE_ID"
    "AGENT_ENGINE_LOCATION"
    "AGENTSPACE_APP_ID"
    "AGENTSPACE_LOCATION"
)

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo -e "${RED}Error: Required environment variable $var is not set${NC}"
        echo "Please set it in your .env file"
        exit 1
    fi
done

# Build reasoning engine resource name
REASONING_ENGINE="projects/${GOOGLE_CLOUD_PROJECT}/locations/${AGENT_ENGINE_LOCATION}/reasoningEngines/${AGENT_ENGINE_ID}"

# Use display name and description from .env (with defaults)
AGENT_DISPLAY_NAME="${AGENTSPACE_DISPLAY_NAME:-Research Agent}"
AGENT_DESCRIPTION="${AGENTSPACE_DESCRIPTION:-Multi-source research agent with PubMed, Patents, Clinical Trials, and Web Research}"

# Display configuration
echo ""
echo -e "${GREEN}AgentSpace Deployment Configuration:${NC}"
echo "  Project:              $GOOGLE_CLOUD_PROJECT"
echo "  Project Number:       $GOOGLE_CLOUD_PROJECT_NUMBER"
echo "  Agent Engine ID:      $AGENT_ENGINE_ID"
echo "  Agent Engine Location: $AGENT_ENGINE_LOCATION"
echo "  AgentSpace App ID:    $AGENTSPACE_APP_ID"
echo "  AgentSpace Location:  $AGENTSPACE_LOCATION"
echo "  Display Name:         $AGENT_DISPLAY_NAME"
echo ""

# Confirm deployment
read -p "Deploy agent to AgentSpace? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled"
    exit 0
fi

echo ""
echo -e "${YELLOW}Deploying agent to AgentSpace...${NC}"
echo ""

# Discovery Engine API endpoint
DISCOVERY_ENGINE_PROD_API_ENDPOINT="https://discoveryengine.googleapis.com"

# Deploy agent to AgentSpace
RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    -H "x-goog-user-project: ${GOOGLE_CLOUD_PROJECT}" \
    "${DISCOVERY_ENGINE_PROD_API_ENDPOINT}/v1alpha/projects/${GOOGLE_CLOUD_PROJECT_NUMBER}/locations/${AGENTSPACE_LOCATION}/collections/default_collection/engines/${AGENTSPACE_APP_ID}/assistants/default_assistant/agents" \
    -d '{
  "displayName": "'"${AGENT_DISPLAY_NAME}"'",
  "description": "'"${AGENT_DESCRIPTION}"'",
  "icon": {
    "uri": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/science/default/24px.svg"
  },
  "adk_agent_definition": {
    "tool_settings": {
      "toolDescription": "'"${AGENT_DESCRIPTION}"'"
    },
    "provisioned_reasoning_engine": {
      "reasoningEngine": "'"${REASONING_ENGINE}"'"
    }
  }
}')

# Extract HTTP status code
HTTP_STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS:" | cut -d: -f2)
RESPONSE_BODY=$(echo "$RESPONSE" | sed '$d')

# Check if deployment was successful
if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
    echo ""
    echo -e "${GREEN}=== Deployment Successful ===${NC}"
    echo ""
    echo "Your research agent has been deployed to Google AgentSpace"
    echo ""
    echo "Access it in Google Cloud Console:"
    echo "  https://console.cloud.google.com/gen-app-builder/engines?project=$GOOGLE_CLOUD_PROJECT"
    echo ""
    echo "Response:"
    echo "$RESPONSE_BODY" | jq '.' 2>/dev/null || echo "$RESPONSE_BODY"
else
    echo ""
    echo -e "${RED}=== Deployment Failed ===${NC}"
    echo ""
    echo "HTTP Status: $HTTP_STATUS"
    echo "Response:"
    echo "$RESPONSE_BODY" | jq '.' 2>/dev/null || echo "$RESPONSE_BODY"
    echo ""
    echo "Common issues:"
    echo "  1. Invalid Agent Engine ID - verify the ID from Agent Engine deployment"
    echo "  2. Invalid AgentSpace App ID - check the app exists in Gen App Builder"
    echo "  3. Permissions - ensure you have roles/discoveryengine.admin"
    echo "  4. Agent Engine not ready - wait for Agent Engine deployment to complete"
    exit 1
fi
