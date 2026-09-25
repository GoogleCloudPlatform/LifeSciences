# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Provenance sidecars and analysis records.

Every phase-1 artifact gets `<name>.meta.json` beside it; every phase-2
run emits `<name>.analysis.json` citing its source and the threshold set
applied. See docs/tool-design-guidance.md §5.

`warnings` on the sidecar is load-bearing: warnings that must reach the
report are captured here so a reviewer can confirm the specialist
relayed them.

Some warnings are stronger than that. A warning carrying a `relay` code
is a **mandatory relay** in the sense of skill-design-guidance §4.5: it
must reach the Layer 1 finding unchanged, because a report that omits it
is wrong rather than merely incomplete. Isoform substitution is the
model case — the numbers describe a different molecule than the one the
finding names.

These carry a stable code (`afdb.partial_coverage`) as well as prose.
The code is what makes the reviewer check mechanical: prose is rewritten
between builds and cannot be matched on, but a skill can name a code and
a reviewer can enumerate the codes in an artifact and confirm each was
addressed. Codes are registered in RELAY_CODES so the set is
discoverable rather than scattered across call sites.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import env, output
from .errors import ArtifactError, Refusal
from .paths import is_safe_to_open
from .toolchain import check_integrity


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: Subset of volatile fields that are interesting when they change on
#: an otherwise-agreeing record.  ``timestamp`` and ``written_by`` are
#: excluded — they change on every re-run and carry no diagnostic value.
#: ``cli_modified_note`` is excluded because ``cli_modified`` already
#: covers the condition.  ``work_order_id`` is excluded because cross-WO
#: changes have their own protection mechanism.
_INTERESTING_VOLATILE_FIELDS = (
    "env_version",
    "capability_state",
    "cli_integrity",
    "cli_modified",
)


#: Provenance fields — excluded from the overwrite comparison so an
#: idempotent re-run stays frictionless.  What matters is whether the
#: *verdict* would change, not whether the clock moved, the commit
#: advanced, or the interpreter was upgraded.
#:
#: Principle: fields that record *what tool environment produced this*
#: never cause a comparison mismatch.  Only fields that represent
#: scientific content (source data, thresholds, metrics, assessment)
#: trigger exit 9.
#:
#: `written_by` is here too, and that is only safe because of what
#: `_may_write` now does with the answer: an agreeing record is kept,
#: not rewritten, so a field excluded from the comparison can no longer
#: be lost by a write that the comparison called harmless. The exclusion
#: and the keep are one mechanism; either alone is a defect.
_VOLATILE_ANALYSIS_FIELDS = (
    "timestamp",
    "written_by",
    # cli_integrity is derived from `git describe --dirty --always`.
    # Without tags it is a bare commit SHA that changes on every commit
    # to DDE — a provenance stamp, not scientific content.
    "cli_integrity",
    # cli_modified / cli_modified_note record whether the toolchain had
    # uncommitted changes.  Conditional: absent when clean, present when
    # dirty.  Provenance, not science.
    "cli_modified",
    "cli_modified_note",
    # env_version records the Python / venv environment.  An upgrade
    # between runs is not a change in the analysis.
    "env_version",
    # capability_state records which optional backends were available.
    # A snapshot of deployment state, not of the verdict.
    "capability_state",
    # work_order_id is an attribution field — it records *who requested*
    # the analysis, not what the analysis found.  Same rationale as
    # written_by: a reviewer in WO-B re-running a specialist's WO-A
    # analysis that produces identical science should not trigger exit-9.
    # Cross-WO overwrite protection is independent: _check_cross_wo_overwrite
    # reads work_order_id straight off disk, never through _comparable().
    "work_order_id",
)

#: Fields that ``_comparable()`` normalises before comparison.  These
#: may differ in notation between old and new records without
#: representing a substantive change.  The guard test in
#: ``test_comparable_field_coverage.py`` enforces that every analysis
#: field is classified as volatile, normalised, or passthrough; adding
#: a field to ``write_analysis()`` without classifying it will fail CI.
_NORMALIZED_ANALYSIS_FIELDS = ("source", "record_type")

#: Set by the phase-2 wrapper when `--overwrite` was passed. A latch
#: rather than an argument for the same reason as the network ban: an
#: argument has to be threaded through every call site, and the call
#: sites are what the guard exists to constrain.
_overwrite_allowed = False
_overwrite_cross_wo_allowed = False


def allow_overwrite(allowed: bool) -> None:
    """Permit this invocation to replace a differing analysis record."""
    global _overwrite_allowed
    _overwrite_allowed = allowed


def allow_overwrite_cross_wo(allowed: bool) -> None:
    """Permit this invocation to overwrite an analysis from a different work order."""
    global _overwrite_cross_wo_allowed
    _overwrite_cross_wo_allowed = allowed


def _writer() -> str | None:
    """Which agent ran this. Identity, never a credential."""
    return os.environ.get("SCION_AGENT_SLUG") or None


def _work_order() -> str | None:
    """Which work order this artifact was produced under."""
    return os.environ.get("DDE_WORK_ORDER_ID") or None


