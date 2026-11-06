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

from foldrun_app.skills._tool_registry import get_tool


def query_alphafold_db_prediction(
    qualifier: str,
    sequence_checksum: str | None = None,
) -> dict:
    """Query public AlphaFold Database (EMBL-EBI) for pre-computed structure predictions, pLDDT/PAE, and download links.

    Args:
        qualifier: UniProt accession (e.g. "P69905"), entry name ("HBA_HUMAN"), or CRC64 checksum.
        sequence_checksum: Optional CRC64 checksum.
    """
    return get_tool("alphafold_db_get_prediction").run(
        {
            "qualifier": qualifier,
            "sequence_checksum": sequence_checksum,
        }
    )


def query_alphafold_db_summary(qualifier: str) -> dict:
    """Query public AlphaFold Database (EMBL-EBI) for sequence length, coverage, and model metadata summary.

    Args:
        qualifier: UniProt accession, entry name, or CRC64 checksum.
    """
    return get_tool("alphafold_db_get_summary").run({"qualifier": qualifier})


def query_alphafold_db_annotations(
    qualifier: str,
    type: str = "MUTAGEN",
) -> dict:
    """Query public AlphaFold Database (EMBL-EBI) for missense variant and mutagenesis annotations.

    Args:
        qualifier: UniProt accession, entry name, or CRC64 checksum.
        type: Annotation type (default "MUTAGEN").
    """
    return get_tool("alphafold_db_get_annotations").run(
        {
            "qualifier": qualifier,
            "type": type,
        }
    )
