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

"""Pytest configuration and global fixtures for AlphaGenome variant annotation agent."""

import os
import sys
from pathlib import Path

# Ensure agent app directory is in python path
APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# Set test environment defaults before app.agent is imported
os.environ.setdefault("SKIP_DEEPMIND_SKILLS_FETCH", "1")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-genomics-project")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("COORDINATOR_MODEL_NAME", "gemini-4-argon")
os.environ.setdefault("WORKER_MODEL_NAME", "gemini-3.8-flash")
