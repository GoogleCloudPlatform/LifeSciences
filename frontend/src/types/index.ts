/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

export interface Location {
  x: number;
  y: number;
}

export interface AnalysisIssue {
  issue_id: string;
  timestamp: string;
  start_timestamp?: string;
  end_timestamp?: string;
  description: string;
  category: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  location?: Location | null;
}

export interface AnalysisResult {
  video_url: string;
  summary: string;
  issues: AnalysisIssue[];
}

export interface AnalyzePayload {
  video_url?: string;
  image_url?: string;
  display_url?: string; // For frontend display when image_url is a gs:// URI
  speed: 'fast' | 'powerful';
  frame_rate?: number;
}

export interface StorageItem {
  name: string;
  uri: string;
  url: string;
  content_type: string;
  created: string;
}
