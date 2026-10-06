# AlphaFold 3 Complex Prediction Workflow

A guide to running AF3 predictions through the `dde alphafold predict` command
on the Vertex AI dedicated endpoint. Covers input preparation, invocation, output
interpretation, and the operational constraints that shape scheduling.

**Source of truth:** `tools/dde/commands/alphafold.py`. Every claim below is
traceable to that file or to the `protein-structure-confidence` skill that wraps
it. If anything here contradicts the code, the code is right.

---

## 1. Overview

`dde alphafold predict` submits one AlphaFold 3 prediction request to a
Vertex AI dedicated endpoint running the AF3 model, then blocks until the
endpoint returns a result or the deadline expires.

The command:

1. Reads and validates an input JSON file.
2. Discovers the AF3 endpoint by display name in the configured GCP project.
3. Acquires a local file lock (single-flight serialisation within one container).
4. Submits the request and retries on 429 (cold-start / busy) and 504 (gateway
   timeout) until a 200 response or the deadline.
5. Writes the raw response, the request copy, and any extracted artifacts
   (structure, summary, pLDDT, PAE) to the project's `raw/structures/` directory
   with a provenance sidecar.

The companion command `dde alphafold analyze-prediction` reads the stored
summary and applies the `af3` threshold set to produce a confidence verdict.

---

## 2. Input JSON format

The input JSON describes the molecular complex to predict. Pass it via
`--input <path>`. The path resolves against the project root when relative.

### Required top-level fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Prediction name. Used to derive output filenames (`AF3-<name>.*`). |
| `modelSeeds` | list of int | Random seeds for the model. Must be non-empty. The Vertex container returns only the top-ranked structure regardless of how many seeds are requested. |
| `sequences` | list of objects | The chains in the complex. Each entry is a single-key object (see below). Must be non-empty. |
| `dialect` | string | Must be `"alphafold3"`. |
| `version` | int | Must be `1`, `2`, or `3`. |

### Annotated example: two-chain protein complex

```json
{
  "name": "PCSK9-LDLR-complex",
  "modelSeeds": [42],
  "sequences": [
    {
      "protein": {
        "id": "A",
        "sequence": "SSVFVQGEESNDKIPVALGLKEKNLYLS..."
      }
    },
    {
      "protein": {
        "id": "B",
        "sequence": "DIVLTQSPASLAVSLGQRATISCRASESVD..."
      }
    }
  ],
  "dialect": "alphafold3",
  "version": 1
}
```

### Sequence entry structure

Each element of `sequences` must be a **single-key object**. The key names the
chain type; the value is an object that must include at minimum an `id` field:

```
{ "<chain_type>": { "id": "<chain_id>", ... } }
```

**Allowed chain types:** `protein`, `rna`, `dna`, `ligand`.

Any other chain type is rejected at validation before the request is sent.

### Wrapper format

The input file may optionally be wrapped in the Vertex `instances` envelope:

```json
{
  "instances": [
    {
      "name": "...",
      "modelSeeds": [...],
      "sequences": [...],
      "dialect": "alphafold3",
      "version": 1
    }
  ]
}
```

The CLI unwraps this automatically. The `instances` array must contain exactly
one element.

### Validation rules

The CLI validates the input **before** sending it to the endpoint. Validation
failures raise immediately without consuming endpoint time:

- All five top-level fields (`name`, `modelSeeds`, `sequences`, `dialect`,
  `version`) must be present.
- `modelSeeds` must be a non-empty list of integers.
- `dialect` must be exactly `"alphafold3"`.
- `version` must be one of `1`, `2`, or `3`.
- Each sequence entry must be a single-key dict with the key being one of the
  allowed chain types.
- Each chain entry must contain an `id` field.
- Chain IDs must be unique across all entries. Duplicate IDs are rejected.

---

## 3. Chain ordering and naming

### Chain IDs

Every chain requires a unique `id` string. The IDs appear in the output
structure (CIF file) and in per-chain metrics. Conventional practice is
single uppercase letters (`"A"`, `"B"`, `"C"`, ...), but any unique string
is accepted by the validation.

### Ordering in the input

Chains appear in the `sequences` array in the order you list them. When
preparing a multi-chain complex:

- List chains in the order you want them labelled.
- The first chain's `id` becomes the first chain in the output structure,
  and so on.

### Domain vs. full-length sequences

Submit the sequence you want predicted, whether that is a full-length protein
or a specific domain. AF3 predicts what you give it:

- **Full-length sequences** predict the complete protein, including any
  disordered regions. This is appropriate when you need the full complex
  geometry.
- **Domain sequences** restrict prediction to a specific region. This is
  appropriate when you know the interaction involves a specific domain, when
  the full-length protein is too large, or when disordered regions would
  degrade the prediction.

