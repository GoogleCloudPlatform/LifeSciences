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

"""ESMFold2 / ESMFold2-Fast Inference Pipeline with configurable scheduling and MSA step.

- Single-sequence mode (`use_msa=False`, default for ESMFold2-Fast):
  ConfigureSeeds → ParallelFor[Predict(GPU)]
- MSA-enabled mode (`use_msa=True`, default for full ESMFold2):
  ConfigureSeeds → MSA (CPU, NFS) → ParallelFor[Predict(GPU)]
"""

import os

from google_cloud_pipeline_components.v1.custom_job import create_custom_training_job_from_component
from kfp import dsl

from .. import config
from ..components import configure_seeds_esmfold2 as ConfigureSeedsESMFold2
from ..components import msa_pipeline_esmfold2 as MSAPipelineESMFold2
from ..components import predict_esmfold2 as PredictESMFold2


def create_esmfold2_inference_pipeline(strategy: str = "STANDARD", use_msa: bool = False):
    """Factory function to create ESMFold2 pipeline with specified strategy and MSA branch.

    Args:
        strategy: Scheduling strategy for GPU tasks ('STANDARD' or 'FLEX_START').
        use_msa: Whether to include the Jackhmmer MSA step before GPU prediction.
    """

    @dsl.pipeline(
        name="esmfold2-inference-pipeline",
        description="ESMFold2 / ESMFold2-Fast structure prediction with configurable scheduling.",
    )
    def esmfold2_inference_pipeline(
        query_json_path: str,
        project: str,
        region: str,
        nfs_cache_path: str,
        num_model_seeds: int = 1,
        num_diffusion_samples: int = 1,
        num_loops: int = 4,
        num_sampling_steps: int = 200,
        base_seed: int = 42,
    ):
        """ESMFold2 Inference Pipeline."""
        configure_seeds_task = ConfigureSeedsESMFold2(  # type: ignore
            num_model_seeds=num_model_seeds,
            base_seed=base_seed,
        ).set_display_name("Configure ESMFold2 Seeds")

        query_json_import = dsl.importer(
            artifact_uri=query_json_path,
            artifact_class=dsl.Artifact,
            reimport=True,
        ).set_display_name("Query JSON")

        if use_msa:
            MSAPipelineOp = create_custom_training_job_from_component(
                MSAPipelineESMFold2,
                display_name="ESMFold2 MSA Pipeline",
                machine_type=config.MSA_MACHINE_TYPE,
                nfs_mounts=[
                    {
                        "server": config.NFS_SERVER or os.environ.get("NFS_SERVER", "placeholder"),
                        "path": config.NFS_PATH or os.environ.get("NFS_PATH", "/placeholder"),
                        "mountPoint": config.NFS_MOUNT_POINT,
                    }
                ],
                network=config.NETWORK or os.environ.get("NETWORK", "placeholder"),
                strategy="STANDARD",
            )
            db_metadata = {
                "uniref90": config.UNIREF90_PATH,
                "mgnify": config.MGNIFY_PATH,
            }
            msa_task = MSAPipelineOp(
                ref_databases=dsl.importer(
                    artifact_uri=config.NFS_MOUNT_POINT,
                    artifact_class=dsl.Dataset,
                    reimport=False,
                    metadata=db_metadata,
                )
                .set_display_name("Reference databases (ESMFold2)")
                .output,
                query_json=query_json_import.output,
            ).set_retry(
                num_retries=2,
                backoff_duration="60s",
                backoff_factor=2.0,
            )
            predict_query_artifact = msa_task.outputs["updated_query_json"]
        else:
            predict_query_artifact = query_json_import.output

        flex_wait_secs = config.DWS_MAX_WAIT_HOURS * 3600
        max_wait = f"{flex_wait_secs}s" if strategy == "FLEX_START" else "86400s"

        JobPredictOp = create_custom_training_job_from_component(
            PredictESMFold2,
            display_name="ESMFold2 Predict",
            machine_type=os.environ.get("PREDICT_MACHINE_TYPE", "g2-standard-12"),
            accelerator_type=os.environ.get("PREDICT_ACCELERATOR_TYPE", "NVIDIA_L4"),
            accelerator_count=int(os.environ.get("PREDICT_ACCELERATOR_COUNT", "1")),
            nfs_mounts=[
                {
                    "server": config.NFS_SERVER or os.environ.get("NFS_SERVER", "placeholder"),
                    "path": config.NFS_PATH or os.environ.get("NFS_PATH", "/placeholder"),
                    "mountPoint": config.NFS_MOUNT_POINT,
                }
            ],
            network=config.NETWORK or os.environ.get("NETWORK", "placeholder"),
            strategy=strategy,
            max_wait_duration=max_wait,
        )

        with dsl.ParallelFor(
            items=configure_seeds_task.outputs["seed_configs"], name="seed-predict"
        ) as seed_config:
            JobPredictOp(
                project=project,
                location=region,
                updated_query_json=predict_query_artifact,
                seed_value=seed_config.seed_value,  # type: ignore
                num_diffusion_samples=num_diffusion_samples,
                num_loops=num_loops,
                num_sampling_steps=num_sampling_steps,
                nfs_cache_path=nfs_cache_path,
            ).set_retry(
                num_retries=2,
                backoff_duration="60s",
                backoff_factor=2.0,
            )

    return esmfold2_inference_pipeline


esmfold2_inference_pipeline = create_esmfold2_inference_pipeline("STANDARD", use_msa=False)
