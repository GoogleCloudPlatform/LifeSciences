---
name: pocket-druggability
description: >
  Detect ligand-binding pockets in a protein structure and assess
  druggability — determine whether the structure contains a tractable
  binding site and, optionally, whether a pocket lines a specific
  interface. Use when evaluating whether a target has a druggable cavity,
  when checking a specific protein–protein interface for pockets, or when
  the fpocket output tree is needed for downstream docking. Do not use
  for fold confidence or domain boundaries (use
  protein-structure-confidence), genetic constraint (use
  target-genetic-evidence), or expression evidence (use
  tissue-expression-profile).
---

## 1. When to use, and when not

Use this skill when you need to detect and score ligand-binding pockets
in a protein structure. Entry points include:

- Determining whether a target structure has a druggable binding site —
  the first tractability question in a structural campaign.
- Asking whether a pocket lines a specific set of residues (a
  protein–protein interface, an allosteric site) — the `--near` query.
- Obtaining fpocket's output tree (per-pocket coordinate files) for
  downstream docking without re-running a tool whose numbers move.
- Comparing druggability across conformations of one target — the score
  is conformation-dependent and a single structure cannot settle the
  question.

**Do not use when:**

- You need fold confidence, domain boundaries, or disorder prediction
  -> `protein-structure-confidence`.
- You need genetic constraint or loss-of-function intolerance
  -> `target-genetic-evidence`.
- You need tissue expression evidence
  -> `tissue-expression-profile`.
- You need to evaluate a tournament ranking
  -> `tournament-corpus`.
- You need to verify a citation
  -> `citation-resolution`.

## 2. Preconditions

- **Structure file** (for `run`): a coordinate file fpocket reads —
  `.pdb`, `.ent`, `.cif`, or `.mmcif`. Passing a sidecar, analysis
  record, or non-structure file is refused with a remedy message.
- **Pockets record** (for `analyze`): the `.pockets.json` written by
  `run`. Do not pass the raw structure to `analyze`.
- **fpocket on PATH**: `run` requires the fpocket binary. If missing,
  the tool fails with a `DependencyError` and a remedy pointing to
  `tools/install.sh`. Run `dde doctor` before first use. It ends
  with a verdict line: `STOP` means fix or report before running
  anything; `PROCEED` means work, and the grouped warnings tell you
  which commands would refuse, which results need careful reading, and
  which are the tooling lead's to clear. Do not judge by the warning
  count; the verdict line grades them for you.
- **No authentication** needed — fpocket is a local binary.
- **No network** — both `run` and `analyze` are offline.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What pockets does this structure have? | `dde pocket run <STRUCTURE>` | `raw/structures/<stem>.pockets.json`<br>`raw/structures/<stem>.pockets.meta.json`<br>`raw/structures/<stem>_fpocket/` (full fpocket tree) |
| Is this structure druggable? | `dde pocket analyze <POCKETS_RECORD>` | `raw/structures/<stem>.pocket.analysis.json` |
| Is there a pocket at this interface? | `dde pocket analyze <POCKETS_RECORD> --near A:145,A:146,B:12` | `raw/structures/<stem>.pocket.analysis.json` |

Run `run` before `analyze`. `analyze` reads from disk and applies the
`pocket` threshold set. It can be re-run with different thresholds
(`--druggable`) without re-running fpocket.

`run` copies the input to a temporary directory, runs fpocket there, and
writes three things: the parsed per-pocket record (`.pockets.json`),
the provenance sidecar (`.pockets.meta.json`), and the complete fpocket
output tree (`<stem>_fpocket/`). The tree holds the per-pocket
coordinate files a docking run needs. Re-deriving them later would mean
re-running a tool whose numbers move between runs, so they are kept
whole.

`analyze` with `--near` answers a different question from `analyze`
alone. Without `--near`, it reports the best pocket anywhere in the
structure. With `--near`, it reports whether any pocket lines the
specified residues — and if so, that pocket's score, not the global
best. The `--near` selector requires chain identifiers (`A:145`, not
just `145`) because residue numbering repeats across chains.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory. When a work
order calls for output under a different directory, pass `--out <DIR>` —
this redirects all output paths shown in the table above to land under the
specified directory instead of the defaults.