The choice affects residue numbering in the output (see §7).

---

## 4. Running a prediction

### CLI invocation

```bash
dde alphafold predict --input path/to/input.json
```

### Options

| Option | Default | Description |
|---|---|---|
| `--input` | (required) | Path to the AF3 input JSON file. |
| `--deadline` | `1800.0` (30 min) | Total time cap in seconds. The command exits with an error if no successful response arrives within this window. |
| `--cold-start-wait` | `25.0` | Seconds between retries when the endpoint is scaling from zero (429, "model is not yet ready"). |
| `--busy-wait` | `15.0` | Seconds between retries when the endpoint is busy with another prediction (429, "already working on a prediction"). |
| `--gateway-timeout-wait` | `60.0` | Seconds to wait after a 504 gateway timeout before retrying. |
| `--out` | (project default) | Override the output directory. |
| `--json` | off | Emit machine-readable JSON output. |
| `--quiet` | off | Emit paths only. |

### Output files

All outputs land in `raw/structures/` under the project root, with the stem
`AF3-<name>` where `<name>` is the `name` field from the input JSON.

| File | Always present | Contents |
|---|---|---|
| `AF3-<name>.response.json` | Yes | Full raw response from the endpoint. |
| `AF3-<name>.request.json` | Yes | Copy of the request as sent. |
| `AF3-<name>.cif` | When present in response | Predicted structure in mmCIF format. |
| `AF3-<name>.summary.json` | When present in response | Prediction summary (pTM, ipTM, ranking score, etc.). |
| `AF3-<name>.plddt.json` | When present in response | Per-residue pLDDT scores. |
| `AF3-<name>.pae.json` | When present in response | Predicted Aligned Error matrix. |
| `AF3-<name>.meta.json` | Yes | Provenance sidecar: endpoint, parameters, wall time, attempt count, warnings. |

### Analyzing the prediction

After `predict` completes, run the analysis phase:

```bash
dde alphafold analyze-prediction raw/structures/AF3-<name>.summary.json
```

This applies the `af3` threshold set and writes
`AF3-<name>.alphafold.analysis.json` with a confidence verdict and metrics.

---

## 5. Wall-clock expectations

### Cold-start behavior

The AF3 Vertex endpoint is a dedicated endpoint that **scales from zero** when
idle. A prediction on a cold endpoint follows this pattern:

1. **504 gateway timeouts** — the gateway times out while the endpoint's
   infrastructure is provisioning. The CLI retries every `--gateway-timeout-wait`
   seconds (default 60s). In the first pilot, 504 errors persisted for
   approximately 18 minutes.

2. **429 "model is not yet ready"** — the container is up but the model is still
   loading. The CLI retries every `--cold-start-wait` seconds (default 25s).

3. **Prediction runs** — once warm, the actual prediction executes.

**First-pilot observation:** a cold-start prediction took approximately 28
minutes end-to-end (roughly 18 minutes of 504 gateway timeouts followed by
retries until the prediction completed). The original estimate of 5–10 minutes
was inaccurate.

### Warm-endpoint predictions

Once the endpoint is warm (i.e. within the keep-alive window after a previous
prediction), the 504 and cold-start phases are skipped. The prediction itself
typically completes faster, but wall-clock time depends on the size and
complexity of the input.

### Deadline

The default deadline is 1800 seconds (30 minutes). If the deadline expires
before a successful response, the command raises `EndpointUnavailable` with the
number of attempts made. Increase the deadline with `--deadline` if cold starts
are expected:

```bash
dde alphafold predict --input input.json --deadline 2400
```

### Retry summary

| HTTP status | Detail pattern | Wait interval | Meaning |
|---|---|---|---|
| 429 | "model is not yet ready" | `--cold-start-wait` (25s) | Endpoint scaling from zero. |
| 429 | "already working on a prediction" | `--busy-wait` (15s) | Another prediction is in progress. |
| 504 | (any) | `--gateway-timeout-wait` (60s) | Gateway timeout during cold start. |
| Transport error | (any) | `--busy-wait` (15s) | Network-level failure; retried. |

All other non-200 status codes raise immediately without retry.

---

## 6. Single-flight constraint

### What it means

The AF3 Vertex endpoint runs on a **single replica**. It can process only one
prediction at a time. Any concurrent request receives a 429 response.

### Within-container serialisation

The CLI acquires an exclusive file lock (`fcntl.flock`) at
`/tmp/dde-af3.lock` (configurable via the `DDE_AF3_LOCK` environment
variable) before sending a request. This means:

- Multiple `dde alphafold predict` invocations in the same container queue
  behind the lock. The second caller blocks until the first releases it.
