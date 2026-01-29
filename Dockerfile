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

# Stage 1: Build the frontend
FROM node:24-slim AS build-frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Build the API
FROM python:3.12-slim AS build-api
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Omit development dependencies
ENV UV_NO_DEV=1
# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

WORKDIR /app

# Install dependencies and package
COPY pyproject.toml README.md ./
RUN uv sync --no-install-project

# Copy API source code
COPY api ./api
RUN uv sync

# Stage 3: Final image without uv
FROM python:3.12-slim

# Create non-root user
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

# Copy built API and frontend assets
COPY --from=build-api --chown=nonroot:nonroot /app /app
COPY --from=build-frontend --chown=nonroot:nonroot /app/frontend/dist /app/static

ENV PATH="/app/.venv/bin:$PATH"
USER nonroot
WORKDIR /app

# Expose the port
EXPOSE 8080

# Run the application
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
