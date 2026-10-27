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

"""Tests for Filestore location resolution and Regional tier cost estimation."""

from unittest.mock import MagicMock, patch

import pytest
from google.api_core import exceptions as gexc

from foldrun_app.core import batch
from foldrun_app.core.config import CoreConfig
from foldrun_app.skills.cost_estimation import pricing


def _instance(ip="10.1.0.2", network="projects/123456/global/networks/foldrun-network"):
    inst = MagicMock()
    inst.networks = [MagicMock()]
    inst.networks[0].ip_addresses = [ip]
    inst.networks[0].network = network
    return inst


class TestFilestoreLocationCandidates:
    def test_region_before_zone_by_default(self):
        assert batch.filestore_location_candidates(None, "us-central1", "us-central1-a") == [
            "us-central1",
            "us-central1-a",
        ]

    def test_explicit_location_first_and_deduplicated(self):
        assert batch.filestore_location_candidates(
            "us-central1-a", "us-central1", "us-central1-a"
        ) == ["us-central1-a", "us-central1"]

    def test_empty_values_skipped(self):
        assert batch.filestore_location_candidates("", None, "us-central1-a") == ["us-central1-a"]


class TestGetFilestoreInstance:
    def test_regional_instance_found_in_region(self):
        client = MagicMock()
        client.get_instance.return_value = _instance()

        batch.get_filestore_instance(
            client, "proj", "foldrun-nfs", ["us-central1", "us-central1-a"]
        )

        client.get_instance.assert_called_once_with(
            name="projects/proj/locations/us-central1/instances/foldrun-nfs"
        )

    def test_legacy_zonal_instance_falls_back_to_zone(self):
        client = MagicMock()
        expected = _instance()
        client.get_instance.side_effect = [gexc.NotFound("not in region"), expected]

        got = batch.get_filestore_instance(
            client, "proj", "foldrun-nfs", ["us-central1", "us-central1-a"]
        )

        assert got is expected
        assert client.get_instance.call_args_list[1].kwargs["name"] == (
            "projects/proj/locations/us-central1-a/instances/foldrun-nfs"
        )

    def test_not_found_anywhere_raises(self):
        client = MagicMock()
        client.get_instance.side_effect = gexc.NotFound("missing")

        with pytest.raises(gexc.NotFound):
            batch.get_filestore_instance(
                client, "proj", "foldrun-nfs", ["us-central1", "us-central1-a"]
            )

    def test_empty_locations_raises_value_error(self):
        client = MagicMock()
        with pytest.raises(ValueError, match="No Filestore location candidates provided"):
            batch.get_filestore_instance(client, "proj", "foldrun-nfs", [])

    def test_non_not_found_errors_propagate_immediately(self):
        client = MagicMock()
        client.get_instance.side_effect = gexc.PermissionDenied("denied")

        with pytest.raises(gexc.PermissionDenied):
            batch.get_filestore_instance(
                client, "proj", "foldrun-nfs", ["us-central1", "us-central1-a"]
            )
        assert client.get_instance.call_count == 1


class TestGetFilestoreInfo:
    def test_env_override_skips_api(self):
        with patch("google.cloud.filestore_v1.CloudFilestoreManagerClient") as mock_fs:
            ip, net = batch.get_filestore_info(
                project_id="proj",
                zone="us-central1-a",
                filestore_id="foldrun-nfs",
                filestore_ip="10.9.9.9",
                filestore_network="projects/1/global/networks/n",
            )
        assert (ip, net) == ("10.9.9.9", "projects/1/global/networks/n")
        mock_fs.assert_not_called()

    def test_regional_lookup_uses_region(self):
        with patch("google.cloud.filestore_v1.CloudFilestoreManagerClient") as mock_fs:
            mock_fs.return_value.get_instance.return_value = _instance()
            ip, net = batch.get_filestore_info(
                project_id="proj",
                zone="us-central1-a",
                filestore_id="foldrun-nfs",
                region="us-central1",
            )
        assert ip == "10.1.0.2"
        assert net == "projects/123456/global/networks/foldrun-network"
        mock_fs.return_value.get_instance.assert_called_once_with(
            name="projects/proj/locations/us-central1/instances/foldrun-nfs"
        )

    def test_explicit_filestore_location_overrides_region_and_zone(self):
        with patch("google.cloud.filestore_v1.CloudFilestoreManagerClient") as mock_fs:
            mock_fs.return_value.get_instance.return_value = _instance()
            batch.get_filestore_info(
                project_id="proj",
                zone="us-central1-a",
                filestore_id="foldrun-nfs",
                region="us-central1",
                filestore_location="us-central1-b",
            )
        mock_fs.return_value.get_instance.assert_called_once_with(
            name="projects/proj/locations/us-central1-b/instances/foldrun-nfs"
        )

    def test_without_region_keeps_legacy_zone_lookup(self):
        with patch("google.cloud.filestore_v1.CloudFilestoreManagerClient") as mock_fs:
            mock_fs.return_value.get_instance.return_value = _instance()
            batch.get_filestore_info(
                project_id="proj", zone="us-central1-a", filestore_id="foldrun-nfs"
            )
        mock_fs.return_value.get_instance.assert_called_once_with(
            name="projects/proj/locations/us-central1-a/instances/foldrun-nfs"
        )


class TestConfigAndPricingFilestore:
    def test_core_config_filestore_location_property(self, mock_env_vars, monkeypatch):
        monkeypatch.delenv("FILESTORE_LOCATION", raising=False)
        cfg = CoreConfig()
        assert cfg.filestore_location == ""

        monkeypatch.setenv("FILESTORE_LOCATION", "us-central1")
        assert cfg.filestore_location == "us-central1"

    def test_filestore_pricing_regional_default_and_custom_iops(self, monkeypatch):
        monkeypatch.setattr(pricing, "_cached_prices", pricing.DEFAULT_PRICES.copy())
        monkeypatch.delenv("FILESTORE_TIER", raising=False)
        monkeypatch.delenv("FILESTORE_CAPACITY_GB", raising=False)
        monkeypatch.delenv("FILESTORE_MAX_IOPS", raising=False)

        default_est = pricing.estimate_monthly()
        assert default_est["infrastructure"]["items"][0] == {
            "item": "Filestore (1 TiB Regional)",
            "monthly_cost": 460.80,
        }
        assert default_est["infrastructure"]["monthly_total"] == 605.80

        # Custom IOPS formula: $40 + 1024 * $0.21 + 4000 * $0.027 = $363.04
        monkeypatch.setenv("FILESTORE_MAX_IOPS", "4000")
        custom_est = pricing.estimate_monthly()
        assert custom_est["infrastructure"]["items"][0] == {
            "item": "Filestore (1024 GiB Regional, 4000 IOPS)",
            "monthly_cost": 363.04,
        }

        # Legacy BASIC_SSD override: 2560 * $0.30 = $768.00
        monkeypatch.delenv("FILESTORE_MAX_IOPS", raising=False)
        monkeypatch.setenv("FILESTORE_TIER", "BASIC_SSD")
        legacy_est = pricing.estimate_monthly()
        assert legacy_est["infrastructure"]["items"][0] == {
            "item": "Filestore (2560 GiB Basic SSD)",
            "monthly_cost": 768.00,
        }
