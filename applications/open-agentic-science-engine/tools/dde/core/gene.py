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

"""HGNC gene symbol alias resolution.

Resolves gene symbols to their canonical HGNC form, handling official
symbols, previous symbols, and alias symbols.  This prevents lookup
failures from being misread as biological findings (#147): an unresolved
symbol is a lookup failure, not evidence of gene absence.

Cross-cutting principle from #84, #110, #147, #148: "Identifier not
resolved" must NEVER be read as "no biological data".  Distinguish
lookup failure from negative evidence.

Resolution order:
  1. Ensembl ID passthrough (``ENSG...``) — no HGNC lookup needed
  2. Official HGNC symbol
  3. Previous symbols (historical names)
  4. Alias symbols (synonyms)

Results are ``@functools.lru_cache``-d for the process lifetime to
avoid repeated API calls for the same symbol within a run.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any

from . import http
from .qps import qps_for_host

HGNC_BASE = "https://rest.genenames.org"
ENSG_RE = re.compile(r"^ENSG\d{11}$")


@dataclass(frozen=True)
class GeneResolution:
    """Result of resolving a gene symbol via HGNC."""

    input: str
    resolved: bool
    canonical_symbol: str | None = None
    hgnc_id: str | None = None
    ensembl_id: str | None = None
    source: str | None = None
    suggestions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise for sidecar recording."""
        d: dict[str, Any] = {"input": self.input, "resolved": self.resolved}
        if self.resolved:
            d["canonical_symbol"] = self.canonical_symbol
            d["hgnc_id"] = self.hgnc_id
            d["ensembl_id"] = self.ensembl_id
            d["source"] = self.source
        else:
            d["suggestions"] = list(self.suggestions)
        return d

    def echo_line(self) -> str | None:
        """Human-readable mapping line, or ``None`` if identity/passthrough."""
        if not self.resolved or self.source == "ensembl_passthrough":
            return None
        if self.input.upper() == (self.canonical_symbol or "").upper():
            return None
        ensembl_part = f" ({self.ensembl_id})" if self.ensembl_id else ""
        return (
            f"{self.input} -> {self.canonical_symbol}{ensembl_part} "
            f"[via HGNC {self.source}]"
        )


def _hgnc_search(search_field: str, term: str) -> list[dict[str, Any]]:
    """Query HGNC REST API: ``/search/<field>/<term>``."""
    url = f"{HGNC_BASE}/search/{search_field}/{term}"
    data = http.get_json(
        url,
        qps=qps_for_host("rest.genenames.org"),
        timeout=30.0,
        headers={"Accept": "application/json"},
    )
    response = data.get("response", {})
    docs = response.get("docs", [])
    return [d for d in docs if isinstance(d, dict)]


@functools.lru_cache(maxsize=512)
def resolve_gene(symbol: str) -> GeneResolution:
    """Resolve a gene symbol or Ensembl ID to its canonical HGNC form.

    Returns a :class:`GeneResolution` — always, never raises on its own.
    The caller decides whether an unresolved result is fatal.

    Results are cached for the process lifetime (``@lru_cache``).
    """
    symbol = symbol.strip()

    # ── Ensembl IDs pass through without HGNC lookup ──────────────────
    if ENSG_RE.match(symbol.upper()):
        return GeneResolution(
            input=symbol,
            resolved=True,
            ensembl_id=symbol.upper(),
            source="ensembl_passthrough",
        )

    # ── 1. Official symbol ────────────────────────────────────────────
    docs = _hgnc_search("symbol", symbol)
    exact = [d for d in docs if (d.get("symbol") or "").upper() == symbol.upper()]
    if exact:
        doc = exact[0]
        return GeneResolution(
            input=symbol,
            resolved=True,
            canonical_symbol=doc.get("symbol"),
            hgnc_id=doc.get("hgnc_id"),
            ensembl_id=doc.get("ensembl_gene_id"),
            source="official",
        )

    # ── 2. Previous symbols ───────────────────────────────────────────
    docs = _hgnc_search("prev_symbol", symbol)
    if docs:
        doc = docs[0]
        return GeneResolution(
            input=symbol,
            resolved=True,
            canonical_symbol=doc.get("symbol"),
            hgnc_id=doc.get("hgnc_id"),
            ensembl_id=doc.get("ensembl_gene_id"),
            source="prev_symbol",
        )

    # ── 3. Alias symbols ─────────────────────────────────────────────
    docs = _hgnc_search("alias_symbol", symbol)
    if docs:
        doc = docs[0]
        return GeneResolution(
            input=symbol,
            resolved=True,
            canonical_symbol=doc.get("symbol"),
            hgnc_id=doc.get("hgnc_id"),
            ensembl_id=doc.get("ensembl_gene_id"),
            source="alias_symbol",
        )

    # ── No match: gather near-miss suggestions ───────────────────────
    suggestions: list[str] = []
    try:
        broad = _hgnc_search("symbol", f"{symbol}*")
        suggestions = sorted({d.get("symbol", "?") for d in broad if d.get("symbol")})[
            :5
        ]
    except Exception:
        pass

    return GeneResolution(
        input=symbol,
        resolved=False,
        suggestions=tuple(suggestions),
    )
