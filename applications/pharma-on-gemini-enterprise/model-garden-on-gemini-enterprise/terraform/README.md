# Deploy Model Garden Agent with Terraform

This folder contains the declarative Terraform configuration to provision the infrastructure for the `model-garden-on-gemini-enterprise` agent, including the empty Reasoning Engine instance and required IAM roles. The actual agent code build and deployment is managed via Cloud Build.

## Features
* **Agent Identity Integration:** Assigns the unique, least-privilege Agent Identity principal (`principal://agents...`) for accessing Agent Platform and Telemetry services by default.
* **Standard Service Account Fallback:** Gracefully supports deploying under a standard custom Service Account if your GCP project is a standalone sandbox (not part of a Google Workspace or Cloud Identity organization).
* **Observability IAM:** Grants `roles/telemetry.writer` to the agent identity for Google Cloud Observability.

---

## Prerequisites
1. **Terraform v1.0+** installed locally.
2. **Google Cloud Provider 7.28.0+** (this configuration requires `version = ">= 7.28.0"` to support `image_spec`).
3. Standard GCP APIs enabled in your target project:
   * `aiplatform.googleapis.com` (Agent Platform)
   * `cloudbuild.googleapis.com` (for building the custom image)
   * `artifactregistry.googleapis.com`
4. Suitable GCP credentials configured via:
   ```bash
   gcloud auth application-default login
   ```

---

## Configuration Variables

Configure these variables in a `terraform.tfvars` file or pass them via the command line (`-var`).

| Variable | Type | Description | Default |
| :--- | :--- | :--- | :--- |
| **`project_id`** | `string` | **(Required)** The Google Cloud Project ID. | *None* |
| **`region`** | `string` | The GCP region to deploy the Agent Runtime to. | `us-central1` |
| **`logs_bucket_name`** | `string` | Existing GCS bucket to use for logs. If not provided, a new one will be created. | `null` |

---

## Deployment Guide

Do **not** apply this Terraform configuration manually. The provisioning of this infrastructure is fully automated by the shared Cloud Build pipeline (`shared/cloudbuild.yaml`).

To deploy the agent (which automatically runs this Terraform configuration first):
Refer to the deployment instructions in the [Pharma on Gemini Enterprise Root README](../../README.md#cicd-deployment-service-account).

### Local Development / Validation (Optional)

If you want to validate the Terraform configuration locally before deploying:

1.  **Initialize** (requires a GCS bucket for remote state):
    ```bash
    terraform init -backend-config="bucket=YOUR_TF_STATE_BUCKET" -backend-config="prefix=terraform/model-garden-agent"
    ```
2.  **Validate**:
    ```bash
    terraform validate
    ```
3.  **Plan**:
    ```bash
    terraform plan -var="project_id=YOUR_PROJECT_ID" -var="region=us-central1"
    ```


