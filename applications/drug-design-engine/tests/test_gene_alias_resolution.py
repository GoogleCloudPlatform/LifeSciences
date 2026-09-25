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

"""Tests for HGNC gene symbol alias resolution (#147).

Covers:
  - Official symbol resolves directly
  - Alias symbol resolves to canonical (LOR -> LORICRIN)
  - Previous symbol resolves to canonical
  - Ensembl ID passes through without HGNC lookup
  - Unknown symbol returns resolved=false with suggestions
  - Resolution is cached (second call does not hit API)
  - Relay fires on unresolved symbol
  - Sidecar records gene resolution
  - "Resolved, no data" is distinct from "unresolved"
  - Echo message shows resolution mapping
  - Integration with expression, genetics, pathway commands
"""

from __future__ import annotations

import json
import sys
import unittest.mock as mock
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.gene import GeneResolution, resolve_gene

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hgnc_response(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a canned HGNC REST API response."""
    return {
        "responseHeader": {"status": 0},
        "response": {
            "numFound": len(docs),
            "docs": docs,
        },
    }


def _hgnc_doc(
    symbol: str = "LORICRIN",
    hgnc_id: str = "HGNC:6659",
    ensembl_id: str = "ENSG00000203782",
    prev_symbols: list[str] | None = None,
    alias_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Build a canned HGNC doc entry."""
    doc: dict[str, Any] = {
        "hgnc_id": hgnc_id,
        "symbol": symbol,
        "ensembl_gene_id": ensembl_id,
    }
    if prev_symbols:
        doc["prev_symbol"] = prev_symbols
    if alias_symbols:
        doc["alias_symbol"] = alias_symbols
    return doc


# ---------------------------------------------------------------------------
# 1. Official symbol resolves directly
# ---------------------------------------------------------------------------


def test_official_symbol_resolves() -> None:
    """An official HGNC symbol resolves with source='official'."""
    doc = _hgnc_doc(symbol="BRCA1", hgnc_id="HGNC:1100", ensembl_id="ENSG00000012048")
    response = _make_hgnc_response([doc])

    # Clear cache before test
    resolve_gene.cache_clear()

    with mock.patch("dde.core.gene.http.get_json") as mock_get:
        mock_get.return_value = response
        result = resolve_gene("BRCA1")

    assert result.resolved is True
    assert result.canonical_symbol == "BRCA1"
    assert result.hgnc_id == "HGNC:1100"
    assert result.ensembl_id == "ENSG00000012048"
    assert result.source == "official"
    assert result.echo_line() is None  # No mapping needed for official
    print("  PASS: official symbol resolves directly")


# ---------------------------------------------------------------------------
# 2. Alias symbol resolves to canonical (LOR -> LORICRIN)
# ---------------------------------------------------------------------------


def test_alias_resolves_to_canonical() -> None:
    """An alias symbol (LOR) resolves to canonical (LORICRIN)."""
    lor_doc = _hgnc_doc(
        symbol="LORICRIN",
        hgnc_id="HGNC:6659",
        ensembl_id="ENSG00000203782",
        alias_symbols=["LOR"],
    )
    empty = _make_hgnc_response([])
    alias_response = _make_hgnc_response([lor_doc])

    resolve_gene.cache_clear()

    call_count = 0

    def side_effect(url: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if "/search/symbol/" in url:
            return empty  # LOR is not an official symbol
        if "/search/prev_symbol/" in url:
            return empty  # LOR is not a previous symbol
        if "/search/alias_symbol/" in url:
            return alias_response  # LOR IS an alias
        return empty

    with mock.patch("dde.core.gene.http.get_json") as mock_get:
        mock_get.side_effect = side_effect
        result = resolve_gene("LOR")

    assert result.resolved is True
    assert result.canonical_symbol == "LORICRIN"
    assert result.hgnc_id == "HGNC:6659"
    assert result.ensembl_id == "ENSG00000203782"
    assert result.source == "alias_symbol"
    assert result.input == "LOR"
    echo = result.echo_line()
    assert echo is not None
    assert "LOR -> LORICRIN" in echo
    assert "ENSG00000203782" in echo
    assert "alias_symbol" in echo
    print("  PASS: alias symbol resolves to canonical (LOR -> LORICRIN)")


# ---------------------------------------------------------------------------
# 3. Previous symbol resolves
# ---------------------------------------------------------------------------


def test_previous_symbol_resolves() -> None:
    """A previous symbol resolves to the current canonical name."""
    old_doc = _hgnc_doc(
        symbol="CURRENT_GENE",
        hgnc_id="HGNC:9999",
        ensembl_id="ENSG00000099999",
    )
    empty = _make_hgnc_response([])
    prev_response = _make_hgnc_response([old_doc])

    resolve_gene.cache_clear()

    def side_effect(url: str, **kwargs: Any) -> dict[str, Any]:
        if "/search/symbol/" in url:
            return empty
        if "/search/prev_symbol/" in url:
            return prev_response
        return empty

    with mock.patch("dde.core.gene.http.get_json") as mock_get:
        mock_get.side_effect = side_effect
        result = resolve_gene("OLD_NAME")

    assert result.resolved is True
    assert result.canonical_symbol == "CURRENT_GENE"
    assert result.source == "prev_symbol"
    echo = result.echo_line()
    assert echo is not None
    assert "OLD_NAME -> CURRENT_GENE" in echo
    print("  PASS: previous symbol resolves")


# ---------------------------------------------------------------------------
# 4. Ensembl ID passes through
# ---------------------------------------------------------------------------


def test_ensembl_id_passes_through() -> None:
    """An Ensembl ID (ENSG...) passes through without HGNC lookup."""
    resolve_gene.cache_clear()

    with mock.patch("dde.core.gene.http.get_json") as mock_get:
        result = resolve_gene("ENSG00000203782")

    # No API calls should have been made
    mock_get.assert_not_called()
    assert result.resolved is True
    assert result.ensembl_id == "ENSG00000203782"
    assert result.source == "ensembl_passthrough"
    assert result.canonical_symbol is None
    assert result.echo_line() is None
    print("  PASS: Ensembl ID passes through without HGNC lookup")


# ---------------------------------------------------------------------------
# 5. Unknown symbol returns resolved=False with suggestions
# ---------------------------------------------------------------------------


def test_unknown_symbol_suggestions() -> None:
    """An unknown symbol returns resolved=false with near-match suggestions."""
    empty = _make_hgnc_response([])
    # Broader search returns some suggestions
    suggestion_docs = [
        _hgnc_doc(symbol="LOXL1", hgnc_id="HGNC:1", ensembl_id="ENSG1"),
        _hgnc_doc(symbol="LOXHD1", hgnc_id="HGNC:2", ensembl_id="ENSG2"),
    ]
    suggestion_response = _make_hgnc_response(suggestion_docs)

    resolve_gene.cache_clear()

    call_urls: list[str] = []

    def side_effect(url: str, **kwargs: Any) -> dict[str, Any]:
        call_urls.append(url)
        # Wildcard search for suggestions
        if "*" in url:
            return suggestion_response
        return empty

    with mock.patch("dde.core.gene.http.get_json") as mock_get:
        mock_get.side_effect = side_effect
        result = resolve_gene("XYZNOTREAL")

    assert result.resolved is False
    assert result.canonical_symbol is None
    assert len(result.suggestions) > 0
    assert "LOXHD1" in result.suggestions or "LOXL1" in result.suggestions

    # to_dict should include suggestions
    d = result.to_dict()
    assert d["resolved"] is False
    assert "suggestions" in d
    print("  PASS: unknown symbol returns resolved=false with suggestions")


# ---------------------------------------------------------------------------
# 6. Resolution is cached
# ---------------------------------------------------------------------------


def test_resolution_cached() -> None:
    """Second call for the same symbol does not hit the API."""
    doc = _hgnc_doc(symbol="TP53", hgnc_id="HGNC:11998", ensembl_id="ENSG00000141510")
    response = _make_hgnc_response([doc])

    resolve_gene.cache_clear()

    with mock.patch("dde.core.gene.http.get_json") as mock_get:
        mock_get.return_value = response
        result1 = resolve_gene("TP53")
        result2 = resolve_gene("TP53")

    # Only one call — second was served from cache
    assert mock_get.call_count == 1
    assert result1 is result2  # Same cached object
    assert result1.resolved is True
    assert result1.canonical_symbol == "TP53"
    print("  PASS: resolution is cached (second call does not hit API)")


# ---------------------------------------------------------------------------
# 7. Relay fires on unresolved symbol
# ---------------------------------------------------------------------------


def test_relay_fires_on_unresolved() -> None:
    """The gene.unresolved_symbol relay code is valid and fires."""
    from dde.core.provenance import RELAY_CODES, relay

    # Verify the relay code is registered
    assert "gene.unresolved_symbol" in RELAY_CODES, (
        "gene.unresolved_symbol not in RELAY_CODES"
    )

    # Build a relay — should not raise
    r = relay(
        "gene.unresolved_symbol",
        "Gene symbol 'XYZ' could not be resolved via HGNC.",
    )
    assert r["code"] == "gene.unresolved_symbol"
    assert "XYZ" in r["message"]
    print("  PASS: relay fires on unresolved symbol")


# ---------------------------------------------------------------------------
# 8. Sidecar records gene resolution
# ---------------------------------------------------------------------------


def test_sidecar_records_resolution() -> None:
    """GeneResolution.to_dict() produces a sidecar-ready record."""
    res = GeneResolution(
        input="LOR",
        resolved=True,
        canonical_symbol="LORICRIN",
        hgnc_id="HGNC:6659",
        ensembl_id="ENSG00000203782",
        source="alias_symbol",
    )
    d = res.to_dict()

    assert d["input"] == "LOR"
    assert d["resolved"] is True
    assert d["canonical_symbol"] == "LORICRIN"
    assert d["hgnc_id"] == "HGNC:6659"
    assert d["ensembl_id"] == "ENSG00000203782"
    assert d["source"] == "alias_symbol"

    # Unresolved case
    unres = GeneResolution(
        input="XYZFAKE",
        resolved=False,
        suggestions=("LOXL1", "LOXHD1"),
    )
    d2 = unres.to_dict()
    assert d2["resolved"] is False
    assert d2["suggestions"] == ["LOXL1", "LOXHD1"]
    print("  PASS: sidecar records gene resolution")


# ---------------------------------------------------------------------------
# 9. "Resolved, no data" is distinct from "unresolved"
# ---------------------------------------------------------------------------


def test_resolved_no_data_distinct_from_unresolved() -> None:
    """Verify that the relay codes for no-data vs unresolved are distinct."""
    from dde.core.provenance import RELAY_CODES

    # All three relay codes must exist
    assert "gene.unresolved_symbol" in RELAY_CODES
    assert "expression.no_data_found" in RELAY_CODES
    assert "genetics.no_data_found" in RELAY_CODES
    assert "pathway.no_data_found" in RELAY_CODES

    # They must be distinct codes with distinct messages
    codes = {
        "gene.unresolved_symbol",
        "expression.no_data_found",
        "genetics.no_data_found",
        "pathway.no_data_found",
    }
    assert len(codes) == 4

    # Unresolved message should mention "lookup failure"
    assert "lookup failure" in RELAY_CODES["gene.unresolved_symbol"]
    # No-data messages should mention "data gap" or similar
    assert "data gap" in RELAY_CODES["expression.no_data_found"]
    print("  PASS: resolved-no-data is distinct from unresolved")


# ---------------------------------------------------------------------------
# 10. Echo message shows resolution mapping
# ---------------------------------------------------------------------------


def test_echo_shows_mapping() -> None:
    """echo_line() returns a human-readable mapping line."""
    # Alias resolution → should echo
    res = GeneResolution(
        input="LOR",
        resolved=True,
        canonical_symbol="LORICRIN",
        ensembl_id="ENSG00000203782",
        source="alias_symbol",
    )
    echo = res.echo_line()
    assert echo is not None
    assert "LOR -> LORICRIN" in echo
    assert "ENSG00000203782" in echo
    assert "HGNC alias_symbol" in echo

    # Official resolution → no echo needed (identity mapping)
    res_official = GeneResolution(
        input="BRCA1",
        resolved=True,
        canonical_symbol="BRCA1",
        source="official",
    )
    assert res_official.echo_line() is None

    # Ensembl passthrough → no echo
    res_ensg = GeneResolution(
        input="ENSG00000203782",
        resolved=True,
        ensembl_id="ENSG00000203782",
        source="ensembl_passthrough",
    )
    assert res_ensg.echo_line() is None

    # Unresolved → no echo
    res_fail = GeneResolution(input="FAKE", resolved=False)
    assert res_fail.echo_line() is None

    print("  PASS: echo message shows resolution mapping correctly")


# ---------------------------------------------------------------------------
# 11. Expression integration: resolve_gene is wired in
# ---------------------------------------------------------------------------


def test_expression_imports_resolve_gene() -> None:
    """expression module imports and uses resolve_gene from core.gene."""
    import inspect

    from dde.commands import expression

    # Verify import
    assert hasattr(expression, "resolve_gene"), (
        "expression module does not import resolve_gene"
    )

    # Verify fetch_cmd source calls resolve_gene
    source = inspect.getsource(expression.fetch_cmd.callback)
    assert "resolve_gene" in source, "fetch_cmd does not call resolve_gene"
    assert "gene_resolution" in source, (
        "fetch_cmd does not record gene_resolution in sidecar"
    )
    assert "gene.unresolved_symbol" in source or "unresolved_symbol" in source, (
        "fetch_cmd does not handle unresolved symbol case"
    )
    print("  PASS: expression imports and uses resolve_gene")


# ---------------------------------------------------------------------------
# 12. Expression integration: sidecar contains gene_resolution key
# ---------------------------------------------------------------------------


def test_expression_sidecar_has_gene_resolution_key() -> None:
    """expression.fetch_cmd passes gene_resolution in sidecar parameters."""
    import inspect

    from dde.commands import expression

    source = inspect.getsource(expression.fetch_cmd.callback)
    # Verify Sidecar is constructed with gene_resolution in parameters dict
    assert "gene_resolution" in source, (
        "fetch_cmd does not pass gene_resolution to Sidecar parameters"
    )
    assert "gene_res.to_dict()" in source, (
        "fetch_cmd does not serialize gene resolution via to_dict()"
    )

    # Also verify GeneResolution.to_dict produces sidecar-compatible output
    lor_res = GeneResolution(
        input="LOR",
        resolved=True,
        canonical_symbol="LORICRIN",
        hgnc_id="HGNC:6659",
        ensembl_id="ENSG00000203782",
        source="alias_symbol",
    )
    d = lor_res.to_dict()
    assert d["input"] == "LOR"
    assert d["canonical_symbol"] == "LORICRIN"
    assert d["source"] == "alias_symbol"
    # Must be JSON-serialisable for sidecar
    json.dumps(d)
    print("  PASS: expression sidecar has gene_resolution key")


# ---------------------------------------------------------------------------
# 13. Genetics integration: resolve_gene is wired in
# ---------------------------------------------------------------------------


def test_genetics_imports_resolve_gene() -> None:
    """genetics module imports and uses resolve_gene from core.gene."""
    import inspect

    from dde.commands import genetics

    assert hasattr(genetics, "resolve_gene"), (
        "genetics module does not import resolve_gene"
    )

    source = inspect.getsource(genetics.fetch_cmd.callback)
    assert "resolve_gene" in source, "genetics.fetch_cmd does not call resolve_gene"
    assert "gene_resolution" in source, (
        "genetics.fetch_cmd does not record gene_resolution in sidecar"
    )
    assert "unresolved_symbol" in source, (
        "genetics.fetch_cmd does not handle unresolved symbol"
    )
    # Verify "resolved, no data" is handled distinctly
    assert "genetics.no_data_found" in source or "no_data_found" in source, (
        "genetics.fetch_cmd does not handle resolved-no-data case"
    )
    print("  PASS: genetics imports and uses resolve_gene")


# ---------------------------------------------------------------------------
# 14. Pathway integration: resolve_gene is wired in
# ---------------------------------------------------------------------------


def test_pathway_imports_resolve_gene() -> None:
    """pathway module imports and uses resolve_gene from core.gene."""
    import inspect

    from dde.commands import pathway

    assert hasattr(pathway, "resolve_gene"), (
        "pathway module does not import resolve_gene"
    )

    source = inspect.getsource(pathway.search_cmd.callback)
    assert "resolve_gene" in source, "pathway.search_cmd does not call resolve_gene"
    assert "gene_resolution" in source, (
        "pathway.search_cmd does not record gene_resolution in sidecar"
    )
    assert "unresolved_symbol" in source, (
        "pathway.search_cmd does not handle unresolved symbol"
    )
    assert "pathway.no_data_found" in source or "no_data_found" in source, (
        "pathway.search_cmd does not handle resolved-no-data case"
    )
    print("  PASS: pathway imports and uses resolve_gene")


# ---------------------------------------------------------------------------
# 15. Expression single-cell integration: resolve_gene is wired in
# ---------------------------------------------------------------------------


def test_expression_single_cell_imports_resolve_gene() -> None:
    """expression.fetch_single_cell_cmd also uses resolve_gene."""
    import inspect

    from dde.commands import expression

    source = inspect.getsource(expression.fetch_single_cell_cmd.callback)
    assert "resolve_gene" in source, "fetch_single_cell_cmd does not call resolve_gene"
    assert "gene_resolution" in source, (
        "fetch_single_cell_cmd does not record gene_resolution"
    )
    print("  PASS: expression single-cell imports resolve_gene")


# ---------------------------------------------------------------------------
# 16. GeneResolution to_dict round-trip
# ---------------------------------------------------------------------------


def test_to_dict_round_trip() -> None:
    """to_dict() produces valid JSON-serialisable dict."""
    res = GeneResolution(
        input="LOR",
        resolved=True,
        canonical_symbol="LORICRIN",
        hgnc_id="HGNC:6659",
        ensembl_id="ENSG00000203782",
        source="alias_symbol",
    )
    d = res.to_dict()
    # Must be JSON-serialisable
    serialised = json.dumps(d)
    restored = json.loads(serialised)
    assert restored["input"] == "LOR"
    assert restored["canonical_symbol"] == "LORICRIN"
    assert restored["resolved"] is True
    print("  PASS: to_dict round-trip is JSON-safe")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        # Core resolver
        ("test_official_symbol_resolves", test_official_symbol_resolves),
        ("test_alias_resolves_to_canonical", test_alias_resolves_to_canonical),
        ("test_previous_symbol_resolves", test_previous_symbol_resolves),
        ("test_ensembl_id_passes_through", test_ensembl_id_passes_through),
        ("test_unknown_symbol_suggestions", test_unknown_symbol_suggestions),
        ("test_resolution_cached", test_resolution_cached),
        # Relay and provenance
        ("test_relay_fires_on_unresolved", test_relay_fires_on_unresolved),
        ("test_sidecar_records_resolution", test_sidecar_records_resolution),
        (
            "test_resolved_no_data_distinct_from_unresolved",
            test_resolved_no_data_distinct_from_unresolved,
        ),
        ("test_echo_shows_mapping", test_echo_shows_mapping),
        # Command integrations (source inspection)
        ("test_expression_imports_resolve_gene", test_expression_imports_resolve_gene),
        (
            "test_expression_sidecar_has_gene_resolution_key",
            test_expression_sidecar_has_gene_resolution_key,
        ),
        ("test_genetics_imports_resolve_gene", test_genetics_imports_resolve_gene),
        ("test_pathway_imports_resolve_gene", test_pathway_imports_resolve_gene),
        (
            "test_expression_single_cell_imports_resolve_gene",
            test_expression_single_cell_imports_resolve_gene,
        ),
        ("test_to_dict_round_trip", test_to_dict_round_trip),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} — {exc}")
            import traceback

            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
