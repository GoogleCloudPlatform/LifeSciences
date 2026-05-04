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

set -e

# ==============================================================================
# Usage
# ==============================================================================
usage() {
    echo "Usage: $0 [PROJECT_ID] [REGION] [--steps STEPS]"
    echo ""
    echo "Deploy the FoldRun ecosystem (or specific steps)."
    echo ""
    echo "Arguments:"
    echo "  PROJECT_ID   GCP project (default: gcloud config)"
    echo "  REGION       GCP region (default: us-central1)"
    echo ""
    echo "Options:"
    echo "  --steps STEPS          Comma-separated list of steps to run:"
    echo "                           infra      - Enable APIs + Terraform"
    echo "                           build      - Cloud Build (containers + Agent Runtime)"
    echo "                           data       - Download genomic databases to NFS + GCS backup"
    echo "                           convert    - MMseqs2 GPU index conversion (optional, requires data)"
    echo "                           all        - Run infra + build + data (no convert)"
    echo "  --build-target TARGET  With --steps build: which components to rebuild (default: all)."
    echo "                           all            - rebuild everything (default)"
    echo "                           of3            - openfold3-components + agent"
    echo "                           af2            - alphafold-components + agent"
    echo "                           boltz2         - boltz2-components + agent"
    echo "                           viewer         - foldrun-viewer + agent"
    echo "                           agent          - agent only (no container rebuilds)"
    echo "                           of3-analysis   - of3-analysis-job Cloud Run Job"
    echo "                           af2-analysis   - af2-analysis-job Cloud Run Job"
    echo "                           boltz2-analysis- boltz2-analysis-job Cloud Run Job"
    echo "                         Combine with commas: --build-target of3,viewer"
    echo "  --db DATABASE          With --steps data or convert: target a single database."
    echo "                   data options: bfd, small_bfd, mgnify, pdb70, pdb_mmcif,"
    echo "                                pdb_seqres, uniref30, uniref90, uniprot, alphafold_params"
    echo "                   convert options: uniref90, mgnify, small_bfd"
    echo "  --force        With --steps data: re-download even if databases exist in GCS."
    echo "  --clean        Remove generated build artifacts (.egg-info, .venv, etc.)"
    echo ""
    echo "Environment variables:"
    echo "  DOWNLOAD_MODE      Database download mode: reduced (default) or full"
    echo "  AF2_VERSION        AlphaFold2 git commit to build (default: pinned commit)"
    echo "  OF3_VERSION        OpenFold3 Docker image tag to use (default: 0.4.0)"
    echo "  BOLTZ_VERSION      Boltz-2 pip package version to install (default: 2.2.1)"
    echo ""
    echo "  The following override the auto-detected naming conventions for"
    echo "  --steps build and --steps data (terraform not required if infra exists):"
    echo "  GCS_BUCKET         Pipeline data bucket (default: PROJECT_ID-foldrun-data)"
    echo "  DATABASES_BUCKET   Genomic databases bucket (default: PROJECT_ID-foldrun-gdbs)"
    echo "  AR_REPO            Artifact Registry repo name (default: foldrun-repo)"
    echo "  FILESTORE_ID       Filestore instance ID (default: foldrun-nfs)"
    echo "  AGENT_SA_EMAIL     Agent service account email"
    echo "  BUILD_SA_EMAIL     Cloud Build service account email"
    echo "  PIPELINES_SA_EMAIL Agent Platform Pipelines service account email"
    echo ""
    echo "Examples:"
    echo "  $0                                      # Full deploy (infra + build + data)"
    echo "  $0 my-project us-central1               # Full deploy to specific project"
    echo "  $0 --steps infra                        # Only run Terraform"
    echo "  $0 --steps build                        # Only run Cloud Build"
    echo "  $0 --steps data                         # Download all genomic databases"
    echo "  $0 --steps data --db uniref90           # Resubmit a single database download"
    echo "  $0 --steps convert                      # Convert all MMseqs2-indexable databases"
    echo "  $0 --steps convert --db mgnify          # Convert a single database"
    echo "  $0 --steps data --force                  # Re-download even if GCS has the data"
    echo "  $0 --steps data,convert                             # Download + convert (full pipeline)"
    echo "  $0 --steps infra,build                              # Terraform + Cloud Build (no data)"
    echo "  $0 --steps build --build-target of3                 # Rebuild only OF3 container + agent"
    echo "  $0 --steps build --build-target agent               # Redeploy agent only (fast)"
    echo "  $0 --steps build --build-target of3,viewer          # Rebuild OF3 + viewer + agent"
    echo "  $0 --steps build --build-target of3-analysis        # Rebuild only OF3 analysis job"
    echo "  DOWNLOAD_MODE=full $0                               # Full deploy with all databases"
    exit 1
}