# Registry of mandatory-relay codes. A skill's interpretation contract
# names these; `dde relays` prints them so skill authors and reviewers
# work from the same list instead of from prose that has since changed.
#
# Each entry is written as an **obligation on the finding**, not as a
# description of the condition. A relay is an instruction, not a caveat:
# `afdb.partial_coverage` does not mean "mention that coverage is
# partial", it means "scope the claim to the modelled region or withhold
# it". Phrased as a description, a skill author satisfies the relay by
# quoting it, and a 20%-covered model gets a finding that discloses the
# truncation and still calls the protein well ordered.
# Shared vocabulary that currently lives above the CLI layer: reading
# this dict by import drags in output, and output imports click. Anything
# outside the venv that wants the registered codes — a checker, a doc
# generator — therefore parses the literal out of this file instead
# (tools/check_threshold_names.py, which exits 2 rather than passing if
# the literal is not found here).
#
# The clean fix is to move this to a leaf module and re-export it. Not
# done, deliberately: there is one such consumer, it is unblocked, and
# the move would break its file assumption for no present gain. The
# trigger to do it is a *second* out-of-tree reader — at that point the
# parse-by-hand workaround stops being one file's quirk and becomes the
# interface.
RELAY_CODES: dict[str, str] = {
    "allen.brain_region_expression_only": (
        "Allen Brain Atlas expression data covers brain regions only. "
        "Expression in peripheral tissues (skin, DRG, etc.) is not "
        "represented. Do not infer absence of expression in non-brain "
        "tissues from this dataset."
    ),
    "afdb.partial_coverage": (
        "Scope every confidence claim to the modelled residue range, or "
        "withhold it. Do not describe the protein — describe the fragment."
    ),
    "afdb.isoform_substituted": (
        "Name the isoform actually analysed wherever the finding names the "
        "protein. Do not attribute these numbers to the canonical entry."
    ),
    "afdb.above_modelling_limit": (
        "State that no full-length prediction exists at any accession. Do "
        "not present the available fragments as the protein's structure."
    ),
    "afdb.coverage_unverified": (
        "Report coverage as unknown. Do not infer completeness from the "
        "absence of a truncation warning."
    ),
    "afdb.confidence_is_not_accuracy": (
        "Do not treat high pLDDT as agreement with an experimental structure "
        "or as confirmation that this is the biologically relevant "
        "conformation. pLDDT is the model's confidence in its own prediction; "
        "a high score means the prediction is self-consistent, not that it "
        "matches reality. Do not write 'the structure is accurate' — write "
        "'the predicted fold is high-confidence.'"
    ),
    "afdb.model_is_not_docking_ready": (
        "Do not use this model for docking or binding-site work without "
        "stating that it is a prediction, not an experimental structure. "
        "Side-chain and loop conformations are the parts pLDDT is least "
        "informative about, and they are what docking depends on. A "
        "high-pLDDT model is a fold hypothesis, not a docking-ready "
        "receptor."
    ),
    "fpocket.conformation_dependent": (
        "Say which conformation was scored, and do not convert a low score "
        "into a claim about the target. fpocket's druggability model was "
        "trained on crystal structures and the score moves with the "
        "conformation; on a predicted or modelled structure a low score is a "
        "statement about the model, not about whether the site can be drugged."
    ),
    "fpocket.single_conformation": (
        "Write the negative as 'no druggable pocket in this conformation of "
        "<structure>', naming the structure. One structure cannot support "
        "'this site cannot be drugged': three CDK2 crystal structures of the "
        "same, heavily drugged ATP site score 0.17, 0.29 and 0.94 on this "
        "scale. A score under the cutoff is a reason to score another "
        "conformation, not a reason to drop a target."
    ),
    "pocket.likely_bundle_void": (
        "Report that this pocket spans the TM bundle interior and is likely "
        "an artefact of the helix packing, not a discrete ligand-binding "
        "cavity. A pocket lining 5+ TM segments with high volume or alpha-"
        "sphere count is the shape of the void between helices, not of a "
        "druggable site. Do not carry its drug score into a tractability "
        "claim."
    ),
    "fpocket.druggability_is_not_affinity": (
        "Report this as 'the site has a pocket with drug-like geometry', never "
        "as evidence that a compound will bind or how tightly. The score "
        "describes the shape, volume and hydrophobicity of a cavity with no "
        "ligand in it; affinity is a property of a pair. A role that lacks an "
        "affinity tool is the one most likely to carry this number in its "
        "place — if that is why it is being read, the answer is that the "
        "question is untooled, not that the pocket is 0.94."
    ),
    "fpocket.ligand_present_in_input": (
        "Pocket analysis was run on a structure containing non-protein chains. "
        "Drug scores may be inflated relative to apo-structure scoring. Compare "
        "with protein-only analysis for accurate druggability assessment."
    ),
    "fpocket.possible_peptide_occlusion": (
        "A low druggability score was computed on a structure containing short "
        "chain(s) that may be peptidic ligands occluding the binding site. "
        "Rerun on an apo or small-molecule-bound conformation before concluding "
        "the target is undruggable. Use --strip-peptides to remove short chains "
        "automatically."
    ),
    "fpocket.low_score_holo_structure": (
        "A low druggability score was computed on a structure containing "
        "non-receptor chain(s). Pocket scores are conformation-dependent; "
        "assess druggability from an apo or alternate-conformation structure "
        "before concluding undruggability."
    ),
    "coscientist.partial_export": (
        "Confine conclusions to the ideas present in the export. Do not "
        "treat absence from it as evidence against an idea."
    ),
    "coscientist.review_recommendation_available": (
        "Address the co-scientist review recommendation before proceeding "
        "with target selection. The tournament produced a structured "
        "recommendation section — present it to the decision-maker and "
        "document whether the recommendation was followed, adapted, or "
        "rejected with rationale."
    ),
    "coscientist.leader_worst_contradiction_profile": (
        "The recommended idea has the highest contradicted-claim count "
        "among all candidates. The Science Lead must acknowledge this "
        "finding, justify proceeding with this target, and consider a "
        "fast-fail foundational claim check before committing a full cohort."
    ),
    "alphagenome.no_quantile_scores": (
        "Do not call any effect significant on raw score alone. No quantile "
        "was returned for the named output types, so the significance rule "
        "the interpretation guide defines could not be applied to them."
    ),
    "alphagenome.quantile_artifact": (
        "Exclude the flagged genes from the effect list entirely. An extreme "
        "quantile on a negligible raw score is a rank against a flat "
        "background; reporting it as a top-percentile effect inverts the "
        "finding."
    ),
    "alphagenome.band_modality_mismatch": (
        "State the assay the score came from alongside any magnitude word. "
        "The bands are RNA-seq-derived, so 'moderate' on a CAGE or ATAC "
        "track is an extrapolation, not a calibrated call."
    ),
    "alphagenome.effect_is_not_pathogenicity": (
        "Do not carry a predicted regulatory effect size into a clinical or "
        "pathogenicity claim. A large effect score means the model predicts "
        "this variant changes regulatory activity; it is not evidence of "
        "causality for a phenotype and not a clinical interpretation. Do "
        "not write 'pathogenic' — write 'predicted large regulatory effect.'"
    ),
    "gene.unresolved_symbol": (
        "Do not query any backend for this symbol. An unresolved identifier "
        "is a lookup failure, not evidence of gene absence. State that the "
        "symbol could not be resolved and that no query was attempted."
    ),
    "expression.no_data_found": (
        "State that the gene was resolved successfully but no expression data "
        "was found in the queried backend. This is a data gap, not evidence "
        "of non-expression. Name the backend and the resolved symbol."
    ),
    "genetics.no_data_found": (
        "State that the gene was resolved successfully via HGNC but the "
        "genetics backend returned no data. This is a data gap in the "
        "backend, not evidence that the gene has no constraint data."
    ),
    "pathway.no_data_found": (
        "State that the gene was resolved successfully via HGNC but no "
        "pathway or ontology results were found. This is a coverage gap "
        "in the queried database, not evidence that the gene has no "
        "pathway involvement."
    ),
    "expression.absent_is_not_evidence": (
        "Write the negative as 'not detected above the cutoff in HPA bulk "
        "consensus', naming the dataset. Do not write that the gene is absent "
        "from the tissue — one dataset cannot support that claim."
    ),
    # Named for what the finding must do, not for what the tool lacks.
    # `single_cell_available_unused` was the first name and it read as
    # "go and fetch it" — the one action nobody can take while
    # single-cell mode is unbuilt. A relay whose instruction cannot be
    # followed gets quoted and dropped.
    "expression.tissue_resolution_only": (
        "Scope the claim to whole-tissue averages and say so. Do not infer "
        "anything about a cell population from a tissue mean — HPA holds "
        "cell-resolved data for this gene that was not used, so the "
        "ecological fallacy here is live rather than hypothetical."
    ),
    "expression.single_cell_unavailable": (
        "State that no cell-resolved data exists for this gene, so the "
        "tissue-average verdict is the best obtainable and cannot be refined "
        "by fetching more. Do not leave the reader expecting a follow-up."
    ),
    "gnomad.constraint_not_estimable": (
        "gnomAD could not estimate loss-of-function constraint for this gene "
        "(insufficient expected LoF variants). Any finding about this gene's "
        "essentiality must state that LoF intolerance could not be assessed, "
        "not silently omit it."
    ),
    "gnomad.constraint_unreliable": (
        "Report the constraint metric with its 90% confidence interval and say "
        "the gene could not be confidently categorised. Do not quote pLI or "
        "LOEUF bare, and do not resolve the ambiguity by picking the metric "
        "that agrees with the hypothesis."
    ),
    "gnomad.constraint_is_not_safety": (
        "Do not carry this into a safety or tolerability claim. LoF intolerance "
        "describes complete loss from conception across development; it says "
        "nothing about partial, reversible, adult pharmacological inhibition, "
        "and reading it as toxicology would eliminate most viable targets."
    ),
    "pubmed.fulltext_unavailable": (
        "The requested article is not available in PubMed Central open access. "
        "The PMCID may be incorrect or the article may not be in the OA subset."
    ),
    "pubmed.search_not_exhaustive": (
        "A PubMed keyword search returns results matching the query terms but "
        "cannot guarantee exhaustive coverage. Relevant publications may use "
        "different terminology, be indexed under different MeSH headings, or "
        "not yet be indexed. Do not treat absence from search results as "
        "evidence of absence in the literature."
    ),
    "pubmed_bq.search_not_exhaustive": (
        "A PubMed BigQuery search uses SQL substring matching against article "
        "titles and abstracts. It cannot guarantee exhaustive coverage: relevant "
        "publications may use different terminology, alternate spellings, or "
        "synonyms not captured by the query terms. Do not treat absence from "
        "search results as evidence of absence in the literature."
    ),
    "litref.resolved_not_verified": (
        "Confirm the record says what the claim says before citing it. This "
        "tool established only that the record exists; it did not read it."
    ),
    "litref.ambiguous_name": (
        "Stop and obtain a unique identifier — NCT, PMID or DOI — before the "
        "claim proceeds. Do not choose among the candidates listed: they are "
        "listed because the tool refused to choose."
    ),
    "litref.name_match_not_unique_identifier": (
        "Report the match as 'one record found by title search', not as the "
        "record. A title search cannot see a name that appears only in an "
        "abstract or a trial description, so uniqueness is unproven."
    ),
    "compreg.resolved_not_verified": (
        "This tool confirmed the identifier maps to a real compound record and "
        "surfaced its canonical name and SMILES. It did not verify that the "
        "compound has the claimed biological activity, mechanism, or therapeutic "
        "indication -- those claims require separate evidence."
    ),
    "compreg.name_match_not_unique_identifier": (
        "This compound was matched by name, not by a unique registry identifier. "
        "A name search may miss synonyms and cannot prove uniqueness. Obtain the "
        "CID or ChEMBL ID and re-resolve."
    ),
    "expression.release_version_unknown": (
        "Cite the artifact by its payload SHA-256, not by an HPA release "
        "number. The release label was not obtained, so any version stated in "
        "the finding would be invented."
    ),
    "alphagenome.unexplained_missing_scores": (
        "Report the scored-track count, not the track total, and say that "
        "some tracks went unscored for reasons the tool could not explain. "
        "Do not aggregate over the full track set."
    ),
    "compound.fragment_stripped": (
        "Name the stripped fragments alongside any finding about the parent "
        "molecule.  The input was a multi-component SMILES (salt or mixture); "
        "descriptors and alerts were computed on the largest fragment only.  "
        "Do not attribute these properties to the original input without "
        "noting what was removed."
    ),
    "compound.alerts_not_toxicology": (
        "Do not conclude the compound is non-toxic from the absence of "
        "PAINS/Brenk/aggregator alerts.  These filters detect known assay "
        "interference patterns and undesirable substructures, not toxicity "
        "mechanisms.  A compound that passes them may still be toxic, "
        "reactive, or genotoxic by pathways these filters do not cover."
    ),
    "compound.sa_score_is_estimate": (
        "Do not conclude that a compound is synthetically feasible from a low "
        "SA-score alone. The SA-score is a computational estimate based on "
        "fragment frequency and molecular complexity -- it does not account for "
        "reagent availability, protecting group strategies, scalability, or "
        "specific reaction conditions. A low score means the molecule's "
        "substructures are commonly seen in known compounds, not that a "
        "synthesis route exists."
    ),
    "assay.screen_quality_insufficient": (
        "Do not report compound activity verdicts from this screen. The "
        "Z-factor is below the usability threshold (Zhang et al. 1999), "
        "meaning the assay window is too narrow to distinguish active from "
        "inactive compounds. The screen itself is the problem, not the "
        "compounds."
    ),
    "assay.cytotoxicity_confound": (
        "Flag this compound's dose-response as potentially confounded by "
        "cytotoxicity. A bell-shaped (non-monotonic) curve — where activity "
        "increases then decreases at higher concentrations — is a hallmark "
        "of cytotoxicity masking the primary pharmacological effect. Do not "
        "report the fitted IC50 without this caveat."
    ),
    "selectivity.ratio_not_affinity": (
        "Report selectivity ratios as assay-derived estimates, never as "
        "thermodynamic selectivity constants. A ratio computed from two IC50 "
        "values inherits whatever caveats apply to IC50 as a measure of "
        "affinity: IC50 is assay-dependent and not a thermodynamic binding "
        "constant, so a 50-fold ratio from two IC50 values each with 3-fold "
        "assay variability is not the same confidence as a ratio from Kd "
        "values. If this ratio is the basis for a selectivity claim, state "
        "the measure type and do not imply thermodynamic precision."
    ),
    "selectivity.panel_incomplete": (
        "Scope the selectivity claim to the off-targets that were actually "
        "tested, and name them. A selectivity assessment over a narrow panel "
        "is incomplete — testing 3 off-targets when a kinome-wide panel would "
        "be the real question. Do not generalise the selectivity claim beyond "
        "the tested panel."
    ),
    "gtex.whole_blood_is_not_peripheral_blood": (
        "Report this as 'GTEx whole-blood RNA-seq expression', not as "
        "'peripheral blood expression'. GTEx whole blood is drawn from "
        "femoral/subclavian veins and measured by bulk RNA-seq; it is a "
        "partial proxy for peripheral blood protein expression, not an "
        "equivalent measurement."
    ),
    "pk.rule_of_exponents_uncorrected": (
        "State that the fitted allometric exponent falls outside the simple "
        "allometry range (0.55-0.70) and that the Mahmood & Balian 1996 rule "
        "of exponents recommends a correction (MLP or brain weight) that has "
        "not been applied. The predicted human CL may be less reliable without "
        "this correction."
    ),
    "pk.single_species_scaling": (
        "Do not present this as a validated estimate. Single-species allometry has "
        "high uncertainty; the rule of exponents requires data from at least two "
        "species for reliable CL prediction. A human dose projection from one "
        "species should not be presented as a validated estimate."
    ),
    "pk.dermal_partition_estimated": (
        "Dermal partition parameters are estimated from steady-state assumptions "
        "and Fick's first law. The steady-state model assumes infinite dose, "
        "constant vehicle concentration at the skin surface, and homogeneous "
        "membrane permeation. Real dermal absorption is affected by formulation "
        "depletion, skin hydration, occlusion, and site-specific differences in "
        "stratum corneum thickness."
    ),
    "screening.prefilter_excludes_not_rejects": (
        "Compounds excluded by the descriptor pre-filter were not docked, not "
        "proven inactive. The pre-filter is a compute-saving heuristic based on "
        "physicochemical property ranges; compounds outside these ranges may still "
        "bind the target. Report must state which pre-filter thresholds were "
        "applied and how many compounds were excluded."
    ),
    "docking.score_is_not_affinity": (
        "Report this as a predicted binding energy estimate, never as a measured "
        "affinity or a potency. A Vina score describes a computed interaction "
        "energy for a pose in a rigid pocket; binding affinity is a thermodynamic "
        "property of a pair measured in solution. A role that lacks an affinity "
        "assay tool is the one most likely to carry this number in its place — "
        "if that is why it is being read, the answer is that the question is "
        "untooled, not that the score is -8.2."
    ),
    "docking.contact_is_not_binding_event": (
        "State that the reported contacts represent geometric proximity "
        "within a static, computationally docked pose — not a confirmed "
        "binding interaction. No hydrogen-bond geometry, electrostatic "
        "complementarity, or reactive-orientation check was performed."
    ),
    "admet.prediction_not_measurement": (
        "Do not treat these predicted ADMET endpoints as measured values. Every "
        "number here is a rule-based prediction from molecular descriptors, not "
        "an in vitro measurement. A clean predicted ADMET profile does not "
        "substitute for in vitro ADMET studies — it identifies which studies to "
        "prioritize, not which to skip."
    ),
    "admet.herg_structural_flag": (
        "Name the specific structural features that triggered this hERG flag "
        "and state that it is a pharmacophore-based prediction, not a measured "
        "IC50 or patch-clamp result. Rule-based hERG prediction has a documented "
        "false-negative rate: absence of this flag is not evidence of hERG safety."
    ),
    "mpo.minmax_cohort_relative": (
        "MPO scores computed with min-max normalization are relative to the "
        "specific cohort scored in this run. Do not compare scores across "
        "different scoring runs or candidate sets — a compound's score "
        "changes when the cohort changes, even if its raw values do not. "
        "Use --scoring absolute with explicit bounds for "
        "cohort-independent scores."
    ),
    "mmp.cliff_not_causation": (
        "Do not interpret a property cliff as evidence of a causal mechanism. "
        "A large property change across a single R-group transformation is a "
        "correlation in this dataset — it flags a substitution worth a medicinal "
        "chemist's attention, but it does not explain why the transformation "
        "matters mechanistically. The structural change may be incidental to "
        "the true driver."
    ),
    "mmp.small_pair_count": (
        "Do not report this transformation's property trend with confidence. "
        "The number of matched pairs supporting it is below the minimum "
        "required for a reliable SAR conclusion. Report the raw observation "
        "and the pair count, not a trend."
    ),
    "tox.genotox_weight_of_evidence": (
        "State that this genotoxicity assessment used ICH S2(R1) "
        "weight-of-evidence reasoning because the battery results were "
        "mixed. Do not report the verdict as a clean negative — name "
        "the positive assay and the basis for the overall assessment."
    ),
    "tox.margin_indeterminate": (
        "Therapeutic index could not be computed because the inputs were "
        "insufficient to distinguish clinical from preclinical exposure. "
        "Do not interpret the absence of a safety flag as a clean result."
    ),
    "bioactivity.externally_sourced": (
        "State that these bioactivity values are literature-derived and retrieved "
        "from a public database. They are reported values from published assays, "
        "not measurements from this program's own screening. Do not present them "
        "as validated in-house data."
    ),
    "homology.structure_is_not_target": (
        "Do not describe this as the structure of the query protein. It is "
        "the structure of a homologous protein; any structural feature, "
        "binding site, or conformation attributed to the query protein from "
        "this structure is a hypothesis transferred by sequence similarity, "
        "not a direct observation. Name the source protein and the sequence "
        "identity in every structural claim."
    ),
    "gwas.association_not_causation": (
        "GWAS associations are statistical correlations between genetic variants "
        "and disease phenotypes. They do not establish causation, directionality, "
        "or mechanism. A significant association means the variant co-occurs with "
        "the phenotype more than expected by chance in the studied population -- "
        "it does not mean the gene product causes the disease or that modulating "
        "it will treat the disease."
    ),
    "opentargets.composite_not_genetic": (
        "The Open Targets overall association score is a composite that blends "
        "genetic_association, literature, expression, and other data types. A "
        "target that passes the composite threshold but fails on "
        "genetic_association alone is supported by non-genetic evidence "
        "(text-mining, expression correlation, etc.), not by GWAS or other "
        "genetic studies. Do not describe this as a genetic association — "
        "describe it as a composite association and name the dominant "
        "contributing data types."
    ),
    "clinvar.classification_is_curated": (
        "Do not describe ClinVar classifications as statistical associations or "
        "correlations. A ClinVar 'Pathogenic' call is a curated clinical "
        "assertion — expert reviewers assessed that this variant causes the "
        "named condition — not a p-value from a population study. However, "
        "variant-level pathogenicity does not imply the gene is a validated "
        "therapeutic target: pathogenicity describes what happens when the "
        "variant is present from conception, not what happens when the gene "
        "product is modulated pharmacologically in an adult."
    ),
    "clinvar.cnv_not_gene_specific": (
        "ClinVar pathogenic variants for this gene are dominated by large CNVs "
        "that span multiple genes, not gene-specific mutations. The pathogenic "
        "count reflects locus overlap, not gene-specific evidence. Any safety "
        "conclusion must distinguish CNV-based from gene-specific pathogenicity."
    ),
    "clinvar.weak_review_status": (
        "Do not cite a ClinVar classification without its review status, and do "
        "not weight a classification with weak review status as though it were "
        "authoritative. A 'Pathogenic' call with 'no assertion criteria provided' "
        "carries much less weight than one 'reviewed by expert panel' or backed "
        "by a 'practice guideline'. Report both the classification and the review "
        "status, or withhold the classification."
    ),
    "ppi.interaction_not_functional": (
        "Protein-protein interactions reported by STRING are aggregated from "
        "multiple evidence channels including text mining, co-expression, and "
        "genomic context. A high interaction score does not confirm direct "
        "physical binding or functional relevance in the tissue or condition "
        "of interest. Experimental validation is required."
    ),
    "faers.spontaneous_reports_not_incidence": (
        "FAERS reports are spontaneous (voluntary) adverse event reports. They "
        "cannot establish incidence rates, causation, or comparative safety. "
        "Reporting rates are affected by media attention, time on market, "
        "indication severity, and reporter awareness. Do not interpret report "
        "counts as incidence or compare raw counts between drugs."
    ),
    "phenotype.model_organism_not_human": (
        "Phenotype data from model organisms (mouse, rat) may not translate "
        "directly to humans. Species differences in gene function, expression "
        "patterns, and compensatory mechanisms mean that a knockout phenotype "
        "in mouse is informative but not predictive of human clinical outcomes."
    ),
    "pathway.membership_not_activity": (
        "Pathway membership means the gene product is annotated to a pathway "
        "or GO term. It does not indicate that the gene is active, rate-limiting, "
        "or causally involved in the pathway in the tissue or condition of interest. "
        "Expression, activity, and essentiality are separate questions."
    ),
    "conservation.rate_is_not_function": (
        "Do not infer functional importance from conservation alone. A conserved "
        "residue evolves slowly across the sampled lineages; it may be "
        "structurally important, functionally important, or both, but conservation "
        "is a phylogenetic observation, not a functional annotation. A variable "
        "residue is not dispensable -- it may be under positive selection or "
        "lineage-specific constraint not captured by this alignment."
    ),
    "conservation.low_coverage": (
        "Conservation analysis did not score all positions. Findings citing "
        "conservation scores MUST note the coverage limitation and must not "
        "claim genome-wide or full-sequence conservation from a partial score."
    ),
    "conservation.pocket_in_gap": (
        "Pocket residues fall in unscored MSA columns. Conservation assessment "
        "for these residues is unavailable. Do not infer conservation or "
        "variability for residues that could not be scored."
    ),
    "dice.in_vitro_not_in_vivo": (
        "DICE expression data is derived from in-vitro stimulated or sorted "
        "immune cells. Expression levels may differ from in-vivo tissue "
        "microenvironments. Cell isolation and culture conditions can alter "
        "gene expression profiles."
    ),
    "pubchem.annotation_is_not_validation": (
        "Published annotations (synonyms, MoA, pharmacological class) are database "
        "records, not in-house validation. Do not treat a PubChem pharmacological "
        "classification as equivalent to a verified mechanism study."
    ),
    "pubchem.drug_status_is_development_history": (
        "A ChEMBL max_phase of 4 means the compound has been approved for some "
        "indication in some jurisdiction. It does not confirm the compound is "
        "approved for the indication under study, or that it is still marketed."
    ),
    "similar.tanimoto_is_2d_only": (
        "Tanimoto similarity is computed from 2D fingerprints and reflects shared "
        "substructure topology, not 3D shape complementarity or biological activity. "
        "Two compounds with Tanimoto 0.95 may have very different binding modes, "
        "selectivity profiles, or ADMET properties."
    ),
    "cellxgene.search_is_metadata_only": (
        "CELLxGENE dataset search returns collection and dataset metadata; "
        "expression values require downloading the H5AD file or using the "
        "Census API. A dataset containing the queried tissue does not confirm "
        "expression of any specific gene."
    ),
    "geo.search_is_metadata_only": (
        "GEO dataset search returns dataset-level metadata (title, summary, "
        "sample list). It does not contain expression values or differential "
        "expression results. Determining whether a gene is differentially "
        "expressed in a dataset requires downloading and analysing the "
        "expression data (GEO2R, supplementary files, or SRA raw data)."
    ),
    "similar.search_incomplete": (
        "Similarity search did not complete for one or more backends. Do not "
        "interpret the absence of similar compounds as confirmed novelty when "
        "the search is incomplete."
    ),
    "similar.all_backends_failed": (
        "All similarity search backends failed. Novelty assessment is incomplete "
        "and the result must not be treated as a clean novelty finding."
    ),
    "similar.database_coverage_limited": (
        "PubChem contains ~116M compounds and ChEMBL ~2.4M bioactive molecules. A "
        "compound with no similar hits may have close analogs in proprietary "
        "collections, patent literature, or databases not queried. Do not treat "
        "'novel by PubChem/ChEMBL' as 'novel.'"
    ),
    "scp.search_is_study_metadata": (
        "Single Cell Portal search returns study-level metadata; gene expression "
        "data requires authenticated access to individual studies. A matching "
        "study does not confirm expression of any specific gene."
    ),
    "disco.search_is_sample_metadata": (
        "DISCO search returns sample-level metadata (tissue, disease, cell "
        "count). Expression values and cell type markers are not included in "
        "search results. Confirming gene expression or cell type enrichment "
        "requires downloading the expression data (H5 files) from DISCO."
    ),
    "spatialdb.spatial_not_bulk": (
        "SpatialDB records describe spatially resolved expression experiments, "
        "not bulk or single-cell RNA-seq. Spatial transcriptomics captures gene "
        "expression with tissue coordinates but covers a limited set of tissues "
        "and studies. Absence from SpatialDB does not mean a gene lacks spatial "
        "expression data — the database indexes published spatial transcriptomics "
        "datasets, not all spatial experiments."
    ),
    "disignatlas.curated_signatures": (
        "Do not treat DisigNAtlas signatures as primary experimental evidence. "
        "These are pre-computed differential expression results aggregated from "
        "public datasets (GEO, ArrayExpress, TCGA) using standardised pipelines. "
        "Individual study quality, sample sizes, and normalisation methods vary. "
        "Treat as a discovery resource for identifying disease-gene associations, "
        "not as a substitute for primary analysis of the underlying data."
    ),
    "trials.text_match_not_mechanism": (
        "Trial search for this query used full-text matching, not mechanism-specific "
        "filtering. Results may include incidental mentions of the query string in "
        "study descriptions, conditions, or unrelated contexts. Any finding citing "
        "this verdict MUST note that results are text-matched, not mechanism-verified, "
        "and that the pipeline classification may be inflated by false positives."
    ),
    "trials.active_competitor_pipeline": (
        "Report that active Phase 3+ clinical trials exist for this target or "
        "query. Late-stage clinical development may affect freedom to operate or "
        "competitive positioning. Name the specific trials, sponsors, and "
        "indications. Do not treat the presence of competitor trials as evidence "
        "that the target is validated — a trial is a bet, not a result."
    ),
    "patent.fto_risk_identified": (
        "Patent landscape shows recent filings that may affect freedom to "
        "operate. Check assignees, claim scope, and jurisdiction before "
        "proceeding."
    ),
    "differentiation.crowded_landscape": (
        "Competitive landscape shows significant activity. Existing "
        "competitor activity is informational, not a go/no-go gate — "
        "a crowded field with a genuinely differentiated angle should "
        "surface as 'differentiated despite crowding', not be rejected. "
        "State the coverage limits of the search."
    ),
    "differentiation.not_legal_clearance": (
        "This assessment is based on public patent database searches and "
        "publicly available information. It is NOT formal legal clearance. "
        "A public search or structural similarity analysis cannot "
        "substitute for a formal freedom-to-operate opinion by qualified "
        "patent counsel. Material FTO conclusions require qualified legal "
        "review."
    ),
    "cite.phantom_citation": (
        "Name the phantom references. A phantom citation invalidates the "
        "claim resting on it, not merely the reference."
    ),
    "cite.suspect_title_match": (
        "State that the reference resolved but the title did not match "
        "within tolerance. Do not report it as verified."
    ),
    "cite.unresolved_offline": (
        "Confine the verification claim to references that resolved. Do "
        "not extend the conclusion to references that could not be checked."
    ),
    "cite.extraction_incomplete": (
        "State that references were recovered by pattern match. A reference "
        "the extractor missed is not in the manifest and was not checked."
    ),
    "surface.sasa_is_static_snapshot": (
        "SASA describes the accessible surface of this single conformation. "
        "Protein dynamics, conformational changes, and binding-partner "
        "occlusion are not represented. A buried region in one conformation "
        "may be exposed in another."
    ),
    "surface.rsa_reference_values": (
        "Relative solvent accessibility uses Tien et al. 2013 theoretical "
        "maximum SASA reference values. Name the reference used alongside "
        "any exposure classification."
    ),
    "preprint.no_results": (
        "The preprint search returned no results. Consider broadening the "
        "query or checking alternative sources."
    ),
    "preprint.query_truncated": (
        "Results were capped at the requested maximum. Additional matching "
        "preprints may exist."
    ),
    # --- cBioPortal ---
    "cbioportal.no_results": (
        "The cBioPortal search returned no results. Consider broadening "
        "the query or checking alternative cancer genomics databases."
    ),
    "cbioportal.query_truncated": (
        "Results were capped at the requested maximum. Additional matching "
        "studies may exist."
    ),
    # --- hypothesis adoption ---
    "hypothesis.adopted_not_generated": (
        "The hypothesis set was attested by a human, not retrieved by a "
        "tool. Its provenance chain terminates at the attestation. Quote "
        "the attestation verbatim in any finding that rests on this "
        "artifact, and do not describe the set as DDE-derived."
    ),
    "hypothesis.unranked_set": (
        "This set carries no ranking. Array position is input order, not "
        "preference. Do not present it as a leaderboard or select 'the "
        "top candidate' from it."
    ),
    "hypothesis.strategy_fallback": (
        "The preferred hypothesis strategy was unavailable and a weaker "
        "method was substituted. Any decision citing this assessment was "
        "made with degraded methodology. Read strategy_requested vs "
        "strategy_used in the sidecar to see what was lost."
    ),
    # --- hypex tournament ---
    "hypex.citation_manifest_absent": (
        "Absence of a citation manifest is not evidence of verified "
        "citations. State which hypotheses lack a manifest and do not "
        "infer citation quality from the absence."
    ),
    "hypex.composite_ranking": (
        "The ranking number is a composite blending ELO with reviewer "
        "scores, not a pure ELO. Report it as a composite and name the "
        "preset used. Do not write it into a field named 'elo'."
    ),
    "hypex.integrity_violations": (
        "State the dangling references. `hypex validate` does not check "
        "these; the ingest is the only place they surface. A dangling "
        "match or review reference means the ranking rests on a record "
        "that cannot be traced to its source."
    ),
    "hypex.pacing_uncoordinated": (
        "The run fanned out without verified shared pacing, so its "
        "retrieval rate against upstream hosts was up to roster_size x "
        "the intended limit. Findings resting on this run's retrievals "
        "may be incomplete through throttling rather than through "
        "absence. State the roster size and the tier observed."
    ),
    "hypex.phantom_citations_present": (
        "Name the affected hypotheses. A phantom citation invalidates "
        "the claim resting on it, not merely the reference."
    ),
    "hypex.quarantined_excluded": (
        "Report the quarantine count alongside the ranking. Quarantined "
        "hypotheses were excluded from the tournament and are not "
        "represented in the standings."
    ),
    "hypex.run_aborted": (
        "No termination record was written. Treat the run as incomplete "
        "and state which epoch it reached."
    ),
    "hypex.run_not_converged": (
        "The ranking is where the run stopped, not where the tournament "
        "settled. Do not report it as converged."
    ),
    "hypex.unrated_hypotheses": (
        "These hypotheses were not ranked; under Swiss pairing they were "
        "excluded entirely. Absence from the ranking is not elimination "
        "by it."
    ),
    "provenance.cross_wo_overwrite": (
        "State that this analysis record was overwritten from a different "
        "work order. The prior work order's evidence chain is broken at "
        "this record. Name both work orders and do not cite this record "
        "as evidence belonging to the original work order."
    ),
    "dossier.relays_forwarded": (
        "Upstream relays from Layer 0 artifacts have been forwarded into "
        "the CTD export. Review the relays section for caveats that affect "
        "regulatory interpretation. Each forwarded relay retains its "
        "original code and source artifact reference."
    ),
    # --- structural superposition ---
    "structure.low_sequence_identity": (
        "Fewer than 50% of residues could be matched between the two "
        "structures. The global RMSD is computed over a minority of the "
        "total residues and does not describe the full structural relationship. "
        "State the matched fraction alongside any RMSD value."
    ),
    # --- docking pose validation ---
    "docking.no_pose_control": (
        "Pose-reproduction RMSD exceeds the threshold but was run with "
        "--warn-only. The docking protocol has not demonstrated pose "
        "reproduction and downstream binding-energy predictions rest on "
        "poses whose geometric accuracy is unvalidated."
    ),
    "docking.pose_reproduction_failed": (
        "The docked pose deviates from the reference ligand placement by "
        "more than the threshold RMSD. The docking protocol did not "
        "reproduce the known binding mode. Do not trust ranked poses from "
        "this receptor/grid configuration without investigating the source "
        "of the deviation."
    ),
}


