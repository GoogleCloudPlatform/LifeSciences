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

import hashlib
import logging
import os
from typing import Any

from google.cloud import batch_v1alpha as batch_v1

from foldrun_app.core.hardware import resolve_boot_disk_type

logger = logging.getLogger(__name__)

# Ranked Gen 3/4 InstanceFlexibilityPolicy selections for Cloud Batch jobs.
# Keeping all candidate machine types in Gen 3 (C3, C3D) and Gen 4 (C4, C4D, N4, N4D)
# ensures every selection shares a single hyperdisk-balanced boot disk InstanceTemplate
# with zero extra template disks.
BATCH_INSTANCE_SELECTIONS: dict[str, tuple[tuple[str, ...], ...]] = {
    "c4-standard-4-lssd": (
        ("c4-standard-4-lssd", "c4d-standard-4-lssd"),
        ("c3-standard-4-lssd", "c3d-standard-4-lssd"),
    ),
    "c4-highmem-32-lssd": (
        ("c4-highmem-32-lssd", "c4d-highmem-32-lssd"),
        ("c3d-highmem-30-lssd",),
    ),
    "n4-standard-4": (
        ("n4-standard-4", "n4d-standard-4"),
        ("c4-standard-4", "c4d-standard-4", "c3-standard-4", "c3d-standard-4"),
    ),
    "n4-standard-2": (
        ("n4-standard-2", "n4d-standard-2"),
        ("c4-standard-2", "c4d-standard-2"),
        ("c3-standard-4", "c3d-standard-4"),
    ),
}


def filestore_location_candidates(
    location: str | None = None,
    region: str | None = None,
    zone: str | None = None,
) -> list[str]:
    """Ordered, de-duplicated Filestore locations to try.

    REGIONAL/ENTERPRISE instances live in a region and BASIC/ZONAL instances in
    a zone. An explicit FILESTORE_LOCATION wins. Otherwise the region is tried
    before the zone so both regional and zonal instances resolve automatically.
    """
    candidates: list[str] = []
    for loc in (location, region, zone):
        if loc and loc not in candidates:
            candidates.append(loc)
    return candidates


def get_filestore_instance(client, project_id: str, filestore_id: str, locations: list[str]):
    """Fetch a Filestore instance, trying each candidate location in order.

    Raises the last NotFound error if the instance is not found in any location.
    """
    from google.api_core import exceptions as gexc

    if not locations:
        raise ValueError("No Filestore location candidates provided")

    last_error: Exception | None = None
    for loc in locations:
        name = f"projects/{project_id}/locations/{loc}/instances/{filestore_id}"
        try:
            instance = client.get_instance(name=name)
            logger.info(f"Found Filestore instance {filestore_id} in {loc}")
            return instance
        except gexc.NotFound as e:
            logger.debug(f"Filestore {filestore_id} not found in {loc}")
            last_error = e
    assert last_error is not None
    raise last_error


def get_filestore_info(
    project_id: str,
    zone: str,
    filestore_id: str,
    filestore_ip: str | None = None,
    filestore_network: str | None = None,
    region: str | None = None,
    filestore_location: str | None = None,
    network_project_number: str | None = None,
):
    """Get Filestore IP and network, from env vars or Filestore API.

    The instance is looked up in ``filestore_location`` if set, otherwise in
    ``region`` (opt-in REGIONAL/ENTERPRISE tiers) and then ``zone`` (default
    BASIC_SSD and ZONAL/BASIC_HDD tiers).

    Returns:
        Tuple of (filestore_ip, filestore_network) where network is fully
        qualified as projects/{number}/global/networks/{name}.
    """
    if filestore_ip and filestore_network:
        return filestore_ip, filestore_network

    from google.cloud import filestore_v1

    client = filestore_v1.CloudFilestoreManagerClient()
    instance = get_filestore_instance(
        client,
        project_id,
        filestore_id,
        filestore_location_candidates(filestore_location, region, zone),
    )
    ip = instance.networks[0].ip_addresses[0]
    network = instance.networks[0].network

    parts = network.split("/")
    if len(parts) >= 5:
        network_project = parts[1]
        network_name = parts[4]
        if network_project.isdigit():
            network = f"projects/{network_project}/global/networks/{network_name}"
        elif network_project_number:
            network = f"projects/{network_project_number}/global/networks/{network_name}"
        else:
            try:
                net_project_number = get_project_number(network_project)
                network = f"projects/{net_project_number}/global/networks/{network_name}"
            except Exception as e:
                logger.warning(
                    f"Failed to get project number for {network_project}: {e}. Using project ID."
                )
                network = f"projects/{network_project}/global/networks/{network_name}"
    else:
        network_name = parts[-1]
        if network_project_number:
            proj_num = network_project_number
        else:
            try:
                proj_num = get_project_number(project_id)
            except Exception as e:
                logger.warning(
                    f"Failed to get project number for {project_id}: {e}. Using project ID."
                )
                proj_num = project_id
        network = f"projects/{proj_num}/global/networks/{network_name}"

    logger.info(f"Retrieved Filestore info: IP={ip}, Network={network}")
    return ip, network