# ==============================================================================
# Parse arguments
# ==============================================================================
STEPS="all"
BUILD_TARGET="all"
DB_NAME=""
FORCE_DATA=false
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --steps)
            STEPS="$2"
            shift 2
            ;;
        --build-target)
            BUILD_TARGET="$2"
            shift 2
            ;;
        --db)
            DB_NAME="$2"
            shift 2
            ;;
        --force)
            FORCE_DATA=true
            shift
            ;;
        --clean)
            echo "Cleaning generated build artifacts..."
            rm -rf foldrun-agent/.venv
            rm -rf foldrun-agent/*.egg-info
            rm -rf foldrun-agent/__pycache__
            find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
            find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
            find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
            echo "Done."
            exit 0
            ;;
        --help|-h)
            usage
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# ==============================================================================
# Optional .env file support
# Load a .env file from the script directory if present. Only sets variables
# that are not already set in the environment, so explicit env var overrides
# and CI/CD injected values always take precedence.
# ==============================================================================
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$_SCRIPT_DIR/.env" ]]; then
    echo "Loading configuration from $_SCRIPT_DIR/.env"
    while IFS='=' read -r key val; do
        # Skip comments, empty lines, and invalid identifiers
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        # Only set if not already in the environment
        [[ -z "${!key+x}" ]] && export "$key"="$val"
    done < "$_SCRIPT_DIR/.env"
fi

# ==============================================================================
# Configuration
# ==============================================================================
PROJECT_ID=${POSITIONAL_ARGS[0]:-$(gcloud config get-value project)}

if [[ "$PROJECT_ID" == "{self.config.project_id}" ]]; then
    echo "ERROR: You passed the literal string '{self.config.project_id}' as the project ID."
    echo "Please replace it with your actual GCP project ID (e.g., my-gcp-project)."
    exit 1
fi

REGION=${POSITIONAL_ARGS[1]:-"us-central1"}
DOWNLOAD_MODE=${DOWNLOAD_MODE:-"reduced"}
AF2_VERSION=${AF2_VERSION:-"42719e135a62438aa651d2bc1d143626083c3703"}
OF3_VERSION=${OF3_VERSION:-"0.4.0"}
BOLTZ_VERSION=${BOLTZ_VERSION:-"2.2.1"}
IAP_ACCESS_DOMAIN=${IAP_ACCESS_DOMAIN:-$(gcloud config get-value account | awk -F '@' '{print $2}')}
TERRAFORM_DIR="terraform"
BUCKET_NAME="${PROJECT_ID}-foldrun-data"

# Parse steps into flags
run_infra=false
run_build=false
run_data=false
run_convert=false

if [[ "$STEPS" == "all" ]]; then
    run_infra=true
    run_build=true
    run_data=true
    # Note: convert is NOT included in 'all' — it's opt-in
else
    IFS=',' read -ra STEP_ARRAY <<< "$STEPS"
    for step in "${STEP_ARRAY[@]}"; do
        case "$step" in
            infra)   run_infra=true ;;
            build)   run_build=true ;;
            data)    run_data=true ;;
            convert) run_convert=true ;;
            *)       echo "Unknown step: $step"; usage ;;
        esac
    done
fi

echo "================================================================================"
echo "FoldRun Ecosystem Deployment"
echo "================================================================================"
echo "Project:       $PROJECT_ID"
echo "Region:        $REGION"
echo "IAP Domain:    $IAP_ACCESS_DOMAIN"
echo "Download Mode: $DOWNLOAD_MODE"
echo "Steps:         $STEPS"
echo "Build Target:  $BUILD_TARGET"
echo ""

# ==============================================================================
# Helper: Resolve deployment configuration for build and data steps.
#
# All FoldRun resources follow predictable naming conventions derived from
# PROJECT_ID — no terraform required for build/data steps run against an
# already-provisioned project.
#
# Priority (highest to lowest):
#   1. Environment variable overrides (for custom deployments or CI/CD)
#   2. Terraform outputs (if terraform is installed and state is accessible)
#   3. FoldRun naming conventions (PROJECT_ID-based defaults)
#
# This means `--steps build` and `--steps data` work without terraform
# installed, as long as infra was previously provisioned by `--steps infra`.
# ==============================================================================
extract_terraform_outputs() {
    # Apply naming-convention defaults first (no terraform needed)
    export GCS_BUCKET="${GCS_BUCKET:-${PROJECT_ID}-foldrun-data}"
    export AR_REPO="${AR_REPO:-foldrun-repo}"
    export FILESTORE_ID="${FILESTORE_ID:-foldrun-nfs}"
    export AGENT_SA_EMAIL="${AGENT_SA_EMAIL:-foldrun-agent-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
    export BUILD_SA_EMAIL="${BUILD_SA_EMAIL:-foldrun-build-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
    export PIPELINES_SA_EMAIL="${PIPELINES_SA_EMAIL:-pipelines-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
    export DATABASES_BUCKET="${DATABASES_BUCKET:-${PROJECT_ID}-foldrun-gdbs}"
    export SUBNET_ID="${SUBNET_ID:-}"
    export NETWORK_ID="${NETWORK_ID:-}"
    export NETWORK_PROJECT_NUMBER="${NETWORK_PROJECT_NUMBER:-}"

    # If terraform is available, cross-check naming-convention defaults against
    # actual state outputs (important for Shared VPC and custom resource names).
    # Uses terraform output -json which is stable and safe to machine-parse —
    # no box-drawing chars or ANSI codes, returns {} cleanly on empty state.
    if command -v terraform &>/dev/null; then
        _tf_env=$(mktemp)
        (
            cd "$TERRAFORM_DIR" || exit 0
            terraform init -reconfigure -input=false > /dev/null 2>&1 || exit 0
            # -json outputs a stable JSON object; empty state returns {}
            tf_json=$(terraform output -json 2>/dev/null) || tf_json="{}"
            # Extract each key if present and non-null in the JSON
            _tf() { echo "$tf_json" | python3 -c \
                "import json,sys; d=json.load(sys.stdin); v=d.get('$1',{}).get('value',''); print(v) if v else None" \
                2>/dev/null || true; }
            v=$(_tf gcs_bucket_name);        if [[ -n "$v" ]]; then echo "GCS_BUCKET=$v"; fi
            v=$(_tf artifact_registry_repo); if [[ -n "$v" ]]; then echo "AR_REPO=$v"; fi
            v=$(_tf filestore_id);           if [[ -n "$v" ]]; then echo "FILESTORE_ID=$v"; fi
            v=$(_tf agent_sa_email);         if [[ -n "$v" ]]; then echo "AGENT_SA_EMAIL=$v"; fi
            v=$(_tf build_sa_email);         if [[ -n "$v" ]]; then echo "BUILD_SA_EMAIL=$v"; fi
            v=$(_tf pipelines_sa_email);     if [[ -n "$v" ]]; then echo "PIPELINES_SA_EMAIL=$v"; fi
            v=$(_tf databases_bucket_name);  if [[ -n "$v" ]]; then echo "DATABASES_BUCKET=$v"; fi
            v=$(_tf subnet_id);              if [[ -n "$v" ]]; then echo "SUBNET_ID=$v"; fi
            v=$(_tf network_id);             if [[ -n "$v" ]]; then echo "NETWORK_ID=$v"; fi
            v=$(_tf network_project_number); if [[ -n "$v" ]]; then echo "NETWORK_PROJECT_NUMBER=$v"; fi
        ) > "$_tf_env" 2>/dev/null || true
        while IFS='=' read -r key val; do
            [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && export "$key"="$val"
        done < "$_tf_env"
        rm -f "$_tf_env"
    fi

    echo "Configuration:"
    echo "  GCS_BUCKET=$GCS_BUCKET"
    echo "  AR_REPO=$AR_REPO"
    echo "  FILESTORE_ID=$FILESTORE_ID"
    echo "  DATABASES_BUCKET=$DATABASES_BUCKET"
    echo "  AGENT_SA_EMAIL=$AGENT_SA_EMAIL"
    echo "  BUILD_SA_EMAIL=$BUILD_SA_EMAIL"
    echo "  PIPELINES_SA_EMAIL=$PIPELINES_SA_EMAIL"
    echo "  SUBNET_ID=$SUBNET_ID"
    echo "  NETWORK_ID=$NETWORK_ID"
    echo "  NETWORK_PROJECT_NUMBER=$NETWORK_PROJECT_NUMBER"
}

# ==============================================================================
# Step: infra
# ==============================================================================
if $run_infra; then
    echo "Step 1: Ensuring foundational APIs are enabled..."
    gcloud services enable cloudresourcemanager.googleapis.com \
        serviceusage.googleapis.com \
        storage.googleapis.com \
        --project=$PROJECT_ID

    TF_STATE_BUCKET="${PROJECT_ID}-tfstate-foldrun"
    echo "Step 2: Configuring Terraform Remote State in GCS: gs://${TF_STATE_BUCKET}"

    if ! gcloud storage buckets describe gs://${TF_STATE_BUCKET} --project=$PROJECT_ID >/dev/null 2>&1; then
        echo "   Creating dedicated state bucket..."
        gcloud storage buckets create gs://${TF_STATE_BUCKET} \
            --project=$PROJECT_ID \
            --location=$REGION \
            --uniform-bucket-level-access
        gcloud storage buckets update gs://${TF_STATE_BUCKET} --versioning
    else
        echo "   State bucket already exists."
    fi

    cat <<EOF > ${TERRAFORM_DIR}/backend.tf
terraform {
  backend "gcs" {
    bucket = "${TF_STATE_BUCKET}"
    prefix = "terraform/state/foldrun"
  }
}
EOF

    echo "Step 3: Provisioning Infrastructure with Terraform"
    cd "$TERRAFORM_DIR"
    terraform init -reconfigure
    echo "Running terraform apply..."
    terraform apply -auto-approve \
        -var="project_id=${PROJECT_ID}" \
        -var="region=${REGION}" \
        -var="bucket_name=${BUCKET_NAME}" \
        -var="iap_access_domain=${IAP_ACCESS_DOMAIN}"
    cd ..
fi

# ==============================================================================
# Step: build
# ==============================================================================
if $run_build; then
    extract_terraform_outputs

    echo "Step 4: Building and Deploying Applications (Cloud Build)"
    echo "Substitutions:"
    echo "  _REGION=${REGION}"
    echo "  _BUCKET_NAME=${GCS_BUCKET}"
    echo "  _FILESTORE_ID=${FILESTORE_ID}"
    echo "  _AR_REPO=${AR_REPO}"
    echo "  _AGENT_SA_EMAIL=${AGENT_SA_EMAIL}"
    echo "  _PIPELINES_SA_EMAIL=${PIPELINES_SA_EMAIL}"
    echo "  _DATABASES_BUCKET=${DATABASES_BUCKET}"
    echo "  _DOWNLOAD_MODE=${DOWNLOAD_MODE}"

    gcloud builds submit . \
        --config cloudbuild.yaml \
        --project "$PROJECT_ID" \
        --substitutions=_REGION="$REGION",_BUCKET_NAME="$GCS_BUCKET",_FILESTORE_ID="$FILESTORE_ID",_AR_REPO="$AR_REPO",_AGENT_SA_EMAIL="$AGENT_SA_EMAIL",_PIPELINES_SA_EMAIL="$PIPELINES_SA_EMAIL",_DATABASES_BUCKET="$DATABASES_BUCKET",_NETWORK_ID="$NETWORK_ID",_NETWORK_PROJECT_NUMBER="$NETWORK_PROJECT_NUMBER",_AF2_VERSION="$AF2_VERSION",_OF3_VERSION="$OF3_VERSION",_BOLTZ_VERSION="$BOLTZ_VERSION",_BUILD_TARGET="$BUILD_TARGET" \
        --machine-type=e2-highcpu-8 \
        --service-account="projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA_EMAIL}"
fi

# ==============================================================================
# Step: data
# ==============================================================================
if $run_data; then
    extract_terraform_outputs

    echo "Step 5: Database Setup"
    cd foldrun-agent
    export GCP_PROJECT_ID=$PROJECT_ID
    export GCP_REGION=$REGION
    export GCS_BUCKET_NAME=$GCS_BUCKET
    export GCS_DATABASES_BUCKET=${DATABASES_BUCKET:-$GCS_BUCKET}
    export ALPHAFOLD_COMPONENTS_IMAGE=${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/alphafold-components:latest
    export OPENFOLD3_COMPONENTS_IMAGE=${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/openfold3-components:latest
    export BOLTZ2_COMPONENTS_IMAGE=${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/boltz2-components:latest
    export DOWNLOAD_MODE
    DB_ARG=""
    if [[ -n "$DB_NAME" ]]; then
        DB_ARG="--db $DB_NAME"
    fi
    FORCE_ARG=""
    if $FORCE_DATA; then
        FORCE_ARG="--force"
    fi

    # If GCS_SOURCE_BUCKET is already set (e.g., from env), use it directly.
    # If a specific --db target is given, skip the menu and download from internet.
    if [[ -n "${GCS_SOURCE_BUCKET:-}" ]]; then
        echo "Restoring databases from GCS: gs://${GCS_SOURCE_BUCKET}/"
        uv run python scripts/setup_data.py $DB_ARG $FORCE_ARG --source-bucket "$GCS_SOURCE_BUCKET"
    elif [[ -n "$DB_NAME" ]]; then
        echo "Downloading $DB_NAME from internet..."
        uv run python scripts/setup_data.py $DB_ARG $FORCE_ARG
    else
        echo ""
        echo "================================================================================"
        echo "  Database Setup — Genetic databases are required for structure prediction."
        echo "================================================================================"
        echo ""
        echo "  FoldRun needs ~500GB of genetic databases (UniRef90, MGnify, PDB, etc.)"
        echo "  on the NFS Filestore. Choose how to populate them:"
        echo ""
        echo "  1) Download from internet"
        echo "     - Fetches from upstream sources (UniProt, EBI, RCSB, AWS S3)"
        echo "     - Takes 2-4 hours depending on network speed"
        echo "     - Automatically backs up to this project's GCS bucket"
        echo "     - Use this for: first-time setup with no existing backups"
        echo ""
        echo "  2) Restore from a GCS bucket"
        echo "     - Copies from an existing GCS backup (~15 min, internal GCP network)"
        echo "     - Can be this project's bucket (rehydrate after Filestore rebuild)"
        echo "       or another project's bucket (shared setup from a colleague)"
        echo "     - Cross-project: the source bucket owner must grant your Compute SA"
        echo "       roles/storage.objectViewer on their bucket"
        echo "     - Automatically backs up to this project's GCS (unless same bucket)"
        echo "     - Use this for: fast setup, disaster recovery, team onboarding"
        echo ""
        echo "  3) Skip — set up databases later"
        echo "     - The agent will deploy but predictions will fail until databases"
        echo "       are available. Run 'scripts/setup_data.py' manually later."
        echo ""
        read -p "  Choose [1/2/3]: " db_choice

        case "$db_choice" in
            2)
                echo ""
                echo "  Enter the GCS bucket name containing the database backups."
                echo "  Examples:"
                echo "    - Same project (rehydrate):  ${GCS_DATABASES_BUCKET:-$GCS_BUCKET}"
                echo "    - Another project (shared):  colleague-project-alphafold-bfds"
                echo ""
                read -p "  Bucket name: " source_bucket
                if [[ -z "$source_bucket" ]]; then
                    echo "ERROR: No bucket name provided. Skipping database setup."
                else
                    echo ""
                    echo "Restoring from gs://${source_bucket}/ → NFS"
                    if [[ "$source_bucket" != "${GCS_DATABASES_BUCKET:-$GCS_BUCKET}" ]]; then
                        echo "Will also backup to gs://${GCS_DATABASES_BUCKET:-$GCS_BUCKET}/"
                    fi
                    uv run python scripts/setup_data.py $DB_ARG $FORCE_ARG --source-bucket "$source_bucket"
                fi
                ;;
            3)
                echo ""
                echo "Skipping database setup. Run manually later:"
                echo "  cd foldrun-agent && uv run python scripts/setup_data.py --models af2,of3"
                ;;
            *)
                echo ""
                echo "Downloading from internet..."
                uv run python scripts/setup_data.py $DB_ARG $FORCE_ARG
                ;;
        esac
    fi
    cd ..
fi

# ==============================================================================
# Step: convert (MMseqs2 GPU index conversion — optional)
# ==============================================================================
if $run_convert; then
    extract_terraform_outputs

    echo "Step 6: MMseqs2 GPU Index Conversion (optional)"
    echo "  Machine: n1-highmem-32 (208 GB RAM, 32 vCPUs)"
    echo "  Storage: 2x local SSD RAID-0 (750 GB NVMe)"
    echo "  Index:   --split 1 (single-pass)"
    cd foldrun-agent
    export GCP_PROJECT_ID=$PROJECT_ID
    export GCP_REGION=$REGION
    export GCS_DATABASES_BUCKET=${DATABASES_BUCKET:-${PROJECT_ID}-foldrun-gdbs}

    # Determine which databases to convert
    if [[ -n "$DB_NAME" ]]; then
        CONVERT_DBS=("$DB_NAME")
    else
        CONVERT_DBS=("uniref90" "mgnify" "small_bfd")
    fi

    for db in "${CONVERT_DBS[@]}"; do
        case "$db" in
            uniref90)   FASTA="uniref90.fasta"; MMSEQS="uniref90_mmseqs"; SUBDIR="uniref90" ;;
            mgnify)     FASTA="mgy_clusters_2022_05.fa"; MMSEQS="mgnify_mmseqs"; SUBDIR="mgnify" ;;
            small_bfd)  FASTA="bfd-first_non_consensus_sequences.fasta"; MMSEQS="small_bfd_mmseqs"; SUBDIR="small_bfd" ;;
            *)          echo "  Skipping $db — not an MMseqs2-indexable database"; continue ;;
        esac

        echo "  Converting: $db"
        uv run python -c "
from foldrun_app.models.af2.tools.download_database import ConvertMMseqs2Tool
import os
os.environ.setdefault('GCP_PROJECT_ID', '$PROJECT_ID')
os.environ.setdefault('GCP_REGION', '$REGION')
os.environ.setdefault('GCS_DATABASES_BUCKET', '$GCS_DATABASES_BUCKET')
from foldrun_app.models.af2.config import Config
config = Config()
tool_config = {'name': 'convert_mmseqs2', 'description': 'Convert to MMseqs2'}
tool = ConvertMMseqs2Tool(tool_config=tool_config, config=config)
result = tool.run({'databases': ['$db']})
print(result)
" 2>&1 || echo "  Warning: conversion submission for $db may have failed"
    done
    cd ..
fi

echo ""
echo "================================================================================"
echo "Deployment Complete!"
echo "================================================================================"
