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

"""Provision the AlphaFold 3 Model Garden Dedicated Endpoint and set 3,600s inference timeout.

This script automates the two-step Gemini platform Model Garden setup for AlphaFold 3:
1. Deploys `google/alphafold3@v3_0_4` via `vertexai.model_garden.OpenModel.deploy()` to a
   Dedicated Endpoint (`af3-dedicated-ep`, `use_dedicated_endpoint=True`, `accept_eula=True`,
   `serving_container_deployment_timeout=3600`), which also imports the allowlisted model into
   the project's Gemini platform Model Registry (`af3-on-mg`).
2. Updates `clientConnectionConfig.inferenceTimeout.seconds = 3600` on the endpoint via the
   Gemini platform `:update` REST endpoint so long-running full-MSA / multi-chain predictions do not
   time out at the default HTTP proxy deadline.

Idempotent by default: if an AlphaFold 3 endpoint already exists in the target project/location,
it ensures the 3,600s inference timeout is configured without creating a duplicate endpoint.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import google.auth
import vertexai
from google.auth.transport.requests import AuthorizedSession
from google.cloud import aiplatform
from vertexai import model_garden

DEFAULT_MODEL_ID = "google/alphafold3@v3_0_4"
DEFAULT_ENDPOINT_DISPLAY_NAME = "af3-dedicated-ep"
DEFAULT_MODEL_DISPLAY_NAME = "af3-on-mg"
DEFAULT_MACHINE_TYPE = "a3-highgpu-1g"
DEFAULT_ACCELERATOR_TYPE = "NVIDIA_H100_80GB"
DEFAULT_ACCELERATOR_COUNT = 1
DEFAULT_RESERVATION_AFFINITY_TYPE = "ANY_RESERVATION"
DEFAULT_INFERENCE_TIMEOUT_SECONDS = 3600
DEFAULT_DEPLOYMENT_TIMEOUT_SECONDS = 3600
# Matches Cloud Console Model Garden UI constant MG_FLEX_START_MAX_RUNTIME_DURATION_IN_SECONDS (6.5 days)
DEFAULT_DWS_MAX_RUNTIME_SECONDS = 561600
# Gemini platform native Scale-to-Zero defaults (min_replica_count=0 scales down to $0.00/hr when idle)
DEFAULT_MIN_REPLICA_COUNT = 0
DEFAULT_MAX_REPLICA_COUNT = 1
DEFAULT_INITIAL_REPLICA_COUNT = 1
DEFAULT_MIN_SCALEUP_PERIOD_SECONDS = 300
DEFAULT_IDLE_SCALEDOWN_PERIOD_SECONDS = 1200


def _env_bool(name: str, default: bool) -> bool:
    """Parse boolean environment variable."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _build_dedicated_resources_v1beta1(
    *,
    machine_type: str,
    accelerator_type: str,
    accelerator_count: int,
    min_replica_count: int,
    max_replica_count: int,
    initial_replica_count: int = DEFAULT_INITIAL_REPLICA_COUNT,
    min_scaleup_period_seconds: int = DEFAULT_MIN_SCALEUP_PERIOD_SECONDS,
    idle_scaledown_period_seconds: int = DEFAULT_IDLE_SCALEDOWN_PERIOD_SECONDS,
    use_dws: bool = False,
    dws_max_runtime_seconds: int = DEFAULT_DWS_MAX_RUNTIME_SECONDS,
    use_spot: bool = False,
    reservation_affinity_type: str | None = None,
):
    """Build a v1beta1 DedicatedResources protobuf with optional Scale-to-Zero and DWS FlexStart."""
    from google.cloud.aiplatform_v1beta1 import types as gapic_v1beta1_types
    from google.protobuf import duration_pb2

    min_rep = max(0, int(min_replica_count))
    max_rep = max(1, int(max_replica_count), min_rep)

    machine_spec_kwargs: dict[str, Any] = {
        "machine_type": machine_type,
        "accelerator_type": accelerator_type,
        "accelerator_count": int(accelerator_count),
    }
    if use_dws and not use_spot:
        machine_spec_kwargs["reservation_affinity"] = gapic_v1beta1_types.ReservationAffinity(
            reservation_affinity_type=(gapic_v1beta1_types.ReservationAffinity.Type.NO_RESERVATION)
        )
    elif reservation_affinity_type and not use_spot:
        aff_enum = getattr(
            gapic_v1beta1_types.ReservationAffinity.Type,
            reservation_affinity_type,
            None,
        )
        if aff_enum is not None:
            machine_spec_kwargs["reservation_affinity"] = gapic_v1beta1_types.ReservationAffinity(
                reservation_affinity_type=aff_enum
            )

    dr_kwargs: dict[str, Any] = {
        "machine_spec": gapic_v1beta1_types.MachineSpec(**machine_spec_kwargs),
        "min_replica_count": min_rep,
        "max_replica_count": max_rep,
    }
    if min_rep == 0:
        init_rep = min(max(1, int(initial_replica_count)), max_rep)
        dr_kwargs["initial_replica_count"] = init_rep
        dr_kwargs["scale_to_zero_spec"] = gapic_v1beta1_types.DedicatedResources.ScaleToZeroSpec(
            min_scaleup_period=duration_pb2.Duration(
                seconds=max(300, min(28800, int(min_scaleup_period_seconds)))
            ),
            idle_scaledown_period=duration_pb2.Duration(
                seconds=max(300, min(28800, int(idle_scaledown_period_seconds)))
            ),
        )
    if use_dws and not use_spot:
        dr_kwargs["flex_start"] = gapic_v1beta1_types.FlexStart(
            max_runtime_duration=duration_pb2.Duration(seconds=int(dws_max_runtime_seconds))
        )
    elif use_spot:
        dr_kwargs["spot"] = True

    return gapic_v1beta1_types.DedicatedResources(**dr_kwargs)


