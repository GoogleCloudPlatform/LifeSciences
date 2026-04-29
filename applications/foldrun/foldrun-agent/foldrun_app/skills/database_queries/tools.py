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

"""AlphaFold Database query tool wrappers for ADK FunctionTool."""

from typing import Optional

from foldrun_app.skills._tool_registry import get_tool


def query_alphafold_db_prediction(
    qualifier: str,
    sequence_checksum: Optional[str] = None,
) -> dict:
    """
    Query AlphaFold Database (EMBL-EBI) for pre-computed protein structure predictions.

    Check if a structure already exists in the public AlphaFold Database before
    running expensive Agent Platform jobs. Returns full prediction data including
    pLDDT scores, PAE, and download links.

    Args:
        qualifier: UniProt accession (e.g., 'P69905'), entry name (e.g., 'HBA_HUMAN'), or CRC64 checksum
        sequence_checksum: Optional CRC64 checksum for validation

    Returns:
        Prediction data with confidence scores and download URLs, or error if not found.
        If not found, use af2_submit_monomer or af2_submit_multimer to run custom prediction.
    """
    return get_tool("alphafold_db_get_prediction").run(
        {
            "qualifier": qualifier,
            "sequence_checksum": sequence_checksum,
        }
    )


def query_alphafold_db_summary(qualifier: str) -> dict:
    """
    Query AlphaFold Database (EMBL-EBI) for lightweight summary of pre-computed models.

    Faster than query_alphafold_db_prediction for quick lookups. Returns sequence
    length, coverage, confidence scores, oligomeric state, and model metadata.

    Args:
        qualifier: UniProt accession, entry name, or CRC64 checksum

    Returns:
        Summary information or error if not found.
    """
    return get_tool("alphafold_db_get_summary").run({"qualifier": qualifier})


def query_alphafold_db_annotations(
    qualifier: str,
    type: str = "MUTAGEN",
) -> dict:
    """
    Query AlphaFold Database (EMBL-EBI) for variant annotations and predicted mutation effects.

    Returns information about missense mutations, predicted pathogenicity, and
    functional effects. Use this to explore how mutations might affect protein
    structure and function.

    Args:
        qualifier: UniProt accession, entry name, or CRC64 checksum
        type: Annotation type (default: 'MUTAGEN')

    Returns:
        Variant annotations or error if not found.
    """
    return get_tool("alphafold_db_get_annotations").run(
        {
            "qualifier": qualifier,
            "type": type,
        }
    )
