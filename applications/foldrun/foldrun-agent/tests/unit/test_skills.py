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

"""Tests for skill wrapper functions.

Each test verifies that the wrapper calls get_tool() with the correct
registry name and passes the right arguments to tool.run().

IMPORTANT: Patches target get_tool at each skill module's use site
(e.g. foldrun_app.skills.job_submission.tools.get_tool) rather than the
registry definition, because test_agent.py imports all modules first
and binds the local name before these patches run.
"""

from unittest.mock import MagicMock, patch


def _mock_tool(return_value=None):
    """Create a mock tool with a .run() method."""
    mock = MagicMock()
    mock.run.return_value = return_value or {"status": "success"}
    return mock


def _patch_get_tool(mock_tool, module_path):
    """Patch get_tool at the use site in a specific skill module.

    Args:
        mock_tool: The mock to return from get_tool.
        module_path: Dotted path to the skill's tools module,
                     e.g. "foldrun_app.skills.job_submission.tools".
    """
    return patch(f"{module_path}.get_tool", return_value=mock_tool)


# Module paths for each skill package
_JOB_SUBMISSION = "foldrun_app.skills.job_submission.tools"
_JOB_MANAGEMENT = "foldrun_app.skills.job_management.tools"
_RESULTS_ANALYSIS = "foldrun_app.skills.results_analysis.tools"
_DATABASE_QUERIES = "foldrun_app.skills.database_queries.tools"
_STORAGE_MANAGEMENT = "foldrun_app.skills.storage_management.tools"
_VISUALIZATION = "foldrun_app.skills.visualization.tools"
_GENETIC_DATABASES = "foldrun_app.skills.genetic_databases.tools"
_INFRASTRUCTURE = "foldrun_app.skills.infrastructure.tools"


class TestJobSubmissionSkills:
    """Tests for job_submission skill wrappers."""

    def test_submit_monomer_calls_correct_tool(self):
        """submit_af2_monomer_prediction calls af2_submit_monomer tool."""
        mock = _mock_tool({"status": "submitted", "job_id": "test-123"})

        with _patch_get_tool(mock, _JOB_SUBMISSION) as mock_get:
            from foldrun_app.skills.job_submission import submit_af2_monomer_prediction

            result = submit_af2_monomer_prediction(
                sequence=">test\nMKTIALSYIF",
                gpu_type="L4",
            )

            mock_get.assert_called_with("af2_submit_monomer")
            mock.run.assert_called_once()
            args = mock.run.call_args[0][0]
            assert args["sequence"] == ">test\nMKTIALSYIF"
            assert args["gpu_type"] == "L4"
            assert result["status"] == "submitted"

    def test_submit_monomer_with_mmseqs2(self):
        """submit_af2_monomer_prediction passes msa_method='mmseqs2'."""
        mock = _mock_tool({"status": "submitted", "job_id": "mmseqs2-123"})

        with _patch_get_tool(mock, _JOB_SUBMISSION):
            from foldrun_app.skills.job_submission import submit_af2_monomer_prediction

            result = submit_af2_monomer_prediction(
                sequence=">test\nMKTIALSYIF",
                gpu_type="L4",
                msa_method="mmseqs2",
                use_small_bfd=True,
            )

            args = mock.run.call_args[0][0]
            assert args["msa_method"] == "mmseqs2"
            assert args["use_small_bfd"] is True

    def test_submit_monomer_default_msa_method(self):
        """submit_af2_monomer_prediction defaults msa_method to 'auto'."""
        mock = _mock_tool({"status": "submitted"})

        with _patch_get_tool(mock, _JOB_SUBMISSION):
            from foldrun_app.skills.job_submission import submit_af2_monomer_prediction

            submit_af2_monomer_prediction(sequence=">test\nMKTIALSYIF")

            args = mock.run.call_args[0][0]
            assert args["msa_method"] == "auto"

    def test_submit_monomer_default_gpu_type(self):
        """submit_af2_monomer_prediction defaults gpu_type to 'auto'."""
        mock = _mock_tool({"status": "submitted"})

        with _patch_get_tool(mock, _JOB_SUBMISSION):
            from foldrun_app.skills.job_submission import submit_af2_monomer_prediction

            submit_af2_monomer_prediction(sequence=">test\nMKTIALSYIF")

            args = mock.run.call_args[0][0]
            assert args["gpu_type"] == "auto"

    def test_submit_multimer_default_gpu_type(self):
        """submit_af2_multimer_prediction defaults gpu_type to 'auto'."""
        mock = _mock_tool({"status": "submitted"})

        with _patch_get_tool(mock, _JOB_SUBMISSION):
            from foldrun_app.skills.job_submission import submit_af2_multimer_prediction

            submit_af2_multimer_prediction(
                sequence=">A\nMKTIALSYIF\n>B\nACDEFGHIKL",
            )

            args = mock.run.call_args[0][0]
            assert args["gpu_type"] == "auto"

    def test_submit_multimer_calls_correct_tool(self):
        """submit_af2_multimer_prediction calls af2_submit_multimer tool."""
        mock = _mock_tool({"status": "submitted", "job_id": "multi-456"})

        with _patch_get_tool(mock, _JOB_SUBMISSION) as mock_get:
            from foldrun_app.skills.job_submission import submit_af2_multimer_prediction

            result = submit_af2_multimer_prediction(
                sequence=">A\nMKTIALSYIF\n>B\nACDEFGHIKL",
                gpu_type="A100",
                num_predictions_per_model=5,
            )

            mock_get.assert_called_with("af2_submit_multimer")
            args = mock.run.call_args[0][0]
            assert args["sequence"] == ">A\nMKTIALSYIF\n>B\nACDEFGHIKL"
            assert args["gpu_type"] == "A100"
            assert args["num_predictions_per_model"] == 5

    def test_submit_multimer_with_mmseqs2(self):
        """submit_af2_multimer_prediction passes msa_method='mmseqs2'."""
        mock = _mock_tool({"status": "submitted"})

        with _patch_get_tool(mock, _JOB_SUBMISSION):
            from foldrun_app.skills.job_submission import submit_af2_multimer_prediction

            submit_af2_multimer_prediction(
                sequence=">A\nMKTIALSYIF\n>B\nACDEFGHIKL",
                msa_method="mmseqs2",
            )

            args = mock.run.call_args[0][0]
            assert args["msa_method"] == "mmseqs2"

    def test_submit_batch_calls_correct_tool(self):
        """submit_af2_batch_predictions calls af2_submit_batch tool."""
        mock = _mock_tool({"status": "submitted", "count": 3})
        batch = [{"sequence": ">a\nMKT"}, {"sequence": ">b\nACD"}]

        with _patch_get_tool(mock, _JOB_SUBMISSION) as mock_get:
            from foldrun_app.skills.job_submission import submit_af2_batch_predictions

            submit_af2_batch_predictions(batch_config=batch)

            mock_get.assert_called_with("af2_submit_batch")
            args = mock.run.call_args[0][0]
            assert args["batch_config"] == batch


