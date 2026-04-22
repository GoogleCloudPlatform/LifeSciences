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

"""Shared Cloud Batch job submission utilities.

Provides NFS-mounted Batch job submission for any model's download tools.
"""

import logging
import os
from typing import Any, Dict, Optional


from google.cloud import batch_v1

logger = logging.getLogger(__name__)

# Machine type -> memory in MiB (Batch defaults to 2GB without this)
MACHINE_MEMORY_MB = {
    "n1-standard-4": 15000,
    "n1-standard-8": 30000,
    "n1-standard-16": 60000,
    "n1-standard-32": 120000,
    "n1-highmem-8": 52000,
    "n1-highmem-16": 104000,
    "n1-highmem-32": 208000,
}


def get_or_create_instance_template(
    project_id: str,
    machine_type: str,
    subnet: str,
    local_ssd_count: int = 0,
) -> str:
    """Create/reuse a Shielded VM instance template. Returns self_link."""
    from google.cloud import compute_v1

    safe_name = f"foldrun-batch-{machine_type.replace('_', '-')}"
    if local_ssd_count > 0:
        safe_name += f"-{local_ssd_count}ssd"

    client = compute_v1.InstanceTemplatesClient()

    try:
        existing = client.get(project=project_id, instance_template=safe_name)
        logger.info(f"Reusing instance template: {safe_name}")
        return existing.self_link
    except Exception:
        pass

    template = compute_v1.InstanceTemplate()
    template.name = safe_name
    props = compute_v1.InstanceProperties()
    props.machine_type = machine_type

    disk = compute_v1.AttachedDisk()
    disk.auto_delete = True
    disk.boot = True
    init = compute_v1.AttachedDiskInitializeParams()
    init.source_image = "projects/debian-cloud/global/images/family/debian-12"
    init.disk_type = "pd-balanced"
    init.disk_size_gb = 50
    disk.initialize_params = init
    props.disks = [disk]

    for i in range(local_ssd_count):
        local_ssd = compute_v1.AttachedDisk()
        local_ssd.auto_delete = True
        local_ssd.type_ = "SCRATCH"
        local_ssd.interface = "NVME"
        local_ssd.device_name = f"local-ssd-{i}"
        init_ssd = compute_v1.AttachedDiskInitializeParams()
        init_ssd.disk_type = "local-ssd"
        local_ssd.initialize_params = init_ssd
        props.disks.append(local_ssd)

    nic = compute_v1.NetworkInterface()
    nic.subnetwork = subnet
    props.network_interfaces = [nic]

    shielded = compute_v1.ShieldedInstanceConfig()
    shielded.enable_secure_boot = True
    shielded.enable_vtpm = True
    shielded.enable_integrity_monitoring = True
    props.shielded_instance_config = shielded

    sa = compute_v1.ServiceAccount()
    sa.email = f"batch-compute-sa@{project_id}.iam.gserviceaccount.com"
    sa.scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    props.service_accounts = [sa]

    template.properties = props

    logger.info(f"Creating instance template: {safe_name}")
    op = client.insert(project=project_id, instance_template_resource=template)
    op.result()
    created = client.get(project=project_id, instance_template=safe_name)
    return created.self_link


def submit_batch_job(
    project_id: str,
    region: str,
    zone: str,
    job_id: str,
    script: str,
    machine_type: str,
    filestore_ip: str,
    nfs_share: str,
    nfs_mount: str,
    labels: Optional[Dict[str, str]] = None,
    subnet_id: Optional[str] = None,
    local_ssd_count: int = 0,
) -> Dict[str, Any]:
    """Submit a Cloud Batch job with NFS volume mount.

    Returns dict with job_id, job_name, console_url.
    """
    client = batch_v1.BatchServiceClient()

    runnable = batch_v1.Runnable()
    runnable.script = batch_v1.Runnable.Script()
    runnable.script.text = f"#!/bin/bash\n{script}"

    task_spec = batch_v1.TaskSpec()
    task_spec.runnables = [runnable]
    task_spec.max_retry_count = 1
    task_spec.max_run_duration = "86400s"

    compute_resource = batch_v1.ComputeResource()
    if machine_type.startswith("n1-"):
        compute_resource.cpu_milli = int(machine_type.split("-")[-1]) * 1000
    else:
        compute_resource.cpu_milli = 4000
    compute_resource.memory_mib = MACHINE_MEMORY_MB.get(machine_type, 30000)
    task_spec.compute_resource = compute_resource

    nfs_volume = batch_v1.Volume()
    nfs_volume.nfs = batch_v1.NFS()
    nfs_volume.nfs.server = filestore_ip
    nfs_volume.nfs.remote_path = nfs_share
    nfs_volume.mount_path = nfs_mount

    volumes = [nfs_volume]
    for i in range(local_ssd_count):
        ssd_volume = batch_v1.Volume()
        ssd_volume.device_name = f"local-ssd-{i}"
        # Mount to /mnt/scratch for the first SSD, /mnt/scratch1 etc. for others
        mount_suffix = str(i) if i > 0 else ""
        ssd_volume.mount_path = f"/mnt/scratch{mount_suffix}"
        volumes.append(ssd_volume)
    task_spec.volumes = volumes

    task_group = batch_v1.TaskGroup()
    task_group.task_spec = task_spec
    task_group.task_count = 1
    task_group.parallelism = 1

    if not subnet_id:
        subnet_id = os.getenv("SUBNET_ID")
        if not subnet_id:
            raise ValueError("SUBNET_ID environment variable must be set.")

    instance_template_url = get_or_create_instance_template(
        project_id=project_id,
        machine_type=machine_type,
        subnet=subnet_id,
        local_ssd_count=local_ssd_count,
    )

    instances = batch_v1.AllocationPolicy.InstancePolicyOrTemplate()
    instances.instance_template = instance_template_url

    allocation_policy = batch_v1.AllocationPolicy()
    allocation_policy.instances = [instances]
    allocation_policy.location = batch_v1.AllocationPolicy.LocationPolicy()
    allocation_policy.location.allowed_locations = [f"zones/{zone}"]

    # Match the service account from the instance template to avoid mismatch error
    sa = batch_v1.ServiceAccount()
    sa.email = f"batch-compute-sa@{project_id}.iam.gserviceaccount.com"
    allocation_policy.service_account = sa

    logs_policy = batch_v1.LogsPolicy()
    logs_policy.destination = batch_v1.LogsPolicy.Destination.CLOUD_LOGGING

    job = batch_v1.Job()
    job.task_groups = [task_group]
    job.allocation_policy = allocation_policy
    job.logs_policy = logs_policy
    if labels:
        job.labels = labels

    request = batch_v1.CreateJobRequest(
        parent=f"projects/{project_id}/locations/{region}",
        job_id=job_id,
        job=job,
    )
    created_job = client.create_job(request=request)

    console_url = (
        f"https://console.cloud.google.com/batch/jobsDetail/regions/"
        f"{region}/jobs/{job_id}?project={project_id}"
    )

    return {
        "job_id": job_id,
        "job_name": created_job.name,
        "console_url": console_url,
    }