## 4. Interpretation contract

### Verdicts

`analyze` applies the `pocket` threshold set and emits one of eight
verdicts, four for whole-structure queries and four for site-specific
queries:

**Without `--near` (whole structure):**

- **druggable-pocket-present** — the best pocket scores at or above
  the configured `druggable_dscore` threshold.
- **borderline** — the best pocket scores between the configured
  `borderline_dscore` and `druggable_dscore` thresholds.
- **no-druggable-pocket-in-this-conformation** — the best pocket
  scores below `borderline_dscore`. Named for what was measured, not
  for what it tempts a reader to conclude.
- **no-pockets-detected** — fpocket found no cavities at all.

**With `--near` (site-specific):**

- **site-druggable** — a pocket lining the requested residues scores
  at or above `druggable_dscore`.
- **site-borderline** — a pocket lines the site but scores between
  `borderline_dscore` and `druggable_dscore`.
- **site-not-druggable-in-this-conformation** — a pocket lines the
  site but scores below `borderline_dscore`.
- **no-pocket-at-site-in-this-conformation** — no detected pocket
  includes any of the requested residues. fpocket finds cavities, so
  this is evidence of no cavity at that site in this conformation —
  not of an undruggable protein. The `fpocket.single_conformation`
  relay fires: absence at the site is the most negative answer the
  site question has.

### Pocket rank vs druggability score

fpocket assigns each detected pocket a rank by its own internal scoring
function, which weights pocket geometry and physico-chemical properties
(volume, hydrophobicity, polarity, alpha-sphere density). The
druggability score (`drug_score`) is a separate continuous metric
produced by a different model — a logistic regression trained on a
set of drugged and non-drugged pockets. The two orderings are
independent: the pocket with the highest drug score is routinely NOT
rank 1. A pocket at rank 28 of 36 can carry the top drug score
because fpocket's internal ranking emphasises different features from
the druggability model.

When reporting results, always state both the pocket rank and the drug
score (e.g. "pocket 28 of 36, drug_score 0.87"). Do not assume rank 1
is the druggable pocket, and do not discard high-rank-number pockets
before checking their drug scores. The `analyze` subcommand already
selects by drug score, not by rank — but raw fpocket output ordered by
rank can mislead a reader who scans only the first entry.

### The conformation problem

This is the core interpretation challenge. The drug score is
conformation-dependent, and by more than it looks. Measured on three
crystal structures of the CDK2 ATP site — a site with drugs on the
market:

| PDB | Drug score |
|---|---|
| 1HCK (ATP) | 0.939 |
| 2W1D (inhibitor) | 0.293 |
| 1AQ1 (staurosporine) | 0.172 |

One site, three structures, a 0.77 spread across the 0.5 decision
boundary. Two of those three would be reported as "borderline" or "not
druggable" for a site that is drugged in the clinic.

A score below the cutoff is a fact about the coordinates it was
computed on and nothing more. The verdict string says
`…-in-this-conformation` and the `fpocket.single_conformation` relay
quotes the three CDK2 numbers — both survive into the artifact, and
neither survives a careless summary. Every finding that reports a
sub-cutoff score must carry the qualifier through to the Layer 1 prose,
not merely acknowledge it in a caveat section.

### Volume is a Monte Carlo estimate

fpocket computes pocket volume by Monte Carlo integration seeded from
`time(NULL)` — one-second resolution, no seed flag. Repeated runs on
one input differ by a few percent. Two runs inside the same second
return identical numbers because the seed has not changed. Volumes are
reported with the configured `volume_estimate_tolerance` attached, and
no verdict is keyed on volume alone. A volume difference below the
tolerance between two runs is noise, not change.

### Experimental vs predicted structures

`run` reads the structure file for `EXPDTA` (PDB) or `_exptl.method`
(mmCIF) to establish whether the coordinates are experimental. When the
structure is not established as experimental, the sidecar records this
and the `fpocket.conformation_dependent` relay fires. On a
non-experimental structure the score constrains the **model in both
directions**: a low score is not evidence against druggability, and a
high score is not evidence for it. The druggability model was trained
on crystal structures; any score on a predicted conformation is a
statement about the model's geometry, not about whether the site can
be drugged.

