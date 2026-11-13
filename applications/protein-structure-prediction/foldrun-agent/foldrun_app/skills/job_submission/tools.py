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
    enable_flex_start: bool = True,
    msa_method: str = "auto",
) -> dict:
    """Submit AlphaFold2 monomer prediction job (requires prior user confirmation).

    Args:
        sequence: FASTA string, raw amino-acid sequence, or GCS URI.
        gpu_type: "auto" (default), "L4", "A100", or "A100_80GB".
        relax_gpu_type: Optional relax GPU ("L4", "A100", "A100_80GB").
        msa_method: "auto" (default), "mmseqs2" (requires use_small_bfd=True), or "jackhmmer".
    """
    validated_sequence = _validate_input_source(sequence, "sequence")
    args = {
        "sequence": validated_sequence,
        "max_template_date": max_template_date,
        "use_small_bfd": use_small_bfd,
        "run_relaxation": run_relaxation,
        "gpu_type": gpu_type,
        "relax_gpu_type": relax_gpu_type,
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
    enable_flex_start: bool = True,
    msa_method: str = "auto",
) -> dict:
    """Submit AlphaFold2 multimer prediction job (requires prior user confirmation).

    Args:
        sequence: Multi-chain FASTA string or GCS URI.
        gpu_type: "auto" (default), "L4", "A100", or "A100_80GB".
        relax_gpu_type: Optional relax GPU ("L4", "A100", "A100_80GB").
        msa_method: "auto" (default), "mmseqs2" (requires use_small_bfd=True), or "jackhmmer".
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
    """Submit OpenFold3 structure prediction job for proteins, RNA, DNA, or ligands (requires prior user confirmation).

    Args:
        input: FASTA string, OF3 JSON string, or GCS URI.
        job_name: Human-readable job name.
        num_model_seeds: Number of seeds (default 1).
        num_diffusion_samples: Diffusion samples per seed (default 5).
        gpu_type: "auto" (default: A100 <=2200 tokens, A100_80GB >2200), "A100", "A100_80GB", or "RTX_PRO_6000".
        enable_flex_start: Use DWS FLEX_START scheduling (default True).
        use_templates: Use PDB structural templates (default True).
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
    """Submit Boltz-2 structure/affinity prediction job for proteins, nucleic acids, ligands, or glycans (requires prior user confirmation).

    Args:
        input: FASTA string, Boltz-2 YAML string, or GCS URI.
        job_name: Human-readable job name.
        num_model_seeds: Number of seeds (default 1).
        num_diffusion_samples: Diffusion samples per seed (default 5).
        gpu_type: "auto" (default: A100 <=2000 tokens, A100_80GB >2000), "L4" (g2-standard-12 <=1000 tokens), "A100", "A100_80GB", or "RTX_PRO_6000".
        enable_flex_start: Use DWS FLEX_START scheduling (default True).
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


def submit_esmfold2_prediction(
    input: str,
    job_name: str | None = None,
    model_variant: str = "fast",
    use_msa: bool | None = None,
    num_model_seeds: int = 1,
    num_diffusion_samples: int = 1,
    num_loops: int = 4,
    num_sampling_steps: int = 200,
    gpu_type: str = "auto",
    enable_flex_start: bool = True,
) -> dict:
    """Submit ESMFold2 / ESMFold2-Fast all-atom structure prediction job for proteins, RNA, DNA, or ligands (requires prior user confirmation).

    Args:
        input: FASTA string, raw amino-acid sequence, ESMFold2 JSON string, or GCS URI.
        job_name: Human-readable job name.
        model_variant: "fast" (default: biohub/ESMFold2-Fast, 24-block trunk, single-sequence use_msa=False) or "full" (biohub/ESMFold2, 48-block trunk, use_msa=True default).
        use_msa: Optional override for Jackhmmer MSA step (always False for "fast"; defaults to True for "full").
        num_model_seeds: Number of random seeds (default 1).
        num_diffusion_samples: Diffusion samples per seed (default 1).
        num_loops: Number of trunk recycling loops (default 4).
        num_sampling_steps: Diffusion sampling steps (default 200).
        gpu_type: "auto" (default: A100 <=2500 tokens fast / <=2000 tokens full, A100_80GB above), "L4", "A100", "A100_80GB", or "RTX_PRO_6000".
        enable_flex_start: Use DWS FLEX_START scheduling (default True).
    """
    validated_input = _validate_input_source(input, "input")
    args: dict = {
        "input": validated_input,
        "model_variant": model_variant,
        "num_model_seeds": num_model_seeds,
        "num_diffusion_samples": num_diffusion_samples,
        "num_loops": num_loops,
        "num_sampling_steps": num_sampling_steps,
        "gpu_type": gpu_type,
        "enable_flex_start": enable_flex_start,
    }
    if job_name is not None:
        args["job_name"] = job_name
    if use_msa is not None:
        args["use_msa"] = use_msa
    return get_tool("esmfold2_submit_prediction").run(args)


def submit_af3_endpoint_prediction(
    input: str,
    job_name: str | None = None,
    msa_free: bool = False,
    model_seeds: list[int] | None = None,
    num_model_seeds: int | None = None,
    num_diffusion_samples: int = 5,
    endpoint_id: str | None = None,
    sync: bool = False,
) -> dict:
    """Submit AlphaFold 3 all-atom prediction job to the dedicated H100 Endpoint via KFP (requires prior user confirmation).

    Args:
        input: FASTA string, AF3 JSON string, or GCS URI.
        job_name: Human-readable job name.
        msa_free: Skip MSA/template search for zero-MSA mode (default False = Full 630 GB MSA).
        model_seeds: Optional list of integer random seeds (e.g. [1] or [1,2,3,4,5]).
        num_model_seeds: Optional seed count (each generates num_diffusion_samples=5 structures).
        num_diffusion_samples: Diffusion samples per seed (default 5).
        endpoint_id: Optional Endpoint ID override.
        sync: Submit async KFP PipelineJob when False (default).
    """
    validated_input = _validate_input_source(input, "input")
    args = {
        "input": validated_input,
        "msa_free": msa_free,
        "num_diffusion_samples": num_diffusion_samples,
        "sync": sync,
    }
    if job_name is not None:
        args["job_name"] = job_name
    if model_seeds is not None:
        args["model_seeds"] = model_seeds
    if num_model_seeds is not None:
        args["num_model_seeds"] = num_model_seeds
    if endpoint_id is not None:
        args["endpoint_id"] = endpoint_id
    return get_tool("af3_submit_prediction").run(args)


def submit_af3_batch_predictions(
    batch_config: list[dict],
) -> dict:
    """Submit multiple AlphaFold 3 prediction jobs in batch across H100 Endpoint replica(s).

    Args:
        batch_config: List of dicts with `input`, optional `job_name`, `msa_free` (default False), and `model_seeds`.
    """
    sanitized_batch = []
    for item in batch_config:
        entry = dict(item)
        raw_input = entry.get("input") or entry.get("sequence")
        if raw_input:
            entry["input"] = _validate_input_source(str(raw_input), "input")
            entry.pop("sequence", None)
        if "job_name" in entry and entry["job_name"] is None:
            del entry["job_name"]
        entry["sync"] = False
        entry.pop("idle_shutdown_minutes", None)
        sanitized_batch.append(entry)
    return get_tool("af3_submit_batch").run(
        {
            "batch_config": sanitized_batch,
        }
    )
