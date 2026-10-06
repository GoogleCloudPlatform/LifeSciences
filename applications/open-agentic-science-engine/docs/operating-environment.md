# Operating Environment — Credential and Access Architecture

This document states the actual credential and access mechanism for each
external dependency dde's tools call. It exists so that an agent
reading a `dde doctor` WARN can tell whether the check is describing a
real gap or the check's own blind spot, without rediscovering the
deployment's architecture from first principles.

This is not a troubleshooting guide and does not duplicate `dde
doctor`'s job. Doctor does mechanical capability checks; this doc gives
standing context for interpreting them.

---

## GCP — Application Default Credentials (ADC)

**Mechanism:** GCP Application Default Credentials, resolved via
`google.auth.default()`. In the current deployment this resolves through
the GCE metadata server to a workload-identity service account (confirmed
during #162: `google.auth.default()` returns
`google.auth.compute_engine.credentials.Credentials` and the token
refreshes successfully). No explicit key file (`GOOGLE_APPLICATION_CREDENTIALS`)
is needed or used in this deployment.

**Used by:**

- `dde alphafold predict` — calls `aiplatform.init(project=...,
  location=...)` with no explicit `credentials=` argument, relying
  entirely on the ADC chain (`alphafold.py`).
- `dde alphagenome score-variant`, `predict-interval` (Vertex backend)
  — `_access_token()` tries `google.auth.default(scopes=[...])` first,
  then falls back to `gcloud auth print-access-token` if ADC is
  unavailable. The sidecar records which source was used
  (`alphagenome.py`).

**Doctor check:** As of #162, `_check_credentials()` first checks for
`GOOGLE_APPLICATION_CREDENTIALS` (explicit key file), then falls through
to attempt real `google.auth.default()` ADC resolution and reports the
credential type that resolved. This replaced the pre-#162 behaviour that
checked only the literal env var and false-negatived on every non-env-var
credential source (metadata server, workload identity, `gcloud auth
application-default login`).

---

## AlphaGenome pip backend — API key (`ALPHAGENOME_API_KEY`)

**Mechanism:** A bespoke API key read from the `ALPHAGENOME_API_KEY`
environment variable. There is no ADC fallback for this credential; the
env-var check is the correct and only check.

**Used by:**

- `dde alphagenome ism` — the ISM command is pip-backend-only (the
  Vertex endpoint rejects `score_ism_variants` as an invalid request
  type). The command calls `_require_api_key()` which reads
  `os.environ.get("ALPHAGENOME_API_KEY")` and raises `CredentialError`
  if unset (`alphagenome.py`).
- `dde alphagenome score-variant --backend pip` — also calls
  `_require_api_key()`, though the pip backend for `score-variant` is
  not yet implemented.

**Doctor check:** `_check_credentials()` checks
`os.environ.get("ALPHAGENOME_API_KEY")` — presence only, never the
value. This is the correct check for a bespoke API key with no fallback
chain.

---

## AlphaFold DB (AFDB) — public, no auth

**Mechanism:** Unauthenticated HTTPS. No API key, no auth header.

**Endpoint:** `https://alphafold.ebi.ac.uk/api/prediction`

**Used by:** `dde alphafold fetch` (`alphafold.py`). Fetches mmCIF
structures, PAE matrices, and API records by UniProt accession via
`http.get_json()` and `http.get_bytes()` with no authentication
parameters.

**Doctor check:** None specific. AFDB availability is not checked by
doctor; a fetch failure surfaces at call time.

---

## UniProt — public, no auth

**Mechanism:** Unauthenticated HTTPS. No API key, no auth header.

**Endpoint:** `https://rest.uniprot.org/uniprotkb`

**Used by:** `dde alphafold fetch` (`alphafold.py`). Used internally
for canonical sequence length cross-checks — `_canonical_length()` calls
`http.get_json()` with no authentication. A failure degrades the
coverage warning to a weaker form but never blocks the fetch.

