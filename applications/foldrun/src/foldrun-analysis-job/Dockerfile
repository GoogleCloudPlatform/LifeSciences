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

# FoldRun Unified Analysis Job - Cloud Run Job Dockerfile
# Build context: applications/foldrun/ (docker build -f src/foldrun-analysis-job/Dockerfile .)

# Stage 1: Build environment
FROM python:3.15-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /build/src/foldrun-analysis-job

# Install build dependencies (apt)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy shared foldrun-common package, config, and source
COPY foldrun-agent/packages/foldrun-common /build/foldrun-agent/packages/foldrun-common
COPY src/foldrun-analysis-job/pyproject.toml src/foldrun-analysis-job/uv.lock src/foldrun-analysis-job/README.md ./
COPY src/foldrun-analysis-job/foldrun_analysis/ ./foldrun_analysis/

# Install the package and its dependencies into /app/.venv
RUN uv sync --frozen --no-dev --no-editable

# Stage 2: Runtime image
FROM python:3.15-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["foldrun-analysis"]
