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

"""Pytest wrapper around `AgentEvaluator.evaluate()` — the canonical ADK eval API.

Equivalent to running:

    adk eval . biocompass_agent/biocompass.evalset.json \\
        --config_file_path biocompass_agent/tests/test_config.json \\
        --print_detailed_results

Use this for CI integration. For ad-hoc runs, prefer the CLI command above.

Costs: each eval case triggers `num_samples` (default 3 in our config) calls
to the rubric judge model AND `num_samples` to the hallucinations judge,
PER invocation. Deep-research cases also trigger the agent itself which
fans out 4 retrievers + synth + critic loop. Run a subset for fast feedback:

    adk eval . biocompass_agent/biocompass.evalset.json:pushback_lecanemab,off_topic_refusal \\
        --config_file_path biocompass_agent/tests/test_config.json
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Load the agent .env BEFORE the AgentEvaluator imports anything that builds
# a Gen AI client (the eval framework does, for the judge models).
_ENV_PATH = Path(__file__).resolve().parent.parent / '.env'
if _ENV_PATH.exists():
  for _line in _ENV_PATH.read_text().splitlines():
    _line = _line.strip()
    if not _line or _line.startswith('#') or '=' not in _line:
      continue
    _k, _v = _line.split('=', 1)
    os.environ.setdefault(_k.strip(), _v.strip())

from google.adk.evaluation.agent_evaluator import AgentEvaluator  # noqa: E402


_AGENT_PROJECT = Path(__file__).resolve().parent.parent.parent
_EVALSET = _AGENT_PROJECT / 'biocompass_agent' / 'biocompass.evalset.json'
_CONFIG = _AGENT_PROJECT / 'biocompass_agent' / 'tests' / 'test_config.json'


@pytest.mark.asyncio
async def test_biocompass_evalset() -> None:
  """Run the full BioCompass evalset against the rubric + hallucinations judges."""
  await AgentEvaluator.evaluate(
      agent_module='biocompass_agent',
      eval_dataset_file_path_or_dir=str(_EVALSET),
      config_file_path=str(_CONFIG),
      num_runs=1,
  )
