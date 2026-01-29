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

import { useState, useCallback, useEffect } from 'react';
import {
  Box,
  AppBar,
  Toolbar,
  Typography,
  LinearProgress,
  ThemeProvider,
  CssBaseline,
} from '@mui/material';
import { Shield as ShieldIcon } from '@mui/icons-material';
import theme from './theme';
import Sidebar from './components/Sidebar';
import MediaViewer from './components/MediaViewer';
import ResultsPanel from './components/ResultsPanel';
import { analyzeContent } from './api/client';
import { AnalyzePayload, AnalysisResult } from './types';

function App() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysisData, setAnalysisData] = useState<AnalysisResult | null>(null);
  const [mediaType, setMediaType] = useState<'video' | 'image' | null>(null);
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [selectedIssueIndex, setSelectedIssueIndex] = useState<number | null>(
    null,
  );

  // Resizer State
  const [resultsWidth, setResultsWidth] = useState(480);
  const [isResizing, setIsResizing] = useState(false);

  const handleResizeStart = () => {
    setIsResizing(true);
  };

  const handleResizeMove = useCallback(
    (e: MouseEvent) => {
      if (!isResizing) return;
      const newWidth = document.body.clientWidth - e.clientX;
      if (newWidth >= 300 && newWidth <= window.innerWidth * 0.6) {
        setResultsWidth(newWidth);
      }
    },
    [isResizing],
  );

  const handleResizeEnd = useCallback(() => {
    setIsResizing(false);
  }, []);

  useEffect(() => {
    if (isResizing) {
      window.addEventListener('mousemove', handleResizeMove);
      window.addEventListener('mouseup', handleResizeEnd);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    } else {
      window.removeEventListener('mousemove', handleResizeMove);
      window.removeEventListener('mouseup', handleResizeEnd);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }
    return () => {
      window.removeEventListener('mousemove', handleResizeMove);
      window.removeEventListener('mouseup', handleResizeEnd);
    };
  }, [isResizing, handleResizeMove, handleResizeEnd]);

  const handleAnalyze = async (payload: AnalyzePayload) => {
    setLoading(true);
    setError(null);
    setAnalysisData(null);
    setSelectedIssueIndex(null);

    // Set Media State
    if (payload.video_url) {
      setMediaType('video');
      setMediaUrl(payload.display_url || payload.video_url);
    } else if (payload.image_url) {
      setMediaType('image');
      setMediaUrl(payload.display_url || payload.image_url);
    }

    try {
      // Standard Analysis
      const result = await analyzeContent(payload);
      setAnalysisData(result);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'An error occurred during analysis',
      );
    } finally {
      setLoading(false);
    }
  };

  const handleIssueClick = (index: number) => {
    setSelectedIssueIndex(index);
    const element = document.getElementById(`issue-${index}`);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  };

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          height: '100vh',
          overflow: 'hidden',
          bgcolor: 'background.default',
        }}
      >
        {/* Progress Bar */}
        {loading && (
          <LinearProgress
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              zIndex: 1201,
            }}
          />
        )}

        {/* AppBar */}
        <AppBar
          position="static"
          elevation={0}
          sx={{ zIndex: 1200, borderBottom: 1, borderColor: 'divider' }}
        >
          <Toolbar sx={{ height: 64, px: 3 }}>
            <ShieldIcon sx={{ color: 'primary.main', fontSize: 28, mr: 1.5 }} />
            <Typography
              variant="h6"
              color="text.primary"
              sx={{ fontWeight: 400, fontSize: 22, letterSpacing: -0.5 }}
            >
              Sentinel
            </Typography>
          </Toolbar>
        </AppBar>

        {/* Main Content */}
        <Box sx={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          <Sidebar
            onAnalyze={handleAnalyze}
            isLoading={loading}
            error={error}
          />

          <MediaViewer
            type={mediaType}
            url={mediaUrl}
            issues={analysisData?.issues || []}
            onMarkerClick={handleIssueClick}
            selectedIssueIndex={selectedIssueIndex}
          />

          <ResultsPanel
            summary={analysisData?.summary || null}
            issues={analysisData?.issues || []}
            width={resultsWidth}
            onResizeStart={handleResizeStart}
            onIssueClick={handleIssueClick}
            selectedIssueIndex={selectedIssueIndex}
            progressItems={[]}
            isLoading={loading}
          />
        </Box>
      </Box>
    </ThemeProvider>
  );
}

export default App;
