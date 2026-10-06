---
name: proximity-protocol
description: Proximity thresholds, borderline-duplicate adjudication, and cluster labeling for the prox pipeline.
---

# Proximity Protocol

This skill teaches you how to use the `prox` CLI pipeline and how to adjudicate
borderline duplicate hypotheses. The proximity agent is a mechanical role: run
the pipeline, adjudicate borderline pairs, label clusters, and report results.

---

## Similarity Thresholds

The `prox` tool computes pairwise cosine similarity of TF-IDF vectors built
from hypothesis texts (title + statement + mechanism). Because TF-IDF is a
**lexical** metric, scores reflect vocabulary overlap, not semantic meaning.
Two hypotheses that describe the same mechanism in different words may score
low, while two that share boilerplate phrasing may score high. Keep this
limitation in mind when interpreting results.

> **Note:** A sentence-transformer backend (`sbert`) is planned but not yet
> implemented — `prox embed --backend sbert` raises `NotImplementedError`.
> All current scores are TF-IDF–based.

Similarity scores are interpreted as follows:

| Range | Interpretation | Action |
|---|---|---|
| < 0.30 | **Low lexical overlap** — little shared vocabulary (not necessarily semantically unrelated) | No edge in the proximity graph |
| 0.30 – 0.80 | **Same cluster** — related hypotheses exploring similar themes | Grouped into the same cluster; no merge concern |
| 0.80 – 0.92 | **Borderline duplicate** — high surface similarity, may or may not be genuinely distinct | LLM must adjudicate (see procedure below) |
| ≥ 0.92 | **Auto-merge candidate** — very likely duplicates or paraphrases | Recommend merge to supervisor without further analysis |

The graph threshold (0.30) determines which pairs get edges. The dupe threshold
(0.80) determines which pairs are flagged for review.

> **Important:** The 0.92 auto-merge threshold is a **protocol-level guideline**
> for the adjudicating agent, not a feature enforced by the `prox` tool. The
> `prox dupes` command reports all pairs at or above the dupe threshold (default
> 0.80) without distinguishing borderline from auto-merge. The agent reading
> the output applies the 0.92 cutoff when deciding which pairs to auto-merge
> versus adjudicate.

---

## prox CLI Commands

Run these commands in order. Each step depends on the output of the previous one.

### 1. Embed

```bash
prox embed --run-dir <run-dir>
```

Vectorises hypothesis texts (title + statement + mechanism) using TF-IDF.
Writes `proximity/embeddings.npz` and `proximity/embedding-ids.json`.

### 2. Graph

```bash
prox graph --run-dir <run-dir> --threshold 0.3
```

Computes pairwise cosine similarity from embeddings. Creates an adjacency graph
with edges for all pairs with similarity ≥ 0.30. Writes `proximity/graph.json`.

### 3. Clusters

```bash
prox clusters --run-dir <run-dir>
```

Runs community detection (Louvain algorithm or connected-components fallback)
on the proximity graph. Assigns each hypothesis a cluster label (`C-NN`).
Updates `proximity/graph.json` with cluster assignments and writes
`proximity/clusters.json`.

### 4. Dupes

```bash
prox dupes --run-dir <run-dir> --threshold 0.80
```

Reports near-duplicate pairs (similarity ≥ 0.80) to stdout as a JSON array.
Each entry contains `a` (first hypothesis ID), `b` (second hypothesis ID),
and `similarity` (cosine similarity score). Pairs are sorted by descending
similarity.

---

## Adjudication Procedure for Borderline Pairs (0.80 – 0.92)

Pairs with similarity between 0.80 and 0.92 require LLM adjudication. These
are too similar to ignore but not similar enough to auto-merge — the agent
must determine whether they are genuinely distinct hypotheses or duplicates.

### Steps

For each borderline pair:

1. **Read both hypothesis JSON files fully.** Load both files from the
   `hypotheses/` directory and read all fields — not just the title.

2. **Compare along these axes:**
   - **Mechanism:** Are they proposing the same causal mechanism, or do they
     differ in the proposed pathway?
   - **Predictions:** Do they make the same testable predictions, or do they
     predict different outcomes?
   - **Experimental approach:** Do they propose the same experiments, or do
     the experimental designs differ meaningfully?
   - **Evidence base:** Do they cite the same literature, or do they draw on
     different evidence?

3. **Decide:** Assign one of three verdicts:

   | Verdict | Meaning | When to use |
   |---|---|---|
   | **MERGE** | The pair are duplicates | Same mechanism, same predictions, same experiments — one is a paraphrase of the other |
   | **KEEP** | The pair are genuinely distinct | Despite surface similarity, they differ meaningfully in mechanism, predictions, or experimental approach |
   | **FLAG** | Unclear — needs human review | The agent cannot confidently decide; differences are subtle or ambiguous |

