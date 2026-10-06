---
name: binding-mode-analysis
description: >
  Score docking poses and classify predicted binding strength —
  receptor preparation, Vina execution, threshold-based pose
  classification, and residue-contact verification. Use when scoring
  how well a ligand fits a pocket, confirming a proposed binding mode,
  classifying poses by predicted binding strength, preparing a receptor
  for docking, or confirming whether a docked pose contacts specific
  named residues. Do not use for pocket detection or druggability (use
  pocket-druggability), compound properties or structural alerts (use
  compound-property-profile), genetic constraint (use
  target-genetic-evidence), or expression evidence (use
  tissue-expression-profile).
---

## 1. When to use, and when not

Use this skill when you need to score docking poses or classify
predicted binding strength. Entry points include:

- Scoring how well a ligand fits a pocket — virtual screening result
  interpretation.
- Confirming a proposed binding mode — pose validation against a
  prepared receptor.
- Classifying poses by predicted binding strength — triage across
  one or more ligands.
- Preparing a receptor for docking — PDBQT conversion and grid box
  derivation from a pocket record.
- Confirming whether a docked pose contacts specific named residues
  (catalytic, interface, or other functionally important residues).

**Do not use when:**

- You need pocket detection or druggability assessment
  -> `pocket-druggability`.
- You need compound properties, drug-likeness, or structural alerts
  -> `compound-property-profile`.
- You need genetic constraint or loss-of-function intolerance
  -> `target-genetic-evidence`.
- You need fold confidence or domain boundaries
  -> `protein-structure-confidence`.
- You need tissue expression evidence
  -> `tissue-expression-profile`.

## 2. Preconditions

- **Structure file + pocket record** (for `prepare`): a PDB or mmCIF
  coordinate file and a `.pockets.json` from `dde pocket run`.
- **Receptor PDBQT + gridbox JSON + ligand(s)** (for `run`): outputs
  from `prepare`, plus one or more ligand files (SDF, MOL, or
  PDBQT). SDF/MOL ligands are converted to PDBQT automatically.
- **Docking result JSON** (for `analyze`): the
  `.docking_result.json` written by `run`.
- **Poses PDBQT + receptor PDBQT** (for `contacts`): the
  `.poses.pdbqt` from `run` and the `.receptor.pdbqt` from
  `prepare`. No additional dependencies beyond what `prepare` and
  `run` already require.
- **Dependencies**: Meeko (`mk_prepare_receptor.py`,
  `mk_prepare_ligand.py`) and the AutoDock Vina binary. Run
  `dde doctor` before first use.
- **No authentication** needed — all operations are offline.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Prepare a receptor for docking | `dde docking prepare <STRUCTURE> <POCKET_RECORD>` | `raw/docking/<stem>.receptor.pdbqt`<br>`raw/docking/<stem>.gridbox.json`<br>`raw/docking/<stem>.prepare.meta.json` |
| Score a ligand against a receptor | `dde docking run <RECEPTOR> <GRIDBOX> <LIGAND...>` | `raw/docking/<ligand_stem>.docking_result.json`<br>`raw/docking/<ligand_stem>.poses.pdbqt`<br>`raw/docking/<ligand_stem>.docking.meta.json` |
| Classify poses by binding strength | `dde docking analyze <DOCKING_RESULT>` | `raw/docking/<stem>.docking.analysis.json` |
| Does a docked pose contact a named residue? | `dde docking contacts <POSES_PDBQT> <RECEPTOR_PDBQT> [--cutoff N]` | `raw/docking/<stem>.contacts.json`<br>`raw/docking/<stem>.contacts.meta.json` |

Three-phase workflow: run `prepare` before `run`, and `run` before
`analyze`. `prepare` and `run` produce Layer 0 artifacts; `analyze`
reads from disk and applies thresholds. `contacts` reads the poses
and receptor PDBQTs produced by `run` and `prepare` respectively;
it can run any time after `run` completes, independently of
`analyze`.

When phase-1 output lives in a non-default directory, pass `--from`
to `analyze` so it reads sidecars from that location. `--out`
overrides the output directory independently.

Notable options:

- `--pocket-rank N` on `prepare`: which pocket to dock into
  (default: rank 1, the top-scoring pocket).
