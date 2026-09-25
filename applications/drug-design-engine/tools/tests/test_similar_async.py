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

"""Tests for PubChem async handling and failed-search distinction (#148).

Covers:
1. PubChem HTTP 202 async polling (mock)
2. Async timeout handling
3. Failed vs clean-result distinction in artifact output
4. Indeterminate verdict on all-backends-failed
5. Relay fires on all-backends-failed and search-incomplete
6. Successful async search produces normal artifact
7. Partial failure (one backend fails, one succeeds)
8. HTTP 5xx/429 retry is present in shared HTTP layer
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the modules under test
# so that the import succeeds even when ``requests``, ``click``,
# ``yaml``, or ``rdkit`` are not installed in the test environment.
_mock_requests = MagicMock()
_mock_yaml = MagicMock()
_mock_click = MagicMock()
_mock_rdkit = MagicMock()
_module_patches = patch.dict(
    "sys.modules",
    {
        "requests": _mock_requests,
        "yaml": _mock_yaml,
        "click": _mock_click,
        "rdkit": _mock_rdkit,
        "rdkit.Chem": MagicMock(),
    },
)
_module_patches.start()

from dde.commands.similar import (  # noqa: E402
    _build_artifact,
    _classify_results,
    _poll_pubchem_listkey,
    _pubchem_similarity,
)
from dde.core import http  # noqa: E402
from dde.core.errors import ArtifactError  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _waiting_response(listkey: str = "12345") -> dict[str, Any]:
    """PubChem async response with a ListKey (HTTP 202 pattern)."""
    return {"Waiting": {"ListKey": listkey}}


def _cid_list_response(cids: list[int] | None = None) -> dict[str, Any]:
    """PubChem response with completed CID list."""
    if cids is None:
        cids = [2244, 5090, 3672]
    return {"IdentifierList": {"CID": cids}}


def _property_response(cids: list[int] | None = None) -> dict[str, Any]:
    """PubChem property table response for CIDs."""
    if cids is None:
        cids = [2244, 5090, 3672]
    props = []
    for cid in cids:
        props.append(
            {
                "CID": cid,
                "CanonicalSMILES": f"SMILES_{cid}",
                "IUPACName": f"compound_{cid}",
                "MolecularWeight": 180.0 + cid % 100,
                "InChIKey": f"INCHIKEY_{cid}",
            }
        )
    return {"PropertyTable": {"Properties": props}}


def _failed_artifact(reason: str = "endpoint down") -> dict[str, Any]:
    """Build a failed search artifact for classification tests."""
    return _build_artifact(
        smiles="CCO",
        search_type="similarity",
        source="both",
        threshold=0.85,
        max_results=20,
        hits=[],
        search_status="failed",
        failure_reason=reason,
        backend_results=[
            {"backend": "pubchem", "status": "failed", "failure_reason": reason},
            {"backend": "chembl", "status": "failed", "failure_reason": reason},
        ],
    )


def _completed_artifact_no_hits() -> dict[str, Any]:
    """Build a completed search artifact with zero hits."""
    return _build_artifact(
        smiles="CCO",
        search_type="similarity",
        source="both",
        threshold=0.85,
        max_results=20,
        hits=[],
        search_status="completed",
        backend_results=[
            {"backend": "pubchem", "status": "completed", "hit_count": 0},
            {"backend": "chembl", "status": "completed", "hit_count": 0},
        ],
    )


def _completed_artifact_with_hits() -> dict[str, Any]:
    """Build a completed artifact with one hit above threshold."""
    hits = [
        {
            "source_db": "pubchem",
            "cid": 2244,
            "canonical_smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
            "iupac_name": "aspirin",
            "tanimoto": 0.92,
            "tanimoto_is_lower_bound": True,
            "molecular_weight": 180.16,
            "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
        }
    ]
    return _build_artifact(
        smiles="CCO",
        search_type="similarity",
        source="pubchem",
        threshold=0.85,
        max_results=20,
        hits=hits,
        search_status="completed",
        backend_results=[
            {"backend": "pubchem", "status": "completed", "hit_count": 1},
        ],
    )


def _partial_artifact() -> dict[str, Any]:
    """Build a partial artifact (one backend succeeded, one failed)."""
    hits = [
        {
            "source_db": "chembl",
            "chembl_id": "CHEMBL25",
            "canonical_smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
            "pref_name": "ASPIRIN",
            "tanimoto": 0.90,
            "molecular_weight": None,
            "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
        }
    ]
    return _build_artifact(
        smiles="CCO",
        search_type="similarity",
        source="both",
        threshold=0.85,
        max_results=20,
        hits=hits,
        search_status="partial",
        failure_reason="pubchem: PubChem async search timed out",
        backend_results=[
            {
                "backend": "pubchem",
                "status": "failed",
                "failure_reason": "PubChem async search timed out",
            },
            {"backend": "chembl", "status": "completed", "hit_count": 1},
        ],
    )


# ---------------------------------------------------------------------------
# Test: PubChem 202 async polling
# ---------------------------------------------------------------------------


class TestPubChemAsyncPolling(unittest.TestCase):
    """PubChem HTTP 202 async ListKey pattern."""

    def test_poll_returns_cids_on_success(self):
        """_poll_pubchem_listkey returns CID list when result is ready."""
        cids = [2244, 5090]
        responses = [_cid_list_response(cids)]

        call_count = 0

        def mock_get_json(url, **kwargs):
            nonlocal call_count
            call_count += 1
            return responses[min(call_count - 1, len(responses) - 1)]

        with (
            patch("dde.commands.similar.http.get_json", side_effect=mock_get_json),
            patch("time.sleep"),
            patch("time.monotonic", side_effect=[0.0, 1.0, 2.0, 3.0]),
        ):
            result = _poll_pubchem_listkey("test-key", qps=5.0, timeout=120)

        self.assertEqual(result, cids)

    def test_poll_retries_on_waiting(self):
        """_poll_pubchem_listkey retries when PubChem returns Waiting."""
        responses = [
            {"Waiting": {"ListKey": "test-key"}},  # still waiting
            {"Waiting": {"ListKey": "test-key"}},  # still waiting
            _cid_list_response([2244]),  # ready
        ]

        call_count = 0

        def mock_get_json(url, **kwargs):
            nonlocal call_count
            result = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return result

        with (
            patch("dde.commands.similar.http.get_json", side_effect=mock_get_json),
            patch("time.sleep"),
            patch("time.monotonic", side_effect=[0.0, 1.0, 2.0, 3.0, 4.0]),
        ):
            result = _poll_pubchem_listkey("test-key", qps=5.0, timeout=120)

        self.assertEqual(result, [2244])
        self.assertEqual(call_count, 3)

    def test_poll_raises_on_timeout(self):
        """_poll_pubchem_listkey raises ArtifactError on timeout."""

        def mock_get_json(url, **kwargs):
            return {"Waiting": {"ListKey": "test-key"}}

        # Simulate time advancing past the timeout
        times = [0.0] + [float(i) for i in range(1, 200)]

        with (
            patch("dde.commands.similar.http.get_json", side_effect=mock_get_json),
            patch("time.sleep"),
            patch("time.monotonic", side_effect=times),
        ):
            with self.assertRaises(ArtifactError) as ctx:
                _poll_pubchem_listkey("test-key", qps=5.0, timeout=5)

        self.assertIn("timed out", str(ctx.exception))

    def test_poll_raises_on_unexpected_response(self):
        """_poll_pubchem_listkey raises ArtifactError on unexpected response."""

        def mock_get_json(url, **kwargs):
            return {"Fault": {"Message": "Server Error"}}

        with (
            patch("dde.commands.similar.http.get_json", side_effect=mock_get_json),
            patch("time.sleep"),
            patch("time.monotonic", side_effect=[0.0, 1.0]),
        ):
            with self.assertRaises(ArtifactError) as ctx:
                _poll_pubchem_listkey("test-key", qps=5.0, timeout=120)

        self.assertIn("failed", str(ctx.exception))


# ---------------------------------------------------------------------------
# Test: PubChem similarity with async 202
# ---------------------------------------------------------------------------


class TestPubChemSimilarityAsync(unittest.TestCase):
    """_pubchem_similarity correctly handles the async ListKey flow."""

    def test_async_listkey_flow(self):
        """When initial request returns Waiting/ListKey, polls and returns hits."""
        initial = _waiting_response("key-123")
        poll_result = _cid_list_response([2244])
        props = _property_response([2244])

        calls = []

        def mock_get_json(url, **kwargs):
            calls.append(url)
            if "similarity/smiles" in url:
                return initial
            elif "listkey" in url:
                return poll_result
            elif "property" in url:
                return props
            return {}

        with (
            patch("dde.commands.similar.http.get_json", side_effect=mock_get_json),
            patch("time.sleep"),
            patch("time.monotonic", side_effect=[0.0, 1.0, 2.0]),
        ):
            hits = _pubchem_similarity("CCO", threshold=0.85, max_results=20)

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["cid"], 2244)
        self.assertEqual(hits[0]["source_db"], "pubchem")
        # Verify tolerate_status was passed (the initial call should include it)
        self.assertTrue(any("similarity/smiles" in c for c in calls))

    def test_direct_result_no_polling(self):
        """When PubChem returns results directly, no polling needed."""
        direct = _cid_list_response([5090])
        props = _property_response([5090])

        def mock_get_json(url, **kwargs):
            if "similarity/smiles" in url:
                return direct
            elif "property" in url:
                return props
            return {}

        with patch("dde.commands.similar.http.get_json", side_effect=mock_get_json):
            hits = _pubchem_similarity("CCO", threshold=0.85, max_results=20)

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["cid"], 5090)

    def test_empty_result_returns_empty_list(self):
        """When PubChem returns no CIDs, returns empty list."""
        direct = {"IdentifierList": {"CID": []}}

        def mock_get_json(url, **kwargs):
            return direct

        with patch("dde.commands.similar.http.get_json", side_effect=mock_get_json):
            hits = _pubchem_similarity("CCO", threshold=0.85, max_results=20)

        self.assertEqual(hits, [])


# ---------------------------------------------------------------------------
# Test: Build artifact with search status
# ---------------------------------------------------------------------------


class TestBuildArtifactSearchStatus(unittest.TestCase):
    """_build_artifact correctly records search status metadata."""

    def test_completed_artifact_has_status(self):
        """Completed search artifact has search_status=completed."""
        artifact = _completed_artifact_no_hits()
        self.assertEqual(artifact["search_status"], "completed")
        self.assertEqual(artifact["result_count"], 0)
        self.assertNotIn("failure_reason", artifact)

    def test_failed_artifact_has_status_and_reason(self):
        """Failed search artifact has search_status=failed and failure_reason."""
        artifact = _failed_artifact("endpoint down")
        self.assertEqual(artifact["search_status"], "failed")
        self.assertEqual(artifact["result_count"], 0)
        self.assertEqual(artifact["failure_reason"], "endpoint down")

    def test_partial_artifact_has_status(self):
        """Partial search artifact has search_status=partial."""
        artifact = _partial_artifact()
        self.assertEqual(artifact["search_status"], "partial")
        self.assertEqual(artifact["result_count"], 1)
        self.assertIn("failure_reason", artifact)

    def test_backend_results_recorded(self):
        """Backend results list is present in artifact."""
        artifact = _failed_artifact()
        self.assertIn("backend_results", artifact)
        self.assertEqual(len(artifact["backend_results"]), 2)

        backends = {br["backend"] for br in artifact["backend_results"]}
        self.assertEqual(backends, {"pubchem", "chembl"})

    def test_default_search_status_is_completed(self):
        """_build_artifact defaults to search_status=completed."""
        artifact = _build_artifact(
            smiles="CCO",
            search_type="similarity",
            source="both",
            threshold=0.85,
            max_results=20,
            hits=[],
        )
        self.assertEqual(artifact["search_status"], "completed")


# ---------------------------------------------------------------------------
# Test: Classification with search status
# ---------------------------------------------------------------------------


class TestClassifyFailedSearch(unittest.TestCase):
    """_classify_results returns indeterminate on failed search."""

    def test_failed_search_returns_indeterminate(self):
        """Failed search → verdict is 'indeterminate', not 'novel'."""
        artifact = _failed_artifact("all backends down")
        verdict, _relays = _classify_results(artifact, 0.85, 0.99)

        self.assertEqual(verdict, "indeterminate")
        # Must NOT be "novel" — that would violate #84.
        self.assertNotEqual(verdict, "novel")

    def test_failed_search_fires_all_backends_failed_relay(self):
        """Failed search fires similar.all_backends_failed relay."""
        artifact = _failed_artifact("timeout on all backends")
        _verdict, relays = _classify_results(artifact, 0.85, 0.99)

        relay_codes = [r["code"] for r in relays]
        self.assertIn("similar.all_backends_failed", relay_codes)

    def test_failed_search_relay_includes_reason(self):
        """The all_backends_failed relay message includes the failure reason."""
        reason = "PubChem 503; ChEMBL connection refused"
        artifact = _failed_artifact(reason)
        _, relays = _classify_results(artifact, 0.85, 0.99)

        abf_relay = next(
            r for r in relays if r["code"] == "similar.all_backends_failed"
        )
        self.assertIn(reason, abf_relay["message"])

    def test_failed_search_still_fires_coverage_relay(self):
        """Failed search still fires the database_coverage_limited relay."""
        artifact = _failed_artifact()
        _, relays = _classify_results(artifact, 0.85, 0.99)

        relay_codes = [r["code"] for r in relays]
        self.assertIn("similar.database_coverage_limited", relay_codes)


class TestClassifyCompletedSearch(unittest.TestCase):
    """_classify_results handles completed searches correctly."""

    def test_completed_no_hits_is_novel(self):
        """Completed search with no hits → verdict is 'novel'."""
        artifact = _completed_artifact_no_hits()
        verdict, _ = _classify_results(artifact, 0.85, 0.99)
        self.assertEqual(verdict, "novel")

    def test_completed_with_hits_above_cutoff(self):
        """Completed search with hits above tanimoto cutoff → known-compound-found."""
        artifact = _completed_artifact_with_hits()
        verdict, _ = _classify_results(artifact, 0.85, 0.99)
        self.assertEqual(verdict, "known-compound-found")

    def test_completed_with_exact_match(self):
        """Hit with tanimoto >= exact_match_cutoff → exact-match."""
        hits = [
            {
                "source_db": "pubchem",
                "cid": 2244,
                "canonical_smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
                "tanimoto": 1.0,
                "inchikey": "TEST",
            }
        ]
        artifact = _build_artifact(
            "CCO",
            "similarity",
            "pubchem",
            0.85,
            20,
            hits,
            search_status="completed",
        )
        verdict, _ = _classify_results(artifact, 0.85, 0.99)
        self.assertEqual(verdict, "exact-match")

    def test_novel_vs_failed_distinction(self):
        """The critical distinction: completed-0-hits ≠ failed-0-hits."""
        completed = _completed_artifact_no_hits()
        failed = _failed_artifact()

        completed_verdict, _ = _classify_results(completed, 0.85, 0.99)
        failed_verdict, _ = _classify_results(failed, 0.85, 0.99)

        # This is the core requirement from #84:
        self.assertEqual(completed_verdict, "novel")
        self.assertEqual(failed_verdict, "indeterminate")
        self.assertNotEqual(completed_verdict, failed_verdict)


class TestClassifyPartialSearch(unittest.TestCase):
    """_classify_results handles partial searches correctly."""

    def test_partial_search_fires_search_incomplete_relay(self):
        """Partial search fires similar.search_incomplete relay."""
        artifact = _partial_artifact()
        _, relays = _classify_results(artifact, 0.85, 0.99)

        relay_codes = [r["code"] for r in relays]
        self.assertIn("similar.search_incomplete", relay_codes)

    def test_partial_search_still_classifies_available_hits(self):
        """Partial search with hits still classifies based on available data."""
        artifact = _partial_artifact()
        verdict, _ = _classify_results(artifact, 0.85, 0.99)

        # The partial artifact has a hit with tanimoto 0.90 >= cutoff 0.85
        self.assertEqual(verdict, "known-compound-found")

    def test_partial_search_no_hits_is_novel(self):
        """Partial search with no hits from the surviving backend → novel."""
        artifact = _build_artifact(
            "CCO",
            "similarity",
            "both",
            0.85,
            20,
            [],
            search_status="partial",
            failure_reason="pubchem: timeout",
            backend_results=[
                {"backend": "pubchem", "status": "failed", "failure_reason": "timeout"},
                {"backend": "chembl", "status": "completed", "hit_count": 0},
            ],
        )
        verdict, relays = _classify_results(artifact, 0.85, 0.99)

        self.assertEqual(verdict, "novel")
        relay_codes = [r["code"] for r in relays]
        self.assertIn("similar.search_incomplete", relay_codes)


# ---------------------------------------------------------------------------
# Test: Legacy artifact compatibility
# ---------------------------------------------------------------------------


class TestLegacyArtifactCompat(unittest.TestCase):
    """Artifacts without search_status field are treated as completed."""

    def test_missing_search_status_defaults_to_completed(self):
        """Legacy artifacts (no search_status) classify normally."""
        legacy = {
            "schema": "dde.similar.v1",
            "query": {"smiles": "CCO"},
            "summary": {"n_hits": 0, "closest_match": None},
            "hits": [],
        }
        verdict, _ = _classify_results(legacy, 0.85, 0.99)
        self.assertEqual(verdict, "novel")


# ---------------------------------------------------------------------------
# Test: HTTP layer already handles 5xx/429
# ---------------------------------------------------------------------------


class TestHttpRetryStatus(unittest.TestCase):
    """Verify that the shared HTTP layer retries on 5xx and 429."""

    def test_retry_status_codes_defined(self):
        """_RETRY_STATUS includes 429, 500, 502, 503, 504."""
        expected = {429, 500, 502, 503, 504}
        self.assertEqual(http._RETRY_STATUS, expected)

    def test_retry_after_header_respected(self):
        """The request() function reads Retry-After on 429."""
        # We verify by reading the source — the retry logic is in
        # http.request() and respects retry_after header.
        import inspect

        source = inspect.getsource(http.request)
        self.assertIn("Retry-After", source)
        self.assertIn("retry_after", source)


# ---------------------------------------------------------------------------
# Test: Relay code registration
# ---------------------------------------------------------------------------


class TestRelayRegistration(unittest.TestCase):
    """New relay codes are registered in provenance.RELAY_CODES."""

    def test_search_incomplete_registered(self):
        """similar.search_incomplete is a registered relay code."""
        from dde.core.provenance import RELAY_CODES

        self.assertIn("similar.search_incomplete", RELAY_CODES)

    def test_all_backends_failed_registered(self):
        """similar.all_backends_failed is a registered relay code."""
        from dde.core.provenance import RELAY_CODES

        self.assertIn("similar.all_backends_failed", RELAY_CODES)

    def test_relay_function_accepts_new_codes(self):
        """provenance.relay() accepts the new codes without raising."""
        from dde.core.provenance import relay

        r1 = relay("similar.search_incomplete", "test message")
        self.assertEqual(r1["code"], "similar.search_incomplete")

        r2 = relay("similar.all_backends_failed", "test message")
        self.assertEqual(r2["code"], "similar.all_backends_failed")


# ---------------------------------------------------------------------------
# Test: Tolerate-status for PubChem 202
# ---------------------------------------------------------------------------


class TestPubChemTolerates202(unittest.TestCase):
    """PubChem search functions pass tolerate_status=(202,) to http.get_json."""

    def test_similarity_passes_tolerate_status(self):
        """_pubchem_similarity passes tolerate_status=(202,) for initial request."""
        kwargs_captured = {}

        def mock_get_json(url, **kwargs):
            if "similarity/smiles" in url:
                kwargs_captured.update(kwargs)
                return {"IdentifierList": {"CID": []}}
            return {}

        with patch("dde.commands.similar.http.get_json", side_effect=mock_get_json):
            _pubchem_similarity("CCO", threshold=0.85, max_results=20)

        self.assertIn("tolerate_status", kwargs_captured)
        self.assertIn(202, kwargs_captured["tolerate_status"])


if __name__ == "__main__":
    unittest.main()
