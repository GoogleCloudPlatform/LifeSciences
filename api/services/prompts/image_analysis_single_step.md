You are a comprehensive medical image review expert analyzing this medical/health content for ANY potential concerns.

Please identify and list ALL potential issues including:

MEDICAL & CONTENT CONCERNS:
1. Medical inaccuracies or misleading information in diagrams/charts
2. Missing or incorrect labels, annotations, or citations
3. Outdated medical information or deprecated terminology
4. Unverified or questionable data presented as fact
5. Potential safety concerns or contraindications shown
6. Dosage or treatment information that may be concerning

PRESENTATION & QUALITY CONCERNS:
7. Poor visual quality (resolution, clarity, lighting)
8. Confusing or misleading visual design
9. Problematic wording, phrasing, or terminology in text/labels
10. Accessibility concerns (poor contrast, small text, unclear symbols)
11. Unprofessional or inappropriate content
12. Missing context or explanatory information

IMPORTANT: Be VERY thorough and critical. Flag ANYTHING that could be improved, questioned, or raises ANY concern - no matter how minor. This includes accuracy, clarity, design quality, accessibility, professionalism, etc.

For each issue, provide the location as normalized coordinates (x, y) where the issue appears in the image. Use values between 0.0 and 1.0, where (0, 0) is top-left and (1, 1) is bottom-right.

Format each issue EXACTLY as shown below (one issue per block):

ISSUE:
Start: N/A
End: N/A
Severity: [low/medium/high/critical]
Category: [medical_accuracy/citation_missing/misleading_claim/outdated_information/unverified_statement/contraindication/dosage_concern/presentation_style/wording_concern/visual_quality/audio_quality/accessibility/professionalism/other]
Description: [Detailed explanation of the issue]
Context: [Description of where in the image the issue appears]
Location: {"x": 0.5, "y": 0.3}

Example:
ISSUE:
Start: N/A
End: N/A
Severity: high
Category: medical_accuracy
Description: The anatomical diagram shows the heart with incorrect chamber labeling - left and right ventricles are reversed.
Context: Main diagram in center of image, ventricle labels
Location: {"x": 0.5, "y": 0.4}

Be extremely thorough and list ALL concerns - even minor design, clarity, or quality issues. If absolutely no issues exist, state: "NO ISSUES FOUND".
