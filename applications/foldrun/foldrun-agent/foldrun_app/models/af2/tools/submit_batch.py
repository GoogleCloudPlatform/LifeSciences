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

"""Tool for submitting batch AlphaFold2 predictions."""

import logging
from typing import Any, Dict

from ..base import AF2Tool
from .submit_monomer import AF2SubmitMonomerTool
from .submit_multimer import AF2SubmitMultimerTool

logger = logging.getLogger(__name__)


class AF2BatchSubmitTool(AF2Tool):
    """Tool for submitting multiple AlphaFold2 predictions in batch."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit batch prediction jobs.

        Args:
            arguments: {
                'batch_config': List of job configurations, each containing:
                    {
                        'sequence': FASTA content or file path,
                        'job_name': Optional job name,
                        'is_multimer': Whether this is a multimer (auto-detected if not specified),
                        'max_template_date': Template search date,
                        'use_small_bfd': Use small BFD database,
                        'run_relaxation': Run AMBER relaxation,
                        'gpu_type': 'L4', 'A100', or 'A100_80GB',
                        'num_predictions_per_model': For multimers only
                    }
            }

        Returns:
            Batch submission results
        """
        batch_config = arguments.get("batch_config", [])

        if not batch_config:
            raise ValueError("batch_config must be a non-empty list")

        # Initialize submission tools
        monomer_tool = AF2SubmitMonomerTool(
            tool_config={
                "name": "af2_submit_monomer_batch",
                "description": "Batch monomer submission",
            },
            config=self.config,
        )
        multimer_tool = AF2SubmitMultimerTool(
            tool_config={
                "name": "af2_submit_multimer_batch",
                "description": "Batch multimer submission",
            },
            config=self.config,
        )

        submitted_jobs = []
        failed_jobs = []

        for idx, job_config in enumerate(batch_config):
            try:
                logger.info(f"Processing job {idx + 1} of {len(batch_config)}")

                # Auto-detect if monomer or multimer if not specified
                if "is_multimer" in job_config:
                    is_multimer = job_config["is_multimer"]
                else:
                    # Auto-detect based on sequence
                    import os

                    from ..utils.fasta_utils import parse_fasta_content, validate_fasta_file

                    sequence = job_config.get("sequence")
                    if os.path.isfile(sequence):
                        is_monomer, _ = validate_fasta_file(sequence)
                        is_multimer = not is_monomer
                    else:
                        sequences = parse_fasta_content(sequence)
                        is_multimer = len(sequences) > 1

                # Submit using appropriate tool
                if is_multimer:
                    result = multimer_tool.run(job_config)
                else:
                    result = monomer_tool.run(job_config)

                submitted_jobs.append(
                    {
                        "index": idx,
                        "job_id": result["job_id"],
                        "job_name": result["job_name"],
                        "status": "submitted",
                        "type": result["sequence_info"]["type"],
                    }
                )

                logger.info(f"Successfully submitted job: {result['job_name']}")

            except Exception as e:
                logger.error(f"Failed to submit job {idx}: {str(e)}")
                failed_jobs.append(
                    {
                        "index": idx,
                        "job_name": job_config.get("job_name", f"job_{idx}"),
                        "status": "failed",
                        "error": str(e),
                    }
                )

        # Return summary
        return {
            "total": len(batch_config),
            "succeeded": len(submitted_jobs),
            "failed": len(failed_jobs),
            "submitted_jobs": submitted_jobs,
            "failed_jobs": failed_jobs if failed_jobs else None,
        }
