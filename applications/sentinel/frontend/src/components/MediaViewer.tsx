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

import React, { useState, useEffect, useRef } from 'react';
import { Box, Typography, Paper, Fade } from '@mui/material';
import { Analytics as AnalyticsIcon } from '@mui/icons-material';
import { AnalysisIssue } from '../types';
import { parseTimestampToSeconds } from '../utils/timeUtils';

interface MediaViewerProps {
  type: 'video' | 'image' | null;
  url: string | null;
  issues: AnalysisIssue[];
  onMarkerClick: (index: number) => void;
  selectedIssueIndex: number | null;
}

const MediaViewer: React.FC<MediaViewerProps> = ({
  type,
  url,
  issues,
  onMarkerClick,
  selectedIssueIndex,
}) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Handle seeking when an issue is selected
  useEffect(() => {
    if (selectedIssueIndex === null || !issues[selectedIssueIndex]) return;

    const issue = issues[selectedIssueIndex];
    if (!issue.start_timestamp) return;

    const seconds = parseTimestampToSeconds(issue.start_timestamp);
    if (seconds === null) return;

    if (type === 'video') {
      const isYouTube =
        url?.includes('youtube.com') || url?.includes('youtu.be');

      if (isYouTube && iframeRef.current) {
        // Seek YouTube iframe
        iframeRef.current.contentWindow?.postMessage(
          JSON.stringify({
            event: 'command',
            func: 'seekTo',
            args: [seconds, true],
          }),
          '*',
        );
      } else if (!isYouTube && videoRef.current) {
        // Seek HTML5 Video
        videoRef.current.currentTime = seconds;
        videoRef.current
          .play()
          .catch((e) => console.log('Autoplay prevented:', e));
      }
    }
  }, [selectedIssueIndex, issues, type, url]);

  const extractVideoID = (videoUrl: string) => {
    try {
      if (videoUrl.includes('v=')) return videoUrl.split('v=')[1].split('&')[0];
      if (videoUrl.includes('youtu.be/')) return videoUrl.split('youtu.be/')[1];
    } catch (e) {
      console.error(e);
    }
    return '';
  };

  const renderContent = () => {
    if (!type) {
      return (
        <Fade in={true} timeout={800}>
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: 'text.secondary',
              gap: 3,
              opacity: 0.7,
            }}
          >
            <Box
              sx={{
                p: 4,
                borderRadius: '50%',
                bgcolor: 'action.hover',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <AnalyticsIcon sx={{ fontSize: 80, color: 'text.disabled' }} />
            </Box>
            <Box sx={{ textAlign: 'center' }}>
              <Typography
                variant="h5"
                sx={{ fontWeight: 400, color: 'text.primary', mb: 1 }}
              >
                Ready to analyze
              </Typography>
              <Typography variant="body1">
                Select a video or image from the sidebar to begin medical review
              </Typography>
            </Box>
          </Box>
        </Fade>
      );
    }

    if (type === 'video' && url) {
      const isYouTube = url.includes('youtube.com') || url.includes('youtu.be');

      return (
        <Paper
          elevation={4}
          sx={{
            width: '100%',
            maxWidth: 1000,
            overflow: 'hidden',
            aspectRatio: '16/9',
            bgcolor: 'black',
            borderRadius: 3,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {isYouTube ? (
            <iframe
              ref={iframeRef}
              width="100%"
              height="100%"
              src={`https://www.youtube.com/embed/${extractVideoID(url)}?enablejsapi=1`}
              frameBorder="0"
              allowFullScreen
              title="Video Player"
            />
          ) : (
            <video
              ref={videoRef}
              width="100%"
              height="100%"
              controls
              src={url}
              title="Video Player"
            />
          )}
        </Paper>
      );
    }

    if (type === 'image' && url) {
      return (
        <Paper
          elevation={4}
          sx={{
            position: 'relative',
            maxWidth: '100%',
            maxHeight: '80vh',
            borderRadius: 3,
            overflow: 'hidden',
            bgcolor: 'black',
          }}
        >
          <img
            src={url}
            alt="Analysis Target"
            style={{
              display: 'block',
              maxWidth: '100%',
              maxHeight: '80vh',
            }}
            onLoad={() => setImageLoaded(true)}
          />
          {imageLoaded &&
            issues.map((issue, idx) => {
              if (!issue.location) return null;
              const isSelected = selectedIssueIndex === idx;
              return (
                <Box
                  key={issue.issue_id}
                  onClick={() => onMarkerClick(idx)}
                  sx={{
                    position: 'absolute',
                    top: `${issue.location.y * 100}%`,
                    left: `${issue.location.x * 100}%`,
                    transform: isSelected
                      ? 'translate(-50%, -50%) scale(1.3)'
                      : 'translate(-50%, -50%)',
                    width: 28,
                    height: 28,
                    bgcolor: isSelected
                      ? getColorForSeverity(issue.severity)
                      : 'rgba(32, 33, 36, 0.9)', // Dark background for markers
                    border: '2px solid',
                    borderColor: getColorForSeverity(issue.severity),
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 12,
                    fontWeight: 'bold',
                    color: isSelected
                      ? '#202124' // Dark text on selected
                      : getColorForSeverity(issue.severity),
                    cursor: 'pointer',
                    boxShadow: 4,
                    zIndex: isSelected ? 10 : 5,
                    transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
                    '&:hover': {
                      transform: 'translate(-50%, -50%) scale(1.1)',
                    },
                  }}
                >
                  {idx + 1}
                </Box>
              );
            })}
        </Paper>
      );
    }

    return null;
  };

  return (
    <Box
      sx={{
        flex: 1,
        bgcolor: 'background.default',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        p: 4,
        overflow: 'hidden',
        position: 'relative',
        backgroundImage:
          'radial-gradient(circle at 50% 50%, rgba(138, 180, 248, 0.05) 0%, rgba(32, 33, 36, 0) 70%)', // Subtle spotlight effect
      }}
    >
      {renderContent()}
    </Box>
  );
};

const getColorForSeverity = (severity: string) => {
  switch (severity) {
    case 'critical':
      return '#ea4335';
    case 'high':
      return '#f97316';
    case 'medium':
      return '#fbbc04';
    case 'low':
      return '#34a853';
    default:
      return '#9aa0a6';
  }
};

export default MediaViewer;
