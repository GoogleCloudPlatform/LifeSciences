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

"""Typing definitions for feedback and audit logs."""

from pydantic import BaseModel, Field


class Feedback(BaseModel):
    """User feedback payload."""

    score: int = Field(..., description="Rating from 1 to 5")
    comment: str = Field(default="", description="Optional textual user feedback")
    session_id: str = Field(default="", description="ADK session identifier")
