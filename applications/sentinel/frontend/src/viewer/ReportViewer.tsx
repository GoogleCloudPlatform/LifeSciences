/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  AppBar,
  Box,
  Button,
  Chip,
  Container,
  Divider,
  Paper,
  Stack,
  Switch,
  TextField,
  Toolbar,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import ImageWithBboxes from './ImageWithBboxes';
import type {
  Finding,
  FinalReport,
  ReviewLens,
  Severity,
} from './types';

const SEVERITY_ORDER: Severity[] = [
  'critical',
  'high',
  'medium',
  'low',
  'informational',
];

const LENS_ORDER: ReviewLens[] = [
  'medical',
  'legal',
  'regulatory',
  'editorial',
  'custom',
];

const SEVERITY_COLOR: Record<Severity, string> = {
  critical: '#f28b82', // Google Red 300
  high: '#fcad70',
  medium: '#fdd663',
  low: '#8ab4f8',
  informational: '#9aa0a6',
};

const LENS_COLOR: Record<ReviewLens, string> = {
  medical: '#81c995', // Google Green 300
  legal: '#8ab4f8',
  regulatory: '#c58af9',
  editorial: '#78d9ec',
  custom: '#f8c473', // Google Orange 200 — visually distinct from the four MLR lenses
};

const LENS_LABEL: Record<ReviewLens, string> = {
  medical: 'Medical',
  legal: 'Legal',
  regulatory: 'Regulatory',
  editorial: 'Editorial',
  custom: 'Custom rules',
};

