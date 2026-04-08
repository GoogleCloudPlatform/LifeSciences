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

"""OpenFold3 Inference Pipeline with configurable FLEX_START scheduling.

3-step pipeline: ConfigureSeeds → MSA (CPU, NFS) → ParallelFor[Predict(GPU)]

Each seed runs on its own A100 via ParallelFor. Within each seed,
OF3 generates num_diffusion_samples structures sequentially.
Default: 5 seeds × 5 samples = 25 structures (AF3 paper protocol).
"""

import os

from google_cloud_pipeline_components.v1.custom_job import create_custom_training_job_from_component
from kfp import dsl

from .. import config
from ..components import configure_seeds_of3 as ConfigureSeedsOF3
from ..components import msa_pipeline_of3 as MSAPipelineOF3
from ..components import predict_of3 as PredictOF3


def create_of3_inference_pipeline(strategy: str = "STANDARD"):
    """Factory function to create OF3 pipeline with specified strategy.

    Args:
        strategy: Scheduling strategy for GPU tasks ('STANDARD' or 'FLEX_START').
    """

    @dsl.pipeline(
        name="openfold3-inference-pipeline",
        description="OpenFold3 structure prediction with configurable scheduling.",
    )
    def of3_inference_pipeline(
        query_json_path: str,
        project: str,
        region: str,
        nfs_params_path: str,
        num_model_seeds: int = 5,
        num_diffusion_samples: int = 5,
    ):
        """OpenFold3 Inference Pipeline.

        ConfigureSeeds → MSA (CPU) → ParallelFor[Predict(GPU, 1 seed each)]
        """

        # Step 1: Generate seed configs for ParallelFor
        configure_seeds_task = ConfigureSeedsOF3(  # type: ignore
            num_model_seeds=num_model_seeds,
        ).set_display_name("Configure OF3 Seeds")

        # Step 2: MSA pipeline — CPU-only, NFS-mounted databases
        MSAPipelineOp = create_custom_training_job_from_component(
            MSAPipelineOF3,
            display_name="OF3 MSA Pipeline",
            machine_type=config.MSA_MACHINE_TYPE,
            nfs_mounts=[
                dict(
                    server=config.NFS_SERVER or os.environ.get("NFS_SERVER", "placeholder"),
                    path=config.NFS_PATH or os.environ.get("NFS_PATH", "/placeholder"),
                    mountPoint=config.NFS_MOUNT_POINT,
                )
            ],
            network=config.NETWORK or os.environ.get("NETWORK", "placeholder"),
            strategy="STANDARD",  # CPU-only, no FLEX_START needed
        )

        db_metadata = {
            "uniref90": config.UNIREF90_PATH,
            "mgnify": config.MGNIFY_PATH,
            "pdb_seqres": config.PDB_SEQRES_PATH,
            "uniprot": config.UNIPROT_PATH,
            "rfam": config.RFAM_PATH,
            "rnacentral": config.RNACENTRAL_PATH,
        }

        # Import query JSON from GCS
        query_json_import = dsl.importer(
            artifact_uri=query_json_path,
            artifact_class=dsl.Artifact,
            reimport=True,
        ).set_display_name("Query JSON")

        msa_task = MSAPipelineOp(
            ref_databases=dsl.importer(
                artifact_uri=config.NFS_MOUNT_POINT,
                artifact_class=dsl.Dataset,
                reimport=False,
                metadata=db_metadata,
            )
            .set_display_name("Reference databases (OF3)")
            .output,
            query_json=query_json_import.output,
        ).set_retry(
            num_retries=2,
            backoff_duration="60s",
            backoff_factor=2.0,
        )

        # Step 3: Predict — ParallelFor over seeds, each on its own A100
        flex_wait_secs = config.DWS_MAX_WAIT_HOURS * 3600
        max_wait = f"{flex_wait_secs}s" if strategy == "FLEX_START" else "86400s"

        JobPredictOp = create_custom_training_job_from_component(
            PredictOF3,
            display_name="OF3 Predict",
            machine_type=os.environ.get("PREDICT_MACHINE_TYPE", "a2-highgpu-1g"),
            accelerator_type=os.environ.get("PREDICT_ACCELERATOR_TYPE", "NVIDIA_TESLA_A100"),
            accelerator_count=int(os.environ.get("PREDICT_ACCELERATOR_COUNT", "1")),
            nfs_mounts=[
                dict(
                    server=config.NFS_SERVER or os.environ.get("NFS_SERVER", "placeholder"),
                    path=config.NFS_PATH or os.environ.get("NFS_PATH", "/placeholder"),
                    mountPoint=config.NFS_MOUNT_POINT,
                )
            ],
            network=config.NETWORK or os.environ.get("NETWORK", "placeholder"),
            strategy=strategy,
            max_wait_duration=max_wait,
        )

        with dsl.ParallelFor(
            items=configure_seeds_task.outputs["seed_configs"], name="seed-predict"
        ) as seed_config:
            predict_task = JobPredictOp(
                project=project,
                location=region,
                updated_query_json=msa_task.outputs["updated_query_json"],
                seed_value=seed_config.seed_value,  # type: ignore
                num_diffusion_samples=num_diffusion_samples,
                nfs_params_path=nfs_params_path,
            ).set_retry(
                num_retries=2,
                backoff_duration="60s",
                backoff_factor=2.0,
            )

    return of3_inference_pipeline


# Default pipeline with STANDARD strategy
of3_inference_pipeline = create_of3_inference_pipeline("STANDARD")