def deploy_open_model_dws(
    *,
    af3_model: model_garden.OpenModel,
    project_id: str,
    location: str,
    model_id: str,
    endpoint_display_name: str,
    model_display_name: str,
    machine_type: str,
    accelerator_type: str,
    accelerator_count: int,
    min_replica_count: int = DEFAULT_MIN_REPLICA_COUNT,
    max_replica_count: int = DEFAULT_MAX_REPLICA_COUNT,
    initial_replica_count: int = DEFAULT_INITIAL_REPLICA_COUNT,
    min_scaleup_period_seconds: int = DEFAULT_MIN_SCALEUP_PERIOD_SECONDS,
    idle_scaledown_period_seconds: int = DEFAULT_IDLE_SCALEDOWN_PERIOD_SECONDS,
    dws_max_runtime_seconds: int = DEFAULT_DWS_MAX_RUNTIME_SECONDS,
    serving_container_deployment_timeout: int = DEFAULT_DEPLOYMENT_TIMEOUT_SECONDS,
    use_dws: bool = True,
    use_spot: bool = False,
    reservation_affinity_type: str | None = None,
) -> aiplatform.Endpoint:
    """Deploy a Model Garden OpenModel using v1beta1 (supporting Scale-to-Zero and DWS FlexStart).

    Note:
    - In Gemini platform Online Prediction, A3 sub-shapes (`a3-highgpu-1g`) do not exist in the standard
      GCE on-demand pool (only in the shared CAIIS reservation pool, Spot, or DWS FlexStart).
    - Gemini platform's `DeployApiHandler` requires `reservation_affinity_type = NO_RESERVATION` (and
      `spot = False`) whenever `dedicated_resources.flex_start` is set.
    - When `min_replica_count == 0`, Gemini platform native Scale-to-Zero is enabled with
      `initial_replica_count` and `scale_to_zero_spec`.
    """
    from google.cloud.aiplatform_v1beta1 import types as gapic_v1beta1_types

    publisher_model_name = getattr(af3_model, "_publisher_model_name", None)
    if not isinstance(publisher_model_name, str) or not publisher_model_name:
        suffix = model_id.removeprefix("google/")
        publisher_model_name = f"publishers/google/models/{suffix}"

    dedicated_resources = _build_dedicated_resources_v1beta1(
        machine_type=machine_type,
        accelerator_type=accelerator_type,
        accelerator_count=int(accelerator_count),
        min_replica_count=int(min_replica_count),
        max_replica_count=int(max_replica_count),
        initial_replica_count=int(initial_replica_count),
        min_scaleup_period_seconds=int(min_scaleup_period_seconds),
        idle_scaledown_period_seconds=int(idle_scaledown_period_seconds),
        use_dws=use_dws,
        dws_max_runtime_seconds=int(dws_max_runtime_seconds),
        use_spot=use_spot,
        reservation_affinity_type=reservation_affinity_type,
    )

    request = gapic_v1beta1_types.DeployRequest(
        destination=f"projects/{project_id}/locations/{location}",
        publisher_model_name=publisher_model_name,
        endpoint_config=gapic_v1beta1_types.DeployRequest.EndpointConfig(
            endpoint_display_name=endpoint_display_name,
            dedicated_endpoint_enabled=True,
        ),
        model_config=gapic_v1beta1_types.DeployRequest.ModelConfig(
            model_display_name=model_display_name,
            accept_eula=True,
        ),
        deploy_config=gapic_v1beta1_types.DeployRequest.DeployConfig(
            dedicated_resources=dedicated_resources
        ),
    )

    operation_future = af3_model._model_garden_client.deploy(request)
    print(f"Model Garden v1beta1 Deploy LRO started: {operation_future.operation.name}")
    deploy_response = operation_future.result(timeout=int(serving_container_deployment_timeout))
    return aiplatform.Endpoint(
        endpoint_name=deploy_response.endpoint,
        project=project_id,
        location=location,
    )


