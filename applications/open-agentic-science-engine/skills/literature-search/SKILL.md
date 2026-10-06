---
name: literature-search
description: >
  Search and retrieve scientific literature — PubMed, preprint servers
  (arXiv, bioRxiv), and PubMed Central full text. Use when discovering
  what has been published on a topic, gene, compound, or disease, when
  establishing the volume and recency of literature for a target, when
  retrieving full-text articles, or when searching preprint servers for
  recent work. Do not use to verify that a specific citation exists (use
  citation-verification), to search cancer genomics databases (use
  dde cbioportal), or to assess whether a paper supports a claim.
---

## 1. When to use, and when not

Use this skill when you need to discover, search, or retrieve scientific
literature. Entry points include:

- Finding publications related to a gene, pathway, compound, or disease
  as part of target validation or literature review.
- Establishing the volume and recency of research activity for a gene
  or topic — a gene with hundreds of recent publications has a different
  evidence base than one with three papers from 2008.
- Identifying the dominant journals and MeSH terms for a research area
  to refine subsequent searches.
- Checking whether a claimed literature consensus exists — does the
  published record support the assertion that "multiple studies have
  shown X"?
- Retrieving the full text of a PubMed Central article for detailed
  analysis or citation extraction.
- Searching preprint servers for recent, not-yet-peer-reviewed work.

**Do not use when:**

- You need to verify that a specific PMID, DOI, or NCT number resolves
  to a real record → `citation-verification`.
- You need cancer genomics study data → `dde cbioportal search`.
- You need to count how many papers support a specific claim — a keyword
  search counts matches, not evidence.

### Question-type routing

| Question type | Command | Skill |
|---|---|---|
| What has been published on a topic? | `dde pubmed search` | this skill |
| What does the result set look like? | `dde pubmed analyze` | this skill |
| Fetch full text of a PMC article | `dde pubmed fulltext` | this skill |
| Find recent preprints (physics, CS, math) | `dde preprint search --source arxiv` | this skill |
| Find recent preprints (biology, life sciences) | `dde preprint search --source biorxiv` | this skill |
| Verify a citation (PMID, DOI, NCT) exists | `dde cite verify` | citation-verification |
| Resolve a literature identifier | `dde litref resolve` | citation-resolution |
| Find cancer genomics studies | `dde cbioportal search` | (genomics) |

## 2. Preconditions

- **Query input**: a PubMed search string. Supports PubMed syntax:
  keywords, MeSH terms, Boolean operators (AND, OR, NOT), and field
  tags ([Title], [Author], etc.). `dde pubmed search --help` for
  details.
- **No authentication** needed — NCBI E-utilities are public.
- **Rate limiting**: NCBI allows 3 requests/sec without an API key.
  The CLI paces requests.
- **Result limit**: `--max-results` controls retrieval (1-100, default
  20). PubMed reports `total_found` even when only a subset is
  retrieved.
- **Non-exhaustive by design**: a keyword search cannot find papers
  that use different terminology, are indexed under different MeSH
  headings, or are not yet indexed. The `search_not_exhaustive` relay
  carries this caveat.
- Run `dde doctor` before first use.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What has been published on this topic? | `dde pubmed search <QUERY> [--max-results N] [--sort relevance\|date]` | `raw/literature/<slug>.esearch.json`<br>`raw/literature/<slug>.efetch.xml`<br>`raw/literature/<slug>.pubmed-search.json`<br>`raw/literature/<slug>.meta.json` |
| What does the result set look like? | `dde pubmed analyze <QUERY>` | `raw/literature/<slug>.pubmed-search.analysis.json` |
| Fetch full text of a PMC article | `dde pubmed fulltext <PMCID>` | `raw/literature/<pmcid>.fulltext.json` |
| Find preprints on a topic (arXiv) | `dde preprint search --source arxiv <QUERY>` | `raw/literature/<slug>.preprint-search.json` |
| Find bioRxiv preprints on a topic | `dde preprint search --source biorxiv <QUERY>` | `raw/literature/<slug>.preprint-search.json` |

