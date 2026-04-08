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

import React, { useState, useMemo } from 'react';
import {
  Box,
  Typography,
  Card,
  Chip,
  List,
  CircularProgress,
  Collapse,
  Paper,
  alpha,
  useTheme,
  IconButton,
  Tooltip,
  Skeleton,
  Fade,
} from '@mui/material';
import {
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  RadioButtonUnchecked as RadioButtonUncheckedIcon,
  Timeline as TimelineIcon,
  AutoAwesome as AutoAwesomeIcon, // Sparkles for AI
  Sort as SortIcon,
  WarningAmber as WarningAmberIcon,
  InfoOutlined as InfoOutlinedIcon,
  ReportProblem as ReportProblemIcon,
  Dangerous as DangerousIcon,
} from '@mui/icons-material';
import { AnalysisIssue } from '../types';

interface ResultsPanelProps {
  summary: string | null;
  issues: AnalysisIssue[];
  width: number;
  onResizeStart: () => void;
  onIssueClick: (index: number) => void;
  selectedIssueIndex: number | null;
  progressItems?: Array<{
    description: string;
    status: 'pending' | 'loading' | 'complete' | 'error';
  }>;
  isLoading?: boolean;
}

type FilterType = 'all' | 'critical' | 'high' | 'medium' | 'low';

