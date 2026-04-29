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

"""AlphaFold Database query tools.

These tools query the public AlphaFold Database (EMBL-EBI) for pre-computed
protein structure predictions. They complement the Agent Platform prediction tools
by allowing users to check if a structure already exists before running
expensive compute jobs.

Based on ToolUniverse AlphaFold tools implementation.
"""

from typing import Any, Dict

from ..base import AF2Tool
from ..utils.alphafold_db import AlphaFoldDBClient


class AlphaFoldDBGetPrediction(AF2Tool):
    """Query AlphaFold DB for full prediction data."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieve full AlphaFold 3D structure prediction from public database.

        Args:
            arguments: {
                'qualifier': UniProt accession (e.g., 'P69905'), entry name, or CRC64 checksum (required),
                'sequence_checksum': Optional CRC64 checksum of the UniProt sequence
            }

        Returns:
            Dict containing:
            - data: Prediction metadata including pLDDT scores, download URLs
            - metadata: Query metadata
            OR
            - error: Error message if query failed
        """
        qualifier = arguments.get("qualifier")
        if not qualifier:
            return {
                "error": "Missing required parameter 'qualifier'",
                "detail": "Provide a UniProt accession (e.g., 'P69905'), entry name (e.g., 'HBA_HUMAN'), or CRC64 checksum",
            }

        sequence_checksum = arguments.get("sequence_checksum")

        client = AlphaFoldDBClient()
        result = client.get_prediction(qualifier, sequence_checksum)

        # Add helpful context to errors
        if "error" in result:
            if "Not found" in result["error"]:
                result["suggestion"] = (
                    "This protein may not be in AlphaFold DB. "
                    "Use af2_submit_monomer or af2_submit_multimer to run a custom prediction on Agent Platform."
                )
            return result

        # Enhance successful response with helpful metadata
        if "data" in result and isinstance(result["data"], list) and len(result["data"]) > 0:
            prediction = result["data"][0]

            # Extract key information for user
            summary = {
                "uniprot_accession": prediction.get("uniprotAccession"),
                "uniprot_id": prediction.get("uniprotId"),
                "organism": prediction.get("organismScientificName"),
                "sequence_length": len(prediction.get("sequence", "")),
                "model_created": prediction.get("modelCreatedDate"),
                "global_plddt": prediction.get("globalMetricValue"),
                "confidence_breakdown": {
                    "very_high (>90)": f"{prediction.get('fractionPlddtVeryHigh', 0) * 100:.1f}%",
                    "confident (70-90)": f"{prediction.get('fractionPlddtConfident', 0) * 100:.1f}%",
                    "low (50-70)": f"{prediction.get('fractionPlddtLow', 0) * 100:.1f}%",
                    "very_low (<50)": f"{prediction.get('fractionPlddtVeryLow', 0) * 100:.1f}%",
                },
                "downloads": {
                    "pdb": prediction.get("pdbUrl"),
                    "cif": prediction.get("cifUrl"),
                    "pae_image": prediction.get("paeImageUrl"),
                    "pae_json": prediction.get("paeDocUrl"),
                },
            }

            result["summary"] = summary
            result["full_data"] = result["data"]  # Keep original data
            result["data"] = summary  # Return summary as primary data

        return result


class AlphaFoldDBGetSummary(AF2Tool):
    """Query AlphaFold DB for summary information."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieve summary details of AlphaFold 3D models from public database.

        Args:
            arguments: {
                'qualifier': UniProt accession, entry name, or CRC64 checksum (required)
            }

        Returns:
            Dict containing:
            - data: Summary information (sequence length, coverage, confidence)
            - metadata: Query metadata
            OR
            - error: Error message if query failed
        """
        qualifier = arguments.get("qualifier")
        if not qualifier:
            return {
                "error": "Missing required parameter 'qualifier'",
                "detail": "Provide a UniProt accession, entry name, or CRC64 checksum",
            }

        client = AlphaFoldDBClient()
        result = client.get_summary(qualifier)

        # Add helpful context to errors
        if "error" in result:
            if "Not found" in result["error"]:
                result["suggestion"] = (
                    "This protein may not be in AlphaFold DB. "
                    "Use af2_submit_monomer or af2_submit_multimer to run a custom prediction."
                )
            return result

        # Enhance successful response
        if "data" in result:
            data = result["data"]

            # Extract key summary info
            summary = {
                "uniprot_entry": data.get("uniprot_entry", {}),
                "structure_count": len(data.get("structures", [])),
            }

            # Add details from first structure if available
            if data.get("structures") and len(data["structures"]) > 0:
                first_struct = data["structures"][0].get("summary", {})
                summary["primary_model"] = {
                    "model_id": first_struct.get("model_identifier"),
                    "provider": first_struct.get("provider"),
                    "created": first_struct.get("created"),
                    "coverage": f"{first_struct.get('coverage', 0) * 100:.1f}%",
                    "avg_confidence": first_struct.get("confidence_avg_local_score"),
                    "oligomeric_state": first_struct.get("oligomeric_state"),
                    "model_url": first_struct.get("model_url"),
                }

            result["summary"] = summary
            result["full_data"] = result["data"]
            result["data"] = summary

        return result


class AlphaFoldDBGetAnnotations(AF2Tool):
    """Query AlphaFold DB for variant annotations."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieve AlphaFold variant annotations (e.g., missense mutations).

        Args:
            arguments: {
                'qualifier': UniProt accession, entry name, or CRC64 checksum (required),
                'type': Annotation type (default: 'MUTAGEN')
            }

        Returns:
            Dict containing:
            - data: Variant annotations with predicted pathogenicity
            - metadata: Query metadata
            OR
            - error: Error message if query failed
        """
        qualifier = arguments.get("qualifier")
        if not qualifier:
            return {
                "error": "Missing required parameter 'qualifier'",
                "detail": "Provide a UniProt accession, entry name, or CRC64 checksum",
            }

        annotation_type = arguments.get("type", "MUTAGEN")

        client = AlphaFoldDBClient()
        result = client.get_annotations(qualifier, annotation_type)

        # Add helpful context to errors
        if "error" in result:
            if "Not found" in result["error"]:
                result["suggestion"] = (
                    "This protein may not have variant annotations in AlphaFold DB. "
                    "Try alphafold_db_get_prediction for structure data instead."
                )
            return result

        # Enhance successful response
        if "data" in result:
            data = result["data"]

            # Extract summary
            annotations = data.get("annotation", [])
            summary = {
                "accession": data.get("accession"),
                "id": data.get("id"),
                "sequence_length": len(data.get("sequence", "")),
                "annotation_count": len(annotations),
                "annotation_type": annotation_type,
            }

            # Group annotations by type if available
            if annotations:
                by_type = {}
                for ann in annotations:
                    ann_type = ann.get("type", "unknown")
                    if ann_type not in by_type:
                        by_type[ann_type] = []
                    by_type[ann_type].append(ann)

                summary["annotations_by_type"] = {k: len(v) for k, v in by_type.items()}

            result["summary"] = summary
            result["full_data"] = result["data"]
            result["data"] = {
                "summary": summary,
                "annotations": annotations[:10],  # First 10 for preview
            }

        return result