function prettyCategory(c: string): string {
  return c
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function SeverityChip({ severity }: { severity: Severity }) {
  return (
    <Chip
      label={severity.toUpperCase()}
      size="small"
      sx={{
        bgcolor: 'transparent',
        color: SEVERITY_COLOR[severity],
        border: `1px solid ${SEVERITY_COLOR[severity]}`,
        fontWeight: 600,
        fontSize: '0.7rem',
      }}
    />
  );
}

function LensChip({ lens }: { lens: ReviewLens }) {
  return (
    <Chip
      label={LENS_LABEL[lens]}
      size="small"
      sx={{
        bgcolor: `${LENS_COLOR[lens]}22`,
        color: LENS_COLOR[lens],
        border: `1px solid ${LENS_COLOR[lens]}55`,
        fontSize: '0.7rem',
      }}
    />
  );
}

interface FindingCardProps {
  finding: Finding;
  isHovered: boolean;
  onHover: (id: string | null) => void;
  expanded: boolean;
  onToggle: (id: string) => void;
  cardRef?: (el: HTMLDivElement | null) => void;
}

function FindingCard({
  finding,
  isHovered,
  onHover,
  expanded,
  onToggle,
  cardRef,
}: FindingCardProps) {
  return (
    <Accordion
      ref={cardRef}
      disableGutters
      expanded={expanded}
      onChange={() => onToggle(finding.finding_id)}
      onMouseEnter={() => onHover(finding.finding_id)}
      onMouseLeave={() => onHover(null)}
      sx={{
        bgcolor: isHovered ? '#3a3b3f' : '#2d2e31',
        borderLeft: `3px solid ${SEVERITY_COLOR[finding.severity]}`,
        boxShadow: isHovered
          ? `0 0 0 2px ${SEVERITY_COLOR[finding.severity]}55`
          : 'none',
        transition: 'all 120ms ease',
        '&:before': { display: 'none' },
        mb: 1,
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Stack
          direction="row"
          spacing={1.5}
          sx={{ width: '100%', alignItems: 'center', flexWrap: 'wrap' }}
        >
          <SeverityChip severity={finding.severity} />
          <LensChip lens={finding.review_lens} />
          <Typography variant="body2" sx={{ color: 'text.secondary' }}>
            {prettyCategory(finding.category)}
          </Typography>
          <Typography
            variant="body2"
            sx={{ color: 'text.secondary', fontFamily: 'monospace' }}
          >
            {finding.finding_id}
          </Typography>
          <Box sx={{ flexGrow: 1 }} />
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            conf {Math.round(finding.confidence * 100)}% ·{' '}
            {finding.evidence_depth}
          </Typography>
        </Stack>
      </AccordionSummary>
      <AccordionDetails>
        <Stack spacing={2}>
          <Typography variant="body1" sx={{ fontWeight: 500 }}>
            {finding.observation}
          </Typography>

          <Box>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>
              MLR Principle
            </Typography>
            <Typography variant="body2" sx={{ fontStyle: 'italic' }}>
              {finding.mlr_principle}
            </Typography>
          </Box>

          {finding.quoted_content && (
            <Paper
              elevation={0}
              sx={{
                p: 2,
                bgcolor: '#1f2023',
                borderLeft: '3px solid #5f6368',
              }}
            >
              <Typography
                variant="body2"
                sx={{ fontStyle: 'italic', color: 'text.secondary' }}
              >
                “{finding.quoted_content}”
              </Typography>
            </Paper>
          )}

          <Box>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>
              Discussion
            </Typography>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
              {finding.discussion}
            </Typography>
          </Box>

          {finding.suggested_questions &&
            finding.suggested_questions.length > 0 && (
              <Box>
                <Typography variant="overline" sx={{ color: 'text.secondary' }}>
                  Questions to raise
                </Typography>
                <Box component="ul" sx={{ m: 0, pl: 3 }}>
                  {finding.suggested_questions.map((q, i) => (
                    <li key={i}>
                      <Typography variant="body2">{q}</Typography>
                    </li>
                  ))}
                </Box>
              </Box>
            )}

          {finding.suggested_actions &&
            finding.suggested_actions.length > 0 && (
              <Box>
                <Typography variant="overline" sx={{ color: 'text.secondary' }}>
                  Possible directions
                </Typography>
                <Box component="ul" sx={{ m: 0, pl: 3 }}>
                  {finding.suggested_actions.map((a, i) => (
                    <li key={i}>
                      <Typography variant="body2">{a}</Typography>
                    </li>
                  ))}
                </Box>
              </Box>
            )}

          {(finding.location || (finding.related_item_ids?.length ?? 0) > 0) && (
            <Stack
              direction="row"
              spacing={2}
              sx={{
                pt: 1,
                borderTop: '1px solid #3c4043',
                flexWrap: 'wrap',
              }}
            >
              {finding.location?.section && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  <strong>Location:</strong> {finding.location.section}
                  {finding.location.page != null &&
                    ` · page ${finding.location.page}`}
                </Typography>
              )}
              {finding.location?.bbox && (
                <Typography
                  variant="caption"
                  sx={{ color: 'text.secondary', fontFamily: 'monospace' }}
                >
                  bbox [
                  {finding.location.bbox.map((n) => n.toFixed(2)).join(', ')}]
                </Typography>
              )}
              {finding.related_item_ids &&
                finding.related_item_ids.length > 0 && (
                  <Typography
                    variant="caption"
                    sx={{ color: 'text.secondary' }}
                  >
                    <strong>Items:</strong>{' '}
                    {finding.related_item_ids.join(', ')}
                  </Typography>
                )}
            </Stack>
          )}
        </Stack>
      </AccordionDetails>
    </Accordion>
  );
}

function CountsStrip({
  byLens,
  bySeverity,
}: {
  byLens?: Record<string, number>;
  bySeverity?: Record<string, number>;
}) {
  const lensEntries = LENS_ORDER.filter(
    (l) => byLens && byLens[l] !== undefined,
  );
  const sevEntries = SEVERITY_ORDER.filter(
    (s) => bySeverity && bySeverity[s] !== undefined,
  );

  if (!lensEntries.length && !sevEntries.length) return null;

  return (
    <Stack direction="row" spacing={4} sx={{ mt: 2, flexWrap: 'wrap' }}>
      {lensEntries.length > 0 && (
        <Box>
          <Typography variant="overline" sx={{ color: 'text.secondary' }}>
            By lens
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 0.5, flexWrap: 'wrap' }}>
            {lensEntries.map((l) => (
              <Chip
                key={l}
                size="small"
                label={`${LENS_LABEL[l]} · ${byLens![l]}`}
                sx={{
                  bgcolor: `${LENS_COLOR[l]}22`,
                  color: LENS_COLOR[l],
                  border: `1px solid ${LENS_COLOR[l]}55`,
                }}
              />
            ))}
          </Stack>
        </Box>
      )}
      {sevEntries.length > 0 && (
        <Box>
          <Typography variant="overline" sx={{ color: 'text.secondary' }}>
            By severity
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 0.5, flexWrap: 'wrap' }}>
            {sevEntries.map((s) => (
              <Chip
                key={s}
                size="small"
                label={`${s} · ${bySeverity![s]}`}
                sx={{
                  bgcolor: 'transparent',
                  color: SEVERITY_COLOR[s],
                  border: `1px solid ${SEVERITY_COLOR[s]}`,
                }}
              />
            ))}
          </Stack>
        </Box>
      )}
    </Stack>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Box sx={{ mt: 4 }}>
      <Typography variant="h6" sx={{ mb: 1.5 }}>
        {title}
      </Typography>
      {children}
    </Box>
  );
}

