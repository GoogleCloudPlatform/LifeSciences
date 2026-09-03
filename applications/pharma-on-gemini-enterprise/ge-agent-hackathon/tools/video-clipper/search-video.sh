#!/bin/bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/video-understanding
# https://github.com/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/video-analysis/youtube_video_analysis.ipynb

# Load environment variables from .env file
set -a
[ -f .env ] && source .env
set +a

# Examples using environment variables (no need to pass project_id and location)
python search-video.py \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  "When does Rick sing 'Never Gonna Give you Up'"

python search-video.py \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  "When does an actor in a white shirt and black shorts do a running flip off a brick wall"

# Override model if needed
python search-video.py \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  "When does an actor in a white shirt and black shorts do a running flip off a brick wall" \
  --model_name "gemini-2.5-pro"

python search-video.py \
  "https://www.youtube.com/watch?v=7waDSzAh28k" \
  "Identify the key Environmental Cleaning Moments in the Video"