class TestJobManagementSkills:
    """Tests for job_management skill wrappers."""

    def test_list_jobs_calls_correct_tool(self):
        """list_jobs calls af2_list_jobs tool."""
        mock = _mock_tool({"jobs": [], "total": 0})

        with _patch_get_tool(mock, _JOB_MANAGEMENT) as mock_get:
            from foldrun_app.skills.job_management import list_jobs

            result = list_jobs(state="running")

            mock_get.assert_called_with("af2_list_jobs")
            args = mock.run.call_args[0][0]
            assert args["state"] == "running"

    def test_check_job_status_calls_correct_tool(self):
        """check_job_status calls af2_check_job_status tool."""
        mock = _mock_tool({"state": "RUNNING", "progress": 50})

        with _patch_get_tool(mock, _JOB_MANAGEMENT) as mock_get:
            from foldrun_app.skills.job_management import check_job_status

            check_job_status(job_id="pipeline-123")

            mock_get.assert_called_with("af2_check_job_status")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"

    def test_get_job_details_calls_correct_tool(self):
        """get_job_details calls af2_get_job_details tool."""
        mock = _mock_tool({"job_id": "pipeline-123", "sequence": ">test\nMKT"})

        with _patch_get_tool(mock, _JOB_MANAGEMENT) as mock_get:
            from foldrun_app.skills.job_management import get_job_details

            get_job_details(job_id="pipeline-123")

            mock_get.assert_called_with("af2_get_job_details")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"

    def test_delete_job_calls_correct_tool(self):
        """delete_job calls af2_delete_job tool with confirm flag."""
        mock = _mock_tool({"status": "deleted"})

        with _patch_get_tool(mock, _JOB_MANAGEMENT) as mock_get:
            from foldrun_app.skills.job_management import delete_job

            delete_job(job_id="pipeline-123", confirm=True)

            mock_get.assert_called_with("af2_delete_job")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"
            assert args["confirm"] is True

    def test_check_gpu_quota_calls_correct_tool(self):
        """check_gpu_quota calls af2_check_gpu_quota tool."""
        mock = _mock_tool({"gpus": {"L4": {"available": 8}}})

        with _patch_get_tool(mock, _JOB_MANAGEMENT) as mock_get:
            from foldrun_app.skills.job_management import check_gpu_quota

            check_gpu_quota()

            mock_get.assert_called_with("af2_check_gpu_quota")

    def test_check_gpu_quota_with_region(self):
        """check_gpu_quota passes region argument when provided."""
        mock = _mock_tool({"gpus": {}})

        with _patch_get_tool(mock, _JOB_MANAGEMENT) as mock_get:
            from foldrun_app.skills.job_management import check_gpu_quota

            check_gpu_quota(region="us-east1")

            args = mock.run.call_args[0][0]
            assert args["region"] == "us-east1"


