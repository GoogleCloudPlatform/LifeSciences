---
name: hypothesis-schema
description: Hypothesis JSON schema reference, quality bar, and hypex CLI usage for creating and validating hypotheses.
---

# Hypothesis Schema

This skill teaches you the hypothesis data format, quality requirements, and
how to use the `hypex` CLI to create and validate hypotheses.

These records remain in the native datastore until the supervisor publishes
the completed run with `dde hypex analyze`.

## Schema Reference

Every hypothesis is a JSON file conforming to `schemas/hypothesis.schema.json`.
The schema uses JSON Schema draft 2020-12.

### Required Fields

All of the following fields are **required**:

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | string | Pattern: `H-NNNN` (e.g., `H-0042`) | Unique identifier. **Assigned automatically** by `hypex add-hypothesis` — do not set this yourself. |
| `title` | string | Max 120 characters | Short descriptive title. Aim for ≤ 15 words. |
| `statement` | string | — | One-paragraph falsifiable claim. |
| `mechanism` | string | — | Proposed causal mechanism explaining the claim. |
| `predictions` | array of strings | Min 1 item | Observable, testable predictions that follow from the hypothesis. |
| `experiments` | array of objects | Min 1 item | Proposed experiments to test the hypothesis (see below). |
| `evidence` | array of objects | Min 1 item | Literature evidence with DDE-resolved record IDs (see below). |
| `focus_area` | string | — | Research focus area this hypothesis belongs to. |
| `lineage` | object | — | Evolutionary lineage tracking (see below). |
| `status` | string | Enum: `proposed`, `reviewed`, `active`, `merged`, `retired`, `quarantined` | Current lifecycle status. Use `"proposed"` for new hypotheses. |
| `created_by` | string | — | Name of the agent that created this hypothesis. |
| `epoch` | integer | Min: 0 | Epoch in which this hypothesis was created. |
| `created_at` | string | ISO 8601 date-time | Timestamp of creation (e.g., `"2026-09-05T14:30:00Z"`). |

### Optional Fields

| Field | Type | Constraints | Description |
|---|---|---|---|
| `cluster` | string | Pattern: `C-NN` (e.g., `C-03`) | Proximity cluster identifier. **Not set by generation agents** — assigned later by the proximity agent. |

### The `experiments` Array

Each experiment object has three required fields:

```json
{
  "design": "Description of the experimental design",
  "readout": "Expected measurement or readout",
  "est_difficulty": "low|med|high"
}
```

| Field | Type | Values | Description |
|---|---|---|---|
| `design` | string | — | What the experiment involves — methodology, model system, key controls. |
| `readout` | string | — | What you measure and what the expected result looks like. |
| `est_difficulty` | string | `low`, `med`, `high` | Estimated difficulty. `low` = standard lab techniques; `med` = specialized equipment or expertise; `high` = novel methods or multi-year effort. |

No additional properties are allowed on experiment objects.

### The `evidence` Array

Each evidence item links a literature record to the hypothesis:

```json
{
  "lit_id": "PMID:38012345",
  "role": "supports",
  "note": "Brief explanation of how this evidence relates to the hypothesis"
}
```

| Field | Type | Values | Description |
|---|---|---|---|
| `lit_id` | string | — | Literature record identifier from DDE search/resolve results (e.g., `PMID:38012345`, `arXiv:2301.12345v2`, `10.1101/2024.01.01.573838`). |
| `role` | string | `supports`, `constrains`, `contradicts` | Relationship of this evidence to the hypothesis. |
| `note` | string | — | Brief explanation of relevance — what the cited paper shows and why it matters. |

No additional properties are allowed on evidence objects.

**Role definitions:**

- `supports` — the evidence directly supports the hypothesis claim or mechanism.
- `constrains` — the evidence limits the scope or applicability of the hypothesis
  (e.g., effect is tissue-specific, or only observed in certain conditions).
- `contradicts` — the evidence conflicts with the hypothesis. Include
  contradicting evidence to demonstrate awareness of counterarguments.

### The `lineage` Object

Lineage tracks how a hypothesis was derived:

```json
{
  "parents": [],
  "operator": "null"
}
```

| Field | Type | Description |
|---|---|---|
| `parents` | array of strings | Parent hypothesis IDs (each matching `H-NNNN`). **Empty array** for original hypotheses. |
| `operator` | string | Evolution operator used. One of: `combine`, `ground`, `simplify`, `oob`, `null`. Use `"null"` for original hypotheses. |

**Operator meanings:**

