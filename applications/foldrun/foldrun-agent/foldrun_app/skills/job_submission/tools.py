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

import os
import tempfile

from foldrun_app.skills._tool_registry import get_tool

DEFAULT_STAGING_DIR = os.path.realpath(os.path.join(tempfile.gettempdir(), "foldrun_staging"))


def _validate_input_source(value: str, param_name: str = "sequence") -> str:
    """Validate that local file paths in sequence/input stay within the authorized staging directory."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{param_name} must be a non-empty string")

    stripped = value.strip()
    if stripped.startswith("gs://"):
        return value

    is_path_like = (
        stripped.startswith(("/", "~", "./", "../", "file://"))
        or "\x00" in value
        or os.path.exists(stripped)
        or os.path.lexists(stripped)
        or (
            "\n" not in stripped
            and not stripped.startswith((">", "{"))
            and ("/" in stripped or "\\" in stripped)
        )
    )

    if is_path_like:
        if "\x00" in value or stripped.startswith("file://"):
            raise ValueError(
                f"Invalid {param_name}: local file path must reside within the authorized staging directory"
            )
        allowed_dir = os.path.realpath(os.getenv("FOLDRUN_STAGING_DIR", DEFAULT_STAGING_DIR))
        resolved_path = os.path.realpath(os.path.expanduser(stripped))
        try:
            common = os.path.commonpath([allowed_dir, resolved_path])
        except ValueError:
            common = ""
        if common != allowed_dir or resolved_path == allowed_dir:
            raise ValueError(
                f"Invalid {param_name}: local file path '{stripped}' is outside the authorized staging directory ({allowed_dir})"
            )
        return resolved_path

    return value


def submit_af2_monomer_prediction(
    sequence: str,
    job_name: str | None = None,
    max_template_date: str = "2030-01-01",
    use_small_bfd: bool = True,
    run_relaxation: bool = True,
    gpu_type: str = "auto",
    relax_gpu_type: str | None = None,
    vertex_repo_path: str | None = None,
    enable_flex_start: bool = True,
    msa_method: str = "auto",
) -> dict:
    """Submit AlphaFold2 monomer protein structure prediction job to Agent Platform.

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
    validated_sequence = _validate_input_source(sequence, "sequence")
    args = {
        "sequence": validated_sequence,
        "max_template_date": max_template_date,
        "use_small_bfd": use_small_bfd,
        "run_relaxation": run_relaxation,
        "gpu_type": gpu_type,
        "relax_gpu_type": relax_gpu_type,
        "vertex_repo_path": vertex_repo_path,
        "enable_flex_start": enable_flex_start,
        "msa_method": msa_method,
    }
    if job_name is not None:
        args["job_name"] = job_name
    return get_tool("af2_submit_monomer").run(args)


def submit_af2_multimer_prediction(
    sequence: str,
    job_name: str | None = None,
    max_template_date: str = "2030-01-01",
    use_small_bfd: bool = True,
    run_relaxation: bool = True,
    gpu_type: str = "auto",
    relax_gpu_type: str | None = None,
    num_predictions_per_model: int = 5,
    vertex_repo_path: str | None = None,
    enable_flex_start: bool = True,
    msa_method: str = "auto",
) -> dict:
    """Submit AlphaFold2 multimer/complex protein structure prediction job to Agent Platform.

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
    validated_sequence = _validate_input_source(sequence, "sequence")
    args = {
        "sequence": validated_sequence,
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
    if job_name is not None:
        args["job_name"] = job_name
    return get_tool("af2_submit_multimer").run(args)


def submit_af2_batch_predictions(batch_config: list[dict]) -> dict:
    """Submit multiple AlphaFold2 prediction jobs in batch."""
    sanitized_batch = []
    for item in batch_config:
        entry = dict(item)
        if "sequence" in entry:
            entry["sequence"] = _validate_input_source(entry["sequence"], "sequence")
        if "job_name" in entry and entry["job_name"] is None:
            del entry["job_name"]
        sanitized_batch.append(entry)
    return get_tool("af2_submit_batch").run({"batch_config": sanitized_batch})


def submit_of3_prediction(
    input: str,
    job_name: str | None = None,
    num_model_seeds: int = 1,
    num_diffusion_samples: int = 5,
    gpu_type: str = "auto",
    enable_flex_start: bool = True,
    use_templates: bool = True,
) -> dict:
    """Submit OpenFold3 structure prediction job to Agent Platform.

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
        use_templates: Use PDB structural templates (default: true). Runs
            jackhmmer against pdb_seqres to find structural homologs, then
            uses local pdb_mmcif CIF files for featurization. Improves
            prediction quality for proteins with known related structures.
            Adds ~10-20 min to the MSA step. Requires pdb_seqres and
            pdb_mmcif databases on NFS (included in 'of3 full' install).
            Set to false for ab initio prediction or to reduce job time.
    """
    validated_input = _validate_input_source(input, "input")
    args = {
        "input": validated_input,
        "num_model_seeds": num_model_seeds,
        "num_diffusion_samples": num_diffusion_samples,
        "gpu_type": gpu_type,
        "enable_flex_start": enable_flex_start,
        "use_templates": use_templates,
    }
    if job_name is not None:
        args["job_name"] = job_name
    return get_tool("of3_submit_prediction").run(args)


def submit_boltz2_prediction(
    input: str,
    job_name: str | None = None,
    num_model_seeds: int = 1,
    num_diffusion_samples: int = 5,
    gpu_type: str = "auto",
    enable_flex_start: bool = True,
) -> dict:
    """Submit Boltz2 structure prediction job to Agent Platform.

    Supports proteins, RNA, DNA, and ligands. Accepts FASTA (auto-converted
    to Boltz2 YAML) or native Boltz2 YAML input.

    Args:
        input: Input in FASTA format, Boltz2 YAML format, or path to file.
            FASTA is auto-converted to Boltz2 YAML.
        job_name: Human-readable job name for tracking.
        num_model_seeds: Number of model seeds (default: 1). More seeds =
            more independent predictions.
        num_diffusion_samples: Number of diffusion samples per seed
            (default: 5). More samples = more structural diversity.
        gpu_type: GPU type. "auto" (default) selects A100 for <=2000 tokens,
            A100_80GB for >2000.
        enable_flex_start: Enable DWS FLEX_START scheduling (default: true).
    """
    validated_input = _validate_input_source(input, "input")
    args = {
        "input": validated_input,
        "num_model_seeds": num_model_seeds,
        "num_diffusion_samples": num_diffusion_samples,
        "gpu_type": gpu_type,
        "enable_flex_start": enable_flex_start,
    }
    if job_name is not None:
        args["job_name"] = job_name
    return get_tool("boltz2_submit_prediction").run(args)
