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

"""Unit tests for instance template isolation in foldrun_app.core.batch."""

from unittest.mock import MagicMock, patch

from foldrun_app.core.batch import (
    get_machine_resources,
    get_or_create_instance_template,
    submit_batch_job,
)


def _make_mock_template(name: str, network: str, subnet: str, self_link: str) -> MagicMock:
    tmpl = MagicMock()
    tmpl.name = name
    tmpl.self_link = self_link
    nic = MagicMock()
    nic.network = network
    nic.subnetwork = subnet
    tmpl.properties.network_interfaces = [nic]
    return tmpl


class TestInstanceTemplateIsolation:
    """Verify instance templates are isolated by region, network, and subnetwork."""

    @patch("google.cloud.compute_v1.InstanceTemplatesClient")
    def test_cross_region_and_cross_network_templates_do_not_collide(self, mock_client_cls):
        """Templates for different regions/VPCs/subnets must not collide or be reused."""
        store: dict[str, MagicMock] = {}
        client = MagicMock()
        mock_client_cls.return_value = client

        def fake_get(project: str, instance_template: str):
            if instance_template in store:
                return store[instance_template]
            raise Exception(f"NotFound: {instance_template}")

        def fake_insert(project: str, instance_template_resource):
            name = instance_template_resource.name
            nic = instance_template_resource.properties.network_interfaces[0]
            store[name] = _make_mock_template(
                name=name,
                network=nic.network,
                subnet=nic.subnetwork,
                self_link=f"projects/{project}/global/instanceTemplates/{name}",
            )
            op = MagicMock()
            op.result.return_value = None
            return op

        client.get.side_effect = fake_get
        client.insert.side_effect = fake_insert

        link_us_central = get_or_create_instance_template(
            project_id="test-proj",
            machine_type="n1-standard-8",
            network="projects/test-proj/global/networks/vpc-a",
            subnet="projects/test-proj/regions/us-central1/subnetworks/subnet-a",
        )
        link_us_east = get_or_create_instance_template(
            project_id="test-proj",
            machine_type="n1-standard-8",
            network="projects/test-proj/global/networks/vpc-a",
            subnet="projects/test-proj/regions/us-east1/subnetworks/subnet-b",
        )
        link_other_vpc = get_or_create_instance_template(
            project_id="test-proj",
            machine_type="n1-standard-8",
            network="projects/test-proj/global/networks/vpc-b",
            subnet="projects/test-proj/regions/us-central1/subnetworks/subnet-a",
        )

        assert link_us_central != link_us_east
        assert link_us_central != link_other_vpc
        assert link_us_east != link_other_vpc
        assert len(store) == 3

    @patch("google.cloud.compute_v1.InstanceTemplatesClient")
    def test_same_region_and_network_reuses_template(self, mock_client_cls):
        """Identical (machine_type, network, subnet, ssd) reuses existing template."""
        client = MagicMock()
        mock_client_cls.return_value = client

        net = "projects/test-proj/global/networks/vpc-a"
        sub = "projects/test-proj/regions/us-central1/subnetworks/subnet-a"
        existing = _make_mock_template(
            name="existing-template",
            network=f"https://www.googleapis.com/compute/v1/{net}",
            subnet=f"https://www.googleapis.com/compute/v1/{sub}",
            self_link="projects/test-proj/global/instanceTemplates/existing-template",
        )
        client.get.return_value = existing

        link = get_or_create_instance_template(
            project_id="test-proj",
            machine_type="n1-standard-8",
            network=net,
            subnet=sub,
        )

        assert link == existing.self_link
        client.insert.assert_not_called()

    @patch("google.cloud.compute_v1.InstanceTemplatesClient")
    def test_mismatched_existing_template_nic_is_not_reused(self, mock_client_cls):
        """An existing template whose NIC does not match network/subnet is not reused."""
        client = MagicMock()
        mock_client_cls.return_value = client

        wrong_existing = _make_mock_template(
            name="wrong-template",
            network="projects/test-proj/global/networks/other-vpc",
            subnet="projects/test-proj/regions/us-west1/subnetworks/other-subnet",
            self_link="projects/test-proj/global/instanceTemplates/wrong-template",
        )
        created = _make_mock_template(
            name="created-template",
            network="projects/test-proj/global/networks/vpc-a",
            subnet="projects/test-proj/regions/us-central1/subnetworks/subnet-a",
            self_link="projects/test-proj/global/instanceTemplates/created-template",
        )
        client.get.side_effect = [wrong_existing, created]
        op = MagicMock()
        op.result.return_value = None
        client.insert.return_value = op

        link = get_or_create_instance_template(
            project_id="test-proj",
            machine_type="n1-standard-8",
            network="projects/test-proj/global/networks/vpc-a",
            subnet="projects/test-proj/regions/us-central1/subnetworks/subnet-a",
        )

        assert link == created.self_link
        client.insert.assert_called_once()

    @patch("google.cloud.compute_v1.InstanceTemplatesClient")
    def test_shared_vpc_project_id_vs_number_reuses_template(self, mock_client_cls):
        """Shared VPC template returned with host project ID still matches host project number request."""
        client = MagicMock()
        mock_client_cls.return_value = client

        existing = _make_mock_template(
            name="shared-vpc-template",
            network="https://www.googleapis.com/compute/v1/projects/host-proj-id/global/networks/shared-vpc",
            subnet="https://www.googleapis.com/compute/v1/projects/host-proj-id/regions/us-central1/subnetworks/shared-sub",
            self_link="projects/service-proj/global/instanceTemplates/shared-vpc-template",
        )
        client.get.return_value = existing

        link = get_or_create_instance_template(
            project_id="service-proj",
            machine_type="n1-standard-8",
            network="projects/123456789012/global/networks/shared-vpc",
            subnet="projects/123456789012/regions/us-central1/subnetworks/shared-sub",
        )

        assert link == existing.self_link
        client.insert.assert_not_called()


