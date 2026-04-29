# FoldRun: Tracking Actual Pipeline Costs via Cloud Billing

This document explains how to query the **actual billed cost** of a FoldRun prediction run using the Cloud Billing BigQuery export. The built-in `get_actual_job_costs` agent tool estimates costs from machine specs and catalog pricing — this approach gets you real post-discount, post-credit amounts directly from your billing data.

## Why the Agent Estimator Isn't Enough

The `get_actual_job_costs` tool computes cost from Agent Platform job metadata (machine type × runtime × catalog rate). This is accurate for list pricing but misses:

- **Committed Use Discounts (CUDs)** — 1yr/3yr GPU reservations reduce effective GPU cost by 20-40%
- **Sustained Use Discounts (SUDs)** — automatically applied when a resource runs >25% of the month
- **Negotiated enterprise rates** — your Google Cloud contract pricing
- **Preemption credits** — preempted FLEX_START jobs are not billed for the interrupted interval
- **Promotional credits** — any active credit programs on the account

The Cloud Billing API does not expose spend data at the resource or label level — it only provides catalog pricing and account management. The only programmatic path to label-attributed actual costs is the **Cloud Billing BigQuery export**.

## How the Billing Label Works

When FoldRun submits an AF2, OF3, or Boltz2 prediction via the Agent Platform Pipelines API, Agent Platform automatically stamps every GCE resource provisioned for that run with the label:

```
vertex-ai-pipelines-run-billing-id = <pipeline_billing_id>
```

This label propagates to every billable line item for that run — GPU instances, CPU instances, disks, and network egress. The `pipeline_billing_id` value can be found on the submitted job in Agent Platform and is also captured in the FoldRun job labels under the same key.

FoldRun additionally applies its own labels at submission time (see `submit_monomer.py`, `submit_multimer.py`, etc.):

| Label | Value | Example |
|---|---|---|
| `vertex-ai-pipelines-run-billing-id` | Set by Agent Platform | `abc123def456` |
| `submitted_by` | `foldrun-agent` | `foldrun-agent` |
| `model_type` | `alphafold2`, `openfold3`, `boltz2` | `alphafold2` |
| `job_type` | `monomer`, `multimer` | `monomer` |
| `seq_name` | Sequence identifier | `my-protein` |
| `seq_len` | Residue/token count | `347` |
| `gpu_type` | GPU tier used | `A100` |

## Prerequisites

### 1. Enable Cloud Billing BigQuery Export

In the GCP console: **Billing → Billing export → BigQuery export → Edit settings**

Enable **Detailed usage cost** export (not standard — detailed includes resource-level labels). Choose a destination project and dataset. The table will be named:

```
gcp_billing_export_resource_v1_{BILLING_ACCOUNT_ID_WITH_UNDERSCORES}
```

For example, billing account `ABCDEF-123456-FEDCBA` → table suffix `ABCDEF_123456_FEDCBA`.

**Note:** export has a 1-2 day lag. Costs for jobs run today will not appear until tomorrow or the following day.

### 2. Grant BigQuery Access

The service account or user running the query needs `roles/bigquery.dataViewer` on the billing export dataset.

### 3. Note Your Dataset Details

