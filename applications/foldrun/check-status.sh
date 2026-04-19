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

PROJECT_ID=${1:?'Usage: ./check-status.sh <PROJECT_ID> [REGION]'}
REGION=${2:-"us-central1"}
TERRAFORM_DIR="terraform"

# Switch to the correct account and project
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1

echo "================================================================================"
echo "🔍 FoldRun Ecosystem Status Checklist"
echo "================================================================================"

if [ -z "$PROJECT_ID" ]; then
    echo "❌ Error: Project ID not found. Run 'gcloud config set project YOUR_PROJECT_ID'"
    exit 1
fi

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
if gcloud run services describe foldrun-viewer --region=$REGION --project=$PROJECT_ID >/dev/null 2>&1; then
    echo "✅ [Cloud Run] foldrun-viewer service is deployed and active"
else
    echo "❌ [Cloud Run] foldrun-viewer service is missing"
fi

# 2b. Check Cloud Run A2A Proxy (optional)
if gcloud run services describe foldrun-a2a --region=$REGION --project=$PROJECT_ID >/dev/null 2>&1; then
    echo "✅ [Cloud Run] foldrun-a2a A2A proxy is deployed and active"
else
    echo "⚠️  [Cloud Run] foldrun-a2a A2A proxy is not deployed (optional — deploy with src/foldrun-a2a/deploy.sh)"
fi

# 3. Check Cloud Run Analysis Jobs
if gcloud run jobs describe af2-analysis-job --region=$REGION --project=$PROJECT_ID >/dev/null 2>&1; then
    echo "✅ [Cloud Run] af2-analysis-job is deployed"
else
    echo "❌ [Cloud Run] af2-analysis-job is missing"
fi

if gcloud run jobs describe of3-analysis-job --region=$REGION --project=$PROJECT_ID >/dev/null 2>&1; then
    echo "✅ [Cloud Run] of3-analysis-job is deployed"
else
    echo "❌ [Cloud Run] of3-analysis-job is missing"
fi

if gcloud run jobs describe boltz2-analysis-job --region=$REGION --project=$PROJECT_ID >/dev/null 2>&1; then
    echo "✅ [Cloud Run] boltz2-analysis-job is deployed"
else
    echo "⚠️  [Cloud Run] boltz2-analysis-job is not deployed (optional — enabled by BOLTZ2_COMPONENTS_IMAGE)"
fi

# 4. Check Agent Engine (via REST API since gcloud subcommand is not available)
ACCESS_TOKEN=$(gcloud auth print-access-token 2>/dev/null)
if [ -n "$ACCESS_TOKEN" ]; then
    AGENT_RESULT=$(curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
        "https://$REGION-aiplatform.googleapis.com/v1beta1/projects/$PROJECT_ID/locations/$REGION/reasoningEngines" 2>/dev/null)
    if echo "$AGENT_RESULT" | grep -q "FoldRun_Agent"; then
        echo "✅ [Vertex AI] FoldRun Agent Engine is deployed"
    else
        echo "❌ [Vertex AI] FoldRun Agent Engine is missing"
    fi
else
    echo "⚠️  [Vertex AI] Could not retrieve access token to check Agent Engine"
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
        [ "$BOLTZ2_PRESENT" = "yes" ] && echo "   ✅ Boltz-2 cache (weights + mols)" || echo "   ⚠️  Boltz-2 databases not downloaded (optional)"
    elif gcloud storage objects describe "gs://$DB_BUCKET_NAME/.deploy-state/data-download-triggered" >/dev/null 2>&1; then
        echo "⏳ [Data] Database download triggered — check Cloud Batch for progress"
    else
        echo "❌ [Data] Databases have NOT been downloaded yet (run ./deploy-all.sh --steps data)"
    fi
else
    echo "❌ [Data] Cannot check data status without a valid DB bucket"
fi

echo "================================================================================"
