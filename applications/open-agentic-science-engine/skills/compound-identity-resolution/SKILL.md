---
name: compound-identity-resolution
description: >
  Confirm that a PubChem CID, ChEMBL ID, or compound name resolves to a
  real compound record — or determine that no such record exists — and
  retrieve published annotations (synonyms, drug status, mechanism of
  action) for a resolved compound. Use when verifying that a compound
  identifier is real before relying on it in a finding, checking whether
  a compound name maps to a unique registry entry, confirming that a CID
  or ChEMBL ID resolves, surfacing canonical name and SMILES for
  cross-checking, or retrieving what is already known about a compound
  from PubChem and ChEMBL. Do not use to retrieve bioactivity data (use
  bioactivity-landscape), to resolve a literature citation (use
  citation-resolution), or to compute molecular descriptors or
  structural alerts (use compound-property-profile).
---

## 1. When to use, and when not

Use this skill when you need to verify that a compound identifier
resolves to a real registry record before a finding relies on it.
Entry points include:

- Confirming that a PubChem CID resolves to a real compound before
  citing it in a finding.
- Confirming that a ChEMBL ID exists in the ChEMBL database.
- Checking whether a compound name maps to exactly one registry
  record — or to several.
- Surfacing the canonical name and SMILES for a resolved compound so
  a downstream step can cross-check what it is working with.
- Discovering that a compound identifier does not exist in any
  queried registry, which is itself a reportable finding.

**Do not use when:**

- You need bioactivity data, dose-response curves, or target-activity
  relationships -> `bioactivity-landscape`.
- You need to resolve a literature citation or clinical trial
  -> `citation-resolution`.
- You need computed molecular descriptors, drug-likeness rules, or
  structural alerts -> `compound-property-profile`.
- You need predicted ADMET properties
  -> `admet-property-prediction`.

## 2. Preconditions

- **Identifier input**: one of:
  - **PubChem CID** (e.g. `2244`) — all digits, unique identifier,
    looked up on PubChem PUG REST.
  - **ChEMBL ID** (e.g. `CHEMBL25`) — CHEMBL followed by digits,
    unique identifier, looked up on ChEMBL REST.
  - **Compound name** (e.g. `palbociclib`) — searched by name on
    PubChem and ChEMBL. Every match is returned; the tool never picks
    one.
- **Source override** (`--source`): `auto` (default), `pubchem`, or
  `chembl`. `auto` dispatches identifiers by their type and sends
  names to both registries.
- **Network access** required for `resolve` (queries PubChem PUG REST
  and ChEMBL REST). `analyze` is offline.
- **No authentication** needed — both registries are public APIs.
- Run `dde doctor` before first use. It ends with a verdict line:
  `STOP` means fix or report before running anything; `PROCEED` means
  work, and the grouped warnings tell you which commands would refuse,
  which results need careful reading, and which are the tooling lead's
  to clear. Do not judge by the warning count; the verdict line grades
  them for you.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Does this compound identifier resolve? | `dde compreg resolve <IDENTIFIER> [--source auto\|pubchem\|chembl]` | `raw/compounds/<slug>.registry-pubchem.json`<br>`raw/compounds/<slug>.registry-chembl.json`<br>`raw/compounds/<slug>.meta.json` |
| What is the outcome? | `dde compreg analyze <IDENTIFIER>` | `raw/compounds/<slug>.analysis.json` |

Run `resolve` before `analyze`. `analyze` reads from disk and applies
the `compreg` threshold set. No network.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.

## 4. Interpretation contract

### Outcomes

`analyze` returns one of:

- **resolved** — exactly one match across all registries queried. If
  resolved by a unique identifier (CID or ChEMBL ID), the record
  exists. If resolved by name, the match is flagged — see
  `compreg.name_match_not_unique_identifier` below.
- **ambiguous** — more than one match. This is a first-class outcome
  and a hard stop: the tool refused to choose, and the specialist must
  obtain a unique identifier before proceeding.
- **not_found** — zero matches across all registries queried. This is
  a completed analysis and a reportable finding: the compound does not
  exist in the queried registries.

### Exit codes and the refusal contract

- **exit 0** — an answer, including a negative one. Both `resolved`
  and `not_found` exit 0.
- **exit 9** — a refusal. Change the input; retrying the same query
  is pointless. `ambiguous` exits 9.
- **exit 1–8** — a failure. Keep the input; the fault is elsewhere
  and may clear.

`analyze` exits **9** on `ambiguous`. The analysis artifact is still
written and its path is still printed — this is not "nothing happened."
The artifact records what was refused and why, so it is citable evidence
that the question was asked and could not be resolved. Treat exit 9 as
**"blocked, obtain a unique identifier"**, not as a tool failure and
not as an empty result. Do not fall back to the first candidate.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `compreg.resolved_not_verified` | Qualifier (claim-triggered) | Outcome is `resolved` (conditional) | This tool confirmed the identifier maps to a real compound record and surfaced its canonical name and SMILES. It did not verify biological activity, mechanism, or therapeutic indication — those claims require separate evidence. |
| `compreg.name_match_not_unique_identifier` | Qualifier (claim-triggered) | Outcome is `resolved` AND identifier kind is `name` (conditional) | This compound was matched by name, not by a unique registry identifier. A name search may miss synonyms and cannot prove uniqueness. Obtain the CID or ChEMBL ID and re-resolve. |