class TestMachineResourceSizing:
    """Verify Cloud Batch CPU and RAM sizing across GCE machine families."""

    def test_n2d_standard_4_fits_within_16_gib_ram(self):
        """boltz2_cache uses n2d-standard-4 (4 vCPU, 16 GiB = 16,384 MiB physical RAM)."""
        cpu_milli, memory_mib = get_machine_resources("n2d-standard-4")
        assert cpu_milli == 4000
        assert memory_mib == 16000
        assert memory_mib < 16384

    def test_n2d_standard_8_uses_all_8_vcpus(self):
        """Database downloads on n2d-standard-8 must allocate 8000 cpu_milli, not 4000."""
        cpu_milli, memory_mib = get_machine_resources("n2d-standard-8")
        assert cpu_milli == 8000
        assert memory_mib == 32000

    def test_n1_and_modern_families(self):
        assert get_machine_resources("n1-standard-4") == (4000, 15000)
        assert get_machine_resources("n1-highmem-32") == (32000, 208000)
        assert get_machine_resources("c2-standard-16") == (16000, 64000)
        assert get_machine_resources("e2-highcpu-8") == (8000, 8000)
        assert get_machine_resources("c3-standard-8-lssd") == (8000, 32000)
        assert get_machine_resources("n2d-custom-8-32768") == (8000, 32000)
        assert get_machine_resources("unknown-type") == (4000, 15000)

    @patch("foldrun_app.core.batch.get_or_create_instance_template")
    @patch("foldrun_app.core.batch.batch_v1.BatchServiceClient")
    def test_submit_batch_job_wires_computed_cpu_and_memory(self, mock_batch_cls, mock_get_tmpl):
        mock_get_tmpl.return_value = "projects/p/global/instanceTemplates/t"
        mock_client = MagicMock()
        mock_created = MagicMock()
        mock_created.name = "projects/p/locations/us-central1/jobs/j1"
        mock_client.create_job.return_value = mock_created
        mock_batch_cls.return_value = mock_client

        submit_batch_job(
            project_id="p",
            region="us-central1",
            zone="us-central1-a",
            job_id="j1",
            script="echo ok",
            machine_type="n2d-standard-8",
            filestore_ip="10.0.0.2",
            filestore_network="projects/123/global/networks/default",
            nfs_share="/datasets",
            nfs_mount="/mnt/nfs/foldrun",
            subnet_name="default",
        )

        req = mock_client.create_job.call_args.kwargs["request"]
        cr = req.job.task_groups[0].task_spec.compute_resource
        assert cr.cpu_milli == 8000
        assert cr.memory_mib == 32000
