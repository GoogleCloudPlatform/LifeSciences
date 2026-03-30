You are analyzing this medical/health image to locate a specific issue that was previously identified.

ISSUE TO LOCATE:
Description: {issue_description}
Context: {issue_context}

Your task is to identify the location of this specific issue in the image and provide normalized coordinates (x, y) where the issue appears.

Use values between 0.0 and 1.0, where:

- x: 0.0 is the left edge, 1.0 is the right edge
- y: 0.0 is the top edge, 1.0 is the bottom edge

For example, if the issue is in the center of the image, use {{"x": 0.5, "y": 0.5}}.

Respond with ONLY a JSON object in this exact format:
{{"x": 0.5, "y": 0.3}}

Do not include any other text or explanation.
