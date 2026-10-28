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

"""Tests for Filestore multi-location resolution and multi-tier cost estimation."""

import sys
from types import SimpleNamespace
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


def _mock_sku(description: str, prices_usd: list[float], regions=("us-central1",)):
    tiered_rates = []
    for p in prices_usd:
        units = int(p)
        nanos = round((p - units) * 1_000_000_000)
        tiered_rates.append(SimpleNamespace(unit_price=SimpleNamespace(units=units, nanos=nanos)))
    return SimpleNamespace(
        description=description,
        service_regions=list(regions),
        pricing_info=[
            SimpleNamespace(pricing_expression=SimpleNamespace(tiered_rates=tiered_rates))
        ],
    )


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

    def test_zonal_instance_falls_back_to_zone(self):
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

    def test_without_region_keeps_zone_lookup(self):
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

    def test_filestore_pricing_basic_ssd_default_and_tier_overrides(self, monkeypatch):
        monkeypatch.setattr(pricing, "_cached_prices", pricing.DEFAULT_PRICES.copy())
        monkeypatch.delenv("FILESTORE_TIER", raising=False)
        monkeypatch.delenv("FILESTORE_CAPACITY_GB", raising=False)
        monkeypatch.delenv("FILESTORE_MAX_IOPS", raising=False)

        # Default BASIC_SSD (2.5 TiB): 2560 * $0.30 = $768.00 ($913.00 infra total)
        default_est = pricing.estimate_monthly()
        assert default_est["infrastructure"]["items"][0] == {
            "item": "Filestore (2.5 TB Basic SSD)",
            "monthly_cost": 768.00,
        }
        assert default_est["infrastructure"]["monthly_total"] == 913.00

        # Opt-in ZONAL (capacity-based): 2048 * $0.25 = $512.00
        monkeypatch.setenv("FILESTORE_TIER", "ZONAL")
        monkeypatch.setenv("FILESTORE_CAPACITY_GB", "2048")
        zonal_est = pricing.estimate_monthly()
        assert zonal_est["infrastructure"]["items"][0] == {
            "item": "Filestore (2048 GiB Zonal)",
            "monthly_cost": 512.00,
        }

        # Opt-in ZONAL custom performance: $20 + 2048 * $0.12 + 16000 * $0.0145 = $497.76
        monkeypatch.setenv("FILESTORE_MAX_IOPS", "16000")
        zonal_custom_est = pricing.estimate_monthly()
        assert zonal_custom_est["infrastructure"]["items"][0] == {
            "item": "Filestore (2048 GiB Zonal, 16000 IOPS)",
            "monthly_cost": 497.76,
        }

        # Opt-in REGIONAL (capacity-based): 2048 * $0.45 = $921.60
        monkeypatch.setenv("FILESTORE_TIER", "REGIONAL")
        monkeypatch.delenv("FILESTORE_MAX_IOPS", raising=False)
        regional_est = pricing.estimate_monthly()
        assert regional_est["infrastructure"]["items"][0] == {
            "item": "Filestore (2048 GiB Regional)",
            "monthly_cost": 921.60,
        }

        # Opt-in REGIONAL custom IOPS: $40 + 1024 * $0.21 + 4000 * $0.027 = $363.04
        monkeypatch.setenv("FILESTORE_CAPACITY_GB", "1024")
        monkeypatch.setenv("FILESTORE_MAX_IOPS", "4000")
        regional_custom_est = pricing.estimate_monthly()
        assert regional_custom_est["infrastructure"]["items"][0] == {
            "item": "Filestore (1024 GiB Regional, 4000 IOPS)",
            "monthly_cost": 363.04,
        }

        # BASIC_HDD & ZONAL High Scale (>= 10 TiB) standalone helper
        assert pricing.estimate_filestore_monthly_cost("BASIC_HDD", 2048) == (
            "Filestore (2048 GiB Basic HDD)",
            327.68,
        )
        assert pricing.estimate_filestore_monthly_cost("ZONAL", 10240) == (
            "Filestore (10240 GiB Zonal)",
            2304.00,
        )

    def test_fetch_live_prices_disambiguates_regional_and_zonal_skus(self, monkeypatch):
        compute_skus = [
            _mock_sku("Compute optimized Core running in Iowa", [0.041]),
            _mock_sku("Compute optimized Ram running in Iowa", [0.005]),
            _mock_sku("G2 Instance Core running in Iowa", [0.025]),
            _mock_sku("G2 Instance Ram running in Iowa", [0.003]),
            _mock_sku("A2 Instance Core running in Iowa", [0.031]),
            _mock_sku("A2 Instance Ram running in Iowa", [0.004]),
            _mock_sku("Nvidia L4 GPU running in Iowa", [0.56]),
            _mock_sku("Nvidia Tesla A100 GPU running in Iowa", [2.93]),
        ]
        # Intentionally place "Filestore Instance Capacity ..." AFTER "Filestore Capacity ..."
        # to verify custom-performance capacity SKUs never overwrite capacity-based SKUs.
        filestore_skus = [
            _mock_sku("Filestore Capacity Basic SSD (Premium) Iowa", [0.30]),
            _mock_sku("Filestore Capacity Basic HDD (Standard) Iowa", [0.16]),
            _mock_sku("Filestore Capacity Regional and Enterprise Iowa", [0.45]),
            _mock_sku("Filestore Capacity Zonal and High Scale Iowa", [0.0, 0.25, 0.225]),
            _mock_sku("Filestore Instance Capacity Regional Iowa", [0.21]),
            _mock_sku("Filestore Instance Capacity Zonal Iowa", [0.12]),
            _mock_sku("Filestore Instance Count Regional Iowa", [40.0]),
            _mock_sku("Filestore Instance Count Zonal Iowa", [20.0]),
            _mock_sku("Filestore Instance IOPS Regional Iowa", [0.027]),
            _mock_sku("Filestore Instance IOPS Zonal Iowa", [0.0145]),
            _mock_sku("Filestore Backup Usage Iowa", [0.08]),
        ]

        mock_client = MagicMock()
        mock_client.list_skus.side_effect = lambda parent: (
            compute_skus if parent == pricing.COMPUTE_ENGINE_SERVICE else filestore_skus
        )
        fake_billing = SimpleNamespace(CloudCatalogClient=lambda: mock_client)
        monkeypatch.setitem(sys.modules, "google.cloud.billing_v1", fake_billing)
        import google.cloud

        monkeypatch.setattr(google.cloud, "billing_v1", fake_billing, raising=False)

        got = pricing.fetch_live_prices("us-central1")
        assert got is not None
        assert got["filestore_basic_ssd_gib_mo"] == pytest.approx(0.30)
        assert got["filestore_basic_hdd_gib_mo"] == pytest.approx(0.16)
        assert got["filestore_regional_gib_mo"] == pytest.approx(0.45)
        assert got["filestore_regional_custom_gib_mo"] == pytest.approx(0.21)
        assert got["filestore_zonal_gib_mo"] == pytest.approx(0.25)
        assert got["filestore_zonal_high_scale_gib_mo"] == pytest.approx(0.225)
        assert got["filestore_zonal_custom_gib_mo"] == pytest.approx(0.12)
        assert got["filestore_regional_custom_instance_mo"] == pytest.approx(40.0)
        assert got["filestore_zonal_custom_instance_mo"] == pytest.approx(20.0)
        assert got["filestore_regional_custom_iops_mo"] == pytest.approx(0.027)
        assert got["filestore_zonal_custom_iops_mo"] == pytest.approx(0.0145)