interface ReportDisplayProps {
  report: FinalReport;
  hoveredFindingId: string | null;
  onHoverFinding: (id: string | null) => void;
  expandedFindingIds: Set<string>;
  onToggleFinding: (id: string) => void;
  registerCardRef: (id: string, el: HTMLDivElement | null) => void;
}

function ReportDisplay({
  report,
  hoveredFindingId,
  onHoverFinding,
  expandedFindingIds,
  onToggleFinding,
  registerCardRef,
}: ReportDisplayProps) {
  const findingsByLens = useMemo(() => {
    const grouped: Record<ReviewLens, Finding[]> = {
      medical: [],
      legal: [],
      regulatory: [],
      editorial: [],
      custom: [],
    };
    for (const f of report.findings) {
      grouped[f.review_lens]?.push(f);
    }
    for (const lens of LENS_ORDER) {
      grouped[lens].sort(
        (a, b) =>
          SEVERITY_ORDER.indexOf(a.severity) -
          SEVERITY_ORDER.indexOf(b.severity),
      );
    }
    return grouped;
  }, [report.findings]);

  return (
    <Paper elevation={1} sx={{ p: { xs: 3, md: 5 }, mt: 3 }}>
      <Stack
        direction="row"
        spacing={2}
        sx={{ mb: 1, alignItems: 'center', flexWrap: 'wrap' }}
      >
        <Chip
          label={report.intended_audience.split('_').join(' ')}
          size="small"
          variant="outlined"
        />
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          Sentinel MLR review · {report.findings.length} findings
        </Typography>
      </Stack>

      <Typography variant="h4" sx={{ fontWeight: 500 }}>
        Reviewer orientation
      </Typography>

      <CountsStrip
        byLens={report.counts_by_lens}
        bySeverity={report.counts_by_severity}
      />

      <Section title="Executive summary">
        <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>
          {report.executive_summary}
        </Typography>
      </Section>

      <Section title="What this piece is">
        <Typography variant="body1" sx={{ mb: 1 }}>
          {report.content_summary}
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          <strong>Promotional intent.</strong> {report.promotional_intent}
        </Typography>
      </Section>

      {report.themes && report.themes.length > 0 && (
        <Section title="Themes">
          <Stack spacing={1}>
            {report.themes.map((t, i) => (
              <Paper
                key={i}
                elevation={0}
                sx={{ p: 2, bgcolor: '#1f2023' }}
              >
                <Typography variant="body2">{t}</Typography>
              </Paper>
            ))}
          </Stack>
        </Section>
      )}

      <Section title="Findings">
        {LENS_ORDER.map((lens) => {
          const items = findingsByLens[lens];
          if (!items?.length) return null;
          return (
            <Box key={lens} sx={{ mb: 3 }}>
              <Stack
                direction="row"
                spacing={1.5}
                sx={{ mb: 1, alignItems: 'center' }}
              >
                <LensChip lens={lens} />
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  {items.length} finding{items.length === 1 ? '' : 's'}
                </Typography>
              </Stack>
              {items.map((f) => (
                <FindingCard
                  key={f.finding_id}
                  finding={f}
                  isHovered={hoveredFindingId === f.finding_id}
                  onHover={onHoverFinding}
                  expanded={expandedFindingIds.has(f.finding_id)}
                  onToggle={onToggleFinding}
                  cardRef={(el) => registerCardRef(f.finding_id, el)}
                />
              ))}
            </Box>
          );
        })}
      </Section>

      {report.open_questions_for_reviewers &&
        report.open_questions_for_reviewers.length > 0 && (
          <Section title="Open questions for the submitter">
            <Box component="ul" sx={{ m: 0, pl: 3 }}>
              {report.open_questions_for_reviewers.map((q, i) => (
                <li key={i}>
                  <Typography variant="body2">{q}</Typography>
                </li>
              ))}
            </Box>
          </Section>
        )}

      {report.recommended_discussion_topics &&
        report.recommended_discussion_topics.length > 0 && (
          <Section title="Recommended discussion topics">
            <Box component="ul" sx={{ m: 0, pl: 3 }}>
              {report.recommended_discussion_topics.map((t, i) => (
                <li key={i}>
                  <Typography variant="body2">{t}</Typography>
                </li>
              ))}
            </Box>
          </Section>
        )}

      <Divider sx={{ mt: 4, mb: 2 }} />
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
        Discussion aid for the brand team — not a verdict.
      </Typography>
    </Paper>
  );
}