class TestResultsAnalysisSkills:
    """Tests for results_analysis skill wrappers."""

    def test_get_prediction_results_calls_correct_tool(self):
        """get_prediction_results calls af2_get_results tool."""
        mock = _mock_tool({"pdb_files": ["ranked_0.pdb"]})

        with _patch_get_tool(mock, _RESULTS_ANALYSIS) as mock_get:
            from foldrun_app.skills.results_analysis import get_prediction_results

            get_prediction_results(job_id="pipeline-123")

            mock_get.assert_called_with("af2_get_results")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"

    def test_analyze_prediction_quality_calls_correct_tool(self):
        """analyze_prediction_quality calls af2_analyze_quality tool."""
        mock = _mock_tool({"plddt_mean": 85.2})

        with _patch_get_tool(mock, _RESULTS_ANALYSIS) as mock_get:
            from foldrun_app.skills.results_analysis import analyze_prediction_quality

            analyze_prediction_quality(job_id="pipeline-123", analyze_all=True)

            mock_get.assert_called_with("af2_analyze_quality")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"
            assert args["analyze_all"] is True

    def test_analyze_job_parallel_calls_correct_tool(self):
        """analyze_job_parallel calls af2_analyze_parallel tool."""
        mock = _mock_tool({"execution_id": "exec-123"})

        with _patch_get_tool(mock, _RESULTS_ANALYSIS) as mock_get:
            from foldrun_app.skills.results_analysis import analyze_job_parallel

            analyze_job_parallel(job_id="pipeline-123", top_n=5)

            mock_get.assert_called_with("af2_analyze_parallel")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"
            assert args["top_n"] == 5

    def test_get_analysis_results_calls_correct_tool(self):
        """get_analysis_results calls af2_get_analysis_results tool."""
        mock = _mock_tool({"status": "complete", "predictions": []})

        with _patch_get_tool(mock, _RESULTS_ANALYSIS) as mock_get:
            from foldrun_app.skills.results_analysis import get_analysis_results

            get_analysis_results(job_id="pipeline-123", wait=True)

            mock_get.assert_called_with("af2_get_analysis_results")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"
            assert args["wait"] is True

    def test_analyze_job_calls_correct_tool(self):
        """analyze_job calls af2_analyze_job_deep tool."""
        mock = _mock_tool({"state": "FAILED", "error": "GPU timeout"})

        with _patch_get_tool(mock, _RESULTS_ANALYSIS) as mock_get:
            from foldrun_app.skills.results_analysis import analyze_job

            analyze_job(job_id="pipeline-123", detail_level="detailed")

            mock_get.assert_called_with("af2_analyze_job_deep")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"
            assert args["detail_level"] == "detailed"


