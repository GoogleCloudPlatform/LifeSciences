---
name: visualization
description: Interactive 3D molecular structure visualization for AlphaFold2, AlphaFold3, OpenFold3, Boltz-2, BoltzGen, RFD4-Proteina, and BindCraft
metadata:
  adk_additional_tools:
    - open_structure_viewer
    - open_of3_structure_viewer
    - open_boltz2_structure_viewer
    - open_boltzgen_structure_viewer
    - open_rfd4_proteina_structure_viewer
    - open_bindcraft_structure_viewer
    - open_af3_structure_viewer
---

# Visualization

- **Visualize structures**: Use open_structure_viewer (AF2), open_af3_structure_viewer (AF3), open_of3_structure_viewer (OF3), open_boltz2_structure_viewer (Boltz-2), open_boltzgen_structure_viewer (BoltzGen), open_rfd4_proteina_structure_viewer (RFD4-Proteina), or open_bindcraft_structure_viewer (BindCraft) for interactive 3D viewing

**IMPORTANT: After displaying analysis results, ALWAYS immediately offer to open the structure viewer:**
- Call the appropriate viewer tool to get the viewer URL
- Present the clickable URL to the user
- This should happen automatically without the user asking
- Example: "Here's the analysis... [analysis output] ... You can view the 3D structure here: [viewer URL]"
