/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Mirrors sentinel_agent/schemas.py FinalReport.
 */

export type ReviewLens =
  | 'medical'
  | 'legal'
  | 'regulatory'
  | 'editorial'
  | 'custom';

export type Severity =
  | 'critical'
  | 'high'
  | 'medium'
  | 'low'
  | 'informational';

export type ConfidenceBand = 'low' | 'medium' | 'high';

export type EvidenceDepth = 'surface' | 'moderate' | 'deep';

export type AudienceType =
  | 'healthcare_professional'
  | 'patient_or_consumer'
  | 'payer'
  | 'mixed_or_unclear';

export interface ContentLocation {
  page?: number | null;
  section?: string | null;
  bbox?: number[] | null;
  quote?: string | null;
}

export interface Finding {
  finding_id: string;
  review_lens: ReviewLens;
  category: string;
  severity: Severity;
  confidence: number;
  confidence_band: ConfidenceBand;
  evidence_depth: EvidenceDepth;
  related_item_ids?: string[];
  quoted_content?: string | null;
  observation: string;
  mlr_principle: string;
  discussion: string;
  suggested_questions?: string[];
  suggested_actions?: string[];
  location?: ContentLocation | null;
}

export interface FinalReport {
  content_summary: string;
  intended_audience: AudienceType;
  promotional_intent: string;
  executive_summary: string;
  themes?: string[];
  findings: Finding[];
  open_questions_for_reviewers?: string[];
  recommended_discussion_topics?: string[];
  counts_by_lens?: Record<string, number>;
  counts_by_severity?: Record<string, number>;
}
