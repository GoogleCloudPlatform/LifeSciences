# Sentinel: Pharma Ad Content Checker

Sentinel is content analysis starter code to support pharmaceutical regulatory affairs and marketing teams. Sentinel starter code can be used as a starting point to construct more efficient workflow applications in the Pharma Industry. Leveraging the power of Google Gemini AI, Sentinel code can help build automation workflows for the review process for promotional advertisements. Its core function is to flag potential issues, verify citation integrity, and check adherence to established industry standards, thereby identifying and annotating areas for expert review. 

**Key Features and Functionality:**

*   **Pharma Ad Video Checker:** Sentinel code performs analysis of video advertisements. It is designed to identify and flag potential issues, providing timestamped markers to facilitate precise editing and review.
*   **Ad Infographic Checker:** The code offers capabilities for reviewing advertisements, and infographics. It provides annotations that highlight potential issues hence speeding up the checking process.
*   **Interactive Professional User Interface (UI):** Sentinel features a clean, efficient interface. This UI provides regulatory and marketing teams with real-time feedback and generates reports, enhancing workflow efficiency and transparency using the Sentinel code.

**Target Users:**

Sentinel code is designed for pharmaceutical regulatory affairs and marketing teams responsible for checking promotional materials.

**Technology and Architecture:**

Sentinel code is built with a FastAPI (Python) backend, an HTML/CSS/JavaScript frontend utilizing Google Material Design, and integrates with the Google Gemini AI API for its analytical capabilities. It is designed for deployment on Cloud Run.

**Important Note:**

Sentinel code is a content analysis starter code and is for administrative and operational support only. It is not intended for any medical purpose, including diagnosis, prevention, monitoring, treatment, or alleviation of disease, injury, or disability, nor for the investigation, replacement, or modification of anatomy or physiological processes, or for the control of conception. Its function is solely to assist regulatory and marketing teams in identifying potential issues in pharmaceutical content. All outputs from Sentinel code should be considered preliminary, require independent verification and further investigation through established company process and methodologies for determining regulatory compliance for marketing content. This code is not intended to be used without appropriate validation, adaptation and/or making meaningful modifications by developers for their specific workflows. This code is intended as a developer accelerator and proof-of-concept. It has not undergone software validation (CSV), penetration testing, or quality assurance required for production environments in regulated industries. Deploying this code into a production workflow without significant modification, security hardening, and appropriate validation is at the user's sole risk.

> [!IMPORTANT]
> **A Note for Developers and Administrators:**
> By default, Vertex AI may collect data to improve service quality. Data collection and logging are **only disabled** if the user explicitly disables **Vertex AI data caching** within the Google Cloud project settings. 

For technical details on how to configure these settings, please refer to the official [Vertex AI Zero Data Retention Documentation](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/vertex-ai-zero-data-retention).


## Local Development

### Prerequisites

- Python 3.12+
- `uv` package manager (**required** - faster than pip/venv)
- Node.js and npm (for frontend)
- Google Cloud Project (for Vertex AI - preferred) OR Google Gemini API key (for AI Studio)
- Google Cloud SDK (for Vertex AI authentication)

### Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/GoogleCloudPlatform/LifeSciences
   cd LifeSciences
   ```

2. **Backend Setup**:
   ```bash
   # Create and activate a virtual environment
   uv venv --python "python3.12" ".venv"
   source .venv/bin/activate

   # Install all dependencies
   uv sync --all-extras
   ```

3. **Frontend Setup**:
   ```bash
   cd frontend
   npm install
   ```

4. **Configure Environment Variables**:
   
   Copy `.env.example` to `.env`.

   * **Vertex AI (Recommended)**:
      * Set `GOOGLE_GENAI_USE_VERTEXAI=true`.
      * Set `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`.
      * Set `GCS_BUCKET_NAME` for image features.
      * Authenticate: `gcloud auth application-default login`.

   * **AI Studio**:
      * Set `GEMINI_API_KEY`.

5. **Run the Development Servers**:

   * **Backend**:
      ```bash
      uv run python -m api.main
      ```
      The API will run at `http://localhost:8000`.

   * **Frontend**:
      ```bash
      cd frontend
      npm run dev
      ```
      The frontend will run at `http://localhost:5173`.

### Running Tests

The project uses `pytest` for unit testing the API services and routes.

```bash
uv run pytest
```

## Docker Deployment

The application is containerized using a multi-stage Docker build that serves the React frontend via the FastAPI backend.

### Build and Run

1. **Build the Image**:
   ```bash
   docker build -t sentinel .
   ```

2. **Run the Container**:

   * **Run with AI Studio (API Key)**

      ```bash
      docker run -p 8080:8080 --env-file .env sentinel
      ```

   * **Run with Vertex AI**

      You need to provide your Google Cloud credentials to the container.

      ```bash
      # Authenticate locally first
      gcloud auth application-default login

      # Run container with mounted credentials and environment file
      # We use --user to ensure the container can read the mounted credentials
      docker run -p 8080:8080 \
      --user $(id -u):$(id -g) \
      -v ~/.config/gcloud/application_default_credentials.json:/app/gcp_creds.json \
      -e GOOGLE_APPLICATION_CREDENTIALS=/app/gcp_creds.json \
      --env-file .env \
      sentinel
      ```