class TestDatabaseQuerySkills:
    """Tests for database_queries skill wrappers."""

    def test_query_prediction_calls_correct_tool(self):
        """query_alphafold_db_prediction calls alphafold_db_get_prediction tool."""
        mock = _mock_tool({"uniprot_id": "P69905"})

        with _patch_get_tool(mock, _DATABASE_QUERIES) as mock_get:
            from foldrun_app.skills.database_queries import query_alphafold_db_prediction

            result = query_alphafold_db_prediction(qualifier="P69905")

            mock_get.assert_called_with("alphafold_db_get_prediction")
            args = mock.run.call_args[0][0]
            assert args["qualifier"] == "P69905"

    def test_query_summary_calls_correct_tool(self):
        """query_alphafold_db_summary calls alphafold_db_get_summary tool."""
        mock = _mock_tool({"uniprot_id": "P69905", "sequence_length": 142})

        with _patch_get_tool(mock, _DATABASE_QUERIES) as mock_get:
            from foldrun_app.skills.database_queries import query_alphafold_db_summary

            query_alphafold_db_summary(qualifier="P69905")

            mock_get.assert_called_with("alphafold_db_get_summary")
            args = mock.run.call_args[0][0]
            assert args["qualifier"] == "P69905"

    def test_query_annotations_calls_correct_tool(self):
        """query_alphafold_db_annotations calls alphafold_db_get_annotations tool."""
        mock = _mock_tool({"annotations": []})

        with _patch_get_tool(mock, _DATABASE_QUERIES) as mock_get:
            from foldrun_app.skills.database_queries import query_alphafold_db_annotations

            query_alphafold_db_annotations(qualifier="P69905", type="MUTAGEN")

            mock_get.assert_called_with("alphafold_db_get_annotations")
            args = mock.run.call_args[0][0]
            assert args["qualifier"] == "P69905"
            assert args["type"] == "MUTAGEN"


class TestStorageManagementSkills:
    """Tests for storage_management skill wrappers."""

    def test_cleanup_gcs_files_calls_correct_tool(self):
        """cleanup_gcs_files calls af2_cleanup_gcs_files tool."""
        mock = _mock_tool({"files_found": 5, "total_size_mb": 34.5})

        with _patch_get_tool(mock, _STORAGE_MANAGEMENT) as mock_get:
            from foldrun_app.skills.storage_management import cleanup_gcs_files

            cleanup_gcs_files(job_id="pipeline-123", search_only=True)

            mock_get.assert_called_with("af2_cleanup_gcs_files")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"
            assert args["search_only"] is True

    def test_cleanup_gcs_files_bulk_mode(self):
        """cleanup_gcs_files supports bulk deletion via gcs_paths."""
        mock = _mock_tool({"deleted": 3})
        paths = ["gs://bucket/dir1/", "gs://bucket/dir2/"]

        with _patch_get_tool(mock, _STORAGE_MANAGEMENT):
            from foldrun_app.skills.storage_management import cleanup_gcs_files

            cleanup_gcs_files(gcs_paths=paths, confirm_delete=True, search_only=False)

            args = mock.run.call_args[0][0]
            assert args["gcs_paths"] == paths
            assert args["confirm_delete"] is True
            assert args["search_only"] is False

    def test_find_orphaned_gcs_files_calls_correct_tool(self):
        """find_orphaned_gcs_files calls af2_find_orphaned_files tool."""
        mock = _mock_tool({"orphaned_count": 2})

        with _patch_get_tool(mock, _STORAGE_MANAGEMENT) as mock_get:
            from foldrun_app.skills.storage_management import find_orphaned_gcs_files

            find_orphaned_gcs_files(check_fasta=True, max_jobs_to_check=500)

            mock_get.assert_called_with("af2_find_orphaned_files")
            args = mock.run.call_args[0][0]
            assert args["check_fasta"] is True
            assert args["max_jobs_to_check"] == 500


class TestVisualizationSkills:
    """Tests for visualization skill wrappers."""

    def test_open_structure_viewer_calls_correct_tool(self):
        """open_structure_viewer calls af2_open_viewer tool."""
        mock = _mock_tool({"viewer_url": "https://viewer.example.com/job/test"})

        with _patch_get_tool(mock, _VISUALIZATION) as mock_get:
            from foldrun_app.skills.visualization import open_structure_viewer

            open_structure_viewer(job_id="pipeline-123", model_name="Best Model")

            mock_get.assert_called_with("af2_open_viewer")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "pipeline-123"
            assert args["model_name"] == "Best Model"
            assert args["open_browser"] is True

    def test_open_boltz2_structure_viewer_calls_correct_tool(self):
        """open_boltz2_structure_viewer calls boltz2_open_viewer tool."""
        mock = _mock_tool({"viewer_url": "https://viewer.example.com/job/boltz-123"})

        with _patch_get_tool(mock, _VISUALIZATION) as mock_get:
            from foldrun_app.skills.visualization import open_boltz2_structure_viewer

            open_boltz2_structure_viewer(job_id="boltz-123")

            mock_get.assert_called_with("boltz2_open_viewer")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "boltz-123"


