# Sentinel: Pharma Regulatory & Ad Content Checker

**Sentinel** is a content analysis solution specifically designed for pharmaceutical regulatory affairs and marketing compliance teams. Powered by **Google Gemini AI**, Sentinel automates the rigorous review of promotional advertisements and regulatory documentation  accuracy, citation integrity, and adherence to industry presentation standards, and flag potential compliance issues for review.

## Features

* **Pharma Ad Video Compliance**: Perform analysis of video advertisements. Sentinel flags potential  inaccuracies and compliance risks with  **timestamped markers**  editing.
* **Regulatory Image & Diagram Audit**: Review complex medical diagrams, infographics, and charts. The tool provides visual annotations to highlight inconsistencies or formatting issues within regulatory submissions.
* **Interactive Professional UI**: A clean, efficient interface built on Google Material Design principles, providing compliance teams with real-time feedback and audit-ready reports.

---

> [!IMPORTANT]
> **A Note for Developers and Administrators:**
> By default, Vertex AI may collect data to improve service quality. Data collection and logging are **only disabled** if the user explicitly disables **Vertex AI data caching** within the Google Cloud project settings. 

For technical details on how to configure these settings, please refer to the official [Vertex AI Zero Data Retention Documentation](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/vertex-ai-zero-data-retention).


---

## Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: HTML/CSS/JavaScript (Material Design)
- **AI**: Google Gemini AI API
- **Deployment**: Vercel

## Local Development

### Prerequisites

- Python 3.9+
- Google Gemini API key

### Setup

1. Clone the repository:
```bash
git clone <your-repo-url>
cd sentinel
```

2. Create and activate virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create `.env` file from example:
```bash
cp .env.example .env
```

5. Add your Gemini API key to `.env`:
```
GEMINI_API_KEY=your_api_key_here
```

6. Run the development server:
```bash
python -m api.main
```

7. Open `frontend/index.html` in your browser or serve it locally:
```bash
cd frontend
python -m http.server 8080
```

The API will be available at `http://localhost:8000` and the frontend at `http://localhost:8080`.

## Deploying to Vercel

### Quick Deploy

1. **Push to GitHub** (if you haven't already):
```bash
git push origin main
```

2. **Import to Vercel**:
   - Go to [vercel.com](https://vercel.com)
   - Click "New Project"
   - Import your GitHub repository
   - Vercel will automatically detect the configuration from `vercel.json`

3. **Add Environment Variables** in Vercel:
   - Go to Project Settings → Environment Variables
   - Add `GEMINI_API_KEY` with your API key

4. **Deploy**:
   - Click "Deploy"
   - Vercel will build and deploy your application

### Manual Configuration

If you need to configure manually:

1. **Framework Preset**: Other
2. **Build Command**: (leave empty)
3. **Output Directory**: (leave empty)
4. **Install Command**: `pip install -r requirements.txt`

### Environment Variables

Set these in Vercel's project settings:

- `GEMINI_API_KEY`: Your Google Gemini API key (required)
- `LOG_LEVEL`: `INFO` (optional, defaults to INFO)
- `CORS_ORIGINS`: `*` (optional, for CORS configuration)

## Project Structure

```
sentinel/
├── api/                    # FastAPI backend
│   ├── routes/            # API route handlers
│   ├── services/          # Business logic (Gemini client, etc.)
│   ├── models/            # Pydantic schemas
│   ├── config.py          # Configuration management
│   └── main.py            # FastAPI application entry point
├── frontend/              # Static frontend
│   └── index.html         # Single-page application
├── requirements.txt       # Python dependencies
├── vercel.json           # Vercel deployment configuration
├── .env.example          # Environment variables template
└── README.md             # This file
```

## API Endpoints

- `GET /` - API information
- `GET /health` - Health check
- `POST /api/v1/analyze` - Analyze video or image URL
- `POST /api/v1/analyze/initial` - Initial image analysis (without locations)
- `POST /api/v1/analyze/location` - Find issue location in image

## Usage

### Analyze a YouTube Video

1. Select "YouTube Video" from the content type dropdown
2. Paste the YouTube URL
3. Optionally adjust the frame rate (lower = fewer tokens used)
4. Click "Analyze"

### Analyze an Image

1. Select "Image URL" or "Upload Image"
2. Provide the image URL or select a file
3. Click "Analyze"
4. Click on numbered markers to see issue details

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.