- The lock is held for the entire prediction, including all retries.

### Cross-container collisions

The file lock serialises callers within one container only.
**Cross-container concurrency is not solved by the CLI.** If two specialists in
separate containers submit predictions simultaneously, one receives a 429
"already working on a prediction" response and retries with `--busy-wait`
backoff.

In practice, the Research Operations Controller schedules AF3 work
sequentially — only one specialist receives an AF3 work order at a time.
If you receive a work order requiring AF3, the endpoint is available.

### Practical implications

- **Do not fan out AF3 predictions to parallel agents.** They will collide
  and spend their time retrying rather than predicting.
- **Schedule AF3 work sequentially.** Each prediction must complete before
  the next begins.
- **A 429 during warm-up is not failure.** The CLI retries automatically.
  The prediction succeeds when the endpoint becomes available, as long as
  the deadline has not expired.

---

## 7. Residue numbering

AF3 output numbering corresponds to **positions in the submitted sequence**,
not to UniProt positions or any external numbering scheme.

- If you submit residues 100–250 of a protein (a domain), AF3 numbers them
  1–151 in the output CIF and per-residue metrics.
- If you submit the full-length sequence, AF3 output position N corresponds
  to the Nth residue of the submitted sequence.

When comparing AF3 output to UniProt annotations or AFDB structures:

- Map AF3 residue numbers back through the submitted sequence to recover
  the original numbering.
- Record the mapping in your finding so a reviewer can verify it.

The `AF3-<name>.request.json` file preserves the exact sequences submitted,
providing the reference needed for this mapping.

---

## 8. Troubleshooting

### 504 gateway timeouts on first run

**Cause:** The endpoint is scaling from zero. The gateway times out before the
endpoint infrastructure is ready.

**What happens:** The CLI retries every 60 seconds (configurable with
`--gateway-timeout-wait`). This phase can last 15–20 minutes on a cold start.

**Action:** Wait. This is expected behavior. If you anticipate a cold start,
consider increasing the deadline.

### 429 "model is not yet ready"

**Cause:** The container is up but the model has not finished loading.

**What happens:** The CLI retries every 25 seconds (configurable with
`--cold-start-wait`). A provenance warning is recorded.

**Action:** Wait. This follows the 504 phase during a cold start.

### 429 "already working on a prediction"

**Cause:** Another prediction is currently running on the single-replica
endpoint.

**What happens:** The CLI retries every 15 seconds (configurable with
`--busy-wait`). A provenance warning is recorded.

**Action:** Wait for the other prediction to finish. If this persists, confirm
that AF3 work is being scheduled sequentially.

### Input validation failures

These are raised before any request is sent:

| Error | Cause |
|---|---|
| "AF3 input missing required field: X" | One of `name`, `modelSeeds`, `sequences`, `dialect`, or `version` is absent. |
| "modelSeeds must be a non-empty list of integers" | `modelSeeds` is empty, missing, or not a list. |
| "dialect must be 'alphafold3'" | Wrong dialect string. |
| "version must be 1, 2 or 3" | Invalid version number. |
| "sequences[N] must be a single-key object" | A sequence entry has zero or multiple keys. |
| "sequences[N] type X not one of ..." | Chain type is not `protein`, `rna`, `dna`, or `ligand`. |
| "sequences[N].X.id is required" | A chain entry is missing its `id` field. |
| "duplicate chain id X" | Two or more chains share the same `id`. |

### Deadline exceeded

**Cause:** The total time (cold start + retries + prediction) exceeded the
`--deadline` value.

**Action:** Increase the deadline. For a known cold start, 2400 seconds
(40 minutes) provides margin beyond the observed ~28-minute cold-start case.

### Endpoint not found

**Error:** "no Vertex endpoint named ... in PROJECT/REGION"

**Cause:** The AF3 endpoint is not deployed, or the display name / project /
region do not match.

**Action:** Confirm the endpoint is deployed. Override with environment
variables if needed:

| Variable | Default | Purpose |
|---|---|---|
| `DDE_AF3_PROJECT` | `pharma-oss-factory` | GCP project ID. |
| `DDE_AF3_REGION` | `us-central1` | GCP region. |
| `DDE_AF3_ENDPOINT_NAME` | `AlphaFold 3 Dedicated Endpoint` | Endpoint display name. |

### Multiple model seeds

If `modelSeeds` contains more than one seed, a provenance warning is recorded:
the Vertex container returns only the top-ranked structure. Per-seed outputs
are not available.

### Missing google-cloud-aiplatform

**Error:** "google-cloud-aiplatform is not installed"

**Action:** Install the package into the tools environment and run
`dde doctor` to verify.
