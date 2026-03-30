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

import React, { useState } from 'react';
import {
  Box,
  Typography,
  Tabs,
  Tab,
  TextField,
  MenuItem,
  Button,
  Paper,
  Alert,
  Fade,
  Avatar,
  IconButton,
} from '@mui/material';
import {
  AutoAwesome as AutoAwesomeIcon,
  YouTube as YouTubeIcon,
  Cloud as CloudIcon,
  Delete as DeleteIcon,
} from '@mui/icons-material';
import { AnalyzePayload, StorageItem } from '../types';
import CloudStoragePicker from './CloudStoragePicker';

interface SidebarProps {
  onAnalyze: (payload: AnalyzePayload) => void;
  isLoading: boolean;
  error?: string | null;
}

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function CustomTabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props;

  if (value !== index) {
    return null;
  }

  return (
    <Fade in={true}>
      <Box
        role="tabpanel"
        id={`simple-tabpanel-${index}`}
        aria-labelledby={`simple-tab-${index}`}
        sx={{ flex: 1, display: 'flex', flexDirection: 'column', pt: 3 }}
        {...other}
      >
        {children}
      </Box>
    </Fade>
  );
}

const Sidebar: React.FC<SidebarProps> = ({ onAnalyze, isLoading, error }) => {
  const [sourceType, setSourceType] = useState<number>(0);
  const [videoUrl, setVideoUrl] = useState('');
  const [selectedStorageItem, setSelectedStorageItem] =
    useState<StorageItem | null>(null);
  const [isPickerOpen, setIsPickerOpen] = useState(false);

  const [speed, setSpeed] = useState<'fast' | 'powerful'>('fast');
  const [frameRate, setFrameRate] = useState<string>('1.0');

  const handleTabChange = (_: React.SyntheticEvent, newValue: number) => {
    setSourceType(newValue);
  };

  const handleStorageSelect = (item: StorageItem) => {
    setSelectedStorageItem(item);
    setIsPickerOpen(false);
  };

  const handleSubmit = () => {
    const payload: AnalyzePayload = {
      speed,
    };

    if (sourceType === 0) {
      if (!videoUrl) return;
      payload.video_url = videoUrl;
      payload.frame_rate = parseFloat(frameRate);
    } else if (sourceType === 1) {
      if (!selectedStorageItem) return;
      // Determine if it's a video or image based on content type
      if (selectedStorageItem.content_type.startsWith('video/')) {
        payload.video_url = selectedStorageItem.uri;
        payload.frame_rate = parseFloat(frameRate);
        // For GCS videos, we also want to tell the viewer to use the proxy URL
        payload.display_url = selectedStorageItem.url;
      } else {
        // Use GS URI for backend analysis (Vertex AI handles it)
        payload.image_url = selectedStorageItem.uri;
        // Use Proxy URL for frontend display
        payload.display_url = selectedStorageItem.url;
      }
    }

    onAnalyze(payload);
  };

  return (
    <Paper
      elevation={0}
      square
      sx={{
        width: 400,
        height: '100%',
        borderRight: 1,
        borderColor: 'divider',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 10,
      }}
    >
      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Tabs
          value={sourceType}
          onChange={handleTabChange}
          variant="fullWidth"
          indicatorColor="primary"
          textColor="primary"
          aria-label="analysis source tabs"
        >
          <Tab icon={<YouTubeIcon />} label="YouTube" iconPosition="start" />
          <Tab icon={<CloudIcon />} label="Cloud" iconPosition="start" />
        </Tabs>
      </Box>

      <Box
        sx={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          p: 3,
          overflowY: 'auto',
        }}
      >
        <CustomTabPanel value={sourceType} index={0}>
          <TextField
            fullWidth
            label="YouTube URL"
            placeholder="https://youtube.com/watch?v=..."
            value={videoUrl}
            onChange={(e) => setVideoUrl(e.target.value)}
            sx={{ mb: 3 }}
          />
          <TextField
            select
            fullWidth
            label="Video Sampling"
            value={frameRate}
            onChange={(e) => setFrameRate(e.target.value)}
            helperText="Higher sampling rates provide more detailed analysis but take longer."
          >
            <MenuItem value="1.0">Standard (1 FPS)</MenuItem>
            <MenuItem value="0.5">Eco (0.5 FPS)</MenuItem>
            <MenuItem value="2.0">Detailed (2 FPS)</MenuItem>
          </TextField>
        </CustomTabPanel>

        <CustomTabPanel value={sourceType} index={1}>
          {!selectedStorageItem ? (
            <Button
              variant="outlined"
              startIcon={<CloudIcon />}
              onClick={() => setIsPickerOpen(true)}
              sx={{ height: 200, borderStyle: 'dashed', borderWidth: 2 }}
            >
              Select or Upload from Cloud
            </Button>
          ) : (
            <Paper
              variant="outlined"
              sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 2 }}
            >
              <Avatar
                src={
                  selectedStorageItem.content_type.startsWith('image/')
                    ? selectedStorageItem.url
                    : undefined
                }
                variant="rounded"
                sx={{ width: 64, height: 64 }}
              >
                {!selectedStorageItem.content_type.startsWith('image/') && (
                  <YouTubeIcon />
                )}
              </Avatar>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="subtitle2" noWrap>
                  {selectedStorageItem.name}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {new Date(selectedStorageItem.created).toLocaleDateString()}
                </Typography>
              </Box>
              <IconButton
                onClick={() => setSelectedStorageItem(null)}
                color="error"
              >
                <DeleteIcon />
              </IconButton>
            </Paper>
          )}

          {selectedStorageItem?.content_type.startsWith('video/') && (
            <Box sx={{ mt: 3 }}>
              <TextField
                select
                fullWidth
                label="Video Sampling"
                value={frameRate}
                onChange={(e) => setFrameRate(e.target.value)}
                helperText="Higher sampling rates provide more detailed analysis but take longer."
              >
                <MenuItem value="1.0">Standard (1 FPS)</MenuItem>
                <MenuItem value="0.5">Eco (0.5 FPS)</MenuItem>
                <MenuItem value="2.0">Detailed (2 FPS)</MenuItem>
              </TextField>
            </Box>
          )}

          <Box sx={{ mt: 2 }}>
            <Typography variant="caption" color="text.secondary">
              Select an image or video from your Cloud Storage library.
            </Typography>
          </Box>

          <CloudStoragePicker
            open={isPickerOpen}
            onClose={() => setIsPickerOpen(false)}
            onSelect={handleStorageSelect}
          />
        </CustomTabPanel>

        <Box sx={{ mt: 'auto', pt: 4 }}>
          <TextField
            select
            fullWidth
            label="Analysis Model"
            value={speed}
            onChange={(e) => setSpeed(e.target.value as 'fast' | 'powerful')}
            helperText={
              speed === 'powerful'
                ? 'Gemini 3.0 Pro: Deep reasoning for complex medical cases.'
                : 'Gemini Flash: Rapid screening and identification.'
            }
            sx={{ mb: 3 }}
          >
            <MenuItem value="fast">Fast (Gemini Flash)</MenuItem>
            <MenuItem value="powerful">Powerful (Gemini 3.0 Pro)</MenuItem>
          </TextField>

          {error && (
            <Alert severity="error" variant="outlined" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Button
            variant="contained"
            fullWidth
            size="large"
            startIcon={<AutoAwesomeIcon />}
            onClick={handleSubmit}
            disabled={
              isLoading ||
              (sourceType === 0 && !videoUrl) ||
              (sourceType === 1 && !selectedStorageItem)
            }
            sx={{
              height: 56,
              borderRadius: 8,
              fontSize: 16,
              fontWeight: 'bold',
              textTransform: 'none',
            }}
          >
            {isLoading ? 'Analyzing...' : 'Analyze Content'}
          </Button>
        </Box>
      </Box>
    </Paper>
  );
};

export default Sidebar;
