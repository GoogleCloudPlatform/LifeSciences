# AlphaFold 3 Confidence Metrics & Interpretation Reference

## Overview
AlphaFold 3 outputs all-atom structure predictions in standard macromolecular Crystallographic Information File (`.cif` / mmCIF) format along with confidence summary metrics (`summary_confidences.json`), per-atom `plddt`, and the Predicted Aligned Error (`pae`) matrix.

## Confidence Metrics per Prediction

Summary metrics are output in `<job_name>_summary_confidences.json` and `analysis/summary.json`, and parsed by `get_af3_results`:

1. **ranking_score** ($-100.0$ to $1.5$, typically $0.0$ to $1.1$):
   - Primary composite metric used to rank diffusion samples and assess overall prediction quality.
   - Official AlphaFold 3 formula for multi-chain complexes (multimers, protein-ligand, protein-DNA/RNA):
     $$\text{ranking\_score} = 0.8 \times \text{ipTM} + 0.2 \times \text{pTM} + 0.5 \times \text{fraction\_disordered} - 100 \times \text{has\_clash}$$
   - For single-chain monomers (where `iptm` is `null`):
     $$\text{ranking\_score} = \text{pTM} + 0.5 \times \text{fraction\_disordered} - 100 \times \text{has\_clash}$$
   - **Why `ranking_score` can exceed `1.0`**: Because of the $+0.5 \times \text{fraction\_disordered}$ term (which prevents penalizing well-folded complexes that carry natural N/C-terminal disordered tails), a high-confidence complex with flexible tails naturally scores slightly above $1.0$ (for example, `af3_insulin_heterodimer` has $\text{pTM}=0.79$, $\text{ipTM}=0.79$, $\text{fraction\_disordered}=0.53 \implies \text{ranking\_score}=1.05$; `egfr_gefitinib_af3_msa` has $\text{pTM}=0.84$, $\text{ipTM}=0.96$, $\text{fraction\_disordered}=0.13 \implies \text{ranking\_score}=1.00$).
   - Interpretation:
     - **$\ge 0.80$**: High confidence. Both global fold and interface contacts are reliable. Suitable for molecular docking and structure-guided drug design.
     - **$0.60 - 0.79$**: Moderate confidence. Global fold is plausible; relative domain or chain orientations should be experimentally validated.
     - **$< 0.60$**: Low confidence. Uncertain relative arrangement or flexible multi-domain system.

2. **ptm** (Predicted TM-score, $0.0$ to $1.0$):
   - Evaluates the global fold accuracy of the entire structure.
   - **$> 0.80$**: Highly reliable tertiary/quaternary fold.
   - **$0.50 - 0.80$**: Confident domain folds, or multi-domain proteins with flexible inter-domain linkers (e.g., full-length 1,210-aa EGFR transmembrane receptor where individual ectodomain/kinase modules fold at $>85$ pLDDT while inter-domain linkers reduce global `pTM` to $0.52$).
   - **Short-Chain / RNA Aptamer Length-Scaling Caveat ($L < 50$ nt/aa)**:
     - The TM-score length-scaling factor $d_0(L)$ is conservative for short chains ($< 50$ nucleotides or residues). For example, on a 34-nt hammerhead ribozyme/aptamer (`af3_rna_aptamer`), `pTM` is $0.34$ even when `mean_plddt = 80.28` and `mean_pae = 4.72 Å` confirm a well-defined stem-loop 3D fold. For short RNA/DNA/peptides ($< 50$ tokens), rely primarily on **`mean_plddt` ($> 75$)** and **`mean_pae` ($< 5\text{ Å}$)** rather than `pTM` alone.

3. **iptm** (Interface Predicted TM-score, $0.0$ to $1.0$):
   - Measures the predicted accuracy of inter-chain or protein-ligand/DNA/RNA contact surfaces.
   - **$> 0.80$**: High interface confidence (e.g., `egfr_gefitinib_af3_msa` $\text{ipTM} = 0.96$, `af3_zinc_finger_dna` $\text{ipTM} = 0.92$).
   - **$0.60 - 0.80$**: Medium-to-good interface confidence (`af3_insulin_heterodimer` $\text{ipTM} = 0.79$).
   - **$< 0.60$**: Uncertain binding pose or cofactor orientation (consider running additional random `modelSeeds` or inspecting `chain_pair_pae_min`).

