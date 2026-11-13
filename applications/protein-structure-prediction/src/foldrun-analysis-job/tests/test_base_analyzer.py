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

"""Tests for BaseStructureAnalyzer, OF3StructureAnalyzer, and Boltz2StructureAnalyzer."""

from __future__ import annotations

import io
import json
import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

_stubs = {
    "google.cloud.storage": MagicMock(),
    "google.cloud.aiplatform_v1": MagicMock(),
    "google.genai": MagicMock(),
    "google.genai.types": MagicMock(),
}
for _mod_name, _stub in _stubs.items():
    sys.modules.setdefault(_mod_name, _stub)

from foldrun_analysis import boltz2_analyzer, of3_analyzer, shared_utils  # noqa: E402
from foldrun_analysis.boltz2_analyzer import Boltz2StructureAnalyzer  # noqa: E402
from foldrun_analysis.of3_analyzer import OF3StructureAnalyzer  # noqa: E402

TWO_CHAIN_BOLTZ_CIF = """\
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
ATOM 1 N N MET A 1 1.0 2.0 3.0 1.0 88.0 1 MET A N
ATOM 2 CA C MET A 1 2.0 3.0 4.0 1.0 92.0 1 MET A CA
HETATM 3 C1 C LIG B 1 5.0 6.0 7.0 1.0 76.0 1 LIG B C1
"""

SINGLE_CHAIN_OF3_CIF = """\
ATOM 1 N N MET A 1 1.0 2.0 1 MET A
ATOM 2 CA C MET A 1 2.0 3.0 1 MET A
ATOM 3 N N GLY A 2 3.0 4.0 2 GLY A
"""


