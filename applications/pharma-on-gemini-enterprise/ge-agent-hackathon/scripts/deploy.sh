#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Deploy an agent with agents-cli, handling the known gotchas.
# Usage: ./scripts/deploy.sh <agent-dir-name> <project> [region]
set -euo pipefail
AGENT="${1:?usage: deploy.sh <agent-dir-name> <project> [region]}"
PROJECT="${2:?usage: deploy.sh <agent-dir-name> <project> [region]}"
REGION="${3:-us-central1}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$HERE/agents/$AGENT"
[ -d "$DIR" ] || { echo "No such agent: $AGENT"; exit 1; }
cd "$DIR"

# GOOGLE_APPLICATION_CREDENTIALS is a RESERVED name in the deployed runtime.
# agents-cli copies .env into the runtime spec, so strip it for the deploy only.
RESTORE=0
if [ -f .env ] && grep -q '^GOOGLE_APPLICATION_CREDENTIALS=' .env; then
  cp .env .env.deploybak; grep -v '^GOOGLE_APPLICATION_CREDENTIALS=' .env.deploybak > .env
  RESTORE=1; echo "==> temporarily removed GOOGLE_APPLICATION_CREDENTIALS from .env (reserved in runtime)"
fi
restore(){ [ "$RESTORE" = "1" ] && mv .env.deploybak .env && echo "==> restored .env"; }
trap restore EXIT

echo "==> Deploying $AGENT to $PROJECT / $REGION"
agents-cli deploy --project="$PROJECT" --region="$REGION" --service-name="$AGENT" || {
  echo
  echo "If this failed with 'resource exhaustion in this region', that is the"
  echo "Agent Runtime per-region quota. Retry with a different region, e.g.:"
  echo "    $0 $AGENT $PROJECT us-east4"
  exit 1
}
echo
echo "Copy the full Agent Runtime resource name printed above, then publish:"
echo
echo "  agents-cli publish gemini-enterprise \\"
echo "    --agent-runtime-id=\"projects/<NUMBER>/locations/$REGION/reasoningEngines/<ID>\" \\"
echo "    --gemini-enterprise-app-id=\"projects/<NUMBER>/locations/global/collections/default_collection/engines/<APP>\" \\"
echo "    --display-name=\"$AGENT\" --registration-type=adk --project-id=$PROJECT"
echo
echo "Both IDs must be FULL resource names, and both want the project NUMBER."