def get_project_number(project_id: str) -> str:
    """Get the GCP project number from the project ID."""
    from google.cloud import resourcemanager_v3

    client = resourcemanager_v3.ProjectsClient()
    project = client.get_project(name=f"projects/{project_id}")
    return project.name.split("/")[-1]


def _extract_network_project(network: str, default_project: str) -> str:
    """Extract the host project from a 'projects/<host>/global/networks/<vpc>' string."""
    if network and network.startswith("projects/") and "/global/networks/" in network:
        host = network.split("/", 2)[1]
        if host:
            return host
    return default_project


def resolve_subnet(project_id: str, region: str, network: str) -> str:
    """Find the subnet name for a VPC network in a given region."""
    from google.cloud import compute_v1

    host_project = _extract_network_project(network, project_id)
    vpc_name = network.split("/")[-1] if "/" in network else network
    subnets_client = compute_v1.SubnetworksClient()
    request = compute_v1.ListSubnetworksRequest(
        project=host_project,
        region=region,
    )
    matching = [
        s
        for s in subnets_client.list(request=request)
        if s.network.endswith(f"/networks/{vpc_name}")
    ]
    if not matching:
        raise RuntimeError(
            f"No subnets found for VPC '{vpc_name}' in {region}. "
            "Batch VMs need a subnet on the same VPC as Filestore."
        )
    return matching[0].name


def _template_nic_matches(existing: Any, network: str, subnet: str) -> bool:
    """Return True if an existing InstanceTemplate's primary NIC matches network and subnet."""
    nics = getattr(getattr(existing, "properties", None), "network_interfaces", None)
    if not nics:
        return False
    nic = nics[0]
    existing_net = getattr(nic, "network", "") or ""
    existing_sub = getattr(nic, "subnetwork", "") or ""
    net_suffix = (
        f"/global/networks/{network.split('/global/networks/')[-1]}"
        if "/global/networks/" in network
        else f"/networks/{network.lstrip('/')}"
    )
    sub_suffix = (
        f"/regions/{subnet.split('/regions/')[-1]}"
        if "/regions/" in subnet
        else f"/subnetworks/{subnet.lstrip('/')}"
    )
    return existing_net.endswith(net_suffix) and existing_sub.endswith(sub_suffix)


def _template_boot_disk_matches(existing: Any, expected_boot_disk_type: str) -> bool:
    """Return True if an existing InstanceTemplate's boot disk type matches ``expected_boot_disk_type``."""
    disks = getattr(getattr(existing, "properties", None), "disks", None)
    if not isinstance(disks, (list, tuple)) or not disks:
        return True
    init_params = getattr(disks[0], "initialize_params", None)
    disk_type = getattr(init_params, "disk_type", None)
    if not isinstance(disk_type, str) or not disk_type.strip():
        return True
    return disk_type.strip().lower().endswith(expected_boot_disk_type.lower())


