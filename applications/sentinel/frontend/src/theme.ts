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

import { createTheme } from '@mui/material/styles';

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#8ab4f8', // Google Blue 200 (Dark mode primary)
      light: '#aecbfa',
      dark: '#669df6',
      contrastText: '#202124',
    },
    secondary: {
      main: '#669df6',
    },
    background: {
      default: '#202124', // Google Dark Grey
      paper: '#2d2e31', // Slightly lighter grey for surfaces
    },
    text: {
      primary: '#e8eaed',
      secondary: '#9aa0a6',
    },
    divider: '#3c4043',
    action: {
      hover: 'rgba(255, 255, 255, 0.08)',
      selected: 'rgba(138, 180, 248, 0.16)',
    },
  },
  typography: {
    fontFamily: '"Roboto", "Helvetica", "Arial", sans-serif',
    h1: {
      fontFamily: '"Google Sans", "Roboto", "Helvetica", "Arial", sans-serif',
    },
    h2: {
      fontFamily: '"Google Sans", "Roboto", "Helvetica", "Arial", sans-serif',
    },
    h3: {
      fontFamily: '"Google Sans", "Roboto", "Helvetica", "Arial", sans-serif',
    },
    h4: {
      fontFamily: '"Google Sans", "Roboto", "Helvetica", "Arial", sans-serif',
    },
    h5: {
      fontFamily: '"Google Sans", "Roboto", "Helvetica", "Arial", sans-serif',
    },
    h6: {
      fontFamily: '"Google Sans", "Roboto", "Helvetica", "Arial", sans-serif',
    },
    button: {
      fontFamily: '"Google Sans", "Roboto", "Helvetica", "Arial", sans-serif',
      textTransform: 'none',
      fontWeight: 500,
    },
  },
  shape: {
    borderRadius: 8,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 24,
          boxShadow: 'none',
          '&:hover': {
            boxShadow:
              '0 1px 2px 0 rgba(0,0,0,0.3), 0 1px 3px 1px rgba(0,0,0,0.15)',
          },
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
        },
        rounded: {
          borderRadius: 16,
        },
        elevation1: {
          boxShadow:
            '0 1px 2px 0 rgba(0,0,0,0.3), 0 1px 3px 1px rgba(0,0,0,0.15)',
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#2d2e31', // Match paper color for app bar in dark mode
          color: '#e8eaed',
          boxShadow: 'inset 0 -1px 0 0 #3c4043',
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          minHeight: 48,
          borderRadius: 24,
          '&.Mui-selected': {
            color: '#8ab4f8',
          },
        },
      },
    },
    MuiTabs: {
      styleOverrides: {
        indicator: {
          height: 3,
          borderTopLeftRadius: 3,
          borderTopRightRadius: 3,
        },
      },
    },
    MuiTextField: {
      defaultProps: {
        variant: 'outlined',
        size: 'small',
      },
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            borderRadius: 8,
          },
        },
      },
    },
  },
});

export default theme;
