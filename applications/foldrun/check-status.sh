#!/bin/bash
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

# ==============================================================================
# FoldRun Ecosystem Status Checker
# Use this script to check the deployment progress and health of your environment
# ==============================================================================

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$_SCRIPT_DIR"

PROJECT_ID=${1:?'Usage: ./check-status.sh <PROJECT_ID> [REGION]'}
REGION=${2:-"us-central1"}
TERRAFORM_DIR="terraform"

# Scope gcloud commands to PROJECT_ID without mutating the user's global gcloud config
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"

echo "================================================================================"
echo "🔍 FoldRun Ecosystem Status Checklist"
echo "================================================================================"

echo "Project: $PROJECT_ID"
echo "Region:  $REGION"
echo "--------------------------------------------------------------------------------"

# 1. Check Terraform
if [ -d "$TERRAFORM_DIR/.terraform" ]; then
    echo "✅ [Terraform] Infrastructure is initialized"
    BUCKET_NAME=$(cd $TERRAFORM_DIR && terraform output -raw gcs_bucket_name 2>/dev/null || echo "")
    DB_BUCKET_NAME=$(cd $TERRAFORM_DIR && terraform output -raw databases_bucket_name 2>/dev/null || echo "")
else
    echo "❌ [Terraform] Infrastructure is NOT initialized (run ./deploy-all.sh)"
fi

if [ -n "$BUCKET_NAME" ] && [[ ! "$BUCKET_NAME" == *"No outputs"* ]]; then
    echo "✅ [Storage] Bucket $BUCKET_NAME created"
else
    echo "❌ [Storage] Bucket not found in Terraform state"
fi

# 2. Check Cloud Run Viewer
if gcloud run services describe foldrun-viewer --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "✅ [Cloud Run] foldrun-viewer service is deployed and active"
else
    echo "❌ [Cloud Run] foldrun-viewer service is missing"
fi

# 3. Check Cloud Run Analysis Job
if gcloud run jobs describe foldrun-analysis-job --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "✅ [Cloud Run] foldrun-analysis-job is deployed"
else
    echo "❌ [Cloud Run] foldrun-analysis-job is missing"
fi