Run `search` before `analyze`. `analyze` reads from disk and produces
summary statistics without network access.

### When to prefer preprint over pubmed

Use `dde preprint search --source arxiv` when looking for recent,
not-yet-peer-reviewed work — preprints appear on arXiv days after
submission, whereas PubMed indexes peer-reviewed publications which
may lag months behind. Use `dde preprint search --source biorxiv`
when looking for recent biology and life-sciences preprints — bioRxiv
covers wet-lab biology, genomics, neuroscience, and related fields
that arXiv does not. Use `dde pubmed search` when looking for
peer-reviewed literature with MeSH indexing, journal provenance, and
the quality signal that peer review provides.

The `<slug>` is derived from the query string (filesystem-safe,
lowercase, max 80 characters). The `.pubmed-search.json` is the
structured artifact with parsed article records (PMID, title, authors,
journal, year, abstract, DOI, MeSH terms). The `.efetch.xml` is the
verbatim PubMed XML response.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.

## 4. Interpretation contract

### Verdicts

`analyze` produces:

- **results_found** — the query matched one or more PubMed records.
  The analysis reports `total_found` (PubMed's count), `n_retrieved`
  (the subset fetched), year distribution, top journals, and top MeSH
  terms.
- **no_results** — the query matched nothing in PubMed.

### What the analysis reports

The analysis provides summary statistics, not scientific conclusions:

- **Year distribution**: publication counts per year. Useful for
  assessing research recency and trajectory — a gene with 50
  publications in 2020-2024 and 2 before 2015 has a different evidence
  landscape than one with steady output since the 1990s.
- **Top journals**: most frequent journals in the result set. Indicates
  the research field (oncology, neuroscience, etc.) and the level of
  the venues.
- **Top MeSH terms**: most frequent Medical Subject Headings. These are
  NLM's controlled vocabulary — useful for refining subsequent searches
  or identifying the dominant research themes.

### The retrieval-vs-total gap

`total_found` may be much larger than `n_retrieved` (capped by
`--max-results`). The summary statistics describe the retrieved subset,
not the full result set. State this: "of N total PubMed results, M were
retrieved and summarised."

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `pubmed.search_not_exhaustive` | Qualifier | Results were found (conditional) | State that the search is a keyword-based sample, not a comprehensive literature survey. Relevant publications may use different terminology, be indexed under different MeSH headings, or not yet be indexed. Do not present search results as a complete survey of the topic. |

Check `mandatory_relays` in the `.pubmed-search.analysis.json`. Every
relay code present must be satisfied in the finding.

### Consequence rules

- **Results found**: report the total count, the number retrieved, the
  year range, and the top journals and MeSH terms. Carry the
  `search_not_exhaustive` qualifier. The result set is a sample, not
  a census.
- **No results**: report as "no PubMed results for this query." This
  may indicate an overly specific query, unusual terminology, or a
  genuinely unpublished topic. Suggest query refinement if appropriate.
- **High total with low retrieval**: when `total_found` far exceeds
  `n_retrieved`, the summary statistics describe a small sample. State
  the gap; do not generalise from a 20-article sample of 5000 results.

### Cross-disciplinary consequences

- Publication volume and recency inform the evidence landscape for a
  target but do not constitute evidence for or against the target
  itself. A well-published gene is not necessarily a good target; an
  unpublished one is not necessarily a bad one.
- MeSH terms from the result set can identify disease areas and
  biological processes associated with the target, useful for
  indication selection.
- Individual articles in the result set may warrant citation
  verification (use `citation-resolution`) before being cited in a
  finding.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.pubmed-search.analysis.json`, satisfies the
`search_not_exhaustive` relay, reports the retrieval-vs-total gap, and
does not present keyword results as a comprehensive survey. Everything
the tools emitted stays under `raw/literature/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Presenting search results as a complete literature survey.** A
  PubMed keyword search is inherently non-exhaustive. The
  `search_not_exhaustive` relay exists because this is the first
  qualifier lost when results are summarised. "The literature shows X"
  is unsupported by a keyword search; "N PubMed results for query Q
  include..." is supported.
- **Generalising from a small retrieval of a large result set.** If
  `total_found` is 5000 and `n_retrieved` is 20, the year distribution
  and journal distribution describe 20 papers, not 5000. State the
  sample size.
- **Citing individual papers from the result set without verification.**
  The search returns metadata (title, authors, abstract). It does not
  verify that each paper says what the title suggests, or that the DOI
  resolves. Use `citation-resolution` before citing a specific paper
  from the results.
- **Treating zero results as evidence of absence.** Zero results may
  reflect an overly specific query, non-standard terminology, or
  indexing lag. It does not prove nothing has been published on the
  topic.
- **Counting results as evidence strength.** "50 papers mention gene X
  and disease Y" is a search statistic, not evidence that gene X causes
  disease Y. Publication count measures research interest, not
  scientific validity.
- **Confusing the query slug with the query.** The filesystem slug is a
  truncated, sanitised version of the query string. When citing the
  search, cite the original query from the artifact's `query.terms`
  field, not the slug.

---

## 6. PubMed via BigQuery (`dde pubmed-bq`)

In addition to the NCBI E-utilities path (`dde pubmed`), this skill
covers `dde pubmed-bq`, which queries the PubMed dataset hosted on
Google BigQuery. BigQuery enables SQL-based filtering with larger
result sets, date range filters, and journal filters.

### When to prefer `pubmed-bq` over `pubmed`

- When you need more than 100 results (BigQuery supports up to 1000).
- When you need date-range or journal-level filtering in the query
  itself, rather than post-hoc filtering of a small result set.
- When the query benefits from SQL substring matching across titles
  and abstracts simultaneously.

### Preconditions

- **Authentication**: requires Google Cloud Application Default
  Credentials (`gcloud auth application-default login` or
  `GOOGLE_APPLICATION_CREDENTIALS`).
- **Dataset**: defaults to `bigquery-public-data.nih_nlm.pubmed`.
  Override with `--bq-dataset` or `$DDE_PUBMED_BQ_DATASET`.
- **Dependency**: `google-cloud-bigquery>=3.0` must be installed.

### Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Search PubMed via BigQuery | `dde pubmed-bq search <QUERY> [--max-results N] [--year-from Y] [--year-to Y] [--journal J] [--bq-dataset D]` | `raw/literature/<slug>.pubmed-bq.json`<br>`raw/literature/<slug>.pubmed-bq.meta.json` |
| Summarise BigQuery results | `dde pubmed-bq analyze <QUERY>` | `raw/literature/<slug>.pubmed-bq.analysis.json` |

Run `search` before `analyze`. `analyze` reads from disk and produces
summary statistics (year distribution, journal distribution, abstract
keyword frequency) without network access.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `pubmed_bq.search_not_exhaustive` | Qualifier | Results were found (conditional) | State that the search uses SQL substring matching, which may miss publications using different terminology, alternate spellings, or synonyms. Do not present results as a complete literature survey. |

### Interpretation

The same interpretation contract from `dde pubmed` (section 4) applies:
results are a keyword-based sample, not a comprehensive survey. The
`search_not_exhaustive` relay must be satisfied. The retrieval-vs-total
gap applies here as well — `--max-results` caps the retrieval.

---

## 7. Related genomics commands

For cancer genomics data, use `dde cbioportal search` and
`dde cbioportal analyze`. cBioPortal aggregates genomic data from
large-scale cancer studies (TCGA, AACR GENIE, institutional cohorts).
Artifacts land under `raw/expression/`.

| Question | Run | Writes to |
|---|---|---|
| What cancer genomics studies match a query? | `dde cbioportal search <QUERY> [--cancer-type T] [--study S] [--max-results N]` | `raw/expression/<slug>.cbioportal-search.json`<br>`raw/expression/<slug>.cbioportal.meta.json` |
| What do the cBioPortal results show? | `dde cbioportal analyze <ARTIFACT>` | `raw/expression/<slug>.cbioportal-search.analysis.json` |

cBioPortal is a cancer genomics resource, not a literature database.
Results are study-level metadata (study ID, name, cancer type, sample
count), not mutation data or expression profiles.
