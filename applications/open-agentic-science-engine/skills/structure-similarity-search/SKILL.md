---
name: structure-similarity-search
description: >
  Search PubChem and ChEMBL for compounds structurally similar to a
  query (Tanimoto similarity) or containing a query substructure.
  Use when finding analogs of hits, checking structural novelty of a
  compound, identifying related compounds with known bioactivity or
  ADMET data, or surveying prior art for patent landscape. Do not use
  to verify a compound identifier resolves (use
  compound-identity-resolution), to retrieve bioactivity data (use
  bioactivity-landscape), or to compute molecular descriptors or
  drug-likeness (use compound-property-profile).
---

## 1. When to use, and when not

Use this skill when you need to find structurally related compounds
in public databases. Entry points include:

- Finding analogs of a hit compound for medicinal chemistry
  exploration.
- Checking structural novelty ("is this compound already known?").
- Finding related compounds with known bioactivity or ADMET data to
  inform a lead series.
- Identifying prior art for patent landscape analysis.

**Do not use when:**

- You need to verify that a compound identifier resolves to a real
  record -> `compound-identity-resolution`.
- You need binding affinity or docking scores
  -> `binding-mode-analysis`.
- You need computed molecular descriptors or drug-likeness
  -> `compound-property-profile`.
- You need bioactivity data, dose-response curves, or
  target-activity relationships -> `bioactivity-landscape`.

## 2. Preconditions

- **Input**: a SMILES string. Validated locally via RDKit before any
  network call. Unparseable SMILES are refused (exit 9).
- **Network access** required for `search` and `substructure`. The
  `analyze` subcommand is offline.
- **No authentication** needed -- both registries are public APIs.
- **Source selection** (`--source`): `pubchem`, `chembl`, or `both`
  (default).
- Run `dde doctor` before first use.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What known compounds are structurally similar? | `dde similar search <SMILES> [--source both] [--threshold <T>] [--max-results 20]` | `raw/compounds/<slug>.similar-<source>.json` + `.meta.json` |
| What known compounds contain this substructure? | `dde similar substructure <SMILES> [--source both] [--max-results 20]` | `raw/compounds/<slug>.substruct-<source>.json` + `.meta.json` |
| What is the verdict? | `dde similar analyze <SMILES>` | `raw/compounds/<slug>.similar.analysis.json` |

Run `search` or `substructure` before `analyze`. `analyze` reads from
disk and applies the `similar-search` threshold set. No network.

`--threshold` applies to similarity search only. Substructure
containment is boolean, not scored -- there is no threshold to set.

Output options: `--json`, `--quiet`, `--out` on all three subcommands.
`--from` on `analyze` only.

## 4. Interpretation contract

### Outcomes

`analyze` returns one of:

- **exact-match** -- at least one hit at or above the
  exact_match_cutoff threshold. The compound itself is in the
  database.
- **known-compound-found** -- at least one hit at or above the
  tanimoto_similarity_cutoff threshold (but below exact_match_cutoff).
  Structurally similar compounds exist.
- **novel** -- no hits above tanimoto_similarity_cutoff. The compound
  is structurally novel with respect to the queried databases.

