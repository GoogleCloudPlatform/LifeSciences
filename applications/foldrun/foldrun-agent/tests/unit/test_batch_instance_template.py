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

from foldrun_app.core.batch import get_or_create_instance_template


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