**Doctor check:** None specific.

---

## gnomAD — public, no auth

**Mechanism:** Unauthenticated HTTPS POST to the GraphQL API. No API
key, no auth header.

**Endpoint:** `https://gnomad.broadinstitute.org/api`

**Used by:** `dde genetics fetch` (`genetics.py`). Sends a GraphQL
query via `http.request("POST", ...)` with only a `Content-Type:
application/json` header — no authentication.

**Doctor check:** None specific. gnomAD's known quirk — errors arriving
as HTTP 200 — is documented as a standing fault in `_check_known_faults()`
but its availability is not probed.

---

## GTEx — public, no auth

**Mechanism:** Unauthenticated HTTPS. No API key, no auth header.

**Endpoint:** `https://gtexportal.org/api/v2`

**Used by:** `dde gtex fetch` (`gtex.py`). Calls `http.get_json()`
and `http.request("GET", ...)` with no authentication parameters.

**Doctor check:** None specific.

---

## Human Protein Atlas (HPA) — public, no auth

**Mechanism:** Unauthenticated HTTPS. No API key, no auth header.

**Endpoint:** `https://www.proteinatlas.org` (per-gene JSON and
`search_download.php`)

**Used by:** `dde expression fetch` and `dde expression
fetch-single-cell` (`expression.py`). All requests go through
`http.request("GET", ...)` and `http.get_json()` with no authentication
parameters.

**Doctor check:** HPA's silent column-drop behaviour and release-pinning
gap are documented as standing faults in `_check_known_faults()`, but HPA
availability itself is not probed.

---

## Europe PMC — public, no auth

**Mechanism:** Unauthenticated HTTPS. No API key, no auth header.

**Endpoint:** `https://www.ebi.ac.uk/europepmc/webservices/rest/search`

**Used by:** `dde litref resolve` (`litref.py`). Calls `http.request("GET", ...)`
with no authentication parameters.

**Doctor check:** None specific.

Note: The issue's mention of `PUBMED_API_KEY` refers to a BioCompass
context, not to dde. DDE's own `litref.py` queries Europe PMC (not
PubMed directly) and uses no API key.

---

## ClinicalTrials.gov — public, no auth

**Mechanism:** Unauthenticated HTTPS. No API key, no auth header.

**Endpoint:** `https://clinicaltrials.gov/api/v2/studies`

**Used by:** `dde litref resolve` (`litref.py`). Calls `http.request("GET", ...)`
with no authentication parameters.

**Doctor check:** None specific.

---

## Dependencies that are NOT in this codebase

**PubChem:** Not called by any dde tool. The issue (#181) mentioned
PubChem in its enumeration, but no command in the `tools/dde/commands/`
tree makes an HTTP call to PubChem.

---

## Summary table

| Dependency          | Auth mechanism                       | Env var / credential                | Tool(s)                                        |
|---------------------|--------------------------------------|-------------------------------------|-------------------------------------------------|
| GCP (Vertex)        | ADC (`google.auth.default()`)        | GCE metadata workload identity      | `alphafold predict`, `alphagenome` (vertex)     |
| AlphaGenome pip     | API key                              | `ALPHAGENOME_API_KEY`               | `alphagenome ism`, `alphagenome score-variant --backend pip` |
| AlphaFold DB        | Public, no auth                      | —                                   | `alphafold fetch`                               |
| UniProt             | Public, no auth                      | —                                   | `alphafold fetch` (coverage cross-check)        |
| gnomAD              | Public, no auth                      | —                                   | `genetics fetch`                                |
| GTEx                | Public, no auth                      | —                                   | `gtex fetch`                                    |
| HPA                 | Public, no auth                      | —                                   | `expression fetch`, `expression fetch-single-cell` |
| Europe PMC          | Public, no auth                      | —                                   | `litref resolve`                                |
| ClinicalTrials.gov  | Public, no auth                      | —                                   | `litref resolve`                                |
