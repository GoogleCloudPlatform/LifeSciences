---
name: visualization
description: Generates interactive 3D molecular structure viewer URLs for completed AlphaFold2, AlphaFold3, OpenFold3, Boltz-2, BoltzGen, RFD4-Proteina, and BindCraft predictions and binder designs. Use when the user asks to view, visualize, or inspect a 3D structure, or immediately after presenting analysis results.
metadata:
  adk_additional_tools:
    - open_structure_viewer
---

# Visualization

- **Open 3D Structure Viewer (`open_structure_viewer`)**: Generates an interactive 3D Mol* viewer URL for completed AF2, AF3, OF3, Boltz-2, BoltzGen, RFD4-Proteina, and BindCraft predictions and binder designs (automatically dispatches by `job_id`).
- **Proactive Viewer Link Rule**: Immediately after displaying completed analysis results, call `open_structure_viewer` and include the clickable 3D viewer URL in your response without waiting for the user to ask.