def _touch_plot_file(*args: Any, **kwargs: Any) -> None:
    for arg in args:
        if isinstance(arg, str) and arg.endswith(".png"):
            with open(arg, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")


class TestOF3StructureAnalyzer:
    """Tests for OpenFold3 sample extraction and consolidation via BaseStructureAnalyzer."""

    def test_analyze_sample_monomer_uses_ptm_as_ranking_score(self):
        analyzer = OF3StructureAnalyzer()
        pred = {
            "sample_name": "seed_1_sample_0",
            "cif_uri": "gs://bucket/of3/seed_1_sample_0_model.cif",
            "aggregated_uri": "gs://bucket/of3/seed_1_sample_0_confidences_aggregated.json",
            "confidences_uri": "gs://bucket/of3/seed_1_sample_0_confidences.json",
            "output_uri": "gs://bucket/of3/analysis/prediction_0_analysis.json",
        }

        def _fake_json(uri: str) -> dict[str, Any]:
            if uri.endswith("_aggregated.json"):
                return {
                    "ptm": 0.87,
                    "iptm": 0.0,
                    "sample_ranking_score": 0.17,
                    "avg_plddt": 86.5,
                    "gpde": 1.12,
                    "has_clash": 0,
                    "disorder": 0,
                    "chain_ptm": {"A": 0.87},
                }
            return {
                "plddt": [85.0, 90.0, 88.0],
                "pde": [[0.0, 1.0, 1.2], [1.0, 0.0, 0.8], [1.2, 0.8, 0.0]],
            }

        uploaded_uris: list[str] = []

        with (
            patch.object(
                shared_utils, "download_json_from_gcs", side_effect=_fake_json
            ),
            patch.object(
                shared_utils,
                "download_text_from_gcs",
                return_value=SINGLE_CHAIN_OF3_CIF,
            ),
            patch.object(
                shared_utils, "plot_plddt_distribution", side_effect=_touch_plot_file
            ),
            patch.object(
                shared_utils, "plot_error_matrix", side_effect=_touch_plot_file
            ),
            patch.object(
                shared_utils, "plot_iptm_matrix", side_effect=_touch_plot_file
            ),
            patch.object(
                shared_utils,
                "upload_to_gcs",
                side_effect=lambda local, uri: uploaded_uris.append(uri),
            ),
        ):
            result = analyzer.analyze_sample(0, pred)

        assert result["ranking_score"] == pytest.approx(0.87)
        assert result["ptm"] == pytest.approx(0.87)
        assert result["plddt_mean"] == pytest.approx((85.0 + 90.0 + 88.0) / 3)
        assert result["has_pde"] is True
        assert "plddt_plot" in result["plots"]
        assert "pde_plot" in result["plots"]
        assert len(uploaded_uris) == 2

    def test_analyze_sample_cleans_up_temp_plots_on_upload_error_and_noop_render(self):
        analyzer = OF3StructureAnalyzer()
        pred = {
            "sample_name": "seed_1_sample_0",
            "cif_uri": "gs://bucket/of3/seed_1_sample_0_model.cif",
            "aggregated_uri": "gs://bucket/of3/seed_1_sample_0_confidences_aggregated.json",
            "confidences_uri": "gs://bucket/of3/seed_1_sample_0_confidences.json",
            "output_uri": "gs://bucket/of3/analysis/prediction_0_analysis.json",
        }

        def _fake_json(uri: str) -> dict[str, Any]:
            if uri.endswith("_aggregated.json"):
                return {"ptm": 0.85, "avg_plddt": 85.0, "chain_ptm": {"A": 0.85}}
            return {
                "plddt": [85.0, 90.0, 88.0],
                "pde": [[0.0, 1.0, 1.2], [1.0, 0.0, 0.8], [1.2, 0.8, 0.0]],
            }

        seen_temp_paths: list[str] = []

        def _noop_plot(*args: Any, **kwargs: Any) -> None:
            for arg in args:
                if isinstance(arg, str) and arg.endswith(".png"):
                    seen_temp_paths.append(arg)

        # Case 1: early-return no-op render leaves 0-byte temp file -> skipped upload & deleted
        with (
            patch.object(
                shared_utils, "download_json_from_gcs", side_effect=_fake_json
            ),
            patch.object(
                shared_utils,
                "download_text_from_gcs",
                return_value=SINGLE_CHAIN_OF3_CIF,
            ),
            patch.object(
                shared_utils, "plot_plddt_distribution", side_effect=_noop_plot
            ),
            patch.object(
                shared_utils, "plot_error_matrix", side_effect=_touch_plot_file
            ),
            patch.object(shared_utils, "upload_to_gcs") as mock_upload,
        ):
            result = analyzer.analyze_sample(0, pred)

        assert "plddt_plot" not in result["plots"]
        assert "pde_plot" in result["plots"]
        mock_upload.assert_called_once()
        assert len(seen_temp_paths) == 1
        assert not seen_temp_paths[0].startswith("/tmp/plddt_plot_")
        assert not os.path.exists(seen_temp_paths[0])

        # Case 2: upload_to_gcs raises -> temp file still cleaned up in finally
        failed_temp_paths: list[str] = []

        def _record_and_touch(*args: Any, **kwargs: Any) -> None:
            for arg in args:
                if isinstance(arg, str) and arg.endswith(".png"):
                    failed_temp_paths.append(arg)
            _touch_plot_file(*args, **kwargs)

        with (
            patch.object(
                shared_utils, "download_json_from_gcs", side_effect=_fake_json
            ),
            patch.object(
                shared_utils,
                "download_text_from_gcs",
                return_value=SINGLE_CHAIN_OF3_CIF,
            ),
            patch.object(
                shared_utils, "plot_plddt_distribution", side_effect=_record_and_touch
            ),
            patch.object(
                shared_utils,
                "upload_to_gcs",
                side_effect=RuntimeError("GCS upload failed"),
            ),
            pytest.raises(RuntimeError, match="GCS upload failed"),
        ):
            analyzer.analyze_sample(0, pred)

        assert len(failed_temp_paths) == 1
        assert not os.path.exists(failed_temp_paths[0])


class TestBoltz2StructureAnalyzer:
    """Tests for Boltz-2 chain remapping, PDE loading, and affinity consolidation."""

    def test_analyze_sample_remaps_numeric_chains_and_loads_npz_pde(self):
        analyzer = Boltz2StructureAnalyzer()
        pred = {
            "sample_name": "seed_42_sample_0",
            "cif_uri": "gs://bucket/b2/query_model_0.cif",
            "aggregated_uri": "gs://bucket/b2/confidence_query_model_0.json",
            "confidences_uri": "gs://bucket/b2/confidence_query_model_0.json",
            "pde_uri": "gs://bucket/b2/pde_query_model_0.npz",
            "output_uri": "gs://bucket/b2/analysis/prediction_0_analysis.json",
        }

        npz_buf = io.BytesIO()
        np.savez(npz_buf, pde=np.array([[0.0, 1.5], [1.5, 0.0]], dtype=np.float32))

        aggregated_payload = {
            "confidence_score": 0.84,
            "ptm": 0.89,
            "iptm": 0.78,
            "complex_plddt": 85.3,
            "complex_pde": 0.95,
            "has_clash": 0,
            "chains_ptm": {"0": 0.91, "1": 0.76},
            "pair_chains_iptm": {
                "0": {"0": 0.91, "1": 0.78},
                "1": {"0": 0.77, "1": 0.76},
            },
        }

        with (
            patch.object(
                shared_utils, "download_json_from_gcs", return_value=aggregated_payload
            ),
            patch.object(
                shared_utils, "download_text_from_gcs", return_value=TWO_CHAIN_BOLTZ_CIF
            ),
            patch.object(
                shared_utils, "download_image_from_gcs", return_value=npz_buf.getvalue()
            ),
            patch.object(
                shared_utils, "plot_plddt_distribution", side_effect=_touch_plot_file
            ),
            patch.object(
                shared_utils, "plot_error_matrix", side_effect=_touch_plot_file
            ),
            patch.object(
                shared_utils, "plot_iptm_matrix", side_effect=_touch_plot_file
            ),
            patch.object(shared_utils, "upload_to_gcs"),
        ):
            result = analyzer.analyze_sample(0, pred)

        assert result["ranking_score"] == pytest.approx(0.84)
        assert result["chain_ptm"] == {"A": 0.91, "B": 0.76}
        assert result["chain_pair_iptm"] == {"(A, B)": 0.78, "(B, A)": 0.77}
        assert result["per_chain_plddt"]["A"]["mean"] == pytest.approx(90.0)
        assert result["per_chain_plddt"]["B"]["mean"] == pytest.approx(76.0)
        assert result["has_pde"] is True
        assert set(result["plots"].keys()) == {
            "plddt_plot",
            "pde_plot",
            "iptm_matrix_plot",
        }

    def test_consolidate_results_ranks_samples_and_attaches_affinity(self):
        analyzer = Boltz2StructureAnalyzer()
        analyzer.consolidation_settle_seconds = 0.0

        blob_0 = MagicMock()
        blob_0.name = "b2/analysis/prediction_0_analysis.json"
        blob_1 = MagicMock()
        blob_1.name = "b2/analysis/prediction_1_analysis.json"

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = [blob_0, blob_1]
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        sample_0 = {
            "prediction_index": 0,
            "sample_name": "seed_1_sample_0",
            "ranking_score": 0.72,
            "ptm": 0.80,
            "iptm": 0.68,
            "plddt_mean": 80.0,
            "plddt_min": 65.0,
            "quality_assessment": "high",
            "has_clash": 0,
            "chain_info": [
                {
                    "chain_id": "A",
                    "molecule_type": "protein",
                    "residue_count": 50,
                    "atom_count": 400,
                }
            ],
            "cif_uri": "gs://bucket/b2/s0.cif",
            "plots": {},
        }
        sample_1 = {
            **sample_0,
            "prediction_index": 1,
            "sample_name": "seed_2_sample_0",
            "ranking_score": 0.91,
            "ptm": 0.93,
            "iptm": 0.88,
            "plddt_mean": 91.5,
            "cif_uri": "gs://bucket/b2/s1.cif",
        }

        def _fake_json(uri: str) -> dict[str, Any]:
            if uri.endswith("prediction_0_analysis.json"):
                return dict(sample_0)
            if uri.endswith("prediction_1_analysis.json"):
                return dict(sample_1)
            if uri.endswith("affinity_query.json"):
                return {
                    "affinity_pred_value": -2.0,
                    "affinity_probability_binary": 0.92,
                }
            raise AssertionError(f"Unexpected JSON URI: {uri}")

        uploaded_payloads: dict[str, Any] = {}

        def _capture_upload(local_path: str, gcs_uri: str) -> None:
            with open(local_path, encoding="utf-8") as fh:
                uploaded_payloads[gcs_uri] = json.load(fh)

        with (
            patch.object(shared_utils.storage, "Client", return_value=mock_client),
            patch.object(
                shared_utils, "download_json_from_gcs", side_effect=_fake_json
            ),
            patch.object(
                shared_utils,
                "download_text_from_gcs",
                return_value="version: 1\nsequences:\n  - protein:\n      id: A\n      sequence: MKTAYIAK\n",
            ),
            patch.object(
                shared_utils,
                "get_job_metadata",
                return_value={"labels": {}, "parameters": {}},
            ),
            patch.object(
                analyzer,
                "generate_gemini_expert_analysis",
                return_value={
                    "status": "success",
                    "analysis": "High confidence complex.",
                },
            ),
            patch.object(
                shared_utils,
                "build_and_upload_run_archive",
                return_value={
                    "artifacts_bundle_uri": "gs://bucket/b2/analysis/artifacts_bundle.zip"
                },
            ),
            patch.object(shared_utils, "upload_to_gcs", side_effect=_capture_upload),
        ):
            summary_data = analyzer.consolidate_results(
                job_id="boltz2-job-123",
                analysis_path="gs://bucket/b2/analysis/",
                bucket_name="bucket",
                task_count=2,
                affinity_uri="gs://bucket/b2/affinity_query.json",
                query_yaml_uri="gs://bucket/boltz2_queries/query.yaml",
            )

        assert summary_data is not None
        assert summary_data["best_prediction"]["sample_name"] == "seed_2_sample_0"
        assert summary_data["best_prediction"]["rank"] == 1
        assert summary_data["summary"]["affinity"]["binding_classification"] == "strong"
        assert summary_data["affinity"]["ic50_nm"] == pytest.approx(10.0)
        assert summary_data["summary"]["protein_info"]["fasta_sequence"] == "MKTAYIAK"
        assert "gs://bucket/b2/analysis/summary.json" in uploaded_payloads


class TestRunTaskStriding:
    """Tests for Cloud Run Job striding and consolidator election."""

    def test_run_task_strides_across_workers_and_elects_last_active_worker(self):
        task_config = {
            "job_id": "of3-job-99",
            "model_type": "openfold3",
            "analysis_path": "gs://bucket/of3/analysis/",
            "predictions": [
                {
                    "index": i,
                    "sample_name": f"sample_{i}",
                    "cif_uri": f"gs://bucket/of3/s{i}.cif",
                    "aggregated_uri": f"gs://bucket/of3/s{i}_agg.json",
                    "confidences_uri": f"gs://bucket/of3/s{i}_conf.json",
                    "output_uri": f"gs://bucket/of3/analysis/prediction_{i}_analysis.json",
                }
                for i in range(3)
            ],
        }

        analyzed_by_worker_0: list[int] = []
        analyzed_by_worker_1: list[int] = []

        def _fake_analyze(idx: int, pred: dict[str, Any]) -> dict[str, Any]:
            return {
                "prediction_index": idx,
                "sample_name": pred["sample_name"],
                "ranking_score": 0.8,
                "plddt_mean": 85.0,
            }

        with (
            patch.object(
                of3_analyzer.ANALYZER,
                "analyze_sample",
                side_effect=lambda idx, pred: (
                    analyzed_by_worker_0.append(idx) or _fake_analyze(idx, pred)
                ),
            ),
            patch.object(shared_utils, "upload_to_gcs"),
            patch.object(shared_utils, "wait_for_sibling_tasks") as mock_wait,
            patch.object(
                of3_analyzer.ANALYZER, "consolidate_results"
            ) as mock_consolidate,
        ):
            of3_analyzer.run_task(
                0, 2, task_config, "bucket", "gs://bucket/of3/analysis/"
            )
            assert analyzed_by_worker_0 == [0, 2]
            mock_wait.assert_not_called()
            mock_consolidate.assert_not_called()

        with (
            patch.object(
                of3_analyzer.ANALYZER,
                "analyze_sample",
                side_effect=lambda idx, pred: (
                    analyzed_by_worker_1.append(idx) or _fake_analyze(idx, pred)
                ),
            ),
            patch.object(shared_utils, "upload_to_gcs"),
            patch.object(shared_utils, "wait_for_sibling_tasks") as mock_wait,
            patch.object(
                of3_analyzer.ANALYZER, "consolidate_results"
            ) as mock_consolidate,
        ):
            of3_analyzer.run_task(
                1, 2, task_config, "bucket", "gs://bucket/of3/analysis/"
            )
            assert analyzed_by_worker_1 == [1]
            mock_wait.assert_called_once_with("bucket", "gs://bucket/of3/analysis/", 3)
            mock_consolidate.assert_called_once()

    def test_run_task_exits_when_task_index_exceeds_predictions(self):
        task_config = {
            "job_id": "boltz2-empty",
            "model_type": "boltz2",
            "predictions": [
                {
                    "index": 0,
                    "sample_name": "s0",
                    "cif_uri": "gs://b/s0.cif",
                    "aggregated_uri": "gs://b/s0.json",
                    "output_uri": "gs://b/out.json",
                }
            ],
        }
        with pytest.raises(SystemExit) as exc_info:
            boltz2_analyzer.run_task(2, 4, task_config, "b", "gs://b/analysis/")
        assert exc_info.value.code == 1