def relay(code: str, message: str) -> dict[str, str]:
    """Build a mandatory-relay record, rejecting unregistered codes."""
    if code not in RELAY_CODES:
        raise KeyError(
            f"unregistered relay code {code!r}; add it to provenance.RELAY_CODES "
            "so skills and reviewers can enumerate it"
        )
    return {"code": code, "message": message}


def record_type_from_schema(schema: str) -> str:
    """Extract the record type from a DDE schema tag.

    Schema format: ``dde.{record_type}.v{version}``

    Examples::

        dde.tox-genotox-assessment.v1 → tox-genotox-assessment
        dde.pk-nca.v1 → pk-nca
        dde.tox-margins.v1 → tox-margins

    Falls back to the full schema string if the format is unrecognised.
    """
    parts = schema.split(".")
    if len(parts) >= 3 and parts[0] == "dde" and parts[-1].startswith("v"):
        return ".".join(parts[1:-1])
    return schema


def record_type_from_filename(filename: str) -> str | None:
    """Extract the record type from a DDE artifact filename.

    Filename format: ``{stem}.{record_type}.json``

    Returns the record type, or None if the format is unrecognised.
    """
    # Strip .json suffix, then the last remaining dotted segment is the record type
    if not filename.endswith(".json"):
        return None
    base = filename[:-5]  # strip .json
    parts = base.rsplit(".", 1)
    if len(parts) == 2:
        return parts[1]
    return None