def _is_lssd_machine_type(machine_type: str) -> bool:
    """Return True if ``machine_type`` bundles Titanium Local SSDs natively (``*-lssd``)."""
    return (machine_type or "").strip().lower().endswith("-lssd")


def _build_lssd_mount_preamble(local_ssd_count: int) -> str:
    """Build an idempotent bash preamble that formats and mounts bundled ``-lssd`` NVMe disks.

    Only creates ``/mnt/scratch*`` when the underlying Titanium NVMe block device
    (``/dev/disk/by-id/google-local-nvme-ssd-N``) is present on the provisioned VM.
    If ``instance_flexibility_policy`` falls back to a non-``-lssd`` shape (such as
    ``n4-standard-4``), ``/mnt/scratch`` is left uncreated so the download script's
    ``if [ ! -d /mnt/scratch ]`` fallback symlinks ``/mnt/scratch`` to ``{dest}/scratch``
    on Filestore NFS.
    """
    if local_ssd_count <= 0:
        return ""
    lines = ["# Mount bundled Titanium Local SSDs (-lssd) if present and not already mounted"]
    for i in range(local_ssd_count):
        suffix = str(i) if i > 0 else ""
        mount_dir = f"/mnt/scratch{suffix}"
        lines.extend(
            [
                f"if ! mountpoint -q {mount_dir} 2>/dev/null; then",
                f"  _SSD_DEV=/dev/disk/by-id/google-local-nvme-ssd-{i}",
                '  if [ -b "$_SSD_DEV" ]; then',
                f"    mkdir -p {mount_dir}",
                '    mkfs.ext4 -F -q "$_SSD_DEV" >/dev/null 2>&1 || true',
                f'    mount "$_SSD_DEV" {mount_dir} 2>/dev/null || true',
                "  fi",
                "fi",
            ]
        )
    return "\n".join(lines) + "\n"


def build_instance_flexibility_policy(
    machine_type: str,
) -> batch_v1.AllocationPolicy.InstanceFlexibilityPolicy:
    """Build a ranked Gen 3/4 Cloud Batch ``InstanceFlexibilityPolicy`` for ``machine_type``."""
    req = (machine_type or "n4-standard-2").strip().lower()
    ranks = BATCH_INSTANCE_SELECTIONS.get(req, ((req,),))
    policy = batch_v1.AllocationPolicy.InstanceFlexibilityPolicy()
    for idx, candidates in enumerate(ranks, start=1):
        selection = batch_v1.AllocationPolicy.InstanceSelection()
        selection.machine_types = list(candidates)
        selection.rank = idx
        policy.instance_selections[f"rank-{idx}"] = selection
    return policy


