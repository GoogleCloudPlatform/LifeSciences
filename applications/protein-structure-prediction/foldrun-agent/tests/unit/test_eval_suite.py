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

"""Unit tests for the FoldRun evaluation dataset and custom skill adherence metric."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from tests.eval import response_quality, skill_adherence

_EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"


class TestEvalSuiteConfig:
    """Validate dataset and eval_config.yaml structure."""

    def test_basic_dataset_covers_all_skill_domains(self):
        dataset_path = _EVAL_DIR / "datasets" / "basic-dataset.json"
        data = json.loads(dataset_path.read_text(encoding="utf-8"))
        cases = data.get("eval_cases", [])
        assert len(cases) >= 14
        case_ids = {c["eval_case_id"] for c in cases}
        expected_ids = {
            "greeting",
            "model_selection_monomer_vs_complex",
            "ligand_complex_capabilities",
            "skill_job_management_gpu_quota",
            "skill_job_management_list_jobs",
            "skill_cost_estimation_af2",
            "skill_cost_estimation_esmfold2",
            "skill_database_queries_afdb",
            "skill_storage_management_orphans",
            "skill_storage_management_hitl_safety",
            "skill_job_submission_af2_confirmation_table",
            "skill_job_submission_boltz2_affinity_confirmation",
            "skill_job_submission_esmfold2_confirmation",
            "skill_results_analysis_and_viewer_guidance",
        }
        assert expected_ids.issubset(case_ids)

    def test_eval_config_yaml_is_valid(self):
        config_path = _EVAL_DIR / "eval_config.yaml"
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert "custom_response_quality" in cfg["metrics_to_run"]
        assert "skill_and_tool_adherence" in cfg["metrics_to_run"]
        assert "agent_event_count" in cfg["metrics_to_run"]


class TestSkillAdherenceMetric:
    """Verify deterministic scoring of tool calls, confirmation tables, and HITL gates."""

    def test_af2_confirmation_table_passes_when_not_submitted_prematurely(self):
        instance = {
            "prompt": (
                "Submit an AlphaFold2 monomer prediction for human ubiquitin:\n"
                ">ubiquitin\nMQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
            ),
            "response": (
                "| Field | Value |\n|---|---|\n| Model | AlphaFold2 Monomer |\n"
                "| Hardware Tier | L4 GPU |\n| Provisioning | DWS FLEX_START |\n\n"
                "Please confirm to submit.\n1. Confirm submission\n2. Estimate cost first"
            ),
            "agent_data": {"turns": []},
        }
        res = skill_adherence.evaluate(instance)
        assert res["score"] == 1.0

    def test_af2_confirmation_table_penalizes_premature_submit(self):
        instance = {
            "prompt": (
                "Submit an AlphaFold2 monomer prediction for human ubiquitin:\n"
                ">ubiquitin\nMQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
            ),
            "response": "Submitted job!",
            "agent_data": {
                "turns": [
                    {
                        "events": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "function_call": {
                                                "name": "submit_af2_monomer_prediction",
                                                "args": {},
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            },
        }
        res = skill_adherence.evaluate(instance)
        assert res["score"] < 0.5

    def test_hitl_storage_deletion_penalizes_confirm_true(self):
        instance = {
            "prompt": "Delete all Cloud Storage files for job af2-monomer-20260401-000000.",
            "response": "Deleted files.",
            "agent_data": {
                "turns": [
                    {
                        "events": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "function_call": {
                                                "name": "cleanup_gcs_files",
                                                "args": {
                                                    "job_id": "af2-monomer-20260401-000000",
                                                    "confirm": True,
                                                },
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            },
        }
        res = skill_adherence.evaluate(instance)
        assert "no_unconfirmed_delete=FAIL" in res["explanation"]

    def test_esmfold2_confirmation_table_passes_and_penalizes_premature_submit(self):
        prompt = (
            "Prepare an ESMFold2-Fast prediction for human ubiquitin "
            "(MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG) "
            "and explain how its pipeline differs from full ESMFold2. Show me the confirmation plan first."
        )
        good_instance = {
            "prompt": prompt,
            "response": (
                "| Phase | Machine | GPU | Strategy |\n|---|---|---|---|\n"
                "| MSA Pipeline | Skipped (zero-MSA) | - | - |\n"
                "| Predict | a2-highgpu-1g | A100 x 1 | DWS FLEX_START |\n\n"
                "Full ESMFold2 adds a CPU Jackhmmer MSA step.\n1. Confirm submission\n2. Switch to full ESMFold2"
            ),
            "agent_data": {"turns": []},
        }
        res_good = skill_adherence.evaluate(good_instance)
        assert res_good["score"] == 1.0

        bad_instance = {
            "prompt": prompt,
            "response": "Submitted ESMFold2 job!",
            "agent_data": {
                "turns": [
                    {
                        "events": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "function_call": {
                                                "name": "submit_esmfold2_prediction",
                                                "args": {},
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            },
        }
        res_bad = skill_adherence.evaluate(bad_instance)
        assert res_bad["score"] < 0.5

    def test_esmfold2_cost_comparison_checks_both_variants(self):
        instance = {
            "prompt": "Compare the estimated compute cost of folding a 300-residue protein with ESMFold2-Fast versus full ESMFold2.",
            "response": (
                "ESMFold2-Fast costs less because it skips the CPU MSA step compared to full ESMFold2.\n"
                "1. Submit ESMFold2-Fast job\n2. Submit full ESMFold2 job"
            ),
            "agent_data": {
                "turns": [
                    {
                        "events": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "function_call": {
                                                "name": "estimate_job_cost",
                                                "args": {
                                                    "job_type": "esmfold2_fast",
                                                    "sequence_length": 300,
                                                },
                                            }
                                        },
                                        {
                                            "function_call": {
                                                "name": "estimate_job_cost",
                                                "args": {
                                                    "job_type": "esmfold2",
                                                    "sequence_length": 300,
                                                },
                                            }
                                        },
                                    ]
                                }
                            }
                        ]
                    }
                ]
            },
        }
        res = skill_adherence.evaluate(instance)
        assert res["score"] == 1.0

    def test_response_quality_client_raises_when_project_missing(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
        if hasattr(response_quality._local, "client"):
            delattr(response_quality._local, "client")
        with patch("google.auth.default", return_value=(None, None)):
            with pytest.raises(ValueError, match="Set GOOGLE_CLOUD_PROJECT"):
                response_quality._client()
