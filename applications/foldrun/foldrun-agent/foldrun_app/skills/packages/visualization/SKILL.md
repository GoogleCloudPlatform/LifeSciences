---
name: visualization
description: Interactive 3D molecular structure visualization for AlphaFold2, AlphaFold3, OpenFold3, and Boltz-2 predictions
metadata:
  adk_additional_tools:
    - open_structure_viewer
    - open_of3_structure_viewer
    - open_boltz2_structure_viewer
    - open_af3_structure_viewer
---

# Visualization

- **Visualize structures**: Use open_structure_viewer (AF2), open_af3_structure_viewer (AF3), open_of3_structure_viewer (OF3), or open_boltz2_structure_viewer (Boltz-2) for interactive 3D viewing

**IMPORTANT: After displaying analysis results, ALWAYS immediately offer to open the structure viewer:**
- Call the appropriate viewer tool to get the viewer URL
- Present the clickable URL to the user
- This should happen automatically without the user asking
- Example: "Here's the analysis... [analysis output] ... You can view the 3D structure here: [viewer URL]"
