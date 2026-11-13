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

"""Unit tests for foldrun_app.core.pipeline_utils (Finding 4.4 / CWE-377)."""

import os
import tempfile
from unittest.mock import MagicMock, patch

from foldrun_app.core.pipeline_utils import compile_pipeline


class TestCompilePipelineSecurity:
    """Tests for secure temporary file creation and path sanitization in compile_pipeline."""

    def test_default_output_path_is_unpredictable(self):
        """When output_path is None, consecutive calls must not produce a predictable static path."""
        dummy_fn = MagicMock()
        mock_compiler = MagicMock()

        with patch("kfp.compiler.Compiler", return_value=mock_compiler):
            path1 = compile_pipeline(dummy_fn, pipeline_name="alphafold-pipeline")
            path2 = compile_pipeline(dummy_fn, pipeline_name="alphafold-pipeline")

        try:
            predictable_path = os.path.join(tempfile.gettempdir(), "alphafold-pipeline.json")
            assert path1 != predictable_path, f"Path should not be predictable: {path1}"
            assert path1 != path2, (
                f"Two invocations should use unique temp files: {path1} vs {path2}"
            )
            assert path1.endswith(".json")
            assert path2.endswith(".json")
        finally:
            for p in (path1, path2):
                if p and os.path.exists(p):
                    os.remove(p)

    def test_pipeline_name_path_traversal_sanitized(self):
        """Path traversal sequences in pipeline_name must be sanitized and stay inside tempdir."""
        dummy_fn = MagicMock()
        mock_compiler = MagicMock()

        with patch("kfp.compiler.Compiler", return_value=mock_compiler):
            path = compile_pipeline(dummy_fn, pipeline_name="../../escaped_target")

        try:
            temp_dir = os.path.realpath(tempfile.gettempdir())
            resolved_path = os.path.realpath(path)
            assert os.path.commonpath([temp_dir, resolved_path]) == temp_dir, (
                f"Resolved path {resolved_path} escaped tempdir {temp_dir}"
            )
            assert ".." not in os.path.basename(path)
            assert "/" not in os.path.basename(path)
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def test_explicit_output_path_preserved(self, tmp_path):
        """When output_path is explicitly provided, compile_pipeline uses it."""
        dummy_fn = MagicMock()
        mock_compiler = MagicMock()
        explicit_path = str(tmp_path / "custom_pipeline.json")

        with patch("kfp.compiler.Compiler", return_value=mock_compiler):
            result = compile_pipeline(dummy_fn, output_path=explicit_path)

        assert result == explicit_path
        mock_compiler.compile.assert_called_once_with(
            pipeline_func=dummy_fn, package_path=explicit_path
        )
