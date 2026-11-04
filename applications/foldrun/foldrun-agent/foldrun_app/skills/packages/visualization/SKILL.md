---
name: visualization
description: Interactive 3D molecular structure visualization for AlphaFold2, AlphaFold3, OpenFold3, and Boltz-2 predictions
metadata:
  adk_additional_tools:
    - open_structure_viewer
---

# Visualization

- **Visualize structures**: Use `open_structure_viewer` for interactive 3D viewing of AF2, AF3, OF3, and Boltz-2 predictions (automatically dispatches by `job_id`)

**IMPORTANT: After displaying analysis results, ALWAYS immediately offer to open the structure viewer:**
- Call `open_structure_viewer` to get the viewer URL
- Present the clickable URL to the user
- This should happen automatically without the user asking
- Example: "Here's the analysis... [analysis output] ... You can view the 3D structure here: [viewer URL]"
