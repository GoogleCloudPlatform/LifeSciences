# Sentinel: Pharma Ad Content Checker



Sentinel is content analysis starter code to support pharmaceutical regulatory affairs and marketing teams. Sentinel starter code can be used as a starting point to construct more efficient workflow applications in the Pharma Industry. Leveraging the power of Google Gemini AI, Sentinel code can help build automation workflows for the review process for promotional advertisements. Its core function is to flag potential issues, verify citation integrity, and check adherence to established industry standards, thereby identifying and annotating areas for expert review. 

**Key Features and Functionality:**

*   **Pharma Ad Video Checker:** Sentinel code performs analysis of video advertisements. It is designed to identify and flag potential issues, providing timestamped markers to facilitate precise editing and review.
*   **Ad Infographic Checker:** The code offers capabilities for reviewing advertisements, and infographics. It provides annotations that highlight potential issues hence speeding up the checking process.
*   **Interactive Professional User Interface (UI):** Sentinel features a clean, efficient interface. This UI provides regulatory and marketing teams with real-time feedback and generates reports, enhancing workflow efficiency and transparency using the Sentinel code.

**Target Users:**

Sentinel code is designed for pharmaceutical regulatory affairs and marketing teams responsible for checking promotional materials.

**Technology and Architecture:**

Sentinel code is built with a FastAPI (Python) backend, an HTML/CSS/JavaScript frontend utilizing Google Material Design, and integrates with the Google Gemini AI API for its analytical capabilities. It is designed for deployment on Vercel.

**Important Note:**

Sentinel code is a content analysis starter code and is for administrative and operational support only. It is not intended for any medical purpose, including diagnosis, prevention, monitoring, treatment, or alleviation of disease, injury, or disability, nor for the investigation, replacement, or modification of anatomy or physiological processes, or for the control of conception. Its function is solely to assist regulatory and marketing teams in identifying potential issues in pharmaceutical content. All outputs from Sentinel code should be considered preliminary, require independent verification and further investigation through established company process and methodologies for determining regulatory compliance for marketing content. This code is not intended to be used without appropriate validation, adaptation and/or making meaningful modifications by developers for their specific workflows. This code is intended as a developer accelerator and proof-of-concept. It has not undergone software validation (CSV), penetration testing, or quality assurance required for production environments in regulated industries. Deploying this code into a production workflow without significant modification, security hardening, and appropriate validation is at the user's sole risk.

> [!IMPORTANT]
> **A Note for Developers and Administrators:**
> By default, Vertex AI may collect data to improve service quality. Data collection and logging are **only disabled** if the user explicitly disables **Vertex AI data caching** within the Google Cloud project settings. 

For technical details on how to configure these settings, please refer to the official [Vertex AI Zero Data Retention Documentation](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/vertex-ai-zero-data-retention).




### Prerequisites

- Python 3.9+
- Google Gemini API key

### Setup

1. Clone the repository:
```bash
git clone https://github.com/GoogleCloudPlatform/LifeSciences/Sentinel
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