| Operator | When Used | Parents |
|---|---|---|
| `null` | Original hypothesis (no parent) | `[]` (empty) |
| `ground` | Strengthened weakest claim with new literature | `["H-NNNN"]` (one parent) |
| `simplify` | Reduced experimental complexity | `["H-NNNN"]` (one parent) |
| `combine` | Merged complementary aspects of two hypotheses | `["H-NNNN", "H-MMMM"]` (two parents) |
| `oob` | Out-of-the-box analogy transfer from adjacent field | `["H-NNNN"]` (one parent) |

### Status Lifecycle

| Status | Set By | Meaning |
|---|---|---|
| `proposed` | Generation / Evolution agent | Newly created, awaiting review |
| `reviewed` | Reflection agent | Has been reviewed at least once |
| `active` | Supervisor | Participating in the tournament |
| `merged` | Proximity agent | Subsumed by another hypothesis |
| `retired` | Supervisor | Removed from active consideration |
| `quarantined` | Reflection agent | Flagged for safety concerns |

**For new hypotheses, always set `status` to `"proposed"`.**

---

## Quality Bar

Every hypothesis you produce must meet these minimum requirements:

1. **Title ≤ 120 characters.** Concise and descriptive.
2. **At least 1 prediction.** Each prediction must be observable and testable.
3. **At least 1 experiment.** With concrete design, readout, and difficulty
   estimate.
4. **At least 1 evidence item with a `lit_id`.** Every factual claim needs a
   literature citation. The `lit_id` must be a real record ID from a DDE search
   results — never fabricate IDs.
5. **Falsifiable statement.** The `statement` must be a claim that could be
   proven wrong by experiment.
6. **Causal mechanism.** The `mechanism` must explain *why* the claim is
   expected to be true — not just *that* it is true.

Hypotheses that fail these requirements will be rejected by schema validation
or flagged in review.

---

## Using `hypex add-hypothesis`

This is the **only** way to add hypotheses to the datastore. Never write
hypothesis JSON files directly.

### Command

```bash
hypex add-hypothesis [file|-] --run <run-id> --run-dir <run-base>
```

### Arguments

