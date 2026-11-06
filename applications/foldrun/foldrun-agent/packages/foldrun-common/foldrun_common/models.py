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

"""Pydantic v2 schemas and model-type helpers shared across FoldRun services."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ModelType = Literal["alphafold2", "alphafold3", "openfold3", "boltz2"]

MODEL_TYPE_ALIASES: dict[str, ModelType] = {
    "af2": "alphafold2",
    "alphafold": "alphafold2",
    "alphafold2": "alphafold2",
    "af3": "alphafold3",
    "alphafold3": "alphafold3",
    "of3": "openfold3",
    "openfold3": "openfold3",
    "boltz": "boltz2",
    "boltz-2": "boltz2",
    "boltz2": "boltz2",
}

SUPPORTED_MODEL_TYPES: frozenset[str] = frozenset(MODEL_TYPE_ALIASES.values())

DEFAULT_GEMINI_AGENT_MODEL = "gemini-3.8-flash"
ALLOWED_GEMINI_AGENT_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-4-argon",
)


def normalize_model_type(raw: str | None) -> str:
    """Normalize raw model_type labels or parameters to canonical FoldRun identifiers."""
    if not raw:
        return ""
    cleaned = str(raw).strip().lower()
    return MODEL_TYPE_ALIASES.get(cleaned, cleaned)


def infer_model_type_from_display_name(display_name: str | None) -> ModelType:
    """Infer canonical model_type from a PipelineJob displayName when labels are absent."""
    lower = (display_name or "").lower()
    if "af3" in lower or "alphafold3" in lower or "alphafold-3" in lower:
        return "alphafold3"
    if "of3" in lower or "openfold3" in lower or "openfold" in lower:
        return "openfold3"
    if "boltz" in lower:
        return "boltz2"
    return "alphafold2"


class AF2PredictionTask(BaseModel):
    """Per-prediction task item for AlphaFold2 analysis."""

    model_config = ConfigDict(extra="allow")

    index: int
    uri: str
    model_name: str
    ranking_confidence: float = 0.0
    output_uri: str = ""


class OF3PredictionTask(BaseModel):
    """Per-sample task item for OpenFold3 analysis."""

    model_config = ConfigDict(extra="allow")

    index: int
    sample_name: str
    cif_uri: str
    confidences_uri: str = ""
    aggregated_uri: str
    output_uri: str = ""


class Boltz2PredictionTask(BaseModel):
    """Per-sample task item for Boltz-2 analysis."""

    model_config = ConfigDict(extra="allow")

    index: int
    sample_name: str
    cif_uri: str
    confidences_uri: str = ""
    aggregated_uri: str
    pde_uri: str = ""
    output_uri: str = ""


class AF3PredictionTask(BaseModel):
    """Per-sample task item for AlphaFold 3 analysis."""

    model_config = ConfigDict(extra="allow")

    index: int
    sample_name: str
    cif_uri: str
    summary_uri: str
    confidences_uri: str | None = None
    output_uri: str = ""


PredictionTaskUnion = (
    AF2PredictionTask
    | OF3PredictionTask
    | Boltz2PredictionTask
    | AF3PredictionTask
    | dict[str, Any]
)


class AnalysisTaskConfig(BaseModel):
    """Schema for ``analysis/task_config.json`` consumed by ``foldrun-analysis-job``."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    model_type: str | None = None
    analysis_path: str = ""
    task_config_uri: str | None = None
    predictions: list[dict[str, Any]] = Field(default_factory=list)
    affinity_uri: str | None = None
    query_yaml_uri: str | None = None
    pipeline_job_id: str | None = None
    project_id: str | None = None
    region: str | None = None
    bucket_name: str | None = None
    job_prefix: str | None = None
    pipeline_root_prefix: str | None = None


