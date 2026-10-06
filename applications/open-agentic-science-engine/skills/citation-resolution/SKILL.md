---
name: citation-resolution
description: >
  Resolve a cited paper or clinical trial to a real registry record — or
  determine that no such record exists. Use when verifying that a
  citation is real before relying on it in a finding, checking whether a
  trial acronym maps to a unique registry entry, or confirming that a
  PMID, DOI, or NCT number resolves. Do not use to read or summarise a
  paper's contents (the tool does not read records), to assess whether a
  paper supports a claim (that is the specialist's job), or to search
  for literature on a topic (use literature search tools).
---

## 1. When to use, and when not

Use this skill when you need to verify that a cited record exists before
a finding relies on it. Entry points include:

- Confirming that a PMID, DOI, or NCT number resolves to a real
  record before citing it in a finding.
- Checking whether a trial acronym (e.g. "PALOMA-3") maps to exactly
  one registry entry — or to several.
- Establishing that a claimed reference is real, as a precondition
  for a specialist to then read and evaluate it.
- Discovering that a cited record does not exist, which is itself a
  reportable finding — it may indicate a fabricated reference.

**Do not use when:**

- You need to read or summarise a paper's contents — this tool
  establishes existence, not content.
- You need to judge whether a paper supports the claim it was cited
  for — that is the specialist's job, and a tool that guessed at
  support would reintroduce the fabrication it exists to catch.
- You need to search for literature on a topic — use literature
  search tools.

## 2. Preconditions

- **Citation input**: one of:
  - **NCT number** (e.g. `NCT01942135`) — unique identifier, looked
    up on ClinicalTrials.gov.
  - **PMID** (e.g. `PMID:25332249` or a bare number at 7+ digits) —
    unique identifier, looked up on Europe PMC. Short numbers (< 7
    digits) without the `PMID:` prefix are not treated as PMIDs to
    avoid confusing a year with a paper.
  - **PMCID** (e.g. `PMC1234567`) — unique identifier, looked up on
    Europe PMC.
  - **DOI** (e.g. `10.1056/NEJMoa1505270`) — unique identifier,
    looked up on Europe PMC.
  - **Name** (e.g. `PALOMA-3`) — searched by title on both
    registries. Every match is returned; the tool never picks one.
- **Source override** (`--source`): `auto` (default), `literature`,
  or `trials`. `auto` dispatches identifiers by their type and sends
  names to both registries.
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
| Does this citation resolve to a real record? | `dde litref resolve <CITATION> [--source auto\|literature\|trials]` | `raw/literature/<slug>.trials.json`<br>`raw/literature/<slug>.literature.json`<br>`raw/literature/<slug>.meta.json` |
| What is the verdict? | `dde litref analyze <CITATION>` | `raw/literature/<slug>.analysis.json` |

Run `resolve` before `analyze`. `analyze` reads from disk and applies
the `litref` threshold set. The only threshold is
`max_matches_recorded` (page size for record enumeration), which is
operational — it changes how much detail is recorded but never changes
the verdict.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.

## 4. Interpretation contract

### Verdicts

`analyze` returns one of:

- **resolved** — exactly one record matched. If resolved by a unique
  identifier (NCT, PMID, PMCID, DOI), the record exists. If resolved
  by name, the match is flagged — see `name_match_not_unique_identifier`
  below.
- **ambiguous** — more than one record matched. This is a first-class
  outcome and a hard stop: the tool refused to choose, and the
  specialist must obtain a unique identifier before proceeding.
- **not_found** — zero records matched. This is a completed analysis
  and a reportable finding: the cited record does not exist in the
  queried registries.

### Exit codes and the refusal contract

The project separates three exit categories (tool-design-guidance §8):

- **exit 0** — an answer, including a negative one. Both `resolved`
  and `not_found` exit 0.
- **exit 9** — a refusal. Change the input; retrying the same query
  is pointless. `ambiguous` exits 9.
- **exit 1-8** — a failure. Keep the input; the fault is elsewhere
  and may clear.

`analyze` exits **9** on `ambiguous`. The analysis artifact is still
written and its path is still printed — this is not "nothing happened."
The artifact records what was refused and why, so it is citable evidence
that the question was asked and could not be resolved. Treat exit 9 as
**"blocked, obtain a unique identifier"**, not as a tool failure and
not as an empty result. Do not fall back to the first candidate.

The candidate list printed to stderr is not a menu. It is printed so
the specialist can go and find the right identifier — not so they can
pick the most likely-looking one. Picking produces a clean audit trail
for the wrong record, which is the failure this whole design exists to
prevent.

### The PALOMA-3 case

The tool exists because of this failure mode. Searching
ClinicalTrials.gov for "PALOMA-3" returns two real trials:

1. NCT05388669 — Janssen, lazertinib + amivantamab (NSCLC)
2. NCT01942135 — Pfizer, palbociclib + fulvestrant (breast)

The wrong one was returned first when this was tested. NCT01942135's `acronym` field is null;
the name appears only in its title. A resolver that picked the top hit
would hand a breast-cancer specialist an NSCLC trial to verify against,
the specialist would "verify" it, and the audit trail would look clean.
A silent wrong answer is worse than a loud missing one — so more than
one match is `ambiguous`, a first-class outcome and a hard stop.

### Mandatory relays

Relays fall into two kinds (see pocket-druggability for the full
definition):

- **Qualifier** — carry a caveat into the finding. The finding still
  reports the result, scoped.
- **Stop** — block a substitution. Adding a disclaimer does not
  satisfy the relay; it makes the substitution look diligent.

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `litref.resolved_not_verified` | Stop | A record was resolved (conditional) | Confirm the record says what the claim says before citing it. This tool established only that the record exists; it did not read it. |
| `litref.ambiguous_name` | Stop | More than one record matched (conditional) | Stop and obtain a unique identifier — NCT, PMID, or DOI — before the claim proceeds. Do not choose among the candidates listed: they are listed because the tool refused to choose. |
| `litref.name_match_not_unique_identifier` | Qualifier | Exactly one record matched by name, not by unique identifier (conditional) | Report the match as "one record found by title search", not as the record. A title search cannot see a name that appears only in an abstract or trial description, so uniqueness is unproven. |

Check `mandatory_relays` in both the `.analysis.json` and the
`.meta.json`. Every relay code present must be satisfied in the finding.

### Consequence rules

- **Resolved by unique identifier**: the record exists. The
  `resolved_not_verified` relay still applies — existence is not
  support. The specialist must read the record to confirm it says
  what the claim says.
- **Resolved by name (single match)**: the record probably exists,
  but uniqueness is unproven. Both `resolved_not_verified` and
  `name_match_not_unique_identifier` fire. The finding must say
  "one record found by title search."
- **Ambiguous**: blocked. The finding cannot proceed until a unique
  identifier is provided. Do not pick the most-likely candidate —
  the tool's entire design is the refusal to pick.
- **Not found**: report the negative. A citation that does not
  resolve is a finding — it may indicate a fabricated reference, or
  a registry that does not index the source. State which registries
  were searched.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.analysis.json`, satisfies all mandatory relays, and does
not choose among ambiguous matches. Everything the tools emitted stays
under `raw/literature/`.

Thresholds are cited by name (e.g. "the configured
`max_matches_recorded` threshold"), never by value. The values in force
are in the artifact: every `.analysis.json` carries `threshold_set`
(name@version), `thresholds_applied`, `threshold_sources` (default |
program | flag), and `threshold_provenance`. A specialist quoting a
number should quote it from the analysis they are citing.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Choosing the first candidate on an ambiguous result.** The tool
  exits 9 specifically to block this. The candidates are listed so
  the specialist can find the right identifier elsewhere — they are
  not a menu. Picking one reintroduces the failure the tool exists
  to prevent. The PALOMA-3 case returned the wrong trial first when
  tested.
- **Treating exit 9 as a tool failure or as "nothing happened."**
  Exit 9 is a refusal, not a failure. The analysis artifact is
  written and printed — it records what was refused and why, and it
  is citable evidence the question was asked. A specialist who
  reports "tool failed" or discards the artifact on an ambiguous
  result has missed the finding.
- **Treating not_found as a tool failure.** not_found exits 0 and is
  a reportable finding. A citation that does not resolve may indicate
  a fabricated reference.
- **Treating resolved as verified.** The tool established that the
  record exists. It did not read the record and has not checked that
  it supports the claim. A specialist who skips verification because
  the citation "passed" has confused existence with support.
- **Inferring uniqueness from a single name match.** A title search
  on Europe PMC or ClinicalTrials.gov cannot see a name that appears
  only in an abstract, description, or a null acronym field. One
  match by name is evidence of existence, not proof of uniqueness.
- **Reporting "verified" when the tool said "resolved."** The word
  "verified" implies someone read the record and confirmed it
  supports the claim. Nobody has. The tool's word is "resolved."