**Substructure-only analyze always returns "novel."** Substructure
hits do not carry Tanimoto scores (containment is not similarity).
When `analyze` reads a substructure artifact, all hits have
`tanimoto: null`, so `max_tanimoto` is 0.0 -- below every threshold.
This is correct behavior. If you need a similarity verdict, run
`search` (not `substructure`) first. Substructure results are useful
on their own for motif-based queries ("what compounds share this
pharmacophore?") but should not be piped through `analyze` for a
novelty verdict.

### Exit codes

- **exit 0** -- an answer, including a negative one.
- **exit 9** -- refusal. Unparseable SMILES (validated by RDKit before
  any network call). Change the input.
- **exit 1-8** -- failure (network, upstream API, etc.).

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `similar.tanimoto_is_2d_only` | Qualifier (claim-triggered) | At least one hit returned (conditional) | Tanimoto similarity is computed from 2D fingerprints and reflects shared substructure topology, not 3D shape complementarity or biological activity. Two compounds with high Tanimoto may have very different binding modes, selectivity profiles, or ADMET properties. Do not equate structural similarity to functional similarity. |
| `similar.database_coverage_limited` | Qualifier (defect-triggered) | Always (unconditional) | PubChem and ChEMBL cover a fraction of chemical space. A compound with no similar hits may have close analogs in proprietary collections, patent literature, or databases not queried. Do not treat "novel by PubChem/ChEMBL" as "novel." |

The unconditional relay is justified: the novelty claim -- "nothing
like this exists" -- is the claim most likely to be over-read. The
relay converts a claim of absence into a scoped claim.

### Consequence rules

- **Exact-match**: the compound itself is already in the database.
  Both relays fire (`tanimoto_is_2d_only` because hits exist,
  `database_coverage_limited` unconditionally). An exact match on
  2D fingerprint is necessary but not sufficient for identity --
  confirm via `compound-identity-resolution` if needed.
- **Known-compound-found**: structurally similar compounds exist.
  Both relays fire (`tanimoto_is_2d_only` because hits exist,
  `database_coverage_limited` unconditionally). The Tanimoto score
  measures 2D topology, not biological function -- two compounds
  with high Tanimoto may have very different activity profiles. The
  similar compound's known properties are useful leads for
  investigation, not conclusions.
- **Novel**: no hits above the similarity threshold.
  `database_coverage_limited` always fires. `tanimoto_is_2d_only`
  fires only when hits exist (novel-with-hits, where all scores
  fell below the threshold). "Novel" means "not found in the
  databases searched at the given threshold."

### Threshold set: similar-search@1.0

Three thresholds (cite by name, never by value):

- **tanimoto_similarity_cutoff** -- the Tanimoto score below which
  hits are not considered "similar." Configurable via `--threshold`
  and program overrides. Not a scientific constant -- different
  fingerprint types and radii produce different similarity
  distributions. Quote `thresholds_applied` from the analysis
  artifact to report the value in force.
- **exact_match_cutoff** -- fixed. Tanimoto at this cutoff with the
  same fingerprint is a necessary (not sufficient) condition for
  identity.
- **novelty_threshold** -- **UNRESOLVED**. "How similar is too
  similar?" is a program decision that depends on the patent
  landscape, the therapeutic area, and the IP strategy. The tool
  reports hits and their scores; the classification is the
  specialist's. Do not invent a value for this threshold.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/`
that cites the `.analysis.json`, satisfies all mandatory relays, and
does not over-read a "novel" verdict. Everything the tools emitted
stays under `raw/compounds/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the
tool emitted. If the tool did not run, there is no value. If a
required tool is unavailable, report the task as blocked. Do not
estimate, and do not proceed on an assumed result.

### Named pathologies

- **Equating Tanimoto similarity to functional similarity.** Two
  compounds with high Tanimoto may have very different binding modes,
  selectivity profiles, ADMET properties, and biological activity.
  Structural similarity is a starting point for investigation, not a
  conclusion about function.
- **Treating "novel by PubChem/ChEMBL" as "novel."** These databases
  cover a fraction of chemical space. Proprietary collections, patent
  filings, unpublished compounds, and specialty databases are not
  searched. A "novel" verdict means "not found here," not "does not
  exist."
- **Using substructure search for novelty assessment.** Substructure
  containment is a structural relationship ("does compound X contain
  motif Y?"), not a similarity measure. Running `analyze` on
  substructure-only results always returns "novel" because
  substructure hits lack Tanimoto scores. Use similarity search for
  novelty assessment.
- **Treating a near-threshold hit as definitive.** A hit at the
  tanimoto_similarity_cutoff boundary is a function of the threshold,
  the fingerprint type, and the fingerprint radius, not a biological
  truth. Different ECFP radii can shift the score for the same
  compound pair.
- **Inventing a novelty threshold.** The novelty_threshold is
  UNRESOLVED by design. "How similar is too similar?" is a program
  decision. Do not pick a number -- report the scores and let the
  program decide.

## 6. Provenance

Databases queried:

- **PubChem PUG REST** -- NCBI's public compound database. Similarity
  and substructure endpoints use an asynchronous ListKey protocol (the
  tool handles polling internally).
- **ChEMBL REST** -- EMBL-EBI's public bioactivity database.
  Similarity and substructure endpoints are synchronous with
  pagination.

Both are public APIs with no authentication required. Rate limits are
enforced by the CLI.