### Mandatory relays

These relays fall into two kinds, and the distinction matters more
than the individual codes:

- **Qualifier relays** carry a caveat into the finding. Satisfying
  them means stating the qualifier — the conformation, the model
  provenance — alongside the score. The finding still reports the
  result; it reports it scoped.
- **Stop relays** block a substitution. Satisfying them does not mean
  adding a disclaimer — it means not using the score for a purpose it
  cannot serve. A stop relay with a caveat attached is the failure it
  exists to prevent, because the caveat makes the substitution look
  diligent.

This axis — **Kind** — answers what the reader must do. It is distinct
from the **defect-versus-claim** axis in tool-design-guidance §5,
which answers what fires the relay (a fault in the data, or the answer
itself having a well-known wrong reading). The two are orthogonal: a
relay can be claim-triggered and a qualifier, or defect-triggered and
a stop. Do not conflate them.

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `fpocket.single_conformation` | Qualifier | The reported score bands as borderline or not-druggable, or no pocket lines the requested site (conditional) | Write the negative as "no druggable pocket in this conformation of <structure>", naming the structure. One structure cannot support "this site cannot be drugged": three CDK2 crystal structures of the same ATP site score 0.17, 0.29 and 0.94. A score under the cutoff is a reason to score another conformation, not a reason to drop a target. |
| `fpocket.conformation_dependent` | Qualifier | The structure is not established as experimental (conditional) | Say which conformation was scored, and do not convert a low score into a claim about the target. The druggability model was trained on crystal structures; on a predicted or modelled structure a low score is a statement about the model, not about whether the site can be drugged. |
| `fpocket.druggability_is_not_affinity` | Stop | The reported score bands as druggable (conditional) | Report the site as having drug-like cavity geometry. Do not report it as evidence that a compound will bind or how tightly. The score describes the shape, volume and hydrophobicity of a cavity with no ligand in it; affinity is a property of a compound–site pair. If the question being answered is about binding or potency, the answer is that the question is untooled — not that the pocket scored 0.94. |

Check `mandatory_relays` in both the `.pocket.analysis.json` and the
`.pockets.meta.json`. Every relay code present must be satisfied in the
finding.

### Detection floor

When the best pocket sits at fpocket's detection floor (alpha sphere
count at or below the configured `min_alpha_spheres` threshold), the
analysis carries an advisory. Treat such a pocket as a candidate to
inspect, not as a characterised site.

### Bundle-void advisory

Multi-helix transmembrane proteins — GPCRs especially — produce a
central cavity where the transmembrane helices surround an internal
void. fpocket correctly identifies this as a pocket and may score it
highly (drug score near 1.0), but it is a structural feature of the
fold, not a discrete drug-binding cavity.

**Recognition pattern.** The following three indicators together
suggest a bundle void rather than a binding pocket:

- **High alpha-sphere count** — 200+ alpha spheres, vs. a typical
  20–80 for drug-binding pockets.
- **Large volume** — 1500+ cubic Ångström, vs. a typical 200–800 for
  drug-binding pockets.
- **Large CoM-to-max-sphere distance** — 20+ Ångström
  (`centre_of_mass_max_sphere_distance`), vs. a typical 8–15 for
  compact cavities.

No single indicator is conclusive — a large binding pocket can be
voluminous, and a multi-site groove can have many alpha spheres. When
all three indicators are present AND the target is a multi-helix
transmembrane protein, flag the pocket as a potential bundle-void
artifact in the finding.

A high drug score on a bundle void does not mean the target is
druggable at that site. The scoring function responds to the enclosed
hydrophobic environment, which is a property of the fold rather than a
binding site. This is interpretive guidance, not a mandatory relay —
the tool emits correct numbers; the skill's job is to help the reader
recognise when those numbers describe a fold feature rather than a drug
target.

### Forming `--near` queries

`--near` queries require the user to supply residue numbers that define
the region of interest. The tool validates that the residues exist in
the structure, not that they are the right residues for the biological
question — a wrong selection produces a confident, well-provenanced
answer about the wrong pocket (see Named pathologies below).