4. **mean_plddt** (per-atom / per-residue Predicted Local Distance Difference Test, $0$ to $100$):
   - Per-atom confidence reflecting local coordinate accuracy (stored in the mmCIF `B_iso_or_equiv` column and `plddt` array).
   - **$> 90$**: Very high confidence. Backbone, side-chain rotamers, and binding-pocket coordinates are highly reliable.
   - **$70 - 90$**: Confident. Well-modelled backbone and secondary structure.
   - **$50 - 70$**: Low confidence. Flexible surface loops or mobile catalytic motifs (e.g., truncated kinase domain without regulatory partner).
   - **$< 50$**: Very low confidence. Typically indicates intrinsically disordered regions (IDRs) or unstructured termini.

5. **PAE & Chain-Pair Matrices** (`mean_pae`, `chain_pair_iptm`, `chain_pair_pae_min`):
   - `mean_pae` (Å): Average Predicted Aligned Error across all token pairs. Values $< 5\text{ Å}$ indicate a compact, rigid fold (`af3_insulin_heterodimer` $2.42\text{ Å}$, `af3_ubiquitin_monomer` $2.80\text{ Å}$, `af3_zinc_finger_dna` $3.44\text{ Å}$).
   - `chain_pair_pae_min`: Minimum inter-chain PAE (Å) between each pair of chains/ligands. Values $< 2.5\text{ Å}$ confirm tight, specific coordination of a ligand, metal ion (`MG`, `ZN`), or nucleic acid strand within the binding pocket.

6. **has_clash** ($0.0$ or $1.0$) & **fraction_disordered** ($0.0$ to $1.0$):
   - `has_clash = 0.0`: Clean all-atom geometry without severe steric overlap.
   - `fraction_disordered`: Proportion of polymer tokens predicted to be intrinsically disordered.

## Empirical Production Benchmarks on Vertex AI (`a3-highgpu-1g` H100 80GB + 630 GB NVMe)

| Modality | Benchmark Job | Polymer / Ligand Entities | Full-MSA Runtime | Mean pLDDT | pTM / ipTM | Mean PAE | Ranking Score |
|---|---|---|---|---|---|---|---|
| **Protein Monomer** | `af3_ubiquitin_monomer` | 76 aa protein (`A`) | **3.8 min** (`228s`) *(58s `--msa-free`)* | `91.92` *(vs `63.68` `--msa-free`)* | `0.85` / — | `2.80 Å` | `0.85` |
| **Protein Multimer** | `af3_insulin_heterodimer` | 21 aa (`A`) + 30 aa (`B`) | **5.8 min** (`350s`) | `90.42` | `0.79` / `0.79` | `2.42 Å` | `1.05` |
| **Protein + CCD Ligand + Metal Ion** | `af3_kinase_atp_mg` | 120 aa (`A`) + `ATP` (`B`) + `MG` (`C`) | **2.9 min** (`173s`) | `55.91` | `0.50` / `0.55` | `11.06 Å` | `0.57` |
| **Protein + SMILES Inhibitor** | `egfr_gefitinib_af3_msa` | 312 aa EGFR kinase (`A`) + Gefitinib SMILES (`B`) | **6.1 min** (`365s`) | `84.54` | `0.84` / `0.96` | `9.81 Å` | `1.00` |
| **Protein + dsDNA Duplex** | `af3_zinc_finger_dna` | 90 aa Zif268 (`A`) + 9 bp dsDNA (`B`, `C`) | **4.2 min** (`255s`) | `95.20` | `0.87` / `0.92` | `3.44 Å` | `0.96` |
| **RNA Monomer** | `af3_rna_aptamer` | 34 nt hammerhead ribozyme (`A`) | **3.1 min** (`185s`) | `80.28` | `0.34` / — | `4.72 Å` | `0.34` |
| **Large Multi-Domain (>1,100 aa)** | `jak2_af3_msa` / `egfr_af3_msa` | 1,132 aa JAK2 / 1,210 aa full-length EGFR | **15.6 – 19.4 min** (`934s – 1161s`) | `81.55` / `72.49` | `0.79` / `0.52` | `12.05 Å` / `22.30 Å` | `0.82` / `0.64` |