## Cloud Run Deployment

Deploying Sentinel to Google Cloud Run is the recommended way to run the application in a production-like environment.

### Prerequisites

- Google Cloud Project with billing enabled
- `gcloud` CLI installed and authenticated
- APIs enabled: Cloud Run, Artifact Registry, Vertex AI, IAP, Cloud Resource Manager, Cloud Storage
- GCS Bucket for storing image features

### Deployment Steps

Follow these steps to build and deploy the Sentinel application using Cloud Run and Artifact Registry.

1. **Set Environment Variables**:
   ```bash
   export PROJECT_ID=[YOUR_PROJECT_ID]
   export REGION=us-central1
   export BUCKET_NAME=sentinel-images-$PROJECT_ID
   
   gcloud config set project $PROJECT_ID
   export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
   ```

2. **Enable APIs**:
   Enable the necessary Google Cloud APIs.
   ```bash
   gcloud services enable \
     run.googleapis.com \
     artifactregistry.googleapis.com \
     aiplatform.googleapis.com \
     iap.googleapis.com \
     storage.googleapis.com \
     cloudresourcemanager.googleapis.com
   ```

3. **Create Artifact Registry Repository**:
   Create a Docker repository to store your container images.
   ```bash
   gcloud artifacts repositories create docker-repo \
     --repository-format=docker \
     --location=$REGION \
     --description="Docker repository"
   ```

4. **Build and Push Image**:
   Build the container image locally and push it to Artifact Registry.
   ```bash
   # Configure Docker authentication
   gcloud auth configure-docker $REGION-docker.pkg.dev

   # Build and Tag
   export IMAGE_URI=$REGION-docker.pkg.dev/$PROJECT_ID/docker-repo/sentinel:latest
   docker build -t $IMAGE_URI .

   # Push to Artifact Registry
   docker push $IMAGE_URI
   ```

5. **Create a GCS Bucket**:
   Sentinel requires a Google Cloud Storage bucket to store image features for analysis.
   ```bash
   gcloud storage buckets create gs://$BUCKET_NAME --location=$REGION
   ```

6. **Create Service Account**:
   Create a dedicated Service Account for the application.
   ```bash
   export SA_NAME=sentinel-sa
   export SA_EMAIL=$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com

   gcloud iam service-accounts create $SA_NAME --display-name="Sentinel Application Service Account"
   ```

7. **Grant Storage Permissions**:
   Grant the Service Account access to the GCS Bucket for reading and writing images.
   ```bash
   gcloud storage buckets add-iam-policy-binding gs://$BUCKET_NAME \
     --member="serviceAccount:$SA_EMAIL" \
     --role="roles/storage.objectUser"
   ```

8. **Grant Vertex AI Permissions**:
   Grant the Service Account access to Vertex AI for using the Gemini API.
   ```bash
   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:$SA_EMAIL" \
     --role="roles/aiplatform.user"
   ```

9. **Deploy with IAP**:
   Deploy the service with Identity-Aware Proxy (IAP) enabled for secure access, using the custom Service Account.
   ```bash
   gcloud beta run deploy sentinel \
     --image $IMAGE_URI \
     --region $REGION \
     --no-allow-unauthenticated \
     --iap \
     --service-account $SA_EMAIL \
     --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global,GCS_BUCKET_NAME=$BUCKET_NAME"
   ```

10. **Configure IAP Access**:
    Grant the IAP Service Agent permission to invoke your Cloud Run service.
    ```bash
    gcloud run services add-iam-policy-binding sentinel \
      --region $REGION \
      --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com" \
      --role="roles/run.invoker"
    ```

11. **Grant User Access**:
    Authorize users or groups to access the application behind IAP.
      ```bash
      gcloud beta iap web add-iam-policy-binding \
      --resource-type=cloud-run \
      --service=sentinel \
      --region=$REGION \
      --member="user:[USER_EMAIL]" \
      --role="roles/iap.httpsResourceAccessor"
      ```
      *Replace `[USER_EMAIL]` with the email address of the user you want to grant access to (e.g., `user@example.com`). You can also use `--member="group:[GROUP_EMAIL]"` for groups.*
      
      **Note:** You must add authorized users (including yourself) to access the application URL.

## Project Structure

```
sentinel/
├── api/                    # FastAPI backend
│   ├── routes/            # API route handlers (analysis, storage, health)
│   ├── services/          # Business logic (Gemini client, analyzer service)
│   ├── models/            # Pydantic schemas
│   ├── config.py          # Configuration management
│   └── main.py            # FastAPI application entry point
├── tests/                  # Unit test suite
├── frontend/              # React frontend
│   ├── src/               # TypeScript source files
│   ├── public/            # Static assets
│   └── index.html         # SPA entry point
├── Dockerfile              # Multi-stage Docker build
├── pyproject.toml         # Python dependencies and metadata
└── README.md               # This file
```

## API Endpoints

- `GET /health` - Health check
- `POST /api/v1/analyze` - Analyze video (URL) or image (URL)
- `POST /api/v1/analyze/upload` - Analyze uploaded image file
- `GET /api/v1/storage/list` - List files in GCS
- `POST /api/v1/storage/upload` - Upload file to GCS
- `GET /api/v1/storage/file/{path}` - Retrieve/stream file from GCS

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


