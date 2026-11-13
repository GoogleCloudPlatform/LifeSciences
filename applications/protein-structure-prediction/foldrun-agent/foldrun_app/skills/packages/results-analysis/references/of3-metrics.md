# OpenFold3 Confidence Metrics & Plots Reference

## Confidence Files per Sample
Two confidence files are produced per sample:

1. `*_confidences_aggregated.json` — summary scores:
   - **sample_ranking_score** (0-1): Overall quality. Use this to rank predictions. >0.7 is good.
   - **ptm** (0-1): Predicted TM-score. Global fold accuracy. >0.8 = high confidence fold.
   - **iptm** (0-1): Interface pTM. Complex interface quality. >0.7 = reliable interface.
   - **avg_plddt** (0-100): Average per-residue confidence. >80 = good, >90 = very high.
   - **gpde**: Global predicted distance error. Lower is better.
   - **has_clash** (0 or 1): Steric clash detected. 0 = clean structure.
   - **disorder** (0 or 1): Disorder prediction.
   - **chain_ptm**: Per-chain pTM dict (e.g., `{"A": 0.87, "B": 0.46}`) — identifies which chains are well-predicted.
   - **chain_pair_iptm**: Per-chain-pair interface dict (e.g., `{"(A, B)": 0.72}`) — the diagonal contains per-chain pTM scores.
   - **bespoke_iptm**: Per-chain-pair interface scores (same pairs as chain_pair_iptm).

2. `*_confidences.json` — per-residue/per-atom arrays (for plots):
   - **plddt**: Per-token pLDDT scores (array, 0-100). Plot as line chart per chain.
   - **pde**: Predicted distance error matrix (NxN). Plot as heatmap.

## OF3 Analysis Plots (available after analysis job runs)
- **ipTM matrix heatmap**: Chain×chain interface quality. Diagonal = per-chain pTM. Off-diagonal = pairwise ipTM. Helps identify which interfaces are confident.
- **Per-residue pLDDT plot**: Line chart per chain showing confidence along the sequence. Dips indicate loops, disorder, or uncertain regions.
- **PAE heatmap**: Residue×residue predicted aligned error. Low values (blue) = confident relative positions. High values (red) = uncertain. Critical for assessing domain arrangements.
- **Contact probability heatmap**: Predicted inter-residue contacts. Useful for identifying binding interfaces.

## Interpreting OF3 Results for Users
- Compare **sample_ranking_score** across seeds/samples to find the best prediction
- The **ipTM matrix diagonal** contains per-chain pTM — quick way to see which chains folded well
- If iptm is low but ptm is high → individual chains fold well but the interface is uncertain
- If has_clash = 1 → suggest trying more seeds or checking the input for steric issues
- Low pLDDT in a region → may be intrinsically disordered (real biology) or poorly sampled (try more seeds)
- For drug binding predictions → focus on chain_pair_iptm between protein and ligand chains
- **Per-chain pLDDT breakdown**: After analysis, results include per-chain mean pLDDT (e.g., protein chain A: 80.8, ligand chain B (ATP): 63.8). Low ligand pLDDT (<60) means the binding pose is uncertain — suggest more seeds