def _check_cross_wo_overwrite(path: Path) -> dict[str, str] | None:
    """Check cross-work-order overwrite protection.

    Returns a relay dict if cross-WO overwrite is proceeding (allowed),
    None if no cross-WO issue exists, or raises Refusal if cross-WO
    overwrite is not explicitly allowed.
    """
    if not _overwrite_allowed or not path.exists():
        return None

    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            return None
        existing_wo = existing.get("work_order_id")
    except (OSError, ValueError):
        return None

    current_wo = _work_order()
    if not existing_wo or not current_wo or existing_wo == current_wo:
        return None

    if not _overwrite_cross_wo_allowed:
        raise Refusal(
            f"{path.name} is attributed to work order {existing_wo} "
            f"(current: {current_wo}). "
            "Use --overwrite-cross-wo to confirm overwriting another "
            "work order's evidence.",
            remedy="Pass --overwrite-cross-wo to explicitly allow overwriting "
            "another work order's analysis, or use --out to write to a "
            "different location.",
        )

    return relay(
        "provenance.cross_wo_overwrite",
        f"Analysis record {path.name} was overwritten from work order "
        f"{existing_wo} to {current_wo}. The prior work order's evidence "
        "chain is broken.",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Sidecar:
    """Builder for a phase-1 `.meta.json` provenance record."""

    def __init__(
        self,
        tool: str,
        subcommand: str,
        endpoint: str | None = None,
        parameters: dict[str, Any] | None = None,
    ):
        self.tool = tool
        self.subcommand = subcommand
        self.endpoint = endpoint
        self.parameters = parameters or {}
        self.outputs: list[dict[str, Any]] = []
        self.warnings: list[str] = list(env.env_warnings())
        self.relays: list[dict[str, str]] = []
        self.extra: dict[str, Any] = {}
        self.started = _utc_now()

    def add_output(self, path: Path) -> Path:
        path = Path(path)
        if not path.is_file():
            raise ArtifactError(f"declared output does not exist: {path}")
        self.outputs.append(
            {
                "path": path.name,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
        return path

    def warn(self, message: str, code: str | None = None) -> None:
        """Record a warning; with `code`, also mark it a mandatory relay.

        Relays stay in `warnings` too. A reader that knows nothing about
        codes still sees the prose, so adding this could not quietly
        remove a warning from anyone's view.
        """
        if message not in self.warnings:
            self.warnings.append(message)
        if code and not any(r["code"] == code for r in self.relays):
            self.relays.append(relay(code, message))

    def note(self, key: str, value: Any) -> None:
        self.extra[key] = value

    def set_capability_state(self, snapshot: dict[str, str]) -> None:
        """Attach the current capability snapshot to this sidecar.

        The snapshot is a dict mapping capability names to their status
        (e.g. ``{"hypex": "unavailable", "alphafold3": "available"}``).
        It makes a decision made under degraded capability state
        structurally distinguishable from one made with full capability.
        """
        self.extra["capability_state"] = snapshot

    def to_dict(self) -> dict[str, Any]:
        tc = check_integrity()
        record = {
            "tool": self.tool,
            "subcommand": self.subcommand,
            "work_order_id": _work_order(),
            "cli_version": env.CLI_VERSION,
            "cli_integrity": tc.integrity,
            "env_version": env.env_version(),
            "interpreter": env.interpreter_tag(),
            "endpoint": self.endpoint,
            "parameters": self.parameters,
            "timestamp": self.started,
            "completed": _utc_now(),
            "outputs": self.outputs,
            "warnings": self.warnings,
            "mandatory_relays": self.relays,
        }
        if tc.modified:
            record["cli_modified"] = True
            record["cli_modified_note"] = (
                "DDE source has uncommitted modifications. Artifacts may not "
                "be reproducible under the declared cli_version."
            )
        record.update(self.extra)
        return record

    def write(self, path: Path) -> Path:
        """Write the sidecar. `path` is the full .meta.json path."""
        path = Path(path)
        if not is_safe_to_open(path):
            raise Refusal(
                f"refusing to write sidecar through symlink: {path}",
                detail="symlink exploitation guard",
            )
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path


def _get_project_root() -> Path | None:
    """Try to discover the project root. Returns None if unavailable."""
    try:
        from .context import resolve_project

        return resolve_project().root.resolve()
    except Exception:
        return None


def _normalize_source(source: str | Path) -> str:
    """Normalize a source reference to a project-relative path string.

    Handles four cases:

    1. **Absolute path** — made relative to the project root.
    2. **Bare filename** (no directory component, has a file extension) —
       searched for in ``ARTIFACT_DIRS``; the first hit is stored as
       its project-relative path.
    3. **Relative path with directory components** — validated against the
       project root for containment and returned as-is.
    4. **Non-path string** (accession, endpoint, query) — returned
       unchanged.

    Raises :class:`ArtifactError` if the resolved path escapes the
    project root.
    """
    source_str = str(source)
    is_path_obj = isinstance(source, Path)

    project_root = _get_project_root()
    if project_root is None:
        return source_str

    source_path = Path(source_str)

    # Case 1: absolute path → make relative to project root.
    if source_path.is_absolute():
        resolved = source_path.resolve()
        if not resolved.is_relative_to(project_root):
            raise ArtifactError(
                f"source path escapes project root: {source}",
                detail=f"resolved to {resolved}, project root is {project_root}",
            )
        return str(resolved.relative_to(project_root))

    # Case 2: bare filename (no directory component).
    if "/" not in source_str and "\\" not in source_str:
        # Only search when source looks like a filename (has an extension)
        # or was explicitly passed as a Path object.
        if "." in source_str or is_path_obj:
            from .context import ARTIFACT_DIRS

            for rel_dir in sorted(set(ARTIFACT_DIRS.values())):
                candidate = project_root / rel_dir / source_str
                if candidate.is_file():
                    return str(Path(rel_dir) / source_str)
        # Not found in artifact dirs or not a filename — return as-is.
        return source_str

    # Case 3: relative path with directory components — verify containment.
    try:
        resolved = (project_root / source_path).resolve()
    except (ValueError, RuntimeError):
        return source_str
    if not resolved.is_relative_to(project_root):
        raise ArtifactError(
            f"source path escapes project root: {source}",
            detail=f"resolved to {resolved}, project root is {project_root}",
        )

    return source_str


def write_analysis(
    path: Path,
    source: str | Path,
    threshold_set: str,
    thresholds_applied: dict[str, Any],
    metrics: dict[str, Any],
    assessment: dict[str, Any],
    *,
    threshold_sources: dict[str, str] | None = None,
    threshold_provenance: str | None = None,
    unresolved: list[str] | None = None,
    mandatory_relays: list[dict[str, str]] | None = None,
    suppress_warnings: bool = False,
    capability_state: dict[str, str] | None = None,
) -> Path:
    """Write a phase-2 `.analysis.json` record.

    ``source`` accepts a :class:`~pathlib.Path` or ``str``.  It is
    normalised to a project-relative string before storage:

    * absolute paths are made relative to the project root;
    * bare filenames are searched for in standard artifact directories;
    * paths that escape the project root raise :class:`ArtifactError`.

    This makes every call site correct by construction — callers can
    pass a ``Path`` object directly and the record will always contain a
    project-relative string like ``"raw/tox/compound.selectivity.json"``.

    `mandatory_relays` is promoted to a top-level field rather than being
    buried in `assessment`, because it is what a reviewer checks against
    the Layer 1 finding. It should be reachable without knowing the
    shape of any particular tool's assessment.

    **Refuses to replace a differing analysis.** A second opinion must be
    producible without destroying the first one, and a path scheme is a
    weak way to guarantee that: two reviewers auditing the same artifact
    on the same day resolve to the same `raw/reanalysis/<date>/` file,
    and the later write silently becomes the record. So the guarantee
    lives at the write instead, where it holds whatever the path scheme
    turns out to be. Re-running and getting the same verdict is not a
    conflict and passes silently; getting a different one raises, because
    that is precisely the case where somebody's citation is about to stop
    matching the file it cites.
    """
    # Compute digest from the original source value (which may be an
    # absolute path or Path object) before normalising to a relative
    # string, so the digest resolves against the filesystem as the
    # caller saw it.
    source_digest = _source_digest(str(source))

    # Normalise source to a project-relative string.
    source = _normalize_source(source)

    tc = check_integrity()
    record: dict[str, Any] = {
        "record_type": "analysis",
        "source": source,
        "cli_version": env.CLI_VERSION,
        "cli_integrity": tc.integrity,
        "env_version": env.env_version(),
        "timestamp": _utc_now(),
        "threshold_set": threshold_set,
        "thresholds_applied": thresholds_applied,
        "metrics": metrics,
        "assessment": assessment,
    }
    if tc.modified:
        record["cli_modified"] = True
        record["cli_modified_note"] = (
            "DDE source has uncommitted modifications. Artifacts may not "
            "be reproducible under the declared cli_version."
        )
    if mandatory_relays:
        record["mandatory_relays"] = mandatory_relays
    if threshold_sources:
        record["threshold_sources"] = threshold_sources
    if threshold_provenance:
        record["threshold_provenance"] = threshold_provenance
    if unresolved:
        record["thresholds_unresolved"] = unresolved
    # Always present, even when nothing identifies the caller. A field
    # that is sometimes absent cannot be quoted by a reviewer's report,
    # and a check that degrades to "the field was missing" is not a
    # check. "unattributed" is quotable and true, and reads as the
    # finding it is when it turns up in an audit.
    record["written_by"] = _writer() or "unattributed"
    record["work_order_id"] = _work_order()

    if source_digest:
        record["source_sha256"] = source_digest
    if capability_state:
        record["capability_state"] = capability_state

    path = Path(path)

    # Cross-WO protection: refuse overwrite across work orders unless
    # --overwrite-cross-wo is explicitly passed.  When allowed, fire a
    # relay so the evidence chain records the break.
    cross_wo_relay = _check_cross_wo_overwrite(path)
    if cross_wo_relay:
        record.setdefault("mandatory_relays", []).append(cross_wo_relay)

    if _may_write(path, record, suppress_warnings=suppress_warnings):
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def _source_digest(source: str) -> str | None:
    """sha256 of the source artifact, when `source` names a readable file.

    `source` is a free-text provenance string on some commands (an
    accession, an endpoint) and a path on others. Hash it when it is a
    file and stay quiet when it is not — a digest invented for a
    non-file would be worse than no digest.
    """
    try:
        candidate = Path(source)
        if candidate.is_file():
            return sha256_file(candidate)
    except (OSError, ValueError):
        pass
    return None


def _comparable(record: dict[str, Any]) -> dict[str, Any]:
    """Prepare an analysis record for equality comparison.

    Strips volatile fields and normalises fields whose notation may
    differ between old and new records without a substantive change:

    * **source** — bare filenames written before #129 are resolved to
      project-relative paths via ``_normalize_source()``, matching the
      form that ``write_analysis()`` now stores.
    * **record_type** — records written before #130 lack this field.
      Absence is treated as ``"analysis"`` so that old and new records
      compare equal when the science is unchanged.
    """
    out = {k: v for k, v in record.items() if k not in _VOLATILE_ANALYSIS_FIELDS}

    # Normalise source notation so bare filenames and project-relative
    # paths for the same file compare equal.  _normalize_source() is
    # idempotent: already-relative paths pass through unchanged.
    if "source" in out:
        out["source"] = _normalize_source(out["source"])

    # Old records written before #130 lack record_type.  New records
    # always carry record_type='analysis'.  Default the absent field so
    # the two sides agree when the science is unchanged.
    out.setdefault("record_type", "analysis")

    return out


def _volatile_stamp_changes(
    existing: dict[str, Any], new: dict[str, Any]
) -> dict[str, tuple[Any, Any]]:
    """Collect interesting volatile fields that differ between two records.

    Returns a dict mapping field name to ``(old_value, new_value)`` for
    each field in ``_INTERESTING_VOLATILE_FIELDS`` whose value changed.
    Fields absent from either record are represented as ``None``.
    """
    changes: dict[str, tuple[Any, Any]] = {}
    for field in _INTERESTING_VOLATILE_FIELDS:
        old_val = existing.get(field)
        new_val = new.get(field)
        if old_val != new_val:
            changes[field] = (old_val, new_val)
    return changes


def _capability_upgrades(
    old_state: dict[str, str] | None,
    new_state: dict[str, str] | None,
) -> list[tuple[str, str, str]]:
    """Detect capabilities that improved between runs.

    Returns a list of ``(capability, old_status, new_status)`` tuples
    where the new state represents a gain — i.e. the capability was
    previously ``"unavailable"`` and is now something else, or was absent
    and is now present.
    """
    if not old_state or not new_state:
        return []
    upgrades: list[tuple[str, str, str]] = []
    all_caps = sorted(set(old_state) | set(new_state))
    for cap in all_caps:
        old = old_state.get(cap, "absent")
        new = new_state.get(cap, "absent")
        if old != new and new not in ("unavailable", "absent"):
            if old in ("unavailable", "absent"):
                upgrades.append((cap, old, new))
    return upgrades


def _emit_volatile_stamp_warning(
    path: Path,
    changes: dict[str, tuple[Any, Any]],
) -> None:
    """Emit a NOTE to stderr listing volatile stamp differences.

    Called when ``_may_write`` determines that two records agree on
    science but differ on provenance stamps.  Informational only — does
    not affect exit code or rewrite behaviour.
    """
    lines: list[str] = [
        f"NOTE: {path.name} agrees (science unchanged). Not rewritten.",
        "Provenance stamps differ from stored record:",
    ]
    for field, (old_val, new_val) in sorted(changes.items()):
        old_repr = (
            json.dumps(old_val) if not isinstance(old_val, str) else f'"{old_val}"'
        )
        new_repr = (
            json.dumps(new_val) if not isinstance(new_val, str) else f'"{new_val}"'
        )

        # For dict-type fields like capability_state, show per-key diffs.
        if isinstance(old_val, dict) and isinstance(new_val, dict):
            diff_keys = sorted(
                k
                for k in set(old_val) | set(new_val)
                if old_val.get(k) != new_val.get(k)
            )
            for k in diff_keys:
                ok = old_val.get(k, "absent")
                nk = new_val.get(k, "absent")
                lines.append(f'  {field}.{k}: "{ok}" → "{nk}"')
        else:
            lines.append(f"  {field}: {old_repr} → {new_repr}")

    # Detect capability upgrades for the specific signal.
    old_cap = changes.get("capability_state", (None, None))[0]
    new_cap = changes.get("capability_state", (None, None))[1]
    upgrades = _capability_upgrades(
        old_cap if isinstance(old_cap, dict) else None,
        new_cap if isinstance(new_cap, dict) else None,
    )
    if upgrades:
        for cap, old_status, new_status in upgrades:
            lines.append(
                f"Capability upgrade available: {cap} was "
                f'"{old_status}", now "{new_status}". Re-running with '
                "--overwrite would update the record with current capabilities."
            )

    lines.append("The stored record was produced under different toolchain conditions.")
    # Emit as a single block to stderr via click.echo (click is
    # already a dependency via the output module).
    import click

    click.echo("\n".join(lines), err=True)


def _may_write(
    path: Path, record: dict[str, Any], *, suppress_warnings: bool = False
) -> bool:
    """Decide whether this analysis may land at this path.

    Three outcomes, and the middle one is the reason this is a decision
    and not an assertion:

    * nothing there, or `--overwrite` — write.
    * a record that agrees — **keep the one already there**, return
      False, and say so on stderr.
    * anything else, including a file that cannot be read — refuse
      (exit 9).

    The middle case used to be a silent overwrite, because the guard
    answered "is this a conflict?" and its caller read the answer as
    "may I write?". Those two questions agree everywhere except on the
    fields excluded from the comparison — so they diverged exactly where
    nobody was looking, and `written_by` is an excluded field. A
    reviewer who re-ran without `--out` and *agreed* with the specialist
    silently took authorship of the specialist's record, at exit 0. The
    disagreeing case was loud and the agreeing case was the common one.
    (Repro from template-builder; diagnosis from plan-review, whose
    generalisation is now orchestration-design-guidance §8.0: a field
    excluded from an equality test is a field that can change without
    anyone noticing, so exclude it only if losing it costs nothing.)

    Rewriting an identical record buys a fresher timestamp and pays with
    the first author's name. Not writing keeps both the attribution and,
    just as usefully, the bytes: a reviewer's confirming run now leaves
    `raw/` untouched, so a checksum sweep over the evidence still reads
    clean afterwards. That is why this is (a) — do not write — rather
    than appending the confirming agent to the record. Appending would
    preserve more, but it would also mutate the artifact under audit,
    and an integrity sweep cannot tell a benign append from tampering.

    An unreadable or non-conforming file is treated as a conflict. It
    cannot be shown to agree, and "could not check" must not resolve to
    "went ahead" — that is the shape of every fabrication this
    architecture is built to prevent.
    """
    if _overwrite_allowed or not path.exists():
        return True
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None

    try:
        records_agree = isinstance(existing, dict) and _comparable(
            existing
        ) == _comparable(record)
    except ArtifactError:
        records_agree = False

    if records_agree:
        if not suppress_warnings:
            previous = existing.get("written_by")
            when = existing.get("timestamp") or "an earlier run"
            mine = record.get("written_by")
            if previous and mine and previous != mine:
                output.warn(
                    f"{path.name} already holds an identical record by {previous} "
                    f"({when}); left as it is. Your run confirms it — cite "
                    f"{previous}'s record, and pass --out if you need your own copy."
                )
            else:
                output.warn(
                    f"{path.name} already holds an identical record ({when}); "
                    "not rewritten."
                )
            # Additive: warn when provenance stamps differ on an
            # otherwise-agreeing record so toolchain boundary crossings
            # (e.g. Hypex vendoring, env upgrades) are visible.
            stamp_changes = _volatile_stamp_changes(existing, record)
            if stamp_changes:
                _emit_volatile_stamp_warning(path, stamp_changes)
        return False

    if isinstance(existing, dict):
        previous = existing.get("written_by") or "an earlier run"
        when = existing.get("timestamp") or "unknown time"
        detail = f"the existing record was written by {previous} at {when}"
    else:
        detail = "the existing file could not be read as an analysis record"

    raise Refusal(
        f"{path.name} already holds a different analysis",
        detail=detail,
        remedy=(
            "write this verdict somewhere else with `--out "
            "raw/reanalysis/<date>-<agent>` and compare the two, or pass "
            "`--overwrite` if replacing the earlier record is what you mean. "
            "Two verdicts that disagree are evidence; one of them silently "
            "replaced is not"
        ),
    )


def read_json(path: Path, what: str = "artifact") -> Any:
    """Read a JSON artifact, failing loudly and specifically."""
    path = Path(path)
    if not path.is_file():
        raise ArtifactError(
            f"{what} not found: {path}",
            remedy="run the corresponding phase-1 subcommand first",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(
            f"{what} is not valid JSON: {path}", detail=str(exc)
        ) from exc
