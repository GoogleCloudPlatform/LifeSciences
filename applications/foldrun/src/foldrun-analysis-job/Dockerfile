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

# Stage 1: Build environment
FROM python:3.13-slim AS builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy build specifications and source package
COPY pyproject.toml .
COPY README.md .
COPY foldrun_analysis/ ./foldrun_analysis/

# Build and install the package
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime image
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Copy installed package and dependencies from builder
COPY --from=builder /install /usr/local

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Entry point runs the installed console script
ENTRYPOINT ["foldrun-analysis"]
