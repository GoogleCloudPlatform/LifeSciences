# AlphaFold 3 Confidence Metrics & Interpretation Reference

## Overview
AlphaFold 3 outputs all-atom structure predictions in standard macromolecular Crystallographic Information File (`.cif` / mmCIF) format along with confidence summary metrics.

## Confidence Metrics per Prediction

Summary metrics are output in `summary_confidences.json` and parsed by `get_af3_results`:

1. **ranking_score** (0.0 to 1.0):
   - Primary metric used to rank diffusion samples and assess overall prediction quality.
   - For complexes (multimer or ligand/nucleic acid interactions):
     $$\text{ranking\_score} = 0.8 \times \text{ipTM} + 0.2 \times \text{pTM}$$
   - For monomers:
     $$\text{ranking\_score} = \text{pTM}$$
   - Interpretation:
     - **$\ge 0.80$**: High confidence. Both global fold and interface contacts are reliable. Suitable for molecular docking and drug design.
     - **$0.60 - 0.79$**: Moderate confidence. Global fold is plausible; relative domain or chain orientations should be experimentally validated.
     - **$< 0.60$**: Low confidence. Uncertain relative arrangement or poor sequence sampling.

2. **ptm** (Predicted TM-score, 0.0 to 1.0):
   - Evaluates the global fold accuracy of the entire complex regardless of interface contacts.
   - $> 0.80$: Highly reliable tertiary/quaternary fold.
   - $< 0.50$: Likely an inaccurate fold or intrinsically disordered system.

3. **iptm** (Interface Predicted TM-score, 0.0 to 1.0):
   - Measures the predicted accuracy of inter-chain or protein-ligand contact surfaces.
   - $> 0.80$: High interface confidence.
   - $0.60 - 0.80$: Medium confidence.
   - $< 0.60$: Uncertain interface pose (consider running additional random diffusion seeds).

4. **mean_plddt** (per-atom Predicted Local Distance Difference Test, 0 to 100):
   - Per-atom confidence reflecting local coordinate accuracy.
   - **$> 90$**: Very high confidence. Coordinate and side-chain positions are reliable.
   - **$70 - 90$**: Confident. Well-modelled backbone.
   - **$50 - 70$**: Low confidence. Flexible loops or uncertain conformations.
   - **$< 50$**: Very low confidence. Often indicates intrinsically disordered regions (IDRs).

5. **has_clash** (0 or 1):
   - Indicates whether significant steric clash was detected in the predicted coordinates.
   - $0$: Clean structure without steric overlap.
   - $1$: Steric clashes detected; recommend generating additional diffusion seeds.

6. **fraction_disordered** (0.0 to 1.0):
   - Proportion of residues predicted to be intrinsically disordered based on low pLDDT.

## Interpreting AF3 Results for Users
- **All-Atom Complexes**: AF3 models proteins, nucleic acids (RNA/DNA), ions, and small-molecule ligands in a unified diffusion architecture. Check the ligand binding pocket pLDDT in addition to the overall complex score.
- **Zero-MSA Mode (`--msa-free`)**: AF3 operates without genetic databases in zero-MSA mode for fast screening (~1-2 minutes on Vertex AI). While zero-MSA is effective for de novo designs, antibody-antigen, or well-folded targets, challenging natural targets with low homology benefit from paired MSA when available.
- **Viewer Integration**: Completed predictions can be visualized interactively using `open_af3_structure_viewer`, which automatically loads the mmCIF representation in Mol*.