def get_or_create_instance_template(
    project_id: str,
    machine_type: str,
    network: str,
    subnet: str,
    local_ssd_count: int = 0,
) -> str:
    """Create/reuse a Shielded VM instance template. Returns self_link."""
    from google.cloud import compute_v1

    boot_disk_type = resolve_boot_disk_type(machine_type, legacy_default="pd-balanced")
    uses_hyperdisk = boot_disk_type == "hyperdisk-balanced"
    effective_ssd_count = (
        0 if (_is_lssd_machine_type(machine_type) or uses_hyperdisk) else local_ssd_count
    )

    net_hash = hashlib.sha256(f"{network}:{subnet}:{boot_disk_type}".encode()).hexdigest()[:8]
    safe_name = f"foldrun-batch-{machine_type.replace('_', '-')}"
    if effective_ssd_count > 0:
        safe_name += f"-{effective_ssd_count}ssd"
    safe_name += f"-{net_hash}"

    client = compute_v1.InstanceTemplatesClient()

    try:
        existing = client.get(project=project_id, instance_template=safe_name)
        if _template_nic_matches(existing, network, subnet) and _template_boot_disk_matches(
            existing, boot_disk_type
        ):
            logger.info(f"Reusing instance template: {safe_name}")
            return existing.self_link
        replacement_suffix = hashlib.sha256(
            f"{safe_name}:{network}:{subnet}:{boot_disk_type}".encode()
        ).hexdigest()[:6]
        safe_name = f"{safe_name}-{replacement_suffix}"
        logger.warning(
            f"Existing instance template has mismatched network/subnet or boot disk; "
            f"creating isolated replacement template {safe_name}."
        )
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
    init.disk_type = boot_disk_type
    init.disk_size_gb = 50
    disk.initialize_params = init
    props.disks = [disk]

    for i in range(effective_ssd_count):
        scratch_disk = compute_v1.AttachedDisk()
        scratch_disk.type_ = "SCRATCH"
        scratch_disk.interface = "NVME"
        scratch_disk.auto_delete = True
        scratch_disk.device_name = f"local-ssd-{i}"
        init_ssd = compute_v1.AttachedDiskInitializeParams()
        init_ssd.disk_type = "local-ssd"
        scratch_disk.initialize_params = init_ssd
        props.disks.append(scratch_disk)

    nic = compute_v1.NetworkInterface()
    nic.network = network
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
    filestore_network: str,
    nfs_share: str,
    nfs_mount: str,
    labels: dict[str, str] | None = None,
    subnet_name: str | None = None,
    local_ssd_count: int = 0,
) -> dict[str, Any]:
    """Submit a Cloud Batch job with NFS volume mount and Gen 3/4 InstanceFlexibilityPolicy.

    Returns dict with job_id, job_name, console_url, machine_type.
    """
    client = batch_v1.BatchServiceClient()

    primary_machine = (machine_type or "n4-standard-2").strip().lower()
    uses_hyperdisk = (
        resolve_boot_disk_type(primary_machine, legacy_default="pd-balanced")
        == "hyperdisk-balanced"
    )
    template_ssd_count = (
        0 if (_is_lssd_machine_type(primary_machine) or uses_hyperdisk) else local_ssd_count
    )
    lssd_mount_count = (
        local_ssd_count
        if local_ssd_count > 0
        else (1 if _is_lssd_machine_type(primary_machine) else 0)
    )
    lssd_preamble = (
        _build_lssd_mount_preamble(lssd_mount_count)
        if lssd_mount_count > 0 and template_ssd_count == 0
        else ""
    )

    runnable = batch_v1.Runnable()
    runnable.script = batch_v1.Runnable.Script()
    runnable.script.text = f"#!/bin/bash\n{lssd_preamble}{script}"

    task_spec = batch_v1.TaskSpec()
    task_spec.runnables = [runnable]
    task_spec.max_retry_count = 1
    task_spec.max_run_duration = "86400s"

    nfs_volume = batch_v1.Volume()
    nfs_volume.nfs = batch_v1.NFS()
    nfs_volume.nfs.server = filestore_ip
    nfs_volume.nfs.remote_path = nfs_share
    nfs_volume.mount_path = nfs_mount

    volumes = [nfs_volume]
    for i in range(template_ssd_count):
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

    if not subnet_name:
        subnet_name = os.getenv("SUBNET_ID") or resolve_subnet(
            project_id, region, filestore_network
        )

    host_project = _extract_network_project(filestore_network, project_id)
    if "global/networks/" in filestore_network:
        batch_network = filestore_network
    else:
        net_name = (
            filestore_network.split("/")[-1] if "/" in filestore_network else filestore_network
        )
        batch_network = f"projects/{host_project}/global/networks/{net_name}"

    if subnet_name.startswith("projects/"):
        batch_subnet = subnet_name
    else:
        batch_subnet = f"projects/{host_project}/regions/{region}/subnetworks/{subnet_name}"

    instance_template_url = get_or_create_instance_template(
        project_id=project_id,
        machine_type=primary_machine,
        network=batch_network,
        subnet=batch_subnet,
        local_ssd_count=template_ssd_count,
    )

    instances = batch_v1.AllocationPolicy.InstancePolicyOrTemplate()
    instances.instance_template = instance_template_url

    allocation_policy = batch_v1.AllocationPolicy()
    allocation_policy.instances = [instances]
    allocation_policy.instance_flexibility_policy = build_instance_flexibility_policy(
        primary_machine
    )
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
        "machine_type": primary_machine,
    }
