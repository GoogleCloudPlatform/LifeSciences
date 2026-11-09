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
    build_instance_flexibility_policy,
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
        first_queried_name = client.get.call_args_list[0].kwargs["instance_template"]
        inserted_name = client.insert.call_args.kwargs["instance_template_resource"].name
        assert inserted_name != first_queried_name
        assert inserted_name.startswith(first_queried_name + "-")

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


class TestInstanceFlexibilityAndTemplates:
    """Verify Cloud Batch Gen 3/4 InstanceFlexibilityPolicy and instance template setup."""

    def test_build_instance_flexibility_policy(self):
        """Gen 3/4 InstanceFlexibilityPolicy ranks C4/C4D/C3/C3D/N4/N4D shapes."""
        # 1. c4-standard-4-lssd -> rank 1: [c4-standard-4-lssd, c4d-standard-4-lssd],
        #    rank 2: [c3-standard-4-lssd, c3d-standard-4-lssd]
        p_c4 = build_instance_flexibility_policy("c4-standard-4-lssd")
        assert list(p_c4.instance_selections["rank-1"].machine_types) == [
            "c4-standard-4-lssd",
            "c4d-standard-4-lssd",
        ]
        assert p_c4.instance_selections["rank-1"].rank == 1
        assert list(p_c4.instance_selections["rank-2"].machine_types) == [
            "c3-standard-4-lssd",
            "c3d-standard-4-lssd",
        ]
        assert p_c4.instance_selections["rank-2"].rank == 2
        assert "rank-3" not in p_c4.instance_selections

        # 2. c4-highmem-32-lssd -> rank 1: [c4-highmem-32-lssd, c4d-highmem-32-lssd],
        #    rank 2: [c3d-highmem-30-lssd]
        p_hm = build_instance_flexibility_policy("c4-highmem-32-lssd")
        assert list(p_hm.instance_selections["rank-1"].machine_types) == [
            "c4-highmem-32-lssd",
            "c4d-highmem-32-lssd",
        ]
        assert list(p_hm.instance_selections["rank-2"].machine_types) == ["c3d-highmem-30-lssd"]
        assert "rank-3" not in p_hm.instance_selections

        # 3. n4-standard-2 -> rank 1: [n4-standard-2, n4d-standard-2],
        #    rank 2: [c4-standard-2, c4d-standard-2],
        #    rank 3: [c3-standard-4, c3d-standard-4]
        p_n4_2 = build_instance_flexibility_policy("n4-standard-2")
        assert list(p_n4_2.instance_selections["rank-1"].machine_types) == [
            "n4-standard-2",
            "n4d-standard-2",
        ]
        assert list(p_n4_2.instance_selections["rank-2"].machine_types) == [
            "c4-standard-2",
            "c4d-standard-2",
        ]
        assert list(p_n4_2.instance_selections["rank-3"].machine_types) == [
            "c3-standard-4",
            "c3d-standard-4",
        ]

        # 4. n4-standard-4 -> rank 1: [n4-standard-4, n4d-standard-4],
        #    rank 2: [c4-standard-4, c4d-standard-4, c3-standard-4, c3d-standard-4]
        p_n4_4 = build_instance_flexibility_policy("n4-standard-4")
        assert list(p_n4_4.instance_selections["rank-1"].machine_types) == [
            "n4-standard-4",
            "n4d-standard-4",
        ]
        assert list(p_n4_4.instance_selections["rank-2"].machine_types) == [
            "c4-standard-4",
            "c4d-standard-4",
            "c3-standard-4",
            "c3d-standard-4",
        ]

    @patch("google.cloud.compute_v1.InstanceTemplatesClient")
    def test_gen4_lssd_and_hyperdisk_instance_templates(self, mock_client_cls):
        """Gen 3/4 Hyperdisk shapes create a single boot-disk-only template; legacy Gen 1/2 attaches SCRATCH SSDs."""
        inserted = []
        client = MagicMock()
        mock_client_cls.return_value = client

        def fake_get(project: str, instance_template: str):
            for t in inserted:
                if t.name == instance_template:
                    m = _make_mock_template(
                        name=t.name,
                        network=t.properties.network_interfaces[0].network,
                        subnet=t.properties.network_interfaces[0].subnetwork,
                        self_link=f"projects/{project}/global/instanceTemplates/{t.name}",
                    )
                    m.properties.disks = t.properties.disks
                    return m
            raise Exception("NotFound")

        def fake_insert(project: str, instance_template_resource):
            inserted.append(instance_template_resource)
            op = MagicMock()
            op.result.return_value = None
            return op

        client.get.side_effect = fake_get
        client.insert.side_effect = fake_insert

        get_or_create_instance_template(
            project_id="p",
            machine_type="c4-standard-4-lssd",
            network="projects/p/global/networks/default",
            subnet="projects/p/regions/us-central1/subnetworks/default",
            local_ssd_count=1,
        )
        c4_tmpl = inserted[-1]
        assert len(c4_tmpl.properties.disks) == 1
        assert c4_tmpl.properties.disks[0].initialize_params.disk_type == "hyperdisk-balanced"

        get_or_create_instance_template(
            project_id="p",
            machine_type="n4-standard-4",
            network="projects/p/global/networks/default",
            subnet="projects/p/regions/us-central1/subnetworks/default",
            local_ssd_count=1,
        )
        n4_tmpl = inserted[-1]
        assert len(n4_tmpl.properties.disks) == 1
        assert n4_tmpl.properties.disks[0].initialize_params.disk_type == "hyperdisk-balanced"

        get_or_create_instance_template(
            project_id="p",
            machine_type="n2d-standard-8",
            network="projects/p/global/networks/default",
            subnet="projects/p/regions/us-central1/subnetworks/default",
            local_ssd_count=1,
        )
        n2d_tmpl = inserted[-1]
        assert len(n2d_tmpl.properties.disks) == 2
        assert n2d_tmpl.properties.disks[0].initialize_params.disk_type == "pd-balanced"
        assert n2d_tmpl.properties.disks[1].type_ == "SCRATCH"
        assert n2d_tmpl.properties.disks[1].initialize_params.disk_type == "local-ssd"

    def test_databases_yaml_uses_right_sized_gen4_machine_types(self):
        """databases.yaml entries use right-sized Gen 4 shapes (n4-standard-2/4, c4-standard-4-lssd) without redundant local_ssd_count."""
        from foldrun_app.core.download import load_manifest

        dbs = load_manifest()["databases"]
        assert dbs["boltz2_cache"]["machine_type"] == "n4-standard-2"
        assert dbs["esmfold2_fast_cache"]["machine_type"] == "n4-standard-4"
        assert dbs["esmfold2_cache"]["machine_type"] == "n4-standard-4"
        for ssd_db in ("uniref90", "mgnify", "uniprot", "pdb_mmcif", "pdb70", "uniref30"):
            assert dbs[ssd_db]["machine_type"] == "c4-standard-4-lssd"
            assert "local_ssd_count" not in dbs[ssd_db]

    @patch("foldrun_app.core.batch.get_or_create_instance_template")
    @patch("foldrun_app.core.batch.batch_v1.BatchServiceClient")
    def test_submit_batch_job_wires_lssd_preamble_and_flexibility_policy(
        self, mock_batch_cls, mock_get_tmpl
    ):
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
            machine_type="c4-standard-4-lssd",
            filestore_ip="10.0.0.2",
            filestore_network="projects/123/global/networks/default",
            nfs_share="/datasets",
            nfs_mount="/mnt/nfs/foldrun",
            subnet_name="default",
        )

        req = mock_client.create_job.call_args.kwargs["request"]
        script_text = req.job.task_groups[0].task_spec.runnables[0].script.text
        assert "_SSD_DEV=/dev/disk/by-id/google-local-nvme-ssd-0" in script_text
        assert 'if [ -b "$_SSD_DEV" ]; then\n    mkdir -p /mnt/scratch' in script_text
        flex = req.job.allocation_policy.instance_flexibility_policy
        assert list(flex.instance_selections["rank-1"].machine_types) == [
            "c4-standard-4-lssd",
            "c4d-standard-4-lssd",
        ]
        assert list(flex.instance_selections["rank-2"].machine_types) == [
            "c3-standard-4-lssd",
            "c3d-standard-4-lssd",
        ]
        assert "rank-3" not in flex.instance_selections
