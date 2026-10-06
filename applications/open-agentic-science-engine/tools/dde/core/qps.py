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

"""Central per-host QPS registry.

One QPS value per hostname, period.  This is the single source of truth
that prevents the bug described in #70: different modules declaring
different QPS for the same host, with the last caller's value silently
winning when pacing state is shared on disk (keyed by host).

For every host that appears below, the value is the **strictest**
(lowest) QPS declared across all modules that contact that host.

When a new host is added to the codebase, add an entry here.
When only one module uses a host, its declared QPS is still entered here
so the single-source-of-truth invariant holds for every host.
"""

from __future__ import annotations

from .ncbi import EUTILS_QPS as _EUTILS_QPS

# ── Host-to-QPS table ──────────────────────────────────────────────────
#
# Key   = hostname exactly as ``urlparse(url).netloc`` returns it
# Value = requests per second (strictest declared across all modules)
#
# When two modules declared different QPS for the same host, the lower
# (stricter) value is kept.  Comments note the original per-module
# values where they differed.

HOST_QPS: dict[str, float] = {
    # ── NCBI ──────────────────────────────────────────────────────────
    "pubchem.ncbi.nlm.nih.gov": 2.0,  # compreg=2, assay=4, pubchem=5, similar=5
    "eutils.ncbi.nlm.nih.gov": _EUTILS_QPS,  # ncbi.py dynamic (3 or 10 w/ key); gwas/clinvar was 3
    # ── EBI ───────────────────────────────────────────────────────────
    "www.ebi.ac.uk": 1.0,  # assay/chembl=1, compreg/chembl=2, gwas/catalog=2,
    # litref/epmc=2, pubchem/chembl=5, similar/chembl=5,
    # pathway/quickgo=5
    "alphafold.ebi.ac.uk": 1.0,  # alphafold=1
    # ── UniProt ───────────────────────────────────────────────────────
    "rest.uniprot.org": 3.0,  # alphafold=3, homology=3, pathway=5
    # ── PDB / RCSB ───────────────────────────────────────────────────
    "search.rcsb.org": 1.0,  # homology=1
    "data.rcsb.org": 1.0,  # homology=1
    "files.rcsb.org": 1.0,  # homology=1
    # ── Clinical / Regulatory ────────────────────────────────────────
    "clinicaltrials.gov": 2.0,  # litref=2, trials=3
    "api.fda.gov": 4.0,  # faers=4
    "api.platform.opentargets.org": 5.0,  # gwas=5
    # ── Expression / Omics ───────────────────────────────────────────
    "www.proteinatlas.org": 1.0,  # expression/hpa=1
    "gtexportal.org": 1.0,  # gtex=1
    "api.cellxgene.cziscience.com": 5.0,  # cellxgene=5
    "www.cbioportal.org": 5.0,  # cbioportal=5
    "api.brain-map.org": 5.0,  # allen=5
    # ── Genetics ─────────────────────────────────────────────────────
    "gnomad.broadinstitute.org": 0.35,  # genetics=0.35
    # ── Phenotype / Model Organisms ──────────────────────────────────
    "www.informatics.jax.org": 2.0,  # phenotype/mgi=2
    "ontology.jax.org": 5.0,  # phenotype/hpo=5
    "www.alliancegenome.org": 5.0,  # phenotype/agr=5
    # ── Gene nomenclature ──────────────────────────────────────────
    "rest.genenames.org": 5.0,  # gene/hgnc=5
    # ── Protein / Pathway ────────────────────────────────────────────
    "string-db.org": 1.0,  # ppi=1
    "reactome.org": 5.0,  # pathway=5
    # ── Single-cell ──────────────────────────────────────────────────
    "dice-database.org": 2.0,  # dice=2
    "immunesinglecell.com": 3.0,  # disco=3
    "www.spatialomics.org": 2.0,  # spatialdb=2
    # ── Citation verification ───────────────────────────────────────
    "api.crossref.org": 2.0,  # cite/crossref=2
    # ── Preprint servers ────────────────────────────────────────────
    "export.arxiv.org": 0.333,  # preprint/arxiv; politeness minimum
    "api.biorxiv.org": 3.0,  # preprint/biorxiv; design spec §4.1.2
    # ── Other ────────────────────────────────────────────────────────
    "www.inbirg.com": 2.0,  # disignatlas=2
    "patents.google.com": 0.5,  # patent=0.5
    "askcos.mit.edu": 1.0,  # retro=1
}

# Default for hosts not in the table — conservative.
_DEFAULT_QPS: float = 2.0


def qps_for_host(host: str) -> float:
    """Return the QPS limit for *host*, defaulting to a conservative 2.0."""
    return HOST_QPS.get(host, _DEFAULT_QPS)
