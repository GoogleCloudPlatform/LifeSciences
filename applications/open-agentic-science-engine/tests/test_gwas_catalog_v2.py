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

"""Regression tests for the GWAS Catalog REST API v2 migration."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from oase.commands import doctor as doctor_mod
from oase.commands import gwas as gwas_mod
from oase.core import http as http_mod
from oase.core.errors import EndpointError


def _response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return response


def _page(
    associations: list[dict[str, Any]],
    *,
    number: int = 0,
    total_pages: int = 1,
    next_url: str | None = None,
) -> dict[str, Any]:
    links: dict[str, Any] = {
        "self": {
            "href": (
                "https://www.ebi.ac.uk/gwas/rest/api/v2/associations"
                f"?mapped_gene=HBB&extended_geneset=true&page={number}&size=500"
            )
        }
    }
    if next_url is not None:
        links["next"] = {"href": next_url}
    return {
        "_embedded": {"associations": associations},
        "_links": links,
        "page": {
            "size": 500,
            "totalElements": len(associations),
            "totalPages": total_pages,
            "number": number,
        },
    }


def _v2_association(
    *,
    association_id: int,
    p_value: float,
    trait_id: str,
    trait_name: str,
    rs_id: str,
    accession_id: str,
    or_per_copy_num: float | None = None,
    beta_num: float | None = None,
    beta: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "association_id": association_id,
        "p_value": p_value,
        "pvalue_mantissa": 5,
        "pvalue_exponent": -8,
        "efo_traits": [{"efo_id": trait_id, "efo_trait": trait_name}],
        "snp_allele": [{"rs_id": rs_id, "effect_allele": "A"}],
        "accession_id": accession_id,
        "mapped_genes": ["HBB"],
        "reported_trait": [trait_name],
        "pubmed_id": "12345678",
    }
    if or_per_copy_num is not None:
        record["or_per_copy_num"] = or_per_copy_num
    if beta_num is not None:
        record["beta_num"] = beta_num
    if beta is not None:
        record["beta"] = beta
    return record


def test_retired_v1_endpoint_is_replaced_by_v2_query() -> None:
    """The fetch must avoid the retired v1 URL and preserve v1 gene semantics."""
    captured_urls: list[str] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
        captured_urls.append(url)
        if "/gwas/rest/api/v2/" not in url:
            raise EndpointError(
                f"{url} returned HTTP 410",
                detail="This legacy GWAS Catalog REST API is no longer available.",
            )
        return _response(_page([]))

    with patch.object(http_mod, "request", side_effect=fake_request):
        gwas_mod._fetch_gwas_catalog("hbb")

    assert len(captured_urls) == 1
    parsed = urlparse(captured_urls[0])
    assert parsed.path == "/gwas/rest/api/v2/associations"
    assert parse_qs(parsed.query) == {
        "mapped_gene": ["HBB"],
        "extended_geneset": ["true"],
        "page": ["0"],
        "size": ["500"],
    }


def test_fetch_follows_two_page_pagination() -> None:
    """All v2 pages are followed and preserved in the raw response envelope."""
    next_url = (
        "https://www.ebi.ac.uk/gwas/rest/api/v2/associations"
        "?mapped_gene=HBB&extended_geneset=true&page=1&size=500"
    )
    first = _page(
        [
            _v2_association(
                association_id=1,
                p_value=5e-8,
                trait_id="EFO_0004305",
                trait_name="erythrocyte count",
                rs_id="rs111",
                accession_id="GCST000001",
            )
        ],
        number=0,
        total_pages=2,
        next_url=next_url,
    )
    second = _page(
        [
            _v2_association(
                association_id=2,
                p_value=1e-9,
                trait_id="MONDO_0011382",
                trait_name="sickle cell disease",
                rs_id="rs222",
                accession_id="GCST000002",
            )
        ],
        number=1,
        total_pages=2,
    )
    responses = iter([_response(first), _response(second)])
    captured_urls: list[str] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
        captured_urls.append(url)
        return next(responses)

    with patch.object(http_mod, "request", side_effect=fake_request):
        raw, artifact = gwas_mod._fetch_gwas_catalog("HBB")

    assert captured_urls[1] == next_url
    assert artifact["summary"]["n_associations"] == 2
    assert [a["study_accession"] for a in artifact["associations"]] == [
        "GCST000002",
        "GCST000001",
    ]
    raw_capture = json.loads(raw)
    assert raw_capture["total_pages"] == 2
    assert raw_capture["pages"] == [first, second]


def test_v2_fields_normalize_to_existing_artifact_contract() -> None:
    """Snake-case v2 records retain the fields consumed by gwas analyze."""
    association = _v2_association(
        association_id=42,
        p_value=2.5e-12,
        trait_id="MONDO_0011382",
        trait_name="sickle cell disease",
        rs_id="rs334",
        accession_id="GCST90000001",
        or_per_copy_num=1.75,
        beta_num=-0.42,
        beta="0.42 unit decrease",
    )

    with patch.object(
        http_mod, "request", return_value=_response(_page([association]))
    ):
        _raw, artifact = gwas_mod._fetch_gwas_catalog("HBB")

    assert artifact["schema"] == "oase.gwas.v1"
    assert artifact["query"] == {"gene": "HBB", "source": "gwas-catalog"}
    assert artifact["summary"]["top_diseases"] == ["sickle cell disease"]
    assert artifact["associations"] == [
        {
            "source_db": "gwas-catalog",
            "disease_id": "MONDO_0011382",
            "disease_name": "sickle cell disease",
            "score": 2.5e-12,
            "rs_ids": ["rs334"],
            "p_value": 2.5e-12,
            "or_per_copy": 1.75,
            "beta": -0.42,
            "beta_text": "0.42 unit decrease",
            "study_accession": "GCST90000001",
        }
    ]


def test_formatted_beta_is_not_coerced_to_numeric_beta() -> None:
    """A formatted v2 beta string must not be parsed into the numeric field."""
    association = _v2_association(
        association_id=43,
        p_value=1e-10,
        trait_id="EFO_0004305",
        trait_name="erythrocyte count",
        rs_id="rs11549407",
        accession_id="GCST90476345",
        beta="3.451 unit decrease",
    )

    with patch.object(
        http_mod, "request", return_value=_response(_page([association]))
    ):
        _raw, artifact = gwas_mod._fetch_gwas_catalog("HBB")

    normalized = artifact["associations"][0]
    assert normalized["beta"] is None
    assert normalized["beta_text"] == "3.451 unit decrease"


def test_empty_v2_result_returns_empty_existing_artifact() -> None:
    """An empty v2 page is a valid zero-association answer."""
    payload = _page([])
    response = _response(payload)
    captured_urls: list[str] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
        captured_urls.append(url)
        return response

    with patch.object(http_mod, "request", side_effect=fake_request):
        raw, artifact = gwas_mod._fetch_gwas_catalog("NOHITS")

    assert urlparse(captured_urls[0]).path == "/gwas/rest/api/v2/associations"
    assert raw == response.content
    assert artifact == {
        "schema": "oase.gwas.v1",
        "query": {"gene": "NOHITS", "source": "gwas-catalog"},
        "summary": {"n_associations": 0, "top_diseases": []},
        "associations": [],
    }


def test_normalized_v2_artifact_keeps_existing_analyze_behavior() -> None:
    """The existing p-value cutoff and relay behavior consume normalized v2 data."""
    association = _v2_association(
        association_id=44,
        p_value=2.5e-12,
        trait_id="MONDO_0011382",
        trait_name="sickle cell disease",
        rs_id="rs334",
        accession_id="GCST90000001",
    )
    with patch.object(
        http_mod, "request", return_value=_response(_page([association]))
    ):
        _raw, artifact = gwas_mod._fetch_gwas_catalog("HBB")

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        relays.append({"code": code, "message": message})

    significant, metrics, assessment = gwas_mod._analyze_gwas(
        "HBB",
        "gwas-catalog",
        artifact["associations"],
        None,
        add_relay,
    )

    assert significant == artifact["associations"]
    assert metrics["threshold"] == 5e-8
    assert metrics["significant_associations"] == 1
    assert assessment["verdict"] == "associations_found"
    assert [relay["code"] for relay in relays] == ["gwas.association_not_causation"]


def test_doctor_probes_v2_metadata() -> None:
    """Doctor should report the supported v2 metadata endpoint as available."""
    response = MagicMock()
    response.status_code = 200
    captured_urls: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> MagicMock:
        captured_urls.append(url)
        return response

    report = doctor_mod.Report()
    with patch("requests.get", side_effect=fake_get):
        doctor_mod._check_gwas_catalog(report)

    assert captured_urls == ["https://www.ebi.ac.uk/gwas/rest/api/v2/metadata"]
    assert len(report.checks) == 1
    assert report.checks[0].status == doctor_mod.OK
    assert "v2" in report.checks[0].detail