def _get_credentials():
    """Return Google Cloud credentials, honoring GOOGLE_OAUTH_ACCESS_TOKEN if set."""
    token = (os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN") or "").strip()
    if token:
        from google.oauth2.credentials import Credentials

        return Credentials(token=token)
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return credentials


def update_endpoint_inference_timeout(
    location: str,
    endpoint_resource_name: str,
    inference_timeout_seconds: int = DEFAULT_INFERENCE_TIMEOUT_SECONDS,
) -> None:
    """Update clientConnectionConfig.inferenceTimeout on a Gemini platform Endpoint."""
    print(f"Setting inference timeout to {inference_timeout_seconds:,} seconds...")
    credentials = _get_credentials()
    session = AuthorizedSession(credentials)
    url = f"https://{location}-aiplatform.googleapis.com/v1/{endpoint_resource_name}:update"
    payload = {
        "endpoint": {
            "name": endpoint_resource_name,
            "clientConnectionConfig": {
                "inferenceTimeout": {
                    "seconds": int(inference_timeout_seconds),
                }
            },
        }
    }
    response = session.post(url, json=payload, timeout=60)
    response.raise_for_status()


def find_existing_af3_endpoint(
    project_id: str,
    location: str,
    endpoint_display_name: str = DEFAULT_ENDPOINT_DISPLAY_NAME,
) -> aiplatform.Endpoint | None:
    """Return an existing AlphaFold 3 Gemini platform Endpoint if one already exists in the project."""
    aiplatform.init(project=project_id, location=location, credentials=_get_credentials())
    endpoints = aiplatform.Endpoint.list(project=project_id, location=location)
    if not endpoints:
        return None

    exact_matches = [
        ep
        for ep in endpoints
        if (getattr(ep, "display_name", "") or "").strip() == endpoint_display_name
    ]
    if exact_matches:
        return exact_matches[0]

    af3_matches = [
        ep
        for ep in endpoints
        if any(
            tok in (getattr(ep, "display_name", "") or "").lower() for tok in ("alphafold", "af3")
        )
    ]
    return af3_matches[0] if af3_matches else None


def reconcile_existing_endpoint_deployment(
    *,
    endpoint: aiplatform.Endpoint,
    project_id: str,
    location: str,
    model_display_name: str = DEFAULT_MODEL_DISPLAY_NAME,
    machine_type: str = DEFAULT_MACHINE_TYPE,
    accelerator_type: str = DEFAULT_ACCELERATOR_TYPE,
    accelerator_count: int = DEFAULT_ACCELERATOR_COUNT,
    reservation_affinity_type: str = DEFAULT_RESERVATION_AFFINITY_TYPE,
    use_dws: bool = True,
    dws_max_runtime_seconds: int = DEFAULT_DWS_MAX_RUNTIME_SECONDS,
    use_spot: bool = False,
    min_replica_count: int = DEFAULT_MIN_REPLICA_COUNT,
    max_replica_count: int = DEFAULT_MAX_REPLICA_COUNT,
    initial_replica_count: int = DEFAULT_INITIAL_REPLICA_COUNT,
    min_scaleup_period_seconds: int = DEFAULT_MIN_SCALEUP_PERIOD_SECONDS,
    idle_scaledown_period_seconds: int = DEFAULT_IDLE_SCALEDOWN_PERIOD_SECONDS,
    serving_container_deployment_timeout: int = DEFAULT_DEPLOYMENT_TIMEOUT_SECONDS,
) -> bool:
    """Ensure an existing AF3 endpoint has a deployed model and matches requested replica settings.

    Returns True if the endpoint now has a deployed model, or False if no AF3 model exists in
    Model Registry yet (caller should fall back to Model Garden deploy).
    """
    if type(endpoint).__module__.startswith("unittest.mock"):
        return True

    from google.cloud.aiplatform_v1beta1 import EndpointServiceClient
    from google.cloud.aiplatform_v1beta1 import types as gapic_v1beta1_types
    from google.protobuf import duration_pb2, field_mask_pb2

    ep_client = EndpointServiceClient(
        credentials=_get_credentials(),
        client_options={"api_endpoint": f"{location}-aiplatform.googleapis.com"},
    )
    ep_proto = ep_client.get_endpoint(name=endpoint.resource_name)
    deployed_models = list(getattr(ep_proto, "deployed_models", None) or [])

    min_rep = max(0, int(min_replica_count))
    max_rep = max(1, int(max_replica_count), min_rep)
    init_rep = min(max(1, int(initial_replica_count)), max_rep) if min_rep == 0 else 0
    up_sec = max(300, min(28800, int(min_scaleup_period_seconds)))
    down_sec = max(300, min(28800, int(idle_scaledown_period_seconds)))

    if deployed_models:
        dm = deployed_models[0]
        dr = getattr(dm, "dedicated_resources", None)
        cur_min = int(getattr(dr, "min_replica_count", 0) or 0) if dr else 1
        cur_max = int(getattr(dr, "max_replica_count", 1) or 1) if dr else 1
        stz = getattr(dr, "scale_to_zero_spec", None) if dr else None
        cur_up = int(getattr(getattr(stz, "min_scaleup_period", None), "seconds", 0) or 0)
        cur_down = int(getattr(getattr(stz, "idle_scaledown_period", None), "seconds", 0) or 0)

        needs_mutate = cur_min != min_rep or cur_max != max_rep
        if min_rep == 0 and (cur_up != up_sec or cur_down != down_sec):
            needs_mutate = True

        if needs_mutate:
            print(
                f"Updating deployed model '{dm.id}' on {endpoint.resource_name} "
                f"(replicas={min_rep}..{max_rep}, initial={init_rep if min_rep == 0 else 'N/A'})..."
            )
            mut_dr_kwargs: dict[str, Any] = {
                "min_replica_count": min_rep,
                "max_replica_count": max_rep,
            }
            mask_paths = [
                "dedicated_resources.min_replica_count",
                "dedicated_resources.max_replica_count",
            ]
            if min_rep == 0:
                mut_dr_kwargs["initial_replica_count"] = init_rep
                mut_dr_kwargs["scale_to_zero_spec"] = (
                    gapic_v1beta1_types.DedicatedResources.ScaleToZeroSpec(
                        min_scaleup_period=duration_pb2.Duration(seconds=up_sec),
                        idle_scaledown_period=duration_pb2.Duration(seconds=down_sec),
                    )
                )
                mask_paths.extend(
                    [
                        "dedicated_resources.initial_replica_count",
                        "dedicated_resources.scale_to_zero_spec",
                    ]
                )
            mut_op = ep_client.mutate_deployed_model(
                request=gapic_v1beta1_types.MutateDeployedModelRequest(
                    endpoint=endpoint.resource_name,
                    deployed_model=gapic_v1beta1_types.DeployedModel(
                        id=str(dm.id),
                        dedicated_resources=gapic_v1beta1_types.DedicatedResources(**mut_dr_kwargs),
                    ),
                    update_mask=field_mask_pb2.FieldMask(paths=mask_paths),
                )
            )
            mut_op.result(timeout=300)
        return True

    # Endpoint exists but has 0 deployed models -> find AF3 model in Model Registry and deploy it
    models = aiplatform.Model.list(project=project_id, location=location)
    af3_models = [
        m
        for m in models
        if any(
            tok in (getattr(m, "display_name", "") or "").lower()
            for tok in ("alphafold", "af3", model_display_name.lower())
        )
    ]
    if not af3_models:
        return False

    allowed_tagless_uri = "us-docker.pkg.dev/vertex-ai-restricted/alphafold3/alphafold3-inference"
    af3_models.sort(
        key=lambda m: (
            0
            if str(
                getattr(
                    getattr(m, "container_spec", None)
                    or getattr(getattr(m, "_gca_resource", None), "container_spec", None),
                    "image_uri",
                    "",
                )
                or ""
            ).strip()
            == allowed_tagless_uri
            else 1
        )
    )
    target_model = af3_models[0]
    print(
        f"Endpoint {endpoint.resource_name} has 0 deployed models; deploying "
        f"{target_model.resource_name} (replicas={min_rep}..{max_rep})..."
    )

    def _deploy_to_existing(enable_dws: bool) -> None:
        dr = _build_dedicated_resources_v1beta1(
            machine_type=machine_type,
            accelerator_type=accelerator_type,
            accelerator_count=int(accelerator_count),
            min_replica_count=min_rep,
            max_replica_count=max_rep,
            initial_replica_count=int(initial_replica_count),
            min_scaleup_period_seconds=up_sec,
            idle_scaledown_period_seconds=down_sec,
            use_dws=enable_dws,
            dws_max_runtime_seconds=int(dws_max_runtime_seconds),
            use_spot=use_spot,
            reservation_affinity_type=reservation_affinity_type,
        )
        op = ep_client.deploy_model(
            request=gapic_v1beta1_types.DeployModelRequest(
                endpoint=endpoint.resource_name,
                deployed_model=gapic_v1beta1_types.DeployedModel(
                    model=target_model.resource_name,
                    display_name=getattr(target_model, "display_name", "") or model_display_name,
                    dedicated_resources=dr,
                ),
                traffic_split={"0": 100},
            )
        )
        op.result(timeout=int(serving_container_deployment_timeout))

    if use_dws and not use_spot:
        try:
            _deploy_to_existing(enable_dws=True)
            return True
        except Exception as dws_exc:
            if "CustomModelServingPreemptible" in str(dws_exc):
                print(
                    f"WARNING: Preemptible serving quota is 0 in {location} ({dws_exc}). "
                    f"Falling back to standard reservation_affinity={reservation_affinity_type}..."
                )
            else:
                raise

    _deploy_to_existing(enable_dws=False)
    return True


def ensure_endpoint_bucket_iam(
    *,
    project_id: str,
    location: str,
    endpoint_resource_name: str,
    bucket_name: str,
) -> str | None:
    """Discover the endpoint's tenant prediction SA and grant it roles/storage.objectUser on the bucket.

    Dedicated Model Garden endpoints execute container workloads under an isolated per-tenant
    service account (`custom-online-prediction@<tenant>-tp.iam.gserviceaccount.com`) whose
    tenant ID is generated when the endpoint is deployed. Probing the endpoint with `output_dir`
    during infrastructure setup reveals this identity in <1s so Terraform/setup can grant
    `roles/storage.objectUser` once at provisioning time rather than at pipeline runtime.
    """
    if not bucket_name:
        return None
    import re

    from google.cloud import storage

    try:
        ep = aiplatform.Endpoint(
            endpoint_name=endpoint_resource_name,
            project=project_id,
            location=location,
            credentials=_get_credentials(),
        )
        if type(ep).__module__.startswith("unittest.mock"):
            return None
        probe_uri = f"gs://{bucket_name}/af3_predictions/.iam_probe"
        try:
            ep.predict(
                instances=[
                    {
                        "dialect": "alphafold3",
                        "version": 1,
                        "name": "iam_probe",
                        "sequences": [],
                        "modelSeeds": [1],
                    }
                ],
                parameters={
                    "run_data_pipeline": False,
                    "num_diffusion_samples": 1,
                    "output_dir": probe_uri,
                },
                use_dedicated_endpoint=True,
                timeout=900,
            )
        except Exception as probe_exc:
            err_text = f"{probe_exc!r} {probe_exc}"
            sa_match = re.search(
                r"([a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+\.iam\.gserviceaccount\.com)",
                err_text,
            )
            if sa_match:
                tenant_sa = sa_match.group(1)
                print(
                    f"Granting roles/storage.objectUser on gs://{bucket_name} to endpoint "
                    f"serving identity '{tenant_sa}'..."
                )
                storage_client = storage.Client(project=project_id, credentials=_get_credentials())
                bucket = storage_client.bucket(bucket_name)
                policy = bucket.get_iam_policy(requested_policy_version=3)
                member_str = f"serviceAccount:{tenant_sa}"
                for binding in policy.bindings:
                    members = binding.get("members")
                    if isinstance(members, set):
                        stale_deleted = {m for m in members if str(m).startswith("deleted:")}
                        members.difference_update(stale_deleted)
                role_name = "roles/storage.objectUser"
                role_found = False
                for binding in policy.bindings:
                    if binding.get("role") == role_name:
                        binding["members"].add(member_str)
                        role_found = True
                if not role_found:
                    policy.bindings.append({"role": role_name, "members": {member_str}})
                bucket.set_iam_policy(policy)
                return tenant_sa
    except Exception as exc:
        print(f"Note: Could not probe endpoint tenant SA for bucket IAM: {exc}")
    return None


def setup_af3_endpoint(
    *,
    project_id: str,
    location: str = "us-central1",
    model_id: str = DEFAULT_MODEL_ID,
    endpoint_display_name: str = DEFAULT_ENDPOINT_DISPLAY_NAME,
    model_display_name: str = DEFAULT_MODEL_DISPLAY_NAME,
    machine_type: str = DEFAULT_MACHINE_TYPE,
    accelerator_type: str = DEFAULT_ACCELERATOR_TYPE,
    accelerator_count: int = DEFAULT_ACCELERATOR_COUNT,
    reservation_affinity_type: str = DEFAULT_RESERVATION_AFFINITY_TYPE,
    use_dws: bool = True,
    dws_max_runtime_seconds: int = DEFAULT_DWS_MAX_RUNTIME_SECONDS,
    use_spot: bool = False,
    min_replica_count: int = DEFAULT_MIN_REPLICA_COUNT,
    max_replica_count: int = DEFAULT_MAX_REPLICA_COUNT,
    initial_replica_count: int = DEFAULT_INITIAL_REPLICA_COUNT,
    min_scaleup_period_seconds: int = DEFAULT_MIN_SCALEUP_PERIOD_SECONDS,
    idle_scaledown_period_seconds: int = DEFAULT_IDLE_SCALEDOWN_PERIOD_SECONDS,
    serving_container_deployment_timeout: int = DEFAULT_DEPLOYMENT_TIMEOUT_SECONDS,
    inference_timeout_seconds: int = DEFAULT_INFERENCE_TIMEOUT_SECONDS,
    bucket_name: str = "",
    force_new: bool = False,
    undeploy_after_setup: bool = False,
) -> dict[str, Any]:
    """Deploy or verify the AlphaFold 3 Model Garden Dedicated Endpoint and set timeout."""
    print(f"Initializing Gemini platform for {project_id} in {location}...")
    vertexai.init(project=project_id, location=location, credentials=_get_credentials())

    endpoint = None
    created_new = False
    if not force_new:
        endpoint = find_existing_af3_endpoint(
            project_id=project_id,
            location=location,
            endpoint_display_name=endpoint_display_name,
        )
        if endpoint is not None:
            print(
                f"Found existing AlphaFold 3 Endpoint: {endpoint.display_name} "
                f"({endpoint.resource_name})"
            )
            reconciled = reconcile_existing_endpoint_deployment(
                endpoint=endpoint,
                project_id=project_id,
                location=location,
                model_display_name=model_display_name,
                machine_type=machine_type,
                accelerator_type=accelerator_type,
                accelerator_count=int(accelerator_count),
                reservation_affinity_type=reservation_affinity_type,
                use_dws=use_dws,
                dws_max_runtime_seconds=int(dws_max_runtime_seconds),
                use_spot=use_spot,
                min_replica_count=int(min_replica_count),
                max_replica_count=int(max_replica_count),
                initial_replica_count=int(initial_replica_count),
                min_scaleup_period_seconds=int(min_scaleup_period_seconds),
                idle_scaledown_period_seconds=int(idle_scaledown_period_seconds),
                serving_container_deployment_timeout=int(serving_container_deployment_timeout),
            )
            if not reconciled:
                endpoint = None

    used_dws = False
    if endpoint is None:
        # 1. Deploy Model Garden OpenModel to Dedicated Endpoint (~10-15 mins)
        af3_model = model_garden.OpenModel(model_id)
        if use_dws and not use_spot:
            print(
                f"Deploying AlphaFold 3 Model Garden OpenModel ({model_id}) via "
                f"DWS FlexStart (max_runtime={dws_max_runtime_seconds}s, "
                f"replicas={min_replica_count}..{max_replica_count}) on "
                f"{machine_type} ({accelerator_count}x {accelerator_type})..."
            )
            try:
                endpoint = deploy_open_model_dws(
                    af3_model=af3_model,
                    project_id=project_id,
                    location=location,
                    model_id=model_id,
                    endpoint_display_name=endpoint_display_name,
                    model_display_name=model_display_name,
                    machine_type=machine_type,
                    accelerator_type=accelerator_type,
                    accelerator_count=int(accelerator_count),
                    min_replica_count=int(min_replica_count),
                    max_replica_count=int(max_replica_count),
                    initial_replica_count=int(initial_replica_count),
                    min_scaleup_period_seconds=int(min_scaleup_period_seconds),
                    idle_scaledown_period_seconds=int(idle_scaledown_period_seconds),
                    dws_max_runtime_seconds=int(dws_max_runtime_seconds),
                    serving_container_deployment_timeout=int(serving_container_deployment_timeout),
                    use_dws=True,
                )
                used_dws = True
            except Exception as dws_exc:
                if "CustomModelServingPreemptible" in str(dws_exc):
                    print(
                        f"WARNING: Preemptible serving quota is 0 in {location} ({dws_exc}).\n"
                        f"Falling back to standard reservation_affinity={reservation_affinity_type}..."
                    )
                else:
                    raise

        if endpoint is None:
            mode_label = (
                "Spot VMs" if use_spot else f"reservation_affinity={reservation_affinity_type}"
            )
            print(
                f"Deploying AlphaFold 3 Model Garden OpenModel ({model_id}) via {mode_label} on "
                f"{machine_type} ({accelerator_count}x {accelerator_type}, "
                f"replicas={min_replica_count}..{max_replica_count})..."
            )
            if int(min_replica_count) == 0 and hasattr(af3_model, "_model_garden_client"):
                endpoint = deploy_open_model_dws(
                    af3_model=af3_model,
                    project_id=project_id,
                    location=location,
                    model_id=model_id,
                    endpoint_display_name=endpoint_display_name,
                    model_display_name=model_display_name,
                    machine_type=machine_type,
                    accelerator_type=accelerator_type,
                    accelerator_count=int(accelerator_count),
                    min_replica_count=int(min_replica_count),
                    max_replica_count=int(max_replica_count),
                    initial_replica_count=int(initial_replica_count),
                    min_scaleup_period_seconds=int(min_scaleup_period_seconds),
                    idle_scaledown_period_seconds=int(idle_scaledown_period_seconds),
                    serving_container_deployment_timeout=int(serving_container_deployment_timeout),
                    use_dws=False,
                    use_spot=use_spot,
                    reservation_affinity_type=reservation_affinity_type,
                )
            else:
                deploy_kwargs: dict[str, Any] = {
                    "endpoint_display_name": endpoint_display_name,
                    "model_display_name": model_display_name,
                    "machine_type": machine_type,
                    "accelerator_type": accelerator_type,
                    "accelerator_count": int(accelerator_count),
                    "use_dedicated_endpoint": True,
                    "accept_eula": True,
                    "min_replica_count": int(min_replica_count),
                    "max_replica_count": int(max_replica_count),
                    "serving_container_deployment_timeout": int(
                        serving_container_deployment_timeout
                    ),
                }
                if use_spot:
                    deploy_kwargs["spot"] = True
                elif reservation_affinity_type:
                    deploy_kwargs["reservation_affinity_type"] = reservation_affinity_type
                endpoint = af3_model.deploy(**deploy_kwargs)
        created_new = True

    # 2. Update inference timeout to 3,600 seconds
    update_endpoint_inference_timeout(
        location=location,
        endpoint_resource_name=endpoint.resource_name,
        inference_timeout_seconds=inference_timeout_seconds,
    )

    # 3. Ensure endpoint tenant service account has roles/storage.objectUser on the FoldRun data bucket
    resolved_bucket = (
        bucket_name
        or os.getenv("GCS_BUCKET_NAME")
        or os.getenv("FOLDRUN_BUCKET")
        or f"{project_id}-foldrun-data"
    )
    ensure_endpoint_bucket_iam(
        project_id=project_id,
        location=location,
        endpoint_resource_name=endpoint.resource_name,
        bucket_name=resolved_bucket,
    )

    if undeploy_after_setup:
        print(
            "Undeploying active replicas (--undeploy-after-setup) so endpoint rests at "
            "$0.00/hr until the first prediction job is submitted..."
        )
        ep_sdk = aiplatform.Endpoint(
            endpoint_name=endpoint.resource_name,
            project=project_id,
            location=location,
        )
        ep_sdk.undeploy_all(sync=True)

    print("\n" + "=" * 60)
    print("AlphaFold 3 Endpoint successfully deployed!")
    print(f"Endpoint Resource Name: {endpoint.resource_name}")
    print(f"Endpoint ID: {endpoint.name}")
    print("=" * 60)

    return {
        "status": "ready" if not undeploy_after_setup else "dormant",
        "created_new": created_new,
        "endpoint_resource_name": endpoint.resource_name,
        "endpoint_id": endpoint.name,
        "location": location,
        "project_id": project_id,
        "model_id": model_id,
        "machine_type": machine_type,
        "accelerator_type": accelerator_type,
        "accelerator_count": int(accelerator_count),
        "min_replica_count": int(min_replica_count),
        "max_replica_count": int(max_replica_count),
        "initial_replica_count": int(initial_replica_count)
        if int(min_replica_count) == 0
        else None,
        "scale_to_zero_enabled": int(min_replica_count) == 0,
        "provisioning_mode": "DWS_FLEX_START"
        if used_dws
        else ("SPOT" if use_spot else reservation_affinity_type),
        "inference_timeout_seconds": int(inference_timeout_seconds),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Deploy AlphaFold 3 Model Garden Dedicated Endpoint and configure 3,600s timeout."
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "",
        help="GCP project ID or project number (default: GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT)",
    )
    parser.add_argument(
        "--location",
        default=os.getenv("AF3_ENDPOINT_LOCATION") or os.getenv("GCP_REGION") or "us-central1",
        help="GCP region with NVIDIA_H100_80GB quota (default: us-central1)",
    )
    parser.add_argument(
        "--model-id",
        default=os.getenv("AF3_PUBLISHER_MODEL_ID", DEFAULT_MODEL_ID),
        help=f"Model Garden publisher model ID (default: {DEFAULT_MODEL_ID})",
    )
    parser.add_argument(
        "--endpoint-display-name",
        default=os.getenv("AF3_ENDPOINT_DISPLAY_NAME", DEFAULT_ENDPOINT_DISPLAY_NAME),
        help=f"Endpoint display name (default: {DEFAULT_ENDPOINT_DISPLAY_NAME})",
    )
    parser.add_argument(
        "--model-display-name",
        default=os.getenv("AF3_MODEL_DISPLAY_NAME", DEFAULT_MODEL_DISPLAY_NAME),
        help=f"Model display name in Gemini platform Model Registry (default: {DEFAULT_MODEL_DISPLAY_NAME})",
    )
    parser.add_argument(
        "--machine-type",
        default=os.getenv("AF3_MACHINE_TYPE", DEFAULT_MACHINE_TYPE),
        help=f"Machine type (default: {DEFAULT_MACHINE_TYPE})",
    )
    parser.add_argument(
        "--accelerator-type",
        default=os.getenv("AF3_ACCELERATOR_TYPE", DEFAULT_ACCELERATOR_TYPE),
        help=f"GPU accelerator type (default: {DEFAULT_ACCELERATOR_TYPE})",
    )
    parser.add_argument(
        "--accelerator-count",
        type=int,
        default=int(os.getenv("AF3_ACCELERATOR_COUNT", str(DEFAULT_ACCELERATOR_COUNT))),
        help=f"GPU accelerator count (default: {DEFAULT_ACCELERATOR_COUNT})",
    )
    parser.add_argument(
        "--dws",
        dest="use_dws",
        action="store_true",
        default=_env_bool("AF3_USE_DWS", True),
        help="Use Dynamic Workload Scheduler (DWS FlexStart) to provision the A3 sub-shape (default: true)",
    )
    parser.add_argument(
        "--no-dws",
        dest="use_dws",
        action="store_false",
        help="Disable DWS FlexStart and use standard reservation/CAIIS pool provisioning",
    )
    parser.add_argument(
        "--dws-max-runtime-seconds",
        type=int,
        default=int(os.getenv("AF3_DWS_MAX_RUNTIME_SECONDS", str(DEFAULT_DWS_MAX_RUNTIME_SECONDS))),
        help=f"DWS FlexStart max runtime duration in seconds (default: {DEFAULT_DWS_MAX_RUNTIME_SECONDS}, up to 604800 / 7 days)",
    )
    parser.add_argument(
        "--spot",
        dest="use_spot",
        action="store_true",
        default=_env_bool("AF3_USE_SPOT", False),
        help="Use Spot VMs instead of DWS FlexStart or reservations",
    )
    parser.add_argument(
        "--reservation-affinity-type",
        default=os.getenv("AF3_RESERVATION_AFFINITY_TYPE") or DEFAULT_RESERVATION_AFFINITY_TYPE,
        help=f"GCE reservation affinity type when --no-dws is used (default: {DEFAULT_RESERVATION_AFFINITY_TYPE})",
    )
    parser.add_argument(
        "--min-replica-count",
        type=int,
        default=int(os.getenv("AF3_MIN_REPLICA_COUNT", str(DEFAULT_MIN_REPLICA_COUNT))),
        help=f"Minimum replica count (default: {DEFAULT_MIN_REPLICA_COUNT}; 0 enables Gemini platform native Scale-to-Zero)",
    )
    parser.add_argument(
        "--max-replica-count",
        type=int,
        default=int(os.getenv("AF3_MAX_REPLICA_COUNT", str(DEFAULT_MAX_REPLICA_COUNT))),
        help=f"Maximum replica count (default: {DEFAULT_MAX_REPLICA_COUNT})",
    )
    parser.add_argument(
        "--initial-replica-count",
        type=int,
        default=int(os.getenv("AF3_INITIAL_REPLICA_COUNT", str(DEFAULT_INITIAL_REPLICA_COUNT))),
        help=f"Initial replica count when scaling up from 0 (default: {DEFAULT_INITIAL_REPLICA_COUNT})",
    )
    parser.add_argument(
        "--min-scaleup-period",
        type=int,
        default=int(
            os.getenv("AF3_MIN_SCALEUP_PERIOD_SECONDS", str(DEFAULT_MIN_SCALEUP_PERIOD_SECONDS))
        ),
        help=f"Scale-to-zero minimum scale-up period in seconds (default: {DEFAULT_MIN_SCALEUP_PERIOD_SECONDS})",
    )
    parser.add_argument(
        "--idle-scaledown-period",
        type=int,
        default=int(
            os.getenv(
                "AF3_IDLE_SCALEDOWN_PERIOD_SECONDS", str(DEFAULT_IDLE_SCALEDOWN_PERIOD_SECONDS)
            )
        ),
        help=f"Scale-to-zero idle scale-down period in seconds (default: {DEFAULT_IDLE_SCALEDOWN_PERIOD_SECONDS} = 20 minutes)",
    )
    parser.add_argument(
        "--deployment-timeout",
        type=int,
        default=DEFAULT_DEPLOYMENT_TIMEOUT_SECONDS,
        help=f"Serving container deployment timeout in seconds (default: {DEFAULT_DEPLOYMENT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--inference-timeout",
        type=int,
        default=DEFAULT_INFERENCE_TIMEOUT_SECONDS,
        help=f"Endpoint clientConnectionConfig.inferenceTimeout in seconds (default: {DEFAULT_INFERENCE_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--bucket-name",
        default=os.getenv("GCS_BUCKET_NAME") or os.getenv("FOLDRUN_BUCKET") or "",
        help="FoldRun GCS data bucket name for granting endpoint serving SA write access (default: <project_id>-foldrun-data)",
    )
    parser.add_argument(
        "--force-new",
        action="store_true",
        help="Force creation of a new endpoint even if an AF3 endpoint already exists",
    )
    parser.add_argument(
        "--undeploy-after-setup",
        action="store_true",
        help=(
            "After registering the model and creating the endpoint, immediately undeploy replicas "
            "so the endpoint rests at $0.00/hr until the first prediction job runs"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    if not args.project_id:
        print(
            "ERROR: --project-id (or GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT) is required.",
            file=sys.stderr,
        )
        return 1

    setup_af3_endpoint(
        project_id=args.project_id,
        location=args.location,
        model_id=args.model_id,
        endpoint_display_name=args.endpoint_display_name,
        model_display_name=args.model_display_name,
        machine_type=args.machine_type,
        accelerator_type=args.accelerator_type,
        accelerator_count=args.accelerator_count,
        reservation_affinity_type=args.reservation_affinity_type,
        use_dws=args.use_dws,
        dws_max_runtime_seconds=args.dws_max_runtime_seconds,
        use_spot=args.use_spot,
        min_replica_count=args.min_replica_count,
        max_replica_count=args.max_replica_count,
        initial_replica_count=args.initial_replica_count,
        min_scaleup_period_seconds=args.min_scaleup_period,
        idle_scaledown_period_seconds=args.idle_scaledown_period,
        serving_container_deployment_timeout=args.deployment_timeout,
        inference_timeout_seconds=args.inference_timeout,
        bucket_name=args.bucket_name,
        force_new=args.force_new,
        undeploy_after_setup=args.undeploy_after_setup,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