const ResultsPanel: React.FC<ResultsPanelProps> = ({
  summary,
  issues,
  width,
  onResizeStart,
  onIssueClick,
  selectedIssueIndex,
  progressItems,
  isLoading = false,
}) => {
  const theme = useTheme();
  const [activeFilter, setActiveFilter] = useState<FilterType>('all');

  const formatCategory = (cat: string) => {
    return cat
      .split('_')
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
  };

  const getSeverityConfig = (severity: string) => {
    switch (severity) {
      case 'critical':
        return {
          color: theme.palette.error.main,
          icon: DangerousIcon,
          label: 'Critical',
        };
      case 'high':
        return { color: '#f97316', icon: ReportProblemIcon, label: 'High' }; // Orange
      case 'medium':
        return {
          color: theme.palette.warning.main,
          icon: WarningAmberIcon,
          label: 'Medium',
        };
      case 'low':
        return {
          color: theme.palette.success.main,
          icon: InfoOutlinedIcon,
          label: 'Low',
        };
      default:
        return {
          color: theme.palette.text.secondary,
          icon: InfoOutlinedIcon,
          label: 'Info',
        };
    }
  };

  const filteredIssues = useMemo(() => {
    if (activeFilter === 'all') return issues;
    return issues.filter((issue) => issue.severity === activeFilter);
  }, [issues, activeFilter]);

  const filterCounts = useMemo(() => {
    const counts = {
      all: issues.length,
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
    };
    issues.forEach((issue) => {
      if (issue.severity in counts) {
        counts[issue.severity as keyof typeof counts]++;
      }
    });
    return counts;
  }, [issues]);

  return (
    <Box
      sx={{ display: 'flex', height: '100%', position: 'relative', zIndex: 10 }}
    >
      {/* Resizer */}
      <Box
        onMouseDown={onResizeStart}
        sx={{
          width: 4,
          cursor: 'col-resize',
          bgcolor: 'divider',
          transition: 'background-color 0.2s',
          '&:hover': { bgcolor: 'primary.main' },
          zIndex: 10,
        }}
      />

      {/* Content */}
      <Paper
        elevation={0}
        square
        sx={{
          width: width,
          display: 'flex',
          flexDirection: 'column',
          borderLeft: 1,
          borderColor: 'divider',
          overflow: 'hidden',
          transition: 'width 0.1s',
          bgcolor: 'background.default',
        }}
      >
        {/* Header */}
        <Box
          sx={{
            p: 2,
            pl: 3,
            pr: 2,
            bgcolor: 'background.paper',
            borderBottom: 1,
            borderColor: 'divider',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            height: 64,
            flexShrink: 0,
          }}
        >
          <Typography
            variant="h6"
            sx={{ fontSize: 18, fontWeight: 500, color: 'text.primary' }}
          >
            Analysis Results
          </Typography>
          <Box>
            <Tooltip title="Sort">
              <IconButton size="small" sx={{ color: 'text.secondary' }}>
                <SortIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        </Box>

        {/* Filter Chips */}
        <Box
          sx={{
            px: 2,
            py: 1.5,
            display: 'flex',
            gap: 1,
            overflowX: 'auto',
            borderBottom: 1,
            borderColor: 'divider',
            bgcolor: 'background.default',
            '&::-webkit-scrollbar': { display: 'none' }, // Hide scrollbar
            msOverflowStyle: 'none',
            scrollbarWidth: 'none',
          }}
        >
          {(['all', 'critical', 'high', 'medium', 'low'] as FilterType[]).map(
            (filter) => {
              const isActive = activeFilter === filter;
              const count = filterCounts[filter];
              if (count === 0 && filter !== 'all') return null; // Hide empty filters

              return (
                <Chip
                  key={filter}
                  label={`${filter.charAt(0).toUpperCase() + filter.slice(1)} (${count})`}
                  onClick={() => setActiveFilter(filter)}
                  size="small"
                  clickable
                  variant={isActive ? 'filled' : 'outlined'}
                  color={isActive ? 'primary' : 'default'}
                  sx={{
                    fontWeight: 500,
                    borderColor: isActive ? 'transparent' : 'divider',
                    bgcolor: isActive ? 'primary.main' : 'transparent',
                    color: isActive ? 'primary.contrastText' : 'text.secondary',
                    '&:hover': {
                      bgcolor: isActive ? 'primary.dark' : 'action.hover',
                    },
                  }}
                />
              );
            },
          )}
        </Box>

        {/* Scrollable Area */}
        <Box sx={{ flex: 1, overflowY: 'auto', p: 2 }}>
          {/* Loading Skeletons */}
          {isLoading && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <Skeleton
                variant="rectangular"
                height={100}
                sx={{ borderRadius: 3, bgcolor: 'action.hover' }}
              />
              <Skeleton
                variant="rectangular"
                height={140}
                sx={{ borderRadius: 3, bgcolor: 'action.hover' }}
              />
              <Skeleton
                variant="rectangular"
                height={140}
                sx={{ borderRadius: 3, bgcolor: 'action.hover' }}
              />
            </Box>
          )}

          {!isLoading && (
            <Fade in={!isLoading} timeout={500}>
              <Box>
                {/* Summary Section - Hero Card */}
                <Collapse in={!!summary} unmountOnExit>
                  <Card
                    elevation={0}
                    sx={{
                      mb: 3,
                      border: 1,
                      borderColor: alpha(theme.palette.primary.main, 0.3),
                      bgcolor: alpha(theme.palette.primary.main, 0.08),
                      borderRadius: 3,
                      overflow: 'visible',
                    }}
                  >
                    <Box sx={{ p: 2.5 }}>
                      <Box
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 1,
                          mb: 1.5,
                        }}
                      >
                        <AutoAwesomeIcon
                          sx={{ fontSize: 20, color: 'primary.main' }}
                        />
                        <Typography
                          variant="subtitle2"
                          sx={{ fontWeight: 600, color: 'primary.main' }}
                        >
                          AI Summary
                        </Typography>
                      </Box>
                      <Typography
                        variant="body2"
                        sx={{ color: 'text.primary', lineHeight: 1.6 }}
                      >
                        {summary}
                      </Typography>
                    </Box>
                  </Card>
                </Collapse>

                {/* Progress Tracker */}
                {progressItems && progressItems.length > 0 && (
                  <Card
                    elevation={0}
                    sx={{
                      mb: 3,
                      border: 1,
                      borderColor: 'divider',
                      borderRadius: 3,
                      bgcolor: 'background.paper',
                    }}
                  >
                    <Box
                      sx={{
                        p: 2,
                        borderBottom: 1,
                        borderColor: 'divider',
                        bgcolor: alpha(theme.palette.action.active, 0.04),
                      }}
                    >
                      <Typography
                        variant="subtitle2"
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 1.5,
                          fontSize: 13,
                          color: 'text.secondary',
                        }}
                      >
                        <CircularProgress size={14} thickness={5} />
                        Processing...
                      </Typography>
                    </Box>
                    <List dense sx={{ p: 0 }}>
                      {progressItems.map((item, idx) => (
                        <Box
                          key={idx}
                          sx={{
                            px: 2,
                            py: 1.5,
                            display: 'flex',
                            alignItems: 'center',
                            gap: 2,
                            borderBottom:
                              idx < progressItems.length - 1 ? 1 : 0,
                            borderColor: 'divider',
                            opacity: item.status === 'pending' ? 0.5 : 1,
                          }}
                        >
                          {item.status === 'pending' && (
                            <RadioButtonUncheckedIcon
                              sx={{ fontSize: 18, color: 'text.disabled' }}
                            />
                          )}
                          {item.status === 'loading' && (
                            <CircularProgress size={16} thickness={5} />
                          )}
                          {item.status === 'complete' && (
                            <CheckCircleIcon
                              sx={{ fontSize: 18, color: 'success.main' }}
                            />
                          )}
                          {item.status === 'error' && (
                            <ErrorIcon
                              sx={{ fontSize: 18, color: 'error.main' }}
                            />
                          )}
                          <Typography
                            variant="body2"
                            sx={{ fontSize: 13, color: 'text.primary' }}
                          >
                            {item.description}
                          </Typography>
                        </Box>
                      ))}
                    </List>
                  </Card>
                )}

                {/* Issues List */}
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {issues.length === 0 && !summary && (
                    <Box
                      sx={{ textAlign: 'center', py: 8, px: 2, opacity: 0.6 }}
                    >
                      <Box
                        sx={{
                          width: 64,
                          height: 64,
                          bgcolor: 'action.hover',
                          borderRadius: '50%',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          mx: 'auto',
                          mb: 2,
                          border: 1,
                          borderColor: 'divider',
                        }}
                      >
                        <TimelineIcon
                          sx={{ fontSize: 32, color: 'text.secondary' }}
                        />
                      </Box>
                      <Typography
                        variant="subtitle1"
                        color="text.secondary"
                        gutterBottom
                      >
                        No Analysis Yet
                      </Typography>
                      <Typography variant="body2" color="text.disabled">
                        Upload content to see AI insights here.
                      </Typography>
                    </Box>
                  )}

                  {filteredIssues.map((issue, idx) => {
                    const isSelected = selectedIssueIndex === idx;
                    const { color, icon: Icon } = getSeverityConfig(
                      issue.severity,
                    );

                    return (
                      <Card
                        key={idx}
                        id={`issue-${idx}`}
                        elevation={isSelected ? 4 : 0}
                        onClick={() => onIssueClick(issues.indexOf(issue))} // Use original index for callback
                        sx={{
                          position: 'relative',
                          cursor: 'pointer',
                          borderRadius: 3,
                          border: 1,
                          borderColor: isSelected ? color : 'divider',
                          transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
                          bgcolor: isSelected
                            ? alpha(color, 0.08)
                            : 'background.paper',
                          overflow: 'visible',
                          '&:hover': {
                            borderColor: isSelected ? color : 'text.disabled',
                            transform: 'translateY(-1px)',
                            boxShadow: theme.shadows[4],
                            bgcolor: isSelected
                              ? alpha(color, 0.12)
                              : 'action.hover',
                          },
                          '&::before': {
                            content: '""',
                            position: 'absolute',
                            left: -1,
                            top: 12,
                            bottom: 12,
                            width: 4,
                            bgcolor: color,
                            borderTopRightRadius: 4,
                            borderBottomRightRadius: 4,
                            opacity: isSelected ? 1 : 0.6,
                          },
                        }}
                      >
                        <Box sx={{ p: 2, pl: 2.5 }}>
                          {/* Header Row */}
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              alignItems: 'flex-start',
                              mb: 1,
                            }}
                          >
                            <Box
                              sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 1,
                              }}
                            >
                              {/* Timestamp Pill */}
                              {issue.start_timestamp &&
                                issue.start_timestamp !== 'N/A' && (
                                  <Box
                                    component="span"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onIssueClick(issues.indexOf(issue));
                                    }}
                                    sx={{
                                      display: 'inline-flex',
                                      alignItems: 'center',
                                      px: 0.75,
                                      py: 0.25,
                                      borderRadius: 1, // 4px radius, more rectangular
                                      bgcolor: alpha(
                                        theme.palette.text.primary,
                                        0.08,
                                      ),
                                      color: 'text.primary',
                                      fontFamily:
                                        '"Roboto Mono", "Menlo", "Consolas", monospace',
                                      fontSize: '0.75rem',
                                      fontWeight: 500,
                                      letterSpacing: -0.25,
                                      cursor: 'pointer',
                                      transition: 'all 0.2s',
                                      border: '1px solid transparent',
                                      '&:hover': {
                                        bgcolor: alpha(
                                          theme.palette.primary.main,
                                          0.15,
                                        ),
                                        color: 'primary.main',
                                        borderColor: alpha(
                                          theme.palette.primary.main,
                                          0.3,
                                        ),
                                      },
                                    }}
                                  >
                                    {issue.start_timestamp}
                                  </Box>
                                )}
                              <Typography
                                variant="caption"
                                sx={{
                                  color: 'text.secondary',
                                  fontWeight: 500,
                                  textTransform: 'uppercase',
                                  letterSpacing: 0.5,
                                }}
                              >
                                {formatCategory(issue.category)}
                              </Typography>
                            </Box>

                            {/* Severity Icon */}
                            <Tooltip title={`Severity: ${issue.severity}`}>
                              <Icon sx={{ fontSize: 18, color: color }} />
                            </Tooltip>
                          </Box>

                          {/* Content */}
                          <Typography
                            variant="body2"
                            sx={{
                              color: 'text.primary',
                              lineHeight: 1.5,
                              fontWeight: isSelected ? 500 : 400,
                            }}
                          >
                            {issue.description}
                          </Typography>
                        </Box>
                      </Card>
                    );
                  })}
                </Box>
              </Box>
            </Fade>
          )}
        </Box>
      </Paper>
    </Box>
  );
};

export default ResultsPanel;
