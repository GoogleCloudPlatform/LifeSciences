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

"""FoldRun ADK Agent application package."""

__all__ = ["app", "create_alphafold_agent", "create_foldrun_agent", "root_agent"]


def __getattr__(name: str):
    """Lazily import agent entrypoints so submodule imports do not trigger eager ADK startup."""
    if name in __all__:
        from foldrun_app import agent as _agent_mod

        return getattr(_agent_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
