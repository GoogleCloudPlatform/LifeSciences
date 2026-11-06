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

"""Shared FoldRun package: Pydantic schemas, GCS discovery, reporting, and archiving."""

from .archive import build_and_upload_run_archive, ensure_run_archive_in_gcs
from .discovery import (
    build_analysis_task_config,
    discover_af2_predictions_in_gcs,
    discover_af3_predictions,
    discover_boltz2_predictions,
    discover_of3_predictions,
    resolve_boltz2_query_yaml_uri,
)
from .gcs import (
    JOB_ID_PATTERN,
    MAX_DECOMPRESSED_ZST_BYTES,
    cleanup_stale_analysis_blobs,
    decompress_zst_bytes,
    is_stale_analysis_blob,
    maybe_decompress_zst,
    sanitize_filename,
    sanitize_json_floats,
    validate_gcs_uri,
)
from .models import (
    ALLOWED_GEMINI_AGENT_MODELS,
    DEFAULT_GEMINI_AGENT_MODEL,
    MODEL_TYPE_ALIASES,
    SUPPORTED_MODEL_TYPES,
    AF2PredictionTask,
    AF3PredictionTask,
    AnalysisMetadata,
    AnalysisSummary,
    AnalysisTaskConfig,
    Boltz2PredictionTask,
    ChainCompositionItem,
    ConsolidatedQualityMetrics,
    ConsolidatedSummaryInner,
    ExpertAnalysisResult,
    ModelType,
    OF3PredictionTask,
    PerChainPLDDTStats,
    PLDDTDistribution,
    ProteinInfo,
    SampleAnalysisResult,
    SamplePlots,
    infer_model_type_from_display_name,
    normalize_model_type,
)
from .report import build_archive_readme, build_expert_analysis_markdown

__all__ = [
    "ALLOWED_GEMINI_AGENT_MODELS",
    "DEFAULT_GEMINI_AGENT_MODEL",
    "JOB_ID_PATTERN",
    "MAX_DECOMPRESSED_ZST_BYTES",
    "MODEL_TYPE_ALIASES",
    "SUPPORTED_MODEL_TYPES",
    "AF2PredictionTask",
    "AF3PredictionTask",
    "AnalysisMetadata",
    "AnalysisSummary",
    "AnalysisTaskConfig",
    "Boltz2PredictionTask",
    "ChainCompositionItem",
    "ConsolidatedQualityMetrics",
    "ConsolidatedSummaryInner",
    "ExpertAnalysisResult",
    "ModelType",
    "OF3PredictionTask",
    "PLDDTDistribution",
    "PerChainPLDDTStats",
    "ProteinInfo",
    "SampleAnalysisResult",
    "SamplePlots",
    "build_analysis_task_config",
    "build_and_upload_run_archive",
    "build_archive_readme",
    "build_expert_analysis_markdown",
    "cleanup_stale_analysis_blobs",
    "decompress_zst_bytes",
    "discover_af2_predictions_in_gcs",
    "discover_af3_predictions",
    "discover_boltz2_predictions",
    "discover_of3_predictions",
    "ensure_run_archive_in_gcs",
    "infer_model_type_from_display_name",
    "is_stale_analysis_blob",
    "maybe_decompress_zst",
    "normalize_model_type",
    "resolve_boltz2_query_yaml_uri",
    "sanitize_filename",
    "sanitize_json_floats",
    "validate_gcs_uri",
]
