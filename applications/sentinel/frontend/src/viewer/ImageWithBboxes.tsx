/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import { Box, Typography } from '@mui/material';
import type { Finding, Severity } from './types';

const SEVERITY_COLOR: Record<Severity, string> = {
  critical: '#f28b82',
  high: '#fcad70',
  medium: '#fdd663',
  low: '#8ab4f8',
  informational: '#9aa0a6',
};

interface Props {
  imageUrl: string;
  findings: Finding[];
  hoveredFindingId: string | null;
  onHoverFinding: (id: string | null) => void;
  onSelectFinding: (id: string) => void;
}

function isValidBbox(bbox: number[] | null | undefined): bbox is number[] {
  if (!bbox || bbox.length !== 4) return false;
  const [x1, y1, x2, y2] = bbox;
  return (
    [x1, y1, x2, y2].every((n) => typeof n === 'number' && n >= 0 && n <= 1) &&
    x2 > x1 &&
    y2 > y1
  );
}

export default function ImageWithBboxes({
  imageUrl,
  findings,
  hoveredFindingId,
  onHoverFinding,
  onSelectFinding,
}: Props) {
  const findingsWithBbox = findings.filter((f) =>
    isValidBbox(f.location?.bbox),
  );

  return (
    <Box sx={{ position: 'relative', width: '100%' }}>
      <Box
        component="img"
        src={imageUrl}
        alt="Submission"
        sx={{
          display: 'block',
          width: '100%',
          height: 'auto',
          borderRadius: 1,
        }}
      />

      {findingsWithBbox.map((f) => {
        const bbox = f.location!.bbox!;
        const [x1, y1, x2, y2] = bbox;
        const isHovered = hoveredFindingId === f.finding_id;
        const isDimmed = hoveredFindingId !== null && !isHovered;
        const color = SEVERITY_COLOR[f.severity];

        return (
          <Box
            key={f.finding_id}
            onMouseEnter={() => onHoverFinding(f.finding_id)}
            onMouseLeave={() => onHoverFinding(null)}
            onClick={() => onSelectFinding(f.finding_id)}
            sx={{
              position: 'absolute',
              left: `${x1 * 100}%`,
              top: `${y1 * 100}%`,
              width: `${(x2 - x1) * 100}%`,
              height: `${(y2 - y1) * 100}%`,
              border: `${isHovered ? 3 : 2}px solid ${color}`,
              boxShadow: isHovered ? `0 0 0 4px ${color}33` : 'none',
              backgroundColor: isHovered ? `${color}20` : 'transparent',
              opacity: isDimmed ? 0.25 : 1,
              transition: 'all 120ms ease',
              cursor: 'pointer',
              borderRadius: 0.5,
              boxSizing: 'border-box',
            }}
          >
            <Typography
              variant="caption"
              sx={{
                position: 'absolute',
                top: -22,
                left: -2,
                px: 0.75,
                py: 0.25,
                bgcolor: color,
                color: '#202124',
                fontFamily: 'monospace',
                fontSize: '0.65rem',
                fontWeight: 700,
                borderRadius: 0.5,
                whiteSpace: 'nowrap',
                pointerEvents: 'none',
              }}
            >
              {f.finding_id}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}
