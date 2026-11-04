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

# AlphaFold Structure Viewer - Cloud Run Dockerfile
# Build context: applications/foldrun/ (docker build -f src/foldrun-viewer/Dockerfile .)

# Stage 1: Build environment
FROM python:3.13-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /build/src/foldrun-viewer

# Copy shared foldrun-common package and viewer dependency lock
COPY foldrun-agent/packages/foldrun-common /build/foldrun-agent/packages/foldrun-common
COPY src/foldrun-viewer/pyproject.toml src/foldrun-viewer/uv.lock ./

# Install dependencies (frozen, no-dev, no-install-project, non-editable for Stage 2)
RUN uv sync --frozen --no-install-project --no-dev --no-editable

# Stage 2: Runtime image
FROM python:3.13-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application files
COPY src/foldrun-viewer/app.py .
COPY src/foldrun-viewer/templates/ templates/
COPY src/foldrun-viewer/static/ static/

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app && chmod o+w /app
USER appuser

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 0 app:app