You'll need:
- **Project ID** where the export dataset lives (may differ from FoldRun's project)
- **Dataset name** (e.g. `billing_export`)
- **Billing account ID suffix** for the table name

## Querying Actual Cost by Pipeline Run

### Cost breakdown for a specific pipeline run

```sql
SELECT
  label.value                                                        AS pipeline_billing_id,
  service.description                                                AS service,
  sku.description                                                    AS resource_sku,
  SUM(cost)                                                          AS list_cost,
  SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0))     AS total_credits,
  SUM(cost)
    + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS net_cost,
  SUM(usage.amount)                                                  AS usage_amount,
  ANY_VALUE(usage.unit)                                              AS usage_unit,
  MIN(usage_start_time)                                              AS started,
  MAX(usage_end_time)                                                AS ended

FROM `{project}.{dataset}.gcp_billing_export_resource_v1_{billing_account}`
   , UNNEST(labels) AS label

WHERE label.key   = 'vertex-ai-pipelines-run-billing-id'
  AND label.value = '{pipeline_billing_id}'   -- from job labels or Agent Platform console
  AND DATE(_PARTITIONTIME) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)

GROUP BY pipeline_billing_id, service, resource_sku, usage_unit
ORDER BY net_cost DESC
```

**Replace:**
- `{project}` — BigQuery project where export lands
- `{dataset}` — export dataset name
- `{billing_account}` — billing account ID with underscores (e.g. `ABCDEF_123456_FEDCBA`)
- `{pipeline_billing_id}` — the pipeline billing ID from the job (visible in Agent Platform job labels or FoldRun job details)

### All FoldRun runs in the last 30 days

```sql
SELECT
  billing_id.value                                                       AS pipeline_billing_id,
  ANY_VALUE(model.value)                                                 AS model_type,
  ANY_VALUE(seq.value)                                                   AS seq_name,
  ANY_VALUE(seqlen.value)                                                AS seq_len,
  SUM(cost)                                                              AS list_cost,
  SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0))         AS total_credits,
  SUM(cost)
    + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0))     AS net_cost,
  MIN(usage_start_time)                                                  AS started,
  MAX(usage_end_time)                                                    AS ended

FROM `{project}.{dataset}.gcp_billing_export_resource_v1_{billing_account}`
   , UNNEST(labels) AS billing_id
LEFT JOIN UNNEST(labels) AS model    ON model.key    = 'model_type'
LEFT JOIN UNNEST(labels) AS sub      ON sub.key      = 'submitted_by'
LEFT JOIN UNNEST(labels) AS seq      ON seq.key      = 'seq_name'
LEFT JOIN UNNEST(labels) AS seqlen   ON seqlen.key   = 'seq_len'

WHERE billing_id.key = 'vertex-ai-pipelines-run-billing-id'
  AND sub.value      = 'foldrun-agent'
  AND DATE(_PARTITIONTIME) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)

GROUP BY pipeline_billing_id
ORDER BY started DESC
```

### Monthly spend by model type

```sql
SELECT
  FORMAT_DATE('%Y-%m', DATE(usage_start_time))                          AS month,
  model.value                                                           AS model_type,
  COUNT(DISTINCT billing_id.value)                                      AS pipeline_runs,
  SUM(cost)
    + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0))    AS net_cost

FROM `{project}.{dataset}.gcp_billing_export_resource_v1_{billing_account}`
   , UNNEST(labels) AS billing_id
LEFT JOIN UNNEST(labels) AS model ON model.key  = 'model_type'
LEFT JOIN UNNEST(labels) AS sub   ON sub.key    = 'submitted_by'

WHERE billing_id.key = 'vertex-ai-pipelines-run-billing-id'
  AND sub.value      = 'foldrun-agent'
  AND DATE(_PARTITIONTIME) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)

GROUP BY month, model_type
ORDER BY month DESC, net_cost DESC
```

## Reading the Results

| Column | What it means |
|---|---|
| `list_cost` | What you'd pay at catalog rates with no discounts |
| `total_credits` | Negative number — CUDs, SUDs, promos, preemption credits |
| `net_cost` | What you actually owe (`list_cost + total_credits`) |
| `resource_sku` | E.g. `"A100 GPU running in Americas"`, `"N1 Custom Instance Core running in Americas"` |
| `usage_amount` / `usage_unit` | E.g. `1.5 hours` of GPU time |

## Limitations

| Limitation | Detail |
|---|---|
| **1-2 day lag** | Billing data is not real-time. Use the agent estimator for same-day cost awareness. |
| **Partition filter required** | Always include `DATE(_PARTITIONTIME) >= ...` to avoid full table scans (the table can be very large) |
| **Labels on custom jobs only** | The `vertex-ai-pipelines-run-billing-id` label is on GCE resources (GPU/CPU instances). Agent Platform API calls (model serving, etc.) are billed separately without this label. For FoldRun, nearly all cost is GCE. |
| **Pipeline orchestrator not labeled** | The Agent Platform pipeline orchestration overhead (the `caip_pipelines_*` jobs) is billed under a separate SKU and is typically a few cents per run. |

## Wiring Into the Agent (Future)

Once a BigQuery billing dataset is configured, `get_actual_job_costs` can be extended to query it directly:

```python
# Proposed env vars
BILLING_EXPORT_PROJECT=gnext26-foldrun       # or separate billing project
BILLING_EXPORT_DATASET=billing_export
BILLING_ACCOUNT_ID=ABCDEF-123456-FEDCBA      # with dashes, normalized internally
```

The tool would:
1. Try BigQuery first for jobs > 2 days old (accounting for export lag)
2. Fall back to the Agent Platform job metadata estimator for recent jobs
3. Surface a `data_source` field in results so the agent can tell the user which method was used

Until then, use the queries above directly in the BigQuery console or via `bq query`.