| Argument | Description |
|---|---|
| `file` | Path to a JSON file containing the hypothesis |
| `-` | Read hypothesis JSON from stdin |
| `--run` | **Required.** Run ID to add the hypothesis to |
| `--run-dir` | Base directory containing runs (default: `/scion-volumes/executions`) |
| `--schema-dir` | Directory containing JSON schemas (defaults to DDE's provisioned Hypex schemas) |

### What It Does

1. Reads the hypothesis JSON from the specified file or stdin.
2. **Allocates the next sequential ID** (e.g., `H-0042`) via an atomic
   lockfile-based counter. Any `id` field in your input is **overwritten**.
3. Validates the complete hypothesis against `hypothesis.schema.json`.
4. Writes the file atomically (temp file + rename) to
   `<run-dir>/<run-id>/hypotheses/H-NNNN.json`.
5. Prints `Added hypothesis: H-NNNN -> <path>` to stdout.

### Usage Examples

**From stdin (preferred for agents):**

```bash
cat <<'EOF' | hypex add-hypothesis --run my-run --run-dir <run-base> -
{
  "title": "Gut Lactobacillus depletion accelerates tau pathology via vagal signaling",
  "statement": "Depletion of Lactobacillus species in the gut microbiome accelerates tau protein aggregation in the hippocampus through aberrant vagus nerve signaling, contributing to Alzheimer's disease progression.",
  "mechanism": "Lactobacillus metabolites (especially short-chain fatty acids) normally suppress pro-inflammatory cytokine release in the gut epithelium. When Lactobacillus is depleted, increased TNF-alpha and IL-6 travel via the vagus nerve afferents to the brainstem and hippocampus, activating microglia and promoting tau hyperphosphorylation.",
  "predictions": [
    "Germ-free mice colonized with Lactobacillus-depleted microbiomes show increased hippocampal p-tau levels compared to controls",
    "Vagotomy blocks the tau-accelerating effect of gut Lactobacillus depletion"
  ],
  "experiments": [
    {
      "design": "Colonize germ-free mice with defined microbiome communities with and without Lactobacillus. Measure hippocampal p-tau by ELISA at 3, 6, and 9 months.",
      "readout": "p-tau/total-tau ratio in hippocampal lysates",
      "est_difficulty": "med"
    },
    {
      "design": "Perform subdiaphragmatic vagotomy vs sham surgery in Lactobacillus-depleted mice. Compare hippocampal p-tau levels.",
      "readout": "p-tau levels and microglial activation markers (Iba1) in vagotomized vs sham mice",
      "est_difficulty": "high"
    }
  ],
  "evidence": [
    {
      "lit_id": "PMID:38012345",
      "role": "supports",
      "note": "Demonstrates altered gut microbiome composition in Alzheimer's patients with reduced Lactobacillus abundance"
    },
    {
      "lit_id": "arXiv:2401.12345",
      "role": "supports",
      "note": "Computational model predicting vagal signaling as the primary gut-brain axis for neuroinflammation"
    }
  ],
  "focus_area": "Gut-brain axis in neurodegeneration",
  "lineage": {"parents": [], "operator": "null"},
  "status": "proposed",
  "created_by": "hypex-generation-01",
  "epoch": 1,
  "created_at": "2026-09-05T14:30:00Z"
}
EOF
```

**From a file:**

```bash
hypex add-hypothesis hypothesis-draft.json --run my-run --run-dir <run-base>
```

**Piping (useful in agent workflows):**

```bash
echo '<hypothesis-json>' | hypex add-hypothesis --run my-run --run-dir <run-base> -
```

### Important Notes

- **Do not set the `id` field** — it is assigned automatically by the command.
  If you include an `id` in your JSON, it will be overwritten.
- **Do not set the `cluster` field** — it is assigned later by the proximity
  agent. The field is optional in the schema.
- **Validation happens automatically.** If your hypothesis does not conform to
  the schema, the command will fail with a validation error. Fix the error and
  retry.
- **Writes are atomic.** The file is written via temp file + rename, so other
  agents will never see a partially written hypothesis.

---

## Other Useful `hypex` Commands

### `hypex validate`

Validate hypothesis (and other artifact) files against their schemas:

```bash
# Validate a specific file
hypex validate <run-dir>/hypotheses/H-0042.json

# Validate all artifacts in a run
hypex validate --run my-run --run-dir <run-base>
```

### `hypex list`

List hypotheses in a run:

```bash
# List all hypotheses
hypex list --run my-run --run-dir <run-base>

# Filter by status
hypex list --run my-run --run-dir <run-base> --status proposed
```

### `hypex next-id`

Allocate the next sequential ID (used internally by `add-hypothesis`, but
available if needed):

```bash
hypex next-id hypothesis --run my-run --run-dir <run-base>           # → H-0043
hypex next-id match --run my-run --run-dir <run-base>                # → M-0001
hypex next-id review --run my-run --run-dir <run-base> --for H-0043  # → H-0043.R-01
```

### `hypex status`

Show a run dashboard:

```bash
hypex status --run my-run --run-dir <run-base>
```

### `hypex init-run`

Initialize a new run (typically done by the supervisor):

```bash
hypex init-run my-run --run-dir <run-base> --goal "Explore mechanisms of age-related cognitive decline"
```

---

## Complete Example: Well-Formed Hypothesis JSON

```json
{
  "title": "Gut Lactobacillus depletion accelerates tau pathology via vagal signaling",
  "statement": "Depletion of Lactobacillus in the gut accelerates hippocampal tau aggregation through vagus nerve-mediated neuroinflammation.",
  "mechanism": "Lactobacillus metabolites suppress gut epithelial cytokine release. Depletion increases TNF-alpha and IL-6 signaling via vagal afferents, activating hippocampal microglia and promoting tau hyperphosphorylation.",
  "predictions": [
    "Germ-free mice with Lactobacillus-depleted microbiomes show elevated hippocampal p-tau",
    "Vagotomy blocks the tau-accelerating effect of Lactobacillus depletion"
  ],
  "experiments": [
    {
      "design": "Colonize germ-free mice ± Lactobacillus; measure p-tau at 3, 6, 9 months",
      "readout": "p-tau/total-tau ratio in hippocampal lysates",
      "est_difficulty": "med"
    }
  ],
  "evidence": [
    {
      "lit_id": "PMID:38012345",
      "role": "supports",
      "note": "Reduced Lactobacillus in Alzheimer's patients"
    }
  ],
  "focus_area": "Gut-brain axis in neurodegeneration",
  "lineage": {"parents": [], "operator": "null"},
  "status": "proposed",
  "created_by": "hypex-generation-01",
  "epoch": 1,
  "created_at": "2026-09-05T14:30:00Z"
}
```

**Do not include `id` or `cluster`** — they are assigned by the system.