class AnalysisMetadata(BaseModel):
    """Schema for ``analysis/analysis_metadata.json`` tracking running/completed jobs."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    total_predictions: int
    started_at: str
    status: str = "running"
    execution_method: str = "cloud_run_job"
    model_type: str
    task_config_uri: str | None = None
    triggered_by: str | None = None
    execution_name: str | None = None
    execution_uid: str | None = None
    cloud_run_job: str | None = None


class PLDDTDistribution(BaseModel):
    """Per-residue/atom pLDDT confidence band counts."""

    model_config = ConfigDict(extra="allow")

    very_low_confidence: int = 0
    low_confidence: int = 0
    high_confidence: int = 0
    very_high_confidence: int = 0


class ChainCompositionItem(BaseModel):
    """Parsed chain composition entry from mmCIF/PDB structures."""

    model_config = ConfigDict(extra="allow")

    chain_id: str
    molecule_type: str = "protein"
    atom_count: int = 0
    residue_count: int = 0
    comp_ids: list[str] = Field(default_factory=list)


class PerChainPLDDTStats(BaseModel):
    """Per-chain pLDDT summary statistics."""

    model_config = ConfigDict(extra="allow")

    mean: float | None = None
    min: float | None = None
    max: float | None = None
    std: float | None = None
    atom_count: int = 0
    residue_count: int = 0
    molecule_type: str = "protein"


class SamplePlots(BaseModel):
    """GCS URIs for diagnostic PNG plots produced per sample."""

    model_config = ConfigDict(extra="allow")

    plddt_plot: str | None = None
    pae_plot: str | None = None
    pde_plot: str | None = None
    iptm_matrix_plot: str | None = None


class SampleAnalysisResult(BaseModel):
    """Schema for ``analysis/prediction_{i}_analysis.json``."""

    model_config = ConfigDict(extra="allow")

    prediction_index: int
    sample_name: str | None = None
    model_name: str | None = None
    cif_uri: str | None = None
    uri: str | None = None
    plddt_mean: float | None = None
    plddt_median: float | None = None
    plddt_min: float | None = None
    plddt_max: float | None = None
    plddt_std: float | None = None
    plddt_distribution: dict[str, int] = Field(default_factory=dict)
    plddt_scores: list[float] = Field(default_factory=list)
    confidence_category: str = "uncomputed"
    quality_assessment: str = "uncomputed"
    ptm: float | None = None
    iptm: float | None = None
    ranking_score: float | None = None
    ranking_confidence: float | None = None
    pde_mean: float | None = None
    pde_max: float | None = None
    ipde_mean: float | None = None
    pae_mean: float | None = None
    pae_max: float | None = None
    has_clash: float | int | bool | None = 0.0
    disorder: float | int | bool | None = 0.0
    chain_ptm: dict[str, Any] = Field(default_factory=dict)
    chain_pair_iptm: dict[str, Any] = Field(default_factory=dict)
    per_chain_plddt: dict[str, Any] = Field(default_factory=dict)
    chain_info: list[dict[str, Any]] = Field(default_factory=list)
    num_atoms: int = 0
    num_chains: int = 0
    plots: dict[str, str | None] = Field(default_factory=dict)
    analyzed_at: str = ""


class ExpertAnalysisResult(BaseModel):
    """Gemini multimodal structural biology review payload."""

    model_config = ConfigDict(extra="allow")

    status: str
    model: str | None = None
    generated_at: str | None = None
    ai_generated: bool | None = None
    analysis: str | None = None
    error: str | None = None


class ConsolidatedQualityMetrics(BaseModel):
    """Quality summary block inside ``summary.json``."""

    model_config = ConfigDict(extra="allow")

    quality_assessment: str = "uncomputed"
    best_model: str | None = None
    best_model_plddt: float | None = None
    best_ranking_score: float | None = None
    best_ptm: float | None = None
    best_iptm: float | None = None
    best_model_pae: float | None = None
    has_clash: float | None = None


class ProteinInfo(BaseModel):
    """Protein/complex composition & pipeline metadata inside ``summary.json``."""

    model_config = ConfigDict(extra="allow")

    total_atoms: int | None = None
    sequence_length: int | None = None
    num_chains: int = 0
    chain_composition: list[dict[str, Any]] = Field(default_factory=list)
    job_metadata: dict[str, Any] = Field(default_factory=dict)
    fasta_header: str | None = None
    fasta_sequence: str | None = None
    input_query: Any | None = None
    input_query_json: Any | None = None
    input_query_yaml: str | dict[str, Any] | None = None


class ConsolidatedSummaryInner(BaseModel):
    """Inner ``summary`` object inside ``summary.json``."""

    model_config = ConfigDict(extra="allow")

    protein_info: dict[str, Any] = Field(default_factory=dict)
    quality_metrics: dict[str, Any] = Field(default_factory=dict)
    statistics: dict[str, Any] = Field(default_factory=dict)
    affinity: dict[str, Any] | None = None


class AnalysisSummary(BaseModel):
    """Top-level schema for ``analysis/summary.json``."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    model_type: str
    analyzed_at: str
    total_predictions: int = 0
    artifacts_bundle_uri: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    best_prediction: dict[str, Any] = Field(default_factory=dict)
    top_predictions: list[dict[str, Any]] = Field(default_factory=list)
    all_predictions_summary: list[dict[str, Any]] = Field(default_factory=list)
    affinity: dict[str, Any] | None = None
    expert_analysis: dict[str, Any] = Field(default_factory=dict)
