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

"""Standalone FastAPI application for the FoldRun Direct Cloud Run HTTP Gateway."""

from fastapi import FastAPI

from .router import router as gateway_router


def create_gateway_app() -> FastAPI:
    """Create and configure the standalone FoldRun Gateway FastAPI application."""
    gateway_app = FastAPI(
        title="FoldRun Direct Cloud Run Gateway",
        description=(
            "Stateless HTTP gateway for AlphaFold 2, AlphaFold 3, OpenFold 3, Boltz-2, "
            "BoltzGen, RFD4-Proteina, and BindCraft on Vertex AI."
        ),
        version="0.1.0",
    )
    gateway_app.include_router(gateway_router)

    @gateway_app.get("/health")
    @gateway_app.get("/v1/health")
    @gateway_app.get("/api/v1/health")
    @gateway_app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Lightweight liveness/readiness probe for Cloud Run."""
        return {"status": "ok"}

    return gateway_app


app = create_gateway_app()