4. **Record the decision with rationale** using the output format below.

### Decision Output Format

Record each adjudication decision as a JSON object:

```json
{
  "pair": ["H-0001", "H-0002"],
  "similarity": 0.87,
  "verdict": "MERGE",
  "rationale": "Both hypotheses propose the same mechanism (gut Lactobacillus depletion → vagal TNF-α signaling → hippocampal tau phosphorylation) with identical predictions and overlapping evidence. H-0002 is a paraphrase of H-0001 with minor wording differences.",
  "recommended_action": "Retire H-0002 in favor of H-0001"
}
```

For KEEP decisions:

```json
{
  "pair": ["H-0003", "H-0004"],
  "similarity": 0.84,
  "verdict": "KEEP",
  "rationale": "Although both hypotheses address gut-brain signaling, H-0003 proposes a serotonergic mechanism while H-0004 proposes a dopaminergic mechanism. Their predictions and proposed experiments are distinct.",
  "recommended_action": "No action — both remain active"
}
```

For FLAG decisions:

```json
{
  "pair": ["H-0005", "H-0006"],
  "similarity": 0.81,
  "verdict": "FLAG",
  "rationale": "H-0005 and H-0006 share a similar mechanism but differ in scope — H-0005 is specific to Alzheimer's while H-0006 covers neurodegeneration broadly. Unclear whether the broader framing is meaningfully distinct or just less precise.",
  "recommended_action": "Supervisor should review whether the scope difference justifies keeping both"
}
```

### Auto-Merge Candidates (> 0.92)

Pairs with similarity above 0.92 are auto-merge candidates. These do not
require full adjudication — recommend merge to the supervisor with a brief
note:

```json
{
  "pair": ["H-0010", "H-0011"],
  "similarity": 0.95,
  "verdict": "MERGE",
  "rationale": "Auto-merge candidate: similarity 0.95 exceeds auto-merge threshold (0.92).",
  "recommended_action": "Retire one hypothesis — recommend keeping H-0010 (earlier ID)"
}
```

---

## Cluster Labeling

### Label Format

Clusters use the `C-NN` format (e.g., `C-01`, `C-02`, `C-03`). This matches
the `cluster` field pattern in the hypothesis schema: `^C-[0-9]{2}$`.

Labels are assigned automatically by `prox clusters` based on community
detection results. The numbering starts at `C-01` and increments sequentially.

### Descriptive Labels

After running `prox clusters`, the proximity agent should give each cluster a
brief descriptive label for the supervisor's use. These labels are not stored
in the hypothesis JSON (the `cluster` field holds only the `C-NN` identifier),
but are included in the report to the supervisor.

Example cluster summary:

```
C-01: Gut-brain axis and neuroinflammation (H-0001, H-0002, H-0005)
C-02: Sleep disruption and cognitive decline (H-0003)
C-03: Exercise-induced neurogenesis (H-0004)
```

### Cluster Semantics

Each cluster represents a thematic focus area — a group of hypotheses that
explore similar scientific themes or mechanisms. Clusters help the supervisor:

- Understand the distribution of hypotheses across research themes
- Identify over-explored areas (large clusters) and under-explored gaps
- Make informed decisions about which hypotheses to prioritize

---

## Output Files

The proximity agent owns the `proximity/` directory within each run. Files
written by the pipeline:

| File | Written by | Contents |
|---|---|---|
| `proximity/embeddings.npz` | `prox embed` | Sparse TF-IDF embedding matrix |
| `proximity/embedding-ids.json` | `prox embed` | Ordered list of hypothesis IDs matching matrix rows |
| `proximity/graph.json` | `prox graph`, updated by `prox clusters` | Adjacency graph with edges, similarities, and cluster assignments |
| `proximity/clusters.json` | `prox clusters` | Cluster membership lists, algorithm name, and cluster count |

The `graph.json` structure after clustering:

```json
{
  "threshold": 0.3,
  "nodes": {
    "H-0001": {
      "cluster": "C-01",
      "edges": [
        {"target": "H-0002", "similarity": 0.87},
        {"target": "H-0005", "similarity": 0.45}
      ]
    }
  }
}
```

The `clusters.json` structure:

```json
{
  "clusters": {
    "C-01": ["H-0001", "H-0002", "H-0005"],
    "C-02": ["H-0003"],
    "C-03": ["H-0004"]
  },
  "algorithm": "louvain",
  "num_clusters": 3
}
```