- `--exhaustiveness N` on `run`: Vina sampling thoroughness
  (default: 8).
- `--n-poses N` on `run`: number of poses to generate (default: 9).
- `--cutoff N` on `contacts`: distance cutoff in Angstroms for
  contact definition (default: 4.0).
- `--min-distance N` on `contacts`: contacts below this distance
  are flagged as potential steric clashes (default: 2.0).

## 4. Interpretation contract

### Verdicts

`analyze` applies the `docking-scores` threshold set and emits one
of:

- **strong-binders-found** — at least one pose in the strong
  predicted binding band (at or below the configured
  `strong_binding_energy` threshold).
- **moderate-binders-found** — best pose in the moderate predicted
  binding band (at or below `moderate_binding_energy`, above
  `strong_binding_energy`).
- **no-significant-binding** — all poses score above the
  `moderate_binding_energy` threshold.

The `weak_binding_energy` threshold is UNRESOLVED in the
`docking-scores` threshold set. Poses above the moderate threshold
are classified as "unclassified" — the tool declines to classify
them as weak or no-binding. Do not invent a weak binding threshold;
the source has not published one.

### What the scores mean

Vina scores are computed interaction energies in kcal/mol (more
negative = stronger predicted binding). They are not measured
binding affinities — they do not correspond to Kd, Ki, or IC50
values. The `docking.score_is_not_affinity` relay enforces this
distinction.

Thresholds are cited by name. The values in force are in the
artifact: every `.analysis.json` carries `threshold_set`,
`thresholds_applied`, `threshold_sources`, and
`threshold_provenance`. Quote values from the analysis being cited,
never from prose.

### Druggability-vs-affinity boundary

This skill occupies the **affinity side** of the boundary. The
`pocket-druggability` skill fires
`fpocket.druggability_is_not_affinity` specifically because the
binding affinity question was untooled — the stop says "if the
question is about binding or potency, the answer is that the
question is untooled." With this skill, the binding prediction
question is tooled via docking. The docking score answers the
binding prediction question, scoped by its own relay
(`docking.score_is_not_affinity`) as a computed interaction energy,
not a measured affinity.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `docking.score_is_not_affinity` | Stop | Any pose bands as strong or moderate — the over-readable result is available (claim-triggered, conditional) | Report the score as a computed interaction energy. Do not carry it as a measured binding affinity (Kd, Ki, IC50). If the question is about measured affinity, the answer is that the measurement has not been done — not that the docking score is strong. |
| `docking.contact_is_not_binding_event` | Qualifier (standing) | Every `docking contacts` run | State that the reported contacts represent geometric proximity within a static, computationally docked pose — not a confirmed binding interaction. No hydrogen-bond geometry, electrostatic complementarity, or reactive-orientation check was performed. |

**Upstream relays.** `analyze` collects upstream relays from the
`prepare` and `run` phase-1 sidecars. These originate in the
pocket-druggability pipeline. Any upstream relay present in the
analysis record must also be satisfied:

| Relay code | Kind | Origin | Obligation |
|---|---|---|---|
| `fpocket.single_conformation` | Qualifier | pocket-druggability | The pocket was scored on a single conformation. Carry the conformation qualifier into any binding finding. |
| `fpocket.conformation_dependent` | Qualifier | pocket-druggability | The structure is not established as experimental. State which conformation was docked and scope any finding accordingly. |
| `fpocket.druggability_is_not_affinity` | Stop | pocket-druggability | Report the pocket score as cavity geometry, not as binding evidence. With docking now tooling the affinity question, the binding prediction is answered by docking scores under `docking.score_is_not_affinity`, not by the druggability score. |

Check `mandatory_relays` in the `.docking.analysis.json` and in
each phase-1 `.meta.json`. Every relay code present must be
satisfied in the finding.

`contacts` emits `docking.contact_is_not_binding_event` as its
own relay, in addition to propagating upstream relays from the
poses and receptor sidecars (originating in receptor preparation
and pocket-druggability). Any upstream relay present in
`.contacts.meta.json` must be satisfied in the finding.

### Residue contacts

