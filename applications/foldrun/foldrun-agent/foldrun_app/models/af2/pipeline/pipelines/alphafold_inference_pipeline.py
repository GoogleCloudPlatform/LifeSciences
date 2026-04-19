# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Universal Alphafold Inference Pipeline with configurable FLEX_START scheduling."""

import os

from google_cloud_pipeline_components.v1.custom_job import create_custom_training_job_from_component
from kfp import dsl

from .. import config
from ..components import configure_run as ConfigureRunOp
from ..components import data_pipeline
from ..components import predict as PredictOp
from ..components import relax as RelaxOp


def create_alphafold_inference_pipeline(strategy: str = "STANDARD", msa_method: str = "jackhmmer"):
    """
    Factory function to create AlphaFold pipeline with specified strategy.

    Args:
        strategy: Scheduling strategy for GPU tasks ('STANDARD' or 'FLEX_START').
                  Note: Data pipeline uses STANDARD for CPU-only (jackhmmer) mode.
                  FLEX_START is only applied to prediction and relaxation tasks.
        msa_method: MSA search method - 'jackhmmer' (CPU) or 'mmseqs2' (GPU).
                    When 'mmseqs2', data pipeline gets an L4 GPU for acceleration.
    """

    # Configure data pipeline hardware based on MSA method
    if msa_method == "mmseqs2":
        dp_machine_type = config.MMSEQS2_DATA_PIPELINE_MACHINE_TYPE
        dp_accel_type = config.MMSEQS2_ACCELERATOR_TYPE
        dp_accel_count = config.MMSEQS2_ACCELERATOR_COUNT
        dp_strategy = strategy  # GPU jobs can use FLEX_START
    else:
        dp_machine_type = config.DATA_PIPELINE_MACHINE_TYPE
        dp_accel_type = None
        dp_accel_count = 0
        dp_strategy = "STANDARD"  # CPU-only jobs cannot use FLEX_START

    @dsl.pipeline(
        name="alphafold2-inference-pipeline",
        description="AlphaFold inference using original data pipeline with configurable scheduling.",
    )
    def alphafold_inference_pipeline(
        sequence_path: str,
        project: str,
        region: str,
        max_template_date: str,
        model_params_gcs_location: str,
        model_preset: str = "monomer",
        use_small_bfd: bool = True,
        num_multimer_predictions_per_model: int = 5,
        is_run_relax: str = "relax",
    ):
        """Universal Alphafold Inference Pipeline."""
        # Note: Environment variables will be validated at runtime by the components

        run_config_task = (
            ConfigureRunOp(  # type: ignore
                sequence_path=sequence_path,
                model_preset=model_preset,
                num_multimer_predictions_per_model=num_multimer_predictions_per_model,
            )
            .set_display_name("Configure Pipeline Run")
            .set_retry(
                num_retries=2,
                backoff_duration="30s",
                backoff_factor=2.0,
            )
        )

        # Data pipeline: CPU-only for jackhmmer, GPU for mmseqs2
        dp_kwargs = dict(
            display_name="Data Pipeline",
            machine_type=dp_machine_type,
            nfs_mounts=[
                dict(
                    server=config.NFS_SERVER or os.environ.get("NFS_SERVER", "placeholder"),
                    path=config.NFS_PATH or os.environ.get("NFS_PATH", "/placeholder"),
                    mountPoint=config.NFS_MOUNT_POINT,
                )
            ],
            network=config.NETWORK or os.environ.get("NETWORK", "placeholder"),
            strategy=dp_strategy,
        )
        if dp_accel_type and dp_accel_count > 0:
            dp_kwargs["accelerator_type"] = dp_accel_type
            dp_kwargs["accelerator_count"] = dp_accel_count

        DataPipelineOp = create_custom_training_job_from_component(data_pipeline, **dp_kwargs)

        # Include MMseqs2 database paths in metadata
        db_metadata = {
            "uniref90": config.UNIREF90_PATH,
            "mgnify": config.MGNIFY_PATH,
            "bfd": config.BFD_PATH,
            "small_bfd": config.SMALL_BFD_PATH,
            "uniref30": config.UNIREF30_PATH,
            "pdb70": config.PDB70_PATH,
            "pdb_mmcif": config.PDB_MMCIF_PATH,
            "pdb_obsolete": config.PDB_OBSOLETE_PATH,
            "pdb_seqres": config.PDB_SEQRES_PATH,
            "uniprot": config.UNIPROT_PATH,
            "uniref90_mmseqs": config.UNIREF90_MMSEQS_PATH,
            "mgnify_mmseqs": config.MGNIFY_MMSEQS_PATH,
            "small_bfd_mmseqs": config.SMALL_BFD_MMSEQS_PATH,
        }

        data_pipeline_task = DataPipelineOp(
            ref_databases=dsl.importer(
                artifact_uri=config.NFS_MOUNT_POINT,
                artifact_class=dsl.Dataset,
                reimport=False,
                metadata=db_metadata,
            )
            .set_display_name("Reference databases")
            .output,
            sequence=run_config_task.outputs["sequence"],
            max_template_date=max_template_date,
            run_multimer_system=run_config_task.outputs["run_multimer_system"],
            use_small_bfd=use_small_bfd,
            msa_method=msa_method,
        ).set_retry(
            num_retries=2,
            backoff_duration="60s",
            backoff_factor=2.0,
        )

        # Per-task max wait for GPU provisioning via DWS.
        # Configurable via DWS_MAX_WAIT_HOURS env var (default: 168h / 7 days).
        # STANDARD strategy always uses 24h (minimum sensible timeout).
        # NOTE: This timeout applies independently to each predict/relax task,
        # not to the pipeline as a whole. A monomer pipeline has ~10 GPU tasks
        # (5 predict + 5 relax), a multimer up to ~50. With parallelism=5,
        # tasks batch sequentially. Consider reducing to 24-48h per task if
        # sustained GPU shortages cause pipelines to hang silently.
        flex_wait_secs = config.DWS_MAX_WAIT_HOURS * 3600
        max_wait = f"{flex_wait_secs}s" if strategy == "FLEX_START" else "86400s"

        JobPredictOp = create_custom_training_job_from_component(
            PredictOp,
            display_name="Predict",
            machine_type=os.environ.get("PREDICT_MACHINE_TYPE", "g2-standard-12"),
            accelerator_type=os.environ.get("PREDICT_ACCELERATOR_TYPE", "NVIDIA_L4"),
            accelerator_count=int(os.environ.get("PREDICT_ACCELERATOR_COUNT", "1")),
            strategy=strategy,
            max_wait_duration=max_wait,
        )

        JobRelaxOp = create_custom_training_job_from_component(
            RelaxOp,
            display_name="Relax",
            machine_type=os.environ.get("RELAX_MACHINE_TYPE", "g2-standard-12"),
            accelerator_type=os.environ.get("RELAX_ACCELERATOR_TYPE", "NVIDIA_L4"),
            accelerator_count=int(os.environ.get("RELAX_ACCELERATOR_COUNT", "1")),
            strategy=strategy,
            max_wait_duration=max_wait,
        )

        model_parameters = dsl.importer(
            artifact_uri=model_params_gcs_location, artifact_class=dsl.Artifact, reimport=True
        ).set_display_name("Model parameters")

        with dsl.ParallelFor(
            items=run_config_task.outputs["model_runners"], name="model-predict"
        ) as model_runner:
            model_predict_task = JobPredictOp(
                project=project,
                location=region,
                model_features=data_pipeline_task.outputs["features"],
                model_params=model_parameters.output,
                model_name=model_runner.model_name,  # type: ignore
                prediction_index=model_runner.prediction_index,  # type: ignore
                run_multimer_system=run_config_task.outputs["run_multimer_system"],
                num_ensemble=run_config_task.outputs["num_ensemble"],
                random_seed=model_runner.random_seed,  # type: ignore
                tf_force_unified_memory=config.TF_FORCE_UNIFIED_MEMORY,
                xla_python_client_mem_fraction=config.XLA_PYTHON_CLIENT_MEM_FRACTION,
            ).set_retry(
                num_retries=2,
                backoff_duration="60s",
                backoff_factor=2.0,
            )

            with dsl.If(is_run_relax == "relax", name="relax-condition"):
                relax_protein_task = JobRelaxOp(
                    project=project,
                    location=region,
                    unrelaxed_protein=model_predict_task.outputs["unrelaxed_protein"],
                    use_gpu=True,
                    tf_force_unified_memory=config.TF_FORCE_UNIFIED_MEMORY,
                    xla_python_client_mem_fraction=config.XLA_PYTHON_CLIENT_MEM_FRACTION,
                ).set_retry(
                    num_retries=2,
                    backoff_duration="60s",
                    backoff_factor=2.0,
                )

    return alphafold_inference_pipeline


# For backward compatibility, create default pipeline with STANDARD strategy
alphafold_inference_pipeline = create_alphafold_inference_pipeline("STANDARD")
