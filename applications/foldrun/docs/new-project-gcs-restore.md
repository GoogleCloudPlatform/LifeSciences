# New Project Setup: Restoring Databases from an Existing GCS Bucket

Use this guide when deploying FoldRun to a new GCP project and an existing
FoldRun deployment has already downloaded the genomic databases. Restoring from
GCS takes ~15 minutes vs 2-4 hours for a fresh internet download.

## Prerequisites

- Access to an existing FoldRun project whose databases bucket you will restore from
- Owner or Editor role on the new target project
- `gcloud` CLI authenticated on your local machine
- `terraform` >= 1.0 installed
- The `uv` Python package manager installed

## Overview

```
Step 1: Create the new GCP project and link billing
Step 2: Run --steps infra  →  provisions all infrastructure and service accounts
Step 3: Grant the new project read access to the source databases bucket
Step 4: Run --steps build  →  builds containers and deploys the agent
Step 5: Run --steps data   →  restores databases from GCS (~15 min)
```

Steps 4 and 5 can be combined into a single `--steps build,data` invocation.

---

## Step 1: Create the Project

```bash
# Create the project
gcloud projects create YOUR_NEW_PROJECT_ID --name="FoldRun"

# Link billing (find your billing account ID with: gcloud billing accounts list)
gcloud billing projects link YOUR_NEW_PROJECT_ID \
  --billing-account=YOUR_BILLING_ACCOUNT_ID

# Set as default for subsequent commands
gcloud config set project YOUR_NEW_PROJECT_ID
```

---

## Step 2: Provision Infrastructure

From the `applications/foldrun/` directory:

```bash
./deploy-all.sh YOUR_NEW_PROJECT_ID us-central1 --steps infra
```

This uses Terraform to create all required GCP resources including:
- VPC network and Filestore NFS instance
- GCS buckets (pipeline data and database backups)
- Artifact Registry repository
- Service accounts with appropriate IAM roles
- Cloud Run services (viewer, A2A proxy)
- Cloud Run Job (FoldRun prediction analysis)

**Wait for this to complete before proceeding to Step 3.** The batch compute
service account must exist before the sharing grant can be applied.

---

## Step 3: Grant Database Bucket Access

The database restore uses Cloud Batch jobs that run as the new project's
`batch-compute-sa` service account. Grant it read access to the source
project's databases bucket:

**Run this on the source project side (or as an admin of the source project):**

```bash
gcloud storage buckets add-iam-policy-binding \
  gs://SOURCE_PROJECT_ID-foldrun-gdbs \
  --member="serviceAccount:batch-compute-sa@YOUR_NEW_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"
```

Replace:
- `SOURCE_PROJECT_ID` — the existing project whose databases you are restoring from
- `YOUR_NEW_PROJECT_ID` — the new project you are deploying to

**Verify the grant was applied:**

```bash
gcloud storage buckets get-iam-policy gs://SOURCE_PROJECT_ID-foldrun-gdbs \
  --filter="bindings.members:batch-compute-sa@YOUR_NEW_PROJECT_ID.iam.gserviceaccount.com"
```

### Cross-org IAM (restricted source projects)

If the source project has an `iam.allowedPolicyMemberDomains` org policy inherited
from its folder, the binding will fail with `HTTP 412: One or more users named in the
policy do not belong to a permitted customer.`

Override the policy at the project level, apply the binding, then restore:

```bash
# 1. Enable the Org Policy API if not already enabled
gcloud services enable orgpolicy.googleapis.com --project=SOURCE_PROJECT_ID

# 2. Override the domain restriction at project level
gcloud org-policies set-policy /dev/stdin --project=SOURCE_PROJECT_ID <<'EOF'
name: projects/SOURCE_PROJECT_ID/policies/iam.allowedPolicyMemberDomains
spec:
  reset: true
EOF

# 3. Wait ~10s for propagation, then apply the binding
gcloud storage buckets add-iam-policy-binding \
  gs://SOURCE_PROJECT_ID-foldrun-gdbs \
  --member="serviceAccount:batch-compute-sa@YOUR_NEW_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"

# 4. Restore folder-level policy inheritance
gcloud org-policies delete iam.allowedPolicyMemberDomains --project=SOURCE_PROJECT_ID
```

The bucket-level IAM binding persists after the project-level override is removed.

---

## Step 4 + 5: Build and Restore

Run build (containers + agent) and data (database restore) together:

```bash
GCS_SOURCE_BUCKET=SOURCE_PROJECT_ID-foldrun-gdbs \
./deploy-all.sh YOUR_NEW_PROJECT_ID us-central1 --steps build,data
```

`GCS_SOURCE_BUCKET` tells the data step to restore from GCS instead of
downloading from the internet. The restore uses `gcloud storage rsync` to copy
each database directory from the source bucket to the new project's NFS
Filestore via Cloud Batch.

### What gets restored

All databases present in the source bucket are restored, typically:

| Path | Content | Models |
|------|---------|--------|
| `uniref90/` | UniRef90 FASTA | AF2, OF3, Boltz-2 |
| `mgnify/` | MGnify clusters | AF2, OF3, Boltz-2 |
| `pdb_seqres/` | PDB sequences | AF2, OF3 |
| `pdb_mmcif/` | PDB CIF structures | AF2, OF3 |
| `uniprot/` | UniProt FASTA | AF2, OF3 |
| `alphafold2/params/` | AF2 model weights | AF2 |
| `small_bfd/` | Small BFD database | AF2 |
| `pdb70/` | PDB70 HHsuite DB | AF2 |
| `of3/params/` | OF3 model weights | OF3 |
| `of3/ccd/` | Chemical Component Dict | OF3 |
| `rfam/` | Rfam RNA database | OF3 |
| `rnacentral/` | RNAcentral RNA database | OF3 |
| `boltz2/cache/` | Boltz-2 weights + CCD | Boltz-2 |

### Monitoring restore progress

Restore jobs run as Cloud Batch jobs in the background. Monitor progress:

```bash
gcloud batch jobs list \
  --project=YOUR_NEW_PROJECT_ID \
  --location=us-central1
```

Or view in the [Cloud Batch console](https://console.cloud.google.com/batch/jobs).

Typical restore time: **~15 minutes** (internal GCP network, no egress costs
when source and destination are in the same region).

---

## Step 6: Verify

```bash
./check-status.sh YOUR_NEW_PROJECT_ID
```

Expected output when complete:

```
✅ [Terraform] Infrastructure provisioned
✅ [Cloud Run] foldrun-viewer service is deployed and active
✅ [Cloud Run] foldrun-analysis-job is deployed
✅ [Agent Platform] FoldRun Agent Runtime is deployed
✅ [Data] Databases present (13 folders)
   ✅ AF2 core databases (uniref90 etc.)
   ✅ OF3 weights + CCD
   ✅ Boltz-2 cache (weights + mols)
```

---

## Cross-Project Sharing Notes

- The IAM grant on the source bucket is permanent until explicitly removed.
  Remove it after the restore completes if you want to restrict ongoing access:
  ```bash
  gcloud storage buckets remove-iam-policy-binding \
    gs://SOURCE_PROJECT_ID-foldrun-gdbs \
    --member="serviceAccount:batch-compute-sa@YOUR_NEW_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer"
  ```

- The restore also automatically backs up the databases to the new project's
  own GCS bucket (`YOUR_NEW_PROJECT_ID-foldrun-gdbs`) so future rebuilds can
  restore from the new project's bucket without needing access to the original.

- If source and destination projects are in different regions, add a
  `--location` flag to match both and avoid inter-region egress costs.