`contacts` computes per-pose ligand-atom-to-receptor-residue
contact distances within a distance cutoff. For each pose in the
multi-model PDBQT, it reports every receptor residue that has at
least one atom within the cutoff distance of any ligand atom, with
the minimum distance recorded.

A sub-cutoff contact distance confirms geometric proximity in one
static computed pose. It does **not** confirm a binding interaction
— no hydrogen-bond geometry, electrostatics, or reactive-orientation
check is performed.

Contacts below the `--min-distance` threshold (default 2.0 Å) are
flagged as potential steric clashes — artifacts of rigid-receptor
docking where the receptor cannot relax around the ligand.

**Contacts vs. fpocket residue membership.** These are distinct
questions. fpocket identifies which residues define the pocket
(cavity geometry, no ligand present). `contacts` identifies which
residues the ligand actually approaches within the cutoff distance
in a docked pose. A residue can define a pocket without being
contacted by a given ligand, and a ligand can contact residues
outside the pocket definition.

**Use case.** Confirming or denying that a docked pose contacts a
specific named residue — catalytic triad residues, interface
residues, or other functionally important positions. This question
is answerable deterministically from the poses and receptor PDBQTs
already on disk; it does not require rendering or visualization.

### Consequence rules

- **Strong-binders-found**: state the best score (quoted from the
  analysis), the threshold set, and the ligand. Proceed scoped as
  computed interaction energy per `docking.score_is_not_affinity`.
- **Moderate-binders-found**: state the best moderate score and the
  threshold set. Moderate predicted binding; does not reach the
  strong band.
- **No-significant-binding**: all poses above the moderate threshold.
  When `weak_binding_energy` is UNRESOLVED, poses in this range are
  unclassified — do not report "no binding" when the tool reported
  "unclassified."

### Cross-disciplinary consequences

- A docking score predicts binding in one conformation with one grid
  box. A different conformation or grid box is a different experiment.
- Docking scores complement but do not replace measured binding
  affinities — a strong computed score is a prediction, not a
  measurement.
- Larger ligands can accumulate more van der Waals contacts and score
  more favourably without tighter binding. Comparing scores across
  ligands of substantially different sizes is misleading.
- Local structure confidence below the trusted threshold (from
  protein-structure-confidence) makes docking scores unreliable in
  that region.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/`
that cites the `.docking.analysis.json`, satisfies every mandatory
relay that fired, names the ligand and receptor in every verdict,
and never converts a Vina score into a measured affinity. Everything
the tools emitted stays under `raw/docking/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the
tool emitted. If the tool did not run, there is no value. If a
required tool is unavailable, report the task as blocked. Do not
estimate, and do not proceed on an assumed result.

### Named pathologies

- **Score/heavy-atom-count mismatch.** Larger ligands accumulate
  more van der Waals contacts and score more favourably without
  tighter binding. Comparing scores across ligands of different
  sizes is misleading.
- **Poses in disordered regions.** Docking into a region where
  structure confidence is low produces poses that reflect modelling
  error, not binding.
- **Druggability score as binding evidence.** The
  `fpocket.druggability_is_not_affinity` relay fires for this; the
  docking score is the binding prediction, not the druggability
  score.
- **Treating the Vina score as Kd or IC50.** The
  `docking.score_is_not_affinity` relay exists for this. A Vina
  score is a computed interaction energy, not a measured affinity.
- **Comparing scores across receptor conformations.** Different
  conformations produce different grid boxes. Scores from different
  grid boxes are not directly comparable.
- **Exhaustiveness too low.** Vina samples stochastically. The
  default exhaustiveness may miss the correct pose for complex
  binding modes.
- **Computing docking scores yourself instead of using the tool.**
  The tool pins Vina version, Meeko version, and grid box
  derivation. The finding must cite the tool's output, not a
  recalculation.
- **UNRESOLVED `weak_binding_energy`.** Treating poses above the
  moderate threshold as "no binding" when the threshold has not been
  established. The tool reports these as "unclassified" — do not
  invent a classification the source declined to publish.
- **Residue-contact verification via rendering.** Concluding that
  confirming whether a docked pose contacts a named residue requires
  rendering or visualization. `docking contacts` answers this
  question deterministically from the poses and receptor PDBQTs on
  disk — no visual inspection needed.