export default function ReportViewer() {
  const [raw, setRaw] = useState('');
  const [submitted, setSubmitted] = useState<string>('');
  const [showRaw, setShowRaw] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [hoveredFindingId, setHoveredFindingId] = useState<string | null>(
    null,
  );
  const [expandedFindingIds, setExpandedFindingIds] = useState<Set<string>>(
    new Set(),
  );
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const parsed = useMemo<{ data?: FinalReport; error?: string }>(() => {
    if (!submitted.trim()) return {};
    try {
      const data = JSON.parse(submitted);
      if (!data || typeof data !== 'object' || !Array.isArray(data.findings)) {
        return { error: 'JSON parsed but does not look like a FinalReport.' };
      }
      return { data: data as FinalReport };
    } catch (e) {
      return { error: e instanceof Error ? e.message : 'Invalid JSON' };
    }
  }, [submitted]);

  // Free the object URL when the image changes or the component unmounts.
  useEffect(() => {
    return () => {
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    };
  }, [imageUrl]);

  const handleImageUpload = useCallback(
    (file: File | null) => {
      if (imageUrl) URL.revokeObjectURL(imageUrl);
      if (!file) {
        setImageUrl(null);
        return;
      }
      setImageUrl(URL.createObjectURL(file));
    },
    [imageUrl],
  );

  const toggleFinding = useCallback((id: string) => {
    setExpandedFindingIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const registerCardRef = useCallback(
    (id: string, el: HTMLDivElement | null) => {
      if (el) cardRefs.current.set(id, el);
      else cardRefs.current.delete(id);
    },
    [],
  );

  const selectFindingFromImage = useCallback(
    (id: string) => {
      setExpandedFindingIds((prev) => new Set(prev).add(id));
      const el = cardRefs.current.get(id);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    },
    [],
  );

  const hasImage = parsed.data && imageUrl;

  return (
    <>
      <AppBar position="static" elevation={0}>
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>
            Sentinel · Report Viewer
          </Typography>
          {parsed.data && (
            <Stack
              direction="row"
              spacing={1}
              sx={{ alignItems: 'center' }}
            >
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Show raw JSON
              </Typography>
              <Switch
                size="small"
                checked={showRaw}
                onChange={(e) => setShowRaw(e.target.checked)}
              />
            </Stack>
          )}
        </Toolbar>
      </AppBar>

      <Container maxWidth={hasImage ? 'xl' : 'md'} sx={{ py: 4 }}>
        <Paper elevation={1} sx={{ p: 3 }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            Paste a Sentinel report
          </Typography>
          <Typography variant="body2" sx={{ color: 'text.secondary', mb: 2 }}>
            Paste the JSON output from the synthesizer agent (the
            <code> final_report </code> object). Optionally upload the
            original submission image to see bounding-box overlays.
          </Typography>
          <TextField
            fullWidth
            multiline
            minRows={6}
            maxRows={12}
            placeholder='{ "content_summary": "...", "findings": [...] }'
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            sx={{
              '& .MuiInputBase-input': {
                fontFamily: 'monospace',
                fontSize: '0.8rem',
              },
            }}
          />
          <Stack
            direction="row"
            spacing={2}
            sx={{ mt: 2, alignItems: 'center', flexWrap: 'wrap' }}
          >
            <Button
              variant="contained"
              onClick={() => setSubmitted(raw)}
              disabled={!raw.trim()}
            >
              Render report
            </Button>
            <Button
              variant="outlined"
              component="label"
              startIcon={<UploadFileIcon />}
            >
              {imageUrl ? 'Replace image' : 'Upload image'}
              <input
                hidden
                type="file"
                accept="image/*"
                onChange={(e) =>
                  handleImageUpload(e.target.files?.[0] ?? null)
                }
              />
            </Button>
            {imageUrl && (
              <Button
                variant="text"
                size="small"
                onClick={() => handleImageUpload(null)}
              >
                Remove image
              </Button>
            )}
            <Box sx={{ flexGrow: 1 }} />
            <Button
              variant="text"
              onClick={() => {
                setRaw('');
                setSubmitted('');
                handleImageUpload(null);
                setExpandedFindingIds(new Set());
                setHoveredFindingId(null);
              }}
            >
              Clear all
            </Button>
          </Stack>
        </Paper>

        {parsed.error && (
          <Alert severity="error" sx={{ mt: 3 }}>
            {parsed.error}
          </Alert>
        )}

        {parsed.data && showRaw && (
          <Paper
            elevation={1}
            sx={{
              mt: 3,
              p: 3,
              fontFamily: 'monospace',
              fontSize: '0.75rem',
              whiteSpace: 'pre-wrap',
              overflowX: 'auto',
            }}
          >
            {JSON.stringify(parsed.data, null, 2)}
          </Paper>
        )}

        {parsed.data && !showRaw && hasImage && (
          <Box
            sx={{
              mt: 3,
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', md: 'minmax(0, 5fr) minmax(0, 7fr)' },
              gap: 3,
              alignItems: 'start',
            }}
          >
            <Box
              sx={{
                position: { md: 'sticky' },
                top: { md: 16 },
                alignSelf: 'start',
              }}
            >
              <Paper elevation={1} sx={{ p: 2 }}>
                <Typography
                  variant="overline"
                  sx={{ color: 'text.secondary' }}
                >
                  Submission · hover to inspect
                </Typography>
                <Box sx={{ mt: 1 }}>
                  <ImageWithBboxes
                    imageUrl={imageUrl!}
                    findings={parsed.data.findings}
                    hoveredFindingId={hoveredFindingId}
                    onHoverFinding={setHoveredFindingId}
                    onSelectFinding={selectFindingFromImage}
                  />
                </Box>
              </Paper>
            </Box>
            <ReportDisplay
              report={parsed.data}
              hoveredFindingId={hoveredFindingId}
              onHoverFinding={setHoveredFindingId}
              expandedFindingIds={expandedFindingIds}
              onToggleFinding={toggleFinding}
              registerCardRef={registerCardRef}
            />
          </Box>
        )}

        {parsed.data && !showRaw && !hasImage && (
          <ReportDisplay
            report={parsed.data}
            hoveredFindingId={hoveredFindingId}
            onHoverFinding={setHoveredFindingId}
            expandedFindingIds={expandedFindingIds}
            onToggleFinding={toggleFinding}
            registerCardRef={registerCardRef}
          />
        )}
      </Container>
    </>
  );
}