class TestBoltz2JobSubmissionSkills:
    """Tests for Boltz-2 job_submission skill wrappers."""

    def test_submit_boltz2_prediction_calls_correct_tool(self):
        """submit_boltz2_prediction calls boltz2_submit_prediction tool."""
        mock = _mock_tool({"status": "submitted", "job_id": "boltz-456"})

        with _patch_get_tool(mock, _JOB_SUBMISSION) as mock_get:
            from foldrun_app.skills.job_submission import submit_boltz2_prediction

            result = submit_boltz2_prediction(
                input=">A\nMKQHEDKL",
                job_name="test-boltz",
                num_model_seeds=2,
                num_diffusion_samples=5,
                gpu_type="A100",
                enable_flex_start=False,
            )

            mock_get.assert_called_with("boltz2_submit_prediction")
            args = mock.run.call_args[0][0]
            assert args["input"] == ">A\nMKQHEDKL"
            assert args["job_name"] == "test-boltz"
            assert args["num_model_seeds"] == 2
            assert args["num_diffusion_samples"] == 5
            assert args["gpu_type"] == "A100"
            assert args["enable_flex_start"] is False
            assert result["status"] == "submitted"

    def test_submit_boltz2_defaults(self):
        """submit_boltz2_prediction has sensible defaults."""
        mock = _mock_tool({"status": "submitted"})

        with _patch_get_tool(mock, _JOB_SUBMISSION):
            from foldrun_app.skills.job_submission import submit_boltz2_prediction

            submit_boltz2_prediction(input=">A\nMKQHEDKL")

            args = mock.run.call_args[0][0]
            assert args["num_model_seeds"] == 1
            assert args["num_diffusion_samples"] == 5
            assert args["gpu_type"] == "auto"
            assert args["enable_flex_start"] is True


class TestBoltz2ResultsAnalysisSkills:
    """Tests for Boltz-2 results_analysis skill wrappers."""

    def test_boltz2_analyze_job_parallel_calls_correct_tool(self):
        """boltz2_analyze_job_parallel calls boltz2_analyze_parallel tool."""
        mock = _mock_tool({"status": "started", "total_predictions": 5})

        with _patch_get_tool(mock, _RESULTS_ANALYSIS) as mock_get:
            from foldrun_app.skills.results_analysis import boltz2_analyze_job_parallel

            result = boltz2_analyze_job_parallel(job_id="boltz-123", overwrite=True)

            mock_get.assert_called_with("boltz2_analyze_parallel")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "boltz-123"
            assert args["overwrite"] is True
            assert result["status"] == "started"

    def test_boltz2_analyze_job_parallel_default_overwrite(self):
        """boltz2_analyze_job_parallel defaults overwrite to False."""
        mock = _mock_tool({"status": "started"})

        with _patch_get_tool(mock, _RESULTS_ANALYSIS):
            from foldrun_app.skills.results_analysis import boltz2_analyze_job_parallel

            boltz2_analyze_job_parallel(job_id="boltz-123")

            args = mock.run.call_args[0][0]
            assert args["overwrite"] is False

    def test_boltz2_get_analysis_results_calls_correct_tool(self):
        """boltz2_get_analysis_results calls boltz2_get_analysis_results tool."""
        mock = _mock_tool({"status": "complete", "ranking_score": 0.85})

        with _patch_get_tool(mock, _RESULTS_ANALYSIS) as mock_get:
            from foldrun_app.skills.results_analysis import boltz2_get_analysis_results

            result = boltz2_get_analysis_results(
                job_id="boltz-123", wait=True, timeout=30, poll_interval=5
            )

            mock_get.assert_called_with("boltz2_get_analysis_results")
            args = mock.run.call_args[0][0]
            assert args["job_id"] == "boltz-123"
            assert args["wait"] is True
            assert args["timeout"] == 30
            assert args["poll_interval"] == 5
            assert result["status"] == "complete"

    def test_boltz2_get_analysis_results_defaults(self):
        """boltz2_get_analysis_results has sensible polling defaults."""
        mock = _mock_tool({"status": "running"})

        with _patch_get_tool(mock, _RESULTS_ANALYSIS):
            from foldrun_app.skills.results_analysis import boltz2_get_analysis_results

            boltz2_get_analysis_results(job_id="boltz-123")

            args = mock.run.call_args[0][0]
            assert args["wait"] is False
            assert args["timeout"] == 10
            assert args["poll_interval"] == 2
