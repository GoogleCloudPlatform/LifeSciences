# Sentinel Frontend

Clean, Google Material Design-inspired frontend for the Sentinel medical literature review tool.

## Features

- **Material Design**: Google-style UI with clean typography and components
- **Real-time Analysis**: Connects to Sentinel API for video analysis
- **Issue Display**: Color-coded severity levels and categorized issues
- **Timestamp Links**: Easy navigation to specific video segments
- **Responsive**: Works on desktop and mobile devices

## Running the Frontend

### Option 1: Simple HTTP Server (Python)

```bash
cd frontend
python3 -m http.server 3000
```

Visit: `http://localhost:3000`

### Option 2: Node.js HTTP Server

```bash
cd frontend
npx http-server -p 3000
```

Visit: `http://localhost:3000`

### Option 3: Open Directly

Simply open `index.html` in your browser.

**Note:** If you open the file directly, you may encounter CORS issues. Use one of the HTTP server options above for best results.

## Configuration

The frontend connects to the API at `http://localhost:8000` by default. To change this, edit the `API_BASE_URL` constant in [index.html](index.html):

```javascript
const API_BASE_URL = 'http://localhost:8000';
```

## Usage

1. Ensure the Sentinel API is running on `http://localhost:8000`
2. Open the frontend in your browser
3. Enter a YouTube URL
4. Click "Analyze" or press Enter
5. Review the results with timestamps and severity levels

## Tech Stack

- **Pure HTML/CSS/JavaScript**: No build step required
- **Google Fonts**: Google Sans and Roboto
- **Material Icons**: Google's Material Design icons
- **Vanilla JS**: No frameworks, lightweight and fast