Both relays are conditional — they fire only on `resolved` outcome.
Check `mandatory_relays` in both the `.analysis.json` and the
`.meta.json`. Every relay code present must be satisfied in the finding.

### Consequence rules

- **Resolved by unique identifier**: the record exists. The
  `resolved_not_verified` relay still applies — existence is not
  verification. The tool confirmed the identifier maps to a real
  compound; it has not confirmed that the compound has the claimed
  biological activity, mechanism, or therapeutic indication.
- **Resolved by name (single match)**: the record probably exists,
  but uniqueness is unproven. Both `resolved_not_verified` and
  `name_match_not_unique_identifier` fire. The finding must note
  that the compound was matched by name search, not by unique
  identifier.
- **Ambiguous**: blocked. The finding cannot proceed until a unique
  identifier is provided. Do not pick the most-likely candidate —
  the tool's entire design is the refusal to pick.
- **Not found**: report the negative. A compound that does not
  resolve is a finding — it may indicate a non-existent compound, a
  registry that does not index it, or an incorrect identifier. State
  which registries were searched.

### Threshold set: compreg@1.0

The threshold set is **empty**. `compreg` makes no scientific judgment,
so it has no scientific cutoffs. The ambiguity contract — more than one
match is ambiguous — is hardcoded, exactly as in `litref`. The empty
set is declared so the analysis artifact carries a well-formed
`threshold_set` tag and program-level overrides can be added later if a
meaningful threshold emerges. There is nothing to cite by name because
there are no thresholds.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.analysis.json`, satisfies all mandatory relays, and does
not choose among ambiguous matches. Everything the tools emitted stays
under `raw/compounds/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Picking a candidate from an ambiguous result.** The tool exits 9
  specifically to block this. The candidates are listed so the
  specialist can find the right identifier elsewhere — they are not a
  menu. Picking one reintroduces the failure the tool exists to prevent:
  a clean audit trail for the wrong compound.
- **Treating a name-matched `resolved` as equivalent to an
  ID-matched `resolved`.** It is not. The
  `compreg.name_match_not_unique_identifier` relay exists for exactly
  this reason. A name search may miss synonyms and cannot prove
  uniqueness — a single name match is evidence of existence, not proof
  of uniqueness.
- **Treating `not_found` as an error rather than a finding.**
  `not_found` exits 0 and is a reportable finding. A compound that
  does not resolve may indicate a non-existent identifier, a registry
  that does not index it, or a fabricated compound reference.
- **Using compreg to verify biological activity.** This tool confirms
  identity — that a compound record exists and surfaces its canonical
  name and SMILES. It does not verify that the compound has the claimed
  activity, mechanism, or therapeutic indication. Those claims require
  separate evidence from bioactivity tools.
- **Computing or guessing compound identity yourself instead of using
  the tool.** The tool queries the authoritative registries and records
  the verbatim responses. A finding that cites a compound record
  without the tool's output has no provenance and cannot be audited.
- **Treating a PubChem pharmacological classification as a verified
  mechanism.** Database classifications are indexed metadata from
  heterogeneous sources. They are not equivalent to a validated
  mechanism of action study.
- **Carrying a ChEMBL max_phase into a current regulatory claim.**
  max_phase 4 means approved somewhere for something. It does not
  mean approved for the indication under study, in the relevant
  jurisdiction, or still marketed.
- **Annotating without resolving identity first.** The annotation
  tool requires a CID. Passing a CID that was not first verified via
  compreg risks annotating the wrong compound. The compreg ->
  pubchem sequence is enforced by the CID-only input contract.
- **Treating "unknown" as "no information exists."** Unknown means
  minimal PubChem annotation. The compound may have extensive
  characterization in proprietary databases, unpublished studies, or
  databases not queried.

## 6. Provenance

Registries queried:

- **PubChem PUG REST** — NCBI's public compound database
  (pubchem.ncbi.nlm.nih.gov). The annotation tool (section 7)
  queries additional PubChem endpoints: synonyms, classification,
  and InChIKey for cross-referencing to ChEMBL.
- **ChEMBL REST** — EMBL-EBI's public bioactivity database
  (www.ebi.ac.uk/chembl). The annotation tool queries the molecule
  endpoint for drug development phase and the mechanism endpoint for
  mechanism of action.

Both are public APIs with no authentication required. Rate limits are
enforced by the CLI.

## 7. Compound annotation

Compreg (sections 1–6) answers "does this compound exist?" Annotation
answers "what is already known about it?" — synonyms, trade names,
pharmacological classification, drug development status, and mechanism
of action. Annotation is the enrichment step after identity resolution.
Both tool groups share the CID/ChEMBL ID namespace and the
`raw/compounds/` output directory.

