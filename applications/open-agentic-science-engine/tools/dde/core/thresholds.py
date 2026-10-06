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

"""Named, versioned thresholds — the numbers that decide things.

Thresholds live in code, never as prose and never as inline literals at
a call site (docs/tool-design-guidance.md §7). Resolution is three
levels, most specific wins:

  1. defaults declared here, carrying a version tag
  2. program overrides in <project>/.dde/thresholds.yaml
  3. per-invocation override flags, recorded in the sidecar

Every `.analysis.json` names the `threshold_set` it applied, so a Layer 1
finding can cite the criterion and not merely the value.

UNRESOLVED thresholds
~~~~~~~~~~~~~~~~~~~~~
A threshold whose defensible value we do not have is declared as
``UNRESOLVED`` rather than filled with a plausible number. Requesting an
unresolved threshold raises `ThresholdError`, which turns "invented
cutoff" into "blocked task".

This is intentional and should be treated as a legitimate terminal
state (tool-design-guidance.md §7).  An UNRESOLVED threshold must only
be resolved when a citable, peer-reviewed or authoritative source
becomes available.  Do NOT fill in "reasonable-looking" numbers: a
plausible guess behind a negative claim about a drug target is the
precise failure mode this sentinel exists to prevent.

The ``ThresholdSet.unresolved()`` method lists which keys remain
UNRESOLVED, and ``dde doctor`` surfaces them at agent start so the
gap is visible without inventing a fix.  Programs that have their own
defensible cutoffs can supply them via ``.dde/thresholds.yaml``
without modifying this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ThresholdError
from .paths import is_safe_to_open

# Sentinel for a threshold that has no defensible default yet.
UNRESOLVED = object()


@dataclass
class ThresholdSet:
    """A named, versioned bundle of decision thresholds."""

    name: str
    version: str
    values: dict[str, Any]
    provenance: str
    overrides: dict[str, Any] = field(default_factory=dict)
    _sources: dict[str, str] = field(default_factory=dict)

    @property
    def tag(self) -> str:
        return f"{self.name}@{self.version}"

    def get(self, key: str) -> Any:
        if key in self.overrides:
            return self.overrides[key]
        if key not in self.values:
            raise ThresholdError(
                f"unknown threshold {key!r} in set {self.tag}",
                detail=f"declared thresholds: {', '.join(sorted(self.values))}",
            )
        value = self.values[key]
        if value is UNRESOLVED:
            raise ThresholdError(
                f"threshold {key!r} in set {self.tag} has no defensible default value",
                detail=(
                    "This cutoff has not been established from a cited source. "
                    "The CLI will not substitute a plausible number."
                ),
                remedy=(
                    f"set it explicitly in <project>/.dde/thresholds.yaml under "
                    f"'{self.name}: {{{key}: <value>}}', or pass the corresponding "
                    "override flag, and record the justification"
                ),
            )
        return value

    def applied(self) -> dict[str, Any]:
        """Thresholds actually resolvable, for the .analysis.json record."""
        out: dict[str, Any] = {}
        for key, value in self.values.items():
            if key in self.overrides:
                out[key] = self.overrides[key]
            elif value is not UNRESOLVED:
                out[key] = value
        return out

    def unresolved(self) -> list[str]:
        return sorted(
            k
            for k, v in self.values.items()
            if v is UNRESOLVED and k not in self.overrides
        )

    def sources(self) -> dict[str, str]:
        """Where each applied threshold came from: default | program | flag."""
        out = {}
        for key in self.applied():
            if key in self.overrides:
                out[key] = "flag"
            else:
                out[key] = self._sources.get(key, "default")
        return out


# ---------------------------------------------------------------------------
# Declared threshold sets
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, ThresholdSet] = {}


def _declare(name: str, version: str, provenance: str, values: dict[str, Any]) -> None:
    _DEFAULTS[name] = ThresholdSet(
        name=name, version=version, values=values, provenance=provenance
    )


# --- AlphaFold DB (pLDDT + PAE) -------------------------------------------
# Values carried over from the science-skills reference implementation
# (analyze_plddt.py / analyze_pae.py module constants), which is the
# provenance we can actually cite for them.
_declare(
    "alphafold",
    "1.0",
    provenance=(
        "science-skills alphafold_database_fetch_and_analyze: "
        "analyze_plddt.py and analyze_pae.py module constants"
    ),
    values={
        # pLDDT — fractions of residues, 0..1
        "plddt_confident": 0.7,
        "plddt_moderate": 0.4,
        "disorder_notable": 0.15,
        "disorder_mixed": 0.3,
        "disorder_mostly": 0.5,
        # PAE — angstroms and residue counts
        "pae_domain_cutoff": 7.0,
        "pae_min_domain_size": 40,
        "pae_merge_cutoff": 15.0,
        "pae_min_global_domain": 50,
        "pae_confident_pair": 5.0,
    },
)

# --- AlphaFold 3 (Vertex endpoint prediction confidence) -------------------
# ipTM/pTM bands are the DeepMind-published interpretation guidance for
# AF-Multimer/AF3 confidence; ranking_score is defined by the container.
_declare(
    "af3",
    "1.0",
    provenance=(
        "DeepMind AlphaFold3 published confidence guidance (ipTM/pTM bands); "
        "ranking_score formula per af3-toolkit notes.md §5"
    ),
    values={
        "iptm_confident": 0.8,
        "iptm_borderline": 0.6,
        "ptm_confident": 0.5,
        "plddt_confident": 70.0,  # AF3 returns pLDDT on a 0-100 scale
        "plddt_low": 50.0,
        "fraction_disordered_notable": 0.3,
    },
)

# --- Structural homology search --------------------------------------------
# Sequence identity bands for interpreting BLAST hits against the PDB.
# The 30% identity boundary is where fold conservation becomes unreliable
# (Rost 1999); the 50% band above which backbone is typically conserved
# is from Chothia & Lesk 1986. Resolution bands are operational
# conventions (CCP4) with no single-paper cutoff. Coverage minimum is
# operational.
_declare(
    "homology",
    "1.0",
    provenance=(
        "Sequence identity bands: Rost, Protein Engineering 1999;12:85-94 "
        "('Twilight zone of protein sequence alignments') — 30% identity as "
        "the boundary below which fold conservation becomes unreliable; "
        "Sander & Schneider, Proteins 1991;9:56-68 for the length-dependent "
        "identity curve. >50% band from Chothia & Lesk, EMBO J 1986;5:823-826 "
        "(structural similarity as a function of sequence identity). "
        "Resolution bands: no single published cutoff exists; 2.5 Å and 3.5 Å "
        "are operational boundaries widely used in structural biology practice "
        "(CCP4 data processing conventions). "
        "Coverage fraction: operational minimum, no external source."
    ),
    values={
        "identity_high": 0.5,
        "identity_moderate": 0.3,
        "resolution_high": 2.5,
        "resolution_low": 3.5,
        "coverage_minimum": 0.5,
    },
)

# --- Co-scientist tournament ----------------------------------------------
# These govern how a tournament result is read, not what is biologically
# true. Defaults are structural (ELO is a relative ranking statistic) and
# deliberately conservative.
_declare(
    "coscientist",
    "1.1",
    provenance=(
        "structural defaults for reading an ELO tournament; "
        "no external source claims these cutoffs"
    ),
    values={
        # A leader is only 'clear' if it beats the runner-up by this margin.
        "elo_decisive_gap": 50.0,
        # Below this win rate an idea's rank is not well supported.
        "min_win_rate": 0.5,
        # Number of contradicted deep-verification claims above which the
        # idea is flagged regardless of rank. This is a count, not a
        # fraction: see COSCIENTIST_CLAIM_DENOMINATOR below.
        "max_contradicted_claims": 0,
        # Minimum matches for a ranking to be considered stable.
        "min_matches": 5,
    },
)

# In every export inspected to date (2026-08-18, two exports of the same
# tournament, 16 claims across 5 ideas), 100% of the claims listed under
# `deepVerification` carried INACCURATE or LEANING_INACCURATE. No claim
# has ever been observed with a passing verdict.
#
# The list is therefore an exception report, not a census: it appears to
# contain only the claims the verifier took issue with. A ratio computed
# over it has a denominator of "claims already known to be disputed" and
# is always ~1.0 — which is why v1.0's `max_inaccurate_fraction` was
# removed rather than retuned. We count contradicted claims instead and
# relay this caveat into every analysis.
COSCIENTIST_CLAIM_DENOMINATOR = (
    "deepVerification lists only claims the verifier disputed; in all exports "
    "observed to date every listed claim carried an inaccurate verdict. Read "
    "n_contradicted_claims as an absolute count of flagged claims, NOT as a "
    "share of the idea's claims. The idea's total claim count is not present "
    "in the export and cannot be inferred."
)

# --- Hypothesis set (adopted / charter) ------------------------------------
# Structural thresholds only — nothing about an adopted set licenses a
# quality threshold.  The set validates that something was adopted; it
# does not claim anything about the hypotheses' merit.
_declare(
    "hypothesis-set",
    "1.0",
    provenance=(
        "Structural thresholds for hypothesis sets adopted from external "
        "sources. No quality threshold — nothing about an adopted set "
        "licenses one."
    ),
    values={
        "min_candidates": 1,
    },
)

# --- hypex tournament -----------------------------------------------------
# These govern how a hypex tournament result is read, not what is
# biologically true. Three of six thresholds are UNRESOLVED, which is
# the honest state.  MarginMultiplier's undocumented 0.75 damping puts
# hypex ELO on a different scale from Co-Scientist, so elo_decisive_gap
# must not inherit 50.0 by analogy.
_declare(
    "hypex",
    "1.0",
    provenance=(
        "structural defaults for reading a hypex ELO tournament; "
        "no external source claims these cutoffs. "
        "elo_decisive_gap, max_suspect_citations and min_safety_score "
        "are UNRESOLVED — measure the distribution before filling them"
    ),
    values={
        # Below this a pairwise ranking is unsupported.
        "min_matches": 5,
        # Below this win rate a hypothesis's rank is not well supported.
        "min_win_rate": 0.5,
        # A count of phantom citations.
        "max_phantom_citations": 0,
        # Deliberately not 50.0 — MarginMultiplier damping puts hypex
        # ELO on a different scale than Co-Scientist.
        "elo_decisive_gap": UNRESOLVED,
        # No corpus has been measured.
        "max_suspect_citations": UNRESOLVED,
        # 1-5 anchors are LLM-reviewer judgements; no basis for cutoff.
        "min_safety_score": UNRESOLVED,
    },
)

# --- AlphaGenome variant effect -------------------------------------------
# Raw-score magnitude bands are quoted directly from the science-skills
# interpretation guide (docs/interpretation-guide.md, "Magnitude Rules").
# That guide carries an explicit disclaimer that these are rules of thumb
# derived primarily from RNA-seq; MANDATORY_ADVISORIES below is relayed
# into every analysis so the caveat travels with the number.
#
# Note on quantile_significance: the guide states "a quantile of 0.99+
# with |raw_score| < 0.1 is effectively NO MOLECULAR EFFECT" and its
# artifact table cites >0.999. It does not state 0.995. We take the
# documented 0.99 rather than an interpolated value.
_declare(
    "alphagenome-variant-effect",
    "1.0",
    provenance=(
        "science-skills alphagenome_single_variant_analysis "
        "docs/interpretation-guide.md, sections 'Magnitude Rules' and "
        "'High Quantile + Low Raw Score'"
    ),
    values={
        "raw_score_no_effect": 0.1,
        "raw_score_subtle": 0.5,
        "raw_score_moderate": 1.0,
        "quantile_significance": 0.99,
        # |quantile| above significance with |raw| below no_effect is the
        # documented statistical artifact, not a biological finding.
        "artifact_raw_ceiling": 0.1,
    },
)

# --- AlphaGenome ISM ------------------------------------------------------
# The interpretation guide gives a qualitative floor ("only tiny bars
# (<0.1 height) or random noise -> No Significant Effect") but no
# motif-calling cutoff. The values we do not have are declared UNRESOLVED
# so `analyze-ism` refuses a verdict rather than inventing one.
_declare(
    "alphagenome-ism",
    "0.1-draft",
    provenance=(
        "noise floor from interpretation-guide.md ISM section; "
        "motif-calling cutoffs have NO cited source"
    ),
    values={
        "ism_noise_floor": 0.1,
        "motif_significance": UNRESOLVED,
        "position_importance": UNRESOLVED,
    },
)

# --- Human Protein Atlas expression ---------------------------------------
# Both resolved values are HPA's own published definitions, quoted from
# proteinatlas.org/humanproteome/tissue/tissue+specific:
#
#   "Detected in tissue when nTPM>=1"
#   "At least four-fold higher mRNA level in a particular tissue/cell type
#    compared to any other tissues/cell types."  (Tissue enriched)
#   "...compared to the average level in all other tissues/cell types."
#    (Tissue enhanced)
#
# Using HPA's numbers rather than our own means a recomputed
# classification can be checked against the curated label HPA ships in
# the same record — `analyze` does exactly that and warns on
# disagreement, which is a live check that this list has not drifted.
#
# low_confidence_ntpm is UNRESOLVED on purpose. A band around the
# detection cutoff, inside which a value should not be read as a
# confident call, is a real need — 1.0 nTPM and 50 nTPM are not the same
# evidence for "expressed" — but HPA publishes no such band and we have
# no other citable source. Picking 2 or 3 because it looks sensible
# would put an invented number behind a negative claim about a drug
# target, which is the precise failure this pilot exists to catch.
# `analyze` therefore reports near-cutoff values without a margin and
# says so in its warnings.
_declare(
    "expression",
    "1.0",
    provenance=(
        "Human Protein Atlas published RNA tissue specificity definitions "
        "(proteinatlas.org/humanproteome/tissue/tissue+specific): detected at "
        "nTPM>=1, enriched/enhanced at >=4-fold. No HPA-documented "
        "near-threshold confidence band exists."
    ),
    values={
        "expressed_ntpm": 1.0,
        "enriched_fold": 4.0,
        "low_confidence_ntpm": UNRESOLVED,
    },
)

# --- HPA single-cell RNA expression (nCPM) ---------------------------------
# HPA publishes single-cell RNA expression at cell-type resolution, with
# the same four-fold enrichment criteria used for tissue data:
#
#   proteinatlas.org/humanproteome/single+cell/single+cell+type:
#   "Detected in at least one cell type (nCPM > 1)"
#   "At least four-fold higher mRNA levels in a particular cell type as
#    compared to all other cell types."  (Cell type enriched)
#   "At least four-fold higher mRNA levels in a particular cell type as
#    compared to average levels in all cell types."  (Cell type enhanced)
#
# The detection comparison is strict (nCPM > 1) rather than the tissue
# convention (nTPM >= 1). The threshold value is 1.0 in both; the
# inequality operator differs and is handled in the command, not here.
_declare(
    "expression-single-cell",
    "1.0",
    provenance=(
        "Human Protein Atlas single-cell RNA specificity definitions "
        "(proteinatlas.org/humanproteome/single+cell/single+cell+type): "
        "detected at nCPM > 1, enriched/enhanced at >=4-fold. Same fold "
        "criterion as the tissue threshold set."
    ),
    values={
        "expressed_ncpm": 1.0,
        "enriched_fold": 4.0,
        "low_confidence_ncpm": UNRESOLVED,
    },
)

# --- DICE immune cell expression (bulk RNA-seq, TPM) ----------------------
# DICE provides bulk RNA-seq TPM across 15 immune cell types but publishes
# no cutoffs for classifying expression levels. Like GTEx, the raw TPM
# values are reported without a threshold verdict unless program-level
# overrides supply defensible cutoffs.
#
# The data is from February 2022 and the database appears to be in
# maintenance mode (La Jolla Institute for Immunology).
_declare(
    "dice-expression",
    "1.0",
    provenance=(
        "DICE (Database of Immune Cell Expression, dice-database.org), "
        "La Jolla Institute for Immunology. Bulk RNA-seq TPM from sorted "
        "human immune cell populations (15 cell types). No expression-level "
        "cutoffs are published by DICE. All values are UNRESOLVED."
    ),
    values={
        # Minimum TPM to consider a gene "expressed" in an immune cell type.
        # DICE publishes no such cutoff.
        "expressed_tpm": UNRESOLVED,
        # TPM level above which expression is considered "high".
        # No published source defines this for DICE data.
        "high_expression_tpm": UNRESOLVED,
        # TPM level below which a non-zero value is considered "low".
        # No published source defines this for DICE data.
        "low_expression_tpm": UNRESOLVED,
    },
)

# --- GTEx expression (whole-blood, TPM) ------------------------------------
# GTEx publishes median TPM from bulk RNA-seq but defines no cutoffs for
# classifying expression levels as high, medium, low, or not expressed.
# Unlike HPA (which publishes nTPM >= 1 as "detected"), GTEx provides no
# analogous threshold. No peer-reviewed source establishes universal TPM
# cutoffs for GTEx whole-blood data.
#
# All values are UNRESOLVED. This is the correct terminal state per
# tool-design-guidance.md §7: "An unresolved threshold is a legitimate
# terminal state. Where a source publishes no cutoff for a quantity, the
# tool must not supply one."
#
# Declaring the set despite all values being UNRESOLVED serves two
# purposes:
#   1. Program-level overrides via .dde/thresholds.yaml can fill in
#      site-specific values when a program has its own defensible cutoffs.
#   2. The analysis artifact records the threshold set name and its
#      unresolved status, making the absence of classification explicit
#      and machine-readable rather than implicit.
#
# Cross-platform comparison — GTEx TPM vs HPA nTPM:
#   GTEx reports median TPM from bulk RNA-seq of 755 whole-blood samples
#   (femoral/subclavian veins). HPA reports consensus nTPM (normalized
#   Transcripts Per Million) from its own pipeline across ~50 tissues.
#   The two units are not directly comparable: nTPM applies HPA-specific
#   normalization that adjusts for transcript length and library
#   composition differently from standard TPM. A gene at 5.0 TPM in GTEx
#   and 5.0 nTPM in HPA is not the same measurement, and blending them
#   into a single "expression level" would manufacture a composite that
#   neither source published.
_declare(
    "gtex-expression",
    "1.0",
    provenance=(
        "GTEx Portal (gtexportal.org), dataset gtex_v8. GTEx publishes "
        "median TPM from bulk RNA-seq but defines no expression-level "
        "cutoffs. No peer-reviewed source establishes universal TPM "
        "thresholds for classifying GTEx whole-blood expression. The "
        "Human Protein Atlas (HPA) uses nTPM with its own cutoffs "
        "(nTPM >= 1 for 'detected'), but nTPM and TPM are not directly "
        "comparable — different normalization, different tissue sets, "
        "different pipelines. All values are UNRESOLVED."
    ),
    values={
        # Minimum TPM to consider a gene "expressed" in whole blood.
        # GTEx publishes no such cutoff; HPA's nTPM >= 1 does not
        # transfer because the units differ.
        "expressed_tpm": UNRESOLVED,
        # TPM level above which expression is considered "high".
        # No published source defines this for GTEx whole-blood data.
        "high_expression_tpm": UNRESOLVED,
        # TPM level below which a non-zero value is considered "low".
        # No published source defines this for GTEx whole-blood data.
        "low_expression_tpm": UNRESOLVED,
    },
)

# --- Compound-registry identity resolution ---------------------------------
# compreg makes no scientific judgment, so it has no scientific cutoff.
# The ambiguity contract — more than one match is ambiguous — is
# hardcoded rather than configurable, exactly as in litref.  The set is
# declared empty so the analysis artifact carries a well-formed
# threshold_set tag and program-level overrides can be added later if a
# meaningful threshold emerges.
_declare(
    "compreg",
    "1.0",
    provenance=(
        "compound-registry identity resolution has no scientific cutoffs; "
        "the ambiguity contract (>1 match = ambiguous) is hardcoded"
    ),
    values={},
)

# --- PubChem compound annotation -------------------------------------------
# Like compreg, annotation makes categorical verdicts (known-drug /
# known-compound / unknown) based on presence of drug development data;
# no numeric cutoffs. The set is declared empty so the analysis artifact
# carries a well-formed threshold_set tag.
_declare(
    "pubchem-annotation",
    "1.0",
    provenance=(
        "PubChem compound annotation makes categorical verdicts (known-drug / "
        "known-compound / unknown) based on presence of drug development data; "
        "no numeric cutoffs"
    ),
    values={},
)

# --- Structure similarity search -------------------------------------------
# ECFP/Morgan fingerprint standard practice for Tanimoto similarity.
# 0.85 is a common cutoff for "similar compounds"; exact match is 1.0.
# Novelty threshold is program-specific and left UNRESOLVED — "how
# similar is too similar?" is a decision that belongs in the program,
# not in a default value.
_declare(
    "similar-search",
    "1.0",
    provenance=(
        "ECFP/Morgan fingerprint standard practice for Tanimoto similarity. "
        "0.85 is a common cutoff; novelty threshold is program-specific"
    ),
    values={
        "tanimoto_similarity_cutoff": 0.85,
        "exact_match_cutoff": 1.0,
        "novelty_threshold": UNRESOLVED,
    },
)

# --- Citation resolution ---------------------------------------------------
# litref makes no scientific judgment, so it has no scientific cutoff.
# The one number it needs is operational: how many matching records to
# enumerate per query. It is declared here rather than inlined because
# it changes what a reader may conclude — beyond the cap the match list
# stops being a census and becomes a sample, and `analyze` says so.
#
# 25 is a page size, not a finding. Ambiguity is decided from the
# registry's own reported hit count, which is not capped, so raising or
# lowering this changes how much detail is recorded and never changes
# resolved / ambiguous / not_found.
_declare(
    "litref",
    "1.0",
    provenance=(
        "operational page size for record enumeration; not a scientific "
        "cutoff and not claimed by any external source"
    ),
    values={
        "max_matches_recorded": 25,
    },
)

# --- gnomAD constraint ------------------------------------------------------
# Every resolved value is gnomAD's own published recommendation, quoted
# from gnomad.broadinstitute.org/help/constraint (source of record:
# broadinstitute/gnomad-browser browser/help/topics/constraint.md):
#
#   "Since `pLI` > 0.9 is widely used in research and clinical
#    interpretation of Mendelian cases, we suggest using the upper bound
#    of the `oe` confidence interval (which we term ... `LOEUF`) < 0.45
#    if a hard threshold is needed."
#
#   "Intermediate `pLI` scores (0.1-0.9) are typically an indication that
#    the gene was too small to be confidently categorized."
#
# The LOEUF cutoff is 0.45 and not the widely-quoted 0.35. 0.35 is the
# gnomAD v2 figure, and the current documentation says o/e values are
# higher in v4 and that "any `LOEUF` thresholds used on v2 will not give
# an equivalent number of genes when applied to v4". Carrying 0.35
# forward would misclassify genes near the boundary — any gene with
# LOEUF between 0.35 and 0.45 changes classification.
#
# pli_indeterminate_floor is the lower edge of gnomAD's documented
# too-small-to-categorise band. It is what `gnomad.constraint_unreliable`
# fires on, which means the unreliability call rests on a cited
# criterion rather than on a gene-size rule of thumb.
#
# loeuf_unreliable_min_expected_lof is UNRESOLVED. A minimum expected-LoF
# count below which LOEUF should be distrusted is a real and widely-used
# idea, but gnomAD publishes no such number — it says instead that "it is
# essential to take the 90% CI into consideration". `analyze` therefore
# reports exp_lof and the full CI and declines to threshold on them.
_declare(
    "gnomad-constraint",
    "1.0",
    provenance=(
        "gnomAD published constraint guidance, gnomad.broadinstitute.org/help/"
        "constraint (gnomad-browser browser/help/topics/constraint.md): pLI > 0.9 "
        "for LoF intolerance, LOEUF < 0.45 as the v4 hard threshold, intermediate "
        "pLI 0.1-0.9 as too small to categorise. No expected-LoF floor is published."
    ),
    values={
        "lof_intolerant_pli": 0.9,
        "pli_indeterminate_floor": 0.1,
        "loeuf_constrained": 0.45,
        "loeuf_unreliable_min_expected_lof": UNRESOLVED,
    },
)

# --- fpocket druggability ---------------------------------------------------
# The drug score is Schmidtke & Barril's logistic model over pocket
# descriptors, trained to separate druggable from non-druggable sites;
# it returns a value in [0,1] and 0.5 is its decision boundary. The
# borderline floor below it is ours and is labelled as such — the paper
# defines one boundary, not a band, and inventing a citation for the
# second number would be worse than owning it.
#
# `volume_estimate_tolerance` is measured, not cited. fpocket computes
# pocket volume by Monte Carlo integration seeded from the clock and
# offers no seed flag, so the same input gives different volumes on
# different runs. Five runs over 1UYD on 2026-08-18 spanned 1415.9-1448.6
# Å3, a 2.3% range; 0.05 is that rounded up. Two runs whose volumes
# differ by less than this have not disagreed about anything.
#
# Consecutive runs inside the same second return identical numbers,
# because the seed is time(NULL) at one-second resolution. A
# reproducibility check run as a tight loop therefore reports perfect
# determinism, which is the trap this note exists to spring.
#
# `druggable_dscore` is a real boundary that cannot be read as a verdict
# about a target, and the difference matters more here than the number
# does. Measured on 2026-08-18 against four crystal structures of two
# sites that are drugged in the clinic:
#
#     CDK2 ATP site   1HCK (ATP)            0.939
#                     2W1D (inhibitor)      0.293
#                     1AQ1 (staurosporine)  0.172
#     HSP90 ATP site  1UYD (inhibitor)      0.855
#
# One site, three CDK2 structures, and a 0.77 spread that crosses the
# boundary twice. The score is dominated by the conformation it was
# measured in, so a single structure scoring under 0.5 is evidence about
# that structure and nothing more. This is why a sub-cutoff verdict
# leaves `dde pocket analyze` with a mandatory relay attached
# (`fpocket.single_conformation`) and why the verdict itself is named
# `no-druggable-pocket-in-this-conformation`: the qualifier has to be
# carried by the machine-readable field, because the one place it will
# not survive is a summary written by someone who wanted a yes.
#
# Read the cutoff as a ranking aid across conformations of one target,
# or across candidate sites in one structure. Do not read it as a gate
# on whether a target is tractable.
_declare(
    "pocket",
    "1.0",
    provenance=(
        "Schmidtke & Barril, J Med Chem 2010;53(15):5858-67, 'Understanding "
        "and predicting druggability: a high-throughput method for detection "
        "of drug binding sites' — the fpocket drug score and its 0.5 decision "
        "boundary. min_alpha_spheres is fpocket 4.2.2's own detection default "
        "(-i). borderline_dscore and volume_estimate_tolerance are local: see "
        "the module comment above for how each was arrived at."
    ),
    values={
        # Drug score at or above which the model calls a pocket druggable.
        "druggable_dscore": 0.5,
        # Below this, the model is not equivocal — it is saying no.
        "borderline_dscore": 0.2,
        # fpocket's own floor for calling a cluster of alpha spheres a
        # pocket at all. Restated here so an analysis can say whether a
        # pocket sits at the detection limit.
        "min_alpha_spheres": 15,
        # Heavy-atom distance within which a pocket residue counts as
        # part of a named site. 5.0 Å is the common contact cutoff for
        # interface definitions; pocket membership here is by residue
        # identity, so this governs only the neighbourhood report.
        "contact_distance": 5.0,
        # Fractional volume difference below which two runs agree.
        "volume_estimate_tolerance": 0.05,
    },
)

# --- compound descriptors (drug-likeness) ---------------------------------
# Lipinski Rule of Five (Lipinski et al., "Experimental and computational
# approaches to estimate solubility and permeability in drug discovery and
# development settings", Adv Drug Deliv Rev 2001;46:3-26): MW <= 500,
# LogP ≤ 5, HBD ≤ 5, HBA ≤ 10.
#
# Veber criteria (Veber et al., "Molecular properties that influence the
# oral bioavailability of drug candidates", J Med Chem 2002;45:2615-23):
# rotatable bonds ≤ 10, TPSA ≤ 140.
#
# One Lipinski violation is tolerated ("Rule of Five" admits one); two or
# more is a red flag.  Veber criteria are stricter — any violation counts.
_declare(
    "compound-descriptors",
    "1.0",
    provenance=(
        "Lipinski et al., Adv Drug Deliv Rev 2001;46:3-26 (MW, LogP, HBD, "
        "HBA); Veber et al., J Med Chem 2002;45:2615-23 (rotatable bonds, "
        "TPSA)"
    ),
    values={
        "max_mw": 500,
        "max_logp": 5,
        "max_hbd": 5,
        "max_hba": 10,
        "max_rotatable_bonds": 10,
        "max_tpsa": 140,
    },
)

# --- compound SA-score (synthetic accessibility) --------------------------
# Ertl & Schuffenhauer, "Estimation of Synthetic Accessibility Score of
# Drug-like Molecules based on Molecular Complexity and Fragment
# Contributions", J Cheminformatics 2009;1:8.  The score runs 1-10
# (1 = easy, 10 = hard).  The <= 6 cutoff is a widely used community
# convention in virtual screening literature for filtering synthetically
# inaccessible compounds; it is not a single-paper citation.
_declare(
    "compound-sa-score",
    "1.0",
    provenance=(
        "Ertl & Schuffenhauer, J Cheminformatics 2009;1:8 (score definition "
        "and distribution analysis). The <= 6 accessibility cutoff is a widely "
        "used community convention in virtual screening literature, not a "
        "single-paper citation."
    ),
    values={
        "max_sa_score": 6.0,
    },
)

# --- compound alerts (severity classification) ----------------------------
# Operational defaults for weighting structural alert types.  PAINS
# (Baell & Holloway, J Med Chem 2010) detect pan-assay interference and
# warrant exclusion; Brenk (Brenk et al., ChemMedChem 2008) flag
# undesirable substructures — a couple are tolerated in early-stage
# screening. Aggregators cause non-specific interference and warrant
# exclusion like PAINS.
_declare(
    "compound-alerts",
    "1.0",
    provenance=(
        "operational defaults for alert classification; PAINS weighting from "
        "Baell & Holloway, J Med Chem 2010;53:2719-40; Brenk tolerance from "
        "Brenk et al., ChemMedChem 2008;3:435-44"
    ),
    values={
        "max_pains_hits": 0,  # any PAINS hit is a concern
        "max_brenk_hits": 2,  # a few Brenk features are tolerated
        "max_aggregator_hits": 0,  # any aggregator pattern is a concern
    },
)

# --- assay activity (dose-response and activity cutoffs) -------------------
# Activity cutoffs and curve-fitting quality thresholds are program-specific
# and have no universal default in standard HTS literature.  All are declared
# UNRESOLVED so the CLI refuses to substitute a plausible number — the
# operator must set them via .dde/thresholds.yaml and record a
# justification.
_declare(
    "assay-activity",
    "1.0",
    provenance=(
        "program-specific cutoffs with no universal default; must be set "
        "via .dde/thresholds.yaml"
    ),
    values={
        "activity_cutoff_inhibition": UNRESOLVED,
        "activity_cutoff_fold_change": UNRESOLVED,
        "hill_slope_min": UNRESOLVED,
        "hill_slope_max": UNRESOLVED,
        "r_squared_floor": UNRESOLVED,
    },
)

# --- assay screen quality (Z-factor) --------------------------------------
# Z-factor thresholds from Zhang, Chung & Oldenburg, "A Simple Statistical
# Parameter for Use in Evaluation and Validation of High Throughput Screening
# Assays", J Biomol Screen 1999;4(2):67-73.
#
# Z' >= 0.5 is an excellent assay; 0 <= Z' < 0.5 is acceptable but marginal;
# Z' < 0 means the signal and background distributions overlap and the screen
# is unusable.
_declare(
    "assay-screen-quality",
    "1.0",
    provenance=(
        "Zhang, Chung & Oldenburg, 'A Simple Statistical Parameter for Use "
        "in Evaluation and Validation of High Throughput Screening Assays', "
        "J Biomol Screen 1999;4(2):67-73"
    ),
    values={
        "z_factor_excellent": 0.5,
        "z_factor_acceptable": 0.0,
    },
)

# --- selectivity margins ----------------------------------------------------
# hERG safety margin from ICH S7B, "The Non-Clinical Evaluation of the
# Potential for Delayed Ventricular Repolarization (QT Interval
# Prolongation) by Human Pharmaceuticals" (adopted 2005).  The guideline
# recommends a ≥30-fold safety margin between the therapeutic free plasma
# concentration and the IC50 for hERG channel inhibition.  This is the
# widely accepted regulatory standard.
#
# All other off-target selectivity margins are UNRESOLVED — there are no
# universally citable conventions.  This is correct per
# tool-design-guidance.md §7 ("An unresolved threshold is a legitimate
# terminal state").
_declare(
    "selectivity-margins",
    "1.0",
    provenance=(
        "ICH S7B, 'The Non-Clinical Evaluation of the Potential for Delayed "
        "Ventricular Repolarization (QT Interval Prolongation) by Human "
        "Pharmaceuticals' (adopted 2005): recommends a ≥30-fold safety margin "
        "between the therapeutic free plasma concentration and the IC50 for "
        "hERG channel inhibition. herg_marginal is a local operational boundary "
        "(herg_margin / 3), not a cited value — see pocket's borderline_dscore "
        "for precedent. No universally citable conventions exist for "
        "other off-target selectivity margins."
    ),
    values={
        "herg_margin": 30.0,
        "herg_marginal": 10.0,
    },
)

# --- docking scores (AutoDock Vina) ----------------------------------------
# Vina binding energy thresholds: values are predicted binding free
# energies in kcal/mol (more negative = stronger predicted binding).
# No universally accepted cutoffs exist for classifying Vina scores;
# the values here are from commonly cited benchmarking studies.
_declare(
    "mmp-cliffs",
    "1.0",
    provenance=(
        "Property-cliff detection thresholds for matched molecular pair analysis. "
        "Minimum pair count from Hussain & Rea, J Chem Inf Model 2010;50:339-348, "
        "who recommend caution with small pair counts but do not prescribe a specific "
        "minimum. cliff_absolute_delta is a per-property dict, not a universal "
        "scalar, because a series file's properties dict is unconstrained — "
        "potency (log-scale, deltas of 0-3), solubility (µg/mL, deltas of "
        "hundreds), and molecular weight (deltas of tens) cannot share one "
        "number. Potency-property thresholds (pic50, pki) are set to 1.0 log "
        "unit (10-fold potency difference), the conservative end of the 0.5-1.0 "
        "range cited in the activity-cliff literature: Stumpfe & Bajorath, "
        "J Med Chem 2012;55:2932-2942; Cruz-Monteagudo et al., Drug Discov "
        "Today 2014;19:1069-1080. Properties not in the dict are not evaluated "
        "for cliffs — this is explicit in the analysis output, not silent. "
        "Override via .dde/thresholds.yaml to add properties or adjust "
        "thresholds for a specific program."
    ),
    values={
        # Minimum matched pairs for a transformation to be reported with confidence.
        # Hussain & Rea do not prescribe a number; this is a conservative operational
        # default. Mark UNRESOLVED if you prefer to force explicit program config.
        "min_pair_count": 3,
        # Property-cliff threshold: a per-property dict keyed by recognized
        # property name (lowercase). A "cliff" for a given property is a
        # transformation where |delta| >= the configured threshold.
        #
        # Only properties with a literature-defensible threshold are included.
        # pic50 and pki (negative-log potency measures) use the activity-cliff
        # convention: 1.0 log unit = 10-fold potency difference (Stumpfe &
        # Bajorath 2012). Properties absent from this dict are not evaluated
        # for cliffs and produce an explicit advisory — never a silent skip.
        #
        # Property names are matched case-insensitively: a series file using
        # "pIC50" or "PIC50" resolves against the "pic50" entry here.
        #
        # Override with a dict in .dde/thresholds.yaml to add program-
        # specific properties (e.g. logd, solubility on a defined scale).
        "cliff_absolute_delta": {
            "pic50": 1.0,
            "pki": 1.0,
        },
    },
)

# --- PK parameters -----------------------------------------------------------
# AUC extrapolation limit from FDA Guidance for Industry: Safety Testing
# of Drug Metabolites (2020 revision). DDI risk classification R-value
# cutoffs from FDA In Vitro Drug Interaction Studies guidance (2020).
# Scaling confidence classes are operational defaults based on
# Mahmood & Balian 1996 rule of exponents. Therapeutic exposure adequacy
# is program-specific with no universal default.
_declare(
    "pk-parameters",
    "1.0",
    provenance=(
        "FDA Guidance for Industry: Safety Testing of Drug Metabolites "
        "(2020 revision), AUC extrapolation limit. FDA In Vitro Drug "
        "Interaction Studies guidance (2020) R-value cutoffs for DDI risk "
        "classification. Scaling confidence classes are operational defaults "
        "based on Mahmood & Balian 1996 rule of exponents. Therapeutic "
        "exposure adequacy is program-specific with no universal default."
    ),
    values={
        # Scaling confidence: single-species vs multi-species
        "scaling_confidence_single": "low",
        "scaling_confidence_multi": "moderate",
        # AUC extrapolation percentage limit — >20% is unreliable per FDA guidance
        "auc_extrapolation_limit": 20.0,
        # DDI R-value cutoffs — 3-tier classification per FDA 2020 guidance.
        # No intermediate "no interaction" tier: the FDA guidance does not define
        # a lower bound below which R is considered noise.
        "ddi_r_possible_interaction": 1.25,
        "ddi_r_clinical_study": 2.0,
        # Therapeutic exposure adequacy — program-specific, no universal default
        "therapeutic_exposure_adequacy": UNRESOLVED,
    },
)

# --- tox safety package (preclinical toxicology) ----------------------------
# Therapeutic index (TI) convention: NOAEL exposure / efficacious exposure
# must be ≥ 10-fold. This is a widely used preclinical safety margin
# convention referenced in ICH M3(R2) general guidance for starting dose
# selection and safety margin assessment.
#
# hERG safety margin: the same ≥30-fold criterion as selectivity-margins,
# declared independently to avoid coupling the tox and selectivity tools
# (see design doc OQ1). ICH S7B recommends ≥30-fold between therapeutic
# free plasma Cmax and hERG IC50.
#
# hERG marginal: an operational boundary at hERG safety margin / 3,
# following the selectivity-margins precedent. Below the full 30-fold
# margin but above the marginal threshold indicates reduced concern;
# below the marginal threshold is elevated concern.
#
# noael_exposure_margin: a minimum acceptable NOAEL exposure in absolute
# terms is entirely program-specific — no universal default exists. This
# is UNRESOLVED and must be set via .dde/thresholds.yaml per program.
_declare(
    "tox-safety-package",
    "1.0",
    provenance=(
        "ICH M3(R2) general guidance for therapeutic index convention "
        "(≥10-fold safety margin between NOAEL exposure and therapeutic "
        "exposure). ICH S7B for hERG safety margin (≥30-fold between "
        "therapeutic free plasma Cmax and hERG IC50, adopted 2005). "
        "herg_marginal is a local operational boundary (herg_safety_margin / 3), "
        "following the selectivity-margins precedent."
    ),
    values={
        "ti_minimum": 10.0,
        "herg_safety_margin": 30.0,
        "herg_marginal": 10.0,
        "noael_exposure_margin": UNRESOLVED,
    },
)

# --- screening pre-filter (descriptor-based compute-saving heuristic) -----
# Lipinski Rule of Five defaults (Lipinski et al., Adv Drug Deliv Rev
# 2001;46:3-26) plus Veber rotatable-bond and TPSA limits (Veber et al.,
# J Med Chem 2002;45:2615-23). Used as a compute-saving heuristic, not
# a pharmacological assessment -- see relay
# screening.prefilter_excludes_not_rejects.
#
# These defaults mirror the existing compound-descriptors threshold set.
# They are independently declared because: (a) screening pre-filter serves
# a different purpose (compute-saving skip vs. drug-likeness assessment),
# (b) users may want relaxed screening thresholds without affecting
# compound analysis, (c) programs can override them independently via
# .dde/thresholds.yaml.
_declare(
    "screening-prefilter",
    "1.0",
    provenance=(
        "Lipinski et al., Adv Drug Deliv Rev 2001;46:3-26 (MW, LogP, HBD, "
        "HBA); Veber et al., J Med Chem 2002;45:2615-23 (rotatable bonds, "
        "TPSA). Used as a compute-saving heuristic, not a pharmacological "
        "assessment."
    ),
    values={
        "max_mw": 500,
        "max_logp": 5,
        "max_hbd": 5,
        "max_hba": 10,
        "max_tpsa": 140,
        "max_rotatable_bonds": 10,
    },
)

# --- Citation verification ------------------------------------------------
# Token-overlap boundaries for title matching in citation verification.
# These are structural defaults observed in a reference implementation of
# citation-checking code, NOT values any external source publishes, and
# they are token-overlap boundaries uncalibrated against any labelled
# corpus. They separate "the resolver found the right paper" from "the
# title is close but not matching" from "the title is completely wrong".
_declare(
    "citation-verification",
    "1.0",
    provenance=(
        "structural defaults observed in a reference implementation of "
        "citation-checking code; NOT values any external source publishes. "
        "Token-overlap boundaries uncalibrated against any labelled corpus."
    ),
    values={
        "title_match_tolerance": 0.75,
        "title_suspect_floor": 0.45,
        "max_phantom_citations": 0,
        "max_suspect_citations": UNRESOLVED,
    },
)

_declare(
    "trials",
    "1.0",
    provenance=(
        "Clinical trial pipeline classification: Phase 2 is the conventional "
        "threshold for 'active pipeline' in pharmaceutical competitive intelligence. "
        "No single-source citation; this reflects standard industry practice for "
        "distinguishing active development programs from early-stage exploration."
    ),
    values={
        # Minimum phase number (numeric) for a recruiting trial to qualify
        # as 'active pipeline'. Phase 2 = 2, Phase 3 = 3, etc.
        "active_pipeline_min_phase": 2,
    },
)

_declare(
    "surface",
    "1.0",
    provenance=(
        "RSA exposure thresholds: Tien et al., PLoS ONE 2013;8:e80635 "
        "(theoretical maxASA values); Rost & Sander, Proteins 1994;20:216-226 "
        "(0.25 RSA burial/exposure boundary). pLDDT disorder proxy: "
        "Jumper et al., Nature 2021;596:583-589 (pLDDT < 50)."
    ),
    values={
        "rsa_exposed": 0.25,
        "rsa_highly_exposed": 0.50,
        "plddt_disorder": 50.0,
        "min_exposed_patch_residues": 5,
        "glycosylation_proximity_angstrom": 10.0,
    },
)

_declare(
    "conservation-scores",
    "1.0",
    provenance=(
        "ConSurf evolutionary conservation grade interpretation: "
        "Ashkenazy et al., Nucleic Acids Res 2016;44:W344-W350; "
        "Landau et al., Nucleic Acids Res 2005;33:W299-W302. "
        "The ConSurf documentation classifies grades 7-9 as 'conserved' "
        "and grades 1-3 as 'variable'. Grade assignment follows the "
        "ConSurf equal-frequency binning methodology applied to "
        "rate4site normalized evolutionary rates."
    ),
    values={
        "conserved_grade_min": 7,  # grades 7-9 = conserved
        "variable_grade_max": 3,  # grades 1-3 = variable
    },
)

_declare(
    "docking-scores",
    "1.0",
    provenance=(
        "AutoDock Vina binding energy interpretation: values are predicted "
        "binding free energies in kcal/mol (more negative = stronger predicted "
        "binding). No universally accepted cutoffs exist for classifying Vina "
        "scores; the values here are from commonly cited benchmarking studies. "
        "Eberhardt et al., J Chem Inf Model 2021;61:3891-3898 (Vina 1.2.x). "
        "Quiroga & Villarreal, PLoS ONE 2016;11:e0155183 report that scores "
        "below -6.0 kcal/mol correspond to low-micromolar predicted affinity "
        "in their benchmark set."
    ),
    values={
        # Vina scores are negative kcal/mol; more negative = stronger binding.
        # These are LOWER bounds (i.e., score must be MORE negative to qualify).
        # Quiroga & Villarreal 2016 benchmark: -6.0 kcal/mol corresponds to
        # roughly low-micromolar predicted affinity.
        "strong_binding_energy": -8.0,  # strong predicted binding
        "moderate_binding_energy": -6.0,  # moderate predicted binding
        # No cited source for a "weak" cutoff exists — marked UNRESOLVED
        # per the project's unresolved threshold pattern.
        "weak_binding_energy": UNRESOLVED,
    },
)

# --- ADMET endpoints -------------------------------------------------------
# Rule-based ADMET prediction thresholds drawn from published literature.
#
# Metabolic stability: Gleeson 2008, "Generation of a Set of Simple,
# Interpretable ADMET Rules of Thumb", J Med Chem 2008;51:817-834.
#
# Permeability: Egan et al., "Prediction of Drug Absorption Using
# Multivariate Statistics", J Med Chem 2000;43:3867-3877.
#
# hERG liability: Aronov 2005, "Predictive in silico modeling for hERG
# channel blockers", Drug Discov Today 2005;10:149-155.
#
# Solubility: Delaney 2004, "ESOL: Estimating Aqueous Solubility Directly
# from Molecular Structure", J Chem Inf Comput Sci 2004;44:1000-1009.
#
# permeability_mw_max has no specific published source for the 500 Da
# cutoff as a permeability boundary (Lipinski's Ro5 uses 500 for
# oral bioavailability, not permeability specifically).
_declare(
    "admet-endpoints",
    "1.0",
    provenance=(
        "Gleeson, J Med Chem 2008;51:817-834 (metabolic stability); "
        "Egan et al., J Med Chem 2000;43:3867-3877 (permeability); "
        "Aronov, Drug Discov Today 2005;10:149-155 (hERG); "
        "Delaney, J Chem Inf Comput Sci 2004;44:1000-1009 (solubility)"
    ),
    values={
        # Metabolic stability (Gleeson 2008)
        "metabolic_logp_high": 4.0,
        "metabolic_aromatic_rings_high": 3,
        "metabolic_logp_low": 3.0,
        # Permeability (Egan 2000)
        "permeability_tpsa_high": 132.0,
        "permeability_logp_min": -1.0,
        "permeability_logp_max": 6.0,
        "permeability_tpsa_low": 150.0,
        "permeability_mw_max": 500.0,
        # hERG (Aronov 2005)
        "herg_logp_min": 3.7,
        "herg_min_aromatic_rings": 2,
        # Solubility classification (Delaney 2004)
        "solubility_high": -1.0,
        "solubility_moderate": -3.0,
        "solubility_low": -5.0,
    },
)

# --- preprint search (default parameters) -----------------------------------
# Default parameters for preprint search; configurable per program.
_declare(
    "preprint-search",
    "1.0",
    provenance=("Default parameters for preprint search; configurable per program."),
    values={
        "max_results_default": 20,
    },
)

# --- cBioPortal search (default parameters) --------------------------------
# Default parameters for cBioPortal cancer genomics search.
_declare(
    "cbioportal-search",
    "1.0",
    provenance=(
        "Default parameters for cBioPortal cancer genomics search; "
        "configurable per program."
    ),
    values={
        "max_results_default": 25,
    },
)

# Warnings that must reach the report whenever these sets are applied.
# The skill's interpretation contract names them; the sidecar is the
# evidence they were emitted (tool-design-guidance §5).
MANDATORY_ADVISORIES: dict[str, list[str]] = {
    "alphagenome-variant-effect": [
        "Raw-score magnitude bands are rules of thumb derived primarily from "
        "RNA-seq and are not absolute; interpretation depends on modality and "
        "assay type (source: interpretation-guide.md disclaimer).",
        "Raw scores are not percentages and must not be reported as such.",
        "Quantile scores are ranked against common variants; a variant in a "
        "poorly-annotated or low-expression region can score a high quantile "
        "without biological significance.",
        "AlphaGenome does not model miRNA effects, RNA secondary structure, "
        "protein folding consequences, or developmental timing.",
    ],
}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def load(
    name: str,
    project_root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> ThresholdSet:
    """Resolve a threshold set: defaults <- program file <- flags."""
    base = _DEFAULTS.get(name)
    if base is None:
        raise ThresholdError(
            f"no threshold set named {name!r}",
            detail=f"declared sets: {', '.join(sorted(_DEFAULTS))}",
        )

    values = dict(base.values)
    sources: dict[str, str] = {}
    version = base.version

    if project_root is not None:
        program = _load_program_file(project_root)
        section = program.get(name)
        if isinstance(section, dict):
            for key, value in section.items():
                if key not in values:
                    raise ThresholdError(
                        f"unknown threshold {key!r} for set {name!r} in "
                        f"{project_root / '.dde' / 'thresholds.yaml'}",
                        detail=f"declared thresholds: {', '.join(sorted(base.values))}",
                    )
                values[key] = value
                sources[key] = "program"
            if section:
                version = f"{base.version}+program"

    return ThresholdSet(
        name=name,
        version=version,
        values=values,
        provenance=base.provenance,
        overrides={k: v for k, v in (overrides or {}).items() if v is not None},
        _sources=sources,
    )


def _load_program_file(project_root: Path) -> dict[str, Any]:
    path = project_root / ".dde" / "thresholds.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError as e:
        raise ThresholdError(
            "PyYAML is required to read program threshold overrides",
            detail=f"{path} exists but yaml is not importable",
            remedy="install PyYAML into the tools environment",
        ) from e
    if not is_safe_to_open(path):
        raise ThresholdError(
            f"refusing to read through symlink: {path}",
            detail="symlink exploitation guard",
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ThresholdError(f"could not parse {path}", detail=str(exc)) from exc
    if not isinstance(data, dict):
        raise ThresholdError(
            f"{path} must contain a mapping of threshold-set name to values"
        )
    return data


def declared_sets() -> dict[str, ThresholdSet]:
    return dict(_DEFAULTS)