Best practices for selecting residues:

- **Well-characterised target families** — for families with known
  binding-site architecture (e.g. GPCRs: TM3/TM6/TM7 for orthosteric
  sites), the relevant residues can be derived from family-specific
  databases such as UniProt topology annotations or GPCRdb.
- **Protein–protein interaction interfaces** — interface residues can
  be derived from complex structures (PDB entries or AlphaFold 3
  predictions of the complex).
- **Unknown target family or binding site** — when the site is not
  characterised, `--near` is less useful. Use the unfiltered pocket
  list and rank by drug score instead.

Automating interface-residue derivation from complex structures is a
tooling gap under consideration (see issue #94).

### Consequence rules

- **Druggable pocket present**: state the pocket rank, the drug score,
  and the structure it was measured on. Proceed to downstream work —
  the score supports tractability in this conformation.
- **Borderline**: state the score and the conformation. The
  `fpocket.single_conformation` relay fires — carry the qualifier.
  A borderline score in one conformation is a reason to score another,
  not a conclusion about the target.
- **Not druggable in this conformation**: the qualifier is the finding.
  The `fpocket.single_conformation` relay fires with the CDK2
  calibration numbers. Do not write "the target is not druggable" —
  write "no druggable pocket was detected in this conformation of
  <structure>" and name the structure.
- **No pockets detected**: fpocket found no cavities. This is unusual
  and may indicate a problem with the input coordinates rather than
  with the target.
- **Site-specific verdicts**: when `--near` was used, the verdict
  describes the pocket at the requested residues, not the best pocket
  in the structure. The analysis reports both. A protein with a
  druggable pocket elsewhere but no pocket at the interface still
  scores `no-pocket-at-site-in-this-conformation` for the interface
  query, and the relay fires.
- **Non-experimental structure**: when `fpocket.conformation_dependent`
  fires, the score constrains the model in both directions. A low
  score is not evidence against druggability, and a high score is not
  evidence for it. State this without hedging — any score on a
  predicted structure is about the model's geometry, not about the
  target.

### Cross-disciplinary consequences

- A druggable-pocket-present verdict makes the fpocket output tree
  available for docking — the per-pocket coordinate files under
  `<stem>_fpocket/` are the direct input.
- A negative verdict on one conformation should prompt scoring
  additional conformations (other crystals, AlphaFold models, MD
  snapshots) before concluding on tractability.
- fpocket scores complement but do not replace experimental binding
  data — a high score is a geometric prediction, not evidence of
  binding.
- The `centre_of_mass_max_sphere_distance` metric in the best-pocket
  record indicates diffuseness. Large values mean alpha spheres are
  spread over what may be several merged surface grooves rather than
  one cavity — the shape of pocket that scores low without the site
  being poor.

### CoM-to-max-sphere distance: reference values

The `centre_of_mass_max_sphere_distance` (the distance from the
pocket's centre of mass to its most distant alpha sphere) provides a
sense of pocket compactness, but the number is uninformative without
a scale. The following reference ranges are drawn from well-known
drug-bound pockets and are provided for calibration, not as
thresholds:

| Site class | Typical range | Examples |
|---|---|---|
| Compact, enclosed drug-binding sites | ~8–12 Å | HIV-1 protease active site (e.g. PDB 1HHP, ~8–10 Å); CDK2 ATP site (e.g. PDB 1HCK, ~10–12 Å) |
| Medium-sized, partially solvent-exposed sites | ~12–18 Å | Larger kinase hinge pockets; nuclear receptor ligand-binding domains |
| Extended surface grooves / merged features | ~20+ Å | PPI interfaces scored as single pockets; elongated allosteric channels |

Values above ~20 Å suggest the alpha spheres span what may be
several merged surface grooves rather than a single discrete cavity.
Such pockets often score low on druggability because fpocket's model
was trained on enclosed, drug-like cavities — the low score reflects
pocket shape, not necessarily site quality. Conversely, a compact
pocket in the 8–12 Å range is geometrically consistent with the
kinds of sites the druggability model was calibrated on.

These ranges are approximate and structure-dependent — they provide
a sense of scale for the metric, not decision boundaries. As with
the CDK2 drug-score calibration, the goal is to give the reader an
interpretive anchor, not a verdict.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.pocket.analysis.json`, satisfies both mandatory relays when they
fire, names the structure in every verdict, and never converts a
sub-cutoff score into a target-level claim. Everything the tools
emitted stays under `raw/structures/`.

Thresholds are cited by name (e.g. "the configured `druggable_dscore`
threshold"), never by value. The values in force are in the artifact:
every `.analysis.json` carries `threshold_set` (name@version),
`thresholds_applied`, `threshold_sources` (default | program | flag),
and `threshold_provenance`. A specialist quoting a number should quote
it from the analysis they are citing.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Writing "the target is not druggable" from a sub-cutoff score.**
  A score below the cutoff is a fact about one set of coordinates.
  Three CDK2 crystal structures of the same ATP site — a site with
  drugs on the market — score 0.17, 0.29 and 0.94 on this scale.
  The `fpocket.single_conformation` relay exists because this is the
  first qualifier lost when a result is summarised by someone who
  wanted a yes.
- **Dropping the conformation qualifier from a negative verdict.**
  The verdict string says `in-this-conformation`. The relay quotes
  the CDK2 numbers. Both survive into the artifact. Neither survives
  a careless summary. A finding that reads "no druggable pocket" where
  the tool said "no druggable pocket in this conformation of 1AQ1" has
  made a different, unsupported claim.
- **Letting a druggability score stand in for an affinity.**
  The `fpocket.druggability_is_not_affinity` relay fires on the
  impressive score — the one most likely to be carried where a binding
  measurement belongs. The score describes cavity geometry with no
  ligand in it; affinity is a property of a compound–site pair. Adding
  a caveat ("this is not an affinity, but...") does not satisfy the
  relay — it produces a pocket score standing in for an affinity with
  a disclaimer attached, which reads as diligence. If the question is
  about binding, the answer is that the question is untooled.
- **Treating a predicted-structure score as target evidence.**
  When `fpocket.conformation_dependent` fires, the structure is not
  established as experimental. The druggability model was trained on
  crystal structures. The score constrains the model in both
  directions: a low score is not evidence against druggability, and a
  high score is not evidence for it. The high-score direction is the
  one most likely to be missed, because the guard was written imagining
  a disappointing result — but the reader holding a 0.94 on an
  AlphaFold model is the one who needs it.
- **Reading a volume difference as a change.** Volume is a Monte Carlo
  estimate with no seed control. Two runs on the same input differ by
  a few percent. A difference below `volume_estimate_tolerance` is
  noise, not a structural difference. Two runs inside the same second
  return identical numbers because the seed is `time(NULL)` at
  one-second resolution — perfect reproducibility in a tight loop is
  the trap, not the proof.
- **Comparing scores across different targets.** The drug score ranks
  conformations of one target or sites within one structure. It was not
  designed to rank targets against each other, and a 0.7 on target A
  and 0.6 on target B does not mean A is more druggable than B.
- **Ignoring the detection-floor advisory.** When the best pocket sits
  at fpocket's alpha-sphere minimum, the pocket was barely detected.
  Treating it as a characterised site overreads a marginal signal.
- **Assuming no-pocket-at-site-in-this-conformation means an undruggable
  interface.** fpocket finds cavities. Protein–protein interfaces are
  often flat — no cavity does not mean no binding opportunity, only that
  fpocket's geometry cannot detect one in this conformation.
- **Passing arbitrary residues to `--near` and trusting the answer.**
  The tool validates that the residues exist in the structure, not that
  they are the site you meant. A wrong or arbitrary residue selection
  produces a confident, well-provenanced answer about the wrong pocket.
  The specialist choosing the residues must justify the selection from
  structural or functional evidence — the tool cannot detect that
  A:145 is the active site and A:300 is a crystal contact.
- **Re-running fpocket for reproducibility and finding perfect agreement.**
  Consecutive runs inside the same second share a seed and return
  identical volumes. Wait at least one second between runs, or the
  reproducibility check proves only that `time(NULL)` is deterministic
  within a second.