### When to use annotation (vs. compreg)

Use the annotation tool group when you have a resolved PubChem CID and
need to know what is already published about the compound:

- Retrieving synonyms and trade names for a resolved compound.
- Checking whether a compound is a known drug with clinical trial or
  approval history.
- Surfacing pharmacological classification and mechanism of action.
- Determining drug development phase (ChEMBL max_phase).

**Input is a PubChem CID (integer).** Resolve to a CID first via
`dde compreg resolve` if you have a name. This enforces identity
resolution before annotation — the annotation tool does not accept
names, preventing annotation of the wrong compound.

**Do not use when:**

- You need to verify that a compound identifier exists
  -> use `dde compreg resolve` (section 3).
- You need bioactivity data, dose-response curves, or target-activity
  relationships -> `bioactivity-landscape`.
- You need computed molecular descriptors or structural alerts
  -> `compound-property-profile`.

### Preconditions

- **Input**: PubChem CID (integer). Not a name, not a ChEMBL ID.
- **Network access** required for `annotate` (queries PubChem PUG REST
  and ChEMBL REST). `analyze-annotation` is offline.
- **No authentication** needed — both registries are public APIs.
- **Identity resolution first**: the CID should have been verified via
  `dde compreg resolve` before annotating. The CID-only input
  contract enforces this sequence — if you only have a name, you must
  resolve it first.

### Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What is known about this compound (synonyms, drug status, MoA)? | `dde pubchem annotate <CID>` | `raw/compounds/<slug>.pubchem-annotation.json`<br>`raw/compounds/<slug>.pubchem-annotation.meta.json` |
| Is this a known drug? What is its development status? | `dde pubchem analyze-annotation <CID>` | `raw/compounds/<slug>.pubchem-annotation.analysis.json` |

Run `annotate` before `analyze-annotation`. `analyze-annotation` reads
from disk and applies the `pubchem-annotation` threshold set. No
network.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.
`analyze-annotation` additionally accepts `--from` for reading from an
alternate source directory.

### Interpretation contract

#### Outcomes

`analyze-annotation` returns one of:

- **known-drug** — ChEMBL max_phase >= 1. The compound has entered
  clinical trials or is approved (for some indication, in some
  jurisdiction — see relay).
- **known-compound** — annotations exist (synonyms, pharmacological
  classification, actions) but no drug development history.
- **unknown** — CID resolves on PubChem but has minimal annotation.
  Rare (most PubChem compounds have at least synonyms).
- **not_found** — CID does not exist on PubChem. Exit 0, artifact
  still written (same pattern as compreg).

#### Exit codes

Same contract as compreg:

- **exit 0** — an answer (including not_found).
- **exit 1–8** — a failure (network, etc.).
- No exit 9 for annotation — there is no ambiguity contract (input
  is a unique CID).

#### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `pubchem.annotation_is_not_validation` | Qualifier (claim-triggered) | Outcome is `known-drug` or `known-compound` (conditional) | Published annotations (synonyms, MoA, pharmacological class) are database records, not in-house validation. Do not treat a PubChem pharmacological classification as equivalent to a verified mechanism study. These are indexed metadata, which may be incomplete, dated, or derived from heterogeneous sources. |
| `pubchem.drug_status_is_development_history` | Qualifier (claim-triggered) | Outcome is `known-drug` with ChEMBL max_phase >= 1 (conditional) | A ChEMBL max_phase of 4 means the compound has been approved for some indication in some jurisdiction. It does not confirm the compound is approved for the indication under study, or that it is still marketed. Do not carry the development phase into a current regulatory claim without checking the specific indication and jurisdiction. |

Both relays are conditional. The first fires only when the compound has
annotations to over-read. The second fires only when drug status data
exists. Check `mandatory_relays` in the `.analysis.json`. Every relay
code present must be satisfied in the finding.

#### Consequence rules

- **Known-drug**: the compound has drug development history. The
  `annotation_is_not_validation` relay applies to all annotations. The
  `drug_status_is_development_history` relay additionally applies. A
  max_phase value tells you where the compound has been, not where it
  is now or whether it is approved for your indication.
- **Known-compound**: annotations exist but no drug data. Only
  `annotation_is_not_validation` fires. The annotations (synonyms,
  pharmacological class) are database records from heterogeneous
  sources.
- **Unknown**: minimal annotations. No relays fire (nothing to
  over-read). This is unusual — most PubChem compounds have at least
  synonyms — and may indicate a very new or niche compound.
- **Not found**: CID does not exist. Report the negative as a finding.

#### Threshold set: pubchem-annotation@1.0

The threshold set is **empty**. The known-drug / known-compound /
unknown classification is categorical, based on presence of drug
development data (ChEMBL max_phase >= 1). No scientific cutoffs. The
empty set is declared so the analysis artifact carries a well-formed
`threshold_set` tag. There is nothing to cite by name because there
are no thresholds.
