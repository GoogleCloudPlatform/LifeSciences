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

"""Job submission tool wrappers for ADK FunctionTool."""

from typing import Optional

from foldrun_app.skills._tool_registry import get_tool


def submit_af2_monomer_prediction(
    sequence: str,
    job_name: Optional[str] = None,
    max_template_date: str = "2030-01-01",
    use_small_bfd: bool = True,
    run_relaxation: bool = True,
    gpu_type: str = "auto",
    relax_gpu_type: Optional[str] = None,
    vertex_repo_path: Optional[str] = None,
    enable_flex_start: bool = True,
    msa_method: str = "auto",
) -> dict:
    """Submit AlphaFold2 monomer protein structure prediction job to Vertex AI.

    Args:
        gpu_type: GPU for predict phase. "auto" (default) selects based on
            sequence length. Override with "L4", "A100", or "A100_80GB".
        relax_gpu_type: GPU type for the relaxation phase. If not specified,
            uses a cost-optimized default (downgraded from predict GPU).
            Options: "L4", "A100", "A100_80GB".
        msa_method: MSA search method. "auto" (default) selects based on
            use_small_bfd. Override with "mmseqs2" (GPU-accelerated, 177x
            faster, requires use_small_bfd=True) or "jackhmmer" (CPU).
    """
    return get_tool("af2_submit_monomer").run(
        {
            "sequence": sequence,
            "job_name": job_name,
            "max_template_date": max_template_date,
            "use_small_bfd": use_small_bfd,
            "run_relaxation": run_relaxation,
            "gpu_type": gpu_type,
            "relax_gpu_type": relax_gpu_type,
            "vertex_repo_path": vertex_repo_path,
            "enable_flex_start": enable_flex_start,
            "msa_method": msa_method,
        }
    )


def submit_af2_multimer_prediction(
    sequence: str,
    job_name: Optional[str] = None,
    max_template_date: str = "2030-01-01",
    use_small_bfd: bool = True,
    run_relaxation: bool = True,
    gpu_type: str = "auto",
    relax_gpu_type: Optional[str] = None,
    num_predictions_per_model: int = 5,
    vertex_repo_path: Optional[str] = None,
    enable_flex_start: bool = True,
    msa_method: str = "auto",
) -> dict:
    """Submit AlphaFold2 multimer/complex protein structure prediction job to Vertex AI.

    Args:
        gpu_type: GPU for predict phase. "auto" (default) selects based on
            total sequence length. Override with "L4", "A100", or "A100_80GB".
        relax_gpu_type: GPU type for the relaxation phase. If not specified,
            uses a cost-optimized default (downgraded from predict GPU).
            Options: "L4", "A100", "A100_80GB".
        msa_method: MSA search method. "auto" (default) selects based on
            use_small_bfd. Override with "mmseqs2" (GPU-accelerated, 177x
            faster, requires use_small_bfd=True) or "jackhmmer" (CPU).
    """
    return get_tool("af2_submit_multimer").run(
        {
            "sequence": sequence,
            "job_name": job_name,
            "max_template_date": max_template_date,
            "use_small_bfd": use_small_bfd,
            "run_relaxation": run_relaxation,
            "gpu_type": gpu_type,
            "relax_gpu_type": relax_gpu_type,
            "num_predictions_per_model": num_predictions_per_model,
            "vertex_repo_path": vertex_repo_path,
            "enable_flex_start": enable_flex_start,
            "msa_method": msa_method,
        }
    )


def submit_af2_batch_predictions(batch_config: list) -> dict:
    """Submit multiple AlphaFold2 prediction jobs in batch."""
    return get_tool("af2_submit_batch").run({"batch_config": batch_config})


def submit_of3_prediction(
    input: str,
    job_name: Optional[str] = None,
    num_model_seeds: int = 1,
    num_diffusion_samples: int = 5,
    gpu_type: str = "auto",
    enable_flex_start: bool = True,
) -> dict:
    """Submit OpenFold3 structure prediction job to Vertex AI.

    Supports proteins, RNA, DNA, and ligands. Accepts FASTA (auto-converted
    to OF3 JSON) or native OF3 JSON input.

    Args:
        input: Input in FASTA format, OF3 JSON format, or path to file.
            FASTA is auto-converted to OF3 JSON.
        job_name: Human-readable job name for tracking.
        num_model_seeds: Number of model seeds (default: 1). More seeds =
            more independent predictions.
        num_diffusion_samples: Number of diffusion samples per seed
            (default: 5). More samples = more structural diversity.
        gpu_type: GPU type. "auto" (default) selects A100 for <=2000 tokens,
            A100_80GB for >2000. No L4 — OF3 requires minimum 40GB VRAM.
        enable_flex_start: Enable DWS FLEX_START scheduling (default: true).
    """
    return get_tool("of3_submit_prediction").run(
        {
            "input": input,
            "job_name": job_name,
            "num_model_seeds": num_model_seeds,
            "num_diffusion_samples": num_diffusion_samples,
            "gpu_type": gpu_type,
            "enable_flex_start": enable_flex_start,
        }
    )