# 4. Check Agent Runtime (via REST API since gcloud subcommand is not available)
ACCESS_TOKEN=$(gcloud auth print-access-token 2>/dev/null)
if [ -n "$ACCESS_TOKEN" ]; then
    AGENT_RESULT=$(curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
        "https://$REGION-aiplatform.googleapis.com/v1beta1/projects/$PROJECT_ID/locations/$REGION/reasoningEngines" 2>/dev/null)
    if echo "$AGENT_RESULT" | grep -q "FoldRun_Agent"; then
        echo "✅ [Agent Platform] FoldRun Agent Runtime is deployed"
        AGENT_RUNTIME_ID=$(echo "$AGENT_RESULT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for re_obj in data.get('reasoningEngines', []):
        if 'FoldRun_Agent' in re_obj.get('displayName', ''):
            print(re_obj.get('name', ''))
            break
except Exception:
    pass
" 2>/dev/null)
        if [ -n "$AGENT_RUNTIME_ID" ]; then
            A2A_BASE_URL="https://$REGION-aiplatform.googleapis.com/reasoningEngines/v1/$AGENT_RUNTIME_ID/api/a2a/foldrun_app"
            echo "   🔗 Reasoning Engine: $AGENT_RUNTIME_ID"
            echo "   🔗 A2A Endpoint:     $A2A_BASE_URL"
            echo "   🔗 A2A Agent Card:   $A2A_BASE_URL/.well-known/agent-card.json"
        fi
    else
        echo "❌ [Agent Platform] FoldRun Agent Runtime is missing"
    fi

    # 4b. Check AlphaFold 3 Gemini platform Dedicated Endpoint
    AF3_LOC=$(cd $TERRAFORM_DIR && terraform output -raw af3_endpoint_location 2>/dev/null || echo "")
    if [ -z "$AF3_LOC" ] || [[ "$AF3_LOC" == *"No outputs"* ]] || [ "$AF3_LOC" = "global" ]; then
        AF3_LOC="$REGION"
    fi
    AF3_EP_RESULT=$(curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
        "https://$AF3_LOC-aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/$AF3_LOC/endpoints" 2>/dev/null)
    AF3_EP_STATUS=$(echo "$AF3_EP_RESULT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for ep in data.get('endpoints', []):
        dname = ep.get('displayName', '')
        if 'af3' in dname.lower() or 'alphafold' in dname.lower():
            deployed = len(ep.get('deployedModels', []))
            timeout_s = ep.get('clientConnectionConfig', {}).get('inferenceTimeout', {}).get('seconds', 'default')
            print(f\"{dname}|{ep.get('name', '')}|{deployed}|{timeout_s}\")
            break
except Exception:
    pass
" 2>/dev/null)
    if [ -n "$AF3_EP_STATUS" ]; then
        IFS='|' read -r _ep_dname _ep_name _ep_models _ep_timeout <<< "$AF3_EP_STATUS"
        if [ "$_ep_models" -gt 0 ] 2>/dev/null; then
            echo "✅ [AlphaFold 3] Dedicated Endpoint '$_ep_dname' is READY in $AF3_LOC ($_ep_models deployed model(s), timeout=${_ep_timeout}s)"
        else
            echo "💤 [AlphaFold 3] Dedicated Endpoint '$_ep_dname' is provisioned in $AF3_LOC (dormant at \$0.00/hr, timeout=${_ep_timeout}s)"
        fi
        echo "   🔗 Endpoint: $_ep_name"
    else
        echo "ℹ️  [AlphaFold 3] No AF3 Dedicated Endpoint provisioned in $AF3_LOC (optional: run ./deploy-all.sh $PROJECT_ID $AF3_LOC --steps infra --enable-af3)"
    fi
else
    echo "⚠️  [Agent Platform] Could not retrieve access token to check Agent Runtime"
fi

# 5. Check Data Download State
if [ -n "$DB_BUCKET_NAME" ] && [[ ! "$DB_BUCKET_NAME" == *"No outputs"* ]]; then
    FOLDER_COUNT=$(gcloud storage ls "gs://$DB_BUCKET_NAME/" 2>/dev/null | grep -c "/$")
    # Core databases required for AF2 + OF3 (uniref90, mgnify, pdb_seqres, etc.)
    # Boltz-2 adds boltz2/ when configured — total varies by model selection
    AF2_CORE_PRESENT=$(gcloud storage ls "gs://$DB_BUCKET_NAME/uniref90/" >/dev/null 2>&1 && echo yes || echo no)
    OF3_PRESENT=$(gcloud storage ls "gs://$DB_BUCKET_NAME/of3/" >/dev/null 2>&1 && echo yes || echo no)
    BOLTZ2_PRESENT=$(gcloud storage ls "gs://$DB_BUCKET_NAME/boltz2/" >/dev/null 2>&1 && echo yes || echo no)
    if [ "$FOLDER_COUNT" -gt 0 ]; then
        echo "✅ [Data] Databases present ($FOLDER_COUNT folders)"
        [ "$AF2_CORE_PRESENT" = "yes" ] && echo "   ✅ AF2 core databases (uniref90 etc.)" || echo "   ❌ AF2 core databases missing"
        [ "$OF3_PRESENT" = "yes" ]   && echo "   ✅ OF3 weights + CCD" || echo "   ⚠️  OF3 databases not downloaded"
        [ "$BOLTZ2_PRESENT" = "yes" ] && echo "   ✅ Boltz-2 cache (weights + mols)" || echo "   ❌ Boltz-2 databases not downloaded (run: setup_data.py --models boltz)"
    elif gcloud storage objects describe "gs://$DB_BUCKET_NAME/.deploy-state/data-download-triggered" >/dev/null 2>&1; then
        echo "⏳ [Data] Database download triggered — check Cloud Batch for progress"
    else
        echo "❌ [Data] Databases have NOT been downloaded yet (run ./deploy-all.sh --steps data)"
    fi
else
    echo "❌ [Data] Cannot check data status without a valid DB bucket"
fi

echo "================================================================================"
