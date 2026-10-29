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

"""BoltzGen Inference Pipeline with configurable FLEX_START scheduling.

2-step pipeline: ConfigureSeeds → ParallelFor[Predict(A100 GPU, mounts /mnt/nfs/foldrun)]

Each seed runs on its own A100 GPU via ParallelFor. Within each seed,
BoltzGen generates num_designs_per_seed designs with budget_per_seed.
Default: 5 seeds x 2 designs = 10 candidate binder complexes.
"""

import os

from google_cloud_pipeline_components.v1.custom_job import create_custom_training_job_from_component
from kfp import dsl

from .. import config
from ..components import configure_seeds_boltzgen as ConfigureSeedsBOLTZGEN
from ..components import predict_boltzgen as PredictBOLTZGEN


def create_boltzgen_inference_pipeline(strategy: str = "STANDARD"):
    """Factory function to create BOLTZGEN pipeline with specified strategy.

    Args:
        strategy: Scheduling strategy for GPU tasks ('STANDARD' or 'FLEX_START').
    """

    @dsl.pipeline(
        name="boltzgen-inference-pipeline",
        description="BoltzGen generative binder design with configurable scheduling.",
    )
    def boltzgen_inference_pipeline(
        query_yaml_path: str,
        project: str,
        region: str,
        nfs_cache_path: str,
        protocol: str = "protein-anything",
        target_structure_path: str = "",
        num_model_seeds: int = 5,
        num_designs_per_seed: int = 2,
        budget_per_seed: int = 2,
        base_seed: int = 42,
        extra_cli_args: list = None,  # noqa: RUF013
    ):
        """BoltzGen Inference Pipeline.

        ConfigureSeeds → ParallelFor[Predict(GPU, 1 seed each)]
        """

        # Step 1: Generate seed configs for ParallelFor
        configure_seeds_task = ConfigureSeedsBOLTZGEN(  # type: ignore
            num_model_seeds=num_model_seeds,
            base_seed=base_seed,
        ).set_display_name("Configure BOLTZGEN Seeds")

        # Import query YAML from GCS
        query_yaml_import = dsl.importer(
            artifact_uri=query_yaml_path,
            artifact_class=dsl.Artifact,
            reimport=True,
        ).set_display_name("Query YAML")

        target_structure_import = dsl.importer(
            artifact_uri=target_structure_path,
            artifact_class=dsl.Artifact,
            reimport=True,
        ).set_display_name("Target Structure")

        # Step 2: Predict — ParallelFor over seeds, each on its own A100 GPU
        flex_wait_secs = config.DWS_MAX_WAIT_HOURS * 3600
        max_wait = f"{flex_wait_secs}s" if strategy == "FLEX_START" else "86400s"

        JobPredictOp = create_custom_training_job_from_component(
            PredictBOLTZGEN,
            display_name="BOLTZGEN Predict",
            machine_type=os.environ.get("PREDICT_MACHINE_TYPE", "a2-highgpu-1g"),
            accelerator_type=os.environ.get("PREDICT_ACCELERATOR_TYPE", "NVIDIA_TESLA_A100"),
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
                query_yaml=query_yaml_import.output,
                seed_value=seed_config.seed_value,  # type: ignore
                protocol=protocol,
                num_designs_per_seed=num_designs_per_seed,
                budget_per_seed=budget_per_seed,
                nfs_cache_path=nfs_cache_path,
                target_structure=target_structure_import.output,
                extra_cli_args=extra_cli_args,
            ).set_retry(
                num_retries=2,
                backoff_duration="60s",
                backoff_factor=2.0,
            )

        # =========================================================================
        # TODO(campaign-scaling): Add optional Step 3 AggregateAndFilterBOLTZGEN
        # fan-in KFP component when num_designs > 100 across shards.
        # When running large-scale design campaigns (>100 designs), an aggregation
        # step will collect all shard metrics CSVs, rank designs globally across
        # all seeds, filter by sc_score < 0.5 and clash_score < 1.0, and stage
        # the top-N designs for downstream experimental triage.
        # =========================================================================

    return boltzgen_inference_pipeline


# Default pipeline with STANDARD strategy
boltzgen_inference_pipeline = create_boltzgen_inference_pipeline("STANDARD")
