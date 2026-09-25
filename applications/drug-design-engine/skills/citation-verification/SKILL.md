---
name: citation-verification
description: >
  Verify that citations in a document resolve to real records and that claimed
  titles match. Use before trusting a document's reference list.
---

## 1. When to use, and when not

Use when you need to verify whether citations in a document are real and accurate.

Do NOT use when:
- You need to resolve a single identifier to its record → use `citation-resolution` skill.
- You need to search for literature by topic → use `literature-search` skill.

## 2. Preconditions

A file (JSON, Markdown, or text) containing citations or references.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Are the citations in this document real? | `dde cite verify <FILE>` | `raw/literature/<slug>.citations.json` |
| What is the verification assessment? | `dde cite analyze --from raw/literature/` | `raw/literature/<slug>.analysis.json` |

## 4. Interpretation contract

### Statuses
- **verified** — citation resolved and title matched within tolerance.
- **suspect-title-match** — citation resolved but title similarity is between suspect floor and match tolerance. Do NOT treat as verified.
- **phantom** — citation did not resolve to any real record, or title completely mismatched.
- **unverified** — could not check due to network error or timeout.

### Verdicts
- `all-verified` — every citation verified, none suspect or phantom.
- `suspect-matches-present` — at least one suspect title match.
- `phantom-citations-present` — at least one phantom citation.
- `partially-unresolved` — some citations could not be checked.
- `no-citations-found` — no extractable citations found.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `cite.phantom_citation` | Defect | any phantom | Name the phantom references. A phantom citation invalidates the claim resting on it. |
| `cite.suspect_title_match` | Qualifier | any suspect-title-match | State that the reference resolved but the title did not match. Do not report it as verified. |
| `cite.unresolved_offline` | Qualifier | any unverified from network error | Confine verification claim to references that resolved. |
| `cite.extraction_incomplete` | Qualifier | extraction_basis is not "structured" | State that references were recovered by pattern match. Missed references were not checked. |

### Threshold set: `citation-verification@1.0`

| Threshold | Default | Meaning |
|---|---|---|
| `title_match_tolerance` | 0.75 | Boundary between verified and suspect |
| `title_suspect_floor` | 0.45 | Boundary between suspect and phantom |
| `max_phantom_citations` | 0 | Maximum acceptable phantom count |
| `max_suspect_citations` | UNRESOLVED | Not yet calibrated |
